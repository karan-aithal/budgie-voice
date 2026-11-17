"""Phase 6: the evdev hotkey, driven by fake devices (nothing here touches /dev/input)."""
from __future__ import annotations

import errno
import logging
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from evdev import ecodes
from pynput.keyboard import Key

from budgie_voice.config.settings import ActivationSettings
from budgie_voice.orchestrator import evdev_hotkey as eh
from budgie_voice.orchestrator.evdev_hotkey import EvdevHotkey
from budgie_voice.orchestrator.hotkey import HotkeyManager, _parse_hotkey

PAUSE, SPACE = ecodes.KEY_PAUSE, ecodes.KEY_SPACE
LCTRL, RCTRL = ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL
UP, DOWN, REPEAT = 0, 1, 2
TOGGLE = ("toggle", "evdev")


class FakeDev:
    """Just enough of evdev.InputDevice: a real fd (select needs one), name, read(), close()."""

    def __init__(self, name: str = "Fake Keyboard", keyboard: bool = True) -> None:
        self.name = name
        self._keyboard = keyboard
        self.fd, self._w = os.pipe()
        self._queue: list[tuple[int, int]] = []
        self._dead = False
        self.closed = False

    def capabilities(self) -> dict[int, list[int]]:
        return {ecodes.EV_KEY: [ecodes.KEY_A, PAUSE]} if self._keyboard else {ecodes.EV_REL: [0]}

    def push(self, code: int, value: int) -> None:
        self._queue.append((code, value))
        os.write(self._w, b"x")

    def unplug(self) -> None:
        self._dead = True
        os.write(self._w, b"x")

    def read(self):  # noqa: ANN201 — generator, like InputDevice.read
        os.read(self.fd, 4096)
        if self._dead:
            raise OSError(errno.ENODEV, "No such device")
        queued, self._queue = self._queue, []
        for code, value in queued:
            yield type("Ev", (), {"type": ecodes.EV_KEY, "code": code, "value": value})()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            os.close(self.fd)
            os.close(self._w)


class World:
    """The fake /dev/input plus everything the listener dispatched."""

    def __init__(self) -> None:
        self.devs: dict[str, FakeDev] = {}
        self.denied: set[str] = set()
        self.calls: list[object] = []

    def add(self, path: str, dev: FakeDev) -> FakeDev:
        self.devs[path] = dev
        return dev


class Svc:
    """Stands in for DictationService: each call returns a tag the fake dispatch records."""

    def toggle(self, source: str) -> tuple[str, str]:
        return ("toggle", source)

    def push_start(self) -> str:
        return "start"

    def push_stop(self) -> str:
        return "stop"

    def notify(self, message: str) -> tuple[str, str]:
        return ("notify", message)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch):
    w = World()

    def _open(path: str) -> FakeDev:
        if path in w.denied:
            raise PermissionError(errno.EACCES, "Permission denied")
        return w.devs[path]

    monkeypatch.setattr(eh, "_paths", lambda: sorted({*w.devs, *w.denied}))
    monkeypatch.setattr(eh, "_open", _open)
    monkeypatch.setattr(eh, "_RESCAN_S", 0.0)  # rescan on every poll
    monkeypatch.setattr(eh, "_POLL_S", 0.02)
    monkeypatch.setattr(eh, "dispatch", lambda coro, loop: w.calls.append(coro))
    eh.auto_backend.cache_clear()
    yield w
    eh.auto_backend.cache_clear()
    for dev in w.devs.values():
        dev.close()


def listener(hotkey: str = "pause", mode: str = "toggle") -> EvdevHotkey:
    """A listener with its devices adopted, but no thread: tests drive poll() themselves."""
    lst = EvdevHotkey(Svc(), None, hotkey, mode)  # type: ignore[arg-type]
    lst._sync(initial=True)  # noqa: SLF001
    return lst


# -- dispatch behaviour -------------------------------------------------------

def test_hold_mode_press_then_release_is_start_then_stop(world: World) -> None:
    kbd = world.add("/dev/input/event1", FakeDev())
    lst = listener("pause", "push_to_hold")

    kbd.push(PAUSE, DOWN)
    lst.poll(0.1)
    assert world.calls == ["start"]

    kbd.push(PAUSE, REPEAT)
    kbd.push(PAUSE, REPEAT)
    lst.poll(0.1)
    assert world.calls == ["start"]  # held down: nothing more

    kbd.push(PAUSE, UP)
    lst.poll(0.1)
    assert world.calls == ["start", "stop"]


