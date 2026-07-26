"""Rotating file + stderr logging for Myna.

Never log raw audio bytes or full transcription text at INFO+.
Transcriptions logged only at DEBUG level.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(level: str = "INFO") -> None:
    """Configure application-wide logging.

    Args:
        level: Root log level string (DEBUG / INFO / WARNING / ERROR).
    """
    log_dir = Path.home() / ".local" / "share" / "myna" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "myna.log"

    # Milliseconds matter: duplicate-trigger and session-lifecycle bugs turn on
    # sub-second gaps between events, which whole-second stamps hide entirely.
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(name)-28s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5_242_880,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not root.handlers:
        root.addHandler(file_handler)
        root.addHandler(stderr_handler)
