"""Real-time streaming speech-to-text via RealtimeSTT.

Words appear as you speak using a dual-model approach:
- ``realtime_model`` (tiny / tiny.en) — fast model running live, confirms
  words every ~50 ms as you speak.
- ``model`` (small / medium) — accurate model runs after end-of-sentence
  and fires the final corrected transcript.

Injection strategy (``inject_on_stable=True``, the default):
    Confirmed words from the fast model are injected immediately so text
    appears while you are still speaking.  When the sentence ends the
    accurate model fires a correction if needed.

Injection strategy (``inject_on_stable=False``):
    Only the final accurate transcript is injected.  Indistinguishable from
    the batch pipeline but with much lower end-of-speech latency because
    RealtimeSTT's WebRTC + Silero dual-VAD is far more responsive than
    Silero alone.

The recogniser runs its capture loop in a daemon thread and communicates
with the asyncio event loop via ``loop.call_soon_threadsafe``.
"""

import asyncio
import logging
import threading
from collections.abc import AsyncGenerator
from typing import Optional

from assistant.config.settings import Settings
from assistant.utils.exceptions import RecognizerError

logger = logging.getLogger(__name__)


class _DeltaTracker:
    """Extract only the *new* portion of cumulative stable text.

    RealtimeSTT's ``on_realtime_transcription_stabilized`` fires with the
    full confirmed text so far (e.g. "Hello" → "Hello world" → "Hello world
    how are").  We need to inject only the delta ("world " → "how are").
    """

    def __init__(self) -> None:
        self._injected = ""

    def delta(self, current: str) -> str:
        """Return only the text appended since the last call.

        Args:
            current: Full cumulative stable text from the fast model.

        Returns:
            New portion to inject, or empty string if nothing new.
        """
        current = current.strip()
        if not current:
            return ""
        if current.startswith(self._injected):
            new = current[len(self._injected):].strip()
            if new:
                self._injected = current
            return new
        # Sentence diverged (correction) — re-inject full stable text
        self._injected = current
        return current

    def reset(self) -> None:
        """Call at the start of each new sentence."""
        self._injected = ""


class RealtimeSpeechRecognizer:
    """Live streaming transcription powered by RealtimeSTT.

    Usage::

        recogniser = RealtimeSpeechRecognizer(settings, loop)
        recogniser.load()          # downloads models on first run
        recogniser.start_thread()  # launch background capture loop

        # Toggle from hotkey:
        recogniser.set_listening(True)
        recogniser.set_listening(False)

        # Consume text:
        async for text in recogniser.stream():
            injector.inject(text)

    Args:
        settings: Application settings (stt.*, vad.*).
        loop: Running asyncio event loop for thread-safe queue push.
        inject_on_stable: If ``True`` (default) inject confirmed words live.
            If ``False`` wait for the accurate model's final sentence.
    """

    def __init__(
        self,
        settings: Settings,
        loop: asyncio.AbstractEventLoop,
        inject_on_stable: bool = True,
    ) -> None:
        self._settings = settings
        self._loop = loop
        self._inject_on_stable = inject_on_stable
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._recorder: Optional[object] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._listening = threading.Event()
        self._delta = _DeltaTracker()

    # ------------------------------------------------------------------
    # Callbacks (called from RealtimeSTT thread)
    # ------------------------------------------------------------------

    def _on_stable(self, text: str) -> None:
        """Inject confirmed words from the fast model immediately."""
        if not self._inject_on_stable:
            return
        delta = self._delta.delta(text)
        if delta:
            logger.debug("Stable delta: %r", delta)
            self._loop.call_soon_threadsafe(self._queue.put_nowait, delta)

    def _on_final(self, text: str) -> None:
        """Inject the accurate final sentence (end-of-utterance)."""
        text = text.strip()
        if not text:
            return
        if self._inject_on_stable:
            # Already injected word-by-word; inject any remainder the fast
            # model missed (delta between stable and final).
            remainder = self._delta.delta(text)
            self._delta.reset()
            if remainder:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, remainder)
        else:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, text)
        self._delta.reset()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Initialise AudioToTextRecorder (blocking — downloads on first run).

        Raises:
            RecognizerError: If RealtimeSTT is not installed or init fails.
        """
        try:
            from RealtimeSTT import AudioToTextRecorder  # noqa: PLC0415
        except ImportError as exc:
            raise RecognizerError(
                "RealtimeSTT not installed — run: pip install RealtimeSTT"
            ) from exc

        stt = self._settings.stt
        vad = self._settings.vad
        realtime_model = getattr(stt, "realtime_model", "tiny")

        try:
            self._recorder = AudioToTextRecorder(
                # Models
                model=stt.model,
                language=stt.language,
                realtime_model_type=realtime_model,
                beam_size=getattr(stt, "beam_size", 1),

                # Live word callbacks
                enable_realtime_transcription=True,
                realtime_processing_pause=0.05,          # update every 50 ms
                on_realtime_transcription_stabilized=self._on_stable,

                # VAD (dual Silero + WebRTC for low-latency detection)
                silero_sensitivity=0.4,
                webrtc_sensitivity=2,
                post_speech_silence_duration=vad.speech_pad_ms / 1000.0,

                # Speed / quality
                # fast_sentence_end=True,
                min_length_of_recording=0.3,
                min_gap_between_recordings=0.0,

                # Misc
                spinner=False,
                use_microphone=True,
                no_log_file=True,
            )
        except Exception as exc:
            raise RecognizerError(
                f"AudioToTextRecorder initialisation failed: {exc}"
            ) from exc

        logger.info(
            "RealtimeSTT ready — model=%s  realtime=%s  inject_on_stable=%s",
            stt.model, realtime_model, self._inject_on_stable,
        )

    def start_thread(self) -> None:
        """Start the background capture + transcription thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._recorder_loop, daemon=True, name="realtime-stt"
        )
        self._thread.start()
        logger.info("RealtimeSTT background thread started")

    def stop(self) -> None:
        """Stop recording and shut down the background thread."""
        self._running = False
        self._listening.clear()
        if self._recorder is not None:
            try:
                self._recorder.abort()  # type: ignore[union-attr]
            except Exception:
                pass

    def set_listening(self, active: bool) -> None:
        """Enable or disable transcription (called from hotkey handler).

        Args:
            active: ``True`` → start capturing; ``False`` → pause.
        """
        if active:
            self._listening.set()
        else:
            self._listening.clear()
            if self._recorder is not None:
                try:
                    self._recorder.abort()  # type: ignore[union-attr]
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _recorder_loop(self) -> None:
        """Run in daemon thread: call recorder.text() in a tight loop."""
        while self._running:
            if not self._listening.wait(timeout=0.1):
                continue  # idle — wait for hotkey
            if not self._running:
                break
            try:
                # text() blocks until one complete utterance is ready;
                # _on_final is called with the accurate result.
                self._recorder.text(self._on_final)  # type: ignore[union-attr]
            except Exception as exc:
                logger.error("Recorder loop error: %s", exc)

    # ------------------------------------------------------------------
    # Async stream
    # ------------------------------------------------------------------

    async def stream(self) -> AsyncGenerator[str, None]:
        """Async generator — yields text chunks to inject.

        Yields:
            Text strings: partial words (fast model) when
            ``inject_on_stable=True``, or complete sentences otherwise.
        """
        while True:
            text = await self._queue.get()
            yield text
