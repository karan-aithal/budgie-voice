"""TrayIcon — QSystemTrayIcon with emoji-rendered state icons.

Icons are 32×32 QPixmaps rendered from emoji via QPainter — zero external
image files required.

``LOW_CONFIDENCE`` auto-reverts to the prior state after 3 s via QTimer.
``COMPLETED`` flashes green for 2 s then reverts to IDLE.
"""
from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

_LOG = logging.getLogger(__name__)


class TrayState(StrEnum):
    """Activity indicator states mapped from session lifecycle."""

    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    LOW_CONFIDENCE = "low_confidence"
    CANCELLED = "cancelled"
    ERROR = "error"


# emoji, tooltip
_STATE_CONFIG: dict[TrayState, tuple[str, str]] = {
    TrayState.IDLE:           ("🔵", "Myna — idle"),
    TrayState.STARTING:       ("🟡", "Myna — starting…"),
    TrayState.RECORDING:      ("🟢", "Myna — recording"),
    TrayState.TRANSCRIBING:   ("🟣", "Myna — transcribing"),
    TrayState.FINALIZING:     ("🟣", "Myna — finalizing"),
    TrayState.COMPLETED:      ("🟢", "Myna — done"),
    TrayState.LOW_CONFIDENCE: ("🟠", "Myna — couldn't hear clearly"),
    TrayState.CANCELLED:      ("🔵", "Myna — cancelled"),
    TrayState.ERROR:          ("🔴", "Myna — error (see log)"),
}


def _emoji_pixmap(emoji: str, size: int = 32) -> QPixmap:
    """Render *emoji* into a square QPixmap via QPainter."""
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    font = QFont()
    font.setPointSize(20)
    painter.setFont(font)
    painter.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, emoji)
    painter.end()
    return px


class TrayIcon(QSystemTrayIcon):
    """System tray icon with state-driven emoji icons and context menu.

    Args:
        on_toggle: Coroutine factory called when "Toggle Listen" is clicked.
        on_enroll: Callable to open the enrollment wizard.
        on_settings: Callable to open the settings window.
        on_quit: Callable to initiate application shutdown.
        log_path: Path to the rotating log file (opened with xdg-open).
        parent: Optional Qt parent.
    """

    def __init__(
        self,
        on_toggle: Callable,
        on_enroll: Callable,
        on_settings: Callable,
        on_quit: Callable,
        log_path: Path,
        parent: object = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-arg]
        self._prior: TrayState = TrayState.IDLE
        self._revert_timer = QTimer()
        self._revert_timer.setSingleShot(True)

        self._build_menu(on_toggle, on_enroll, on_settings, on_quit, log_path)
        self._update_icon(TrayState.IDLE)
        self.setVisible(True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_state(self, state: TrayState) -> None:
        """Update the tray icon and tooltip to reflect *state*.

        ``LOW_CONFIDENCE`` auto-reverts to prior state after 3 s.
        ``COMPLETED`` reverts to IDLE after 2 s.
        """
        if state == TrayState.LOW_CONFIDENCE:
            self._update_icon(TrayState.LOW_CONFIDENCE)
            self._revert_timer.timeout.connect(
                lambda: self._update_icon(self._prior), Qt.ConnectionType.SingleShotConnection
            )
            self._revert_timer.start(3000)
        elif state == TrayState.COMPLETED:
            self._prior = TrayState.IDLE
            self._update_icon(TrayState.COMPLETED)
            self._revert_timer.timeout.connect(
                lambda: self._update_icon(TrayState.IDLE), Qt.ConnectionType.SingleShotConnection
            )
            self._revert_timer.start(2000)
        else:
            self._prior = state
            self._update_icon(state)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_icon(self, state: TrayState) -> None:
        emoji, tooltip = _STATE_CONFIG.get(state, ("⚫", "Myna"))
        self.setIcon(_emoji_pixmap(emoji))  # type: ignore[call-arg]
        self.setToolTip(tooltip)

    def _build_menu(
        self,
        on_toggle: Callable,
        on_enroll: Callable,
        on_settings: Callable,
        on_quit: Callable,
        log_path: Path,
    ) -> None:
        import subprocess  # noqa: PLC0415

        menu = QMenu()

        toggle_act = menu.addAction("▶  Toggle Listen")
        toggle_act.triggered.connect(lambda: asyncio.ensure_future(on_toggle()))

        enroll_act = menu.addAction("🎤  Enroll Voice")
        enroll_act.triggered.connect(on_enroll)

        settings_act = menu.addAction("⚙  Settings")
        settings_act.triggered.connect(on_settings)

        log_act = menu.addAction("📄  Open Log")
        log_act.triggered.connect(
            lambda: subprocess.run(["xdg-open", str(log_path)], check=False)
        )

        menu.addSeparator()
        quit_act = menu.addAction("✕  Quit")
        quit_act.triggered.connect(on_quit)

        self.setContextMenu(menu)
