"""Myna Phase 4 entry point.

Adds system tray, low-confidence indicator, secure-field blocking,
and Settings UI.  Uses qasync to run asyncio inside the Qt event loop.

Run:
    python -m myna.main
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Callable

from myna.audio.adapter import AudioAdapter
from myna.config.settings import Settings, load_settings
from myna.dbus.service import MynaDBusService
from myna.inference.backends.registry import get_backend
from myna.inference.client import InferenceClient
from myna.inference.server import InferenceServer
from myna.injection.base import TextOutputBackend
from myna.injection.selector import select_backend
from myna.orchestrator.dictation_service import DictationService
from myna.orchestrator.hotkey import HotkeyManager
from myna.orchestrator.session import SessionState
from myna.postprocessing.pipeline import PostProcessingPipeline
from myna.speaker.verifier import SpeakerVerifier
from myna.utils.logging_setup import setup_logging

_LOG = logging.getLogger(__name__)

# SessionState → TrayState mapping (imported lazily to allow headless mode)
_SESSION_TO_TRAY: dict[SessionState, str] = {
    SessionState.IDLE:         "idle",
    SessionState.STARTING:     "starting",
    SessionState.RECORDING:    "recording",
    SessionState.TRANSCRIBING: "transcribing",
    SessionState.FINALIZING:   "finalizing",
    SessionState.COMPLETED:    "completed",
    SessionState.CANCELLED:    "cancelled",
    SessionState.ERROR:        "error",
}


async def main_async() -> None:
    """Full async startup — works headless or with Qt tray."""
    settings = load_settings()
    setup_logging(settings.logging.level)
    loop = asyncio.get_running_loop()

    _LOG.info("Myna starting (Phase 4)")

    # ------------------------------------------------------------------
    # Inference backend + server
    # ------------------------------------------------------------------
    backend = get_backend(settings.inference.backend)
    await backend.load(
        settings.inference.model,
        settings.inference.device,
        settings.inference.compute_type,
        beam_size=settings.inference.beam_size,
        no_speech_threshold=settings.inference.no_speech_threshold,
        log_prob_threshold=settings.inference.log_prob_threshold,
        compression_ratio_threshold=settings.inference.compression_ratio_threshold,
    )

    server = InferenceServer(backend, port=settings.inference.server_port)
    server.start()
    await asyncio.sleep(0.3)

    client = InferenceClient(port=settings.inference.server_port)
    await client.connect(language=settings.inference.language)

    # ------------------------------------------------------------------
    # Audio + pipeline + injection
    # ------------------------------------------------------------------
    adapter = AudioAdapter(settings.audio, settings.vad, loop)

    pp = settings.postprocessing
    pipeline = PostProcessingPipeline(
        normalize_numbers=pp.normalize_numbers,
        auto_capitalize=pp.auto_capitalize,
        punctuation_commands=pp.punctuation_commands,
        language_cleanup=pp.language_cleanup,
    )

    injector: TextOutputBackend
    try:
        injector = select_backend(settings.injection.backend, delay_ms=settings.injection.delay_ms)
    except Exception as exc:
        _LOG.warning("Injection backend unavailable (%s) — null fallback", exc)
        injector = _NullBackend()

    # ------------------------------------------------------------------
    # Speaker verifier
    # ------------------------------------------------------------------
    vp_path = Path(settings.speaker.voiceprint_path).expanduser()
    verifier = SpeakerVerifier(vp_path, threshold=settings.speaker.threshold)
    if settings.speaker.enabled:
        try:
            await loop.run_in_executor(None, verifier.load)
        except Exception as exc:
            _LOG.warning("SpeakerVerifier: %s", exc)

    # ------------------------------------------------------------------
    # Callback chains (populated after tray is created)
    # ------------------------------------------------------------------
    _state_cbs: list[Callable[[SessionState], None]] = []
    _low_conf_cbs: list[Callable[[], None]] = []
    _level_cbs: list[Callable[[float], None]] = []

    def _on_state(state: SessionState) -> None:
        for cb in _state_cbs:
            cb(state)

    def _on_low_conf() -> None:
        for cb in _low_conf_cbs:
            cb()

    def _on_level(level: float) -> None:
        for cb in _level_cbs:
            cb(level)

    # ------------------------------------------------------------------
    # DictationService
    # ------------------------------------------------------------------
    dict_svc = DictationService(
        adapter=adapter, client=client, pipeline=pipeline,
        injector=injector, verifier=verifier, settings=settings,
        on_state_change=_on_state, on_low_confidence=_on_low_conf,
        on_audio_level=_on_level,
    )

    # ------------------------------------------------------------------
    # DBus service (with settings reload)
    # ------------------------------------------------------------------
    def _reload_settings() -> None:
        old_inf = dict_svc._settings.inference
        new = load_settings()
        dict_svc._settings = new
        # Propagate new audio settings into the live adapter + capture
        adapter._audio_settings = new.audio
        adapter._capture._settings = new.audio
        _LOG.info("Settings reloaded via DBus")

        new_inf = new.inference
        if (
            old_inf.model != new_inf.model
            or old_inf.device != new_inf.device
            or old_inf.compute_type != new_inf.compute_type
            or old_inf.backend != new_inf.backend
        ):
            _LOG.info(
                "Inference settings changed (%s → %s) — hot-reloading backend",
                old_inf.model, new_inf.model,
            )
            asyncio.run_coroutine_threadsafe(
                server.reload_backend(
                    new_inf.model,
                    new_inf.device,
                    new_inf.compute_type,
                    beam_size=new_inf.beam_size,
                    no_speech_threshold=new_inf.no_speech_threshold,
                    log_prob_threshold=new_inf.log_prob_threshold,
                    compression_ratio_threshold=new_inf.compression_ratio_threshold,
                ),
                loop,
            )

    dbus_svc = MynaDBusService(dict_svc, loop, on_reload=_reload_settings)
    _state_cbs.append(lambda s: dbus_svc.emit_state_changed(str(s)))
    dbus_svc.start()

    await dict_svc.start()

    # ------------------------------------------------------------------
    # System tray (Qt required; degrade gracefully)
    # ------------------------------------------------------------------
    stop_event = asyncio.Event()

    try:
        from myna.indicator.recorder_widget import FloatingRecorderWidget  # noqa: PLC0415
        from myna.indicator.tray import TrayIcon, TrayState  # noqa: PLC0415
        from myna.settings_ui.window import SettingsWindow  # noqa: PLC0415
        from myna.speaker.enroller import EnrollmentWizard  # noqa: PLC0415

        log_path = Path.home() / ".local" / "share" / "myna" / "logs" / "myna.log"
        tray = TrayIcon(
            on_toggle=dict_svc.toggle,
            on_enroll=lambda: EnrollmentWizard(verifier).show(),
            on_settings=lambda: SettingsWindow(dict_svc._settings).show(),
            on_quit=stop_event.set,
            log_path=log_path,
        )
        tray.show()

        def _on_cancel_clicked() -> None:
            asyncio.run_coroutine_threadsafe(dict_svc.cancel(), loop)

        recorder_widget = FloatingRecorderWidget(on_cancel=_on_cancel_clicked)

        def _update_recorder_widget(state: SessionState) -> None:
            if state == SessionState.RECORDING:
                recorder_widget.show_recording()
            elif state == SessionState.TRANSCRIBING or state == SessionState.FINALIZING:
                recorder_widget.show_transcribing()
            else:
                recorder_widget.hide_widget()

        _state_cbs.append(
            lambda s: tray.set_state(TrayState(_SESSION_TO_TRAY.get(s, "idle")))
        )
        _state_cbs.append(_update_recorder_widget)
        _low_conf_cbs.append(lambda: tray.set_state(TrayState.LOW_CONFIDENCE))
        _level_cbs.append(recorder_widget.push_level)
        _LOG.info("System tray active")
    except Exception as exc:
        _LOG.info("Tray unavailable (%s) — headless mode", exc)
        _state_cbs.append(lambda s: _LOG.info("State → %s", s))

    # ------------------------------------------------------------------
    # Hotkey manager
    # ------------------------------------------------------------------
    hotkey = HotkeyManager(dict_svc, loop, settings.activation)
    hotkey.start()

    # ------------------------------------------------------------------
    # Signal handlers + run loop
    # ------------------------------------------------------------------
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    print(
        f"Myna ready — {settings.activation.hotkey} ({settings.activation.mode}).  Ctrl+C to exit.",
        flush=True,
    )

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        hotkey.stop()
        await dict_svc.stop()
        await client.disconnect()
        server.stop()
        dbus_svc.stop()
        _LOG.info("Shutdown complete")


# ---------------------------------------------------------------------------
# Null injection backend
# ---------------------------------------------------------------------------

class _NullBackend(TextOutputBackend):
    async def start_session(self, target_window_id: int | None) -> None: pass
    async def commit_text(self, text: str) -> None:
        print(f"[INJECT] {text!r}", flush=True)
    async def cancel_session(self) -> None: pass
    async def end_session(self) -> None: pass
    def is_secure_field(self, window_id: int | None) -> bool: return False

    @property
    def backend_name(self) -> str:
        return "null"


# ---------------------------------------------------------------------------
# Entry point — Qt + qasync if available, asyncio.run headless fallback
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415
        import qasync  # noqa: PLC0415

        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
        with loop:
            loop.run_until_complete(main_async())
    except ImportError:
        _LOG.info("PyQt6/qasync not available — running headless")
        try:
            asyncio.run(main_async())
        except KeyboardInterrupt:
            pass
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
