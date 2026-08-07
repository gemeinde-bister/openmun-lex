#!/usr/bin/env python3
"""VS cantonal law sync with validation and reporting.

Syncs German + French from lex.vs.ch in a single pass (one API call
per document), validates the output, and writes a summary report.

Usage:
    cd sync && uv run python ../scripts/sync_vs.py
    cd sync && uv run python ../scripts/sync_vs.py --store /path/to/data
    cd sync && uv run python ../scripts/sync_vs.py --force
    cd sync && uv run python ../scripts/sync_vs.py --validate-only

Must be run from within the sync/ directory (or with PYTHONPATH set)
so that lex_sync is importable.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from lxml import etree

from lex_sync.sync import SyncStats, sync_all


def _validate_xml_files(vs_dir: Path, lang: str) -> list[str]:
    """Parse every {lang}.xml under vs_dir, return list of errors.

    Checks both root-level files (``{sysno}/{lang}.xml``) and
    versioned date subdirectories (``{sysno}/{date}/{lang}.xml``).
    """
    errors: list[str] = []

    # Root level: vs/{sysno}/{lang}.xml
    for xml_path in sorted(vs_dir.glob(f"*/{lang}.xml")):
        try:
            etree.parse(str(xml_path))
        except etree.XMLSyntaxError as exc:
            rel = f"{xml_path.parent.name}/{lang}.xml"
            errors.append(f"{rel}: {exc}")

    # Date subdirectories: vs/{sysno}/{date}/{lang}.xml
    for xml_path in sorted(vs_dir.glob(f"*/*/{lang}.xml")):
        # Skip if already matched at root level (glob won't overlap, but be safe)
        if xml_path.parent.parent == vs_dir:
            continue
        try:
            etree.parse(str(xml_path))
        except etree.XMLSyntaxError as exc:
            sysno = xml_path.parent.parent.name
            date_dir = xml_path.parent.name
            rel = f"{sysno}/{date_dir}/{lang}.xml"
            errors.append(f"{rel}: {exc}")

    return errors


def _cross_check(vs_dir: Path) -> list[str]:
    """Check for anomalies between language variants.

    Only checks root-level docs (not date subdirectories) since
    both languages are always synced together per version.
    """
    warnings: list[str] = []

    de_docs = {p.parent.name for p in vs_dir.glob("*/de.xml") if p.parent.parent == vs_dir}
    fr_docs = {p.parent.name for p in vs_dir.glob("*/fr.xml") if p.parent.parent == vs_dir}

    # French-only docs are suspicious — could indicate a de sync failure
    fr_only = fr_docs - de_docs
    if fr_only:
        warnings.append(
            f"{len(fr_only)} docs exist in fr but not de: "
            f"{', '.join(sorted(fr_only)[:5])}"
            f"{'...' if len(fr_only) > 5 else ''}"
        )

    return warnings


def _format_stats(stats: SyncStats) -> str:
    """Format sync stats as a single summary line."""
    parts = [
        f"{stats.total} checked",
        f"{stats.synced} synced",
        f"{stats.skipped} unchanged",
    ]
    if stats.repealed:
        parts.append(f"{stats.repealed} repealed")
    if stats.failed:
        parts.append(f"{stats.failed} FAILED")
    if stats.warnings:
        parts.append(f"{stats.warnings} warnings")
    return ", ".join(parts)


def run_validate(store_root: Path) -> tuple[list[str], list[str]]:
    """Validate all XML files and cross-check languages.

    Returns (xml_errors, cross_check_warnings).
    """
    vs_dir = store_root / "vs"
    if not vs_dir.exists():
        return [f"VS directory not found: {vs_dir}"], []

    xml_errors: list[str] = []
    for lang in ("de", "fr"):
        xml_errors.extend(_validate_xml_files(vs_dir, lang))

    cross_warnings = _cross_check(vs_dir)
    return xml_errors, cross_warnings


def write_report(
    report_path: Path,
    stats: SyncStats | None,
    xml_errors: list[str],
    cross_warnings: list[str],
    success: bool,
) -> None:
    """Append a sync report entry to the log file."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    status = "OK" if success else "FAILED"

    lines = [f"[{timestamp}] VS sync: {status}"]
    if stats is not None:
        lines.append(f"  {_format_stats(stats)}")
        if stats.failures:
            for sysno, err in stats.failures[:10]:
                lines.append(f"    FAIL {sysno}: {err}")
            if len(stats.failures) > 10:
                lines.append(f"    ... and {len(stats.failures) - 10} more")

    if xml_errors:
        lines.append(f"  XML validation: {len(xml_errors)} errors")
        for err in xml_errors[:5]:
            lines.append(f"    {err}")
        if len(xml_errors) > 5:
            lines.append(f"    ... and {len(xml_errors) - 5} more")
    else:
        lines.append("  XML validation: OK")

    if cross_warnings:
        for w in cross_warnings:
            lines.append(f"  WARN: {w}")

    lines.append("")  # blank line separator

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VS sync with validation (de + fr, single pass)",
    )
    parser.add_argument(
        "-s", "--store", default="../data",
        help="Store directory (default: ../data)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-sync even if versions haven't changed",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-document output",
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="Skip sync, only validate existing files",
    )
    parser.add_argument(
        "--report", default=None,
        help="Report file path (default: {store}/sync_report.log)",
    )
    parser.add_argument(
        "filter", nargs="?",
        help="Filter: sync only laws matching this number or title",
    )
    args = parser.parse_args()

    store_root = Path(args.store).resolve()
    report_path = Path(args.report) if args.report else store_root / "sync_report.log"

    if args.validate_only:
        print("Validating existing files...", file=sys.stderr)
        xml_errors, cross_warnings = run_validate(store_root)
        stats = None
        success = len(xml_errors) == 0
    else:
        stats = sync_all(
            store_root, force=args.force, quiet=args.quiet,
            filter_pattern=args.filter,
        )
        xml_errors, cross_warnings = run_validate(store_root)
        success = stats.failed == 0 and len(xml_errors) == 0

    # Report
    write_report(report_path, stats, xml_errors, cross_warnings, success)

    # Summary to stderr
    print(file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    if stats is not None:
        print(f"  {_format_stats(stats)}", file=sys.stderr)
    if xml_errors:
        print(f"  XML errors: {len(xml_errors)}", file=sys.stderr)
    if cross_warnings:
        for w in cross_warnings:
            print(f"  WARN: {w}", file=sys.stderr)
    status = "OK" if success else "FAILED"
    print(f"  Status: {status}", file=sys.stderr)
    print(f"  Report: {report_path}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
