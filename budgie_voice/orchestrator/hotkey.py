"""HotkeyManager — global hotkey listener: pynput (X11) or evdev (X11 + Wayland).

Which one runs is ``activation.resolved_trigger()``; the evdev listener lives in
:mod:`budgie_voice.orchestrator.evdev_hotkey`.

Supports two activation modes:

* ``"toggle"``      — one press starts; next press stops.
* ``"push_to_hold"``— hold to record; release to stop.

Callbacks run in a background pynput thread and dispatch coroutines
to the asyncio event loop via :func:`asyncio.run_coroutine_threadsafe`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pynput.keyboard

from budgie_voice.utils.aio import dispatch

if TYPE_CHECKING:
    from budgie_voice.config.settings import ActivationSettings
    from budgie_voice.orchestrator.dictation_service import DictationService
    from budgie_voice.orchestrator.evdev_hotkey import EvdevHotkey

_LOG = logging.getLogger(__name__)

# X11/Linux reports left/right modifier variants — collapse to the generic
# Key used by _KEY_MAP so held-combo matching works.
_MODIFIER_ALIASES: dict[pynput.keyboard.Key, pynput.keyboard.Key] = {
    pynput.keyboard.Key.ctrl_l: pynput.keyboard.Key.ctrl,
    pynput.keyboard.Key.ctrl_r: pynput.keyboard.Key.ctrl,
    pynput.keyboard.Key.alt_l: pynput.keyboard.Key.alt,
    pynput.keyboard.Key.alt_r: pynput.keyboard.Key.alt,
    pynput.keyboard.Key.alt_gr: pynput.keyboard.Key.alt,
    pynput.keyboard.Key.shift_l: pynput.keyboard.Key.shift,
    pynput.keyboard.Key.shift_r: pynput.keyboard.Key.shift,
    pynput.keyboard.Key.cmd_l: pynput.keyboard.Key.cmd,
    pynput.keyboard.Key.cmd_r: pynput.keyboard.Key.cmd,
}

# pynput Key names that map to special Key enum values
_KEY_MAP: dict[str, pynput.keyboard.Key] = {
    "ctrl":  pynput.keyboard.Key.ctrl,
    "alt":   pynput.keyboard.Key.alt,
    "shift": pynput.keyboard.Key.shift,
    "super": pynput.keyboard.Key.cmd,
    "cmd":   pynput.keyboard.Key.cmd,
    "win":   pynput.keyboard.Key.cmd,
    "space": pynput.keyboard.Key.space,
    "tab":   pynput.keyboard.Key.tab,
    "esc":   pynput.keyboard.Key.esc,
    "enter": pynput.keyboard.Key.enter,
    "right_ctrl":  pynput.keyboard.Key.ctrl_r,  # side-specific, see HotkeyManager._normalise
    "pause":       pynput.keyboard.Key.pause,
    "scroll_lock": pynput.keyboard.Key.scroll_lock,
    **{f"f{n}": getattr(pynput.keyboard.Key, f"f{n}") for n in range(1, 13)},
}


def _parse_hotkey(combo: str) -> frozenset[pynput.keyboard.Key | pynput.keyboard.KeyCode]:
    """Parse ``"ctrl+space"`` into a frozenset of pynput key objects."""
    keys: list[pynput.keyboard.Key | pynput.keyboard.KeyCode] = []
    for part in combo.lower().split("+"):
        part = part.strip()
        if part in _KEY_MAP:
            keys.append(_KEY_MAP[part])
        elif len(part) == 1:
            keys.append(pynput.keyboard.KeyCode.from_char(part))
        else:
            _LOG.warning("Unknown hotkey token %r — ignored", part)
    return frozenset(keys)


class HotkeyManager:
    """Global hotkey listener using ``pynput.keyboard.Listener``.

    Args:
        service: :class:`~budgie_voice.orchestrator.dictation_service.DictationService`
            whose coroutines are dispatched on key events.
        loop: The running asyncio event loop.
        settings: Activation configuration (mode + hotkey string).
    """

    def __init__(
        self,
        service: DictationService,
        loop: asyncio.AbstractEventLoop,
        settings: ActivationSettings,
    ) -> None:
        self._service = service
        self._loop = loop
        self._settings = settings
        self._hotkey = _parse_hotkey(settings.hotkey)
        self._pressed: set[pynput.keyboard.Key | pynput.keyboard.KeyCode] = set()
        self._listener: pynput.keyboard.Listener | None = None
        self._evdev: EvdevHotkey | None = None
        self._push_active = False

    def start(self) -> None:
        """Start the listener that owns the hotkey, in a background thread."""
        if self._settings.resolved_trigger() == "evdev":
            self._start_evdev()
            return
        self._listener = pynput.keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()
        _LOG.info(
            "HotkeyManager started — mode=%s hotkey=%s",
            self._settings.mode,
            self._settings.hotkey,
        )

    def apply(self, settings: ActivationSettings) -> None:
        """Adopt new activation settings; restart the listener only if needed."""
        old = self._settings
        self._settings = settings
        self._hotkey = _parse_hotkey(settings.hotkey)
        keys = ("hotkey", "evdev_hotkey", "command_hotkey", "mode", "trigger")
        if all(getattr(old, k) == getattr(settings, k) for k in keys):
            return
        self.stop()
        self._pressed.clear()
        self._push_active = False
        if settings.resolved_trigger() in ("pynput", "evdev"):
            self.start()

    def stop(self) -> None:
        """Stop the listener and release the background thread."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._evdev is not None:
            self._evdev.stop()
            self._evdev = None

    def _start_evdev(self) -> None:
        from budgie_voice.orchestrator.evdev_hotkey import EvdevHotkey  # noqa: PLC0415 — python-evdev only needed here

        try:
            self._evdev = EvdevHotkey(
                self._service, self._loop, self._settings.evdev_hotkey, self._settings.mode,
                command_hotkey=self._settings.command_hotkey or None,
            )
        except ValueError as exc:
            msg = (
                f"activation.evdev_hotkey {self._settings.evdev_hotkey!r} / "
                f"command_hotkey {self._settings.command_hotkey!r} is invalid ({exc}) — no hotkey"
            )
            _LOG.error(msg)
            dispatch(self._service.notify(f"⚠️ {msg}"), self._loop)
            return
        self._evdev.start()

    # ------------------------------------------------------------------
    # pynput callbacks (background thread)
    # ------------------------------------------------------------------

    def _on_press(self, key: pynput.keyboard.Key | pynput.keyboard.KeyCode) -> None:
        self._pressed.add(self._normalise(key))

        if not self._hotkey.issubset(self._pressed):
            return

        match self._settings.mode:
            case "toggle":
                self._pressed.clear()  # prevent repeat-fire while held
                self._dispatch(self._service.toggle("pynput"))
            case "push_to_hold":
                if not self._push_active:
                    self._push_active = True
                    self._dispatch(self._service.push_start())

    def _on_release(self, key: pynput.keyboard.Key | pynput.keyboard.KeyCode) -> None:
        self._pressed.discard(self._normalise(key))

        if self._settings.mode == "push_to_hold" and self._push_active:
            if not self._hotkey.issubset(self._pressed):
                self._push_active = False
                self._dispatch(self._service.push_stop())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dispatch(self, coro: object) -> None:
        dispatch(coro, self._loop)  # type: ignore[arg-type]

    def _normalise(
        self, key: pynput.keyboard.Key | pynput.keyboard.KeyCode,
    ) -> pynput.keyboard.Key | pynput.keyboard.KeyCode:
        """Normalise left/right modifier variants to canonical form.

        Linux/X11 reports ``ctrl_l``/``ctrl_r`` etc., never the generic
        ``Key.ctrl`` that ``_KEY_MAP`` uses — without this mapping the
        hotkey's ``issubset`` check can never match. A side the hotkey names
        explicitly (``right_ctrl``) is left alone so only that side matches.
        """
        return key if key in self._hotkey else _MODIFIER_ALIASES.get(key, key)