def test_default_hotkey_works_as_hold_to_talk(world: World) -> None:
    kbd = world.add("/dev/input/event1", FakeDev())
    lst = listener(ActivationSettings().evdev_hotkey, "push_to_hold")

    kbd.push(ecodes.KEY_F9, DOWN)
    lst.poll(0.1)
    kbd.push(ecodes.KEY_F9, UP)
    lst.poll(0.1)

    assert world.calls == ["start", "stop"]


def test_toggle_mode_fires_once_per_press_despite_autorepeat(world: World) -> None:
    kbd = world.add("/dev/input/event1", FakeDev())
    lst = listener("pause", "toggle")

    for value in (DOWN, REPEAT, REPEAT, REPEAT, REPEAT, UP, DOWN, REPEAT, UP):
        kbd.push(PAUSE, value)
    lst.poll(0.1)

    assert world.calls == [TOGGLE, TOGGLE]


@pytest.mark.parametrize("ctrl", [LCTRL, RCTRL])
def test_generic_modifier_matches_left_and_right(world: World, ctrl: int) -> None:
    kbd = world.add("/dev/input/event1", FakeDev())
    lst = listener("ctrl+space")

    kbd.push(ctrl, DOWN)
    kbd.push(SPACE, DOWN)
    lst.poll(0.1)

    assert world.calls == [TOGGLE]


def test_side_specific_modifier_ignores_the_other_side(world: World) -> None:
    kbd = world.add("/dev/input/event1", FakeDev())
    lst = listener("right_ctrl")

    kbd.push(LCTRL, DOWN)
    kbd.push(LCTRL, UP)
    lst.poll(0.1)
    assert world.calls == []

    kbd.push(RCTRL, DOWN)
    lst.poll(0.1)
    assert world.calls == [TOGGLE]


def test_partial_combo_does_not_fire(world: World) -> None:
    kbd = world.add("/dev/input/event1", FakeDev())
    lst = listener("ctrl+space")

    kbd.push(SPACE, DOWN)
    lst.poll(0.1)

    assert world.calls == []


def test_only_hotkey_codes_are_ever_stored(world: World) -> None:
    kbd = world.add("/dev/input/event1", FakeDev())
    lst = listener("pause")

    for code in (ecodes.KEY_A, ecodes.KEY_B, ecodes.KEY_ENTER, PAUSE):
        kbd.push(code, DOWN)
    lst.poll(0.1)

    assert lst._pressed == {"/dev/input/event1": {PAUSE}}  # noqa: SLF001


# -- devices coming and going -------------------------------------------------

def test_unplugged_device_does_not_stop_the_listener(world: World) -> None:
    one = world.add("/dev/input/event1", FakeDev("one"))
    two = world.add("/dev/input/event2", FakeDev("two"))
    lst = listener("pause", "push_to_hold")

    one.push(PAUSE, DOWN)
    lst.poll(0.1)
    assert world.calls == ["start"]

    one.unplug()  # read() now raises ENODEV; the key it held counts as released
    del world.devs["/dev/input/event1"]
    lst.poll(0.1)
    assert world.calls == ["start", "stop"]

    two.push(PAUSE, DOWN)  # the other keyboard still works
    lst.poll(0.1)
    assert world.calls == ["start", "stop", "start"]


def test_device_vanishing_from_the_listing_is_dropped(world: World) -> None:
    kbd = world.add("/dev/input/event1", FakeDev())
    lst = listener("pause", "push_to_hold")
    kbd.push(PAUSE, DOWN)
    lst.poll(0.1)

    del world.devs["/dev/input/event1"]  # gone from /dev/input without a read error
    lst.poll(0.1)

    assert world.calls == ["start", "stop"]
    assert kbd.closed


def test_keyboard_plugged_in_while_running_is_picked_up(world: World) -> None:
    mouse = world.add("/dev/input/event0", FakeDev("mouse", keyboard=False))
    lst = listener("pause")
    assert lst._devs == {}  # noqa: SLF001 — nothing usable yet
    assert mouse.closed  # a non-keyboard is closed straight away, not kept open

    kbd = world.add("/dev/input/event1", FakeDev("late keyboard"))
    lst.poll(0)
    kbd.push(PAUSE, DOWN)
    lst.poll(0.1)

    assert [c[0] for c in world.calls] == ["notify", "toggle"]  # "no keyboard" at start, then it works
    assert world.calls[-1] == TOGGLE


