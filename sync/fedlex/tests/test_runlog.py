"""Tests for per-run logging."""

from __future__ import annotations

import logging
from pathlib import Path

from lex_fedlex_sync.runlog import (
    configure_console,
    end_run_log,
    log,
    progress,
    start_run_log,
)


def test_start_run_log_creates_file_with_header(tmp_path: Path) -> None:
    path, handler = start_run_log(tmp_path, command="fedlex-sync sync --mode latest")
    try:
        log.warning("something odd")
        progress("  101  OK")
    finally:
        end_run_log(handler)

    assert path.parent == tmp_path / "sync_logs"
    assert path.name.endswith("_ch.log")
    text = path.read_text(encoding="utf-8")
    assert "CH sync run started" in text
    assert "command: fedlex-sync sync --mode latest" in text
    assert f"store:   {tmp_path}" in text
    assert "WARNING something odd" in text
    assert "INFO      101  OK" in text
    # detached: later records do not reach the closed file
    log.warning("after close")
    assert "after close" not in path.read_text(encoding="utf-8")


def test_two_runs_in_same_second_get_distinct_files(tmp_path: Path) -> None:
    p1, h1 = start_run_log(tmp_path, command="a")
    p2, h2 = start_run_log(tmp_path, command="b")
    end_run_log(h1)
    end_run_log(h2)
    assert p1 != p2
    assert p1.exists() and p2.exists()


def test_quiet_console_hides_progress_but_not_warnings(capsys) -> None:
    handler = configure_console(quiet=True)
    try:
        progress("progress line")
        log.info("run-level info")
        log.warning("a warning")
    finally:
        log.removeHandler(handler)
    err = capsys.readouterr().err
    assert "progress line" not in err
    assert "run-level info" in err
    assert "a warning" in err


def test_verbose_console_shows_progress(capsys) -> None:
    handler = configure_console(quiet=False)
    try:
        progress("progress line")
    finally:
        log.removeHandler(handler)
    assert "progress line" in capsys.readouterr().err


def test_logger_level_allows_info(tmp_path: Path) -> None:
    _, handler = start_run_log(tmp_path, command="x")
    end_run_log(handler)
    assert log.isEnabledFor(logging.INFO)
