"""BackendSelector — auto-selects the best available injection backend.

Selection order for ``"auto"`` mode:
    1. Native Wayland session (``XDG_SESSION_TYPE=wayland``) →
       :class:`~myna.injection.ydotool_backend.YdotoolBackend` — IBus/xdotool
       both inject via X11 XTEST, which is a no-op against native Wayland
       windows (Mutter et al. don't forward synthetic X11 input to them).
       ydotool goes through ``/dev/uinput`` instead, which is
       compositor-agnostic.
    2. :class:`~myna.injection.ibus.IBusBackend` — if IBus daemon is running
       (X11 sessions only).
    3. :class:`~myna.injection.xdotool.XdotoolBackend` — X11 fallback.

Explicit selections (``"ibus"``, ``"xdotool"``, ``"xdg"``, ``"wayland"``,
``"ydotool"``) bypass probing.
"""
from __future__ import annotations

import logging
import os

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
        case "ydotool":
            return _make_ydotool(delay_ms)
        case _:
            raise ValueError(f"Unknown injection backend {backend_name!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auto_select(delay_ms: int) -> TextOutputBackend:
    """On native Wayland, prefer ydotool; else probe IBus, fall back to xdotool."""
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        from myna.injection.ydotool_backend import is_ydotool_available  # noqa: PLC0415

        if is_ydotool_available():
            try:
                backend = _make_ydotool(delay_ms)
                _LOG.info("Injection backend selected: ydotool (native Wayland session)")
                return backend
            except MynaInjectionError as exc:
                _LOG.warning("ydotool unavailable (%s) — falling back to xdotool/XTEST, "
                              "which will not work on native Wayland windows", exc)
        else:
            _LOG.warning(
                "Native Wayland session but ydotool not installed — falling back to "
                "xdotool/XTEST (sudo apt install ydotool for working text injection)"
            )

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


def _make_ydotool(delay_ms: int) -> TextOutputBackend:
    from myna.injection.ydotool_backend import YdotoolBackend  # noqa: PLC0415

    return YdotoolBackend(delay_ms=delay_ms)
