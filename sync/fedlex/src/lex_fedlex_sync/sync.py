"""Sync engine: fetch and store federal law AKN XML documents.

Trilingual sync: each version is fetched once per language (de, fr, it)
and stored side by side.  Historical versions are stored in date-stamped
subdirectories.

Robustness rules:

- A download that fails (after retries) or does not return AKN XML is a
  **failure** for that act.  Nothing of the act is written and its index
  entry is not updated, so the next run picks it up again.
- A language Fedlex simply does not offer for a version is an **upstream
  gap**: counted, logged as a warning, and recorded in ``stats.gaps``.  The
  version is still stored in the languages that exist.
- All files of an act are downloaded and validated first, then written in
  one go, so a mid-way failure cannot leave the act half-updated.
- The sync index is always persisted, even if a later step raises.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

import httpx

from lex_fedlex_sync.runlog import log, progress
from lex_fedlex_sync.sparql import (
    SYNC_LANGS,
    FedlexEntry,
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
    xml_present,
)

AKN_ROOT_TAG = "{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}akomaNtoso"

# Download retry policy: transient transport errors and 5xx/429 responses are
# retried with a short backoff; other HTTP errors fail immediately.
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_BACKOFF_SECONDS = 1.0
DOWNLOAD_TIMEOUT_SECONDS = 60.0
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class DownloadError(Exception):
    """A Fedlex file could not be downloaded or is not AKN XML."""


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
    lang_gaps: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    # (sr, version date, lang) for every requested language Fedlex did not
    # offer as XML — upstream gaps, not download failures.
    gaps: list[tuple[str, str, str]] = field(default_factory=list)


def sync_all(
    store_root: Path,
    *,
    langs: tuple[str, ...] = SYNC_LANGS,
    mode: str = "latest",
    limit: int | None = None,
    workers: int = 8,
    filter_pattern: str | None = None,
    force: bool = False,
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

    Returns:
        SyncStats with counts and failure details.
    """
    assert mode in ("latest", "include-history", "all-versions"), (
        f"Invalid mode: {mode}"
    )
    assert len(langs) >= 1, "At least one language required"
    assert workers >= 1, f"workers must be >= 1: {workers}"

    stats = SyncStats()
    lang_label = "+".join(langs)

    with make_client() as client:
        # 1. Fetch trilingual index
        log.info("Fetching SR index from Fedlex SPARQL (%s)...", lang_label)
        entries = fetch_index(client, langs=langs)
        log.info("Found %d in-force federal laws", len(entries))
        if not entries:
            raise RuntimeError("Fedlex index query returned no in-force laws")

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
        limit_msg = f", limit={limit}" if limit is not None else ""
        log.info(
            "Syncing %d laws (%s, mode=%s, workers=%d%s)...",
            stats.total, lang_label, mode, workers, limit_msg,
        )

        try:
            # 5. Parallel sync (httpx.Client is thread-safe)
            def do_sync(entry: FedlexEntry) -> None:
                _sync_one(
                    client, entry, store_root, langs, mode,
                    sync_index, index_lock, stats, limit, force,
                )

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(do_sync, entry): entry
                    for entry in entries
                }
                for future in as_completed(futures):
                    exc = future.exception()
                    if exc is not None:
                        # _sync_one handles its own errors; anything escaping
                        # is a programmer error — still record it, never
                        # abort the run silently.
                        entry = futures[future]
                        _record_failure(stats, index_lock, entry.sr,
                                        f"unexpected: {exc!r}")

            # 6. Mark acts that dropped out of the in-force index as repealed.
            # Keep their files + stable ELI URI (URI-MODEL.md Principle 6);
            # they are excluded from search at index-build time.  Only on a
            # full sync — a filtered or limited run isn't authoritative about
            # the in-force set.
            if filter_pattern is None and limit is None:
                in_force_srs = {e.sr for e in entries}
                dropped = [sr for sr in list(sync_index) if sr not in in_force_srs]
                if dropped:
                    log.info("Checking %d act(s) no longer in force...", len(dropped))
                for sr in dropped:
                    try:
                        _mark_repealed(client, sr, store_root, sync_index, stats)
                    except Exception as exc:  # noqa: BLE001 — one act must not abort the run
                        _record_failure(stats, index_lock, sr, f"repeal-check: {exc!r}")
        finally:
            # 7. Always persist what was completed — entries are only added
            # once an act is fully written, so a partial index is consistent.
            write_index(store_root, sync_index)

    _log_summary(stats)
    return stats


