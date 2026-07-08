"""WaylandBackend — stub for future Wayland input-method backend."""
from __future__ import annotations

from myna.injection.base import TextOutputBackend


class WaylandBackend(TextOutputBackend):
    """Stub: Wayland ``input-method-unstable-v2`` backend.

    Not yet implemented. Will support text injection on Wayland compositors
    via the ``zwp_input_method_v2`` protocol.
    """

    async def start_session(self, target_window_id: int | None) -> None:
        raise NotImplementedError(
            "WaylandBackend: future backend — not yet implemented"
        )

    async def commit_text(self, text: str) -> None:
        raise NotImplementedError(
            "WaylandBackend: future backend — not yet implemented"
        )

    async def cancel_session(self) -> None:
        raise NotImplementedError(
            "WaylandBackend: future backend — not yet implemented"
        )

    async def end_session(self) -> None:
        raise NotImplementedError(
            "WaylandBackend: future backend — not yet implemented"
        )

    def is_secure_field(self, window_id: int | None) -> bool:
        raise NotImplementedError(
            "WaylandBackend: future backend — not yet implemented"
        )

    @property
    def backend_name(self) -> str:
        return "wayland"
