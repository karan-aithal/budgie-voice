"""IBusBackend — IBus-aware X11 text injection.

Probes whether the IBus daemon is running (used by :mod:`selector` for
auto-selection).  Injection is performed via ``xdotool`` with
``--clearmodifiers`` which respects the active IBus input method.

Full IBus engine protocol integration (committing text directly through
an IBus engine) is planned for a future phase; this implementation
provides correct IBus-compatible injection for Phase 2.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import unicodedata

from myna.injection.base import TextOutputBackend
from myna.injection.secure import SecureFieldDetector
from myna.utils.exceptions import MynaInjectionError

_LOG = logging.getLogger(__name__)


def is_ibus_running() -> bool:
    """Return ``True`` if the IBus daemon appears to be running."""
    try:
        result = subprocess.run(
            ["ibus", "list-engine"],
            capture_output=True,
            timeout=3,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


class IBusBackend(TextOutputBackend):
    """IBus-compatible text injection backend.

    Verifies IBus is running at construction; injects text via
    ``xdotool type --clearmodifiers`` which is compatible with IBus
    input methods.

    Args:
        delay_ms: Per-key delay for xdotool.

    Raises:
        MynaInjectionError: IBus daemon is not running or xdotool missing.
    """

    def __init__(self, delay_ms: int = 12) -> None:
        if not is_ibus_running():
            raise MynaInjectionError("IBus daemon is not running")
        self._delay_ms = delay_ms
        self._window_id: int | None = None
        self._secure = SecureFieldDetector()

    # ------------------------------------------------------------------
    # TextOutputBackend interface
    # ------------------------------------------------------------------

    async def start_session(self, target_window_id: int | None) -> None:
        """Lock the target window.

        Args:
            target_window_id: X11 window ID; ``None`` auto-captures focus.
        """
        if target_window_id is not None:
            self._window_id = target_window_id
        else:
            loop = asyncio.get_running_loop()
            self._window_id = await loop.run_in_executor(None, _get_focused_window)
        _LOG.debug("IBusBackend session started, window_id=%s", self._window_id)

    async def commit_text(self, text: str) -> None:
        """Inject *text* via xdotool (IBus-compatible).

        Args:
            text: Post-processed text to inject.
        """
        text = _sanitize(text)
        if not text:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._inject_sync, text)

    async def cancel_session(self) -> None:
        self._window_id = None

    async def end_session(self) -> None:
        self._window_id = None

    def is_secure_field(self, window_id: int | None) -> bool:
        return self._secure.is_secure(window_id)

    @property
    def backend_name(self) -> str:
        return "ibus"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _inject_sync(self, text: str) -> None:
        parts = text.split("\n")
        for idx, part in enumerate(parts):
            if part:
                cmd = ["xdotool", "type"]
                if self._window_id is not None:
                    cmd += ["--window", str(self._window_id)]
                cmd += ["--clearmodifiers", "--delay", str(self._delay_ms), "--", part]
                subprocess.run(cmd, check=False, capture_output=True, timeout=10)
            if idx < len(parts) - 1:
                cmd = ["xdotool", "key"]
                if self._window_id is not None:
                    cmd += ["--window", str(self._window_id)]
                cmd += ["--clearmodifiers", "Return"]
                subprocess.run(cmd, check=False, capture_output=True, timeout=5)


def _sanitize(text: str) -> str:
    return "".join(
        c
        for c in text
        if c == "\n" or (c.isprintable() and unicodedata.category(c)[0] != "C")
    )


def _get_focused_window() -> int | None:
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None
