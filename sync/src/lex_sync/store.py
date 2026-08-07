"""Local AKN store for synced cantonal law documents.

Storage layout:
    {store_root}/
    ├── vs/
    │   ├── {sysno}/
    │   │   ├── de.xml            # AKN XML (German, latest)
    │   │   ├── fr.xml            # AKN XML (French, latest)
    │   │   ├── meta.json         # Sidecar metadata (with versions list)
    │   │   ├── source.json       # Current version API response
    │   │   ├── {date}/           # Version by in-force date
    │   │   │   ├── de.xml
    │   │   │   ├── fr.xml
    │   │   │   └── source.json
    │   │   └── ...
    │   └── ...
    └── sync_index.json           # Sync index (version tracking)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from lex_sync.convert import (
    ConvertResult,
    DocumentMeta,
    parse_abrogated_date,
    serialize,
)


@dataclass(frozen=True)
class VersionInfo:
    """A synced version entry for meta.json."""

    date: str
    version_id: int


def write_document(
    store_root: Path,
    result: ConvertResult,
    lang: str,
    *,
    date: str | None = None,
) -> Path:
    """Write a single language variant to the local store.

    Args:
        store_root: Root directory for the AKN store.
        result: Converted document with XML and metadata.
        lang: Language code (used for XML filename).
        date: Optional ISO date for versioned storage.  When set,
            writes to ``vs/{sysno}/{date}/{lang}.xml``.

    Returns the directory where the document was written.
    """
    sysno = result.meta.systematic_number
    if date:
        doc_dir = store_root / "vs" / sysno / date
    else:
        doc_dir = store_root / "vs" / sysno
    doc_dir.mkdir(parents=True, exist_ok=True)

    # AKN XML
    xml_path = doc_dir / f"{lang}.xml"
    xml_path.write_bytes(serialize(result.xml))

    return doc_dir


def write_meta(
    store_root: Path,
    meta: DocumentMeta,
    *,
    versions: list[VersionInfo] | None = None,
) -> None:
    """Write sidecar metadata for a document.

    Args:
        store_root: Root directory for the AKN store.
        meta: Document metadata from conversion.
        versions: Optional list of synced versions (newest-first).
    """
    doc_dir = store_root / "vs" / meta.systematic_number
    doc_dir.mkdir(parents=True, exist_ok=True)
    meta_path = doc_dir / "meta.json"

    data = _meta_to_dict(meta)
    if versions is not None:
        data["versions"] = [asdict(v) for v in versions]

    # Uniform lifecycle status mirroring the federal meta.json, so the index
    # builder and viewer read one field across jurisdictions.  Derived from the
    # canton's abrogation flag; abrogated_scheduled (future repeal) is still in
    # force today and is NOT treated as repealed.
    if meta.abrogated:
        data["status"] = "repealed"
        data["repealed_date"] = parse_abrogated_date(meta.abrogated_dates_str)
    else:
        data["status"] = "in_force"
        data["repealed_date"] = None

    meta_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_source(
    store_root: Path,
    sysno: str,
    api_response: dict,
    *,
    date: str | None = None,
) -> None:
    """Write the original API JSON response for reference.

    Args:
        store_root: Root directory for the AKN store.
        sysno: Systematic number.
        api_response: Raw API response dict.
        date: Optional ISO date for versioned storage.
    """
    if date:
        doc_dir = store_root / "vs" / sysno / date
    else:
        doc_dir = store_root / "vs" / sysno
    doc_dir.mkdir(parents=True, exist_ok=True)
    source_path = doc_dir / "source.json"
    source_path.write_text(
        json.dumps(api_response, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_meta(store_root: Path, sysno: str) -> DocumentMeta | None:
    """Read sidecar metadata for a document, or None if not synced."""
    meta_path = store_root / "vs" / sysno / "meta.json"
    if not meta_path.exists():
        return None
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return _dict_to_meta(data)


def read_meta_versions(store_root: Path, sysno: str) -> list[VersionInfo]:
    """Read the versions list from meta.json, or empty list if absent."""
    meta_path = store_root / "vs" / sysno / "meta.json"
    if not meta_path.exists():
        return []
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return [
        VersionInfo(**v) for v in data.get("versions", [])
    ]


def write_index(store_root: Path, index: dict) -> None:
    """Write the sync index (tracks what's been synced and version hashes)."""
    index_path = store_root / "sync_index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_index(store_root: Path) -> dict:
    """Read the sync index, or empty dict if none exists.

    Handles backward compatibility:
    - Old ``index.json`` filename (renamed to ``sync_index.json``)
    - Old entries keyed by ``sysno:lang`` (collapsed to bare ``sysno``)
    """
    # Prefer new filename, fall back to old
    index_path = store_root / "sync_index.json"
    if not index_path.exists():
        index_path = store_root / "index.json"
    if not index_path.exists():
        return {}
    raw = json.loads(index_path.read_text(encoding="utf-8"))

    # Migrate old keys: "175.1:de" → "175.1" (drop lang suffix)
    migrated: dict = {}
    for key, value in raw.items():
        bare_key = key.split(":")[0]
        # Keep the first (or only) entry per sysno
        if bare_key not in migrated:
            migrated[bare_key] = value
    return migrated


def _meta_to_dict(meta: DocumentMeta) -> dict:
    """Serialize DocumentMeta to a JSON-safe dict."""
    return asdict(meta)


def _dict_to_meta(data: dict) -> DocumentMeta:
    """Deserialize DocumentMeta from a dict."""
    from lex_sync.convert import ChangeDocument, Material, VersionRecord

    return DocumentMeta(
        systematic_number=data["systematic_number"],
        law_type=data["law_type"],
        law_type_id=data.get("law_type_id"),
        pdf_link=data["pdf_link"],
        pdf_link_tol=data["pdf_link_tol"],
        pdf_link_tol_size=data["pdf_link_tol_size"],
        available_languages=data["available_languages"],
        abrogated=data["abrogated"],
        abrogated_scheduled=data["abrogated_scheduled"],
        abrogated_dates_str=data["abrogated_dates_str"],
        old_versions=[
            VersionRecord(**v) for v in data.get("old_versions", [])
        ],
        change_documents=[
            ChangeDocument(**cd) for cd in data.get("change_documents", [])
        ],
        materials=[
            Material(**m) for m in data.get("materials", [])
        ],
    )
