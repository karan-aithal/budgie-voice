"""TrayIcon — QSystemTrayIcon with colored-mic state icons.

Icons are SVG files under ``icons/``, one per state color.

``LOW_CONFIDENCE`` auto-reverts to the prior state after 3 s via QTimer.
``COMPLETED`` flashes green for 2 s then reverts to IDLE.
"""
from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QByteArray, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from budgie_voice.ui.icons import icon as lucide
from budgie_voice.utils.aio import log_failure

_LOG = logging.getLogger(__name__)

_ICONS_DIR = Path(__file__).parent / "icons"
# GNOME renders this menu in the shell, in its own dark colours, so menu glyphs use a fixed mid-grey
# that reads on both dark and light menus instead of following the app palette.
_GLYPH = QColor("#8e8e93")


def _state_icon(color: str) -> QIcon:
    """The coloured mic. Red is green.svg with another fill, so there is no red.svg to keep in sync."""
    if color != "red":
        return QIcon(str(_ICONS_DIR / f"{color}.svg"))
    svg = (_ICONS_DIR / "green.svg").read_text().replace("#47B02B", "#FF4141")
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):  # the sizes tray hosts ask for
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        painter = QPainter(px)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(px)
    return icon


def _glyph(name: str) -> QIcon:
    return lucide(name, _GLYPH, size=16)


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


# icon color, tooltip. The tooltip is also the status line at the top of the menu, because
# GNOME's tray often never shows tooltips.
_STATE_CONFIG: dict[TrayState, tuple[str, str]] = {
    TrayState.IDLE:           ("blue", "Budgie — ready to dictate"),
    TrayState.STARTING:       ("yellow", "Budgie — starting…"),
    TrayState.RECORDING:      ("green", "Budgie — listening"),
    TrayState.TRANSCRIBING:   ("purple", "Budgie — transcribing…"),
    TrayState.FINALIZING:     ("purple", "Budgie — finishing…"),
    TrayState.COMPLETED:      ("green", "Budgie — done"),
    TrayState.LOW_CONFIDENCE: ("orange", "Budgie — couldn't hear clearly"),
    TrayState.CANCELLED:      ("blue", "Budgie — cancelled"),
    TrayState.ERROR:          ("red", "Budgie — something went wrong (see log)"),
}

# The Toggle action is the same action in every state; only its wording follows what it will do.
_STOP_STATES = frozenset({TrayState.STARTING, TrayState.RECORDING})
_BUSY_STATES = frozenset({TrayState.TRANSCRIBING, TrayState.FINALIZING})


class TrayIcon(QSystemTrayIcon):
    """System tray icon with state-driven colored-mic icons and context menu.

    Args:
        on_toggle: Coroutine factory called when "Toggle Listen" is clicked.
        on_enroll: Callable to open the enrollment wizard.
        on_settings: Callable to open the settings window.
        on_restart: Callable to shut down and relaunch the app.
        get_last_text: Returns the last dictation, for "Copy Last Transcript".
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
        on_restart: Callable | None = None,
        get_last_text: Callable[[], str] | None = None,
        parent: object = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-arg]
        self._prior: TrayState = TrayState.IDLE
        self._revert_timer = QTimer()
        self._revert_timer.setSingleShot(True)

        self._build_menu(on_toggle, on_enroll, on_settings, on_quit, log_path, on_restart, get_last_text)
        self._update_icon(TrayState.IDLE)
        self.setVisible(True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_state(self, state: TrayState) -> None:
        """Update the tray icon and tooltip to reflect *state*.

        ``LOW_CONFIDENCE`` auto-reverts to prior state after 3 s.
        ``COMPLETED`` and ``ERROR`` show for 2 s, then revert to IDLE. The session
        goes to IDLE right after either, so an IDLE arriving during a flash is
        deferred to the timer instead of wiping the flash.
        """
        if state == TrayState.IDLE and self._revert_timer.isActive():
            self._prior = TrayState.IDLE
            return
        if state == TrayState.LOW_CONFIDENCE:
            self._update_icon(TrayState.LOW_CONFIDENCE)
            self._revert_timer.timeout.connect(
                lambda: self._update_icon(self._prior), Qt.ConnectionType.SingleShotConnection
            )
            self._revert_timer.start(3000)
        elif state in (TrayState.COMPLETED, TrayState.ERROR):
            self._prior = TrayState.IDLE
            self._update_icon(state)
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
        color, tooltip = _STATE_CONFIG.get(state, ("blue", "Budgie"))
        self.setIcon(_state_icon(color))
        self.setToolTip(tooltip)
        # Menu text follows what is shown, including the 2 s COMPLETED/ERROR flashes.
        self._status_act.setText(tooltip)
        if state in _STOP_STATES:
            self._toggle_act.setText("Stop Dictation")
            self._toggle_act.setIcon(_glyph("square"))
        elif state in _BUSY_STATES:
            self._toggle_act.setText("Finishing…")
            self._toggle_act.setIcon(_glyph("loader-circle"))
        else:
            self._toggle_act.setText("Start Dictation")
            self._toggle_act.setIcon(_glyph("play"))

    def _build_menu(
        self,
        on_toggle: Callable,
        on_enroll: Callable,
        on_settings: Callable,
        on_quit: Callable,
        log_path: Path,
        on_restart: Callable | None = None,
        get_last_text: Callable[[], str] | None = None,
    ) -> None:
        import subprocess  # noqa: PLC0415

        menu = QMenu()

        # Read-only state line; its text (and the Toggle label) are set by _update_icon.
        self._status_act = menu.addAction("")
        self._status_act.setEnabled(False)
        menu.addSeparator()

        self._toggle_act = menu.addAction("Start Dictation")
        self._toggle_act.triggered.connect(
            lambda: asyncio.ensure_future(on_toggle()).add_done_callback(log_failure)
        )

        enroll_act = menu.addAction(_glyph("user-round-plus"), "Enroll Voice…")
        enroll_act.triggered.connect(on_enroll)

        settings_act = menu.addAction(_glyph("sliders-horizontal"), "Settings…")
        settings_act.triggered.connect(on_settings)

        log_act = menu.addAction(_glyph("file-text"), "Open Log")
        log_act.triggered.connect(
            lambda: subprocess.run(["xdg-open", str(log_path)], check=False)
        )

        if get_last_text is not None:
            copy_act = menu.addAction(_glyph("copy"), "Copy Last Transcript")
            copy_act.triggered.connect(
                lambda: QApplication.clipboard().setText(get_last_text())  # type: ignore[union-attr]
            )

        menu.addSeparator()

        if on_restart is not None:
            restart_act = menu.addAction(_glyph("rotate-cw"), "Restart")
            restart_act.triggered.connect(on_restart)

        quit_act = menu.addAction(_glyph("log-out"), "Quit")
        quit_act.triggered.connect(on_quit)

        self.setContextMenu(menu)
