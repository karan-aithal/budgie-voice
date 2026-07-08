"""XdgBackend — stub for future xdg-portal text-input backend."""
from __future__ import annotations

from myna.injection.base import TextOutputBackend


class XdgBackend(TextOutputBackend):
    """Stub: xdg-portal ``org.freedesktop.portal.InputMethod`` backend.

    Not yet implemented. Will support sandboxed Wayland/X11 apps via the
    xdg-desktop-portal text-input protocol.
    """

    async def start_session(self, target_window_id: int | None) -> None:
        raise NotImplementedError("XdgBackend: future backend — not yet implemented")

    async def commit_text(self, text: str) -> None:
        raise NotImplementedError("XdgBackend: future backend — not yet implemented")

    async def cancel_session(self) -> None:
        raise NotImplementedError("XdgBackend: future backend — not yet implemented")

    async def end_session(self) -> None:
        raise NotImplementedError("XdgBackend: future backend — not yet implemented")

    def is_secure_field(self, window_id: int | None) -> bool:
        raise NotImplementedError("XdgBackend: future backend — not yet implemented")

    @property
    def backend_name(self) -> str:
        return "xdg"
