"""Phase 10 Gate 4: tray state-aware text and icon fallback, enrollment wizard presentation.

Offscreen Qt. The wizard's device scan is stubbed (no pactl, no PortAudio) and no worker is started:
recording, embedding and verification logic are not exercised here, only what the user is shown.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QIcon, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton  # noqa: E402

from budgie_voice.indicator.tray import TrayIcon, TrayState  # noqa: E402
from budgie_voice.speaker import enroller  # noqa: E402
from budgie_voice.ui.theme import themed_icon  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _has_emoji(text: str) -> bool:
    return any(ord(c) >= 0x1F000 or 0x2600 <= ord(c) <= 0x27BF for c in text)


def _tray() -> TrayIcon:
    return TrayIcon(
        on_toggle=lambda: None, on_enroll=lambda: None, on_settings=lambda: None, on_quit=lambda: None,
        log_path=Path("/tmp/budgie-voice-test.log"), on_restart=lambda: None, get_last_text=lambda: "x",
    )


def _actions(tray: TrayIcon) -> list:
    return [a for a in tray.contextMenu().actions() if not a.isSeparator()]


# ---------------------------------------------------------------------------
# themed_icon
# ---------------------------------------------------------------------------

def _fake_theme(monkeypatch, available: set[str]) -> None:
    px = QPixmap(8, 8)
    px.fill()
    real = QIcon(px)
    monkeypatch.setattr(QIcon, "fromTheme", staticmethod(lambda name, *a: real if name in available else QIcon()))


def test_themed_icon_prefers_symbolic_then_plain_then_empty(qapp, monkeypatch) -> None:
    _fake_theme(monkeypatch, {"edit-copy-symbolic", "view-refresh"})
    assert not themed_icon("edit-copy-symbolic").isNull()      # symbolic present
    assert not themed_icon("view-refresh-symbolic").isNull()   # only the plain twin present
    assert themed_icon("nothing-here-symbolic").isNull()       # neither: empty icon, no error


# ---------------------------------------------------------------------------
# Tray
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("state", "label"), [
    (TrayState.IDLE, "Start Dictation"),
    (TrayState.STARTING, "Stop Dictation"),
    (TrayState.RECORDING, "Stop Dictation"),
    (TrayState.TRANSCRIBING, "Finishing…"),
    (TrayState.FINALIZING, "Finishing…"),
    (TrayState.COMPLETED, "Start Dictation"),
    (TrayState.CANCELLED, "Start Dictation"),
])
def test_toggle_label_and_status_line_follow_the_state(qapp, state, label) -> None:
    tray = _tray()
    tray.set_state(state)
    assert tray._toggle_act.text() == label
    assert tray._status_act.text() == tray.toolTip() and tray.toolTip().startswith("Budgie — ")
    assert not tray._status_act.isEnabled()  # a read-only line, never an action


def test_toggle_label_returns_to_start_when_back_to_idle(qapp) -> None:
    tray = _tray()
    tray.set_state(TrayState.RECORDING)
    tray.set_state(TrayState.IDLE)
    assert tray._toggle_act.text() == "Start Dictation"


def test_menu_has_no_emoji_and_keeps_the_tested_words(qapp) -> None:
    tray = _tray()  # the menu dies with the tray: keep it alive
    texts = [a.text() for a in _actions(tray)]
    assert not any(_has_emoji(t) for t in texts), texts
    for word in ("Restart", "Quit", "Copy Last"):
        assert any(word in t for t in texts), word


def test_menu_works_when_the_icon_theme_has_nothing(qapp, monkeypatch) -> None:
    monkeypatch.setattr(QIcon, "fromTheme", staticmethod(lambda *a: QIcon()))
    tray = _tray()
    tray.set_state(TrayState.RECORDING)
    acts = _actions(tray)
    assert all(a.icon().isNull() for a in acts)                       # no glyphs, no errors
    assert [a.text() for a in acts][1] == "Stop Dictation" and len(acts) == 8
    assert not tray.icon().isNull()                                    # the state icon is bundled SVG, not themed


# ---------------------------------------------------------------------------
# Enrollment wizard
# ---------------------------------------------------------------------------

@pytest.fixture()
def wizard(qapp, monkeypatch):
    monkeypatch.setattr(enroller.subprocess, "check_output", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(enroller.sd, "query_devices", lambda: [])
    return enroller.EnrollmentWizard(MagicMock())


def test_four_pages_in_order(wizard) -> None:
    assert [type(wizard.page(i)).__name__ for i in range(4)] == [
        "_WelcomePage", "_RecordingPage", "_ProcessingPage", "_ConfirmationPage"]
    assert all(wizard.page(i).title() for i in range(4))


def test_welcome_copy_matches_the_clip_count(wizard) -> None:
    text = " ".join(lbl.text() for lbl in wizard.page(0).findChildren(QLabel))
    assert f"{enroller._N_CLIPS} short clips" in text
    assert "3 short clips" not in text


def test_no_emoji_in_any_wizard_text(wizard) -> None:
    texts: list[str] = []
    for i in range(4):
        page = wizard.page(i)
        texts += [page.title(), page.subTitle()]
        texts += [w.text() for w in page.findChildren(QLabel)]
        texts += [w.text() for w in page.findChildren(QPushButton)]
        for combo in page.findChildren(QComboBox):
            texts += [combo.itemText(j) for j in range(combo.count())]
    texts += [lbl.text() for lbl in wizard._steps._labels]
    assert not [t for t in texts if _has_emoji(t)]


def test_step_indicator_follows_the_wizard(wizard) -> None:
    def current() -> list[int]:
        return [i for i, lbl in enumerate(wizard._steps._labels) if int(lbl.font().weight()) >= 600]

    assert current() == [0]
    for page_id in (1, 2, 3):
        wizard.currentIdChanged.emit(page_id)
        assert current() == [page_id]
    assert "current" in wizard._steps._labels[3].accessibleName()
    assert "Step 4 of 4" in wizard._steps._labels[3].accessibleName()


def test_recording_feedback_and_completion_gate(wizard) -> None:
    page = wizard.page(1)
    assert not page.isComplete() and page._level.isHidden()

    page._on_recorded(np.full(100, 0.01, np.float32))                 # quiet clip
    assert page._level._value.text().startswith("Too quiet") and not page._level.isHidden()
    page._on_recorded(np.full(100, 0.5, np.float32))                  # good clip
    assert page._level._value.text().startswith("Good")
    assert page._progress.value() == 2 and "Clip 2 of 15" in page._status.text()

    for _ in range(enroller._N_CLIPS - 2):
        page._on_recorded(np.full(100, 0.5, np.float32))
    assert page.isComplete() and page._progress.value() == enroller._N_CLIPS
    assert page._record_btn.text() == "All clips recorded" and not page._record_btn.isEnabled()
    assert "Click Next" in page._status.text()


def test_recording_error_reenables_the_button(wizard) -> None:
    page = wizard.page(1)
    page._record_btn.setEnabled(False)
    page._busy.show()
    page._on_error("no microphone")
    assert page._record_btn.isEnabled() and page._busy.isHidden()
    assert page._status.text() == "Error: no microphone"


def test_processing_result_states(wizard) -> None:
    page = wizard.page(2)
    assert not page.isComplete() and page._result._value.text().startswith("Working")
    page._on_error("disk full")
    assert page._result._value.text() == "Failed — disk full" and not page.isComplete()
    page._on_done()
    assert page.isComplete() and page._result._value.text().startswith("Saved")


def test_verification_result_presentation(wizard) -> None:
    page = wizard.page(3)
    assert page._result._value.text().startswith("Waiting")
    page._verifier = MagicMock()
    page._verifier.verify.return_value = (True, 0.8731)
    page._on_recorded(np.zeros(10, np.float32))
    assert page._result._value.text() == "Match — your voice was recognized (score 0.873)"
    page._verifier.verify.return_value = (False, 0.3)
    page._on_recorded(np.zeros(10, np.float32))
    assert page._result._value.text().startswith("No match — your voice was not recognized (score 0.300)")
    assert page._test_btn.isEnabled()
