"""Tests for lex_sync.store module."""

from __future__ import annotations

import json
from pathlib import Path

from lxml import etree

from lex_sync.convert import (
    AKN_NS,
    ChangeDocument,
    DocumentMeta,
    Material,
    VersionRecord,
)
from lex_sync.store import (
    VersionInfo,
    read_index,
    read_meta,
    read_meta_versions,
    write_document,
    write_index,
    write_meta,
    write_source,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _make_minimal_result():
    """Create a minimal ConvertResult for testing."""
    from lex_sync.convert import ConvertResult

    nsmap = {None: AKN_NS}
    root = etree.Element(f"{{{AKN_NS}}}akomaNtoso", nsmap=nsmap)
    etree.SubElement(root, f"{{{AKN_NS}}}act")

    meta = DocumentMeta(
        systematic_number="175.1",
        law_type="Gesetz",
        law_type_id=4,
        pdf_link="https://example.com/pdf",
        pdf_link_tol="https://example.com/tol.pdf",
        pdf_link_tol_size=12345,
        available_languages=["de", "fr"],
        abrogated=False,
        abrogated_scheduled=False,
        abrogated_dates_str="",
        old_versions=[
            VersionRecord(version_id=100, structured_document_id=200,
                          version_dates_str="von 01.01.2020"),
        ],
        change_documents=[
            ChangeDocument(id=1, number="2021-001", title="Amendment",
                           date_of_decision="01.01.2021",
                           date_of_publication="15.01.2021",
                           pdf_link="https://example.com/cd.pdf",
                           for_selected_version=True),
        ],
        materials=[
            Material(id=10, title="Related Law", url="https://example.com/m"),
        ],
    )

    return ConvertResult(xml=root, meta=meta, warnings=[])


def test_write_creates_xml(tmp_path: Path) -> None:
    result = _make_minimal_result()
    doc_dir = write_document(tmp_path, result, "de")

    assert doc_dir == tmp_path / "vs" / "175.1"
    assert (doc_dir / "de.xml").exists()


def test_write_meta_creates_file(tmp_path: Path) -> None:
    result = _make_minimal_result()
    write_meta(tmp_path, result.meta)

    assert (tmp_path / "vs" / "175.1" / "meta.json").exists()


def test_write_meta_in_force_status(tmp_path: Path) -> None:
    """A non-abrogated law gets status=in_force, repealed_date=None."""
    result = _make_minimal_result()
    write_meta(tmp_path, result.meta)

    data = json.loads(
        (tmp_path / "vs" / "175.1" / "meta.json").read_text(encoding="utf-8"))
    assert data["status"] == "in_force"
    assert data["repealed_date"] is None


def test_write_meta_repealed_status(tmp_path: Path) -> None:
    """An abrogated law gets status=repealed with the parsed repeal date."""
    import dataclasses

    result = _make_minimal_result()
    meta = dataclasses.replace(
        result.meta, abrogated=True,
        abrogated_dates_str="Aufgehoben am: 28.02.2026",
    )
    write_meta(tmp_path, meta)

    data = json.loads(
        (tmp_path / "vs" / "175.1" / "meta.json").read_text(encoding="utf-8"))
    assert data["status"] == "repealed"
    assert data["repealed_date"] == "2026-02-28"


def test_write_source_creates_file(tmp_path: Path) -> None:
    api_response = {"text_of_law": {"systematic_number": "175.1"}}
    write_source(tmp_path, "175.1", api_response)

    source_path = tmp_path / "vs" / "175.1" / "source.json"
    assert source_path.exists()
    loaded = json.loads(source_path.read_text(encoding="utf-8"))
    assert loaded == api_response


def test_write_xml_is_valid(tmp_path: Path) -> None:
    result = _make_minimal_result()
    write_document(tmp_path, result, "de")

    xml_bytes = (tmp_path / "vs" / "175.1" / "de.xml").read_bytes()
    root = etree.fromstring(xml_bytes)
    assert root.tag == f"{{{AKN_NS}}}akomaNtoso"


def test_write_meta_roundtrips(tmp_path: Path) -> None:
    result = _make_minimal_result()
    write_meta(tmp_path, result.meta)

    meta = read_meta(tmp_path, "175.1")
    assert meta is not None
    assert meta.systematic_number == "175.1"
    assert meta.law_type == "Gesetz"
    assert meta.law_type_id == 4
    assert meta.available_languages == ["de", "fr"]
    assert len(meta.old_versions) == 1
    assert meta.old_versions[0].version_id == 100
    assert len(meta.change_documents) == 1
    assert meta.change_documents[0].number == "2021-001"
    assert len(meta.materials) == 1
    assert meta.materials[0].title == "Related Law"


def test_read_meta_missing(tmp_path: Path) -> None:
    assert read_meta(tmp_path, "999.999") is None


def test_index_roundtrip(tmp_path: Path) -> None:
    idx = {
        "175.1": {"version_uid": "abc123", "synced_at": "2026-01-01T00:00:00"},
        "101.1": {"version_uid": "def456", "synced_at": "2026-01-01T00:00:00"},
    }
    write_index(tmp_path, idx)

    loaded = read_index(tmp_path)
    assert loaded == idx


def test_index_migrates_lang_scoped_keys(tmp_path: Path) -> None:
    """Old index entries with :lang suffix are collapsed to bare sysno."""
    old_idx = {
        "175.1:de": {"version_uid": "abc123", "synced_at": "2026-01-01T00:00:00"},
        "175.1:fr": {"version_uid": "abc123", "synced_at": "2026-01-02T00:00:00"},
    }
    (tmp_path / "sync_index.json").write_text(json.dumps(old_idx), encoding="utf-8")

    loaded = read_index(tmp_path)
    assert "175.1" in loaded
    assert "175.1:de" not in loaded
    assert "175.1:fr" not in loaded


def test_index_reads_old_filename(tmp_path: Path) -> None:
    """Falls back to index.json if sync_index.json doesn't exist."""
    old_idx = {
        "175.1": {"version_uid": "abc123", "synced_at": "2026-01-01T00:00:00"},
    }
    (tmp_path / "index.json").write_text(json.dumps(old_idx), encoding="utf-8")

    loaded = read_index(tmp_path)
    assert "175.1" in loaded


def test_read_index_missing(tmp_path: Path) -> None:
    assert read_index(tmp_path) == {}


def test_write_overwrite(tmp_path: Path) -> None:
    """Writing the same document twice overwrites cleanly."""
    result = _make_minimal_result()
    write_document(tmp_path, result, "de")
    write_document(tmp_path, result, "de")

    xml_bytes = (tmp_path / "vs" / "175.1" / "de.xml").read_bytes()
    root = etree.fromstring(xml_bytes)
    assert root.tag == f"{{{AKN_NS}}}akomaNtoso"


# --- Versioned storage (date parameter) ---


def test_write_document_with_date(tmp_path: Path) -> None:
    result = _make_minimal_result()
    doc_dir = write_document(tmp_path, result, "de", date="2021-05-01")

    assert doc_dir == tmp_path / "vs" / "175.1" / "2021-05-01"
    assert (doc_dir / "de.xml").exists()
    # Root should NOT be written
    assert not (tmp_path / "vs" / "175.1" / "de.xml").exists()


def test_write_source_with_date(tmp_path: Path) -> None:
    api_response = {"text_of_law": {"systematic_number": "175.1"}}
    write_source(tmp_path, "175.1", api_response, date="2021-05-01")

    source_path = tmp_path / "vs" / "175.1" / "2021-05-01" / "source.json"
    assert source_path.exists()
    loaded = json.loads(source_path.read_text(encoding="utf-8"))
    assert loaded == api_response
    # Root should NOT be written
    assert not (tmp_path / "vs" / "175.1" / "source.json").exists()


def test_write_document_root_and_date(tmp_path: Path) -> None:
    """Can write to both root and date subdir for current version."""
    result = _make_minimal_result()
    write_document(tmp_path, result, "de")
    write_document(tmp_path, result, "de", date="2023-01-01")

    assert (tmp_path / "vs" / "175.1" / "de.xml").exists()
    assert (tmp_path / "vs" / "175.1" / "2023-01-01" / "de.xml").exists()


# --- Meta with versions ---


def test_write_meta_with_versions(tmp_path: Path) -> None:
    result = _make_minimal_result()
    versions = [
        VersionInfo(date="2023-01-01", version_id=3103),
        VersionInfo(date="2021-05-01", version_id=2863),
        VersionInfo(date="2012-01-01", version_id=1664),
    ]
    write_meta(tmp_path, result.meta, versions=versions)

    meta_path = tmp_path / "vs" / "175.1" / "meta.json"
    assert meta_path.exists()
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "versions" in data
    assert len(data["versions"]) == 3
    assert data["versions"][0] == {"date": "2023-01-01", "version_id": 3103}
    assert data["versions"][2] == {"date": "2012-01-01", "version_id": 1664}


def test_write_meta_without_versions(tmp_path: Path) -> None:
    """Without versions param, no 'versions' key in JSON."""
    result = _make_minimal_result()
    write_meta(tmp_path, result.meta)

    data = json.loads(
        (tmp_path / "vs" / "175.1" / "meta.json").read_text(encoding="utf-8")
    )
    assert "versions" not in data


def test_read_meta_versions(tmp_path: Path) -> None:
    result = _make_minimal_result()
    versions = [
        VersionInfo(date="2023-01-01", version_id=3103),
        VersionInfo(date="2021-05-01", version_id=2863),
    ]
    write_meta(tmp_path, result.meta, versions=versions)

    loaded = read_meta_versions(tmp_path, "175.1")
    assert len(loaded) == 2
    assert loaded[0].date == "2023-01-01"
    assert loaded[0].version_id == 3103
    assert loaded[1].date == "2021-05-01"
    assert loaded[1].version_id == 2863


def test_read_meta_versions_missing(tmp_path: Path) -> None:
    assert read_meta_versions(tmp_path, "999.999") == []


def test_read_meta_versions_no_versions_key(tmp_path: Path) -> None:
    """Old meta.json without 'versions' key returns empty list."""
    result = _make_minimal_result()
    write_meta(tmp_path, result.meta)  # No versions param

    loaded = read_meta_versions(tmp_path, "175.1")
    assert loaded == []
