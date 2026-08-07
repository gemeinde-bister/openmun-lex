"""Sync engine: fetch and store federal law AKN XML documents.

Trilingual sync: each version is fetched once per language (de, fr, it)
and stored side by side.  Historical versions are stored in date-stamped
subdirectories.
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

import httpx

from lex_fedlex_sync.sparql import (
    SYNC_LANGS,
    FedlexEntry,
    VersionInfo,
    fetch_all_xml_urls,
    fetch_index,
    fetch_law_status,
    make_client,
)
from lex_fedlex_sync.store import (
    LawMeta,
    VersionMeta,
    read_index,
    read_meta,
    write_index,
    write_meta,
    write_xml,
)


@dataclass
class SyncStats:
    """Aggregate statistics for a sync run."""

    total: int = 0
    synced: int = 0
    skipped: int = 0
    failed: int = 0
    no_xml: int = 0
    downloads: int = 0
    repealed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


def sync_all(
    store_root: Path,
    *,
    langs: tuple[str, ...] = SYNC_LANGS,
    mode: str = "latest",
    limit: int | None = None,
    workers: int = 8,
    filter_pattern: str | None = None,
    force: bool = False,
    quiet: bool = False,
) -> SyncStats:
    """Sync federal laws from Fedlex SPARQL to local store.

    Fetches index and XML URLs for all requested languages in single
    SPARQL queries, then downloads all language variants per version.

    Args:
        store_root: Root directory for the store.
        langs: Languages to sync (default: de, fr, it).
        mode: Sync mode — 'latest', 'include-history', or 'all-versions'.
        limit: Max number of laws to actually sync (skips don't count).
        workers: Number of parallel download workers.
        filter_pattern: Filter laws by SR number or title substring.
        force: Re-download even if version hasn't changed.
        quiet: Suppress per-law output.

    Returns:
        SyncStats with counts and failure details.
    """
    assert mode in ("latest", "include-history", "all-versions"), (
        f"Invalid mode: {mode}"
    )
    assert len(langs) >= 1, "At least one language required"

    stats = SyncStats()

    with make_client() as client:
        # 1. Fetch trilingual index
        if not quiet:
            lang_label = "+".join(langs)
            print(f"Fetching SR index from Fedlex SPARQL ({lang_label})...",
                  file=sys.stderr)
        entries = fetch_index(client, langs=langs)
        if not quiet:
            print(f"Found {len(entries)} in-force federal laws", file=sys.stderr)

        # 2. Apply filter
        if filter_pattern:
            pat = filter_pattern.lower()
            entries = [
                e for e in entries
                if pat in e.sr.lower() or pat in e.title.lower()
            ]

        # 3. Load existing index for incremental check
        sync_index = read_index(store_root) if not force else {}
        index_lock = Lock()

        # 4. Sort unsynced entries first so --limit hits new laws early
        if limit is not None and not force:
            synced_srs = set(sync_index)
            unsynced = [e for e in entries if e.sr not in synced_srs]
            already = [e for e in entries if e.sr in synced_srs]
            entries = unsynced + already

        stats.total = len(entries)
        if not quiet:
            lang_label = "+".join(langs)
            limit_msg = f", limit={limit}" if limit is not None else ""
            print(f"Syncing {stats.total} laws ({lang_label}, mode={mode}, "
                  f"workers={workers}{limit_msg})...",
                  file=sys.stderr)

        # 5. Parallel sync (httpx.Client is thread-safe)
        def do_sync(entry: FedlexEntry) -> None:
            _sync_one(
                client, entry, store_root, langs, mode,
                sync_index, index_lock, stats, limit, force, quiet,
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(do_sync, entry): entry
                for entry in entries
            }
            for future in as_completed(futures):
                exc = future.exception()
                if exc is not None:
                    entry = futures[future]
                    with index_lock:
                        stats.failed += 1
                        stats.failures.append((entry.sr, str(exc)))
                    if not quiet:
                        print(f"  {entry.sr:>10}  FAIL: {exc}", file=sys.stderr)

        # 6. Mark acts that dropped out of the in-force index as repealed.
        # Keep their files + stable ELI URI (URI-MODEL.md Principle 6); they
        # are excluded from search at index-build time.  Only on a full sync —
        # a filtered or limited run isn't authoritative about the in-force set.
        if filter_pattern is None and limit is None:
            in_force_srs = {e.sr for e in entries}
            dropped = [sr for sr in list(sync_index) if sr not in in_force_srs]
            if dropped and not quiet:
                print(f"Checking {len(dropped)} act(s) no longer in force...",
                      file=sys.stderr)
            for sr in dropped:
                _mark_repealed(client, sr, store_root, sync_index, stats, quiet)

    # 7. Write updated index
    write_index(store_root, sync_index)

    return stats


def _mark_repealed(
    client: httpx.Client,
    sr: str,
    store_root: Path,
    sync_index: dict,
    stats: SyncStats,
    quiet: bool,
) -> None:
    """Mark a dropped-out act as repealed in sync_index + meta.json.

    Idempotent: already-repealed acts are skipped.  Files are never deleted —
    the act stays resolvable at its ELI URI, just flagged and kept out of the
    search index.
    """
    entry = sync_index.get(sr, {})
    if entry.get("status") == "repealed":
        return
    law_uri = entry.get("law_uri")
    if not law_uri:
        return

    status_code, repealed_date = fetch_law_status(client, law_uri)
    if status_code == "0":
        # Still in force but absent from our in-force index — don't touch.
        # (Not expected on a full sync; stay conservative.)
        return

    entry["status"] = "repealed"
    entry["repealed_date"] = repealed_date
    entry["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    sync_index[sr] = entry

    meta = read_meta(store_root, sr)
    if meta is not None:
        meta.status = "repealed"
        meta.repealed_date = repealed_date
        write_meta(store_root, meta)

    stats.repealed += 1
    if not quiet:
        print(f"  {sr:>10}  REPEALED ({repealed_date or '?'}) — kept, "
              f"excluded from index", file=sys.stderr)


def _sync_one(
    client: httpx.Client,
    entry: FedlexEntry,
    store_root: Path,
    langs: tuple[str, ...],
    mode: str,
    sync_index: dict,
    index_lock: Lock,
    stats: SyncStats,
    limit: int | None,
    force: bool,
    quiet: bool,
) -> None:
    """Sync a single federal law in all requested languages."""
    sr = entry.sr

    # Early exit if limit already reached
    with index_lock:
        if limit is not None and stats.synced >= limit:
            return

    # Fetch all versions with all-language URLs in one SPARQL call
    versions = fetch_all_xml_urls(client, entry.law_uri, langs=langs)
    if mode == "latest" and versions:
        versions = versions[:1]

    if not versions:
        with index_lock:
            stats.no_xml += 1
        if not quiet:
            print(f"  {sr:>10}  SKIP (no XML)", file=sys.stderr)
        return

    # Incremental check
    with index_lock:
        existing = sync_index.get(sr, {})
    existing_history = set(existing.get("synced_history", []))
    existing_latest = existing.get("latest_date")
    existing_langs = set(existing.get("langs", []))
    latest_date = versions[0].date

    # Check if we need any work
    langs_match = set(langs) <= existing_langs
    if not force and mode == "latest" and existing_latest == latest_date and langs_match:
        with index_lock:
            stats.skipped += 1
        return

    # Reserve a slot atomically before expensive download
    with index_lock:
        if limit is not None and stats.synced >= limit:
            return
        stats.synced += 1
        reserved_position = stats.synced

    # Download and store
    new_history: list[str] = []
    downloaded_latest = False
    n_downloads = 0
    try:
        for i, ver in enumerate(versions):
            if i == 0:
                # Latest version: always write (we passed the skip check above)
                if not force and existing_latest == latest_date and langs_match:
                    continue
                for lang in langs:
                    url = ver.urls.get(lang)
                    if url is None:
                        continue
                    xml_bytes = _download_xml(client, url)
                    if xml_bytes is None:
                        continue
                    write_xml(store_root, sr, lang, xml_bytes)
                    n_downloads += 1
                downloaded_latest = True
            else:
                # Historical version: skip if date already synced AND all langs present
                if not force and ver.date in existing_history and langs_match:
                    continue
                for lang in langs:
                    url = ver.urls.get(lang)
                    if url is None:
                        continue
                    xml_bytes = _download_xml(client, url)
                    if xml_bytes is None:
                        continue
                    write_xml(store_root, sr, lang, xml_bytes, date=ver.date)
                    n_downloads += 1
                new_history.append(ver.date)

        # Update meta.json
        meta = LawMeta(
            sr=sr,
            titles=entry.titles,
            abbreviations=entry.abbreviations,
            law_uri=entry.law_uri,
            versions=[
                VersionMeta(date=v.date, urls=v.urls) for v in versions
            ],
        )
        write_meta(store_root, meta)

        # Update sync index
        all_history = sorted(existing_history | set(new_history), reverse=True)
        with index_lock:
            sync_index[sr] = {
                "law_uri": entry.law_uri,
                "titles": entry.titles,
                "abbreviations": entry.abbreviations,
                "latest_date": latest_date,
                "synced_history": all_history,
                "langs": list(langs),
                "status": "in_force",
                "repealed_date": None,
                "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            stats.downloads += n_downloads
    except Exception:
        # Release the reserved slot on failure
        with index_lock:
            stats.synced -= 1
            stats.failed += 1
            stats.failures.append((sr, "download failed"))
        return

    if not quiet:
        new_count = (1 if downloaded_latest else 0) + len(new_history)
        total_count = 1 + len(all_history)
        print(
            f"  [{reserved_position}/{limit or '∞'}] {sr:>10}  "
            f"OK ({n_downloads} files, {new_count} new/{total_count} total versions)",
            file=sys.stderr,
        )


def _download_xml(client: httpx.Client, url: str) -> bytes | None:
    """Download XML bytes from a Fedlex filestore URL."""
    try:
        resp = client.get(url, timeout=60)
        resp.raise_for_status()
        return resp.content
    except httpx.HTTPError:
        return None
