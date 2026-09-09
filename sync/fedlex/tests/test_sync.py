"""Tests for lex_fedlex_sync.sync module."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote_plus

import httpx as hx
import pytest

from lex_fedlex_sync.sparql import LANG_URI, SPARQL_ENDPOINT
from lex_fedlex_sync.store import read_index, read_meta, read_xml
from lex_fedlex_sync.sync import sync_all

# All sync tests need reusable mocks: the index SPARQL query, per-law
# SPARQL queries, and XML downloads all go through the same mocked transport.
pytestmark = pytest.mark.httpx_mock(
    can_send_already_matched_responses=True,
    assert_all_responses_were_requested=False,
)

LANGS = ("de", "fr", "it")

# -- Fixtures: mock SPARQL and XML responses --

INDEX_RESPONSE = {
    "results": {
        "bindings": [
            # SR 101 — de, fr, it
            {"sr": {"value": "101"}, "law": {"value": "https://fedlex.data.admin.ch/eli/cc/1999/404"}, "lang": {"value": LANG_URI["de"]}, "title": {"value": "Bundesverfassung (BV)"}, "short": {"value": "BV"}},
            {"sr": {"value": "101"}, "law": {"value": "https://fedlex.data.admin.ch/eli/cc/1999/404"}, "lang": {"value": LANG_URI["fr"]}, "title": {"value": "Constitution fédérale (Cst.)"}, "short": {"value": "Cst."}},
            {"sr": {"value": "101"}, "law": {"value": "https://fedlex.data.admin.ch/eli/cc/1999/404"}, "lang": {"value": LANG_URI["it"]}, "title": {"value": "Costituzione federale (Cost.)"}, "short": {"value": "Cost."}},
            # SR 210 — de, fr, it
            {"sr": {"value": "210"}, "law": {"value": "https://fedlex.data.admin.ch/eli/cc/24/233_245_233"}, "lang": {"value": LANG_URI["de"]}, "title": {"value": "Schweizerisches Zivilgesetzbuch (ZGB)"}, "short": {"value": "ZGB"}},
            {"sr": {"value": "210"}, "law": {"value": "https://fedlex.data.admin.ch/eli/cc/24/233_245_233"}, "lang": {"value": LANG_URI["fr"]}, "title": {"value": "Code civil suisse (CC)"}, "short": {"value": "CC"}},
            {"sr": {"value": "210"}, "law": {"value": "https://fedlex.data.admin.ch/eli/cc/24/233_245_233"}, "lang": {"value": LANG_URI["it"]}, "title": {"value": "Codice civile svizzero (CC)"}, "short": {"value": "CC"}},
        ],
    },
}

# Latest versions (all 3 langs per date)
LATEST_XML_101 = {
    "results": {
        "bindings": [
            {"date": {"value": "2024-03-03"}, "lang": {"value": LANG_URI["de"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-latest-de.xml"}},
            {"date": {"value": "2024-03-03"}, "lang": {"value": LANG_URI["fr"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-latest-fr.xml"}},
            {"date": {"value": "2024-03-03"}, "lang": {"value": LANG_URI["it"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-latest-it.xml"}},
        ],
    },
}

LATEST_XML_210 = {
    "results": {
        "bindings": [
            {"date": {"value": "2024-01-01"}, "lang": {"value": LANG_URI["de"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/210-latest-de.xml"}},
            {"date": {"value": "2024-01-01"}, "lang": {"value": LANG_URI["fr"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/210-latest-fr.xml"}},
            {"date": {"value": "2024-01-01"}, "lang": {"value": LANG_URI["it"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/210-latest-it.xml"}},
        ],
    },
}

ALL_XML_101 = {
    "results": {
        "bindings": [
            # 2024-03-03
            {"date": {"value": "2024-03-03"}, "lang": {"value": LANG_URI["de"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-20240303-de.xml"}},
            {"date": {"value": "2024-03-03"}, "lang": {"value": LANG_URI["fr"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-20240303-fr.xml"}},
            {"date": {"value": "2024-03-03"}, "lang": {"value": LANG_URI["it"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-20240303-it.xml"}},
            # 2023-01-01
            {"date": {"value": "2023-01-01"}, "lang": {"value": LANG_URI["de"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-20230101-de.xml"}},
            {"date": {"value": "2023-01-01"}, "lang": {"value": LANG_URI["fr"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-20230101-fr.xml"}},
            {"date": {"value": "2023-01-01"}, "lang": {"value": LANG_URI["it"]}, "url": {"value": "https://fedlex.data.admin.ch/filestore/101-20230101-it.xml"}},
        ],
    },
}

SAMPLE_XML = b'<?xml version="1.0"?><akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><act name="bv"/></akomaNtoso>'

EMPTY_BINDINGS = {"results": {"bindings": []}}


def _setup_latest_mocks(httpx_mock):
    """Mock for a latest-mode trilingual sync."""

    def _sparql_handler(request):
        body = unquote_plus(request.content.decode())
        if "ConsolidationAbstract" in body:
            return hx.Response(200, json=INDEX_RESPONSE)
        if "eli/cc/1999/404" in body:
            return hx.Response(200, json=LATEST_XML_101)
        if "eli/cc/24/233_245_233" in body:
            return hx.Response(200, json=LATEST_XML_210)
        return hx.Response(200, json=EMPTY_BINDINGS)

    httpx_mock.add_callback(_sparql_handler, url=SPARQL_ENDPOINT)
    # XML download mocks for all languages
    for suffix in ("101-latest-de", "101-latest-fr", "101-latest-it",
                    "210-latest-de", "210-latest-fr", "210-latest-it"):
        httpx_mock.add_response(
            url=f"https://fedlex.data.admin.ch/filestore/{suffix}.xml",
            content=SAMPLE_XML,
        )


def _setup_history_mocks(httpx_mock):
    """Mock for a history-mode trilingual sync of SR 101."""

    def _sparql_handler(request):
        body = unquote_plus(request.content.decode())
        if "ConsolidationAbstract" in body:
            return hx.Response(200, json=INDEX_RESPONSE)
        if "eli/cc/1999/404" in body:
            return hx.Response(200, json=ALL_XML_101)
        if "eli/cc/24/233_245_233" in body:
            return hx.Response(200, json=LATEST_XML_210)
        return hx.Response(200, json=EMPTY_BINDINGS)

    httpx_mock.add_callback(_sparql_handler, url=SPARQL_ENDPOINT)
    for suffix in ("101-20240303-de", "101-20240303-fr", "101-20240303-it",
                    "101-20230101-de", "101-20230101-fr", "101-20230101-it",
                    "210-latest-de", "210-latest-fr", "210-latest-it"):
        httpx_mock.add_response(
            url=f"https://fedlex.data.admin.ch/filestore/{suffix}.xml",
            content=SAMPLE_XML,
        )


# -- Tests --

def test_sync_latest_creates_trilingual_files(tmp_path: Path, httpx_mock) -> None:
    _setup_latest_mocks(httpx_mock)

    stats = sync_all(
        tmp_path, langs=LANGS, mode="latest",
        filter_pattern="101", workers=1,
    )

    assert stats.synced == 1
    assert stats.failed == 0
    assert (tmp_path / "ch" / "101" / "de.xml").exists()
    assert (tmp_path / "ch" / "101" / "fr.xml").exists()
    assert (tmp_path / "ch" / "101" / "it.xml").exists()
    assert (tmp_path / "ch" / "101" / "meta.json").exists()
    assert stats.downloads == 3  # 3 languages


def test_sync_writes_index(tmp_path: Path, httpx_mock) -> None:
    _setup_latest_mocks(httpx_mock)

    sync_all(
        tmp_path, langs=LANGS, mode="latest",
        filter_pattern="101", workers=1,
    )

    idx = read_index(tmp_path)
    assert "101" in idx
    assert idx["101"]["latest_date"] == "2024-03-03"
    assert idx["101"]["langs"] == ["de", "fr", "it"]
    assert idx["101"]["synced_history"] == []
    assert "synced_at" in idx["101"]


def test_sync_meta_trilingual(tmp_path: Path, httpx_mock) -> None:
    _setup_latest_mocks(httpx_mock)
    sync_all(
        tmp_path, langs=LANGS, mode="latest",
        filter_pattern="101", workers=1,
    )

    meta = read_meta(tmp_path, "101")
    assert meta is not None
    assert meta.sr == "101"
    assert meta.titles["de"] == "Bundesverfassung (BV)"
    assert meta.titles["fr"] == "Constitution fédérale (Cst.)"
    assert meta.titles["it"] == "Costituzione federale (Cost.)"
    assert meta.abbreviations["de"] == "BV"
    assert meta.abbreviations["fr"] == "Cst."
    assert meta.abbreviations["it"] == "Cost."


def test_sync_incremental_skips(tmp_path: Path, httpx_mock) -> None:
    """Second sync in latest mode skips unchanged laws."""
    _setup_latest_mocks(httpx_mock)
    stats1 = sync_all(
        tmp_path, langs=LANGS, mode="latest",
        filter_pattern="101", workers=1,
    )
    assert stats1.synced == 1

    stats2 = sync_all(
        tmp_path, langs=LANGS, mode="latest",
        filter_pattern="101", workers=1,
    )
    assert stats2.skipped == 1
    assert stats2.synced == 0


def test_sync_force_resync(tmp_path: Path, httpx_mock) -> None:
    _setup_latest_mocks(httpx_mock)
    sync_all(
        tmp_path, langs=LANGS, mode="latest",
        filter_pattern="101", workers=1,
    )

    stats = sync_all(
        tmp_path, langs=LANGS, mode="latest",
        filter_pattern="101", workers=1, force=True,
    )
    assert stats.synced == 1


def test_sync_no_xml_available(tmp_path: Path, httpx_mock) -> None:
    """Laws without XML are counted as no_xml, not failed."""

    def _sparql_handler(request):
        body = unquote_plus(request.content.decode())
        if "ConsolidationAbstract" in body:
            return hx.Response(200, json=INDEX_RESPONSE)
        return hx.Response(200, json=EMPTY_BINDINGS)

    httpx_mock.add_callback(_sparql_handler, url=SPARQL_ENDPOINT)

    stats = sync_all(
        tmp_path, langs=LANGS, mode="latest",
        filter_pattern="101", workers=1,
    )
    assert stats.no_xml == 1
    assert stats.failed == 0


def test_sync_xml_content(tmp_path: Path, httpx_mock) -> None:
    _setup_latest_mocks(httpx_mock)
    sync_all(
        tmp_path, langs=LANGS, mode="latest",
        filter_pattern="101", workers=1,
    )

    for lang in LANGS:
        xml = read_xml(tmp_path, "101", lang)
        assert xml == SAMPLE_XML


def test_sync_history_mode(tmp_path: Path, httpx_mock) -> None:
    """include-history syncs multiple versions in all languages."""
    _setup_history_mocks(httpx_mock)

    stats = sync_all(
        tmp_path, langs=LANGS, mode="include-history",
        filter_pattern="101", workers=1,
    )

    assert stats.synced == 1
    # Latest version — all 3 langs
    for lang in LANGS:
        assert (tmp_path / "ch" / "101" / f"{lang}.xml").exists()
    # Historical version — all 3 langs
    for lang in LANGS:
        assert (tmp_path / "ch" / "101" / "2023-01-01" / f"{lang}.xml").exists()

    idx = read_index(tmp_path)
    assert idx["101"]["latest_date"] == "2024-03-03"
    assert "2023-01-01" in idx["101"]["synced_history"]
    assert "2024-03-03" not in idx["101"]["synced_history"]
    assert stats.downloads == 6  # 2 versions × 3 langs


def test_sync_limit(tmp_path: Path, httpx_mock) -> None:
    """Limit counts actual syncs, not entries processed."""
    _setup_latest_mocks(httpx_mock)

    stats = sync_all(
        tmp_path, langs=LANGS, mode="latest",
        limit=1, workers=1,
    )

    assert stats.synced == 1
    assert stats.total == 2  # both entries seen, but only 1 synced


def test_sync_multiple_laws(tmp_path: Path, httpx_mock) -> None:
    _setup_latest_mocks(httpx_mock)

    stats = sync_all(
        tmp_path, langs=LANGS, mode="latest",
        workers=1,
    )

    assert stats.total == 2
    assert stats.synced == 2
    for lang in LANGS:
        assert (tmp_path / "ch" / "101" / f"{lang}.xml").exists()
        assert (tmp_path / "ch" / "210" / f"{lang}.xml").exists()


def test_sync_stats_counts(tmp_path: Path, httpx_mock) -> None:
    _setup_latest_mocks(httpx_mock)

    stats = sync_all(
        tmp_path, langs=LANGS, mode="latest",
        filter_pattern="101", workers=1,
    )
    assert stats.total == 1
    assert stats.synced + stats.skipped + stats.failed + stats.no_xml == stats.total


def test_sync_limit_excludes_skips(tmp_path: Path, httpx_mock) -> None:
    """With limit=1, syncing twice should sync a different law each run
    because unsynced entries are sorted first."""
    _setup_latest_mocks(httpx_mock)

    # First run: sync 1 law
    stats1 = sync_all(
        tmp_path, langs=LANGS, mode="latest",
        limit=1, workers=1,
    )
    assert stats1.synced == 1

    # Second run: unsynced entries sorted first, so the other law is synced
    stats2 = sync_all(
        tmp_path, langs=LANGS, mode="latest",
        limit=1, workers=1,
    )
    assert stats2.synced == 1

    # Both laws now synced
    idx = read_index(tmp_path)
    assert "101" in idx
    assert "210" in idx


def test_sync_adds_missing_langs(tmp_path: Path, httpx_mock) -> None:
    """Re-sync with more languages downloads missing language files."""
    _setup_latest_mocks(httpx_mock)

    # First: sync DE only
    sync_all(
        tmp_path, langs=("de",), mode="latest",
        filter_pattern="101", workers=1,
    )
    assert (tmp_path / "ch" / "101" / "de.xml").exists()
    assert not (tmp_path / "ch" / "101" / "fr.xml").exists()

    # Second: sync all 3 languages — should detect missing fr/it
    stats = sync_all(
        tmp_path, langs=LANGS, mode="latest",
        filter_pattern="101", workers=1,
    )
    assert stats.synced == 1  # Re-synced because langs expanded
    assert (tmp_path / "ch" / "101" / "fr.xml").exists()
    assert (tmp_path / "ch" / "101" / "it.xml").exists()


# -- Robustness: failures are loud, never silent --


def _sparql_101_only(httpx_mock, versions_response):
    """Mock the index + one law's version query; XML downloads are set by the test."""

    def _sparql_handler(request):
        body = unquote_plus(request.content.decode())
        if "ConsolidationAbstract" in body:
            return hx.Response(200, json=INDEX_RESPONSE)
        if "eli/cc/1999/404" in body:
            return hx.Response(200, json=versions_response)
        return hx.Response(200, json=EMPTY_BINDINGS)

    httpx_mock.add_callback(_sparql_handler, url=SPARQL_ENDPOINT)


