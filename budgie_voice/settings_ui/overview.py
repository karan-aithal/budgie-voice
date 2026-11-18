"""Overview dashboard: readiness, productivity cards, activity chart, system health, recent dictations.

Reads :class:`~budgie_voice.ui.status.RuntimeStatus` on demand (page opened, mic list
refreshed, or the runtime reports a change). No polling, no UI-thread network: the LM Studio
check runs on a worker thread and reports back through a queued signal. Dictation activity is
metadata only (time, duration, result, character count).

The productivity cards and the chart show an honest empty state until dictation history is
persisted: nothing here invents a number.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from budgie_voice.analytics import stats
from budgie_voice.analytics.store import HistoryStore
from budgie_voice.config.settings import Settings
from budgie_voice.ui.chart import BarChart
from budgie_voice.ui.status import RuntimeStatus, Status, probe_llm
from budgie_voice.ui.style import set_icon, ui_font
from budgie_voice.ui.widgets import (
    MARGIN,
    SPACING,
    Chip,
    Glyph,
    StatCard,
    StatusRow,
    card,
    heading,
    muted,
    section,
)

_LOG = logging.getLogger(__name__)

_RECENT = 5       # rows in "Recent dictations"
_ROW_PX = 26      # QTreeView::item min-height in ui.style
_WIDE_PX = 500    # at least this wide: four stat cards in a row (they need 491), otherwise 2 x 2


def _fmt_time(ms: int) -> str:
    """"4 min 5 s". A number never parts from its unit (no-break space), so a narrow card wraps between units."""
    nb = " "
    s = round(ms / 1000)
    if s < 60:
        return f"{s}{nb}s"
    if s < 3600:
        return f"{s // 60}{nb}min {s % 60}{nb}s"
    return f"{s // 3600}{nb}h {s % 3600 // 60}{nb}min"


def _greeting(hour: int) -> str:
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


def _readiness(asr: Status, mic: Status) -> tuple[Status, str, str]:
    """(dot, chip word, sentence) for whether dictation can start. LM Studio and speaker verification
    do not gate it: the first only polishes text, the second reports its own state in System health."""
    if asr == Status.CONNECTING:
        return Status.CONNECTING, "Loading", "A speech model is loading…"
    if asr == Status.UNKNOWN:
        return Status.UNKNOWN, "Unknown", "Budgie has not reported its status."
    if asr != Status.READY:
        return Status.UNAVAILABLE, "Not ready", "Speech recognition is not loaded."
    if mic == Status.UNAVAILABLE:
        return Status.UNAVAILABLE, "Not ready", "The configured microphone was not found."
    return Status.READY, "Ready", "Your Budgie workspace is ready."


class OverviewPage(QWidget):
    """Args:
        settings: Settings as loaded when the window opened.
        status: Runtime facts from main (an empty one when Settings runs standalone).
        mic_name: Display name of a configured device if the system currently lists it, else None.
        history: Saved dictation history for the statistics. None (Settings run standalone) shows them as unavailable.
    """

    _llm_done = pyqtSignal(object, str)  # (Status, detail), emitted from the probe thread
    view_all = pyqtSignal()              # "View all" was pressed: show the history page

    def __init__(
        self, settings: Settings, status: RuntimeStatus, mic_name: Callable[[str], str | None],
        history: HistoryStore | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._status = status
        self._mic_name = mic_name
        self._history = history
        self._stats_note = muted("")
        self._probing = False

        self._mic = StatusRow("Microphone")
        self._asr = StatusRow("Speech recognition")
        self._llm = StatusRow("LM Studio")
        self._speaker = StatusRow("Speaker verification")

        self._activity = QTreeWidget()
        self._activity.setHeaderLabels(["Time", "Duration", "Result", "Characters"])
        self._activity.setRootIsDecorated(False)
        self._activity.setUniformRowHeights(True)
        self._activity.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._activity.header().setStretchLastSection(True)
        for col in (1, 3):  # numbers are right-aligned, so are their headers
            self._activity.headerItem().setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._activity.setAccessibleName("Recent dictations")
        self._activity.viewport().setAutoFillBackground(False)  # let the rounded frame show through
        self._empty = muted("No dictations yet this session.")

        self._hero_title = heading("")
        self._hero_text = muted("")
        self._chip = Chip()
        self._hint = muted("")
        self._hint.setFont(ui_font(13))

        self._cards = [
            StatCard("text", "Words dictated", "Words typed into other apps"),
            StatCard("activity", "Dictation sessions", "Completed dictations"),
            StatCard("gauge", "Average speed", "Words per minute of recording"),
            StatCard("clock", "Total dictation time", "Time spent recording"),
        ]
        self._grid = QGridLayout()
        self._grid.setSpacing(SPACING)
        self._cols = 0

        self._chart = BarChart()
        seg = QHBoxLayout()
        seg.setSpacing(6)
        for days in (7, 30):
            btn = QPushButton(f"{days} days")
            btn.setProperty("seg", True)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)  # siblings under one parent: checking one unchecks the other
            btn.setChecked(days == 7)
            btn.setAccessibleName(f"Show the last {days} days")
            btn.clicked.connect(lambda _=False, d=days: self._chart.set_days(d))
            seg.addWidget(btn)
        chart_head = QHBoxLayout()
        chart_head.addWidget(section("Words per day"), stretch=1)
        chart_head.addLayout(seg)
        chart_box = QWidget()
        chart_col = QVBoxLayout(chart_box)
        chart_col.setContentsMargins(0, 8, 0, 8)
        chart_col.addLayout(chart_head)
        chart_col.addWidget(self._chart)

        view_all = QPushButton("View all")
        set_icon(view_all, "history")
        view_all.clicked.connect(self.view_all)
        recent_head = QHBoxLayout()
        recent_head.addWidget(section("Recent dictations"), stretch=1)
        recent_head.addWidget(view_all)

        col = QVBoxLayout(self)
        col.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        col.setSpacing(SPACING)
        col.addWidget(self._hero())
        col.addLayout(self._grid)
        col.addWidget(self._stats_note)
        col.addWidget(card(chart_box))
        col.addSpacing(SPACING)
        col.addWidget(section("System health"))
        col.addWidget(card(self._mic, self._asr, self._llm, self._speaker))
        col.addSpacing(SPACING)
        col.addLayout(recent_head)
        col.addWidget(muted("Metadata only. Transcripts are never shown or stored here."))
        col.addWidget(self._activity)
        col.addWidget(self._empty)
        col.addStretch()

        self._reflow(2)  # the narrow layout first: it is the one that must fit the window's minimum
        self._refresh_stats()
        self._llm_done.connect(self._on_llm_done)
        # Held weakly by the status: when this window is dropped it simply stops being called.
        status.subscribe(self._on_runtime)

    def _hero(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        title = QHBoxLayout()
        title.addWidget(self._hero_title, stretch=1)
        title.addWidget(self._chip)
        hint = QHBoxLayout()
        hint.setSpacing(8)
        self._hint_icon = Glyph("mic", 16)
        hint.addWidget(self._hint_icon)
        hint.addWidget(self._hint, stretch=1)
        col = QVBoxLayout(frame)
        col.setContentsMargins(20, 18, 20, 18)
        col.setSpacing(6)
        col.addLayout(title)
        col.addWidget(self._hero_text)
        col.addSpacing(6)
        col.addLayout(hint)
        return frame

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read everything. Starts one LM Studio check (none when disabled)."""
        self._refresh_mic()
        self._on_runtime()
        self._check_llm()

    def _on_runtime(self) -> None:
        self._refresh_asr()
        self._refresh_speaker()
        self._refresh_activity()
        self._refresh_hero()
        self._refresh_stats()  # runs when a dictation ends, after the history row was written

    # -- productivity statistics --------------------------------------------

    def _refresh_stats(self) -> None:
        """Cards and chart from the saved history; an honest note and "—" whenever there is nothing to show."""
        h = self._history
        days = stats.daily(h) if h is not None and h.available else {}
        if not days:
            note = ("Statistics are not available in this window." if h is None
                    else "History is unavailable, so statistics cannot be shown." if not h.healthy  # after the read
                    else "No dictations recorded yet. Statistics appear after your first dictation.")
            for c in self._cards:
                c.set_value("—")
            self._chart.set_data({})
        else:
            t = stats.total(days.values())
            wpm = t.wpm
            for c, value in zip(self._cards, (
                f"{t.words:,}", f"{t.sessions:,}", "—" if wpm is None else f"{wpm:.0f}",
                "—" if t.time_ms == 0 else _fmt_time(t.time_ms),
            )):
                c.set_value(value)
            since = date.today() - timedelta(days=29)  # the chart's longest range
            self._chart.set_data({d: s.words for d, s in days.items() if d >= since})
            note = "All time. Counts only words that were typed into another app."
        self._stats_note.setText(note)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reflow(4 if self.width() >= _WIDE_PX else 2)

    def _reflow(self, cols: int) -> None:
        if cols == self._cols:
            return
        self._cols = cols
        for i, c in enumerate(self._cards):
            self._grid.addWidget(c, i // cols, i % cols)
        for i in range(4):
            self._grid.setColumnStretch(i, 1 if i < cols else 0)

    # -- hero --------------------------------------------------------------

    def _refresh_hero(self) -> None:
        dot, word, text = _readiness(self._asr.status, self._mic.status)
        self._hero_title.setText(_greeting(datetime.now().hour))
        self._hero_text.setText(text)
        self._chip.set_status(dot, word)
        act = self._settings.activation
        verb = "Hold" if act.mode == "push_to_hold" else "Press"
        self._hint.setText(f"{verb} {act.hotkey} to dictate")
        shown = bool(act.hotkey)
        self._hint.setVisible(shown)
        self._hint_icon.setVisible(shown)

    # -- microphone -----------------------------------------------------

    def _refresh_mic(self) -> None:
        devices = self._settings.audio.device_list()
        if not devices:
            # Auto picks at session start; there is nothing to look up until then.
            self._mic.set_status(Status.UNKNOWN, "Auto — the microphone is chosen when dictation starts")
            return
        for i, device in enumerate(devices):
            name = self._mic_name(device)
            if name:
                self._mic.set_status(Status.READY, f"{name} (detected{'; fallback' if i else ''})")
                return
        self._mic.set_status(Status.UNAVAILABLE, f"{devices[0]} not found")

    # -- speech recognition ----------------------------------------------

    def _refresh_asr(self) -> None:
        inf = self._settings.inference
        configured = f"{inf.backend} / {inf.model}"
        info = self._status.asr_info()
        if info is None:
            self._asr.set_status(Status.UNKNOWN, f"{configured} configured; no status reported")
            return
        active = f"{info.backend} / {info.model}"
        if info.loading:
            # The window's settings are the old ones until the swap finishes: name the real target.
            self._asr.set_status(
                Status.CONNECTING, f"loading {info.target or 'a new model'}… (still serving {active})"
            )
            return
        detail = active if info.ready else f"{active} is not loaded"
        if active != configured:
            detail += f" (configured {configured}, not applied)"
        self._asr.set_status(Status.READY if info.ready else Status.UNAVAILABLE, detail)

    # -- speaker verification --------------------------------------------

    def _refresh_speaker(self) -> None:
        sp = self._settings.speaker
        if not sp.enabled:
            self._speaker.set_status(Status.DISABLED)
            return
        if not Path(sp.voiceprint_path).expanduser().exists():
            self._speaker.set_status(Status.UNAVAILABLE, "no voiceprint yet — enroll your voice")
            return
        ready = self._status.speaker_ready()
        if ready is None:
            self._speaker.set_status(Status.UNKNOWN, "voiceprint found; no status reported")
        elif ready:
            self._speaker.set_status(Status.READY, f"voiceprint loaded, threshold {sp.threshold:.2f}")
        else:
            self._speaker.set_status(Status.UNAVAILABLE, "voiceprint found but not loaded — restart Budgie")

    # -- recent dictations -------------------------------------------------

    def _refresh_activity(self) -> None:
        self._activity.clear()
        for a in list(reversed(self._status.activity))[:_RECENT]:  # newest first
            item = QTreeWidgetItem(
                [a.when.strftime("%d %b %H:%M"), f"{a.duration_s:.1f} s", a.outcome, str(a.chars)]
            )
            for col in (1, 3):
                item.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._activity.addTopLevelItem(item)
        rows = self._activity.topLevelItemCount()
        # Sized to its rows: the page scrolls as a whole, so the list must not claim spare height.
        self._activity.setFixedHeight(self._activity.header().sizeHint().height() + rows * _ROW_PX + 16)
        self._activity.setVisible(rows > 0)
        self._empty.setVisible(rows == 0)

    # -- LM Studio ---------------------------------------------------------

    def _check_llm(self) -> None:
        cfg = self._settings.llm
        if not cfg.enabled:
            self._llm.set_status(Status.DISABLED, "not used (llm.enabled is off)")
            return  # no request, no thread
        if self._probing:
            return
        self._probing = True
        self._llm.set_status(Status.CONNECTING, "checking…")
        threading.Thread(target=self._probe, args=(cfg,), daemon=True, name="budgie-llm-probe").start()

    def _probe(self, cfg: object) -> None:
        """Worker thread: never touches a widget, only emits a queued signal."""
        try:
            status, detail = probe_llm(cfg)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 — a failed check must not leave "checking…" forever
            _LOG.info("LM Studio check failed (%s)", type(exc).__name__)
            status, detail = Status.ERROR, "check failed"
        try:
            self._llm_done.emit(status, detail)
        except RuntimeError:  # window closed while the request was in flight
            pass

    def _on_llm_done(self, status: Status, detail: str) -> None:
        self._probing = False
        self._llm.set_status(status, detail)
