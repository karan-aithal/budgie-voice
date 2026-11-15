"""Myna Phase 4 entry point.

Adds system tray, low-confidence indicator, secure-field blocking,
and Settings UI.  Uses qasync to run asyncio inside the Qt event loop.

Run:
    python -m myna.main
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Callable

from myna.audio.adapter import AudioAdapter
from myna.audio.spectrum import AudioLevel
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
from myna.utils.aio import dispatch
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


async def main_async() -> bool:
    """Full async startup — works headless or with Qt tray.

    Returns:
        ``True`` if the tray's Restart action asked for a relaunch. Shutdown has
        already completed by then, so the caller can safely re-exec.
    """
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

    server = InferenceServer(backend, port=settings.inference.server_port, min_turn_s=settings.inference.min_turn_s)
    server.start()
    await asyncio.sleep(0.3)

    client = InferenceClient(port=settings.inference.server_port)
    await client.connect(language=settings.inference.language)

    # ------------------------------------------------------------------
    # Audio + pipeline + injection
    # ------------------------------------------------------------------
    adapter = AudioAdapter(settings.audio, settings.vad, loop)
    try:  # first load may download the model; do it now, not on the first hotkey press
        await loop.run_in_executor(None, adapter.preload)
    except Exception as exc:  # noqa: BLE001 — open() retries the load per session
        _LOG.warning("VAD preload failed (%s) — will retry on first session", exc)

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
    _level_cbs: list[Callable[[AudioLevel], None]] = []
    _preview_widget_holder: dict[str, object] = {}

    def _on_state(state: SessionState) -> None:
        for cb in _state_cbs:
            cb(state)

    def _on_low_conf() -> None:
        for cb in _low_conf_cbs:
            cb()

    def _on_level(level: AudioLevel) -> None:
        for cb in _level_cbs:
            cb(level)

    async def _on_preview(text: str) -> str | None:
        widget = _preview_widget_holder.get("widget")
        if widget is None:
            return text
        future = loop.create_future()
        widget.show_preview(text, lambda r: future.set_result(r))  # type: ignore[attr-defined]
        return await future

    # ------------------------------------------------------------------
    # DictationService
    # ------------------------------------------------------------------
    dict_svc = DictationService(
        adapter=adapter, client=client, pipeline=pipeline,
        injector=injector, verifier=verifier, settings=settings,
        on_state_change=_on_state, on_low_confidence=_on_low_conf,
        on_audio_level=_on_level, on_preview=_on_preview,
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
            dispatch(
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
    # Set by the tray's Restart action. Restart deliberately reuses the normal
    # shutdown path rather than exec'ing straight away, so the BT profile is
    # restored, the DBus name released and the inference server stopped before
    # the new process starts contending for them.
    restart_requested = False

    def _request_restart() -> None:
        nonlocal restart_requested
        restart_requested = True
        _LOG.info("Restart requested from tray — shutting down to relaunch")
        stop_event.set()

    try:
        from myna.indicator.preview_widget import PreviewWidget  # noqa: PLC0415
        from myna.indicator.recorder_widget import FloatingRecorderWidget  # noqa: PLC0415
        from myna.indicator.tray import TrayIcon, TrayState  # noqa: PLC0415
        from myna.settings_ui.window import SettingsWindow  # noqa: PLC0415
        from myna.speaker.enroller import EnrollmentWizard  # noqa: PLC0415

        log_path = Path.home() / ".local" / "share" / "myna" / "logs" / "myna.log"
        tray = TrayIcon(
            on_toggle=lambda: dict_svc.toggle("tray"),
            on_enroll=lambda: EnrollmentWizard(verifier).show(),
            on_settings=lambda: SettingsWindow(dict_svc._settings).show(),
            on_quit=stop_event.set,
            on_restart=_request_restart,
            log_path=log_path,
        )
        tray.show()

        def _on_cancel_clicked() -> None:
            dispatch(dict_svc.cancel(), loop)

        recorder_widget = FloatingRecorderWidget(
            on_cancel=_on_cancel_clicked, style=settings.indicator.visualizer
        )
        _preview_widget_holder["widget"] = PreviewWidget()

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
    # Only start the in-process listener if it owns the hotkey. On Wayland it
    # can't see global keys anyway, and running it alongside a GNOME shortcut
    # that calls DBus Toggle double-fires every press.
    trigger = settings.activation.resolved_trigger()
    hotkey = HotkeyManager(dict_svc, loop, settings.activation)
    if trigger == "pynput":
        hotkey.start()
    else:
        _LOG.info(
            "Hotkey owned by %r (activation.trigger=%r, session=%s) — pynput listener not started; "
            "bind %s to a desktop shortcut running: dbus-send --session --type=method_call "
            "--dest=com.canonical.Myna /com/canonical/Myna com.canonical.Myna.Toggle",
            trigger, settings.activation.trigger,
            os.environ.get("XDG_SESSION_TYPE", "unknown"), settings.activation.hotkey,
        )

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
        adapter.shutdown()
        await client.disconnect()
        server.stop()
        dbus_svc.stop()
        _LOG.info("Shutdown complete")

    return restart_requested


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

def _relaunch() -> None:
    """Replace this process with a fresh Myna.

    ``execv`` keeps the PID, so a systemd user unit sees one continuous process
    rather than an exit it would have to restart itself. Runs only after
    main_async's shutdown finished, so nothing is still holding the BT profile,
    the DBus name or the inference port.
    """
    _LOG.info("Relaunching: %s -m myna.main", sys.executable)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, "-m", "myna.main"])


def main() -> None:
    restart = False
    try:
        # Native Wayland toplevels can't be positioned or forced always-on-top
        # (no such client capability in the protocol) — route Qt through
        # XWayland instead, where the recorder widget's move()/StaysOnTopHint
        # are backed by real EWMH requests the WM honors.
        if os.environ.get("XDG_SESSION_TYPE") == "wayland" and "QT_QPA_PLATFORM" not in os.environ:
            os.environ["QT_QPA_PLATFORM"] = "xcb"

        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415
        import qasync  # noqa: PLC0415

        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
        with loop:
            restart = bool(loop.run_until_complete(main_async()))
    except ImportError:
        _LOG.info("PyQt6/qasync not available — running headless")
        try:
            restart = bool(asyncio.run(main_async()))
        except KeyboardInterrupt:
            pass
    except KeyboardInterrupt:
        pass

    if restart:
        _relaunch()  # never returns


if __name__ == "__main__":
    main()
