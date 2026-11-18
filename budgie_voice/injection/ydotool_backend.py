"""YdotoolBackend — text injection via ``ydotool`` (Linux uinput).

Unlike xdotool/XTEST, ydotool injects through ``/dev/uinput`` — a virtual
kernel input device — so it works under any compositor (X11, GNOME/Mutter
Wayland, wlroots, ...). No X11 window server involved, so there is no
window-ID targeting: text goes wherever focus currently is at injection
time, same as a physical keyboard.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time
import unicodedata

from budgie_voice.injection.base import TextOutputBackend, chunk_text, run_checked, type_timeout
from budgie_voice.injection.secure import SecureFieldDetector
from budgie_voice.utils.exceptions import BudgieInjectionError

_LOG = logging.getLogger(__name__)

_SOCKET = "/tmp/.ydotool_socket"
_KEY_ENTER = 28  # linux/input-event-codes.h KEY_ENTER


def is_ydotool_available() -> bool:
    """Return ``True`` if the ``ydotool`` binary is on PATH."""
    try:
        subprocess.run(["ydotool", "--help"], capture_output=True, timeout=3)
        return True
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def _ensure_daemon() -> None:
    """Start ``ydotoold`` if present and its socket isn't already up.

    Best-effort only: some ``ydotool`` builds ship without a separate
    ``ydotoold`` and fall back to talking to ``/dev/uinput`` directly
    (slightly higher per-call latency, otherwise fine) — so a missing
    daemon binary is not an error.
    """
    import os

    if os.path.exists(_SOCKET):
        return
    try:
        subprocess.Popen(
            ["ydotoold"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError):
        _LOG.info("ydotoold not present — ydotool will use direct /dev/uinput fallback")
        return
    for _ in range(20):  # up to ~2s
        if os.path.exists(_SOCKET):
            return
        time.sleep(0.1)
    _LOG.warning("ydotoold started but socket %s never appeared", _SOCKET)


def _sanitize(text: str) -> str:
    return "".join(
        c
        for c in text
        if c == "\n" or (c.isprintable() and unicodedata.category(c)[0] != "C")
    )


# ydotool types through a US-layout keycode table, so non-ASCII cannot be typed.
# These are the only such characters the post-processing pipeline emits
# (punctuation commands); anything else is left as the ASR produced it.
_ASCII_FALLBACK = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2014": "-"})


class YdotoolBackend(TextOutputBackend):
    """Inject text via ``ydotool`` (uinput) — works on native Wayland.

    Args:
        delay_ms: Per-key delay passed to ``ydotool type --key-delay``.
    """

    def __init__(self, delay_ms: int = 12) -> None:
        if not is_ydotool_available():
            raise BudgieInjectionError(
                "ydotool not found — install with: sudo apt install ydotool"
            )
        _ensure_daemon()
        self._delay_ms = delay_ms
        self._secure = SecureFieldDetector()

    # ------------------------------------------------------------------
    # TextOutputBackend interface
    # ------------------------------------------------------------------

    async def start_session(self, target_window_id: int | None) -> None:
        """No-op — uinput has no window concept; types wherever focus is."""

    async def commit_text(self, text: str) -> None:
        """Inject *text* wherever the current input focus is.

        Args:
            text: Post-processed text; may contain ``\\n``.

        Raises:
            BudgieInjectionError: ydotool invocation fails.
        """
        text = _sanitize(text).translate(_ASCII_FALLBACK)
        if not text:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._inject_sync, text)

    async def end_session(self) -> None:
        """No session state to release."""

    def is_secure_field(self, window_id: int | None) -> bool:
        """Best-effort secure-field check (X11-based; permissive on Wayland)."""
        return self._secure.is_secure(window_id)

    @property
    def backend_name(self) -> str:
        return "ydotool"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _inject_sync(self, text: str) -> None:
        parts = text.split("\n")
        for idx, part in enumerate(parts):
            if part:
                self._type(part)
            if idx < len(parts) - 1:
                self._enter()

    def _type(self, text: str) -> None:
        for piece in chunk_text(text):
            run_checked(
                ["ydotool", "type", "--key-delay", str(self._delay_ms), "--", piece],
                type_timeout(piece, self._delay_ms), "ydotool type",
            )

    def _enter(self) -> None:
        run_checked(["ydotool", "key", f"{_KEY_ENTER}:1", f"{_KEY_ENTER}:0"], 5, "ydotool key")
