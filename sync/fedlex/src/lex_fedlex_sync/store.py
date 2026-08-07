"""Local store for synced federal law AKN XML documents.

Storage layout (trilingual with history):
    {store_root}/
    ├── ch/
    │   ├── sync_index.json         # Sync tracking
    │   ├── {sr}/
    │   │   ├── de.xml              # Latest version (German)
    │   │   ├── fr.xml              # Latest version (French)
    │   │   ├── it.xml              # Latest version (Italian)
    │   │   ├── meta.json           # Law metadata + version list
    │   │   └── {date}/             # Historical versions
    │   │       ├── de.xml
    │   │       ├── fr.xml
    │   │       └── it.xml
    │   └── ...
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VersionMeta:
    """A version entry in meta.json with per-language download URLs."""

    date: str
    urls: dict[str, str] = field(default_factory=dict)


@dataclass
class LawMeta:
    """Per-law metadata stored as meta.json."""

    sr: str
    titles: dict[str, str]
    abbreviations: dict[str, str]
    law_uri: str
    versions: list[VersionMeta] = field(default_factory=list)
    # Lifecycle status: "in_force" or "repealed".  A repealed act keeps its
    # files and stable ELI URI (URI-MODEL.md Principle 6) but is excluded from
    # the search index.  repealed_date is the ISO date it left force
    # (Fedlex max jolux:dateNoLongerInForce), or None while in force.
    status: str = "in_force"
    repealed_date: str | None = None

    @property
    def title(self) -> str:
        """Primary language title (first available)."""
        if not self.titles:
            return ""
        return next(iter(self.titles.values()))

    @property
    def abbreviation(self) -> str:
        """Primary language abbreviation (first available)."""
        if not self.abbreviations:
            return ""
        return next(iter(self.abbreviations.values()))


def write_xml(
    store_root: Path,
    sr: str,
    lang: str,
    xml_bytes: bytes,
    *,
    date: str | None = None,
) -> Path:
    """Write XML to the store.

    If date is None, writes as the latest version ({sr}/{lang}.xml).
    If date is given, writes as a historical version ({sr}/{date}/{lang}.xml).

    Returns the path written.
    """
    if date is not None:
        xml_path = store_root / "ch" / sr / date / f"{lang}.xml"
    else:
        xml_path = store_root / "ch" / sr / f"{lang}.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_bytes(xml_bytes)
    return xml_path


def write_meta(store_root: Path, meta: LawMeta) -> Path:
    """Write per-law metadata to meta.json."""
    meta_path = store_root / "ch" / meta.sr / "meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "sr": meta.sr,
        "titles": meta.titles,
        "abbreviations": meta.abbreviations,
        "law_uri": meta.law_uri,
        "status": meta.status,
        "repealed_date": meta.repealed_date,
        "versions": [
            {"date": v.date, "urls": v.urls} for v in meta.versions
        ],
    }
    meta_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta_path


def read_meta(store_root: Path, sr: str) -> LawMeta | None:
    """Read per-law metadata, or None if not synced.

    Handles backward compatibility with old single-language meta format.
    """
    meta_path = store_root / "ch" / sr / "meta.json"
    if not meta_path.exists():
        return None
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return _dict_to_meta(data)


def _dict_to_meta(data: dict) -> LawMeta:
    """Convert a meta.json dict to LawMeta, handling old and new formats."""
    # New format: titles/abbreviations are dicts
    if "titles" in data:
        titles = data["titles"]
        abbreviations = data.get("abbreviations", {})
    else:
        # Old format: single title/abbreviation strings (assumed German)
        titles = {"de": data.get("title", "")}
        abbr = data.get("abbreviation", "")
        abbreviations = {"de": abbr} if abbr else {}

    # Versions: new format has urls dict, old format has single url string
    versions = []
    for v in data.get("versions", []):
        if "urls" in v:
            versions.append(VersionMeta(date=v["date"], urls=v["urls"]))
        elif "url" in v:
            versions.append(VersionMeta(date=v["date"], urls={"de": v["url"]}))
        else:
            versions.append(VersionMeta(date=v["date"]))

    return LawMeta(
        sr=data["sr"],
        titles=titles,
        abbreviations=abbreviations,
        law_uri=data["law_uri"],
        versions=versions,
        # Absent status (pre-lifecycle meta.json) means the act was synced
        # while in force — default accordingly.
        status=data.get("status", "in_force"),
        repealed_date=data.get("repealed_date"),
    )


def write_index(store_root: Path, index: dict[str, dict]) -> None:
    """Write the sync tracking index."""
    index_path = store_root / "ch" / "sync_index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_index(store_root: Path) -> dict[str, dict]:
    """Read the sync tracking index, or empty dict if none exists.

    Handles backward compatibility: old ``index.json`` filename
    (renamed to ``sync_index.json``).
    """
    index_path = store_root / "ch" / "sync_index.json"
    if not index_path.exists():
        # Fall back to old filename
        index_path = store_root / "ch" / "index.json"
    if not index_path.exists():
        return {}
    return json.loads(index_path.read_text(encoding="utf-8"))


def read_xml(store_root: Path, sr: str, lang: str) -> bytes | None:
    """Read the latest version XML, or None if not present."""
    xml_path = store_root / "ch" / sr / f"{lang}.xml"
    if not xml_path.exists():
        return None
    return xml_path.read_bytes()


def count_laws(store_root: Path) -> int:
    """Count synced laws (directories under ch/ with any language XML)."""
    ch_dir = store_root / "ch"
    if not ch_dir.is_dir():
        return 0
    return sum(
        1 for d in ch_dir.iterdir()
        if d.is_dir() and any(
            p.suffix == ".xml" and p.stem in ("de", "fr", "it")
            for p in d.iterdir()
        )
    )
