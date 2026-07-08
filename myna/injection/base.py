"""TextOutputBackend ABC — versioned interface all injection backends implement."""
from __future__ import annotations

from abc import ABC, abstractmethod

INTERFACE_VERSION = "1.0"


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
