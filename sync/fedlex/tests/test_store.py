"""Tests for lex_fedlex_sync.store module."""

from __future__ import annotations

import json
from pathlib import Path

from lex_fedlex_sync.store import (
    LawMeta,
    VersionMeta,
    count_laws,
    read_index,
    read_meta,
    read_xml,
    write_index,
    write_meta,
    write_xml,
)


SAMPLE_XML = b'<?xml version="1.0"?><akomaNtoso/>'


def test_write_xml_latest(tmp_path: Path) -> None:
    path = write_xml(tmp_path, "101", "de", SAMPLE_XML)
    assert path == tmp_path / "ch" / "101" / "de.xml"
    assert path.read_bytes() == SAMPLE_XML


def test_write_xml_latest_multilang(tmp_path: Path) -> None:
    """All 3 languages written side by side."""
    for lang in ("de", "fr", "it"):
        write_xml(tmp_path, "101", lang, SAMPLE_XML)
    assert (tmp_path / "ch" / "101" / "de.xml").exists()
    assert (tmp_path / "ch" / "101" / "fr.xml").exists()
    assert (tmp_path / "ch" / "101" / "it.xml").exists()


def test_write_xml_historical(tmp_path: Path) -> None:
    path = write_xml(tmp_path, "101", "de", SAMPLE_XML, date="2024-03-03")
    assert path == tmp_path / "ch" / "101" / "2024-03-03" / "de.xml"
    assert path.read_bytes() == SAMPLE_XML


def test_write_xml_historical_multilang(tmp_path: Path) -> None:
    for lang in ("de", "fr", "it"):
        write_xml(tmp_path, "101", lang, SAMPLE_XML, date="2024-03-03")
    assert (tmp_path / "ch" / "101" / "2024-03-03" / "de.xml").exists()
    assert (tmp_path / "ch" / "101" / "2024-03-03" / "fr.xml").exists()
    assert (tmp_path / "ch" / "101" / "2024-03-03" / "it.xml").exists()


def test_write_xml_sr_with_dots(tmp_path: Path) -> None:
    """SR numbers with dots (e.g. 814.20) used as directory names."""
    path = write_xml(tmp_path, "814.20", "de", SAMPLE_XML)
    assert path == tmp_path / "ch" / "814.20" / "de.xml"
    assert path.exists()


def test_meta_roundtrip(tmp_path: Path) -> None:
    meta = LawMeta(
        sr="101",
        titles={"de": "Bundesverfassung", "fr": "Constitution fédérale", "it": "Costituzione federale"},
        abbreviations={"de": "BV", "fr": "Cst.", "it": "Cost."},
        law_uri="https://fedlex.data.admin.ch/eli/cc/1999/404",
        versions=[
            VersionMeta(
                date="2024-03-03",
                urls={"de": "https://example.com/v1-de.xml", "fr": "https://example.com/v1-fr.xml"},
            ),
            VersionMeta(
                date="2023-01-01",
                urls={"de": "https://example.com/v2-de.xml"},
            ),
        ],
    )
    write_meta(tmp_path, meta)

    loaded = read_meta(tmp_path, "101")
    assert loaded is not None
    assert loaded.sr == "101"
    assert loaded.titles == {"de": "Bundesverfassung", "fr": "Constitution fédérale", "it": "Costituzione federale"}
    assert loaded.abbreviations == {"de": "BV", "fr": "Cst.", "it": "Cost."}
    assert loaded.law_uri == "https://fedlex.data.admin.ch/eli/cc/1999/404"
    assert len(loaded.versions) == 2
    assert loaded.versions[0].date == "2024-03-03"
    assert loaded.versions[0].urls["de"] == "https://example.com/v1-de.xml"
    assert loaded.versions[0].urls["fr"] == "https://example.com/v1-fr.xml"
    assert loaded.versions[1].date == "2023-01-01"

    # Backward-compat properties
    assert loaded.title == "Bundesverfassung"
    assert loaded.abbreviation == "BV"


