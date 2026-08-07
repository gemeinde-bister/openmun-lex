"""Tests for lex_akn.metadata module."""

from datetime import date

from lxml.etree import _ElementTree

from lex_akn.metadata import extract_metadata


def test_extract_metadata_basic(bv_tree: _ElementTree) -> None:
    meta = extract_metadata(bv_tree)
    assert meta.sr_number == "101"
    assert meta.country == "CH"
    assert meta.language == "de"


def test_extract_metadata_title(bv_tree: _ElementTree) -> None:
    meta = extract_metadata(bv_tree)
    assert "Bundesverfassung" in meta.title


def test_extract_metadata_uris(bv_tree: _ElementTree) -> None:
    meta = extract_metadata(bv_tree)
    assert "eli/cc/1999/404" in meta.work_uri
    assert meta.expression_uri != ""
    assert meta.manifestation_uri != ""


def test_extract_metadata_dates(bv_tree: _ElementTree) -> None:
    meta = extract_metadata(bv_tree)
    # BV was adopted 18 April 1999, in force 1 Jan 2000
    assert meta.date_document is not None
    assert meta.date_entry_in_force is not None
    assert meta.date_document.year == 1999
    assert meta.date_entry_in_force.year == 2000


def test_extract_metadata_short_title(bv_tree: _ElementTree) -> None:
    meta = extract_metadata(bv_tree)
    assert meta.short_title in ("BV", "")  # may or may not be present


def test_extract_metadata_abbreviation_federal(bv_tree: _ElementTree) -> None:
    meta = extract_metadata(bv_tree)
    assert meta.abbreviation == "BV"


# --- Cantonal (VS) metadata tests ---


def test_cantonal_metadata_basic(kv_tree: _ElementTree) -> None:
    meta = extract_metadata(kv_tree)
    assert meta.sr_number == "101.1"
    assert meta.country == "CH-VS"
    assert meta.language == "de"


def test_cantonal_metadata_title(kv_tree: _ElementTree) -> None:
    meta = extract_metadata(kv_tree)
    assert "Verfassung des Kantons Wallis" in meta.title


def test_cantonal_metadata_abbreviation(kv_tree: _ElementTree) -> None:
    meta = extract_metadata(kv_tree)
    assert meta.abbreviation == "KV"


def test_cantonal_metadata_dates(kv_tree: _ElementTree) -> None:
    meta = extract_metadata(kv_tree)
    # KV decision date 1907-03-08, enactment 1907-06-02
    assert meta.date_document is not None
    assert meta.date_document == date(1907, 3, 8)
    assert meta.date_entry_in_force is not None
    assert meta.date_entry_in_force == date(1907, 6, 2)


def test_cantonal_metadata_applicability(kv_tree: _ElementTree) -> None:
    meta = extract_metadata(kv_tree)
    # inForceSince on Expression: 2023-05-01
    assert meta.date_applicability is not None
    assert meta.date_applicability == date(2023, 5, 1)
