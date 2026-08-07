"""Sync engine: fetch, convert, and store cantonal law documents.

Single-pass bilingual sync: each document is fetched once from lex.vs.ch
and converted to both German and French AKN XML.  The original API JSON
is stored as ``source.json`` for reference and re-conversion.

Version sync: for each law, all historical versions listed in
``old_versions[]`` are fetched and stored in date-stamped subdirectories.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

import json

from lex_sync.api import LawEntry, fetch_document_json, fetch_index, fetch_version_json, make_client
from lex_sync.convert import convert_document, parse_abrogated_date, parse_in_force_date
from lex_sync.store import (
    VersionInfo,
    read_index,
    read_meta_versions,
    write_document,
    write_index,
    write_meta,
    write_source,
)

# Languages to produce from each API response.
SYNC_LANGS = ("de", "fr")


@dataclass
class SyncStats:
    """Aggregate statistics for a sync run."""

    total: int = 0
    synced: int = 0
    skipped: int = 0
    failed: int = 0
    warnings: int = 0
    versions_synced: int = 0
    repealed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


def sync_all(
    store_root: Path,
    *,
    active_only: bool = True,
    filter_pattern: str | None = None,
    force: bool = False,
    quiet: bool = False,
    langs: tuple[str, ...] = SYNC_LANGS,
) -> SyncStats:
    """Sync all (or filtered) laws from lex.vs.ch to the local store.

    Fetches each document once and converts to all requested languages.
    The index is always fetched in the first language (primary).

    For each law, all historical versions are also fetched and stored
    in date-stamped subdirectories.

    Args:
        store_root: Root directory for the AKN store.
        active_only: Skip abrogated laws.
        filter_pattern: Optional filter on SR number or title.
        force: Re-sync even if version_uid hasn't changed.
        quiet: Suppress per-document output.
        langs: Languages to produce (default: de, fr).

    Returns:
        SyncStats with counts and failure details.
    """
    assert len(langs) >= 1, "At least one language required"
    stats = SyncStats()
    primary_lang = langs[0]

    with make_client() as client:
        # Fetch index in primary language
        if not quiet:
            print("Fetching index...", file=sys.stderr)
        entries = fetch_index(client, lang=primary_lang)

        if active_only:
            entries = [e for e in entries if not e.abrogated]

        if filter_pattern:
            pat = filter_pattern.lower()
            entries = [
                e for e in entries
                if pat in e.systematic_number.lower()
                or pat in e.title.lower()
            ]

        stats.total = len(entries)
        if not quiet:
            lang_label = "+".join(langs)
            print(f"Syncing {stats.total} laws ({lang_label})...", file=sys.stderr)

        # Load existing index for incremental check
        sync_index = read_index(store_root) if not force else {}

        for i, entry in enumerate(entries, 1):
            _sync_one(
                client, entry, store_root, langs,
                sync_index, stats, force, quiet, i,
            )

        # Mark laws that dropped out of the active set as repealed.  Keep their
        # files + stable ELI URI (URI-MODEL.md Principle 6); they are excluded
        # from search at index-build time.  Only on a full active-only sync — a
        # filtered or forced run isn't authoritative about the active set.
        if active_only and filter_pattern is None and not force:
            active_srs = {e.systematic_number for e in entries}
            dropped = [s for s in list(sync_index) if s not in active_srs]
            if dropped and not quiet:
                print(f"Checking {len(dropped)} law(s) no longer active...",
                      file=sys.stderr)
            for sysno in dropped:
                _mark_repealed(
                    client, sysno, store_root, sync_index, stats, langs, quiet,
                )

    # Write updated sync index
    write_index(store_root, sync_index)

    return stats


def _mark_repealed(
    client: httpx.Client,
    sysno: str,
    store_root: Path,
    sync_index: dict,
    stats: SyncStats,
    langs: tuple[str, ...],
    quiet: bool,
) -> None:
    """Mark a law that left the active set as repealed; keep its files.

    Re-fetches the document (the lex.vs.ch document endpoint still serves
    abrogated laws) so meta.json carries the abrogation date, then flags it.
    If the document is fully gone (404), the existing files are kept and the
    status is flipped in place.  Idempotent: already-repealed laws are skipped.
    """
    entry = sync_index.get(sysno, {})
    if entry.get("status") == "repealed":
        return

    response = fetch_document_json(client, sysno, lang=langs[0])
    if response is not None:
        first_result = None
        try:
            for lang in langs:
                result = convert_document(response, lang=lang)
                write_document(store_root, result, lang)
                if first_result is None:
                    first_result = result
            write_source(store_root, sysno, response)
            versions = read_meta_versions(store_root, sysno)
            write_meta(store_root, first_result.meta, versions=versions)
        except Exception as exc:  # noqa: BLE001 — keep existing files on failure
            stats.failed += 1
            stats.failures.append((sysno, f"repeal-mark: {exc}"))
            return
        abrogated = first_result.meta.abrogated
        status = "repealed" if abrogated else "in_force"
        repealed_date = (
            parse_abrogated_date(first_result.meta.abrogated_dates_str)
            if abrogated else None
        )
    else:
        # 404 — fully gone upstream.  Keep existing files; flip status in place.
        meta_path = store_root / "vs" / sysno / "meta.json"
        if meta_path.exists():
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            data["status"] = "repealed"
            data["repealed_date"] = data.get("repealed_date") or \
                parse_abrogated_date(data.get("abrogated_dates_str", ""))
            meta_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        status = "repealed"
        repealed_date = None

    entry["status"] = status
    entry["repealed_date"] = repealed_date
    entry["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    sync_index[sysno] = entry

    if status == "repealed":
        stats.repealed += 1
        if not quiet:
            print(f"{sysno:>10}  REPEALED ({repealed_date or '?'}) — kept, "
                  f"excluded from index", file=sys.stderr)


def _sync_one(
    client: httpx.Client,
    entry: LawEntry,
    store_root: Path,
    langs: tuple[str, ...],
    sync_index: dict,
    stats: SyncStats,
    force: bool,
    quiet: bool,
    position: int,
) -> None:
    """Sync a single law document in all requested languages, including versions."""
    sysno = entry.systematic_number
    prefix = f"[{position}/{stats.total}]"

    try:
        # Fetch JSON once — response contains all languages
        api_response = fetch_document_json(client, sysno, lang=langs[0])
        if api_response is None:
            if not quiet:
                print(f"{prefix} {sysno:>10}  SKIP (not found)", file=sys.stderr)
            stats.skipped += 1
            return

        tol = api_response.get("text_of_law", {})
        version_uid = tol.get("version_uid", "")
        sv = tol.get("selected_version", {})
        current_version_id = sv.get("id")

        # Build list of old versions from the response (only those with structured content)
        old_versions_raw = tol.get("old_versions", [])
        old_version_ids = {ov["id"] for ov in old_versions_raw if ov.get("structured_document_id") is not None}
        all_version_ids = old_version_ids | ({current_version_id} if current_version_id else set())

        # Check what's already synced
        existing_entry = sync_index.get(sysno, {})
        existing_uid = existing_entry.get("version_uid", "")
        existing_versions = set(existing_entry.get("versions_synced", []))

        # Determine if we need to do anything
        uid_unchanged = existing_uid == version_uid
        all_versions_synced = all_version_ids <= existing_versions

        if not force and uid_unchanged and all_versions_synced:
            if not quiet:
                print(f"{prefix} {sysno:>10}  SKIP (unchanged)", file=sys.stderr)
            stats.skipped += 1
            return

        all_warnings: list[str] = []
        version_infos: list[VersionInfo] = []
        n_versions_synced = 0

        # --- Current version ---
        current_needs_sync = force or not uid_unchanged or current_version_id not in existing_versions

        if current_needs_sync:
            # Parse in-force date from current version's dates_str
            current_dates_str = sv.get("version_dates_str", "")
            try:
                current_date = parse_in_force_date(current_dates_str)
            except ValueError:
                current_date = tol.get("publication_enactment", "")

            # Convert and write each language — root (latest) + date subdir
            first_result = None
            for lang in langs:
                result = convert_document(api_response, lang=lang, in_force_date=current_date)
                all_warnings.extend(result.warnings)
                # Write to root (latest)
                write_document(store_root, result, lang)
                # Write to date subdir
                if current_date:
                    write_document(store_root, result, lang, date=current_date)
                if first_result is None:
                    first_result = result

            # Store source.json at root + date subdir
            write_source(store_root, sysno, api_response)
            if current_date:
                write_source(store_root, sysno, api_response, date=current_date)

            if current_date and current_version_id is not None:
                version_infos.append(VersionInfo(date=current_date, version_id=current_version_id))

            n_versions_synced += 1
        else:
            # Current version already synced — still need first_result for meta
            first_result = None
            for lang in langs:
                result = convert_document(api_response, lang=lang)
                if first_result is None:
                    first_result = result
                break  # Only need one for meta

            # Reconstruct current version info from dates_str
            current_dates_str = sv.get("version_dates_str", "")
            try:
                current_date = parse_in_force_date(current_dates_str)
            except ValueError:
                current_date = tol.get("publication_enactment", "")
            if current_date and current_version_id is not None:
                version_infos.append(VersionInfo(date=current_date, version_id=current_version_id))

        assert first_result is not None

        # --- Old versions ---
        for ov in old_versions_raw:
            ov_id = ov["id"]

            # Skip versions without structured content (PDF-only, no JSON)
            if ov.get("structured_document_id") is None:
                continue

            if not force and ov_id in existing_versions:
                # Already synced — just add to version_infos for meta
                ov_dates_str = ov.get("version_dates_str", "")
                try:
                    ov_date = parse_in_force_date(ov_dates_str)
                except ValueError:
                    ov_date = ""
                if ov_date:
                    version_infos.append(VersionInfo(date=ov_date, version_id=ov_id))
                continue

            # Fetch this old version
            ov_response = fetch_version_json(client, sysno, ov_id, lang=langs[0])
            if ov_response is None:
                all_warnings.append(f"Old version {ov_id} returned 404")
                continue

            # Verify the fetched version has structured content
            ov_tol = ov_response.get("text_of_law", {})
            ov_sv = ov_tol.get("selected_version", {})
            if ov_sv.get("json_content") is None:
                all_warnings.append(f"Old version {ov_id} has no structured content")
                continue

            # Extract in-force date from fetched response
            ov_dates_str = ov_sv.get("version_dates_str", "")
            try:
                ov_date = parse_in_force_date(ov_dates_str)
            except ValueError:
                # Fall back to the date from the current version's old_versions[] metadata
                ov_dates_str_meta = ov.get("version_dates_str", "")
                try:
                    ov_date = parse_in_force_date(ov_dates_str_meta)
                except ValueError:
                    all_warnings.append(
                        f"Old version {ov_id}: cannot extract date from "
                        f"{ov_dates_str!r} or {ov_dates_str_meta!r}"
                    )
                    continue

            # Convert and write to date subdir only
            for lang in langs:
                ov_result = convert_document(ov_response, lang=lang, in_force_date=ov_date)
                all_warnings.extend(ov_result.warnings)
                write_document(store_root, ov_result, lang, date=ov_date)

            write_source(store_root, sysno, ov_response, date=ov_date)
            version_infos.append(VersionInfo(date=ov_date, version_id=ov_id))
            n_versions_synced += 1

        # Sort versions newest-first
        version_infos.sort(key=lambda v: v.date, reverse=True)

        # Write meta with versions list
        write_meta(store_root, first_result.meta, versions=version_infos)

        if all_warnings:
            stats.warnings += len(all_warnings)
            if not quiet:
                for w in all_warnings:
                    print(f"  WARN: {w}", file=sys.stderr)

        # Update sync index
        all_synced_versions = existing_versions | {
            vi.version_id for vi in version_infos
        }
        _abrogated = first_result.meta.abrogated
        sync_index[sysno] = {
            "version_uid": version_uid,
            "title": entry.title,
            "langs": list(langs),
            "status": "repealed" if _abrogated else "in_force",
            "repealed_date": (
                parse_abrogated_date(first_result.meta.abrogated_dates_str)
                if _abrogated else None
            ),
            "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "versions_synced": sorted(all_synced_versions),
        }

        n_warn = len(all_warnings)
        parts = []
        if n_versions_synced > 0:
            parts.append(f"{n_versions_synced} version(s)")
        if n_warn > 0:
            parts.append(f"{n_warn} warnings")
        status = f"OK ({', '.join(parts)})" if parts else "OK"
        if not quiet:
            print(f"{prefix} {sysno:>10}  {status}", file=sys.stderr)
        stats.synced += 1
        stats.versions_synced += n_versions_synced

    except Exception as exc:
        stats.failed += 1
        stats.failures.append((sysno, str(exc)))
        if not quiet:
            print(f"{prefix} {sysno:>10}  FAIL: {exc}", file=sys.stderr)
