"""Tests for the VS sync orchestrator (validation + reporting)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lxml import etree

# Import orchestrator functions — the script lives outside the package,
# so we add its directory to sys.path.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".." / "scripts"))

from sync_vs import (
    _cross_check,
    _format_stats,
    _validate_xml_files,
    run_validate,
    write_report,
)

from lex_sync.convert import AKN_NS
from lex_sync.sync import SyncStats


def _write_minimal_xml(path: Path) -> None:
    """Write a minimal valid AKN XML file."""
    nsmap = {None: AKN_NS}
    root = etree.Element(f"{{{AKN_NS}}}akomaNtoso", nsmap=nsmap)
    etree.SubElement(root, f"{{{AKN_NS}}}act")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8"))


def _write_invalid_xml(path: Path) -> None:
    """Write an invalid XML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<broken xml", encoding="utf-8")


# --- XML validation ---


def test_validate_xml_all_valid(tmp_path: Path) -> None:
    vs_dir = tmp_path / "vs"
    _write_minimal_xml(vs_dir / "175.1" / "de.xml")
    _write_minimal_xml(vs_dir / "101.1" / "de.xml")

    errors = _validate_xml_files(vs_dir, "de")
    assert errors == []


def test_validate_xml_catches_invalid(tmp_path: Path) -> None:
    vs_dir = tmp_path / "vs"
    _write_minimal_xml(vs_dir / "175.1" / "de.xml")
    _write_invalid_xml(vs_dir / "999.1" / "de.xml")

    errors = _validate_xml_files(vs_dir, "de")
    assert len(errors) == 1
    assert "999.1" in errors[0]


def test_validate_xml_no_files(tmp_path: Path) -> None:
    vs_dir = tmp_path / "vs"
    vs_dir.mkdir(parents=True)

    errors = _validate_xml_files(vs_dir, "de")
    assert errors == []


# --- Cross-check ---


def test_cross_check_clean(tmp_path: Path) -> None:
    vs_dir = tmp_path / "vs"
    _write_minimal_xml(vs_dir / "175.1" / "de.xml")
    _write_minimal_xml(vs_dir / "175.1" / "fr.xml")
    _write_minimal_xml(vs_dir / "101.1" / "de.xml")  # de-only is fine

    warnings = _cross_check(vs_dir)
    assert warnings == []


def test_cross_check_fr_only_warns(tmp_path: Path) -> None:
    vs_dir = tmp_path / "vs"
    _write_minimal_xml(vs_dir / "175.1" / "de.xml")
    _write_minimal_xml(vs_dir / "175.1" / "fr.xml")
    _write_minimal_xml(vs_dir / "999.1" / "fr.xml")  # fr-only: suspicious

    warnings = _cross_check(vs_dir)
    assert len(warnings) == 1
    assert "999.1" in warnings[0]


# --- Stats formatting ---


def test_format_stats_clean() -> None:
    stats = SyncStats(total=100, synced=5, skipped=95)
    line = _format_stats(stats)
    assert "100 checked" in line
    assert "5 synced" in line
    assert "95 unchanged" in line
    assert "FAIL" not in line


def test_format_stats_with_failures() -> None:
    stats = SyncStats(total=100, synced=5, skipped=93, failed=2)
    line = _format_stats(stats)
    assert "2 FAILED" in line


def test_format_stats_with_warnings() -> None:
    stats = SyncStats(total=100, synced=5, skipped=95, warnings=3)
    line = _format_stats(stats)
    assert "3 warnings" in line


# --- Report writing ---


def test_write_report_creates_file(tmp_path: Path) -> None:
    report_path = tmp_path / "sync_report.log"
    stats = SyncStats(total=100, synced=3, skipped=97)

    write_report(report_path, stats, [], [], True)

    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert "VS sync: OK" in content
    assert "100 checked" in content


def test_write_report_appends(tmp_path: Path) -> None:
    report_path = tmp_path / "sync_report.log"
    stats = SyncStats(total=10, synced=1, skipped=9)

    write_report(report_path, stats, [], [], True)
    write_report(report_path, stats, [], [], True)

    content = report_path.read_text(encoding="utf-8")
    assert content.count("VS sync: OK") == 2


def test_write_report_records_failures(tmp_path: Path) -> None:
    report_path = tmp_path / "sync_report.log"
    stats = SyncStats(total=10, synced=8, failed=2)
    stats.failures = [("175.1", "timeout"), ("101.1", "parse error")]

    write_report(report_path, stats, [], [], False)

    content = report_path.read_text(encoding="utf-8")
    assert "FAILED" in content
    assert "175.1" in content
    assert "timeout" in content


