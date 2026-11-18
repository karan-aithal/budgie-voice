"""ASR model swap, end to end through the real ``main_async``.

``_reload_locked`` is a closure inside main_async, so the only honest check is to run main_async
itself with fakes at its edges (inference server/client, audio, DBus, hotkey, model loading) and
trigger the reload the way the DBus ``Reload()`` does. Asserts what Settings → Overview would show
before, during and after a swap, and that a failed or no-op reload leaves the old model serving.
"""
from __future__ import annotations

import asyncio
import copy
import gc
import os
import signal
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import sounddevice as sd

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from budgie_voice import main  # noqa: E402
from budgie_voice.analytics.store import HistoryStore  # noqa: E402
from budgie_voice.indicator import tray as tray_mod  # noqa: E402
from budgie_voice.indicator.tray import TrayIcon  # noqa: E402
from budgie_voice.config.settings import Settings  # noqa: E402
from budgie_voice.orchestrator import dictation_service  # noqa: E402
from budgie_voice.settings_ui.overview import OverviewPage  # noqa: E402
from budgie_voice.settings_ui.window import SettingsWindow  # noqa: E402
from budgie_voice.speaker.enroller import EnrollmentWizard  # noqa: E402
from budgie_voice.ui.status import RuntimeStatus  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class _Backend:
    def __init__(self, model: str) -> None:
        self.model, self.is_ready = model, True


class _Server:
    made: list["_Server"] = []

    def __init__(self, backend, port=0, min_turn_s=0.0) -> None:
        self.backend, self.swaps, self.stopped = backend, [], False
        _Server.made.append(self)

    def start(self) -> None: ...
    def stop(self) -> None: self.stopped = True
    def swap_backend(self, backend) -> None: self.swaps.append(backend)


class _Client:
    def __init__(self, *a, **k) -> None: ...
    async def connect(self, language="en") -> None: ...
    async def disconnect(self) -> None: ...
    def set_language(self, language) -> None: ...
    def set_hotwords(self, hotwords) -> None: ...

    async def receive_segments(self):
        await asyncio.Event().wait()  # no transcripts in this test
        yield  # pragma: no cover — makes this an async generator


class _DBus:
    made: list["_DBus"] = []

    def __init__(self, service, loop, on_reload=None) -> None:
        self.on_reload = on_reload
        _DBus.made.append(self)

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def emit_state_changed(self, state) -> None: ...


class _Hotkey:
    made: list["_Hotkey"] = []

    def __init__(self, *a, **k) -> None:
        _Hotkey.made.append(self)

    def apply(self, activation) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...


class _Status(RuntimeStatus):
    """The real RuntimeStatus, plus a record of what a subscribed Overview would be told."""

    made: list["_Status"] = []

    def __init__(self, verifier=None) -> None:
        super().__init__(verifier)
        self.seen: list[tuple[str, bool, bool]] = []
        self.subscribe(self._record)
        _Status.made.append(self)

    def _record(self) -> None:
        info = self.asr_info()
        self.seen.append((info.model, info.ready, info.loading))


def _settings(model: str) -> Settings:
    s = Settings()
    s.inference.backend, s.inference.model = "faster_whisper", model
    s.activation.trigger = "dbus"  # no in-process key listener
    s.speaker.enabled = False
    s.learning.vocab_enabled = s.learning.corrections_enabled = False
    s.injection.block_secure_fields = False
    return s


async def _until(pred, what: str, timeout: float = 3.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if pred():
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"timed out waiting for: {what}")


def _patch_main(monkeypatch, current: dict, fake_load, notices) -> None:
    """Fakes at main_async's edges; everything between them is the real code."""
    for fake in (_Server, _DBus, _Hotkey, _Status):
        fake.made.clear()
    monkeypatch.setattr(main, "load_settings", lambda: copy.deepcopy(current["s"]))
    monkeypatch.setattr(main, "setup_logging", lambda level: None)
    monkeypatch.setattr(main, "notify", AsyncMock())
    monkeypatch.setattr(main, "_load_backend", fake_load)
    monkeypatch.setattr(main, "InferenceServer", _Server)
    monkeypatch.setattr(main, "InferenceClient", _Client)
    monkeypatch.setattr(main, "AudioAdapter", lambda *a, **k: MagicMock())
    monkeypatch.setattr(main, "BudgieDBusService", _DBus)
    monkeypatch.setattr(main, "HotkeyManager", _Hotkey)
    monkeypatch.setattr(main, "_make_injector", lambda s: main._NullBackend())
    monkeypatch.setattr(main, "RuntimeStatus", _Status)
    # The history file is an edge too: a test run must never create one in the real home directory.
    monkeypatch.setattr(main, "HistoryStore", lambda path: HistoryStore(Path(tempfile.mkdtemp()) / "history.db"))
    monkeypatch.setattr(dictation_service, "_notify", notices)


