"""Budgie entry point.

Adds system tray, low-confidence indicator, secure-field blocking,
and Settings UI.  Uses qasync to run asyncio inside the Qt event loop.

Run:
    python -m budgie_voice.main
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Callable

from budgie_voice.audio.adapter import AudioAdapter
from budgie_voice.audio.spectrum import AudioLevel
from budgie_voice.analytics.recorder import SessionRecorder
from budgie_voice.analytics.store import HistoryStore
from budgie_voice.config.settings import Settings, load_settings
from budgie_voice.dbus.service import BudgieDBusService
from budgie_voice.inference.backends.base import InferenceBackend
from budgie_voice.inference.backends.registry import get_backend, resolve
from budgie_voice.inference.client import InferenceClient
from budgie_voice.inference.server import InferenceServer
from budgie_voice.injection.base import TextOutputBackend
from budgie_voice.injection.selector import select_backend
from budgie_voice.orchestrator.dictation_service import DictationService
from budgie_voice.orchestrator.hotkey import HotkeyManager
from budgie_voice.orchestrator.session import SessionState
from budgie_voice.postprocessing.pipeline import PostProcessingPipeline
from budgie_voice.speaker.verifier import SpeakerVerifier
from budgie_voice.ui.status import RuntimeStatus
from budgie_voice.utils.aio import dispatch
from budgie_voice.utils.exceptions import BudgieInjectionError
from budgie_voice.utils.logging_setup import setup_logging
from budgie_voice.utils.notify import notify

_LOG = logging.getLogger(__name__)

# SessionState → TrayState mapping (imported lazily to allow headless mode)
_SESSION_TO_TRAY: dict[SessionState, str] = {
    SessionState.IDLE:         "idle",
    SessionState.STARTING:     "starting",
    SessionState.RECORDING:    "recording",
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

    _LOG.info("Budgie starting")

    # ------------------------------------------------------------------
    # Inference backend + server
    # ------------------------------------------------------------------
    inf = settings.inference
    backend_name, model, problem = resolve(inf.backend, inf.model)
    if problem:  # never crash-loop under systemd over a typo in config.yaml
        _LOG.error("Inference config invalid: %s", problem)
        await notify(settings, f"⚠️ {problem}")
    active_key = _backend_key(inf, backend_name, model)
    backend = await _load_backend(settings, backend_name, model)

    server = InferenceServer(backend, port=inf.server_port, min_turn_s=inf.min_turn_s)
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

    pipeline = _make_pipeline(settings)
    injector = _make_injector(settings)

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

    # What Settings → Overview reads. Plain Python (no Qt), so headless runs build it too.
    status = RuntimeStatus(verifier)
    status.set_asr(backend, backend_name, model)

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
    injector_holder = {"injector": injector}
    # Filled by the tray block when Qt is up; a headless run has nothing to theme.
    ui_hooks: dict[str, Callable[[str], None]] = {}
    dict_svc = DictationService(
        adapter=adapter, client=client, pipeline=pipeline,
        injector=injector, verifier=verifier, settings=settings,
        on_state_change=_on_state, on_low_confidence=_on_low_conf,
        on_audio_level=_on_level, on_preview=_on_preview,
    )

    # ------------------------------------------------------------------
    # DBus service (with settings reload)
    # ------------------------------------------------------------------
    reload_lock = asyncio.Lock()

    async def _reload() -> None:
        """Re-read config.yaml and apply it to the running app, on the loop.

        Reloads run one at a time: a model load takes a minute, and an
        overlapping reload would compare against the not-yet-swapped model, so
        whichever finished last would win regardless of what config.yaml says.

        Each part is applied independently: a failure in one is reported and
        leaves the rest of the reload (and the previous state of that part) intact.
        """
        async with reload_lock:
            await _reload_locked()

    async def _reload_locked() -> None:
        nonlocal active_key
        old = dict_svc.settings
        new = load_settings()
        problems: list[str] = []

        if (sync_theme_hook := ui_hooks.get("theme")) is not None:
            sync_theme_hook(new.ui.theme)  # no-op when Settings → Save already applied it

        backend_name, model, problem = resolve(new.inference.backend, new.inference.model)
        if problem:
            problems.append(problem)
            _LOG.error("Inference config invalid: %s", problem)
        key = _backend_key(new.inference, backend_name, model)
        if key != active_key:
            _LOG.info("Inference settings changed (%s) — loading new backend", model)
            status.set_asr_loading(True, f"{backend_name} / {model}")
            try:
                new_backend = await _load_backend(new, backend_name, model)
                server.swap_backend(new_backend)
                active_key = key
                status.set_asr(new_backend, backend_name, model)
            except Exception as exc:  # noqa: BLE001 — old backend keeps serving
                _LOG.exception("Backend reload failed")
                problems.append(f"Could not load {model} ({type(exc).__name__}) — keeping the previous model")
            finally:
                status.set_asr_loading(False)

        if (new.injection.backend, new.injection.delay_ms) != (old.injection.backend, old.injection.delay_ms):
            new_injector = _make_injector(new)
        else:
            new_injector = injector_holder["injector"]
        injector_holder["injector"] = new_injector
        dict_svc.apply(new, _make_pipeline(new), new_injector)

        try:
            await loop.run_in_executor(None, adapter.configure, new.audio, new.vad)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("Audio reload failed")
            problems.append(f"Audio settings not applied ({type(exc).__name__})")
        hotkey.apply(new.activation)
        verifier.set_threshold(new.speaker.threshold)
        client.set_language(new.inference.language)

        restart_only = [
            name for name, changed in (
                ("inference.server_port", new.inference.server_port != old.inference.server_port),
                ("logging.level", new.logging.level != old.logging.level),
            ) if changed
        ]
        msg = "Settings reloaded"
        if restart_only:
            msg += f" — restart needed for {', '.join(restart_only)}"
        _LOG.info("%s", msg)
        await dict_svc.notify("⚠️ " + "; ".join(problems) if problems else msg)

    dbus_svc = BudgieDBusService(dict_svc, loop, on_reload=lambda: dispatch(_reload(), loop))
    _state_cbs.append(lambda s: dbus_svc.emit_state_changed(str(s)))
    # Saved history: one metadata row per dictation (when, how long, how many words/characters; never
    # any text). Registered BEFORE the status callback below: the Overview refreshes from that one
    # and must find the row already written. The recorder swallows its own errors.
    history = HistoryStore(Path.home() / ".local" / "share" / "budgie-voice" / "history.db")
    recorder = SessionRecorder(history)
    _state_cbs.append(lambda s: recorder.on_state(str(s), dict_svc.typed_chars, dict_svc.typed_words))
    # Activity history: the state change plus how many characters were typed, never any text.
    # (on_session_state swallows its own errors: nothing here may stop the tray/recorder callbacks.)
    _state_cbs.append(lambda s: status.on_session_state(str(s), dict_svc.typed_chars))
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
        from budgie_voice.indicator.preview_widget import PreviewWidget  # noqa: PLC0415
        from budgie_voice.indicator.recorder_widget import FloatingRecorderWidget  # noqa: PLC0415
        from budgie_voice.indicator.tray import TrayIcon, TrayState  # noqa: PLC0415
        from budgie_voice.settings_ui.window import SettingsWindow  # noqa: PLC0415
        from budgie_voice.speaker.enroller import EnrollmentWizard  # noqa: PLC0415
        from budgie_voice.ui.theme import sync_theme  # noqa: PLC0415
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

        ui_hooks["theme"] = lambda mode: sync_theme(QApplication.instance(), mode)
        ui_hooks["theme"](settings.ui.theme)

        # One window of each kind, kept alive: a parentless Qt widget is destroyed as soon as
        # Python drops its last reference, so `Window(...).show()` alone can vanish at the next GC.
        windows: dict[str, object] = {}

        def _open_once(key: str, make: Callable[[], object]) -> None:
            win = windows.get(key)
            if win is None or not win.isVisible():  # type: ignore[attr-defined]
                win = windows[key] = make()
                win.show()  # type: ignore[attr-defined]
            win.raise_()  # type: ignore[attr-defined]
            win.activateWindow()  # type: ignore[attr-defined]

        log_path = Path.home() / ".local" / "share" / "budgie-voice" / "logs" / "budgie-voice.log"
        tray = TrayIcon(
            on_toggle=lambda: dict_svc.toggle("tray"),
            on_enroll=lambda: _open_once("enroll", lambda: EnrollmentWizard(verifier)),
            on_settings=lambda: _open_once("settings", lambda: SettingsWindow(dict_svc.settings, status=status, history=history)),
            on_quit=stop_event.set,
            on_restart=_request_restart,
            get_last_text=lambda: dict_svc.last_transcript,
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
            elif state == SessionState.FINALIZING:
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
    # Only start an in-process listener if it owns the hotkey: running one
    # alongside a GNOME shortcut that calls DBus Toggle double-fires every press.
    trigger = settings.activation.resolved_trigger()
    _LOG.info(
        "Hotkey owned by %r (activation.trigger=%r, session=%s)",
        trigger, settings.activation.trigger, os.environ.get("XDG_SESSION_TYPE", "unknown"),
    )
    hotkey = HotkeyManager(dict_svc, loop, settings.activation)
    if trigger in ("pynput", "evdev"):
        hotkey.start()
    else:
        _LOG.info(
            "No in-process listener; bind %s to a desktop shortcut running: dbus-send --session "
            "--type=method_call --dest=io.gitlab.karanaithal841.BudgieVoice "
            "/io/gitlab/karanaithal841/BudgieVoice io.gitlab.karanaithal841.BudgieVoice.Toggle",
            settings.activation.hotkey,
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
        f"Budgie ready — {settings.activation.active_hotkey()} ({settings.activation.mode}).  Ctrl+C to exit.",
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


def _backend_key(inf: object, backend: str, model: str) -> tuple:
    """Everything that requires loading a new backend instance when it changes."""
    return (
        backend, model, inf.device, inf.compute_type, inf.beam_size,  # type: ignore[attr-defined]
        inf.no_speech_threshold, inf.log_prob_threshold, inf.compression_ratio_threshold,  # type: ignore[attr-defined]
    )


async def _load_backend(settings: Settings, name: str, model: str) -> InferenceBackend:
    """Load *model* on backend *name*, telling the user first if that means a download."""
    inf = settings.inference
    backend = get_backend(name)
    if not backend.is_cached(model, inf.compute_type):
        await notify(settings, f"Downloading {model} (first run only) — this can take a minute")
    await backend.load(
        model, inf.device, inf.compute_type,
        beam_size=inf.beam_size,
        no_speech_threshold=inf.no_speech_threshold,
        log_prob_threshold=inf.log_prob_threshold,
        compression_ratio_threshold=inf.compression_ratio_threshold,
    )
    return backend


def _make_pipeline(settings: Settings) -> PostProcessingPipeline:
    pp = settings.postprocessing
    return PostProcessingPipeline(
        normalize_numbers=pp.normalize_numbers,
        auto_capitalize=pp.auto_capitalize,
        punctuation_commands=pp.punctuation_commands,
        language_cleanup=pp.language_cleanup,
        strip_fillers=pp.strip_fillers,
        spoken_edits=pp.spoken_edits,
        strip_trailing_period=pp.strip_trailing_period,
        snippets=pp.snippets or None,  # a bare `snippets:` in YAML is None
    )


def _make_injector(settings: Settings) -> TextOutputBackend:
    try:
        injector = select_backend(settings.injection.backend, delay_ms=settings.injection.delay_ms)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Injection backend unavailable (%s) — null fallback", exc)
        return _NullBackend()
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and settings.injection.block_secure_fields:
        _LOG.warning("Secure-field detection is unavailable on Wayland — dictation is not blocked in password fields")
    return injector


# ---------------------------------------------------------------------------
# Null injection backend
# ---------------------------------------------------------------------------

class _NullBackend(TextOutputBackend):
    """Stand-in when no real injection backend could be created.

    Typing fails loudly instead of pretending to work: DictationService turns the error
    into "Typing failed — text kept: tray → Copy Last Transcript", so the dictation is
    recoverable. It must never print or log the text: stdout reaches the journal.
    """

    async def start_session(self, target_window_id: int | None) -> None: pass
    async def commit_text(self, text: str) -> None:
        raise BudgieInjectionError("no injection backend available — see the startup warning in the log")
    async def end_session(self) -> None: pass
    def is_secure_field(self, window_id: int | None) -> bool: return False

    @property
    def backend_name(self) -> str:
        return "null"


# ---------------------------------------------------------------------------
# Entry point — Qt + qasync if available, asyncio.run headless fallback
# ---------------------------------------------------------------------------

def _relaunch() -> None:
    """Replace this process with a fresh Budgie.

    ``execv`` keeps the PID, so a systemd user unit sees one continuous process
    rather than an exit it would have to restart itself. Runs only after
    main_async's shutdown finished, so nothing is still holding the BT profile,
    the DBus name or the inference port.
    """
    _LOG.info("Relaunching: %s -m budgie_voice.main", sys.executable)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, "-m", "budgie_voice.main"])


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
