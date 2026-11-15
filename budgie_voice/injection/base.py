"""TextOutputBackend ABC — versioned interface all injection backends implement."""
from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod

from budgie_voice.utils.exceptions import BudgieInjectionError

INTERFACE_VERSION = "1.0"

_PIECE_CHARS = 200


def chunk_text(text: str, limit: int = _PIECE_CHARS) -> list[str]:
    """Split *text* into pieces of at most *limit* characters, breaking after whitespace.

    Pieces join back to *text* exactly. A single unbroken run longer than *limit*
    (a URL) is cut hard. Typing in bounded pieces keeps every subprocess call short
    enough for a timeout that means something, and a failure loses at most one piece.
    """
    pieces: list[str] = []
    cur = ""
    for tok in re.findall(r"\S+\s*|\s+", text):
        while len(tok) > limit:
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(tok[:limit])
            tok = tok[limit:]
        if len(cur) + len(tok) > limit:
            pieces.append(cur)
            cur = ""
        cur += tok
    if cur:
        pieces.append(cur)
    return pieces


def type_timeout(piece: str, delay_ms: int) -> float:
    """Seconds a typing subprocess may take: 2x the pure key delay, plus 5 s margin
    for startup, keymap remaps and a busy desktop (raise the margin if typing is
    killed on a loaded machine)."""
    return len(piece) * max(delay_ms, 1) * 2 / 1000 + 5.0


def run_checked(cmd: list[str], timeout: float, what: str) -> None:
    """Run *cmd*; raise :exc:`BudgieInjectionError` on a timeout, spawn failure or non-zero exit."""
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BudgieInjectionError(f"{what} failed: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise BudgieInjectionError(f"{what} exited {result.returncode}: {stderr}")


class TextOutputBackend(ABC):
    """Abstract text injection backend.

    All implementations carry ``interface_version`` for capability negotiation.
    The target window is locked at :meth:`start_session` and never changes
    mid-session.
    """

    interface_version: str = INTERFACE_VERSION

    @abstractmethod
    async def start_session(self, target_window_id: int | None) -> None:
        """Lock the injection target and allocate any resources.

        Args:
            target_window_id: X11 window ID to inject into; ``None`` to
                auto-capture the current focus at session start.
        """

    @abstractmethod
    async def commit_text(self, text: str) -> None:
        """Inject *text* into the locked target window.

        Only printable characters and ``\\n`` are allowed; control characters
        and unsafe key combos are stripped before injection.

        Args:
            text: Finalised post-processed text to inject.
        """

    @abstractmethod
    async def cancel_session(self) -> None:
        """Abort the current session; discard any uncommitted text."""

    @abstractmethod
    async def end_session(self) -> None:
        """Finalise the session and release resources."""

    @abstractmethod
    def is_secure_field(self, window_id: int | None) -> bool:
        """Return ``True`` if *window_id* is a password / secure input field.

        Args:
            window_id: X11 window ID to check; ``None`` checks current focus.
        """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Human-readable backend identifier (e.g. ``"xdotool"``)."""
