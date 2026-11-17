"""Native Minimal theme: Qt's own colour scheme plus a few status colours.

No stylesheet and no custom palette. Fusion repaints natively when the colour scheme
changes, so every palette-driven widget (and the tray menu) follows for free. Only
custom-painted widgets need tokens, and they must read them *inside paintEvent*:
``colorSchemeChanged`` fires slightly before the application palette is updated.

``system`` hands the decision back to the desktop (GNOME ``color-scheme``), including live
changes. ``light`` / ``dark`` pin it. The recorder overlay and transcript preview paint
their own colours and are unaffected.

"""
from __future__ import annotations

import logging
from typing import Literal

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import QApplication

from budgie_voice.ui.status import Status

_LOG = logging.getLogger(__name__)

ThemeMode = Literal["system", "light", "dark"]
MODES: tuple[str, ...] = ("system", "light", "dark")

_SCHEME = {
    "system": Qt.ColorScheme.Unknown,  # follow the desktop, live
    "light": Qt.ColorScheme.Light,
    "dark": Qt.ColorScheme.Dark,
}


# Status colours, tuned per scheme for contrast against the window background.
# Disabled/Unknown are neutral and use the palette's disabled text colour.
_STATUS_LIGHT = {Status.READY: "#186f33", Status.CONNECTING: "#8a5a00",
                 Status.UNAVAILABLE: "#8a5a00", Status.ERROR: "#c01c28"}
_STATUS_DARK = {Status.READY: "#57c27a", Status.CONNECTING: "#e5a50a",
                Status.UNAVAILABLE: "#e5a50a", Status.ERROR: "#ff7b63"}


def themed_icon(name: str) -> QIcon:
    """The desktop theme's icon *name*, else its non-symbolic twin, else an empty icon.

    Never raises and never returns a placeholder: a menu or button simply shows no glyph on a
    desktop whose icon theme lacks it. Symbolic icons come back mid-grey from Qt, readable
    (3:1 or better) on both the light and the dark window colours.
    """
    for candidate in (name, name.removesuffix("-symbolic")):
        icon = QIcon.fromTheme(candidate)
        if not icon.isNull():
            return icon
    return QIcon()


_applied: str | None = None  # the mode last handed to Qt successfully


def apply_theme(app: QApplication, mode: str) -> None:
    """Apply *mode* app-wide, live. An unknown value falls back to ``system``.

    Never raises: a theme is cosmetic and must not cost the user the tray. On a Qt too old for
    ``setColorScheme`` (PyQt6 < 6.8) the desktop's own look is kept and a warning is logged.
    """
    global _applied
    if mode not in _SCHEME:
        _LOG.warning("Unknown ui.theme %r — using system", mode)
        mode = "system"
    try:
        app.styleHints().setColorScheme(_SCHEME[mode])
    except Exception as exc:  # noqa: BLE001 — see docstring
        _LOG.warning("Could not apply ui.theme %r (%s) — keeping the desktop look", mode, type(exc).__name__)
        return
    _applied = mode


def sync_theme(app: QApplication, mode: str) -> None:
    """:func:`apply_theme` unless *mode* is already in effect.

    Startup, Settings → Save and a DBus Reload all call this, so one change is applied once
    however many of them see it.
    """
    if (mode if mode in _SCHEME else "system") != _applied:
        apply_theme(app, mode)


def is_dark(app: QApplication | None = None) -> bool:
    """Whether the *effective* palette is dark (right for all three modes)."""
    app = app or QApplication.instance()
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def status_color(status: Status, dark: bool | None = None) -> QColor:
    """Colour for *status*; *dark* defaults to the effective palette. Call from paintEvent, not __init__."""
    app = QApplication.instance()
    table = _STATUS_DARK if (is_dark(app) if dark is None else dark) else _STATUS_LIGHT
    if status in table:
        return QColor(table[status])
    return app.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
