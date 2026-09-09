"""CLI for fedlex-sync: sync federal law AKN XML to local store."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from lex_fedlex_sync.runlog import configure_console, end_run_log, log, start_run_log
from lex_fedlex_sync.sparql import SYNC_LANGS, fetch_index, make_client


def _parse_langs(raw: str) -> tuple[str, ...]:
    """Parse comma-separated language codes, validating each."""
    valid = {"de", "fr", "it"}
    codes = tuple(c.strip() for c in raw.split(","))
    for c in codes:
        if c not in valid:
            print(f"Invalid language: {c!r} (valid: {', '.join(sorted(valid))})",
                  file=sys.stderr)
            sys.exit(1)
    return codes


def cmd_index(args: argparse.Namespace) -> None:
    """List all in-force federal laws from Fedlex SPARQL."""
    langs = _parse_langs(args.langs)
    with make_client() as client:
        entries = fetch_index(client, langs=langs)

    if args.filter:
        pattern = args.filter.lower()
        entries = [
            e for e in entries
            if pattern in e.sr.lower()
            or pattern in e.title.lower()
        ]

    for entry in entries:
        abbr = f" ({entry.abbreviation})" if entry.abbreviation else ""
        # Fedlex titles occasionally contain line breaks; keep one law per line.
        title = " ".join(entry.title.split())
        print(f"{entry.sr:>10}  {title}{abbr}")

    print(f"\n{len(entries)} laws", file=sys.stderr)


def cmd_sync(args: argparse.Namespace) -> None:
    """Sync federal laws to local AKN store, writing one run log per sync."""
    from lex_fedlex_sync.sync import sync_all

    langs = _parse_langs(args.langs)
    store_root = Path(args.store).resolve()
    configure_console(quiet=args.quiet)
    log_path, handler = start_run_log(
        store_root, command=shlex.join(sys.argv),
    )
    try:
        try:
            stats = sync_all(
                store_root,
                langs=langs,
                mode=args.mode,
                limit=args.limit,
                workers=args.workers,
                filter_pattern=args.filter,
                force=args.force,
            )
        except Exception as exc:  # noqa: BLE001 — record the abort in the run log
            log.error("Sync aborted: %r", exc)
            log.info("Run log: %s", log_path)
            raise
        log.info("Run log: %s", log_path)
    finally:
        end_run_log(handler)

    if stats.failures:
        sys.exit(1)


def cmd_status(args: argparse.Namespace) -> None:
    """Show sync status and statistics."""
    from lex_fedlex_sync.store import count_laws, read_index

    store_root = Path(args.store)
    index = read_index(store_root)

    if not index:
        print("No sync data found.", file=sys.stderr)
        return

    total_laws = len(index)
    total_history = sum(len(v.get("synced_history", [])) for v in index.values())
    laws_on_disk = count_laws(store_root)

    # Detect languages synced
    all_langs: set[str] = set()
    for v in index.values():
        all_langs.update(v.get("langs", []))
    lang_label = "+".join(sorted(all_langs)) if all_langs else "?"

    print(f"Store: {store_root / 'ch'}")
    print(f"Laws in index:       {total_laws}")
    print(f"Laws on disk:        {laws_on_disk}")
    print(f"Historical versions: {total_history}")
    print(f"Languages:           {lang_label}")

    # Find oldest and newest sync
    synced_times = [v["synced_at"] for v in index.values() if "synced_at" in v]
    if synced_times:
        print(f"Oldest sync: {min(synced_times)}")
        print(f"Newest sync: {max(synced_times)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fedlex-sync",
        description="Sync federal law AKN XML from Fedlex to local store",
    )
    parser.add_argument(
        "--langs", default=",".join(SYNC_LANGS),
        help=f"Comma-separated languages (default: {','.join(SYNC_LANGS)})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # index
    p = sub.add_parser("index", aliases=["ls"], help="List in-force federal laws")
    p.add_argument("filter", nargs="?", help="Filter by SR number or title")

    # sync
    p = sub.add_parser("sync", help="Sync federal laws to local store")
    p.add_argument(
        "-s", "--store", default="data",
        help="Store directory (default: data)",
    )
    p.add_argument("filter", nargs="?", help="Filter by SR number or title")
    p.add_argument(
        "--mode", default="latest",
        choices=["latest", "include-history", "all-versions"],
        help="Sync mode (default: latest)",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="Max number of laws to process",
    )
    p.add_argument(
        "--workers", type=int, default=8,
        help="Parallel download workers (default: 8)",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Re-sync even if unchanged",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Hide per-law progress on the console (the run log keeps it)",
    )

    # status
    p = sub.add_parser("status", help="Show sync statistics")
    p.add_argument(
        "-s", "--store", default="data",
        help="Store directory (default: data)",
    )

    args = parser.parse_args()

    commands = {
        "index": cmd_index,
        "ls": cmd_index,
        "sync": cmd_sync,
        "status": cmd_status,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
