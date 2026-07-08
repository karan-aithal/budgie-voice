"""BackendSelector — auto-selects the best available injection backend.

Selection order for ``"auto"`` mode:
    1. :class:`~myna.injection.ibus.IBusBackend` — if IBus daemon is running.
    2. :class:`~myna.injection.xdotool.XdotoolBackend` — X11 fallback.

Explicit selections (``"ibus"``, ``"xdotool"``, ``"xdg"``, ``"wayland"``)
bypass probing.
"""
from __future__ import annotations

import logging

from myna.injection.base import TextOutputBackend
from myna.utils.exceptions import MynaInjectionError

_LOG = logging.getLogger(__name__)


def select_backend(
    backend_name: str = "auto",
    delay_ms: int = 12,
) -> TextOutputBackend:
    """Instantiate and return the appropriate :class:`TextOutputBackend`.

    Args:
        backend_name: One of ``"auto"``, ``"ibus"``, ``"xdotool"``,
            ``"xdg"``, ``"wayland"``.
        delay_ms: Per-keystroke delay forwarded to the chosen backend.

    Returns:
        A ready-to-use (but not yet session-started) backend.

    Raises:
        MynaInjectionError: Requested backend unavailable.
        ValueError: Unknown *backend_name*.
    """
    match backend_name:
        case "auto":
            return _auto_select(delay_ms)
        case "ibus":
            return _make_ibus(delay_ms)
        case "xdotool":
            return _make_xdotool(delay_ms)
        case "xdg":
            from myna.injection.xdg import XdgBackend  # noqa: PLC0415

            return XdgBackend()
        case "wayland":
            from myna.injection.wayland import WaylandBackend  # noqa: PLC0415

            return WaylandBackend()
        case _:
            raise ValueError(f"Unknown injection backend {backend_name!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auto_select(delay_ms: int) -> TextOutputBackend:
    """Probe IBus; fall back to xdotool."""
    from myna.injection.ibus import is_ibus_running  # noqa: PLC0415

    if is_ibus_running():
        try:
            backend = _make_ibus(delay_ms)
            _LOG.info("Injection backend selected: ibus")
            return backend
        except MynaInjectionError:
            pass

    _LOG.info("Injection backend selected: xdotool (IBus unavailable)")
    return _make_xdotool(delay_ms)


def _make_ibus(delay_ms: int) -> TextOutputBackend:
    from myna.injection.ibus import IBusBackend  # noqa: PLC0415

    return IBusBackend(delay_ms=delay_ms)


def _make_xdotool(delay_ms: int) -> TextOutputBackend:
    from myna.injection.xdotool import XdotoolBackend  # noqa: PLC0415

    return XdotoolBackend(delay_ms=delay_ms)
