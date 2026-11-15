"""TrayIcon context menu wiring.

Runs against the offscreen Qt platform so it needs no display. Skipped entirely
when PyQt6 isn't installed (the app already degrades to headless there).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from budgie_voice.indicator.tray import TrayIcon  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_tray(**overrides):
    calls: dict[str, int] = {"restart": 0, "quit": 0}
    kwargs = dict(
        on_toggle=lambda: None,
        on_enroll=lambda: None,
        on_settings=lambda: None,
        on_quit=lambda: calls.__setitem__("quit", calls["quit"] + 1),
        on_restart=lambda: calls.__setitem__("restart", calls["restart"] + 1),
        log_path=Path("/tmp/budgie-voice-test.log"),
    )
    kwargs.update(overrides)
    return TrayIcon(**kwargs), calls  # type: ignore[arg-type]


def test_restart_action_present_and_fires(qapp) -> None:
    tray, calls = _make_tray()
    actions = {a.text(): a for a in tray.contextMenu().actions()}

    restart = next((a for t, a in actions.items() if "Restart" in t), None)
    assert restart is not None, f"no Restart entry in {list(actions)}"

    restart.trigger()
    assert calls["restart"] == 1
    assert calls["quit"] == 0, "Restart must not fire the Quit handler"


def test_restart_omitted_when_no_handler(qapp) -> None:
    """Callers that don't support relaunching simply don't get the entry."""
    tray, _ = _make_tray(on_restart=None)
    texts = [a.text() for a in tray.contextMenu().actions()]

    assert not any("Restart" in t for t in texts)
    assert any("Quit" in t for t in texts)


def test_quit_still_wired(qapp) -> None:
    tray, calls = _make_tray()
    quit_act = next(a for a in tray.contextMenu().actions() if "Quit" in a.text())

    quit_act.trigger()
    assert calls["quit"] == 1
    assert calls["restart"] == 0


def test_copy_last_transcript_puts_text_on_clipboard(qapp) -> None:
    tray, _ = _make_tray(get_last_text=lambda: "hello from last time")
    copy = next(a for a in tray.contextMenu().actions() if "Copy Last" in a.text())

    copy.trigger()

    assert QApplication.clipboard().text() == "hello from last time"
