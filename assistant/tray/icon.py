"""System tray icon with state-driven appearance for Voice Assistant.

Icons are 32×32 QPixmaps built at startup by rendering Unicode emoji via
QPainter — no external image files required.
"""

import logging
import os
import platform
import subprocess
from enum import Enum, auto
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

logger = logging.getLogger(__name__)

_LOG_PATH = Path.home() / ".local" / "share" / "voice-assistant" / "logs" / "app.log"


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class TrayState(Enum):
    """Visual states for the system tray icon.

    Attributes:
        IDLE: 🔵  Assistant is running but not listening.
        LISTENING: 🟢  Actively capturing and transcribing.
        LOW_CONFIDENCE_WARNING: 🟠  Last segment was low-confidence.
        ERROR: 🔴  A fatal error occurred.
    """

    IDLE = auto()
    LISTENING = auto()
    LOW_CONFIDENCE_WARNING = auto()
    ERROR = auto()


_STATE_META: dict[TrayState, tuple[str, str]] = {
    TrayState.IDLE: ("🔵", "Voice Assistant — Idle (press hotkey to listen)"),
    TrayState.LISTENING: ("🟢", "Voice Assistant — Listening"),
    TrayState.LOW_CONFIDENCE_WARNING: ("🟠", "Voice Assistant — Low confidence"),
    TrayState.ERROR: ("🔴", "Voice Assistant — Error"),
}


# ---------------------------------------------------------------------------
# Emoji → QPixmap helper
# ---------------------------------------------------------------------------


def _emoji_pixmap(emoji: str, size: int = 32) -> QPixmap:
    """Render *emoji* onto a transparent QPixmap of *size* × *size* pixels.

    Args:
        emoji: Unicode emoji string to render.
        size: Icon edge length in pixels.

    Returns:
        Transparent-background QPixmap with the emoji centred.
    """
    px = QPixmap(size, size)
    px.fill(QColor(0, 0, 0, 0))
    painter = QPainter(px)
    font = QFont()
    font.setPixelSize(size - 4)
    painter.setFont(font)
    painter.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, emoji)
    painter.end()
    return px


# ---------------------------------------------------------------------------
# TrayIcon
# ---------------------------------------------------------------------------


class TrayIcon(QSystemTrayIcon):
    """System tray icon with state management and context menu.

    All callbacks are invoked in the Qt (main) thread.

    Args:
        on_toggle: Invoked when "Start / Stop Listening" is clicked.
        on_enroll: Invoked when "Enroll Voice" is clicked.
        on_quit: Invoked when "Quit" is clicked.
    """

    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_enroll: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        super().__init__()

        # Pre-build one QIcon per state so set_state() costs nothing
        self._icons: dict[TrayState, QIcon] = {
            state: QIcon(_emoji_pixmap(emoji))
            for state, (emoji, _) in _STATE_META.items()
        }

        # Context menu
        menu = QMenu()
        self._start_act = menu.addAction("▶  Start Listening")
        self._stop_act = menu.addAction("⏹  Stop Listening")
        menu.addSeparator()
        enroll_act = menu.addAction("🎤  Enroll Voice")
        log_act = menu.addAction("📄  Open Log")
        menu.addSeparator()
        quit_act = menu.addAction("✕  Quit")

        self._start_act.triggered.connect(on_toggle)
        self._stop_act.triggered.connect(on_toggle)
        enroll_act.triggered.connect(on_enroll)
        log_act.triggered.connect(TrayIcon._open_log)
        quit_act.triggered.connect(on_quit)

        self.setContextMenu(menu)
        self.set_state(TrayState.IDLE)
        self.show()
        logger.debug("Tray icon created")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_state(self, state: TrayState) -> None:
        """Change icon appearance and tooltip.

        Args:
            state: Target :class:`TrayState`.
        """
        _, tooltip = _STATE_META[state]
        self.setIcon(self._icons[state])
        self.setToolTip(tooltip)
        self._start_act.setVisible(state != TrayState.LISTENING)
        self._stop_act.setVisible(state == TrayState.LISTENING)
        logger.debug("Tray → %s", state.name)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _open_log() -> None:
        """Open the log file with the OS default viewer (best-effort)."""
        if not _LOG_PATH.exists():
            logger.warning("Log file not found: %s", _LOG_PATH)
            return
        match platform.system():
            case "Linux":
                subprocess.run(["xdg-open", str(_LOG_PATH)], check=False)
            case "Windows":
                os.startfile(str(_LOG_PATH))  # type: ignore[attr-defined]
            case "Darwin":
                subprocess.run(["open", str(_LOG_PATH)], check=False)
