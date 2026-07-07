"""Voice Assistant — Phase 3 entry point.

Pipeline (Phase 3):

    Microphone → AudioCapture → asyncio.Queue
                                      │
                                 SileroVAD.stream()
                                      │  (speech segments)
                                 SpeakerVerifier.verify()  ← Phase 3
                                      │  (is_me=False → skip)
                                 WhisperRecognizer.transcribe()
                                      │
                              XdotoolInjector.inject()
                                      │
                                  stdout  [HH:MM:SS] [conf=0.87] Hello world

Run:
    python assistant/main.py
    python -m assistant.main

Exit cleanly with Ctrl+C or SIGTERM.
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime

from assistant.audio.capture import AudioCapture
from assistant.audio.vad import SileroVAD
from assistant.config.settings import Settings, load_settings
from assistant.injector.xdotool import XdotoolInjector
from assistant.speech.recognizer import WhisperRecognizer
from assistant.speaker.verifier import SpeakerVerifier
from assistant.utils.exceptions import InjectorError
from assistant.utils.logging_setup import setup_logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inner pipeline
# ---------------------------------------------------------------------------


async def _run_pipeline(
    audio_queue: asyncio.Queue,
    vad: SileroVAD,
    recognizer: WhisperRecognizer,
    settings: Settings,
    injector: XdotoolInjector | None = None,
    verifier: SpeakerVerifier | None = None,
) -> None:
    """Drive the VAD → speaker verify → STT → inject/stdout loop.

    Args:
        audio_queue: Queue populated by :class:`AudioCapture`.
        vad: Configured :class:`SileroVAD` instance.
        recognizer: Loaded :class:`WhisperRecognizer` instance.
        settings: Full application settings (threshold, etc.).
        injector: Optional :class:`XdotoolInjector`; ``None`` on non-Linux.
        verifier: Optional :class:`SpeakerVerifier`; skips verification when
            ``None`` or when voiceprint is absent (disabled mode).
    """
    loop = asyncio.get_running_loop()
    threshold = settings.stt.confidence_threshold

    async for segment in vad.stream(audio_queue):
        # Speaker verification — fast, but still run in executor for safety
        if verifier is not None and settings.speaker.enabled:
            is_me, score = await loop.run_in_executor(
                None, verifier.verify, segment
            )
            if not is_me:
                logger.debug("Speaker rejected (score=%.3f) — skipping", score)
                continue

        # Transcription is CPU-intensive — run in thread pool to avoid
        # blocking the event loop and starving the audio queue.
        result = await loop.run_in_executor(None, recognizer.transcribe, segment)

        if result is None:
            continue

        ts = datetime.now().strftime("%H:%M:%S")

        if result.confidence < threshold:
            logger.warning(
                "[%s] [conf=%.2f] LOW CONFIDENCE — discarded: %s",
                ts,
                result.confidence,
                result.text,
            )
        else:
            line = f"[{ts}] [conf={result.confidence:.2f}] {result.text}"
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
            logger.info(line)
            if injector is not None:
                try:
                    await loop.run_in_executor(None, injector.inject, result.text)
                except InjectorError as exc:
                    logger.error("Text injection failed: %s", exc)


# ---------------------------------------------------------------------------
# Async entry point
# ---------------------------------------------------------------------------


async def main_async() -> None:
    """Initialise all components and run the pipeline until cancelled.

    Order of operations:
    1. Load config (copies example if missing).
    2. Set up logging.
    3. Construct AudioCapture, SileroVAD, WhisperRecognizer.
    4. Load Whisper model in thread pool (may download on first run).
    5. Start microphone capture.
    6. Run pipeline task; install SIGINT/SIGTERM handlers.
    7. On exit, stop capture and log clean shutdown.
    """
    settings = load_settings()

    setup_logging(
        level=settings.logging.level,
        max_bytes=settings.logging.max_bytes,
        backup_count=settings.logging.backup_count,
    )

    logger.info("Voice Assistant Phase 3 — starting")

    audio_queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    capture = AudioCapture(
        queue=audio_queue,
        sample_rate=settings.audio.sample_rate,
        chunk_duration_ms=settings.audio.chunk_duration_ms,
        device=settings.audio.device,
    )

    vad = SileroVAD(
        aggressiveness=settings.vad.aggressiveness,
        speech_pad_ms=settings.vad.speech_pad_ms,
        sample_rate=settings.audio.sample_rate,
    )

    recognizer = WhisperRecognizer(
        model_size=settings.stt.model,
        device=settings.stt.device,
        compute_type=settings.stt.compute_type,
        language=settings.stt.language,
        beam_size=settings.stt.beam_size,
    )

    loop = asyncio.get_running_loop()

    logger.info("Loading Whisper '%s' model …", settings.stt.model)
    await loop.run_in_executor(None, recognizer.load)

    verifier = SpeakerVerifier(
        voiceprint_path=settings.speaker.voiceprint_path,
        threshold=settings.speaker.threshold,
    )
    if settings.speaker.enabled:
        logger.info("Loading speaker verifier …")
        await loop.run_in_executor(None, verifier.load)

    await capture.start()

    injector: XdotoolInjector | None = XdotoolInjector(
        delay_ms=settings.injection.delay_ms,
        clear_modifiers=settings.injection.clear_modifiers,
    )

    pipeline_task = asyncio.create_task(
        _run_pipeline(audio_queue, vad, recognizer, settings, injector, verifier),
        name="pipeline",
    )

    def _handle_signal(sig: signal.Signals) -> None:
        logger.info("Received %s — initiating shutdown", sig.name)
        pipeline_task.cancel()

    # add_signal_handler is Unix-only; fall back gracefully on Windows
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal, sig)
    except (NotImplementedError, ValueError):
        # Windows: KeyboardInterrupt will propagate naturally via asyncio.run()
        signal.signal(
            signal.SIGINT,
            lambda _s, _f: loop.call_soon_threadsafe(pipeline_task.cancel),
        )

    logger.info("Listening — speak into the microphone.  Ctrl+C to quit.")

    try:
        await pipeline_task
    except asyncio.CancelledError:
        pass
    finally:
        await capture.stop()
        logger.info("Voice Assistant stopped cleanly")


# ---------------------------------------------------------------------------
# Synchronous entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Synchronous entry point invoked by ``python assistant/main.py``."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