def test_real_thread_dispatches_and_releases_devices_on_stop(world: World) -> None:
    kbd = world.add("/dev/input/event1", FakeDev())
    lst = EvdevHotkey(Svc(), None, "pause", "toggle")  # type: ignore[arg-type]
    lst.start()
    try:
        kbd.push(PAUSE, DOWN)
        deadline = time.monotonic() + 2
        while not world.calls and time.monotonic() < deadline:
            time.sleep(0.02)
        assert world.calls == [TOGGLE]
    finally:
        lst.stop()
    deadline = time.monotonic() + 2
    while not kbd.closed and time.monotonic() < deadline:
        time.sleep(0.02)
    assert kbd.closed


# -- what we say about what we can see ----------------------------------------

def test_every_opened_device_is_logged_by_name(world: World, caplog: pytest.LogCaptureFixture) -> None:
    world.add("/dev/input/event3", FakeDev("My Keyboard"))
    world.add("/dev/input/event4", FakeDev("Other Board"))
    caplog.set_level(logging.INFO, logger=eh.__name__)

    listener()

    assert "opened 'My Keyboard' (/dev/input/event3)" in caplog.text
    assert "opened 'Other Board' (/dev/input/event4)" in caplog.text


def test_no_readable_keyboard_logs_error_and_notifies_the_fix(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.denied = {"/dev/input/event0", "/dev/input/event1"}
    caplog.set_level(logging.ERROR, logger=eh.__name__)

    listener()

    assert "sudo usermod -aG input $USER" in caplog.text
    (kind, message), = world.calls  # type: ignore[misc]
    assert kind == "notify" and "input group" in message and "usermod" in message


def test_only_a_tablet_readable_is_reported_as_partial_not_as_support(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.add("/dev/input/event6", FakeDev("XPPen Deco Mini7 V2 Keyboard"))
    world.denied = {"/dev/input/event0", "/dev/input/event1"}
    caplog.set_level(logging.WARNING, logger=eh.__name__)

    listener()

    assert "XPPen Deco Mini7 V2 Keyboard" in caplog.text
    assert "2 other input device(s)" in caplog.text
    assert "usermod -aG input" in caplog.text
    (kind, message), = world.calls  # type: ignore[misc]
    assert kind == "notify" and "If your keyboard is not listed" in message


def test_full_access_is_quiet(world: World, caplog: pytest.LogCaptureFixture) -> None:
    world.add("/dev/input/event1", FakeDev())
    caplog.set_level(logging.WARNING, logger=eh.__name__)

    listener()

    assert caplog.text == "" and world.calls == []


def test_device_listing_includes_unreadable_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    """evdev.list_devices() hides permission-denied nodes, so the 'N unreadable' warning
    could never fire. The real _paths() must list them."""
    monkeypatch.setattr(eh.evdev, "list_devices", lambda: pytest.fail("hides unreadable nodes"))
    monkeypatch.setattr(eh.glob, "glob", lambda pattern: ["/dev/input/event1", "/dev/input/event0"])

    assert eh._paths() == ["/dev/input/event0", "/dev/input/event1"]  # noqa: SLF001


# -- backend selection ---------------------------------------------------------

def test_auto_on_x11_stays_pynput_without_touching_input_devices(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(eh, "_paths", lambda: pytest.fail("scanned /dev/input on X11"))

    assert ActivationSettings().resolved_trigger() == "pynput"


def test_auto_on_wayland_picks_evdev_when_a_keyboard_is_readable(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    world.add("/dev/input/event1", FakeDev("Real Keyboard"))
    caplog.set_level(logging.INFO, logger=eh.__name__)

    assert ActivationSettings().resolved_trigger() == "evdev"

    assert "auto → evdev" in caplog.text and "Real Keyboard" in caplog.text


@pytest.mark.parametrize("setup", ["denied_only", "no_keyboards", "empty"])
def test_auto_on_wayland_falls_back_to_dbus(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, setup: str
) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    if setup == "denied_only":
        world.denied = {"/dev/input/event0"}
    elif setup == "no_keyboards":
        world.add("/dev/input/event0", FakeDev("mouse", keyboard=False))
    caplog.set_level(logging.INFO, logger=eh.__name__)

    assert ActivationSettings().resolved_trigger() == "dbus"

    assert "auto → dbus" in caplog.text and "no readable keyboard" in caplog.text


def test_auto_choice_is_made_once_so_the_owner_cannot_flip(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    kbd = world.add("/dev/input/event1", FakeDev())
    a = ActivationSettings()
    assert a.resolved_trigger() == "evdev"

    del world.devs["/dev/input/event1"]  # unplugged later; toggle() asks again on every press
    assert a.resolved_trigger() == "evdev"
    assert kbd.closed  # the probe closed what it opened


def test_explicit_evdev_needs_no_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(eh, "_paths", lambda: pytest.fail("scanned"))
    assert ActivationSettings(trigger="evdev").resolved_trigger() == "evdev"


# -- hotkey resolution ---------------------------------------------------------

def test_defaults_keep_ctrl_space_and_use_f9_only_for_evdev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    a = ActivationSettings()
    assert (a.hotkey, a.evdev_hotkey) == ("ctrl+space", "f9")
    assert a.active_hotkey() == "ctrl+space"
    a.trigger = "dbus"
    assert a.active_hotkey() == "ctrl+space"
    a.trigger = "evdev"
    assert a.active_hotkey() == "f9"


@pytest.mark.parametrize("name", ["pause", "scroll_lock", "right_ctrl", "f5", "f9", "f12", "ctrl+f9"])
def test_extended_key_names_parse_for_both_listeners(name: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    tokens = name.count("+") + 1

    assert len(eh._parse(name)) == tokens  # noqa: SLF001
    assert len(_parse_hotkey(name)) == tokens
    assert "Unknown hotkey token" not in caplog.text


def test_unknown_evdev_key_is_an_error_not_a_shorter_hotkey() -> None:
    with pytest.raises(ValueError, match="typo"):
        eh._parse("ctrl+typo")  # noqa: SLF001 — must not degrade to bare Ctrl


def test_pynput_right_ctrl_matches_only_the_right_side() -> None:
    settings = ActivationSettings(trigger="pynput", hotkey="right_ctrl")
    right_only = HotkeyManager(MagicMock(), MagicMock(), settings)
    assert right_only._normalise(Key.ctrl_r) is Key.ctrl_r  # noqa: SLF001
    assert right_only._normalise(Key.ctrl_l) not in right_only._hotkey  # noqa: SLF001

    either = HotkeyManager(MagicMock(), MagicMock(), ActivationSettings(trigger="pynput"))
    assert either._normalise(Key.ctrl_r) is Key.ctrl  # noqa: SLF001 — unchanged for ctrl+space


# -- HotkeyManager wiring ------------------------------------------------------

def test_manager_runs_the_evdev_listener_and_restarts_it_only_on_change() -> None:
    with patch.object(eh, "EvdevHotkey") as cls:
        mgr = HotkeyManager(MagicMock(), MagicMock(), ActivationSettings(trigger="evdev"))
        mgr.start()
        assert cls.call_count == 1 and cls.call_args.args[2:] == ("f9", "toggle")
        cls.return_value.start.assert_called_once()

        mgr.apply(ActivationSettings(trigger="evdev", debounce_ms=100))  # unrelated
        assert cls.call_count == 1

        mgr.apply(ActivationSettings(trigger="evdev", evdev_hotkey="pause"))
        assert cls.call_count == 2
        cls.return_value.stop.assert_called_once()

        mgr.apply(ActivationSettings(trigger="dbus"))  # DBus owns it now
        assert cls.call_count == 2 and mgr._evdev is None  # noqa: SLF001


def test_manager_reports_an_invalid_evdev_hotkey(caplog: pytest.LogCaptureFixture) -> None:
    service = MagicMock()
    with patch("budgie_voice.orchestrator.hotkey.dispatch") as dispatch:
        mgr = HotkeyManager(service, MagicMock(), ActivationSettings(trigger="evdev", evdev_hotkey="ctrl+typo"))
        mgr.start()

    assert mgr._evdev is None  # noqa: SLF001
    assert "ctrl+typo" in caplog.text
    dispatch.assert_called_once()
    service.notify.assert_called_once()
