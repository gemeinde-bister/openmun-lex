"""Sync engine: fetch, convert, and store cantonal law documents.

Single-pass bilingual sync: each document is fetched once from lex.vs.ch
and converted to both German and French AKN XML.  The original API JSON
is stored as ``source.json`` for reference and re-conversion.

Version sync: for each law, all historical versions listed in
``old_versions[]`` are fetched and stored in date-stamped subdirectories.

Robustness rules:

- A law the index lists but the document endpoint does not serve, a
  payload the converter cannot handle, or an exhausted retry is a
  **failure** for that law: nothing of it is written and its index entry is
  left untouched, so the next run retries it.
- A language lex.vs.ch does not offer for a version is an **upstream gap**:
  counted, logged as a warning, recorded in ``stats.gaps``; the version is
  still stored in the languages that exist.
- Converter warnings (unexpected structure) are counted and logged per law.
- Everything for a law is fetched and converted first, then written in one
  go, so a mid-way failure cannot leave the law half-updated.
- The sync index is always persisted, even if a later step raises.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from lex_sync.api import (
    LawEntry,
    fetch_document_json,
    fetch_index,
    fetch_version_json,
    make_client,
)
from lex_sync.convert import (
    ConvertResult,
    convert_document,
    parse_abrogated_date,
    parse_in_force_date,
)
from lex_sync.runlog import log, progress
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
    lang_gaps: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    # (sysno, version date, lang) for every requested language lex.vs.ch
    # does not offer for that version — upstream gaps, not failures.
    gaps: list[tuple[str, str, str]] = field(default_factory=list)


class _LawFailure(Exception):
    """A per-law problem that fails the law without aborting the run."""


def sync_all(
    store_root: Path,
    *,
    active_only: bool = True,
    filter_pattern: str | None = None,
    force: bool = False,
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
        langs: Languages to produce (default: de, fr).

    Returns:
        SyncStats with counts and failure details.
    """
    assert len(langs) >= 1, "At least one language required"
    stats = SyncStats()
    primary_lang = langs[0]
    lang_label = "+".join(langs)

    with make_client() as client:
        # Fetch index in primary language
        log.info("Fetching index from lex.vs.ch (%s)...", primary_lang)
        entries = fetch_index(client, lang=primary_lang)
        log.info("Found %d laws in index", len(entries))
        if not entries:
            raise RuntimeError("lex.vs.ch index returned no laws")

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
        log.info("Syncing %d laws (%s%s)...", stats.total, lang_label,
                 ", force" if force else "")

        # Load existing index for incremental check
        sync_index = read_index(store_root) if not force else {}

        try:
            for i, entry in enumerate(entries, 1):
                _sync_one(
                    client, entry, store_root, langs,
                    sync_index, stats, force, i,
                )

            # Mark laws that dropped out of the active set as repealed.  Keep
            # their files + stable ELI URI (URI-MODEL.md Principle 6); they are
            # excluded from search at index-build time.  Only on a full
            # active-only sync — a filtered or forced run isn't authoritative
            # about the active set.
            if active_only and filter_pattern is None and not force:
                active_srs = {e.systematic_number for e in entries}
                dropped = [s for s in list(sync_index) if s not in active_srs]
                if dropped:
                    log.info("Checking %d law(s) no longer active...", len(dropped))
                for sysno in dropped:
                    try:
                        _mark_repealed(
                            client, sysno, store_root, sync_index, stats, langs,
                        )
                    except Exception as exc:  # noqa: BLE001 — one law must not abort the run
                        _record_failure(stats, sysno, f"repeal-check: {exc!r}")
        finally:
            # Always persist what was completed — entries are only added once
            # a law is fully written, so a partial index is consistent.
            write_index(store_root, sync_index)

    _log_summary(stats)
    return stats


def _record_failure(stats: SyncStats, sysno: str, reason: str) -> None:
    """Count and log a per-law failure."""
    stats.failed += 1
    stats.failures.append((sysno, reason))
    log.error("%10s  FAIL: %s", sysno, reason)


def _log_summary(stats: SyncStats) -> None:
    """Log the run summary and the full failure list."""
    log.info(
        "Sync complete: %d checked, %d synced (%d versions), %d skipped, "
        "%d repealed, %d language gaps, %d warnings, %d failed",
        stats.total, stats.synced, stats.versions_synced, stats.skipped,
        stats.repealed, stats.lang_gaps, stats.warnings, stats.failed,
    )
    for sysno, reason in stats.failures:
        log.error("  failed %10s: %s", sysno, reason)


