"""Dictation History page: saved sessions, filterable and sortable, with a metadata-only detail view.

Shows when, how long, how many words and characters, and how it ended. Never a transcript, never
audio: none is stored, so there is nothing here that could show it. Read-only: this page has no
delete or clear controls.
"""
from __future__ import annotations

from datetime import date

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from budgie_voice.analytics import sessions
from budgie_voice.analytics.sessions import Filter, SessionRow
from budgie_voice.analytics.store import HistoryStore
from budgie_voice.settings_ui.overview import _fmt_time
from budgie_voice.ui.status import RuntimeStatus
from budgie_voice.ui.style import set_icon, ui_font
from budgie_voice.ui.widgets import MARGIN, SPACING, Glyph, card, heading, muted, section

_PAGE = 50        # rows per "Load more"
_VISIBLE = 10     # rows the list shows before it scrolls
_ROW_PX = 26      # QTreeView::item min-height in ui.style
_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
_NB = " "


def _duration(ms: int) -> str:
    return f"{ms / 1000:.1f}{_NB}s" if ms < 60_000 else _fmt_time(ms)


class HistoryPage(QWidget):
    """Args:
        history: The saved sessions. None (Settings run standalone) says the history is not available.
        status: Runtime events: the list reloads when a dictation ends while the page is open.
    """

    def __init__(self, history: HistoryStore | None, status: RuntimeStatus) -> None:
        super().__init__()
        self._history = history
        self._rows: list[SessionRow] = []
        self._cursor = None
        self._more = False

        self._from = QLineEdit()
        self._from.setPlaceholderText("From date")
        self._from.setToolTip("Show dictations from this day on. Format: 2026-11-18")
        self._from.setAccessibleName("Show sessions from this date")
        self._to = QLineEdit()
        self._to.setPlaceholderText("To date")
        self._to.setToolTip("Show dictations up to this day. Format: 2026-11-18")
        self._to.setAccessibleName("Show sessions up to this date")
        self._status = QComboBox()
        for label, value in (("All results", None), ("Completed", "completed"),
                             ("Cancelled", "cancelled"), ("Failed", "failed")):
            self._status.addItem(label, value)
        self._status.setAccessibleName("Filter by result")
        self._sort = QComboBox()
        self._sort.addItem("Newest first", True)
        self._sort.addItem("Oldest first", False)
        self._sort.setAccessibleName("Sort order")
        clear = QPushButton("Clear")
        set_icon(clear, "x")
        clear.clicked.connect(self._clear)
        self._hint = muted("Dates look like 2026-11-18.")
        self._hint.hide()

        self._list = QTreeWidget()
        self._list.setHeaderLabels(["Date", "Time", "Duration", "Words", "Characters", "Result"])
        self._list.setRootIsDecorated(False)
        self._list.setUniformRowHeights(True)
        self._list.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._list.header().setStretchLastSection(True)
        for col in (2, 3, 4):
            self._list.headerItem().setTextAlignment(col, _RIGHT)
        self._list.setAccessibleName("Saved dictations")
        self._list.viewport().setAutoFillBackground(False)  # let the rounded frame show through
        self._list.setFixedHeight(self._list.header().sizeHint().height() + _VISIBLE * _ROW_PX + 16)
        self._list.itemSelectionChanged.connect(self._on_select)
        self._summary = muted("")
        self._more_btn = QPushButton("Load more")
        self._more_btn.clicked.connect(lambda: self._load(reset=False))

        self._detail = self._build_detail()
        self._controls = QWidget()
        self._build_controls(clear)
        self._empty = self._build_empty()

        col = QVBoxLayout(self)
        col.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        col.setSpacing(SPACING)
        col.addWidget(heading("Dictation History"))
        col.addWidget(self._controls)
        col.addWidget(self._empty)
        col.addStretch()

        for edit in (self._from, self._to):
            edit.editingFinished.connect(self.refresh)
        self._status.currentIndexChanged.connect(self.refresh)
        self._sort.currentIndexChanged.connect(self.refresh)
        status.subscribe(self._on_runtime)  # held weakly: a closed Settings window is simply dropped

    # -- layout ------------------------------------------------------------

    def _build_controls(self, clear: QPushButton) -> None:
        filters = QHBoxLayout()
        filters.setSpacing(8)
        filters.addWidget(self._from, stretch=1)
        filters.addWidget(self._to, stretch=1)
        filters.addWidget(self._status, stretch=1)
        filters.addWidget(self._sort, stretch=1)
        filters.addWidget(clear)
        foot = QHBoxLayout()
        foot.addWidget(self._summary, stretch=1)
        foot.addWidget(self._more_btn)
        col = QVBoxLayout(self._controls)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACING)
        col.addLayout(filters)
        col.addWidget(self._hint)
        col.addWidget(self._list)
        col.addLayout(foot)
        col.addWidget(section("Details"))
        col.addWidget(self._detail)

    def _build_detail(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        grid = QGridLayout(frame)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self._facts: dict[str, QLabel] = {}
        for i, key in enumerate(("Started", "Duration", "Words", "Characters", "Speed", "Result", "Session")):
            name = muted(key)
            value = QLabel("—")
            value.setFont(ui_font(13))
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(name, i, 0, alignment=Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, i, 1)
            self._facts[key] = value
        self._detail_hint = muted("Select a dictation to see its details. Only these facts are kept: "
                                  "never the words you said, and never audio.")
        grid.addWidget(self._detail_hint, len(self._facts), 0, 1, 2)
        return frame

    def _build_empty(self) -> QFrame:
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 24, 0, 24)
        col.setSpacing(8)
        col.addWidget(Glyph("history", 28), alignment=Qt.AlignmentFlag.AlignHCenter)
        self._empty_title = section("")
        self._empty_text = muted("")
        for label in (self._empty_title, self._empty_text):
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(label)
        return card(body)

    # -- loading -----------------------------------------------------------

    def refresh(self) -> None:
        """Reload from the first page with the current filters."""
        self._load(reset=True)

    def _on_runtime(self) -> None:
        if self.isVisible():  # a hidden page reloads when it is next opened
            self.refresh()

    def _clear(self) -> None:
        for edit in (self._from, self._to):
            edit.clear()
        for combo in (self._status, self._sort):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.refresh()

    def _filter(self) -> Filter:
        dates: list[date | None] = []
        bad = False
        for edit in (self._from, self._to):
            text = edit.text().strip()
            try:
                dates.append(date.fromisoformat(text) if text else None)
            except ValueError:
                dates.append(None)  # an unreadable date is ignored, and the hint says so
                bad = True
        self._hint.setVisible(bad)
        return Filter(dates[0], dates[1], self._status.currentData())

    def _show_empty(self, title: str, text: str) -> None:
        self._empty_title.setText(title)
        self._empty_text.setText(text)
        self._controls.hide()
        self._empty.show()

    def _load(self, reset: bool) -> None:
        h = self._history
        if h is None:
            return self._show_empty("Dictation history is not available here",
                                    "Open Settings from the Budgie tray icon to see your saved dictations.")
        if not h.available:
            return self._show_empty("History is unavailable",
                                    "Budgie could not open its history file, so nothing can be shown. "
                                    "Dictation itself is not affected.")
        keep = self._selected_id()  # by session id: a reload can shift every row's position
        if reset:
            self._rows, self._cursor = [], None
        flt = self._filter()
        rows, self._more = sessions.page(h, flt, newest_first=bool(self._sort.currentData()),
                                         after=self._cursor, limit=_PAGE)
        self._rows += rows
        if rows:
            self._cursor = rows[-1].cursor
        total = sessions.count(h, flt)
        if not h.healthy:  # a read just failed: do not mistake that for an empty history
            return self._show_empty("History is unavailable",
                                    "Budgie could not read its history file just now. "
                                    "Dictation itself is not affected.")
        if not self._rows and total == 0 and sessions.count(h) == 0:  # nothing has ever been saved
            return self._show_empty("No dictation history yet",
                                    "Completed dictation sessions will appear here, showing only when, how "
                                    "long and how much. Never the words you said, and never audio.")
        self._empty.hide()
        self._controls.show()
        self._fill(total, keep)

    def _selected_id(self) -> str | None:
        item = self._list.currentItem()
        return self._rows[item.data(0, Qt.ItemDataRole.UserRole)].id if item is not None else None

    def _fill(self, total: int, keep: str | None) -> None:
        self._list.clear()
        for i, r in enumerate(self._rows):
            item = QTreeWidgetItem([
                f"{r.local_date:%d %b %Y}", f"{r.started_at.astimezone():%H:%M}", _duration(r.duration_ms),
                f"{r.words:,}", f"{r.chars:,}", r.status.capitalize(),
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            for col in (2, 3, 4):
                item.setTextAlignment(col, _RIGHT)
            self._list.addTopLevelItem(item)
            if r.id == keep:
                self._list.setCurrentItem(item)
        self._summary.setText(f"Showing {len(self._rows)} of {total:,} dictations" if self._rows
                              else "No dictations match these filters.")
        self._more_btn.setVisible(self._more)
        self._on_select()

    # -- detail ------------------------------------------------------------

    def _on_select(self) -> None:
        item = self._list.currentItem()
        row = self._rows[item.data(0, Qt.ItemDataRole.UserRole)] if item is not None else None
        self._detail_hint.setVisible(row is None)
        wpm = row.wpm if row else None
        values = {
            "Started": f"{row.started_at.astimezone():%A %d %B %Y, %H:%M:%S}" if row else "—",
            "Duration": _duration(row.duration_ms) if row else "—",
            "Words": f"{row.words:,}" if row else "—",
            "Characters": f"{row.chars:,}" if row else "—",
            "Speed": f"{wpm:.0f} words per minute" if wpm is not None else "—",
            "Result": row.status.capitalize() if row else "—",
            "Session": row.id if row else "—",
        }
        for key, text in values.items():
            self._facts[key].setText(text)
