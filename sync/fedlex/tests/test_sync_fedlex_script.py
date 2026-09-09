"""Tests for the federal sync orchestrator script (validation + reporting)."""

from __future__ import annotations

import sys
from pathlib import Path

# The script lives outside the package; import it from scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".." / "scripts"))

from sync_fedlex import (  # noqa: E402
    _cross_check,
    _format_stats,
    _validate_xml_files,
    run_validate,
    write_report,
)

from lex_fedlex_sync.sync import SyncStats  # noqa: E402

AKN = b'<?xml version="1.0"?><akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><act/></akomaNtoso>'


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_validate_xml_catches_invalid_in_root_and_dated(tmp_path: Path) -> None:
    ch = tmp_path / "ch"
    _write(ch / "101" / "de.xml", AKN)
    _write(ch / "101" / "2023-01-01" / "de.xml", b"<broken")
    _write(ch / "210" / "de.xml", b"not xml at all")

    errors = _validate_xml_files(ch, "de")

    assert len(errors) == 2
    assert any(e.startswith("101/2023-01-01/de.xml") for e in errors)
    assert any(e.startswith("210/de.xml") for e in errors)


def test_cross_check_flags_missing_primary_language(tmp_path: Path) -> None:
    ch = tmp_path / "ch"
    _write(ch / "101" / "de.xml", AKN)
    _write(ch / "101" / "fr.xml", AKN)
    _write(ch / "210" / "fr.xml", AKN)  # no de

    warnings = _cross_check(ch)

    assert len(warnings) == 1
    assert "1 acts missing de" in warnings[0]
    assert "210" in warnings[0]


def test_run_validate_missing_store(tmp_path: Path) -> None:
    errors, warnings = run_validate(tmp_path)
    assert errors and "CH directory not found" in errors[0]
    assert warnings == []


def test_format_stats_includes_gaps_and_failures() -> None:
    stats = SyncStats(total=5, synced=2, skipped=2, failed=1, lang_gaps=3, downloads=6)
    line = _format_stats(stats)
    assert "5 checked" in line
    assert "3 language gaps" in line
    assert "1 FAILED" in line
    assert "6 files" in line


def test_write_report_ok(tmp_path: Path) -> None:
    report = tmp_path / "sync_report.log"
    stats = SyncStats(total=3, synced=3, downloads=9)

    write_report(report, stats, [], [], True, log_path=tmp_path / "run.log")

    text = report.read_text(encoding="utf-8")
    assert "CH sync: OK" in text
    assert "XML validation: OK" in text
    assert f"log: {tmp_path / 'run.log'}" in text


def test_write_report_records_failures_gaps_and_abort(tmp_path: Path) -> None:
    report = tmp_path / "sync_report.log"
    stats = SyncStats(total=3, synced=1, failed=1, lang_gaps=1)
    stats.failures = [("101", "download failed: https://x (HTTP 502)")]
    stats.gaps = [("210", "2019-01-01", "it")]

    write_report(
        report, stats, ["210/de.xml: broken"], ["1 acts missing de"], False,
        abort_reason="RuntimeError: index empty",
    )

    text = report.read_text(encoding="utf-8")
    assert "CH sync: FAILED" in text
    assert "ABORTED: RuntimeError: index empty" in text
    assert "FAIL 101: download failed" in text
    assert "GAP 210 2019-01-01: no it upstream" in text
    assert "XML validation: 1 errors" in text
    assert "WARN: 1 acts missing de" in text


def test_write_report_appends(tmp_path: Path) -> None:
    report = tmp_path / "sync_report.log"
    write_report(report, SyncStats(), [], [], True)
    write_report(report, SyncStats(), [], [], True)
    assert report.read_text(encoding="utf-8").count("CH sync: OK") == 2
