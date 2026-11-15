"""XdotoolBackend — X11 text injection via xdotool.

The target window ID is locked at :meth:`start_session`, then *activated*
immediately before typing so keystrokes land in it even if focus moved during
transcription.

Deliberately does NOT pass ``--window`` to ``xdotool type``. Per xdotool(1)
"SENDEVENT NOTES", ``--window`` switches xdotool from XTEST to ``XSendEvent``,
and X11 marks those events with a synthetic flag that "many programs observe
and reject" — Electron (VS Code), Firefox, Chrome and most GTK apps drop them
silently, so the text vanishes with no error. Activating the window first and
typing via XTEST is the only approach that works across toolkits.
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

# Settle time after the WM reports the target window active, before typing.
_FOCUS_SETTLE_S = 0.05


def _check_xdotool() -> None:
    """Raise :exc:`BudgieInjectionError` if xdotool is not on PATH."""
    try:
        subprocess.run(
            ["xdotool", "--version"],
            capture_output=True,
            timeout=3,
        )
    except (FileNotFoundError, OSError) as exc:
        raise BudgieInjectionError(
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
            BudgieInjectionError: xdotool invocation fails.
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
        self._activate_target()
        parts = text.split("\n")
        for idx, part in enumerate(parts):
            if part:
                self._type_segment(part)
            if idx < len(parts) - 1:
                self._press_key("Return")

    def _activate_target(self) -> None:
        """Give the locked target window X input focus before typing.

        Required because we type via XTEST (see module docstring), and XTEST
        delivers to whatever currently holds focus — which by injection time may
        be the recording overlay or whatever the user clicked. ``--sync`` waits
        for the window to actually become active rather than racing it.

        Best-effort: if the window is gone or refuses focus we still type, since
        the current focus is usually the right place anyway.
        """
        if self._window_id is None:
            return
        try:
            result = subprocess.run(
                ["xdotool", "windowactivate", "--sync", str(self._window_id)],
                check=False, capture_output=True, timeout=5,
            )
            if result.returncode != 0:
                _LOG.warning(
                    "windowactivate %s failed (%s) — typing into current focus",
                    self._window_id,
                    result.stderr.decode("utf-8", "replace").strip(),
                )
                return
        except (OSError, subprocess.TimeoutExpired) as exc:
            _LOG.warning("windowactivate failed (%s) — typing into current focus", exc)
            return
        # --sync returns once the WM reports the window active, but toolkits
        # still need a moment to move the caret into the focused widget; without
        # this the first characters are occasionally dropped. Real-world timing
        # knob, not a magic number — raise it if leading characters go missing.
        time.sleep(_FOCUS_SETTLE_S)

    def _type_segment(self, text: str) -> None:
        # No --window: that would switch xdotool to XSendEvent, which most
        # toolkits reject. See module docstring.
        for piece in chunk_text(text):
            run_checked(
                ["xdotool", "type", "--clearmodifiers", "--delay", str(self._delay_ms), "--", piece],
                type_timeout(piece, self._delay_ms), "xdotool type",
            )

    def _press_key(self, key: str) -> None:
        run_checked(["xdotool", "key", "--clearmodifiers", key], 5, "xdotool key")


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
