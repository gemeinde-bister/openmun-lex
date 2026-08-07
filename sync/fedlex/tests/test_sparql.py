"""Tests for lex_fedlex_sync.sparql module."""

from __future__ import annotations

import pytest

from lex_fedlex_sync.sparql import (
    LANG_URI,
    SPARQL_ENDPOINT,
    FedlexEntry,
    VersionInfo,
    fetch_all_xml_urls,
    fetch_index,
    fetch_law_status,
    make_client,
    sr_sort_key,
)


# Sample SPARQL response for multilingual index query
INDEX_BINDINGS = {
    "results": {
        "bindings": [
            # SR 101 — de
            {
                "sr": {"value": "101"},
                "law": {"value": "https://fedlex.data.admin.ch/eli/cc/1999/404"},
                "lang": {"value": LANG_URI["de"]},
                "title": {"value": "Bundesverfassung der Schweizerischen Eidgenossenschaft (BV)"},
                "short": {"value": "BV"},
            },
            # SR 101 — fr
            {
                "sr": {"value": "101"},
                "law": {"value": "https://fedlex.data.admin.ch/eli/cc/1999/404"},
                "lang": {"value": LANG_URI["fr"]},
                "title": {"value": "Constitution fédérale de la Confédération suisse (Cst.)"},
                "short": {"value": "Cst."},
            },
            # SR 101 — it
            {
                "sr": {"value": "101"},
                "law": {"value": "https://fedlex.data.admin.ch/eli/cc/1999/404"},
                "lang": {"value": LANG_URI["it"]},
                "title": {"value": "Costituzione federale della Confederazione Svizzera (Cost.)"},
                "short": {"value": "Cost."},
            },
            # SR 210 — de only (short missing, extracted from title)
            {
                "sr": {"value": "210"},
                "law": {"value": "https://fedlex.data.admin.ch/eli/cc/24/233_245_233"},
                "lang": {"value": LANG_URI["de"]},
                "title": {"value": "Schweizerisches Zivilgesetzbuch (ZGB)"},
            },
            # SR 210 — fr
            {
                "sr": {"value": "210"},
                "law": {"value": "https://fedlex.data.admin.ch/eli/cc/24/233_245_233"},
                "lang": {"value": LANG_URI["fr"]},
                "title": {"value": "Code civil suisse (CC)"},
                "short": {"value": "CC"},
            },
            # Treaty
            {
                "sr": {"value": "0.101"},
                "law": {"value": "https://fedlex.data.admin.ch/eli/cc/1974/2151_2151_2151"},
                "lang": {"value": LANG_URI["de"]},
                "title": {"value": "Konvention zum Schutze der Menschenrechte (EMRK)"},
                "short": {"value": "EMRK"},
            },
        ],
    },
}


