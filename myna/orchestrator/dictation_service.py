"""DictationService — policy-only session coordinator.

Owns the session lifecycle; delegates audio capture, inference, post-processing,
and text injection to their respective components.  Model-agnostic.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

import numpy as np

from myna.audio.adapter import AudioAdapter
from myna.audio.spectrum import AudioLevel, analyze
from myna.config.settings import Settings
from myna.injection.base import TextOutputBackend
from myna.injection.xdotool import _get_focused_window
from myna.orchestrator.session import DictationSession, SessionState
from myna.orchestrator.corrections import CorrectionMemory
from myna.orchestrator.vocab import PersonalVocab
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
        on_audio_level: Callable[[AudioLevel], None] | None = None,
        on_preview: Callable[[str], Awaitable[str | None]] | None = None,
    ) -> None:
        self._adapter = adapter
        self._client = client
        self._pipeline = pipeline
        self._injector = injector
        self._verifier = verifier
        self._settings = settings
        self._on_low_confidence = on_low_confidence
        self._on_audio_level = on_audio_level
        self._on_preview = on_preview
        # The session fires the callback itself, on every transition.
        self._session = DictationSession(on_change=on_state_change)
        adapter.on_silent = self._on_mic_silent
        self._last_toggle = 0.0
        self._send_task: asyncio.Task[None] | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._pending: list[TranscriptSegment] = []
        self._audio_buf: list[np.ndarray] = []
        self._vocab: PersonalVocab | None = None
        if settings.learning.vocab_enabled:
            self._vocab = PersonalVocab(Path(settings.learning.vocab_path).expanduser())
            self._client.set_hotwords(self._vocab.hotwords())
        self._corrections: CorrectionMemory | None = None
        if settings.learning.corrections_enabled:
            self._corrections = CorrectionMemory(Path(settings.learning.corrections_path).expanduser())

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

    async def toggle(self, source: str = "?") -> None:
        """Toggle: start if IDLE, stop otherwise.

        One physical key press can reach here twice — on X11 the pynput listener
        sees the combo *and* a GNOME custom shortcut fires the DBus ``Toggle``
        method — which would start a session and immediately stop it. Only the
        source named by ``activation.trigger`` is allowed to drive the hotkey;
        the other is dropped no matter how far apart the two land. Timing alone
        can't fix this: the observed gap between the two exceeded any window
        short enough to still permit a deliberate quick stop.

        Args:
            source: Which trigger fired — ``"pynput"``, ``"dbus"``, or ``"tray"``.
                The first two match ``activation.trigger`` values deliberately, so
                ownership is a direct comparison. ``"tray"`` is an explicit click
                and is always allowed.
        """
        owner = self._settings.activation.resolved_trigger()
        if source in ("pynput", "dbus") and source != owner:
            _LOG.info(
                "Toggle from %r ignored — activation.trigger gives the hotkey to %r",
                source, owner,
            )
            return

        # Same-source backstop: key auto-repeat, tray double-click.
        now = time.monotonic()
        elapsed_ms = (now - self._last_toggle) * 1000.0
        if elapsed_ms < self._settings.activation.debounce_ms:
            _LOG.info(
                "Toggle from %r ignored — repeat %.0f ms after last (debounce %d ms)",
                source, elapsed_ms, self._settings.activation.debounce_ms,
            )
            return
        # Stamped before the first await: this is what makes a second toggle
        # arriving mid-start bail out instead of racing the session open.
        self._last_toggle = now
        _LOG.info("Toggle from %r accepted (state=%s)", source, self._session.state)

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

    async def cancel(self) -> None:
        """Abort the active session without injecting anything (e.g. floating-widget Cancel button)."""
        if self._session.is_active:
            await self._cancel_session()

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
        try:
            # pactl / BT profile switch / PortAudio block for up to seconds: keep them off the loop.
            await asyncio.get_running_loop().run_in_executor(None, self._adapter.open)
            await self._session.to_recording()
            self._send_task = asyncio.create_task(self._send_loop(), name="myna-send")
        except Exception:  # noqa: BLE001
            await self._fail("Could not start dictation")

    async def _stop_session(self) -> None:
        try:
            await self._session.to_finalizing()
        except MynaSessionError:
            return

        try:
            await self._stop_capture()
            # Closes the open turn and returns once its segments are in _pending.
            await self._client.flush(self._settings.inference.finalize_timeout_s)

            inject_ok = await self._verify_speaker()
            if inject_ok:
                texts = [self._pipeline.run(seg.text) for seg in self._pending]
                heard = " ".join(t for t in texts if t)
                if self._corrections is not None:
                    heard = self._corrections.apply(heard)  # auto-fix known mishears
                combined = heard
                if heard and self._on_preview is not None and self._settings.injection.preview_before_inject:
                    combined = await self._on_preview(heard)
                if combined:
                    _LOG.info("Injecting %d chars", len(combined))
                    _LOG.debug("Injecting: %r", combined[:80])
                    await self._injector.start_session(self._session.target_window_id)
                    await self._injector.commit_text(combined)
                    await self._injector.end_session()
                    # heard vs combined diff = the training signal: what ASR got
                    # wrong and what the user actually meant.
                    if self._corrections is not None and combined != heard:
                        self._corrections.learn(heard, combined)
                    if self._vocab is not None:
                        self._vocab.update(combined)
                if self._vocab is not None:
                    self._client.set_hotwords(self._vocab.hotwords())

            self._pending.clear()
            self._audio_buf.clear()
            await self._session.to_completed()
        except TimeoutError:
            await self._fail("Transcription timed out — nothing typed")
        except Exception:  # noqa: BLE001
            await self._fail("Dictation failed — nothing typed")

    async def _cancel_session(self) -> None:
        await self._stop_capture()
        await self._reset_server()
        self._pending.clear()
        self._audio_buf.clear()
        await self._session.to_cancelled()

    async def _stop_capture(self) -> None:
        """Stop the send loop and the microphone. Never raises."""
        if self._send_task:
            self._send_task.cancel()
            await asyncio.gather(self._send_task, return_exceptions=True)
            self._send_task = None
        try:
            await asyncio.get_running_loop().run_in_executor(None, self._adapter.close)
        except Exception:  # noqa: BLE001
            _LOG.exception("Closing the audio adapter failed")

    async def _reset_server(self) -> None:
        """Drop the server's open turn so it cannot reach the next session. Never raises."""
        try:
            await self._client.reset()
        except Exception as exc:  # noqa: BLE001 — recv loop reconnects a dead socket
            _LOG.warning("Could not reset inference turn: %s", exc)

    async def _fail(self, what: str) -> None:
        """Failure exit, called from an ``except`` block: log, clean up, back to IDLE."""
        _LOG.exception("%s", what)
        await self._stop_capture()
        await self._reset_server()
        self._pending.clear()
        self._audio_buf.clear()
        await self._session.to_error()
        await _notify(self._settings, f"⚠️ {what}")

    # ------------------------------------------------------------------
    # Async loops
    # ------------------------------------------------------------------

    async def _send_loop(self) -> None:
        try:
            async for frame in self._adapter.stream():
                pcm = np.frombuffer(frame.data, dtype=np.int16)
                if self._settings.speaker.enabled:
                    self._audio_buf.append(pcm.astype(np.float32) / 32_767.0)
                if self._on_audio_level is not None and len(pcm):
                    self._on_audio_level(analyze(pcm))
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
                        _LOG.info("Segment rejected conf=%.2f < %.2f (%d chars)", seg.confidence, threshold, len(seg.text))
                        _LOG.debug("Rejected segment: %r", seg.text[:40])
                        await _notify(self._settings, self._settings.notifications.low_confidence_message)
                        if self._on_low_confidence is not None:
                            self._on_low_confidence()
                        continue
                    _LOG.info("Segment accepted conf=%.2f (%d chars)", seg.confidence, len(seg.text))
                    _LOG.debug("Accepted segment: %r", seg.text[:60])
                    self._pending.append(seg)
                # Iteration ending without an error means the socket closed
                # cleanly; without this the loop would spin on the dead socket.
                raise ConnectionError("inference socket closed")
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

    def _on_mic_silent(self, label: str) -> None:
        asyncio.ensure_future(_notify(self._settings, f"⚠️ Mic silent: {label}"))

    async def _verify_speaker(self) -> bool:
        if not self._settings.speaker.enabled or not self._audio_buf:
            return True
        audio = np.concatenate(self._audio_buf)
        loop = asyncio.get_running_loop()
        is_me, score = await loop.run_in_executor(None, self._verifier.verify, audio)
        if not is_me:
            _LOG.info("Speaker verification failed (score=%.2f) — discarded", score)
            await _notify(self._settings, "⚠️ Speaker not recognised — text discarded")
        elif (
            self._settings.learning.adapt_voiceprint
            and score < 1.0  # 1.0 = passthrough, nothing to learn from
            and score >= self._settings.speaker.threshold + 0.05
        ):
            # Confident match → nudge voiceprint toward current voice/headset.
            # Margin guard keeps borderline (possibly impostor) audio out.
            await loop.run_in_executor(None, self._verifier.adapt, audio)
        return is_me


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