def test_download_http_error_is_a_failure(tmp_path: Path, httpx_mock, monkeypatch) -> None:
    """A 404 on one language fails the act; nothing is written, nothing indexed."""
    monkeypatch.setattr("lex_fedlex_sync.sync.DOWNLOAD_BACKOFF_SECONDS", 0.0)
    _sparql_101_only(httpx_mock, LATEST_XML_101)
    httpx_mock.add_response(url="https://fedlex.data.admin.ch/filestore/101-latest-de.xml", content=SAMPLE_XML)
    httpx_mock.add_response(url="https://fedlex.data.admin.ch/filestore/101-latest-fr.xml", status_code=404)

    stats = sync_all(tmp_path, langs=LANGS, mode="latest", filter_pattern="101", workers=1)

    assert stats.failed == 1
    assert stats.synced == 0
    sr, reason = stats.failures[0]
    assert sr == "101"
    assert "101-latest-fr.xml" in reason and "404" in reason
    # Atomic per act: the successfully downloaded de.xml was not written either
    assert not (tmp_path / "ch" / "101" / "de.xml").exists()
    assert "101" not in read_index(tmp_path)


def test_download_non_xml_is_a_failure(tmp_path: Path, httpx_mock) -> None:
    """An HTML error page served with HTTP 200 is rejected."""
    _sparql_101_only(httpx_mock, LATEST_XML_101)
    for lang in ("de", "fr"):
        httpx_mock.add_response(
            url=f"https://fedlex.data.admin.ch/filestore/101-latest-{lang}.xml",
            content=SAMPLE_XML,
        )
    httpx_mock.add_response(
        url="https://fedlex.data.admin.ch/filestore/101-latest-it.xml",
        content=b"<html><body>Service unavailable</body></html>",
    )

    stats = sync_all(tmp_path, langs=LANGS, mode="latest", filter_pattern="101", workers=1)

    assert stats.failed == 1
    assert "not an akomaNtoso document" in stats.failures[0][1]
    assert not (tmp_path / "ch" / "101").exists()


