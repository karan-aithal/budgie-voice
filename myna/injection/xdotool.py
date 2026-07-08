"""XdotoolBackend — X11 text injection via xdotool.

The target window ID is locked at :meth:`start_session`; all subsequent
``commit_text`` calls use ``--window <id>`` so the wrong window is never
targeted even if focus changes during transcription.
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


def _check_xdotool() -> None:
    """Raise :exc:`MynaInjectionError` if xdotool is not on PATH."""
    try:
        subprocess.run(
            ["xdotool", "--version"],
            capture_output=True,
            timeout=3,
        )
    except (FileNotFoundError, OSError) as exc:
        raise MynaInjectionError(
            "xdotool not found — install with: sudo apt install xdotool"
        ) from exc


def _sanitize(text: str) -> str:
    """Strip non-printable characters; keep \\n."""
    return "".join(
        c
        for c in text
        if c == "\n" or (c.isprintable() and unicodedata.category(c)[0] != "C")
    )


class XdotoolBackend(TextOutputBackend):
    """Inject text into X11 windows using ``xdotool type``.

    Args:
        delay_ms: Per-key delay passed to ``xdotool type --delay``.
    """

    def __init__(self, delay_ms: int = 12) -> None:
        _check_xdotool()
        self._delay_ms = delay_ms
        self._window_id: int | None = None
        self._secure = SecureFieldDetector()

    # ------------------------------------------------------------------
    # TextOutputBackend interface
    # ------------------------------------------------------------------

    async def start_session(self, target_window_id: int | None) -> None:
        """Lock the target window for this session.

        Args:
            target_window_id: X11 window ID; ``None`` captures current focus.
        """
        if target_window_id is not None:
            self._window_id = target_window_id
        else:
            loop = asyncio.get_running_loop()
            self._window_id = await loop.run_in_executor(None, _get_focused_window)
        _LOG.debug("XdotoolBackend session started, window_id=%s", self._window_id)

    async def commit_text(self, text: str) -> None:
        """Inject *text* into the locked target window.

        Args:
            text: Post-processed text; may contain ``\\n``.

        Raises:
            MynaInjectionError: xdotool invocation fails.
        """
        text = _sanitize(text)
        if not text:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._inject_sync, text)

    async def cancel_session(self) -> None:
        """Discard session state; no text is injected."""
        self._window_id = None

    async def end_session(self) -> None:
        """Release session state."""
        self._window_id = None

    def is_secure_field(self, window_id: int | None) -> bool:
        """Return ``True`` if *window_id* is a password field."""
        return self._secure.is_secure(window_id)

    @property
    def backend_name(self) -> str:
        return "xdotool"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _inject_sync(self, text: str) -> None:
        parts = text.split("\n")
        for idx, part in enumerate(parts):
            if part:
                self._type_segment(part)
            if idx < len(parts) - 1:
                self._press_key("Return")

    def _type_segment(self, text: str) -> None:
        cmd = ["xdotool", "type"]
        if self._window_id is not None:
            cmd += ["--window", str(self._window_id)]
        cmd += ["--clearmodifiers", "--delay", str(self._delay_ms), "--", text]
        try:
            subprocess.run(cmd, check=False, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MynaInjectionError(f"xdotool type failed: {exc}") from exc

    def _press_key(self, key: str) -> None:
        cmd = ["xdotool", "key"]
        if self._window_id is not None:
            cmd += ["--window", str(self._window_id)]
        cmd += ["--clearmodifiers", key]
        try:
            subprocess.run(cmd, check=False, capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MynaInjectionError(f"xdotool key failed: {exc}") from exc


def _get_focused_window() -> int | None:
    """Return the current focused X11 window ID, or ``None`` on failure."""
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None
