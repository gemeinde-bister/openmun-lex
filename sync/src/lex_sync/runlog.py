"""Run logging for lex-sync: console output plus one log file per run.

All sync modules log through the package logger (``log``).  Per-document
progress lines are tagged so ``--quiet`` can hide them on the console while
the run log file always receives everything.

Layout::

    {store_root}/sync_logs/{YYYYMMDD-HHMMSS}_vs.log

The run log is the authoritative record of a sync: header, every document
line, every warning and failure, validation results and the final summary.
The shared ``sync_report.log`` only carries the per-run summary.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

LOGGER_NAME = "lex_sync"
SOURCE = "vs"

log = logging.getLogger(LOGGER_NAME)

_CONSOLE_FORMAT = "%(message)s"
_FILE_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
_FILE_DATEFMT = "%Y-%m-%d %H:%M:%S"


def progress(msg: str, *args: object) -> None:
    """Log a per-document progress line (hidden on the console by --quiet)."""
    log.info(msg, *args, extra={"progress": True})


class _ConsoleFilter(logging.Filter):
    """Drop progress lines when quiet; warnings and errors always pass."""

    def __init__(self, quiet: bool) -> None:
        super().__init__()
        self._quiet = quiet

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        return not (self._quiet and getattr(record, "progress", False))


def configure_console(*, quiet: bool) -> logging.Handler:
    """Attach a stderr handler to the package logger.

    Returns the handler so callers can remove it again (tests).
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    handler.addFilter(_ConsoleFilter(quiet))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return handler


def start_run_log(
    store_root: Path,
    *,
    command: str,
    source: str = SOURCE,
) -> tuple[Path, logging.Handler]:
    """Create the per-run log file and attach it to the package logger.

    Args:
        store_root: Store root; the log goes to ``{store_root}/sync_logs/``.
        command: Human-readable command line, written into the header.
        source: Source tag for the file name (default ``vs``).

    Returns ``(path, handler)``; pass the handler to :func:`end_run_log`.
    """
    assert store_root.is_absolute(), f"store_root must be absolute: {store_root}"
    log_dir = store_root / "sync_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = log_dir / f"{stamp}_{source}.log"
    # Never overwrite an earlier run started within the same second.
    n = 1
    while path.exists():
        n += 1
        path = log_dir / f"{stamp}_{source}.{n}.log"

    handler = logging.FileHandler(path, mode="x", encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_FILE_DATEFMT))
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    log.info("=== %s sync run started ===", source.upper())
    log.info("command: %s", command)
    log.info("store:   %s", store_root)
    return path, handler


def end_run_log(handler: logging.Handler) -> None:
    """Flush, close and detach a handler returned by start_run_log."""
    handler.flush()
    log.removeHandler(handler)
    handler.close()