def _record_failure(stats: SyncStats, lock: Lock, sr: str, reason: str) -> None:
    """Count and log a per-act failure (thread-safe)."""
    with lock:
        stats.failed += 1
        stats.failures.append((sr, reason))
    log.error("  %10s  FAIL: %s", sr, reason)


def _log_summary(stats: SyncStats) -> None:
    """Log the run summary and the full failure / gap lists."""
    log.info(
        "Sync complete: %d checked, %d synced, %d skipped, %d no-xml, "
        "%d repealed, %d language gaps, %d failed, %d files downloaded",
        stats.total, stats.synced, stats.skipped, stats.no_xml,
        stats.repealed, stats.lang_gaps, stats.failed, stats.downloads,
    )
    for sr, reason in stats.failures:
        log.error("  failed %10s: %s", sr, reason)


def _mark_repealed(
    client: httpx.Client,
    sr: str,
    store_root: Path,
    sync_index: dict,
    stats: SyncStats,
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
        log.warning("  %10s  dropped from index but has no law_uri — cannot classify", sr)
        return

    status_code, repealed_date = fetch_law_status(client, law_uri)
    if status_code == "0":
        # Still in force per Fedlex but absent from our in-force index.  Not
        # expected on a full sync; stay conservative and leave it untouched,
        # but say so — this is a sign the index query is missing acts.
        log.warning(
            "  %10s  absent from in-force index but Fedlex still reports it in force — left untouched",
            sr,
        )
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
    else:
        log.warning("  %10s  repealed but has no meta.json on disk", sr)

    stats.repealed += 1
    progress("  %10s  REPEALED (%s) — kept, excluded from index",
             sr, repealed_date or "?")


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
) -> None:
    """Sync a single federal law in all requested languages.

    What is on disk is the truth: every language Fedlex offers for a version
    is downloaded if its file is missing (or, for the latest version, if the
    applicability date changed).  This makes the sync self-healing — a gap
    Fedlex fills later, or a file lost on disk, is picked up on the next run.

    Never raises for per-act problems: failures are counted in ``stats``
    and logged, and the act's index entry is left untouched so the next run
    retries it.
    """
    sr = entry.sr

    # Early exit if limit already reached
    with index_lock:
        if limit is not None and stats.synced >= limit:
            return

    try:
        versions = fetch_all_xml_urls(client, entry.law_uri, langs=langs)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        _record_failure(stats, index_lock, sr, f"SPARQL version query: {exc!r}")
        return
    if mode == "latest" and versions:
        versions = versions[:1]

    if not versions:
        with index_lock:
            stats.no_xml += 1
        log.warning("  %10s  SKIP (no XML offered upstream in %s)", sr, "+".join(langs))
        return

    with index_lock:
        existing = sync_index.get(sr, {})
    existing_history = set(existing.get("synced_history", []))
    existing_latest = existing.get("latest_date")
    latest_date = versions[0].date
    latest_changed = existing_latest != latest_date

    # Plan: which (lang, date) files to fetch, which languages are not offered.
    plan: list[tuple[str, str | None, str]] = []  # (lang, date_dir, url)
    gaps: list[tuple[str, str]] = []  # (version date, lang)
    for i, ver in enumerate(versions):
        date_dir = None if i == 0 else ver.date
        for lang in langs:
            url = ver.urls.get(lang)
            if url is None:
                gaps.append((ver.date, lang))
                continue
            needed = (
                force
                or (i == 0 and latest_changed)
                or not xml_present(store_root, sr, lang, date=date_dir)
            )
            if needed:
                plan.append((lang, date_dir, url))
    history_dates = [v.date for v in versions[1:]]
    new_history = [d for d in history_dates if d not in existing_history]
    index_stale = (
        latest_changed
        or bool(new_history)
        or not set(langs) <= set(existing.get("langs", []))
        or existing.get("status") != "in_force"
    )

    if not plan and not index_stale and not force:
        with index_lock:
            stats.skipped += 1
        return

    # Reserve a slot atomically before the expensive downloads
    with index_lock:
        if limit is not None and stats.synced >= limit:
            return
        stats.synced += 1
        reserved_position = stats.synced

    # Phase 1: download + validate everything for this act into memory, so a
    # failure cannot leave the act half-updated on disk.
    downloaded: list[tuple[str, str | None, bytes]] = []
    try:
        for lang, date_dir, url in plan:
            downloaded.append((lang, date_dir, _download_xml(client, url)))
    except DownloadError as exc:
        with index_lock:
            stats.synced -= 1
        _record_failure(stats, index_lock, sr, str(exc))
        return

    # Phase 2: write files, meta and index entry.
    try:
        for lang, date_dir, xml_bytes in downloaded:
            write_xml(store_root, sr, lang, xml_bytes, date=date_dir)
        meta = LawMeta(
            sr=sr,
            titles=entry.titles,
            abbreviations=entry.abbreviations,
            law_uri=entry.law_uri,
            versions=[VersionMeta(date=v.date, urls=v.urls) for v in versions],
        )
        write_meta(store_root, meta)
    except OSError as exc:
        with index_lock:
            stats.synced -= 1
        _record_failure(stats, index_lock, sr, f"write: {exc!r}")
        return

    with index_lock:
        sync_index[sr] = {
            "law_uri": entry.law_uri,
            "titles": entry.titles,
            "abbreviations": entry.abbreviations,
            "latest_date": latest_date,
            "synced_history": sorted(existing_history | set(history_dates), reverse=True),
            "langs": list(langs),
            "status": "in_force",
            "repealed_date": None,
            "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        stats.downloads += len(downloaded)
        stats.lang_gaps += len(gaps)
        stats.gaps.extend((sr, date, lang) for date, lang in gaps)

    for date, lang in gaps:
        log.warning("  %10s  version %s: no %s XML offered upstream", sr, date, lang)
    progress(
        "  [%d/%s] %10s  OK (%d files, %d new/%d total versions)",
        reserved_position, limit or "∞", sr, len(downloaded),
        len(new_history), len(versions),
    )


def _download_xml(client: httpx.Client, url: str) -> bytes:
    """Download one AKN XML file from the Fedlex filestore.

    Retries transient failures (transport errors, 429/5xx) a few times, then
    validates that the payload is well-formed XML with an ``akomaNtoso``
    root.  Raises :class:`DownloadError` on any unrecoverable problem so the
    caller can fail the act loudly instead of silently skipping the file.
    """
    assert url.startswith("https://"), f"unexpected download URL: {url}"
    last_error = ""
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            resp = client.get(url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code == 200:
                return _validate_akn(resp.content, url)
            last_error = f"HTTP {resp.status_code}"
            if resp.status_code not in _RETRY_STATUS:
                break
        if attempt < DOWNLOAD_ATTEMPTS:
            log.warning("  retry %d/%d for %s (%s)", attempt, DOWNLOAD_ATTEMPTS, url, last_error)
            time.sleep(DOWNLOAD_BACKOFF_SECONDS * attempt)
    raise DownloadError(f"download failed: {url} ({last_error})")


def _validate_akn(content: bytes, url: str) -> bytes:
    """Return ``content`` if it is well-formed AKN XML, else raise DownloadError."""
    if not content.strip():
        raise DownloadError(f"empty response: {url}")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise DownloadError(f"not well-formed XML: {url} ({exc})") from exc
    if root.tag != AKN_ROOT_TAG:
        raise DownloadError(f"not an akomaNtoso document: {url} (root={root.tag})")
    return content
