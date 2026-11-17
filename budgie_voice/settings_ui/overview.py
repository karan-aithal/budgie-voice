"""Overview page: what the running app says about its parts, plus recent dictation metadata.

Reads :class:`~budgie_voice.ui.status.RuntimeStatus` on demand (page opened, mic list
refreshed, or the runtime reports a change). No polling, no UI-thread network: the LM Studio
check runs on a worker thread and reports back through a queued signal. Dictation activity is
metadata only (time, duration, result, character count).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from budgie_voice.config.settings import Settings
from budgie_voice.ui.status import RuntimeStatus, Status, probe_llm
from budgie_voice.ui.widgets import MARGIN, SPACING, StatusRow, card, heading, muted, section

_LOG = logging.getLogger(__name__)


class OverviewPage(QWidget):
    """Args:
        settings: Settings as loaded when the window opened.
        status: Runtime facts from main (an empty one when Settings runs standalone).
        mic_name: Display name of a configured device if the system currently lists it, else None.
    """

    _llm_done = pyqtSignal(object, str)  # (Status, detail), emitted from the probe thread

    def __init__(
        self, settings: Settings, status: RuntimeStatus, mic_name: Callable[[str], str | None]
    ) -> None:
        super().__init__()
        self._settings = settings
        self._status = status
        self._mic_name = mic_name
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
        self._activity.setAccessibleName("Recent dictations")
        self._activity.viewport().setAutoFillBackground(False)  # let the rounded frame show through
        self._empty = muted("No dictations yet this session.")

        col = QVBoxLayout(self)
        col.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        col.setSpacing(SPACING)
        col.addWidget(heading("Overview"))
        col.addWidget(card(self._mic, self._asr, self._llm, self._speaker))
        col.addSpacing(SPACING)
        col.addWidget(section("Recent dictations"))
        col.addWidget(muted("Metadata only. Transcripts are never shown or stored here."))
        col.addWidget(self._activity, stretch=1)
        col.addWidget(self._empty)

        self._llm_done.connect(self._on_llm_done)
        # Held weakly by the status: when this window is dropped it simply stops being called.
        status.subscribe(self._on_runtime)

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
        for a in reversed(self._status.activity):  # newest first
            item = QTreeWidgetItem(
                [a.when.strftime("%H:%M:%S"), f"{a.duration_s:.1f} s", a.outcome, str(a.chars)]
            )
            for col in (1, 3):
                item.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._activity.addTopLevelItem(item)
        has = self._activity.topLevelItemCount() > 0
        self._activity.setVisible(has)
        self._empty.setVisible(not has)

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