def _row(settings: Settings, status: RuntimeStatus) -> str:
    page = OverviewPage(settings, status, lambda d: None)
    page.refresh()
    return page._asr._value.text()


async def test_swap_success_failure_and_noop_through_main_async(monkeypatch, qapp) -> None:
    current = {"s": _settings("small")}
    loads: list[str] = []
    entered: dict[str, asyncio.Event] = {"medium": asyncio.Event()}
    release = asyncio.Event()
    notices = AsyncMock()

    async def fake_load(settings, name, model):
        loads.append(model)
        if model in entered:
            entered[model].set()
            await release.wait()  # a slow model load: the loop must stay free meanwhile
        if model == "large-v3":
            raise RuntimeError("download failed")
        return _Backend(model)

    _patch_main(monkeypatch, current, fake_load, notices)

    app = asyncio.create_task(main.main_async())
    try:
        await _until(lambda: _Hotkey.made and _DBus.made, "app startup")
        await asyncio.sleep(0.05)  # shutdown signal handlers are registered right after
        status, server, dbus = _Status.made[0], _Server.made[0], _DBus.made[0]

        # -- before: ready on the configured model ---------------------------------------
        assert _row(current["s"], status) == "Ready — faster_whisper / small"

        # -- during: a swap to a slow-loading model --------------------------------------
        current["s"] = _settings("medium")
        dbus.on_reload()  # what DBus Reload() does
        await _until(entered["medium"].is_set, "medium load to start")
        info = status.asr_info()
        assert (info.loading, info.target, info.model, info.ready) == (
            True, "faster_whisper / medium", "small", True)  # old model keeps serving
        assert server.swaps == []
        # A Settings window opened mid-swap still has the old settings: the row must name the target.
        assert _row(_settings("small"), status) == (
            "Connecting — loading faster_whisper / medium… (still serving faster_whisper / small)")

        # -- after: swapped, loading cleared ---------------------------------------------
        release.set()
        await _until(lambda: not status.asr_info().loading and status.asr_info().model == "medium", "swap done")
        assert len(server.swaps) == 1 and server.swaps[0].model == "medium"
        assert _row(current["s"], status) == "Ready — faster_whisper / medium"
        assert status.seen[-1] == ("medium", True, False)
        assert (("small", True, True)) in status.seen  # subscribers were told it was loading
        assert any("Settings reloaded" in c.args[1] for c in notices.call_args_list)

        # -- failure: the old model keeps serving, and the Overview says the config is not applied
        current["s"] = _settings("large-v3")
        dbus.on_reload()
        await _until(lambda: "large-v3" in loads and not status.asr_info().loading, "failed load to finish")
        await asyncio.sleep(0.05)
        assert len(server.swaps) == 1  # nothing swapped
        assert status.asr_info().model == "medium" and status.asr_info().ready
        assert _row(current["s"], status) == (
            "Ready — faster_whisper / medium (configured faster_whisper / large-v3, not applied)")
        assert any("Could not load large-v3" in c.args[1] and "keeping the previous model" in c.args[1]
                   for c in notices.call_args_list)

        # -- a config that still names the failed model is retried on the next reload ------------
        loads_before = loads.count("large-v3")
        dbus.on_reload()
        await _until(lambda: loads.count("large-v3") == loads_before + 1 and not status.asr_info().loading, "retry")
        assert len(server.swaps) == 1

        # -- no-op: config back on the model that is serving reloads nothing, never flashes "loading"
        current["s"] = _settings("medium")
        await asyncio.sleep(0.05)
        seen_before, loads_before = len(status.seen), list(loads)
        dbus.on_reload()
        await asyncio.sleep(0.2)
        assert loads == loads_before and len(status.seen) == seen_before
        assert _row(current["s"], status) == "Ready — faster_whisper / medium"
    finally:
        os.kill(os.getpid(), signal.SIGINT)  # main_async's own shutdown handler
        restart = await asyncio.wait_for(app, 5)

    assert restart is False and server.stopped


class _TrayProbe(TrayIcon):
    made: list["_TrayProbe"] = []

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        _TrayProbe.made.append(self)


