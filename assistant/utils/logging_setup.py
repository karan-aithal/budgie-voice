"""Logging configuration: rotating file handler + stderr handler.

Call ``setup_logging()`` once at startup before any loggers are used.
"""

import logging
import logging.handlers
import sys
from pathlib import Path

_DEFAULT_LOG_DIR = Path.home() / ".local" / "share" / "voice-assistant" / "logs"


def setup_logging(
    level: str = "INFO",
    max_bytes: int = 5_242_880,
    backup_count: int = 3,
    log_dir: Path | None = None,
) -> None:
    """Configure root logger with a rotating file handler and a stderr handler.

    Args:
        level: Log level string — DEBUG | INFO | WARNING | ERROR.
        max_bytes: Maximum size of a single log file before rotation.
        backup_count: Number of rotated backup files to keep.
        log_dir: Directory for ``app.log``.
            Defaults to ``~/.local/share/voice-assistant/logs/``.
    """
    resolved_dir = log_dir or _DEFAULT_LOG_DIR
    resolved_dir.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        filename=resolved_dir / "app.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(numeric_level)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    stderr_handler.setLevel(numeric_level)

    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)