def _mark_repealed(
    client: httpx.Client,
    sysno: str,
    store_root: Path,
    sync_index: dict,
    stats: SyncStats,
    langs: tuple[str, ...],
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
            _record_failure(stats, sysno, f"repeal-mark: {exc!r}")
            return
        abrogated = first_result.meta.abrogated
        status = "repealed" if abrogated else "in_force"
        repealed_date = (
            parse_abrogated_date(first_result.meta.abrogated_dates_str)
            if abrogated else None
        )
        if not abrogated:
            log.warning(
                "%10s  dropped from the active index but upstream does not flag it "
                "abrogated — kept in force",
                sysno,
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
        else:
            log.warning("%10s  gone upstream (404) and has no meta.json on disk", sysno)
        status = "repealed"
        repealed_date = None

    entry["status"] = status
    entry["repealed_date"] = repealed_date
    entry["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    sync_index[sysno] = entry

    if status == "repealed":
        stats.repealed += 1
        progress("%10s  REPEALED (%s) — kept, excluded from index",
                 sysno, repealed_date or "?")


def _available_langs(response: dict) -> set[str]:
    """Languages the API reports for the selected version of a response."""
    sv = response.get("text_of_law", {}).get("selected_version", {})
    return {
        al["language"]["iso639_1_code"]
        for al in sv.get("available_languages", [])
        if al.get("language", {}).get("iso639_1_code")
    }


def _in_force_date(sv: dict, fallback: str) -> str:
    """In-force date of a version from its dates string, else ``fallback``."""
    try:
        return parse_in_force_date(sv.get("version_dates_str", ""))
    except ValueError:
        return fallback


def _sync_one(
    client: httpx.Client,
    entry: LawEntry,
    store_root: Path,
    langs: tuple[str, ...],
    sync_index: dict,
    stats: SyncStats,
    force: bool,
    position: int,
) -> None:
    """Sync a single law document in all requested languages, including versions.

    Never raises for per-law problems: failures are counted in ``stats``
    and logged, and the law's index entry is left untouched so the next run
    retries it.
    """
    sysno = entry.systematic_number
    prefix = f"[{position}/{stats.total}]"

    try:
        _sync_one_inner(client, entry, store_root, langs, sync_index, stats, force, prefix)
    except _LawFailure as exc:
        _record_failure(stats, sysno, str(exc))
    except Exception as exc:  # noqa: BLE001 — never abort the run for one law
        _record_failure(stats, sysno, f"{type(exc).__name__}: {exc}")


def _sync_one_inner(
    client: httpx.Client,
    entry: LawEntry,
    store_root: Path,
    langs: tuple[str, ...],
    sync_index: dict,
    stats: SyncStats,
    force: bool,
    prefix: str,
) -> None:
    sysno = entry.systematic_number

    # Fetch JSON once — response contains all languages
    api_response = fetch_document_json(client, sysno, lang=langs[0])
    if api_response is None:
        raise _LawFailure(
            "listed in the index but show_as_json returned 404 "
            "(upstream inconsistency)"
        )

    tol = api_response.get("text_of_law", {})
    version_uid = tol.get("version_uid", "")
    sv = tol.get("selected_version", {})
    current_version_id = sv.get("id")
    if not version_uid or current_version_id is None:
        raise _LawFailure("payload has no version_uid / selected_version.id")

    # Build list of old versions from the response (only those with structured content)
    old_versions_raw = tol.get("old_versions", [])
    old_version_ids = {
        ov["id"] for ov in old_versions_raw
        if ov.get("structured_document_id") is not None
    }
    all_version_ids = old_version_ids | {current_version_id}

    # Check what's already synced
    existing_entry = sync_index.get(sysno, {})
    existing_uid = existing_entry.get("version_uid", "")
    existing_versions = set(existing_entry.get("versions_synced", []))
    existing_langs = set(existing_entry.get("langs", []))

    # Determine if we need to do anything
    uid_unchanged = existing_uid == version_uid
    all_versions_synced = all_version_ids <= existing_versions
    langs_match = set(langs) <= existing_langs

    if not force and uid_unchanged and all_versions_synced and langs_match:
        progress("%s %10s  SKIP (unchanged)", prefix, sysno)
        stats.skipped += 1
        return

    all_warnings: list[str] = []
    gaps: list[tuple[str, str]] = []  # (version date, lang)
    version_infos: list[VersionInfo] = []
    n_versions_synced = 0
    # Deferred writes: (result, lang, date) and (response, date)
    pending_docs: list[tuple[ConvertResult, str, str | None]] = []
    pending_sources: list[tuple[dict, str | None]] = []

    # --- Current version ---
    current_date = _in_force_date(sv, tol.get("publication_enactment", ""))
    current_needs_sync = (
        force or not uid_unchanged or not langs_match
        or current_version_id not in existing_versions
    )

    first_result: ConvertResult | None = None
    if current_needs_sync:
        available = _available_langs(api_response)
        for lang in langs:
            if available and lang not in available:
                gaps.append((current_date or "current", lang))
                continue
            result = convert_document(api_response, lang=lang, in_force_date=current_date)
            all_warnings.extend(result.warnings)
            pending_docs.append((result, lang, None))
            if current_date:
                pending_docs.append((result, lang, current_date))
            if first_result is None:
                first_result = result
        if first_result is None:
            raise _LawFailure(
                f"none of the requested languages ({'+'.join(langs)}) is "
                f"available upstream ({'+'.join(sorted(available)) or 'none'})"
            )
        pending_sources.append((api_response, None))
        if current_date:
            pending_sources.append((api_response, current_date))
        n_versions_synced += 1
    else:
        # Current version already synced — still need one conversion for meta
        first_result = convert_document(api_response, lang=langs[0])

    if current_date:
        version_infos.append(VersionInfo(date=current_date, version_id=current_version_id))
    else:
        all_warnings.append("Current version has no in-force date")

    # --- Old versions ---
    for ov in old_versions_raw:
        ov_id = ov["id"]

        # Skip versions without structured content (PDF-only, no JSON)
        if ov.get("structured_document_id") is None:
            continue

        if not force and ov_id in existing_versions and langs_match:
            # Already synced — just add to version_infos for meta
            ov_date = _in_force_date(ov, "")
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

        # In-force date: from the fetched version, else from the listing
        ov_date = _in_force_date(ov_sv, "") or _in_force_date(ov, "")
        if not ov_date:
            all_warnings.append(
                f"Old version {ov_id}: cannot extract date from "
                f"{ov_sv.get('version_dates_str', '')!r} or "
                f"{ov.get('version_dates_str', '')!r}"
            )
            continue

        available = _available_langs(ov_response)
        wrote_any = False
        for lang in langs:
            if available and lang not in available:
                gaps.append((ov_date, lang))
                continue
            ov_result = convert_document(ov_response, lang=lang, in_force_date=ov_date)
            all_warnings.extend(ov_result.warnings)
            pending_docs.append((ov_result, lang, ov_date))
            wrote_any = True
        if not wrote_any:
            all_warnings.append(
                f"Old version {ov_id} ({ov_date}) is available in none of the "
                f"requested languages"
            )
            continue

        pending_sources.append((ov_response, ov_date))
        version_infos.append(VersionInfo(date=ov_date, version_id=ov_id))
        n_versions_synced += 1

    # Sort versions newest-first
    version_infos.sort(key=lambda v: v.date, reverse=True)

    # --- Write phase: everything is fetched and converted; now persist ---
    assert first_result is not None
    for result, lang, date in pending_docs:
        write_document(store_root, result, lang, date=date)
    for response, date in pending_sources:
        write_source(store_root, sysno, response, date=date)
    write_meta(store_root, first_result.meta, versions=version_infos)

    # Update sync index
    all_synced_versions = existing_versions | {vi.version_id for vi in version_infos}
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

    # --- Report ---
    for w in all_warnings:
        log.warning("%10s  %s", sysno, w)
    for date, lang in gaps:
        log.warning("%10s  version %s: %s not available upstream", sysno, date, lang)
    stats.warnings += len(all_warnings)
    stats.lang_gaps += len(gaps)
    stats.gaps.extend((sysno, date, lang) for date, lang in gaps)

    parts = []
    if n_versions_synced > 0:
        parts.append(f"{n_versions_synced} version(s)")
    if all_warnings:
        parts.append(f"{len(all_warnings)} warnings")
    if gaps:
        parts.append(f"{len(gaps)} language gaps")
    status = f"OK ({', '.join(parts)})" if parts else "OK"
    progress("%s %10s  %s", prefix, sysno, status)
    stats.synced += 1
    stats.versions_synced += n_versions_synced
