"""Voice Assistant — entry point.

Two pipeline modes (set via ``stt.mode`` in config):

  realtime  — RealtimeSTT drives mic capture + VAD + dual-model STT.
              Confirmed words are injected live as you speak (word by word).
              Fastest UX; speaker verification bypassed.

  batch     — Original pipeline: sounddevice → Silero VAD → faster-whisper.
              Full speaker verification and confidence checks available.
              Rolling 2-second chunks give a live feel; final flush on silence.

In both modes:
  • Hotkey toggles is_listening (pynput, background thread)
  • System tray shows state (qasync fuses Qt + asyncio)
  • xdotool window ID captured BEFORE transcription to fix focus-shift bug
  • Ctrl+C / SIGTERM / tray Quit exits cleanly

Run:
    python assistant/main.py
    python -m assistant.main
"""

import asyncio
import logging
import signal
import subprocess
import sys
from datetime import datetime
from typing import Callable, Optional

from assistant.audio.capture import AudioCapture
from assistant.audio.vad import SileroVAD
from assistant.config.settings import Settings, load_settings
from assistant.injector.xdotool import XdotoolInjector
from assistant.speech.realtime import RealtimeSpeechRecognizer
from assistant.speech.recognizer import WhisperRecognizer
from assistant.speaker.verifier import SpeakerVerifier
from assistant.tray.icon import TrayIcon, TrayState
from assistant.utils.exceptions import InjectorError, RecognizerError
from assistant.utils.logging_setup import setup_logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Notification helper
# ---------------------------------------------------------------------------