def test_meta_backward_compat_old_format(tmp_path: Path) -> None:
    """Old meta.json with single title/abbreviation strings."""
    meta_dir = tmp_path / "ch" / "101"
    meta_dir.mkdir(parents=True)
    old_data = {
        "sr": "101",
        "title": "Bundesverfassung",
        "abbreviation": "BV",
        "law_uri": "https://fedlex.data.admin.ch/eli/cc/1999/404",
        "versions": [
            {"date": "2024-03-03", "url": "https://example.com/v1.xml"},
        ],
    }
    (meta_dir / "meta.json").write_text(json.dumps(old_data), encoding="utf-8")

    loaded = read_meta(tmp_path, "101")
    assert loaded is not None
    assert loaded.titles == {"de": "Bundesverfassung"}
    assert loaded.abbreviations == {"de": "BV"}
    assert loaded.versions[0].urls == {"de": "https://example.com/v1.xml"}
    # Properties still work
    assert loaded.title == "Bundesverfassung"
    assert loaded.abbreviation == "BV"


def test_read_meta_missing(tmp_path: Path) -> None:
    assert read_meta(tmp_path, "999") is None


def test_index_roundtrip(tmp_path: Path) -> None:
    idx = {
        "101": {
            "law_uri": "https://example.com/101",
            "latest_date": "2024-03-03",
            "synced_history": ["2023-01-01"],
            "langs": ["de", "fr", "it"],
            "synced_at": "2026-01-01T00:00:00",
        },
    }
    write_index(tmp_path, idx)

    loaded = read_index(tmp_path)
    assert loaded == idx


def test_index_backward_compat_old_filename(tmp_path: Path) -> None:
    """Old index.json still readable."""
    ch_dir = tmp_path / "ch"
    ch_dir.mkdir(parents=True)
    idx = {"101": {"law_uri": "x", "lang": "de"}}
    (ch_dir / "index.json").write_text(json.dumps(idx), encoding="utf-8")

    loaded = read_index(tmp_path)
    assert loaded == idx


def test_read_index_missing(tmp_path: Path) -> None:
    assert read_index(tmp_path) == {}


def test_read_xml(tmp_path: Path) -> None:
    write_xml(tmp_path, "101", "de", SAMPLE_XML)
    assert read_xml(tmp_path, "101", "de") == SAMPLE_XML


def test_read_xml_other_lang(tmp_path: Path) -> None:
    write_xml(tmp_path, "101", "fr", SAMPLE_XML)
    assert read_xml(tmp_path, "101", "fr") == SAMPLE_XML
    assert read_xml(tmp_path, "101", "de") is None


def test_read_xml_missing(tmp_path: Path) -> None:
    assert read_xml(tmp_path, "101", "de") is None


def test_count_laws(tmp_path: Path) -> None:
    assert count_laws(tmp_path) == 0

    write_xml(tmp_path, "101", "de", SAMPLE_XML)
    write_xml(tmp_path, "210", "fr", SAMPLE_XML)
    assert count_laws(tmp_path) == 2


def test_count_laws_any_lang(tmp_path: Path) -> None:
    """Count includes laws with only fr or it XML."""
    write_xml(tmp_path, "101", "it", SAMPLE_XML)
    assert count_laws(tmp_path) == 1


def test_count_laws_ignores_index(tmp_path: Path) -> None:
    """sync_index.json should not be counted as a law."""
    write_index(tmp_path, {"101": {}})
    assert count_laws(tmp_path) == 0


def test_write_overwrite(tmp_path: Path) -> None:
    """Writing the same SR twice overwrites cleanly."""
    write_xml(tmp_path, "101", "de", b"<old/>")
    write_xml(tmp_path, "101", "de", SAMPLE_XML)
    assert read_xml(tmp_path, "101", "de") == SAMPLE_XML
