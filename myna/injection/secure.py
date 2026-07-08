"""SecureFieldDetector — block injection into password / secure fields.

Checks ``xprop WM_CLASS`` and ``WM_NAME`` against blocklists.
Falls back gracefully on non-Linux / missing xprop.
"""
from __future__ import annotations

import logging
import re
import subprocess

_LOG = logging.getLogger(__name__)

BLOCKED_WM_CLASS: list[str] = [
    "pinentry",
    "gnome-keyring",
    "polkit-gnome",
    "gksu",
    "gcr-prompter",
    "org.gnome.polkit",
]

BLOCKED_TITLE_PATTERNS: list[str] = [
    r"password",
    r"passphrase",
    r"authentication",
    r"unlock",
    r"keyring",
    r"sudo",
    r"pin\b",
    r"secret",
]

_COMPILED_TITLE = [re.compile(p, re.IGNORECASE) for p in BLOCKED_TITLE_PATTERNS]


class SecureFieldDetector:
    """Probe X11 window properties to detect secure input fields.

    Uses ``xprop`` to read ``WM_CLASS`` and ``WM_NAME``.  Returns ``False``
    gracefully when ``xprop`` is unavailable (e.g. on non-X11 systems).
    """

    def is_secure(self, window_id: int | None = None) -> bool:
        """Return ``True`` if *window_id* matches the secure-field blocklist.

        Args:
            window_id: X11 window integer ID; ``None`` → always ``False``.
        """
        if window_id is None:
            return False

        wm_class = self._xprop(window_id, "WM_CLASS")
        wm_name = self._xprop(window_id, "WM_NAME")

        wm_class_lower = wm_class.lower()
        for blocked in BLOCKED_WM_CLASS:
            if blocked.lower() in wm_class_lower:
                _LOG.info("Secure field detected via WM_CLASS: %s", blocked)
                return True

        for pat in _COMPILED_TITLE:
            if pat.search(wm_name):
                _LOG.info("Secure field detected via WM_NAME pattern")
                return True

        return False

    @staticmethod
    def _xprop(window_id: int, prop: str) -> str:
        try:
            result = subprocess.run(
                ["xprop", "-id", str(window_id), prop],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return ""
