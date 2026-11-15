"""Phase 1: a dictation session can neither lose text nor wedge.

Runs the real DictationService, InferenceClient and server ``_handle_ws``
against an in-memory socket and a fake backend whose "transcript" is the set
of sample values it was given — so a test can tell whose audio it received.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import numpy as np

from budgie_voice.config.settings import Settings
from budgie_voice.inference.client import InferenceClient
from budgie_voice.inference.protocol import AudioFrame, TranscriptSegment
from budgie_voice.inference.server import _handle_ws
from budgie_voice.orchestrator import dictation_service
from budgie_voice.orchestrator.dictation_service import DictationService
from budgie_voice.orchestrator.session import SessionState


class _Link:
    """One in-memory socket, exposing both the client and the server API."""

    def __init__(self) -> None:
        self.up: asyncio.Queue[str] = asyncio.Queue()
        self.down: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, raw: str) -> None:  # client end
        await self.up.put(raw)

    def __aiter__(self) -> "_Link":
        return self

    async def __anext__(self) -> str:
        return await self.down.get()

    async def receive_text(self) -> str:  # server end
        return await self.up.get()

    async def send_text(self, raw: str) -> None:
        await self.down.put(raw)


class _Backend:
    """Transcript = the distinct int16 sample values heard, e.g. ``"1000"``."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.gate.set()
        self.turns = 0
        self.script: list[str] = []  # if set, one entry is spoken per turn instead

    async def transcribe_stream(self, frames, language, hotwords=None):
        heard: set[int] = set()
        async for frame in frames:
            heard.update(int(v) for v in np.rint(frame * 32767.0))
        await self.gate.wait()
        self.turns += 1
        text = self.script.pop(0) if self.script else " ".join(map(str, sorted(heard)))
        yield TranscriptSegment(text, 0.9, True, language)


class _Adapter:
    def __init__(self) -> None:
        self.frames: list[AudioFrame] = []
        self.open_error: Exception | None = None

    def open(self) -> None:
        if self.open_error is not None:
            raise self.open_error

    def close(self) -> None:
        pass

    async def stream(self):
        for frame in self.frames:
            yield frame
        await asyncio.Event().wait()


def _frame(value: int, end: bool = False) -> AudioFrame:
    return AudioFrame(data=np.full(160, value, np.int16).tobytes(), is_end_of_speech=end)


class _Rig:
    def __init__(self, monkeypatch, timeout_s: float = 5.0, min_turn_s: float = 15.0) -> None:
        monkeypatch.setattr(dictation_service, "_notify", AsyncMock())
        monkeypatch.setattr(dictation_service, "_get_focused_window", lambda: 1)
        settings = Settings()
        settings.activation.debounce_ms = 0
        settings.speaker.enabled = False
        settings.learning.vocab_enabled = False
        settings.learning.corrections_enabled = False
        settings.injection.block_secure_fields = False
        settings.injection.preview_before_inject = False
        settings.inference.finalize_timeout_s = timeout_s

        self.backend = _Backend()
        self.adapter = _Adapter()
        self.injector = MagicMock()
        self.injector.start_session = AsyncMock()
        self.injector.commit_text = AsyncMock()
        self.injector.end_session = AsyncMock()
        self.states: list[SessionState] = []
        pipeline = MagicMock()
        pipeline.run.side_effect = lambda text: text

        link = _Link()
        client = InferenceClient()
        client._ws = link
        self.svc = DictationService(
            adapter=self.adapter, client=client, pipeline=pipeline,  # type: ignore[arg-type]
            injector=self.injector, verifier=MagicMock(), settings=settings,
            on_state_change=self.states.append,
        )
        self._server = asyncio.ensure_future(_handle_ws(link, self.backend, min_turn_s))  # type: ignore[arg-type]

    async def dictate(self, *frames: AudioFrame, stop: bool = True) -> None:
        """Start a session, let *frames* reach the server, optionally stop."""
        self.adapter.frames = list(frames)
        await self.svc.toggle("tray")
        await asyncio.sleep(0.05)
        if stop:
            await self.svc.toggle("tray")

    def typed(self) -> list[str]:
        return [call.args[0] for call in self.injector.commit_text.call_args_list]

    async def close(self) -> None:
        self.backend.gate.set()
        await self.svc.stop()
        self._server.cancel()


async def test_stop_with_open_turn_yields_its_text(monkeypatch) -> None:
    rig = _Rig(monkeypatch)
    await rig.svc.start()
    await rig.dictate(_frame(1000))  # VAD never signalled end of speech
    assert rig.typed() == ["1000"]
    assert rig.states == [
        SessionState.STARTING, SessionState.RECORDING, SessionState.FINALIZING,
        SessionState.COMPLETED, SessionState.IDLE,
    ]
    await rig.close()


async def test_second_session_does_not_get_first_sessions_audio(monkeypatch) -> None:
    rig = _Rig(monkeypatch)
    await rig.svc.start()
    await rig.dictate(_frame(1000))
    await rig.dictate(_frame(2000))
    assert rig.typed() == ["1000", "2000"]
    await rig.close()


async def test_cancel_then_start_is_clean(monkeypatch) -> None:
    """Cancel while a closed turn is still being transcribed: its late segment
    and its audio must both stay out of the next session."""
    rig = _Rig(monkeypatch, min_turn_s=0)  # the hint closes the turn at once
    await rig.svc.start()
    rig.backend.gate.clear()
    await rig.dictate(_frame(1000, end=True), _frame(1500), stop=False)
    await rig.svc.cancel()
    assert rig.svc.state == SessionState.IDLE

    await rig.dictate(_frame(2000), stop=False)
    rig.backend.gate.set()  # session 1's turn finishes only now
    await rig.svc.toggle("tray")
    assert rig.typed() == ["2000"]
    await rig.close()


async def test_adapter_open_failure_returns_to_idle(monkeypatch) -> None:
    rig = _Rig(monkeypatch)
    await rig.svc.start()
    rig.adapter.open_error = RuntimeError("no microphone")
    await rig.svc.toggle("tray")
    assert rig.svc.state == SessionState.IDLE
    assert SessionState.ERROR in rig.states

    rig.adapter.open_error = None
    await rig.svc.toggle("tray")
    assert rig.svc.state == SessionState.RECORDING
    await rig.close()


async def test_injector_failure_returns_to_idle(monkeypatch) -> None:
    rig = _Rig(monkeypatch)
    await rig.svc.start()
    rig.injector.commit_text.side_effect = RuntimeError("xdotool died")
    await rig.dictate(_frame(1000))
    assert rig.svc.state == SessionState.IDLE

    rig.injector.commit_text.side_effect = None
    await rig.dictate(_frame(2000))
    assert rig.typed()[-1] == "2000"
    await rig.close()


async def test_flush_timeout_injects_nothing_and_recovers(monkeypatch) -> None:
    rig = _Rig(monkeypatch, timeout_s=0.05)
    await rig.svc.start()
    rig.backend.gate.clear()  # transcription never finishes in time
    await rig.dictate(_frame(1000))
    assert rig.svc.state == SessionState.IDLE
    assert rig.typed() == []

    rig.backend.gate.set()
    await rig.dictate(_frame(2000))
    assert rig.typed() == ["2000"]  # the timed-out text does not resurface
    await rig.close()