async def test_tray_opens_one_retained_window_per_kind_and_reopens(monkeypatch, qapp) -> None:
    """The real _open_once closure, driven through the real tray actions.

    A parentless Qt widget is destroyed once Python drops its last reference, so each window must
    survive a garbage collection, never duplicate while open, and reopen after being closed.
    """
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(sd, "query_devices", lambda: [])  # no pactl / PortAudio in the device lists
    _TrayProbe.made.clear()
    monkeypatch.setattr(tray_mod, "TrayIcon", _TrayProbe)

    async def fake_load(settings, name, model):
        return _Backend(model)

    _patch_main(monkeypatch, {"s": _settings("small")}, fake_load, AsyncMock())
    app = asyncio.create_task(main.main_async())
    try:
        await _until(lambda: _Hotkey.made and _DBus.made and _TrayProbe.made, "app startup")
        await asyncio.sleep(0.05)
        tray = _TrayProbe.made[0]

        def action(word: str):
            return next(a for a in tray.contextMenu().actions() if word in a.text())

        def shown(cls) -> list:
            return [w for w in QApplication.topLevelWidgets() if isinstance(w, cls) and w.isVisible()]

        for word, cls in (("Settings", SettingsWindow), ("Enroll", EnrollmentWizard)):
            action(word).trigger()
            gc.collect()  # nothing but main's own reference may keep it alive
            await asyncio.sleep(0.05)
            (first,) = shown(cls)

            action(word).trigger()  # already open: raised, not duplicated
            await asyncio.sleep(0.05)
            assert shown(cls) == [first]

            first.close()
            await asyncio.sleep(0.05)
            assert shown(cls) == []

            action(word).trigger()  # closed: a fresh one opens
            await asyncio.sleep(0.05)
            (again,) = shown(cls)
            again.close()
    finally:
        os.kill(os.getpid(), signal.SIGINT)
        await asyncio.wait_for(app, 5)


async def _start_app(monkeypatch, current: dict):
    _TrayProbe.made.clear()
    monkeypatch.setattr(tray_mod, "TrayIcon", _TrayProbe)

    async def fake_load(settings, name, model):
        return _Backend(model)

    _patch_main(monkeypatch, current, fake_load, AsyncMock())
    app = asyncio.create_task(main.main_async())
    await _until(lambda: _Hotkey.made and _DBus.made and _TrayProbe.made, "app startup")
    await asyncio.sleep(0.05)
    return app


async def test_reload_applies_a_changed_theme_once(monkeypatch, qapp) -> None:
    """Hand-edited ui.theme + DBus Reload takes effect; an unchanged or typo'd mode never repeats."""
    from budgie_voice.ui import theme as theme_mod

    monkeypatch.setattr(theme_mod, "_applied", None)
    applied: list[str] = []
    real = theme_mod.apply_theme
    monkeypatch.setattr(theme_mod, "apply_theme", lambda a, mode: (applied.append(mode), real(a, mode))[1])
    current = {"s": _settings("small")}
    current["s"].ui.theme = "dark"
    app = await _start_app(monkeypatch, current)
    try:
        assert applied == ["dark"]  # startup
        dbus = _DBus.made[0]

        dbus.on_reload()  # nothing changed
        await asyncio.sleep(0.2)
        assert applied == ["dark"]

        current["s"].ui.theme = "light"
        dbus.on_reload()
        await _until(lambda: applied == ["dark", "light"], "theme applied on reload")

        current["s"].ui.theme = "sepia"  # a typo falls back to system, once
        for _ in range(2):
            dbus.on_reload()
            await asyncio.sleep(0.2)
        assert applied == ["dark", "light", "sepia"]
    finally:
        os.kill(os.getpid(), signal.SIGINT)
        await asyncio.wait_for(app, 5)
        real(qapp, "system")


async def test_a_theme_failure_does_not_cost_the_tray(monkeypatch, qapp) -> None:
    """Regression: apply_theme raising used to drop the whole tray block into headless mode."""
    from budgie_voice.ui import theme as theme_mod

    monkeypatch.setattr(theme_mod, "_SCHEME", {"system": None, "light": None, "dark": None})  # Qt rejects these
    app = await _start_app(monkeypatch, {"s": _settings("small")})
    try:
        assert _TrayProbe.made  # the tray, recorder and preview wiring all survived
        assert not app.done()
    finally:
        os.kill(os.getpid(), signal.SIGINT)
        await asyncio.wait_for(app, 5)