# Sample multilingual XML URL response
ALL_XML_BINDINGS = {
    "results": {
        "bindings": [
            # 2024-03-03: de, fr, it
            {"date": {"value": "2024-03-03"}, "lang": {"value": LANG_URI["de"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-20240303-de.xml"}},
            {"date": {"value": "2024-03-03"}, "lang": {"value": LANG_URI["fr"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-20240303-fr.xml"}},
            {"date": {"value": "2024-03-03"}, "lang": {"value": LANG_URI["it"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-20240303-it.xml"}},
            # 2023-01-01: de, fr, it
            {"date": {"value": "2023-01-01"}, "lang": {"value": LANG_URI["de"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-20230101-de.xml"}},
            {"date": {"value": "2023-01-01"}, "lang": {"value": LANG_URI["fr"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-20230101-fr.xml"}},
            {"date": {"value": "2023-01-01"}, "lang": {"value": LANG_URI["it"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-20230101-it.xml"}},
        ],
    },
}

EMPTY_BINDINGS = {"results": {"bindings": []}}


def test_fetch_index_trilingual(httpx_mock) -> None:
    httpx_mock.add_response(
        url=SPARQL_ENDPOINT,
        json=INDEX_BINDINGS,
    )

    with make_client() as client:
        entries = fetch_index(client, langs=("de", "fr", "it"))

    assert len(entries) == 3
    # Treaties sort first
    assert entries[0].sr == "0.101"
    assert entries[0].abbreviation == "EMRK"
    assert entries[1].sr == "101"
    assert entries[2].sr == "210"

    # Check trilingual titles for SR 101
    bv = entries[1]
    assert bv.titles["de"] == "Bundesverfassung der Schweizerischen Eidgenossenschaft (BV)"
    assert bv.titles["fr"] == "Constitution fédérale de la Confédération suisse (Cst.)"
    assert bv.titles["it"] == "Costituzione federale della Confederazione Svizzera (Cost.)"
    assert bv.abbreviations["de"] == "BV"
    assert bv.abbreviations["fr"] == "Cst."
    assert bv.abbreviations["it"] == "Cost."

    # .title and .abbreviation return first (de)
    assert bv.title == bv.titles["de"]
    assert bv.abbreviation == bv.abbreviations["de"]


def test_fetch_index_abbr_from_title(httpx_mock) -> None:
    """Abbreviation extracted from title if short field missing."""
    bindings = {
        "results": {
            "bindings": [
                {
                    "sr": {"value": "220"},
                    "law": {"value": "https://example.com/or"},
                    "lang": {"value": LANG_URI["de"]},
                    "title": {"value": "Obligationenrecht (OR)"},
                },
            ],
        },
    }
    httpx_mock.add_response(url=SPARQL_ENDPOINT, json=bindings)

    with make_client() as client:
        entries = fetch_index(client, langs=("de",))

    assert entries[0].abbreviations == {"de": "OR"}


def test_fetch_index_dedup(httpx_mock) -> None:
    """Multiple rows for same SR+lang are deduplicated."""
    bindings = {
        "results": {
            "bindings": [
                {
                    "sr": {"value": "101"},
                    "law": {"value": "https://fedlex.data.admin.ch/eli/cc/1999/404"},
                    "lang": {"value": LANG_URI["de"]},
                    "title": {"value": "Old title"},
                },
                {
                    "sr": {"value": "101"},
                    "law": {"value": "https://fedlex.data.admin.ch/eli/cc/1999/404"},
                    "lang": {"value": LANG_URI["de"]},
                    "title": {"value": "Bundesverfassung (BV)"},
                    "short": {"value": "BV"},
                },
            ],
        },
    }
    httpx_mock.add_response(url=SPARQL_ENDPOINT, json=bindings)

    with make_client() as client:
        entries = fetch_index(client, langs=("de",))

    # First row wins (dedup keeps first per lang)
    assert len(entries) == 1
    assert entries[0].abbreviations["de"] == "BV"


def test_fetch_index_taxonomy_sourced_sr(httpx_mock) -> None:
    """SR sourced from the taxonomy notation (typed literal, no historicalLegalId).

    Regression guard for the 369 totally-revised in-force acts (e.g. the 2020
    DSG, SR 235.1) whose expression carries no historicalLegalId — their SR
    arrives as a ``skos:notation`` typed literal with datatype ``id-systematique``.
    ``fetch_index`` must read the value regardless of datatype.
    """
    bindings = {
        "results": {
            "bindings": [
                {
                    "sr": {
                        "type": "typed-literal",
                        "datatype": "https://fedlex.data.admin.ch/vocabulary/notation-type/id-systematique",
                        "value": "235.1",
                    },
                    "law": {"value": "https://fedlex.data.admin.ch/eli/cc/2022/491"},
                    "lang": {"value": LANG_URI["de"]},
                    "title": {"value": "Bundesgesetz vom 25. September 2020 über den Datenschutz (DSG)"},
                    "short": {"value": "DSG"},
                },
            ],
        },
    }
    httpx_mock.add_response(url=SPARQL_ENDPOINT, json=bindings)

    with make_client() as client:
        entries = fetch_index(client, langs=("de",))

    assert len(entries) == 1
    assert entries[0].sr == "235.1"
    assert entries[0].law_uri == "https://fedlex.data.admin.ch/eli/cc/2022/491"
    assert entries[0].abbreviations["de"] == "DSG"


@pytest.mark.integration
def test_fetch_index_includes_revised_dsg_live() -> None:
    """Live: the in-force 2020 DSG (SR 235.1) must appear in the real index.

    This is the law that the old historicalLegalId-only query dropped; the
    taxonomy-notation query must recover it.  Opt in with ``-m integration``.
    """
    with make_client() as client:
        entries = fetch_index(client, langs=("de",))

    by_sr = {e.sr: e for e in entries}
    assert "235.1" in by_sr, "DSG (235.1) missing — taxonomy notation query regressed"
    assert "Datenschutz" in by_sr["235.1"].titles["de"]
    # Universe must be materially larger than the historicalLegalId-only 4724.
    assert len(entries) > 5000


def test_fetch_all_xml_urls_multilang(httpx_mock) -> None:
    httpx_mock.add_response(url=SPARQL_ENDPOINT, json=ALL_XML_BINDINGS)

    with make_client() as client:
        versions = fetch_all_xml_urls(
            client,
            "https://fedlex.data.admin.ch/eli/cc/1999/404",
            langs=("de", "fr", "it"),
        )

    assert len(versions) == 2
    assert versions[0].date == "2024-03-03"
    assert versions[1].date == "2023-01-01"

    # Each version has 3 language URLs
    v0 = versions[0]
    assert len(v0.urls) == 3
    assert "de" in v0.urls
    assert "fr" in v0.urls
    assert "it" in v0.urls
    assert v0.urls["de"].endswith("-de.xml")
    assert v0.urls["fr"].endswith("-fr.xml")


def test_fetch_all_xml_urls_empty(httpx_mock) -> None:
    httpx_mock.add_response(url=SPARQL_ENDPOINT, json=EMPTY_BINDINGS)

    with make_client() as client:
        versions = fetch_all_xml_urls(
            client,
            "https://example.com/noxml",
            langs=("de",),
        )

    assert versions == []


def test_fetch_law_status_repealed(httpx_mock) -> None:
    """A repealed act reports status code 3 and the latest repeal date."""
    bindings = {
        "results": {
            "bindings": [
                {
                    "status": {"value": "https://fedlex.data.admin.ch/vocabulary/enforcement-status/3"},
                    "repealed": {"value": "2026-03-01"},
                },
            ],
        },
    }
    httpx_mock.add_response(url=SPARQL_ENDPOINT, json=bindings)

    with make_client() as client:
        status, repealed = fetch_law_status(
            client, "https://fedlex.data.admin.ch/eli/cc/2009/123",
        )

    assert status == "3"
    assert repealed == "2026-03-01"


def test_fetch_law_status_in_force_no_date(httpx_mock) -> None:
    """An in-force act reports status 0 and no repeal date."""
    bindings = {
        "results": {
            "bindings": [
                {
                    "status": {"value": "https://fedlex.data.admin.ch/vocabulary/enforcement-status/0"},
                },
            ],
        },
    }
    httpx_mock.add_response(url=SPARQL_ENDPOINT, json=bindings)

    with make_client() as client:
        status, repealed = fetch_law_status(client, "https://example.com/x")

    assert status == "0"
    assert repealed is None


def test_fetch_law_status_unknown_uri(httpx_mock) -> None:
    """An unknown URI yields a safe in-force default (no marking)."""
    httpx_mock.add_response(url=SPARQL_ENDPOINT, json={"results": {"bindings": []}})

    with make_client() as client:
        status, repealed = fetch_law_status(client, "https://example.com/missing")

    assert status == "0"
    assert repealed is None


def test_sr_sort_key_domestic() -> None:
    entries = [
        FedlexEntry(sr="210", law_uri="", titles={}, abbreviations={}),
        FedlexEntry(sr="101", law_uri="", titles={}, abbreviations={}),
        FedlexEntry(sr="101.1", law_uri="", titles={}, abbreviations={}),
    ]
    entries.sort(key=sr_sort_key)
    assert [e.sr for e in entries] == ["101", "101.1", "210"]


def test_sr_sort_key_treaties_first() -> None:
    """Treaties (0.*) get prefix (10,) which sorts before domestic (101+)."""
    entries = [
        FedlexEntry(sr="0.101", law_uri="", titles={}, abbreviations={}),
        FedlexEntry(sr="101", law_uri="", titles={}, abbreviations={}),
        FedlexEntry(sr="999", law_uri="", titles={}, abbreviations={}),
    ]
    entries.sort(key=sr_sort_key)
    assert [e.sr for e in entries] == ["0.101", "101", "999"]
