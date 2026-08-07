#!/usr/bin/env python3
"""Federal (Fedlex) law sync with validation and reporting.

Syncs German + French + Italian from the Fedlex SPARQL endpoint, validates
the downloaded AKN XML, cross-checks language coverage, and appends a summary
to the shared sync report.  This is the federal counterpart of ``sync_vs.py``.

Default mode is ``include-history`` — the public deployment (lex.bister.li)
serves historical versions, so every in-force version of each act is fetched,
not just the latest.

Usage:
    cd sync/fedlex && uv run python ../../scripts/sync_fedlex.py
    cd sync/fedlex && uv run python ../../scripts/sync_fedlex.py --force
    cd sync/fedlex && uv run python ../../scripts/sync_fedlex.py --validate-only
    cd sync/fedlex && uv run python ../../scripts/sync_fedlex.py --mode latest

Must be run from within the sync/fedlex/ directory (or with PYTHONPATH set)
so that lex_fedlex_sync is importable.
"""

from __future__ import annotations

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from lex_fedlex_sync.sparql import SYNC_LANGS
from lex_fedlex_sync.sync import SyncStats, sync_all

LANGS = SYNC_LANGS  # ("de", "fr", "it")


def _validate_xml_files(ch_dir: Path, lang: str) -> list[str]:
    """Parse every {lang}.xml under ch_dir, return list of syntax errors.

    Checks both root-level files (``{sr}/{lang}.xml``) and versioned date
    subdirectories (``{sr}/{date}/{lang}.xml``).
    """
    errors: list[str] = []

    # Root level: ch/{sr}/{lang}.xml
    for xml_path in sorted(ch_dir.glob(f"*/{lang}.xml")):
        try:
            ET.parse(str(xml_path))
        except ET.ParseError as exc:
            rel = f"{xml_path.parent.name}/{lang}.xml"
            errors.append(f"{rel}: {exc}")

    # Date subdirectories: ch/{sr}/{date}/{lang}.xml
    for xml_path in sorted(ch_dir.glob(f"*/*/{lang}.xml")):
        if xml_path.parent.parent == ch_dir:
            continue  # already covered at root level
        try:
            ET.parse(str(xml_path))
        except ET.ParseError as exc:
            sr = xml_path.parent.parent.name
            date_dir = xml_path.parent.name
            rel = f"{sr}/{date_dir}/{lang}.xml"
            errors.append(f"{rel}: {exc}")

    return errors


def _cross_check(ch_dir: Path) -> list[str]:
    """Check for language-coverage anomalies between root-level variants.

    German is the primary language (base for doc-type classification), so an
    act that exists in fr/it but is missing de is suspicious — it usually
    points to a partial download.  Also reports the per-language totals.
    """
    warnings: list[str] = []

    present: dict[str, set[str]] = {}
    for lang in LANGS:
        present[lang] = {
            p.parent.name
            for p in ch_dir.glob(f"*/{lang}.xml")
            if p.parent.parent == ch_dir
        }

    all_srs = set().union(*present.values()) if present else set()

    # Acts missing the primary (de) language despite having another variant.
    missing_de = (all_srs - present["de"]) if "de" in present else set()
    if missing_de:
        warnings.append(
            f"{len(missing_de)} acts missing de (primary): "
            f"{', '.join(sorted(missing_de)[:5])}"
            f"{'...' if len(missing_de) > 5 else ''}"
        )

    return warnings


def _format_stats(stats: SyncStats) -> str:
    """Format federal sync stats as a single summary line."""
    parts = [
        f"{stats.total} checked",
        f"{stats.synced} synced",
        f"{stats.skipped} unchanged",
        f"{stats.no_xml} no-xml",
        f"{stats.repealed} repealed",
        f"{stats.downloads} files",
    ]
    if stats.failed:
        parts.append(f"{stats.failed} FAILED")
    return ", ".join(parts)


def run_validate(store_root: Path) -> tuple[list[str], list[str]]:
    """Validate all federal XML files and cross-check languages.

    Returns (xml_errors, cross_check_warnings).
    """
    ch_dir = store_root / "ch"
    if not ch_dir.exists():
        return [f"CH directory not found: {ch_dir}"], []

    xml_errors: list[str] = []
    for lang in LANGS:
        xml_errors.extend(_validate_xml_files(ch_dir, lang))

    cross_warnings = _cross_check(ch_dir)
    return xml_errors, cross_warnings


def write_report(
    report_path: Path,
    stats: SyncStats | None,
    xml_errors: list[str],
    cross_warnings: list[str],
    success: bool,
) -> None:
    """Append a federal sync report entry to the log file."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    status = "OK" if success else "FAILED"

    lines = [f"[{timestamp}] CH sync: {status}"]
    if stats is not None:
        lines.append(f"  {_format_stats(stats)}")
        if stats.failures:
            for sr, err in stats.failures[:10]:
                lines.append(f"    FAIL {sr}: {err}")
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
        description="Federal (Fedlex) sync with validation (de + fr + it)",
    )
    parser.add_argument(
        "-s", "--store", default="../../data",
        help="Store directory (default: ../../data)",
    )
    parser.add_argument(
        "--mode", default="include-history",
        choices=["latest", "include-history", "all-versions"],
        help="Sync mode (default: include-history)",
    )
    parser.add_argument(
        "--workers", type=int, default=8,
        help="Parallel download workers (default: 8)",
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
        help="Filter: sync only laws matching this SR number or title",
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
            store_root,
            langs=LANGS,
            mode=args.mode,
            workers=args.workers,
            force=args.force,
            quiet=args.quiet,
            filter_pattern=args.filter,
        )
        xml_errors, cross_warnings = run_validate(store_root)
        success = stats.failed == 0 and len(xml_errors) == 0

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
