"""BackendSelector — auto-selects the best available injection backend.

Selection order for ``"auto"`` mode:
    1. Native Wayland session (``XDG_SESSION_TYPE=wayland``) →
       :class:`~budgie_voice.injection.ydotool_backend.YdotoolBackend` — xdotool
       injects via X11 XTEST, which is a no-op against native Wayland
       windows (Mutter et al. don't forward synthetic X11 input to them).
       ydotool goes through ``/dev/uinput`` instead, which is
       compositor-agnostic. Requires the ``ydotool`` package and the running
       user in the ``input`` group (or a udev rule granting ``/dev/uinput``
       access) — that's the actual portability requirement across
       GNOME/Wayland distros, not IBus (IBus is an input-method framework,
       not a text-injection transport; committing text into an arbitrary
       focused window over IBus requires registering as its active engine,
       which this project doesn't implement).
    2. :class:`~budgie_voice.injection.xdotool.XdotoolBackend` — X11 fallback.

Explicit selections (``"xdotool"``, ``"ydotool"``) bypass probing.
"""
from __future__ import annotations

import logging
import os

from budgie_voice.injection.base import TextOutputBackend
from budgie_voice.utils.exceptions import BudgieInjectionError

_LOG = logging.getLogger(__name__)


def select_backend(
    backend_name: str = "auto",
    delay_ms: int = 12,
) -> TextOutputBackend:
    """Instantiate and return the appropriate :class:`TextOutputBackend`.

    Args:
        backend_name: One of ``"auto"``, ``"xdotool"``, ``"ydotool"``.
        delay_ms: Per-keystroke delay forwarded to the chosen backend.

    Returns:
        A ready-to-use (but not yet session-started) backend.

    Raises:
        BudgieInjectionError: Requested backend unavailable.
        ValueError: Unknown *backend_name*.
    """
    match backend_name:
        case "auto":
            return _auto_select(delay_ms)
        case "xdotool":
            return _make_xdotool(delay_ms)
        case "ydotool":
            return _make_ydotool(delay_ms)
        case _:
            raise ValueError(f"Unknown injection backend {backend_name!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auto_select(delay_ms: int) -> TextOutputBackend:
    """On native Wayland, prefer ydotool; else fall back to xdotool (X11)."""
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        from budgie_voice.injection.ydotool_backend import is_ydotool_available  # noqa: PLC0415

        if is_ydotool_available():
            try:
                backend = _make_ydotool(delay_ms)
                _LOG.info("Injection backend selected: ydotool (native Wayland session)")
                return backend
            except BudgieInjectionError as exc:
                _LOG.warning("ydotool unavailable (%s) — falling back to xdotool/XTEST, "
                              "which will not work on native Wayland windows", exc)
        else:
            _LOG.warning(
                "Native Wayland session but ydotool not installed — falling back to "
                "xdotool/XTEST (sudo apt install ydotool, add yourself to the 'input' "
                "group or add a udev rule for /dev/uinput, for working text injection)"
            )

    _LOG.info("Injection backend selected: xdotool")
    return _make_xdotool(delay_ms)


def _make_xdotool(delay_ms: int) -> TextOutputBackend:
    from budgie_voice.injection.xdotool import XdotoolBackend  # noqa: PLC0415

    return XdotoolBackend(delay_ms=delay_ms)


def _make_ydotool(delay_ms: int) -> TextOutputBackend:
    from budgie_voice.injection.ydotool_backend import YdotoolBackend  # noqa: PLC0415

    return YdotoolBackend(delay_ms=delay_ms)