def test_write_report_records_xml_errors(tmp_path: Path) -> None:
    report_path = tmp_path / "sync_report.log"
    stats = SyncStats(total=10, synced=10)

    write_report(
        report_path, stats,
        xml_errors=["999.1/de.xml: broken"],
        cross_warnings=[],
        success=False,
    )

    content = report_path.read_text(encoding="utf-8")
    assert "XML validation: 1 errors" in content
    assert "999.1" in content


def test_write_report_validate_only(tmp_path: Path) -> None:
    """Report works with stats=None (validate-only mode)."""
    report_path = tmp_path / "sync_report.log"

    write_report(report_path, None, [], [], True)

    content = report_path.read_text(encoding="utf-8")
    assert "VS sync: OK" in content
    assert "XML validation: OK" in content


# --- Full validation ---


def test_run_validate_clean(tmp_path: Path) -> None:
    vs_dir = tmp_path / "vs"
    _write_minimal_xml(vs_dir / "175.1" / "de.xml")
    _write_minimal_xml(vs_dir / "175.1" / "fr.xml")

    xml_errors, cross_warnings = run_validate(tmp_path)
    assert xml_errors == []
    assert cross_warnings == []


def test_run_validate_missing_vs_dir(tmp_path: Path) -> None:
    xml_errors, cross_warnings = run_validate(tmp_path)
    assert len(xml_errors) == 1
    assert "not found" in xml_errors[0]


def test_run_validate_catches_both_langs(tmp_path: Path) -> None:
    """Validates both de and fr XML files."""
    vs_dir = tmp_path / "vs"
    _write_minimal_xml(vs_dir / "175.1" / "de.xml")
    _write_invalid_xml(vs_dir / "175.1" / "fr.xml")
    _write_invalid_xml(vs_dir / "101.1" / "de.xml")

    xml_errors, cross_warnings = run_validate(tmp_path)
    assert len(xml_errors) == 2


# --- Date subdirectory validation ---


def test_validate_xml_date_subdirs(tmp_path: Path) -> None:
    """Validates XML files in date subdirectories."""
    vs_dir = tmp_path / "vs"
    _write_minimal_xml(vs_dir / "175.1" / "de.xml")
    _write_minimal_xml(vs_dir / "175.1" / "2023-01-01" / "de.xml")
    _write_minimal_xml(vs_dir / "175.1" / "2021-05-01" / "de.xml")

    errors = _validate_xml_files(vs_dir, "de")
    assert errors == []


def test_validate_xml_catches_invalid_in_date_subdir(tmp_path: Path) -> None:
    """Invalid XML in a date subdirectory is caught."""
    vs_dir = tmp_path / "vs"
    _write_minimal_xml(vs_dir / "175.1" / "de.xml")
    _write_invalid_xml(vs_dir / "175.1" / "2021-05-01" / "de.xml")

    errors = _validate_xml_files(vs_dir, "de")
    assert len(errors) == 1
    assert "175.1/2021-05-01" in errors[0]


def test_validate_xml_both_root_and_subdir_invalid(tmp_path: Path) -> None:
    """Reports errors from both root and date subdirectories."""
    vs_dir = tmp_path / "vs"
    _write_invalid_xml(vs_dir / "175.1" / "de.xml")
    _write_invalid_xml(vs_dir / "175.1" / "2021-05-01" / "de.xml")
    _write_minimal_xml(vs_dir / "175.1" / "2023-01-01" / "de.xml")

    errors = _validate_xml_files(vs_dir, "de")
    assert len(errors) == 2


def test_run_validate_includes_date_subdirs(tmp_path: Path) -> None:
    """Full validation covers date subdirectories."""
    vs_dir = tmp_path / "vs"
    _write_minimal_xml(vs_dir / "175.1" / "de.xml")
    _write_minimal_xml(vs_dir / "175.1" / "fr.xml")
    _write_invalid_xml(vs_dir / "175.1" / "2021-05-01" / "de.xml")
    _write_minimal_xml(vs_dir / "175.1" / "2021-05-01" / "fr.xml")

    xml_errors, cross_warnings = run_validate(tmp_path)
    assert len(xml_errors) == 1
    assert "2021-05-01" in xml_errors[0]


def test_cross_check_ignores_date_subdirs(tmp_path: Path) -> None:
    """Cross-check only compares root-level docs, not date subdirs."""
    vs_dir = tmp_path / "vs"
    _write_minimal_xml(vs_dir / "175.1" / "de.xml")
    _write_minimal_xml(vs_dir / "175.1" / "fr.xml")
    # Only de in a date subdir is fine — not flagged
    _write_minimal_xml(vs_dir / "175.1" / "2021-05-01" / "de.xml")

    warnings = _cross_check(vs_dir)
    assert warnings == []
