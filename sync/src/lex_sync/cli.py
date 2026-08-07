"""CLI for lex-sync: sync lex.vs.ch cantonal law to local AKN store."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lex_sync.api import fetch_categories, fetch_index, make_client


def cmd_index(args: argparse.Namespace) -> None:
    """List all available laws from lex.vs.ch."""
    with make_client() as client:
        entries = fetch_index(client, lang=args.lang)

    if args.filter:
        pattern = args.filter.lower()
        entries = [
            e for e in entries
            if pattern in e.systematic_number.lower()
            or pattern in e.title.lower()
        ]

    if args.active_only:
        entries = [e for e in entries if not e.abrogated]

    for entry in entries:
        status = " [aufgehoben]" if entry.abrogated else ""
        print(f"{entry.systematic_number:>10}  {entry.title}{status}")

    print(f"\n{len(entries)} laws", file=sys.stderr)


def cmd_categories(args: argparse.Namespace) -> None:
    """Show the systematic category tree."""
    with make_client() as client:
        categories = fetch_categories(client, lang=args.lang)

    def show(cat, depth: int = 0) -> None:
        indent = "  " * depth
        print(f"{indent}{cat.systematic_number:>6}  {cat.name}")
        for child in cat.children:
            show(child, depth + 1)

    for cat in categories:
        show(cat)


def cmd_sync(args: argparse.Namespace) -> None:
    """Sync laws from lex.vs.ch to local AKN store."""
    from lex_sync.sync import sync_all

    store_root = Path(args.store)
    stats = sync_all(
        store_root,
        active_only=not args.all,
        filter_pattern=args.filter,
        force=args.force,
    )

    # Summary
    print(file=sys.stderr)
    print(f"Sync complete: {stats.synced} synced, "
          f"{stats.skipped} skipped, {stats.failed} failed, "
          f"{stats.warnings} warnings", file=sys.stderr)

    if stats.failures:
        print(f"\nFailed documents:", file=sys.stderr)
        for sysno, err in stats.failures:
            print(f"  {sysno}: {err}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lex-sync",
        description="Sync lex.vs.ch cantonal law to local AKN XML store",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # index
    p = sub.add_parser("index", aliases=["ls"], help="List all available laws")
    p.add_argument("filter", nargs="?", help="Filter by number or title")
    p.add_argument(
        "-l", "--lang", default="de", choices=["de", "fr"],
        help="Language (default: de)",
    )
    p.add_argument(
        "-a", "--active-only", action="store_true",
        help="Show only active (non-abrogated) laws",
    )

    # categories
    p = sub.add_parser("categories", aliases=["cat"], help="Show category tree")
    p.add_argument(
        "-l", "--lang", default="de", choices=["de", "fr"],
        help="Language (default: de)",
    )

    # sync
    p = sub.add_parser("sync", help="Sync laws to local AKN store (de+fr)")
    p.add_argument(
        "-s", "--store", default="data",
        help="Store directory (default: data)",
    )
    p.add_argument(
        "filter", nargs="?",
        help="Filter: sync only laws matching this number or title",
    )
    p.add_argument(
        "--all", action="store_true",
        help="Include abrogated laws (default: active only)",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Re-sync even if version hasn't changed",
    )

    args = parser.parse_args()

    commands = {
        "index": cmd_index,
        "ls": cmd_index,
        "categories": cmd_categories,
        "cat": cmd_categories,
        "sync": cmd_sync,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