def _send_notification(message: str, tool: str = "notify-send") -> None:
    """Fire a desktop notification (best-effort, never raises).

    Args:
        message: Notification body text.
        tool: CLI tool name (``notify-send`` or ``libnotify``).
    """
    try:
        subprocess.run(
            [tool, "Voice Assistant", message],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        logger.warning("%s not found — install libnotify-bin", tool)


# ---------------------------------------------------------------------------
# Hotkey helpers
# ---------------------------------------------------------------------------


def _fmt_pynput(combo: str) -> str:
    """Convert ``'ctrl+space'`` → ``'<ctrl>+<space>'`` for pynput.

    Args:
        combo: Hotkey string from config (e.g. ``"ctrl+space"``).

    Returns:
        pynput-compatible hotkey string.
    """
    def _wrap(k: str) -> str:
        k = k.strip().lower()
        return k if len(k) == 1 else f"<{k}>"

    return "+".join(_wrap(p) for p in combo.split("+"))


def _start_hotkey(
    combo: str,
    toggle_fn: Callable[[], None],
    loop: asyncio.AbstractEventLoop,
) -> object:
    """Start a pynput GlobalHotKeys listener in a background thread.

    The *toggle_fn* is scheduled on the asyncio event loop (thread-safe)
    so it is always called from the Qt / asyncio thread.

    Args:
        combo: Hotkey string from config (e.g. ``"ctrl+space"``).
        toggle_fn: Zero-arg callable to invoke on each key press.
        loop: The running asyncio event loop.

    Returns:
        The started :class:`pynput.keyboard.GlobalHotKeys` instance,
        or ``None`` if pynput is not installed.
    """
    try:
        from pynput import keyboard  # noqa: PLC0415
    except ImportError:
        logger.warning("pynput not installed — hotkey disabled (pip install pynput)")
        return None

    pynput_combo = _fmt_pynput(combo)

    def _on_activate() -> None:
        loop.call_soon_threadsafe(toggle_fn)

    listener = keyboard.GlobalHotKeys({pynput_combo: _on_activate})
    listener.start()
    logger.info("Hotkey registered: %s  →  %s", combo, pynput_combo)
    return listener


# ---------------------------------------------------------------------------
# Tray-restore coroutine
# ---------------------------------------------------------------------------


async def _restore_tray(
    tray: TrayIcon,
    is_listening: asyncio.Event,
    delay_s: float = 3.0,
) -> None:
    """Restore tray to LISTENING state after *delay_s* seconds.

    Cancelled silently if the pipeline is stopped before the delay elapses.

    Args:
        tray: :class:`TrayIcon` to update.
        is_listening: If not set by the time the delay elapses, do nothing
            (user stopped listening during the warning window).
        delay_s: Seconds to wait before restoring.
    """
    await asyncio.sleep(delay_s)
    if is_listening.is_set():
        tray.set_state(TrayState.LISTENING)


# ---------------------------------------------------------------------------
# Enroller launcher
# ---------------------------------------------------------------------------


def _launch_enroller(settings: Settings) -> None:
    """Show the enrollment wizard (non-blocking).

    Args:
        settings: App settings passed to :class:`EnrollmentWizard`.
    """
    try:
        from assistant.speaker.enroller import EnrollmentWizard  # noqa: PLC0415

        wizard = EnrollmentWizard(settings)
        wizard.show()
    except Exception as exc:
        logger.error("Failed to open enrollment wizard: %s", exc)


# ---------------------------------------------------------------------------
# ── REALTIME pipeline (RealtimeSTT — live word-by-word injection) ───────────
# ---------------------------------------------------------------------------


async def _run_realtime_pipeline(
    recognizer: RealtimeSpeechRecognizer,
    settings: Settings,
    is_listening: asyncio.Event,
    injector: Optional[XdotoolInjector],
    tray: Optional[TrayIcon],
) -> None:
    """Inject words live as RealtimeSTT confirms them.

    Synchronises ``is_listening`` with the recogniser every 50 ms so the
    hotkey toggle propagates quickly without blocking the event loop.
    """
    loop = asyncio.get_running_loop()
    _last_listening = False

    async def _sync() -> None:
        nonlocal _last_listening
        while True:
            now = is_listening.is_set()
            if now != _last_listening:
                recognizer.set_listening(now)
                _last_listening = now
            await asyncio.sleep(0.05)

    sync_task = asyncio.create_task(_sync(), name="listening-sync")

    try:
        async for text in recognizer.stream():
            if not is_listening.is_set():
                continue

            ts = datetime.now().strftime("%H:%M:%S")
            sys.stdout.write(f"[{ts}] {text}\n")
            sys.stdout.flush()
            logger.info("[%s] %s", ts, text)

            if injector is not None:
                # Capture window NOW — we're about to inject immediately
                window_id = injector.get_focused_window()
                try:
                    await loop.run_in_executor(
                        None, injector.inject, text, window_id
                    )
                except InjectorError as exc:
                    logger.error("Injection failed: %s", exc)
    finally:
        sync_task.cancel()


# ---------------------------------------------------------------------------
# ── BATCH pipeline (Silero VAD + faster-whisper rolling chunks) ─────────────
# ---------------------------------------------------------------------------


async def _run_batch_pipeline(
    audio_queue: asyncio.Queue,
    vad: SileroVAD,
    recognizer: WhisperRecognizer,
    settings: Settings,
    is_listening: asyncio.Event,
    injector: Optional[XdotoolInjector],
    verifier: Optional[SpeakerVerifier],
    tray: Optional[TrayIcon],
) -> None:
    """VAD → speaker verify → Whisper → inject.

    Key fix: ``window_id`` is captured from xdotool *before* the Whisper
    call (which blocks for 0.2-2 s) so text always lands in the window the
    user was typing into, even if focus moves during transcription.
    """
    loop = asyncio.get_running_loop()
    threshold = settings.stt.confidence_threshold

    async for segment in vad.stream(audio_queue):
        if not is_listening.is_set():
            continue

        # ── Speaker verification ───────────────────────────────────────────
        if verifier is not None and settings.speaker.enabled:
            is_me, score = await loop.run_in_executor(
                None, verifier.verify, segment
            )
            if not is_me:
                logger.debug("Speaker rejected (score=%.3f)", score)
                continue

        # ── Capture window BEFORE Whisper blocks (key fix) ─────────────────
        window_id: str | None = None
        if injector is not None:
            window_id = await loop.run_in_executor(
                None, injector.get_focused_window
            )

        # ── Transcription ──────────────────────────────────────────────────
        result = await loop.run_in_executor(None, recognizer.transcribe, segment)
        if result is None:
            continue

        ts = datetime.now().strftime("%H:%M:%S")

        if result.confidence < threshold:
            logger.warning(
                "[%s] [conf=%.2f] LOW CONFIDENCE — discarded: %s",
                ts, result.confidence, result.text,
            )
            if tray:
                tray.set_state(TrayState.LOW_CONFIDENCE_WARNING)
                asyncio.create_task(
                    _restore_tray(tray, is_listening, delay_s=3.0),
                    name="tray-restore",
                )
            _send_notification(
                settings.notifications.low_confidence_message,
                settings.notifications.tool,
            )
        else:
            line = f"[{ts}] [conf={result.confidence:.2f}] {result.text}"
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
            logger.info(line)
            if injector is not None:
                try:
                    await loop.run_in_executor(
                        None, injector.inject, result.text, window_id
                    )
                except InjectorError as exc:
                    logger.error("Injection failed: %s", exc)


# ---------------------------------------------------------------------------
# Async entry point
# ---------------------------------------------------------------------------


async def main_async() -> None:
    """Initialise all components and run the correct pipeline until cancelled."""
    settings = load_settings()
    setup_logging(
        level=settings.logging.level,
        max_bytes=settings.logging.max_bytes,
        backup_count=settings.logging.backup_count,
    )

    mode = getattr(settings.stt, "mode", "realtime")
    logger.info("Voice Assistant starting — mode=%s", mode)

    loop = asyncio.get_running_loop()

    injector: XdotoolInjector | None = XdotoolInjector(
        delay_ms=settings.injection.delay_ms,
        clear_modifiers=settings.injection.clear_modifiers,
    )

    # ------------------------------------------------------------------
    # Shared state (defined before closures that capture them)
    # ------------------------------------------------------------------
    is_listening: asyncio.Event = asyncio.Event()
    tray: TrayIcon | None = None
    pipeline_task: asyncio.Task | None = None

    def _toggle() -> None:
        if is_listening.is_set():
            is_listening.clear()
            if tray:
                tray.set_state(TrayState.IDLE)
            logger.info("Listening stopped")
        else:
            is_listening.set()
            if tray:
                tray.set_state(TrayState.LISTENING)
            logger.info("Listening started")

    def _quit() -> None:
        if pipeline_task is not None:
            pipeline_task.cancel()

    # ------------------------------------------------------------------
    # Tray icon
    # ------------------------------------------------------------------
    if settings.tray.enabled:
        try:
            tray = TrayIcon(
                on_toggle=_toggle,
                on_enroll=lambda: _launch_enroller(settings),
                on_quit=_quit,
            )
        except Exception as exc:
            logger.warning("Tray icon unavailable: %s", exc)

    # ------------------------------------------------------------------
    # Hotkey
    # ------------------------------------------------------------------
    hotkey_listener = _start_hotkey(settings.hotkey.toggle_listen, _toggle, loop)

    # ------------------------------------------------------------------
    # Mode-specific setup and pipeline task
    # ------------------------------------------------------------------
    capture: Optional[AudioCapture] = None
    rt_recognizer: Optional[RealtimeSpeechRecognizer] = None

    if mode == "realtime":
        logger.info("Loading RealtimeSTT — model=%s  realtime=%s",
                    settings.stt.model, getattr(settings.stt, "realtime_model", "tiny"))
        rt_recognizer = RealtimeSpeechRecognizer(settings, loop, inject_on_stable=True)
        try:
            await loop.run_in_executor(None, rt_recognizer.load)
        except RecognizerError as exc:
            logger.error("%s — falling back to batch mode", exc)
            mode = "batch"

    if mode == "batch":
        vad = SileroVAD(
            aggressiveness=settings.vad.aggressiveness,
            speech_pad_ms=settings.vad.speech_pad_ms,
            min_speech_ms=getattr(settings.vad, "min_speech_ms", 400),
            live_chunk_ms=getattr(settings.vad, "live_chunk_ms", 2000),
            sample_rate=settings.audio.sample_rate,
        )
        recognizer = WhisperRecognizer(
            model_size=settings.stt.model,
            device=settings.stt.device,
            compute_type=settings.stt.compute_type,
            language=settings.stt.language,
            beam_size=settings.stt.beam_size,
        )
        verifier = SpeakerVerifier(
            voiceprint_path=settings.speaker.voiceprint_path,
            threshold=settings.speaker.threshold,
        )
        audio_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        capture = AudioCapture(
            queue=audio_queue,
            sample_rate=settings.audio.sample_rate,
            chunk_duration_ms=settings.audio.chunk_duration_ms,
            device=settings.audio.device,
        )

        logger.info("Loading Whisper '%s' …", settings.stt.model)
        await loop.run_in_executor(None, recognizer.load)
        if settings.speaker.enabled:
            await loop.run_in_executor(None, verifier.load)
        await capture.start()

        pipeline_task = asyncio.create_task(
            _run_batch_pipeline(
                audio_queue, vad, recognizer, settings,
                is_listening, injector, verifier, tray,
            ),
            name="pipeline",
        )
    else:
        # realtime mode
        rt_recognizer.start_thread()
        pipeline_task = asyncio.create_task(
            _run_realtime_pipeline(rt_recognizer, settings, is_listening, injector, tray),
            name="pipeline",
        )

    # ------------------------------------------------------------------
    # OS signal handlers
    # ------------------------------------------------------------------
    def _handle_signal(sig: signal.Signals) -> None:
        logger.info("Received %s — shutting down", sig.name)
        _quit()

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal, sig)
    except (NotImplementedError, ValueError):
        signal.signal(signal.SIGINT, lambda _s, _f: loop.call_soon_threadsafe(_quit))

    logger.info(
        "Ready — press %s to start listening.  Ctrl+C or tray Quit to exit.",
        settings.hotkey.toggle_listen,
    )

    try:
        await pipeline_task
    except asyncio.CancelledError:
        pass
    finally:
        if capture is not None:
            await capture.stop()
        if rt_recognizer is not None:
            rt_recognizer.stop()
        if hotkey_listener is not None:
            try:
                hotkey_listener.stop()
            except Exception:
                pass
        logger.info("Voice Assistant stopped cleanly")


# ---------------------------------------------------------------------------
# Synchronous entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Synchronous entry point.

    Uses :mod:`qasync` when available (fuses Qt + asyncio event loops).
    Falls back to plain ``asyncio.run`` when tray is disabled or PyQt6 /
    qasync are not installed.
    """
    try:
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415
        import qasync  # noqa: PLC0415

        app = QApplication(sys.argv)
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
        with loop:
            loop.run_until_complete(main_async())
    except ImportError:
        # qasync / PyQt6 not installed — headless mode (no tray)
        logger.debug("qasync/PyQt6 not available — running in headless mode")
        asyncio.run(main_async())


if __name__ == "__main__":
    main()

