"""DictationService — policy-only session coordinator.

Owns the session lifecycle; delegates audio capture, inference, post-processing,
and text injection to their respective components.  Model-agnostic.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import TYPE_CHECKING, Callable

import numpy as np

from myna.audio.adapter import AudioAdapter
from myna.config.settings import Settings
from myna.injection.base import TextOutputBackend
from myna.injection.xdotool import _get_focused_window
from myna.orchestrator.session import DictationSession, SessionState
from myna.postprocessing.pipeline import PostProcessingPipeline
from myna.utils.exceptions import MynaSessionError

if TYPE_CHECKING:
    from myna.inference.client import InferenceClient
    from myna.inference.protocol import TranscriptSegment
    from myna.speaker.verifier import SpeakerVerifier

_LOG = logging.getLogger(__name__)


class DictationService:
    """Coordinates AudioAdapter, InferenceClient, pipeline, and injector.

    Args:
        adapter: Audio capture chain (closed between sessions).
        client: Persistent inference WebSocket client.
        pipeline: Post-processing pipeline.
        injector: Text injection backend.
        verifier: Speaker verifier (may be disabled).
        settings: Application settings.
        on_state_change: Optional callback invoked on every state transition.
    """

    def __init__(
        self,
        adapter: AudioAdapter,
        client: InferenceClient,
        pipeline: PostProcessingPipeline,
        injector: TextOutputBackend,
        verifier: SpeakerVerifier,
        settings: Settings,
        on_state_change: Callable[[SessionState], None] | None = None,
        on_low_confidence: Callable[[], None] | None = None,
    ) -> None:
        self._adapter = adapter
        self._client = client
        self._pipeline = pipeline
        self._injector = injector
        self._verifier = verifier
        self._settings = settings
        self._on_state_change = on_state_change
        self._on_low_confidence = on_low_confidence
        self._session = DictationSession()
        self._send_task: asyncio.Task[None] | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._pending: list[TranscriptSegment] = []
        self._audio_buf: list[np.ndarray] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the persistent segment-receive loop."""
        self._recv_task = asyncio.create_task(self._recv_loop(), name="myna-recv")

    async def stop(self) -> None:
        """Graceful shutdown — cancel active session then recv loop."""
        if self._session.is_active:
            await self._cancel_session()
        if self._recv_task:
            self._recv_task.cancel()
            await asyncio.gather(self._recv_task, return_exceptions=True)

    # ------------------------------------------------------------------
    # Hotkey callbacks (called thread-safely via run_coroutine_threadsafe)
    # ------------------------------------------------------------------

    async def toggle(self) -> None:
        """Toggle: start if IDLE, stop otherwise."""
        if self._session.state == SessionState.IDLE:
            await self._start_session()
        else:
            await self._stop_session()

    async def push_start(self) -> None:
        """Push-to-hold: begin session."""
        if self._session.state == SessionState.IDLE:
            await self._start_session()

    async def push_stop(self) -> None:
        """Push-to-hold: end session."""
        if self._session.is_active:
            await self._stop_session()

    @property
    def state(self) -> SessionState:
        return self._session.state

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _start_session(self) -> None:
        window_id = _get_focused_window()

        if self._settings.injection.block_secure_fields and self._injector.is_secure_field(window_id):
            _LOG.info("Secure field — session blocked")
            await _notify(self._settings, self._settings.notifications.secure_field_message)
            return

        self._pending.clear()
        self._audio_buf.clear()

        await self._session.to_starting(window_id)
        self._adapter.open()
        await self._session.to_recording()
        self._send_task = asyncio.create_task(self._send_loop(), name="myna-send")
        self._fire_state()

    async def _stop_session(self) -> None:
        try:
            await self._session.to_finalizing()
        except MynaSessionError:
            return

        if self._send_task:
            self._send_task.cancel()
            await asyncio.gather(self._send_task, return_exceptions=True)
        self._adapter.close()
        await asyncio.sleep(0.4)  # drain server buffer

        inject_ok = await self._verify_speaker()
        if inject_ok:
            for seg in self._pending:
                text = self._pipeline.run(seg.text)
                if text:
                    _LOG.info("Injecting: conf=%.2f %r", seg.confidence, text[:80])
                    await self._injector.start_session(self._session.target_window_id)
                    await self._injector.commit_text(text)
                    await self._injector.end_session()

        self._pending.clear()
        self._audio_buf.clear()
        await self._session.to_completed()
        self._fire_state()

    async def _cancel_session(self) -> None:
        if self._send_task:
            self._send_task.cancel()
            await asyncio.gather(self._send_task, return_exceptions=True)
        try:
            self._adapter.close()
        except Exception:
            pass
        self._pending.clear()
        self._audio_buf.clear()
        await self._session.to_cancelled()
        self._fire_state()

    # ------------------------------------------------------------------
    # Async loops
    # ------------------------------------------------------------------

    async def _send_loop(self) -> None:
        try:
            async for frame in self._adapter.stream():
                if self._settings.speaker.enabled:
                    pcm = np.frombuffer(frame.data, dtype=np.int16)
                    self._audio_buf.append(pcm.astype(np.float32) / 32_767.0)
                await self._client.send_frame(frame)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            _LOG.warning("Send loop error: %s", exc)

    async def _recv_loop(self) -> None:
        while True:
            threshold = self._settings.inference.confidence_threshold
            try:
                async for seg in self._client.receive_segments():
                    if not self._session.is_active or not seg.is_final:
                        continue
                    if seg.confidence < threshold:
                        _LOG.info("Segment rejected conf=%.2f < %.2f: %r", seg.confidence, threshold, seg.text[:40])
                        await _notify(self._settings, self._settings.notifications.low_confidence_message)
                        if self._on_low_confidence is not None:
                            self._on_low_confidence()
                        continue
                    _LOG.info("Segment accepted conf=%.2f: %r", seg.confidence, seg.text[:60])
                    self._pending.append(seg)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                _LOG.warning("Recv loop disconnected: %s — reconnecting in 2 s", exc)
                await asyncio.sleep(2.0)
                try:
                    await self._client.connect(self._settings.inference.language)
                except Exception as conn_exc:
                    _LOG.warning("Reconnect failed: %s — retrying", conn_exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _verify_speaker(self) -> bool:
        if not self._settings.speaker.enabled or not self._audio_buf:
            return True
        audio = np.concatenate(self._audio_buf)
        loop = asyncio.get_running_loop()
        is_me, score = await loop.run_in_executor(None, self._verifier.verify, audio)
        if not is_me:
            _LOG.info("Speaker verification failed (score=%.2f) — discarded", score)
            await _notify(self._settings, "⚠️ Speaker not recognised — text discarded")
        return is_me

    def _fire_state(self) -> None:
        if self._on_state_change is not None:
            self._on_state_change(self._session.state)


async def _notify(settings: Settings, message: str) -> None:
    try:
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [settings.notifications.tool, "Myna", message],
                capture_output=True,
                timeout=3,
            ),
        )
    except Exception:
        pass
