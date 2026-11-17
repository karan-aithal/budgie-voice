"""DictationService — policy-only session coordinator.

Owns the session lifecycle; delegates audio capture, inference, post-processing,
and text injection to their respective components.  Model-agnostic.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

import numpy as np

from budgie_voice.audio.adapter import AudioAdapter
from budgie_voice.audio.spectrum import AudioLevel, analyze
from budgie_voice.config.settings import Settings
from budgie_voice.injection.base import TextOutputBackend
from budgie_voice.injection.secure import SecureFieldDetector
from budgie_voice.injection.xdotool import _get_focused_window
from budgie_voice.orchestrator.session import DictationSession, SessionState
from budgie_voice.orchestrator.corrections import CorrectionMemory
from budgie_voice.orchestrator.vocab import PersonalVocab
from budgie_voice.postprocessing.pipeline import PostProcessingPipeline
from budgie_voice.utils import media, selection
from budgie_voice.utils.exceptions import BudgieSessionError
from budgie_voice.utils.notify import notify as _notify  # tests patch this name

if TYPE_CHECKING:
    from budgie_voice.inference.client import InferenceClient
    from budgie_voice.inference.protocol import TranscriptSegment
    from budgie_voice.orchestrator.llm import LlmClient
    from budgie_voice.speaker.verifier import SpeakerVerifier

_LOG = logging.getLogger(__name__)


def _build_llm(settings: Settings) -> LlmClient | None:
    """None unless llm.enabled; disabled means no import and no network, ever."""
    if not settings.llm.enabled:
        return None
    from budgie_voice.orchestrator.llm import LlmClient  # noqa: PLC0415 — only when enabled

    try:
        return LlmClient(settings.llm)
    except ValueError as exc:
        _LOG.error("LLM features disabled: %s", exc)
        return None


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
        self._last_transcript = ""  # memory only, never written to disk
        self._audio_buf: list[np.ndarray] = []
        self._profile: dict[str, object] | None = None  # per-app overrides, set per session
        self._wayland_profile_logged = False
        self._paused_players: list[str] = []  # media we paused, to resume at the end
        self._llm = _build_llm(settings)
        self._command_sel: str | None = None  # command mode: the text being rewritten, memory only
        # Progressive typing (injection.progressive): _typed segments of _pending have been
        # consumed, under _type_lock, so each is typed exactly once and in order.
        self._progressive = False
        self._progressive_warned = False
        self._typed = 0
        self._typed_chars = 0
        self._type_failed = False
        self._type_lock = asyncio.Lock()
        self._type_tasks: set[asyncio.Task[None]] = set()
        # The injector a progressive session started on. apply() may swap self._injector
        # mid-dictation; the session keeps typing through (and ends) the one that holds
        # its locked window. The swapped-in injector serves the next session.
        self._prog_injector: TextOutputBackend | None = None
        self._vocab: PersonalVocab | None = None
        if settings.learning.vocab_enabled:
            self._vocab = PersonalVocab(Path(settings.learning.vocab_path).expanduser())
        self._corrections: CorrectionMemory | None = None
        if settings.learning.corrections_enabled:
            self._corrections = CorrectionMemory(Path(settings.learning.corrections_path).expanduser())
        self._refresh_hotwords()

    @property
    def settings(self) -> Settings:
        return self._settings

    async def notify(self, message: str) -> None:
        """Desktop notification, for callers outside the service (reload, startup)."""
        await _notify(self._settings, message)

    def apply(
        self, settings: Settings, pipeline: PostProcessingPipeline, injector: TextOutputBackend
    ) -> None:
        """Swap in reloaded settings, post-processing pipeline and injector.

        Applies from the next session; main builds the pipeline and injector,
        so this class stays policy-only.
        """
        self._settings = settings
        self._pipeline = pipeline
        self._injector = injector
        self._llm = _build_llm(settings)
        self._progressive_warned = False  # a changed setting may deserve a fresh notification
        self._refresh_hotwords()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the persistent segment-receive loop."""
        self._recv_task = asyncio.create_task(self._recv_loop(), name="budgie-voice-recv")

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
            source: Which trigger fired — ``"pynput"``, ``"evdev"``, ``"dbus"``, or
                ``"tray"``. The first three match ``activation.trigger`` values
                deliberately, so ownership is a direct comparison. ``"tray"`` is an
                explicit click and is always allowed.
        """
        owner = self._settings.activation.resolved_trigger()
        if source in ("pynput", "evdev", "dbus") and source != owner:
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

    async def command(self) -> None:
        """Command mode, toggle style: start on the highlighted text, or stop and rewrite it."""
        if self._session.state == SessionState.IDLE:
            await self.command_start()
        else:
            await self._stop_session()

    async def command_start(self) -> None:
        """Command mode: record a spoken instruction for the highlighted text.

        Hold mode ends it with ``push_stop``. Nothing happens without a selection or
        without a working LLM; the user is told which.
        """
        if self._session.state != SessionState.IDLE:
            return
        if self._llm is None:
            await _notify(self._settings, "⚠️ Command mode needs llm.enabled with a valid llm.url and llm.model")
            return
        sel = await asyncio.get_running_loop().run_in_executor(None, selection.read_primary)
        if not sel.strip():
            await _notify(self._settings, "⚠️ Select some text first — nothing changed")
            return
        await self._start_session(sel)

    async def cancel(self) -> None:
        """Abort the active session without injecting anything (e.g. floating-widget Cancel button)."""
        if self._session.is_active:
            await self._cancel_session()

    @property
    def last_transcript(self) -> str:
        """Final text of the most recent dictation, kept so a failed typing can be recovered."""
        return self._last_transcript

    @property
    def typed_chars(self) -> int:
        """Characters typed into the target by the current or last session (0 if none).

        A count only, never text: Settings → Overview shows it next to each dictation.
        """
        return self._typed_chars

    @property
    def state(self) -> SessionState:
        return self._session.state

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _start_session(self, sel: str | None = None) -> None:
        self._command_sel = sel  # every start resets it, so a dictation is never a command
        window_id = _get_focused_window()

        if self._settings.injection.block_secure_fields and self._injector.is_secure_field(window_id):
            _LOG.info("Secure field — session blocked")
            await _notify(self._settings, self._settings.notifications.secure_field_message)
            return

        self._pending.clear()
        self._audio_buf.clear()
        self._typed = self._typed_chars = 0
        self._type_failed = False
        self._refresh_hotwords()  # picks up dictionary edits without a restart
        self._profile = self._resolve_profile(window_id)

        await self._session.to_starting(window_id)
        try:
            if self._settings.activation.pause_media:
                self._paused_players = await asyncio.get_running_loop().run_in_executor(
                    None, media.pause_playing
                )
            # pactl / BT profile switch / PortAudio block for up to seconds: keep them off the loop.
            await asyncio.get_running_loop().run_in_executor(None, self._adapter.open)
            await self._session.to_recording()
            self._progressive = await self._use_progressive(sel)
            if self._progressive:
                self._last_transcript = ""  # rebuilt turn by turn, for Copy Last Transcript
                self._prog_injector = self._injector  # pinned before the first await: _fail can always end it
                await self._prog_injector.start_session(window_id)
            self._send_task = asyncio.create_task(self._send_loop(), name="budgie-voice-send")
        except Exception:  # noqa: BLE001
            await self._fail("Could not start dictation")

    async def _stop_session(self) -> None:
        try:
            await self._session.to_finalizing()
        except BudgieSessionError:
            return

        sel, self._command_sel = self._command_sel, None
        stopped_at = time.monotonic()
        text_ready = False
        try:
            await self._stop_capture()
            # Closes the open turn and returns once its segments are in _pending.
            await self._client.flush(self._settings.inference.finalize_timeout_s)
            _LOG.info(
                "Stop-to-text %.2f s (%d segments)", time.monotonic() - stopped_at, len(self._pending),
            )

            inject_ok = await self._verify_speaker()
            if self._progressive:
                # Earlier turns were typed while speaking; the flush above added the last.
                await self._type_new()
                typed = await self._end_progressive()
                text_ready = bool(self._last_transcript)
                _LOG.info("Progressive typing done: %d chars typed", typed)
                if self._vocab is not None:
                    self._vocab.update(self._last_transcript)
            elif inject_ok:
                # One pipeline pass over the whole dictation: per-segment passes put a
                # full stop and a capital at every pause.
                heard = self._clean(" ".join(seg.text for seg in self._pending if seg.text))
                text = heard
                if sel is not None:
                    text = await self._rewrite(sel, heard)  # heard is the spoken instruction
                elif self._llm is not None:
                    text = await self._llm.polish(heard)
                _LOG.info(
                    "Stop-to-preview %.2f s (llm=%s)", time.monotonic() - stopped_at,
                    "on" if self._llm is not None else "off",
                )
                combined = text
                if text and self._on_preview is not None and self._settings.injection.preview_before_inject:
                    combined = await self._on_preview(text)
                if combined:
                    self._last_transcript = combined
                    text_ready = True
                    _LOG.info("Injecting %d chars", len(combined))
                    _LOG.debug("Injecting: %r", combined[:80])
                    await self._injector.start_session(self._session.target_window_id)
                    await self._injector.commit_text(combined)
                    self._typed_chars = len(combined)  # same counter progressive typing keeps
                    await self._injector.end_session()
                    # heard vs combined diff = the training signal: what ASR got
                    # wrong and what the user actually meant. Only when the user edited
                    # the preview (combined != text): an untouched polished text must not
                    # teach the LLM's rewording as ASR mishears. Not for command mode.
                    if self._corrections is not None and sel is None and combined != text:
                        self._corrections.learn(heard, combined)
                    if self._vocab is not None:
                        self._vocab.update(combined)

            self._pending.clear()
            self._audio_buf.clear()
            await self._session.to_completed()
        except TimeoutError:
            await self._fail(
                "Transcription timed out — the last part was not typed; earlier text stays"
                if self._progressive and self._typed_chars else "Transcription timed out — nothing typed"
            )
        except Exception:  # noqa: BLE001
            await self._fail(
                "Typing failed — text kept: tray → Copy Last Transcript" if text_ready
                else "Dictation failed — nothing typed"
            )

    async def _rewrite(self, sel: str, instruction: str) -> str:
        """Command mode: the rewritten text, or "" after telling the user the selection is untouched."""
        out = await self._llm.rewrite(sel, instruction) if self._llm is not None and instruction else None
        if out is None:
            await _notify(self._settings, "⚠️ Command failed — selection unchanged")
            return ""
        return out

    def _clean(self, raw: str) -> str:
        """Post-process transcript text: the pipeline (with per-app overrides), then known mishears."""
        pipeline = self._pipeline.with_overrides(self._profile) if self._profile else self._pipeline
        text = pipeline.run(raw)
        return self._corrections.apply(text) if self._corrections is not None else text

    async def _use_progressive(self, sel: str | None) -> bool:
        """Whether this session types turn by turn. Safety gates, never preferences.

        Off (and said so once, in the log and a notification) while the preview is on or
        speaker verification is on; silently off for command mode, which is not a plain
        dictation.
        """
        inj = self._settings.injection
        if not inj.progressive or sel is not None:
            return False
        if self._settings.speaker.enabled:
            reason = "speaker.enabled is on — typing before verification would bypass it"
        elif inj.preview_before_inject:
            reason = "injection.preview_before_inject is on — the preview is never bypassed"
        else:
            return True
        if not self._progressive_warned:
            self._progressive_warned = True
            _LOG.warning("injection.progressive ignored: %s", reason)
            await _notify(self._settings, f"⚠️ Progressive typing is off: {reason}")
        return False

    def _spawn_typing(self) -> None:
        task = asyncio.create_task(self._type_new(), name="budgie-voice-type")
        self._type_tasks.add(task)
        task.add_done_callback(self._type_tasks.discard)

    async def _type_new(self) -> None:
        """Type the segments that arrived since the last call: in order, exactly once.

        Joined to what is already typed by a single space. Never edits typed text. The
        counter moves before typing, so a failure can never cause a retype. Types through
        the session's pinned injector, whatever a settings reload has swapped in since.
        """
        async with self._type_lock:
            if not self._progressive or self._prog_injector is None:  # ended meanwhile: stale task
                return
            segs, self._typed = self._pending[self._typed:], len(self._pending)
            text = self._clean(" ".join(s.text for s in segs if s.text))
            if not text:
                return
            if self._last_transcript:
                text = " " + text
            self._last_transcript += text
            if self._type_failed:
                return  # an earlier turn failed: keep the rest for Copy Last Transcript, never out of order
            try:
                await self._prog_injector.commit_text(text)
            except Exception:  # noqa: BLE001
                self._type_failed = True
                _LOG.exception("Progressive typing failed")
                await _notify(
                    self._settings,
                    "⚠️ Typing failed — the rest is kept: tray → Copy Last Transcript",
                )
                return
            self._typed_chars += len(text)
            _LOG.info("Progressive: typed %d chars (%d so far)", len(text), self._typed_chars)
            _LOG.debug("Progressive typed: %r", text[:80])

    async def _end_progressive(self) -> int:
        """Leave progressive mode and release the injector session; returns chars typed. Never raises.

        Waits for a turn being typed right now: typing already under way is never interrupted.
        """
        if not self._progressive:
            return 0
        async with self._type_lock:
            self._progressive = False  # queued typing tasks now find nothing to do
            typed = self._typed_chars
            injector, self._prog_injector = self._prog_injector, None
        try:
            if injector is not None:
                await injector.end_session()
        except Exception:  # noqa: BLE001
            _LOG.warning("Ending the progressive typing session failed", exc_info=True)
        return typed

    async def _cancel_session(self) -> None:
        self._command_sel = None
        await self._stop_capture()
        await self._reset_server()
        typed = await self._end_progressive()  # never deletes what was typed
        self._pending.clear()
        self._audio_buf.clear()
        await self._session.to_cancelled()
        if typed:
            await _notify(
                self._settings,
                f"⚠️ Cancelled — {typed} characters were already typed and stay where they are",
            )

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
        if self._paused_players:  # every way a session ends passes through here
            players, self._paused_players = self._paused_players, []
            await asyncio.get_running_loop().run_in_executor(None, media.resume, players)

    def _resolve_profile(self, window_id: int | None) -> dict[str, object] | None:
        """Per-app overrides for this session: first profile whose regex hits the window's WM_CLASS.

        X11 only — on Wayland there is no WM_CLASS to read, so the default profile applies.
        """
        profiles = self._settings.postprocessing.profiles
        if not profiles:
            return None
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            if not self._wayland_profile_logged:
                self._wayland_profile_logged = True
                _LOG.info("Per-app profiles need X11 (no WM_CLASS on Wayland) — default profile applies")
            return None
        if window_id is None:
            return None
        wm_class = SecureFieldDetector._xprop(window_id, "WM_CLASS")  # noqa: SLF001 — shared xprop helper
        for profile in profiles:
            try:
                if re.search(str(profile["match"]), wm_class, re.IGNORECASE):
                    _LOG.info("Profile %r applies", profile["match"])
                    return {k: v for k, v in profile.items() if k != "match"}
            except (KeyError, re.error) as exc:
                _LOG.warning("Ignoring bad profile %r (%s: %s)", profile, type(exc).__name__, exc)
        return None

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
        await self._end_progressive()
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
                    # confidence is None for backends that do not measure it (Parakeet)
                    if seg.confidence is not None and seg.confidence < threshold:
                        _LOG.info("Segment rejected conf=%.2f < %.2f (%d chars)", seg.confidence, threshold, len(seg.text))
                        _LOG.debug("Rejected segment: %r", seg.text[:40])
                        await _notify(self._settings, self._settings.notifications.low_confidence_message)
                        if self._on_low_confidence is not None:
                            self._on_low_confidence()
                        continue
                    conf = "n/a" if seg.confidence is None else f"{seg.confidence:.2f}"
                    _LOG.info("Segment accepted conf=%s (%d chars)", conf, len(seg.text))
                    _LOG.debug("Accepted segment: %r", seg.text[:60])
                    self._pending.append(seg)
                    if self._progressive:
                        self._spawn_typing()  # a task: typing must not hold up segment intake
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

    def _refresh_hotwords(self) -> None:
        """Hotwords: dictionary terms, then preview-confirmed corrections, then learned vocab."""
        terms = _read_terms(Path(self._settings.learning.dictionary_path).expanduser())
        if self._corrections is not None:
            terms += self._corrections.targets()
        if self._vocab is not None:
            terms += (self._vocab.hotwords() or "").split()
        # De-duplicate, then cap: at most 100 terms of at most 64 characters each.
        kept = [t for t in dict.fromkeys(terms) if len(t) <= _MAX_HOTWORD_CHARS][:_MAX_HOTWORDS]
        if len(kept) < len(set(terms)):
            _LOG.debug("Hotwords capped: kept %d of %d terms", len(kept), len(set(terms)))
        self._client.set_hotwords(" ".join(kept) or None)

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


_MAX_HOTWORDS = 100
_MAX_HOTWORD_CHARS = 64


def _read_terms(path: Path) -> list[str]:
    """One term per line; blank lines and ``#`` comments ignored. [] if unreadable."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [t for line in lines if (t := line.strip()) and not t.startswith("#")]
