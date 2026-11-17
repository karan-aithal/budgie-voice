"""Global hotkey from raw evdev key events — works on X11 and Wayland.

Reads every keyboard under ``/dev/input`` and never grabs one (no ``EVIOCGRAB``, no
uinput re-emit): a hang here cannot take the keyboard away. The flip side is that the
hotkey still reaches the focused application, exactly as with pynput — pick a key
applications ignore (the default is ``f9``; ``pause`` is quieter where the keyboard has it).

Needs read access to ``/dev/input/event*``: membership of the ``input`` group or a udev
rule. This module only *prints* the fix; it never changes group membership.

Privacy: only key codes that belong to the configured hotkey are looked at or kept, and
nothing but device names is ever logged.
"""
from __future__ import annotations

import functools
import glob
import logging
import select
import threading
import time
from typing import TYPE_CHECKING, Literal

import evdev
from evdev import ecodes

from budgie_voice.utils.aio import dispatch

if TYPE_CHECKING:
    import asyncio

    from budgie_voice.orchestrator.dictation_service import DictationService

_LOG = logging.getLogger(__name__)

_RESCAN_S = 2.0  # ponytail: polling for hotplug; pyudev/inotify only if 2 s latency ever bites
_POLL_S = 0.5    # also the worst-case delay for stop() to take effect

_FIX = (
    "add yourself to the input group — run: sudo usermod -aG input $USER — then log out "
    "and back in (or install a udev rule granting read access to /dev/input/event*)"
)

_NAMES: dict[str, tuple[int, ...]] = {
    "ctrl":        (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL),
    "right_ctrl":  (ecodes.KEY_RIGHTCTRL,),
    "alt":         (ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT),
    "shift":       (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT),
    "super":       (ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA),
    "cmd":         (ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA),
    "win":         (ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA),
    "space":       (ecodes.KEY_SPACE,),
    "tab":         (ecodes.KEY_TAB,),
    "esc":         (ecodes.KEY_ESC,),
    "enter":       (ecodes.KEY_ENTER,),
    "pause":       (ecodes.KEY_PAUSE,),
    "scroll_lock": (ecodes.KEY_SCROLLLOCK,),
    **{f"f{n}": (ecodes.ecodes[f"KEY_F{n}"],) for n in range(1, 13)},
}


def _parse(combo: str) -> list[frozenset[int]]:
    """``"ctrl+space"`` → one set of acceptable key codes per token (left/right variants merged).

    Raises:
        ValueError: A token is not a known key name. Unlike the pynput parser this never
            drops it silently — ``ctrl+typo`` must not turn into a bare-Ctrl hotkey.
    """
    groups: list[frozenset[int]] = []
    for tok in (t.strip() for t in combo.lower().split("+")):
        codes = _NAMES.get(tok)
        if codes is None and len(tok) == 1 and tok.isalnum():
            code = ecodes.ecodes.get(f"KEY_{tok.upper()}")
            codes = (code,) if code is not None else None
        if not codes:
            raise ValueError(f"unknown key {tok!r}")
        groups.append(frozenset(codes))
    return groups


# -- device discovery ---------------------------------------------------------
# _paths/_open are the only calls into the real /dev/input; tests replace them.

def _paths() -> list[str]:
    # Not evdev.list_devices(): it silently drops unreadable nodes, which would hide
    # exactly the "your keyboard is permission-denied" case this module must report.
    return sorted(glob.glob("/dev/input/event*"))


def _open(path: str) -> evdev.InputDevice:
    return evdev.InputDevice(path)


def _open_keyboards(paths: list[str]) -> tuple[dict[str, evdev.InputDevice], set[str], int]:
    """Open every readable keyboard in *paths*.

    Returns ``(opened by path, paths that are not keyboards, permission-denied count)``.
    A path that vanishes between listing and opening is in none of the three.
    """
    opened: dict[str, evdev.InputDevice] = {}
    other: set[str] = set()
    denied = 0
    for path in paths:
        try:
            dev = _open(path)
        except PermissionError:
            denied += 1
            continue
        except OSError:
            continue
        try:
            is_kbd = ecodes.KEY_A in dev.capabilities().get(ecodes.EV_KEY, ())
        except OSError:
            is_kbd = False
        if is_kbd:
            opened[path] = dev
        else:
            other.add(path)
            dev.close()
    return opened, other, denied


@functools.cache
def auto_backend() -> Literal["evdev", "dbus"]:
    """``trigger: auto`` on Wayland: evdev if any keyboard is readable, else dbus.

    Decided once per process — ``DictationService.toggle`` asks on every press, and the
    running listener must keep the same owner. Logs the choice and the reason.

    ponytail: "a readable keyboard" is any readable device with letter keys, which also
    matches e.g. a drawing tablet's button pad. We can't tell it from the real keyboard,
    so the listener warns loudly when other devices were permission-denied.
    """
    opened, _, denied = _open_keyboards(_paths())
    names = [dev.name for dev in opened.values()]
    for dev in opened.values():
        dev.close()
    if names:
        _LOG.info(
            "trigger auto → evdev: readable keyboard(s) %s (%d other device(s) permission-denied)",
            names, denied,
        )
        return "evdev"
    _LOG.info(
        "trigger auto → dbus: no readable keyboard under /dev/input (%d device(s) permission-denied)",
        denied,
    )
    return "dbus"


