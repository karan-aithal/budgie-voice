"""Phase 10 theme foundation: modes, status-colour contrast, ui.theme config, StatusRow.

Offscreen Qt ignores QStyleHints.setColorScheme (only a real xcb/wayland platform honours it),
so the mode test checks what is handed to Qt, and palette-dependent code is driven with an
explicit palette. Real-desktop detection and live switching were verified by hand (Gate 2 report).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPalette  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from budgie_voice.config.settings import Settings, load_settings  # noqa: E402
from budgie_voice.ui.theme import Status, apply_theme, is_dark, status_color  # noqa: E402
from budgie_voice.ui.widgets import StatusRow  # noqa: E402

# Fusion's window backgrounds for the two schemes, as measured on a real X11 GNOME session.
_BG_LIGHT, _BG_DARK = QColor("#efefef"), QColor("#2a2a2a")


@pytest.fixture()
def app():
    a = QApplication.instance() or QApplication([])
    saved = a.palette()
    yield a
    a.setPalette(saved)


class _Hints:
    def __init__(self) -> None:
        self.scheme = None

    def setColorScheme(self, scheme) -> None:  # noqa: N802
        self.scheme = scheme


class _App:
    def __init__(self) -> None:
        self.hints = _Hints()

    def styleHints(self) -> _Hints:  # noqa: N802
        return self.hints


@pytest.mark.parametrize(("mode", "scheme"), [
    ("system", Qt.ColorScheme.Unknown),  # Unknown = hand control back to the desktop
    ("light", Qt.ColorScheme.Light),
    ("dark", Qt.ColorScheme.Dark),
    ("sepia", Qt.ColorScheme.Unknown),   # typo in config.yaml: fall back, don't crash or stick
])
def test_apply_theme_hands_qt_the_right_scheme(mode, scheme) -> None:
    fake = _App()
    apply_theme(fake, mode)  # type: ignore[arg-type]
    assert fake.hints.scheme == scheme


def test_is_dark_reads_the_effective_palette(app) -> None:
    for bg, dark in ((_BG_DARK, True), (_BG_LIGHT, False)):
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, bg)
        app.setPalette(pal)
        assert is_dark(app) is dark


@pytest.mark.parametrize("dark", [False, True])
@pytest.mark.parametrize("status", [Status.READY, Status.CONNECTING, Status.UNAVAILABLE, Status.ERROR])
def test_status_colours_are_readable_in_both_schemes(app, dark, status) -> None:
    def lum(c: QColor) -> float:
        def ch(v: float) -> float:
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c.redF()) + 0.7152 * ch(c.greenF()) + 0.0722 * ch(c.blueF())

    hi, lo = sorted((lum(status_color(status, dark)), lum(_BG_DARK if dark else _BG_LIGHT)), reverse=True)
    assert (hi + 0.05) / (lo + 0.05) >= 4.5  # WCAG AA


def test_ui_theme_defaults_to_system_and_loads(tmp_path) -> None:
    assert Settings().ui.theme == "system"
    cfg = tmp_path / "config.yaml"
    cfg.write_text("ui:\n  theme: dark\n")
    assert load_settings(cfg).ui.theme == "dark"


def test_status_row_states_the_status_in_words(app) -> None:
    row = StatusRow("LM Studio", Status.UNAVAILABLE, "not reachable")
    assert row._value.text() == "Unavailable — not reachable"
    row.set_status(Status.DISABLED)
    assert row._value.text() == "Disabled"


# ---------------------------------------------------------------------------
# Integration-review regressions
# ---------------------------------------------------------------------------

def test_apply_theme_never_raises_and_says_so(caplog) -> None:
    """PyQt6 < 6.8 has no setColorScheme; that must not cost the user the tray."""
    from budgie_voice.ui import theme

    class OldQt:
        def styleHints(self):  # noqa: N802
            return object()  # no setColorScheme

    before = theme._applied
    with caplog.at_level("WARNING"):
        theme.apply_theme(OldQt(), "dark")  # type: ignore[arg-type]
    assert "Could not apply ui.theme" in caplog.text
    assert theme._applied == before  # a failed apply is not recorded as in effect


def test_sync_theme_applies_a_mode_once(monkeypatch) -> None:
    from budgie_voice.ui import theme

    monkeypatch.setattr(theme, "_applied", None)
    fake = _App()
    theme.sync_theme(fake, "dark")  # type: ignore[arg-type]
    fake.hints.scheme = None
    theme.sync_theme(fake, "dark")  # type: ignore[arg-type]
    assert fake.hints.scheme is None  # already in effect: not handed to Qt again
    theme.sync_theme(fake, "light")  # type: ignore[arg-type]
    assert fake.hints.scheme == Qt.ColorScheme.Light
    theme.sync_theme(fake, "sepia")  # type: ignore[arg-type]  # unknown = system
    assert fake.hints.scheme == Qt.ColorScheme.Unknown
    fake.hints.scheme = None
    theme.sync_theme(fake, "sepia")  # type: ignore[arg-type]  # and a typo does not re-apply on every reload
    assert fake.hints.scheme is None


def test_empty_or_malformed_sections_keep_their_defaults(tmp_path, caplog) -> None:
    from dataclasses import fields

    names = [f.name for f in fields(Settings)]
    cfg = tmp_path / "config.yaml"
    cfg.write_text("".join(f"{n}:\n" for n in names))  # every section present but empty (None)
    s = load_settings(cfg)
    assert s == Settings()  # nothing replaced by None
    assert s.ui.theme == "system" and s.indicator.visualizer == "waveform"

    cfg.write_text("ui: dark\naudio:\n  denoise: true\n")  # a section that is not a mapping
    with caplog.at_level("WARNING"):
        s = load_settings(cfg)
    assert s.ui.theme == "system" and "must be a mapping" in caplog.text
    assert s.audio.denoise is True  # the rest of the file still applies
