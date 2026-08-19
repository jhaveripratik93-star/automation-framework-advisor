"""Centralized logging configuration for the Automation Framework Advisor.

Call configure_logging() once at app startup. Writes to logs/advisor.log
(rotating, 5MB max, 3 backups) and to stderr at WARNING+ level.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


class _SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that silently skips emission on OSError (e.g. disk full)."""

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        try:
            return super().shouldRollover(record)
        except OSError:
            return False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except OSError:
            pass


def configure_logging(level: int = logging.WARNING, log_dir: str = "logs") -> None:
    """Set up root logger with rotating file handler and console handler.

    Args:
        level: Minimum log level for the file handler (default WARNING).
        log_dir: Directory to write log files into.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if root.handlers:
        root.setLevel(logging.DEBUG)
        return

    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler — WARNING and above (avoids flooding disk)
    file_handler = _SafeRotatingFileHandler(
        log_path / "advisor.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)

    # Console handler — INFO and above (shows in terminal)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Suppress noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "watchdog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
