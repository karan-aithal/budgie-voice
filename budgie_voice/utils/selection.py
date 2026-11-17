"""Read the primary selection (the highlighted text) with the tool the session already has."""
from __future__ import annotations

import os
import subprocess


def read_primary() -> str:
    """Currently highlighted text, or "" if none or the tool is missing.

    Blocking (subprocess): call it from an executor. The text is never logged.
    """
    cmd = (
        ["wl-paste", "--primary", "--no-newline"]
        if os.environ.get("XDG_SESSION_TYPE") == "wayland"
        else ["xclip", "-o", "-selection", "primary"]
    )
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=2, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""
