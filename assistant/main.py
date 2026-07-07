"""Voice Assistant — Phase 4 entry point.

Pipeline (Phase 4):

    Microphone → AudioCapture → asyncio.Queue
                                      │
                                 SileroVAD.stream()
                                      │  (only when is_listening is set)
                                 SpeakerVerifier.verify()
                                      │  (is_me=False → skip)
                                 WhisperRecognizer.transcribe()
                                      │
                              ┌── confidence < threshold ──▶ 🟠 tray + notify-send
                              └── confidence OK ──────────▶ XdotoolInjector + stdout

Hotkey toggles is_listening asyncio.Event (pynput in background thread).
Qt event loop + asyncio fused via qasync.

Run:
    python assistant/main.py
    python -m assistant.main

Exit cleanly with Ctrl+C, SIGTERM, or tray "Quit".
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
from assistant.speech.recognizer import WhisperRecognizer
from assistant.speaker.verifier import SpeakerVerifier
from assistant.tray.icon import TrayIcon, TrayState
from assistant.utils.exceptions import InjectorError
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
# Inner pipeline
# ---------------------------------------------------------------------------


async def _run_pipeline(
    audio_queue: asyncio.Queue,
    vad: SileroVAD,
    recognizer: WhisperRecognizer,
    settings: Settings,
    is_listening: asyncio.Event,
    injector: Optional[XdotoolInjector] = None,
    verifier: Optional[SpeakerVerifier] = None,
    tray: Optional[TrayIcon] = None,
) -> None:
    """Drive the VAD → verify → STT → inject / stdout loop.

    Args:
        audio_queue: Queue populated by :class:`AudioCapture`.
        vad: Configured :class:`SileroVAD` instance.
        recognizer: Loaded :class:`WhisperRecognizer` instance.
        settings: Full application settings.
        is_listening: Set = actively transcribing; clear = idle.
        injector: Optional xdotool injector (Linux only).
        verifier: Optional ECAPA-TDNN speaker verifier.
        tray: Optional system tray icon for state updates.
    """
    loop = asyncio.get_running_loop()
    threshold = settings.stt.confidence_threshold

    async for segment in vad.stream(audio_queue):
        # Respect hotkey toggle — discard segment if not listening
        if not is_listening.is_set():
            continue

        # Speaker verification
        if verifier is not None and settings.speaker.enabled:
            is_me, score = await loop.run_in_executor(
                None, verifier.verify, segment
            )
            if not is_me:
                logger.debug("Speaker rejected (score=%.3f)", score)
                continue

        # Transcription
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
                    await loop.run_in_executor(None, injector.inject, result.text)
                except InjectorError as exc:
                    logger.error("Injection failed: %s", exc)


# ---------------------------------------------------------------------------
# Async entry point
# ---------------------------------------------------------------------------


async def main_async() -> None:
    """Initialise all components and run the pipeline until cancelled.

    Sequence:
    1. Load config → logging setup.
    2. Build AudioCapture, SileroVAD, WhisperRecognizer, SpeakerVerifier.
    3. Load models in thread pool.
    4. Create ``is_listening`` asyncio.Event (initially not set → IDLE).
    5. Create TrayIcon (if enabled); connect toggle / enroll / quit callbacks.
    6. Register hotkey via pynput (if pynput installed).
    7. Start audio capture; launch pipeline task.
    8. Install SIGINT / SIGTERM handlers.
    9. On exit: stop capture, stop hotkey listener, log shutdown.
    """
    settings = load_settings()
    setup_logging(
        level=settings.logging.level,
        max_bytes=settings.logging.max_bytes,
        backup_count=settings.logging.backup_count,
    )
    logger.info("Voice Assistant Phase 4 — starting")

    loop = asyncio.get_running_loop()
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
    verifier = SpeakerVerifier(
        voiceprint_path=settings.speaker.voiceprint_path,
        threshold=settings.speaker.threshold,
    )

    # Load models (blocking — may download on first run)
    logger.info("Loading Whisper '%s' model …", settings.stt.model)
    await loop.run_in_executor(None, recognizer.load)
    if settings.speaker.enabled:
        await loop.run_in_executor(None, verifier.load)

    injector: XdotoolInjector | None = XdotoolInjector(
        delay_ms=settings.injection.delay_ms,
        clear_modifiers=settings.injection.clear_modifiers,
    )

    # ------------------------------------------------------------------
    # Shared state — defined before closures that capture them
    # ------------------------------------------------------------------
    is_listening: asyncio.Event = asyncio.Event()
    tray: TrayIcon | None = None
    pipeline_task: asyncio.Task | None = None  # assigned below; closures read it

    def _toggle() -> None:
        """Toggle listening state (runs in Qt / asyncio thread)."""
        if is_listening.is_set():
            is_listening.clear()
            if tray:
                tray.set_state(TrayState.IDLE)
            logger.info("Listening stopped via toggle")
        else:
            is_listening.set()
            if tray:
                tray.set_state(TrayState.LISTENING)
            logger.info("Listening started via toggle")

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
    # Hotkey listener (pynput, background thread)
    # ------------------------------------------------------------------
    hotkey_listener = _start_hotkey(
        settings.hotkey.toggle_listen, _toggle, loop
    )

    await capture.start()

    pipeline_task = asyncio.create_task(
        _run_pipeline(
            audio_queue, vad, recognizer, settings,
            is_listening, injector, verifier, tray,
        ),
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
        signal.signal(
            signal.SIGINT,
            lambda _s, _f: loop.call_soon_threadsafe(_quit),
        )

    logger.info(
        "Ready — press %s to start listening.  Ctrl+C or tray Quit to exit.",
        settings.hotkey.toggle_listen,
    )

    try:
        await pipeline_task
    except asyncio.CancelledError:
        pass
    finally:
        await capture.stop()
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