def test_download_empty_body_is_a_failure(tmp_path: Path, httpx_mock) -> None:
    _sparql_101_only(httpx_mock, LATEST_XML_101)
    for lang in ("de", "fr"):
        httpx_mock.add_response(
            url=f"https://fedlex.data.admin.ch/filestore/101-latest-{lang}.xml",
            content=SAMPLE_XML,
        )
    httpx_mock.add_response(
        url="https://fedlex.data.admin.ch/filestore/101-latest-it.xml", content=b"",
    )

    stats = sync_all(tmp_path, langs=LANGS, mode="latest", filter_pattern="101", workers=1)

    assert stats.failed == 1
    assert "empty response" in stats.failures[0][1]


def test_download_retries_transient_errors(tmp_path: Path, httpx_mock, monkeypatch) -> None:
    """A 503 followed by a 200 succeeds without a failure being recorded."""
    monkeypatch.setattr("lex_fedlex_sync.sync.DOWNLOAD_BACKOFF_SECONDS", 0.0)
    _sparql_101_only(httpx_mock, LATEST_XML_101)
    for lang in ("fr", "it"):
        httpx_mock.add_response(
            url=f"https://fedlex.data.admin.ch/filestore/101-latest-{lang}.xml",
            content=SAMPLE_XML,
        )
    calls = {"n": 0}

    def _flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return hx.Response(503, content=b"try later")
        return hx.Response(200, content=SAMPLE_XML)

    httpx_mock.add_callback(
        _flaky, url="https://fedlex.data.admin.ch/filestore/101-latest-de.xml",
    )

    stats = sync_all(tmp_path, langs=LANGS, mode="latest", filter_pattern="101", workers=1)

    assert calls["n"] == 2
    assert stats.failed == 0
    assert stats.synced == 1
    assert (tmp_path / "ch" / "101" / "de.xml").read_bytes() == SAMPLE_XML


