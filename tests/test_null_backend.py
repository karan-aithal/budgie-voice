"""The no-injection-backend fallback must fail loudly and never print dictated text.

stdout reaches the systemd journal, so any text there breaks the "transcript is never
logged at INFO or above" rule; the old fallback printed every dictation to it.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from budgie_voice.main import _NullBackend
from budgie_voice.orchestrator import dictation_service
from budgie_voice.orchestrator.session import SessionState
from budgie_voice.utils.exceptions import BudgieInjectionError

from test_session_reliability import _frame, _Rig

SECRET = "my password is hunter2"


async def test_null_backend_raises_and_prints_nothing(capsys, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(BudgieInjectionError):
        await _NullBackend().commit_text(SECRET)
    seen = capsys.readouterr()
    assert SECRET not in seen.out + seen.err
    assert SECRET not in caplog.text


@pytest.mark.parametrize("progressive", [False, True])
async def test_dictation_with_no_backend_keeps_the_text_and_leaks_nothing(
    monkeypatch, capsys, caplog, progressive: bool,
) -> None:
    caplog.set_level(logging.INFO)  # INFO and above: DEBUG may carry text by design
    rig = _Rig(monkeypatch)
    rig.svc._injector = _NullBackend()  # noqa: SLF001
    rig.svc.settings.injection.progressive = progressive  # preview and speaker are already off in the rig
    rig.backend.script = [SECRET]
    await rig.svc.start()
    rig.adapter.frames = [_frame(1000)]
    await rig.svc.toggle("tray")
    await asyncio.sleep(0.05)
    await rig.svc.toggle("tray")

    assert rig.svc.state == SessionState.IDLE
    assert rig.svc.last_transcript == SECRET  # recoverable from the tray
    assert any("Typing failed" in c.args[1] for c in dictation_service._notify.call_args_list)  # noqa: SLF001
    seen = capsys.readouterr()
    assert SECRET not in seen.out + seen.err
    assert SECRET not in caplog.text
    await rig.close()
