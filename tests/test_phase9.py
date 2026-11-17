"""Phase 9: progressive typing — each closed turn is typed while speaking.

Runs the real DictationService against the Phase 1 rig. ``min_turn_s=0`` makes every
end-of-speech frame close a turn at once, so a script of turns arrives during "speech".
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from budgie_voice.orchestrator import dictation_service
from budgie_voice.orchestrator.session import SessionState
from budgie_voice.utils import selection

from test_session_reliability import _frame, _Rig

TURNS = ["first turn", "second turn", "third turn"]


def _rig(monkeypatch, *, progressive: bool = True, speaker: bool = False, preview: bool = False) -> _Rig:
    rig = _Rig(monkeypatch, min_turn_s=0)
    inj = rig.svc.settings.injection
    inj.progressive, inj.preview_before_inject = progressive, preview
    rig.svc.settings.speaker.enabled = speaker
    rig.svc._verifier.verify.return_value = (True, 1.0)  # noqa: SLF001
    if preview:
        rig.svc._on_preview = AsyncMock(side_effect=lambda t: t)  # noqa: SLF001
    rig.backend.script = list(TURNS)
    return rig


async def _speak(rig: _Rig) -> None:
    """Start, let two turns close during speech; the third stays open until stop."""
    rig.adapter.frames = [_frame(1000, end=True), _frame(2000, end=True), _frame(3000)]
    await rig.svc.toggle("tray")
    await asyncio.sleep(0.2)


def _notices() -> list[str]:
    return [c.args[1] for c in dictation_service._notify.call_args_list]  # noqa: SLF001


# -- ordering, exactly once ---------------------------------------------------

async def test_turns_are_typed_while_speaking_in_order_once_and_stop_types_the_rest(monkeypatch) -> None:
    rig = _rig(monkeypatch)
    await rig.svc.start()
    await _speak(rig)
    assert rig.typed() == ["first turn", " second turn"]  # before stop, single space join
    rig.injector.start_session.assert_awaited_once()
    rig.injector.end_session.assert_not_awaited()

    await rig.svc.toggle("tray")  # stop
    assert rig.typed() == ["first turn", " second turn", " third turn"]  # only the remainder
    rig.injector.start_session.assert_awaited_once()
    rig.injector.end_session.assert_awaited_once()
    assert rig.svc.last_transcript == "first turn second turn third turn"
    assert rig.svc.state == SessionState.IDLE
    await rig.close()


async def test_a_second_progressive_session_starts_from_scratch(monkeypatch) -> None:
    rig = _rig(monkeypatch)
    await rig.svc.start()
    await _speak(rig)
    await rig.svc.toggle("tray")
    rig.backend.script = ["again"]
    rig.adapter.frames = [_frame(4000)]
    await rig.svc.toggle("tray")
    await asyncio.sleep(0.05)
    await rig.svc.toggle("tray")
    assert rig.typed()[-1] == "again"  # no leading space, no replay of the first session
    assert rig.typed().count("first turn") == 1
    await rig.close()


async def test_progressive_off_types_everything_once_at_stop(monkeypatch) -> None:
    rig = _rig(monkeypatch, progressive=False)
    await rig.svc.start()
    await _speak(rig)
    assert rig.typed() == []  # nothing during speech: Phase 8 behaviour
    await rig.svc.toggle("tray")
    assert rig.typed() == ["first turn second turn third turn"]
    assert not any("rogressive" in n for n in _notices())
    await rig.close()


# -- cancel -------------------------------------------------------------------

async def test_cancel_after_typing_keeps_the_text_says_so_and_leaves_state_clean(monkeypatch) -> None:
    rig = _rig(monkeypatch)
    await rig.svc.start()
    await _speak(rig)
    await rig.svc.cancel()

    assert rig.typed() == ["first turn", " second turn"]  # nothing more, nothing deleted
    assert rig.svc.state == SessionState.IDLE
    assert any("22 characters were already typed" in n for n in _notices())
    rig.injector.end_session.assert_awaited_once()
    assert [c[0] for c in rig.injector.method_calls] == ["start_session", "commit_text", "commit_text", "end_session"]

    rig.backend.script = ["fresh"]
    rig.adapter.frames = [_frame(5000)]
    await rig.svc.toggle("tray")
    await asyncio.sleep(0.05)
    await rig.svc.toggle("tray")
    assert rig.typed() == ["first turn", " second turn", "fresh"]
    await rig.close()


async def test_cancel_before_anything_was_typed_is_silent(monkeypatch) -> None:
    rig = _rig(monkeypatch)
    await rig.svc.start()
    rig.adapter.frames = [_frame(1000)]
    await rig.svc.toggle("tray")
    await asyncio.sleep(0.05)
    await rig.svc.cancel()
    assert rig.typed() == []
    assert not any("already typed" in n for n in _notices())
    await rig.close()


# -- safety gates: automatic, not preferences ---------------------------------

async def test_speaker_verification_keeps_progressive_off_and_says_why_once(monkeypatch) -> None:
    rig = _rig(monkeypatch, speaker=True)
    await rig.svc.start()
    await _speak(rig)
    assert rig.typed() == []  # never types unverified speech
    await rig.svc.toggle("tray")
    assert rig.typed() == ["first turn second turn third turn"]  # the normal, verified path
    rig.svc._verifier.verify.assert_called_once()  # noqa: SLF001

    rig.backend.script = list(TURNS)
    await _speak(rig)
    await rig.svc.toggle("tray")
    assert len([n for n in _notices() if "Progressive typing is off" in n and "speaker" in n]) == 1
    await rig.close()


async def test_preview_keeps_progressive_off_and_never_bypasses_it(monkeypatch) -> None:
    rig = _rig(monkeypatch, preview=True)
    await rig.svc.start()
    await _speak(rig)
    assert rig.typed() == []
    await rig.svc.toggle("tray")
    rig.svc._on_preview.assert_awaited_once_with("first turn second turn third turn")  # noqa: SLF001
    assert rig.typed() == ["first turn second turn third turn"]
    assert len([n for n in _notices() if "Progressive typing is off" in n and "preview" in n]) == 1
    await rig.close()


async def test_command_mode_is_never_progressive(monkeypatch) -> None:
    rig = _rig(monkeypatch)
    monkeypatch.setattr(selection, "read_primary", lambda: "hello world")
    rig.svc._llm = SimpleNamespace(rewrite=AsyncMock(return_value="Hi."), polish=AsyncMock())  # noqa: SLF001
    await rig.svc.start()
    rig.adapter.frames = [_frame(1000, end=True), _frame(2000)]
    await rig.svc.command_start()
    await asyncio.sleep(0.2)
    assert rig.typed() == []  # the instruction is never typed
    await rig.svc.command()
    assert rig.typed() == ["Hi."]
    assert not any("Progressive" in n for n in _notices())  # not a configuration problem
    await rig.close()


async def test_a_dictation_after_a_command_can_be_progressive_again(monkeypatch) -> None:
    rig = _rig(monkeypatch)
    monkeypatch.setattr(selection, "read_primary", lambda: "hello world")
    rig.svc._llm = SimpleNamespace(rewrite=AsyncMock(return_value="Hi."), polish=AsyncMock())  # noqa: SLF001
    await rig.svc.start()
    rig.adapter.frames = [_frame(1000)]
    await rig.svc.command_start()
    await asyncio.sleep(0.05)
    await rig.svc.command()
    rig.backend.script = list(TURNS)
    await _speak(rig)
    assert rig.typed() == ["Hi.", "first turn", " second turn"]
    await rig.svc.cancel()
    await rig.close()


# -- failure, corrections, polish ---------------------------------------------

async def test_a_typing_failure_never_retypes_or_types_out_of_order(monkeypatch) -> None:
    rig = _rig(monkeypatch)
    rig.injector.commit_text.side_effect = [RuntimeError("window gone"), None, None]
    await rig.svc.start()
    await _speak(rig)
    assert len(rig.typed()) == 1  # the failed turn was attempted once; turn 2 was not typed after it
    assert any("Typing failed" in n for n in _notices())
    await rig.svc.toggle("tray")
    assert len(rig.typed()) == 1  # still nothing after the gap
    assert rig.svc.last_transcript == "first turn second turn third turn"  # all recoverable from the tray
    assert rig.svc.state == SessionState.IDLE
    await rig.close()


async def test_corrections_apply_per_turn_but_nothing_is_learned_and_no_polish(monkeypatch) -> None:
    rig = _rig(monkeypatch)
    rig.svc._corrections = MagicMock()  # noqa: SLF001
    rig.svc._corrections.apply.side_effect = lambda t: t.replace("first", "1st")  # noqa: SLF001
    rig.svc._llm = SimpleNamespace(polish=AsyncMock(side_effect=lambda t: t.upper()))  # noqa: SLF001
    await rig.svc.start()
    await _speak(rig)
    await rig.svc.toggle("tray")
    assert rig.typed() == ["1st turn", " second turn", " third turn"]
    rig.svc._corrections.learn.assert_not_called()  # noqa: SLF001
    rig.svc._llm.polish.assert_not_called()  # noqa: SLF001
    await rig.close()


# -- settings reload mid-dictation (apply() swaps the injector) ---------------

def _new_injector() -> MagicMock:
    inj = MagicMock()
    inj.start_session, inj.commit_text, inj.end_session = AsyncMock(), AsyncMock(), AsyncMock()
    return inj


def _calls(inj: MagicMock) -> list[str]:
    return [c[0] for c in inj.method_calls]


def _typed(inj: MagicMock) -> list[str]:
    return [c.args[0] for c in inj.commit_text.call_args_list]


def _two_phase_stream(rig: _Rig, release: asyncio.Event) -> None:
    """Turn 1 at once; turns 2 and 3 only once *release* is set (after the reload)."""
    async def stream():
        yield _frame(1000, end=True)
        await release.wait()
        yield _frame(2000, end=True)
        yield _frame(3000)
        await asyncio.Event().wait()

    rig.adapter.stream = stream


async def test_reload_mid_progressive_dictation_keeps_the_session_on_its_injector(monkeypatch) -> None:
    rig = _rig(monkeypatch)
    old, new = rig.injector, _new_injector()
    release = asyncio.Event()
    _two_phase_stream(rig, release)
    await rig.svc.start()
    await rig.svc.toggle("tray")
    await asyncio.sleep(0.15)
    assert _typed(old) == ["first turn"]

    rig.svc.apply(rig.svc.settings, rig.svc._pipeline, new)  # noqa: SLF001 — what main._reload does
    release.set()
    await asyncio.sleep(0.15)
    await rig.svc.toggle("tray")  # stop

    assert _calls(old) == ["start_session", "commit_text", "commit_text", "commit_text", "end_session"]
    assert _typed(old) == ["first turn", " second turn", " third turn"]  # all of it, in order, once
    assert new.method_calls == []  # the replacement never saw a half-finished session
    assert rig.svc._prog_injector is None  # noqa: SLF001

    # The next session runs on the replacement injector, from its own start_session.
    rig.backend.script = ["again"]

    async def one_turn():
        yield _frame(4000)
        await asyncio.Event().wait()

    rig.adapter.stream = one_turn
    await rig.svc.toggle("tray")
    await asyncio.sleep(0.05)
    await rig.svc.toggle("tray")
    assert _calls(new) == ["start_session", "commit_text", "end_session"]
    assert _typed(new) == ["again"]
    assert len(old.method_calls) == 5  # unchanged
    await rig.close()


async def test_reload_mid_dictation_without_progressive_uses_the_replacement_at_stop(monkeypatch) -> None:
    rig = _rig(monkeypatch, progressive=False)
    old, new = rig.injector, _new_injector()
    release = asyncio.Event()
    _two_phase_stream(rig, release)
    await rig.svc.start()
    await rig.svc.toggle("tray")
    await asyncio.sleep(0.15)

    rig.svc.apply(rig.svc.settings, rig.svc._pipeline, new)  # noqa: SLF001
    release.set()
    await asyncio.sleep(0.15)
    await rig.svc.toggle("tray")

    assert old.method_calls == []  # nothing was started before stop: unchanged Phase 8 behaviour
    assert _calls(new) == ["start_session", "commit_text", "end_session"]
    assert _typed(new) == ["first turn second turn third turn"]
    await rig.close()