def test_download_gives_up_after_max_attempts(tmp_path: Path, httpx_mock, monkeypatch) -> None:
    monkeypatch.setattr("lex_fedlex_sync.sync.DOWNLOAD_BACKOFF_SECONDS", 0.0)
    _sparql_101_only(httpx_mock, LATEST_XML_101)
    for lang in ("fr", "it"):
        httpx_mock.add_response(
            url=f"https://fedlex.data.admin.ch/filestore/101-latest-{lang}.xml",
            content=SAMPLE_XML,
        )
    calls = {"n": 0}

    def _down(request):
        calls["n"] += 1
        return hx.Response(502, content=b"bad gateway")

    httpx_mock.add_callback(
        _down, url="https://fedlex.data.admin.ch/filestore/101-latest-de.xml",
    )

    stats = sync_all(tmp_path, langs=LANGS, mode="latest", filter_pattern="101", workers=1)

    from lex_fedlex_sync.sync import DOWNLOAD_ATTEMPTS
    assert calls["n"] == DOWNLOAD_ATTEMPTS
    assert stats.failed == 1
    assert "HTTP 502" in stats.failures[0][1]


def test_language_gap_is_recorded_not_failed(tmp_path: Path, httpx_mock) -> None:
    """A version Fedlex only offers in some languages syncs those and records the gap."""
    two_langs = {
        "results": {
            "bindings": [
                b for b in LATEST_XML_101["results"]["bindings"]
                if b["lang"]["value"] != LANG_URI["it"]
            ],
        },
    }
    _sparql_101_only(httpx_mock, two_langs)
    for lang in ("de", "fr"):
        httpx_mock.add_response(
            url=f"https://fedlex.data.admin.ch/filestore/101-latest-{lang}.xml",
            content=SAMPLE_XML,
        )

    stats = sync_all(tmp_path, langs=LANGS, mode="latest", filter_pattern="101", workers=1)

    assert stats.failed == 0
    assert stats.synced == 1
    assert stats.lang_gaps == 1
    assert stats.gaps == [("101", "2024-03-03", "it")]
    assert (tmp_path / "ch" / "101" / "de.xml").exists()
    assert (tmp_path / "ch" / "101" / "fr.xml").exists()
    assert not (tmp_path / "ch" / "101" / "it.xml").exists()
    # The gap is documented in meta.json for later audits
    meta = read_meta(tmp_path, "101")
    assert meta is not None
    assert set(meta.versions[0].urls) == {"de", "fr"}


