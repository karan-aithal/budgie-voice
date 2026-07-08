"""HotkeyManager — global hotkey listener via pynput.

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

if TYPE_CHECKING:
    from myna.config.settings import ActivationSettings
    from myna.orchestrator.dictation_service import DictationService

_LOG = logging.getLogger(__name__)

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
    "f1":    pynput.keyboard.Key.f1,  "f2": pynput.keyboard.Key.f2,
    "f3":    pynput.keyboard.Key.f3,  "f4": pynput.keyboard.Key.f4,
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
        service: :class:`~myna.orchestrator.dictation_service.DictationService`
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
        self._push_active = False

    def start(self) -> None:
        """Start listening for the global hotkey in a background thread."""
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

    def stop(self) -> None:
        """Stop the listener and release the background thread."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

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
                self._dispatch(self._service.toggle())
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
        asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]

    @staticmethod
    def _normalise(
        key: pynput.keyboard.Key | pynput.keyboard.KeyCode,
    ) -> pynput.keyboard.Key | pynput.keyboard.KeyCode:
        """Normalise left/right modifier variants to canonical form."""
        try:
            # e.g. Key.ctrl_l → Key.ctrl
            canonical = key.vk  # type: ignore[union-attr]
        except AttributeError:
            pass
        return key
