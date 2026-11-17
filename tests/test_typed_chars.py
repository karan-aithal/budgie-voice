"""DictationService.typed_chars: the exact per-session count behind Settings → Overview's activity list.

Runs the real DictationService against the Phase 1 rig. The earlier UI-side guess (comparing
last_transcript objects) was wrong for one-character dictations and had to be replaced.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from budgie_voice.ui.status import RuntimeStatus

from test_phase9 import _rig, _speak
from test_session_reliability import _frame, _Rig

FULL = "first turn second turn third turn"


async def _dictate(rig: _Rig, text: str, preview=None) -> None:
    rig.backend.script = [text]
    rig.svc._on_preview = preview  # noqa: SLF001
    rig.svc.settings.injection.preview_before_inject = preview is not None  # per dictation, never sticky
    await rig.dictate(_frame(1000))


async def test_non_progressive_counts_what_was_typed(monkeypatch) -> None:
    rig = _rig(monkeypatch, progressive=False)
    await rig.svc.start()
    assert rig.svc.typed_chars == 0
    await _speak(rig)
    await rig.svc.toggle("tray")
    assert rig.svc.typed_chars == len(FULL) == len(rig.typed()[0])
    await rig.close()


async def test_progressive_counts_the_session_not_the_previous_one(monkeypatch) -> None:
    rig = _rig(monkeypatch)
    await rig.svc.start()
    await _speak(rig)
    assert rig.svc.typed_chars == len("first turn second turn")  # grows while speaking
    await rig.svc.toggle("tray")
    assert rig.svc.typed_chars == len(FULL)

    rig.backend.script = ["again"]
    rig.adapter.frames = [_frame(4000)]
    await rig.svc.toggle("tray")
    await asyncio.sleep(0.05)
    await rig.svc.toggle("tray")
    assert rig.svc.typed_chars == len("again")  # reset at session start
    await rig.close()


async def test_cancelled_preview_is_zero_even_after_an_earlier_dictation(monkeypatch) -> None:
    rig = _rig(monkeypatch, progressive=False)
    await rig.svc.start()
    await _dictate(rig, "hello world", preview=AsyncMock(side_effect=lambda t: t))
    assert rig.svc.typed_chars == len("hello world")

    await _dictate(rig, "another one", preview=AsyncMock(return_value=None))  # user pressed Esc
    assert rig.svc.typed_chars == 0
    assert rig.svc.last_transcript == "hello world"  # unchanged: Copy Last Transcript still works
    await rig.close()


async def test_empty_dictation_is_zero(monkeypatch) -> None:
    rig = _rig(monkeypatch, progressive=False)
    await rig.svc.start()
    await _dictate(rig, "hello world")
    await _dictate(rig, "")
    assert rig.svc.typed_chars == 0
    await rig.close()


async def test_identical_one_character_dictations_each_count(monkeypatch) -> None:
    """CPython shares one-character strings, so the old identity check scored the second one 0."""
    rig = _rig(monkeypatch, progressive=False)
    await rig.svc.start()
    for _ in range(2):
        await _dictate(rig, "a")
        assert rig.svc.typed_chars == len(rig.typed()[-1]) >= 1
    await rig.close()


async def test_typing_failure_counts_nothing(monkeypatch) -> None:
    rig = _rig(monkeypatch, progressive=False)
    await rig.svc.start()
    rig.injector.commit_text.side_effect = RuntimeError("xdotool died")
    await _dictate(rig, "hello world")
    assert rig.svc.typed_chars == 0
    await rig.close()


async def test_activity_list_gets_counts_through_the_real_state_callback(monkeypatch) -> None:
    """The exact wiring main.py uses: every state change plus dict_svc.typed_chars."""
    rig = _rig(monkeypatch, progressive=False)
    status = RuntimeStatus()
    rig.svc._session._on_change = lambda s: status.on_session_state(str(s), rig.svc.typed_chars)  # noqa: SLF001
    await rig.svc.start()

    await _dictate(rig, "hello world")
    await _dictate(rig, "another one", preview=AsyncMock(return_value=None))
    await _dictate(rig, "a")
    await _dictate(rig, "a")

    assert [(a.outcome, a.chars) for a in status.activity] == [
        ("Completed", 11), ("Completed", 0), ("Completed", 1), ("Completed", 1),
    ]
    assert all("hello" not in repr(a) for a in status.activity)
    await rig.close()
