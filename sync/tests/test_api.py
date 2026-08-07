"""Tests for lex_sync.api module."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from lex_sync.api import (
    Category,
    LawEntry,
    _sysno_sort_key,
    fetch_categories,
    fetch_index,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def index_response() -> dict:
    with open(FIXTURES / "index_subset.json") as f:
        return json.load(f)


@pytest.fixture()
def categories_response() -> list:
    with open(FIXTURES / "categories_subset.json") as f:
        return json.load(f)


# --- Sort key ---


def test_sysno_sort_numeric() -> None:
    assert _sysno_sort_key("101.1") < _sysno_sort_key("175.1")


def test_sysno_sort_subparts() -> None:
    assert _sysno_sort_key("175.1") < _sysno_sort_key("175.100")


def test_sysno_sort_deep() -> None:
    keys = [_sysno_sort_key(s) for s in ["414.300", "414.1", "414.2"]]
    assert sorted(keys) == [
        _sysno_sort_key("414.1"),
        _sysno_sort_key("414.2"),
        _sysno_sort_key("414.300"),
    ]


# --- fetch_index ---


def test_fetch_index_parses_entries(
    index_response: dict, httpx_mock,
) -> None:
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )

    with httpx.Client() as client:
        entries = fetch_index(client)

    assert len(entries) > 0
    assert all(isinstance(e, LawEntry) for e in entries)


def test_fetch_index_sorted(
    index_response: dict, httpx_mock,
) -> None:
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )

    with httpx.Client() as client:
        entries = fetch_index(client)

    sysnos = [e.systematic_number for e in entries]
    assert sysnos == sorted(sysnos, key=_sysno_sort_key)


def test_fetch_index_entry_fields(
    index_response: dict, httpx_mock,
) -> None:
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )

    with httpx.Client() as client:
        entries = fetch_index(client)

    entry = entries[0]
    assert isinstance(entry.id, int)
    assert isinstance(entry.systematic_number, str)
    assert len(entry.systematic_number) > 0
    assert isinstance(entry.title, str)
    assert len(entry.title) > 0
    assert isinstance(entry.category_id, int)
    assert isinstance(entry.abrogated, bool)
    assert isinstance(entry.structured_document_id, int)


# --- fetch_categories ---


def test_fetch_categories_parses(
    categories_response: list, httpx_mock,
) -> None:
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/systematic_categories",
        json=categories_response,
    )

    with httpx.Client() as client:
        cats = fetch_categories(client)

    assert len(cats) == 2
    assert all(isinstance(c, Category) for c in cats)


def test_fetch_categories_has_children(
    categories_response: list, httpx_mock,
) -> None:
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/systematic_categories",
        json=categories_response,
    )

    with httpx.Client() as client:
        cats = fetch_categories(client)

    # First category (Staat, Volk, Behörden) has subcategories
    first = cats[0]
    assert len(first.children) > 0
    assert all(isinstance(c, Category) for c in first.children)


def test_fetch_categories_fields(
    categories_response: list, httpx_mock,
) -> None:
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/systematic_categories",
        json=categories_response,
    )

    with httpx.Client() as client:
        cats = fetch_categories(client)

    cat = cats[0]
    assert isinstance(cat.id, int)
    assert isinstance(cat.systematic_number, str)
    assert isinstance(cat.name, str)
    assert len(cat.name) > 0