def test_unchanged_history_run_is_skipped(tmp_path: Path, httpx_mock) -> None:
    """A second include-history run with nothing new counts as skipped, not synced."""
    _setup_history_mocks(httpx_mock)
    sync_all(tmp_path, langs=LANGS, mode="include-history", filter_pattern="101", workers=1)

    stats = sync_all(tmp_path, langs=LANGS, mode="include-history", filter_pattern="101", workers=1)

    assert stats.synced == 0
    assert stats.skipped == 1
    assert stats.downloads == 0


def test_missing_file_on_disk_is_redownloaded(tmp_path: Path, httpx_mock) -> None:
    """Disk is the truth: a lost historical file is fetched again on the next run."""
    _setup_history_mocks(httpx_mock)
    sync_all(tmp_path, langs=LANGS, mode="include-history", filter_pattern="101", workers=1)
    lost = tmp_path / "ch" / "101" / "2023-01-01" / "fr.xml"
    lost.unlink()

    stats = sync_all(tmp_path, langs=LANGS, mode="include-history", filter_pattern="101", workers=1)

    assert stats.synced == 1
    assert stats.downloads == 1
    assert lost.read_bytes() == SAMPLE_XML


def test_repeal_check_error_does_not_abort_run(tmp_path: Path, httpx_mock) -> None:
    """A failing status query for a dropped act is a recorded failure; the index is still written."""
    from lex_fedlex_sync.store import write_index

    write_index(tmp_path, {
        "999": {"law_uri": "https://fedlex.data.admin.ch/eli/cc/9999/1",
                "latest_date": "2000-01-01", "synced_history": [],
                "langs": ["de", "fr", "it"], "status": "in_force"},
    })

    def _sparql_handler(request):
        body = unquote_plus(request.content.decode())
        if "ConsolidationAbstract" in body:
            return hx.Response(200, json=INDEX_RESPONSE)
        if "eli/cc/9999/1" in body:
            return hx.Response(500, content=b"boom")
        if "eli/cc/1999/404" in body:
            return hx.Response(200, json=LATEST_XML_101)
        if "eli/cc/24/233_245_233" in body:
            return hx.Response(200, json=LATEST_XML_210)
        return hx.Response(200, json=EMPTY_BINDINGS)

    httpx_mock.add_callback(_sparql_handler, url=SPARQL_ENDPOINT)
    for suffix in ("101-latest-de", "101-latest-fr", "101-latest-it",
                   "210-latest-de", "210-latest-fr", "210-latest-it"):
        httpx_mock.add_response(
            url=f"https://fedlex.data.admin.ch/filestore/{suffix}.xml", content=SAMPLE_XML,
        )

    stats = sync_all(tmp_path, langs=LANGS, mode="latest", workers=1)

    assert stats.synced == 2
    assert stats.failed == 1
    assert stats.failures[0][0] == "999"
    assert stats.failures[0][1].startswith("repeal-check")
    idx = read_index(tmp_path)
    assert "101" in idx and "210" in idx
    assert idx["999"]["status"] == "in_force"  # untouched, retried next run


def test_version_query_error_is_a_failure(tmp_path: Path, httpx_mock) -> None:
    def _sparql_handler(request):
        body = unquote_plus(request.content.decode())
        if "ConsolidationAbstract" in body:
            return hx.Response(200, json=INDEX_RESPONSE)
        return hx.Response(503, content=b"down")

    httpx_mock.add_callback(_sparql_handler, url=SPARQL_ENDPOINT)

    stats = sync_all(tmp_path, langs=LANGS, mode="latest", filter_pattern="101", workers=1)

    assert stats.failed == 1
    assert stats.failures[0][1].startswith("SPARQL version query")
    assert stats.no_xml == 0