class EvdevHotkey:
    """Background thread that turns evdev key events into service calls.

    Dispatches exactly like the pynput listener: ``toggle("evdev")`` once per press in
    toggle mode, ``push_start``/``push_stop`` on press/release in hold mode.

    Args:
        service: Receives the dispatched coroutines.
        loop: The running asyncio event loop.
        hotkey: ``activation.evdev_hotkey``, same ``a+b`` format as ``activation.hotkey``.
        mode: ``"toggle"`` or ``"push_to_hold"``.

    Raises:
        ValueError: *hotkey* contains an unknown key name.
    """

    def __init__(
        self,
        service: DictationService,
        loop: asyncio.AbstractEventLoop,
        hotkey: str,
        mode: str,
    ) -> None:
        self._service = service
        self._loop = loop
        self._hotkey = hotkey
        self._mode = mode
        self._groups = _parse(hotkey)
        self._watched = frozenset().union(*self._groups)  # the only codes ever stored
        self._devs: dict[str, evdev.InputDevice] = {}
        self._pressed: dict[str, set[int]] = {}  # per device, so an unplug releases its keys
        self._not_kbd: set[str] = set()
        self._down = False
        self._next_scan = 0.0
        self._stop = threading.Event()

    def start(self) -> None:
        self._sync(initial=True)
        threading.Thread(target=self._run, name="budgie-evdev", daemon=True).start()
        _LOG.info("evdev hotkey started — mode=%s hotkey=%s", self._mode, self._hotkey)

    def stop(self) -> None:
        self._stop.set()  # the thread notices within _POLL_S and closes the devices

    # ------------------------------------------------------------------
    # Thread body
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self.poll(_POLL_S)
                except Exception:  # noqa: BLE001 — a dead thread means a silently dead hotkey
                    _LOG.exception("evdev poll failed — retrying")
                    self._stop.wait(1.0)
        finally:
            for dev in self._devs.values():
                dev.close()

    def poll(self, timeout: float) -> None:
        """One cycle: read whatever is ready, then rescan for plugged/unplugged keyboards."""
        by_fd = {dev.fd: path for path, dev in self._devs.items()}
        ready = select.select(list(by_fd), [], [], timeout)[0]
        if self._stop.is_set():
            return
        for fd in ready:
            path = by_fd[fd]
            try:
                for ev in self._devs[path].read():
                    if ev.type == ecodes.EV_KEY:
                        self._on_key(path, ev.code, ev.value)
            except BlockingIOError:
                pass
            except OSError:  # ENODEV: unplugged
                self._drop(path)
        if time.monotonic() >= self._next_scan:
            self._sync()

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------

    def _sync(self, initial: bool = False) -> None:
        """Adopt newly plugged keyboards and forget unplugged ones."""
        self._next_scan = time.monotonic() + _RESCAN_S
        paths = _paths()
        for gone in [p for p in self._devs if p not in paths]:
            self._drop(gone)
        self._not_kbd &= set(paths)
        opened, other, denied = _open_keyboards(
            [p for p in paths if p not in self._devs and p not in self._not_kbd]
        )
        self._not_kbd |= other
        for path, dev in opened.items():
            self._devs[path] = dev
            self._pressed[path] = set()
            _LOG.info("evdev: opened %r (%s)", dev.name, path)
        if initial:
            self._report(denied)

    def _drop(self, path: str) -> None:
        dev = self._devs.pop(path)
        self._pressed.pop(path, None)
        _LOG.info("evdev: %r (%s) removed", dev.name, path)
        try:
            dev.close()
        except OSError:
            pass
        self._evaluate()  # a key still held on that device is now released

    def _report(self, denied: int) -> None:
        """Say plainly what we can and cannot see — never imply more than we have."""
        if not self._devs:
            msg = f"No readable keyboard in /dev/input — {_FIX}"
            _LOG.error("evdev hotkey cannot work: %s", msg)
            self._notify(f"⚠️ Hotkey unavailable. {msg}")
        elif denied:
            names = [dev.name for dev in self._devs.values()]
            msg = (
                f"Hotkey listens on {names} only; {denied} other input device(s) are "
                f"unreadable. If your keyboard is not listed, {_FIX}"
            )
            _LOG.warning("evdev: %s", msg)
            self._notify(f"⚠️ {msg}")

    def _notify(self, message: str) -> None:
        dispatch(self._service.notify(message), self._loop)

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    def _on_key(self, path: str, code: int, value: int) -> None:
        if code not in self._watched or value == 2:  # not our key / autorepeat
            return
        held = self._pressed[path]
        if value:
            held.add(code)
        else:
            held.discard(code)
        self._evaluate()

    def _evaluate(self) -> None:
        """Fire on the edge where the full combo becomes held / stops being held."""
        held = set().union(*self._pressed.values())
        down = all(group & held for group in self._groups)
        if down == self._down:
            return
        self._down = down
        if self._mode == "push_to_hold":
            dispatch(self._service.push_start() if down else self._service.push_stop(), self._loop)
        elif down:
            dispatch(self._service.toggle("evdev"), self._loop)
