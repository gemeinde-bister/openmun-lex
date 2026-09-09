"""Tests for lex_sync.sync module."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest

from lex_sync.store import read_index, read_meta, read_meta_versions
from lex_sync.sync import sync_all

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def gemg_response() -> dict:
    with open(FIXTURES / "gemg_subset.json") as f:
        return json.load(f)


@pytest.fixture()
def index_response() -> dict:
    with open(FIXTURES / "index_subset.json") as f:
        return json.load(f)


def _make_old_version_response(gemg_response: dict, version_id: int, dates_str: str) -> dict:
    """Build a mock API response for an old version fetch.

    Clones the fixture and patches selected_version to look like a historical version.
    """
    resp = copy.deepcopy(gemg_response)
    resp["text_of_law"]["selected_version"]["id"] = version_id
    resp["text_of_law"]["selected_version"]["version_dates_str"] = dates_str
    return resp


def _setup_mocks(httpx_mock, index_response, gemg_response):
    """Set up mock responses for a sync of GemG with all versions."""
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )
    # Current version
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/show_as_json",
        json=gemg_response,
    )
    # Old versions (IDs from gemg_subset.json: 2863 and 1664)
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/versions/2863/show_as_json",
        json=_make_old_version_response(
            gemg_response, 2863,
            "Version in Kraft von: 01.05.2021 bis: 31.12.2022 (Beschlussdatum: 15.09.2011)",
        ),
    )
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/versions/1664/show_as_json",
        json=_make_old_version_response(
            gemg_response, 1664,
            "Version in Kraft von: 01.01.2012 bis: 30.04.2021 (Beschlussdatum: 15.09.2011)",
        ),
    )


def _setup_mocks_skip(httpx_mock, index_response, gemg_response):
    """Set up minimal mocks for a sync that will skip (index + doc only).

    Used when testing incremental skip — the skip path only fetches the
    index and current doc JSON to check version_uid, never old versions.
    """
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/show_as_json",
        json=gemg_response,
    )


def _setup_mocks_no_versions(httpx_mock, index_response, gemg_response):
    """Set up mock responses without old version endpoints (basic tests)."""
    # Strip old_versions from response to avoid version fetch attempts
    resp = copy.deepcopy(gemg_response)
    resp["text_of_law"]["old_versions"] = []
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/show_as_json",
        json=resp,
    )
    return resp


def test_sync_creates_both_language_files(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    _setup_mocks(httpx_mock, index_response, gemg_response)

    stats = sync_all(
        tmp_path,
        filter_pattern="175.1",
       
    )

    assert stats.synced >= 1
    assert stats.failed == 0
    assert (tmp_path / "vs" / "175.1" / "de.xml").exists()
    assert (tmp_path / "vs" / "175.1" / "fr.xml").exists()
    assert (tmp_path / "vs" / "175.1" / "meta.json").exists()
    assert (tmp_path / "vs" / "175.1" / "source.json").exists()


def test_sync_writes_index(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    _setup_mocks(httpx_mock, index_response, gemg_response)

    sync_all(tmp_path, filter_pattern="175.1")

    idx = read_index(tmp_path)
    assert "175.1" in idx
    assert "version_uid" in idx["175.1"]
    assert "synced_at" in idx["175.1"]
    assert idx["175.1"]["langs"] == ["de", "fr"]


def test_sync_incremental_skips_unchanged(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """Second sync skips if version_uid hasn't changed and all versions synced."""
    # First sync
    _setup_mocks(httpx_mock, index_response, gemg_response)
    stats1 = sync_all(tmp_path, filter_pattern="175.1")
    assert stats1.synced >= 1

    # Second sync — only needs index + current doc (skip path doesn't fetch versions)
    _setup_mocks_skip(httpx_mock, index_response, gemg_response)
    stats2 = sync_all(tmp_path, filter_pattern="175.1")
    assert stats2.skipped >= 1
    assert stats2.synced == 0


def test_sync_force_resync(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """Force flag causes re-sync even if unchanged."""
    _setup_mocks(httpx_mock, index_response, gemg_response)
    sync_all(tmp_path, filter_pattern="175.1")

    _setup_mocks(httpx_mock, index_response, gemg_response)
    stats = sync_all(
        tmp_path, filter_pattern="175.1",
        force=True,
    )
    assert stats.synced >= 1


def test_sync_meta_readable(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    _setup_mocks(httpx_mock, index_response, gemg_response)
    sync_all(tmp_path, filter_pattern="175.1")

    meta = read_meta(tmp_path, "175.1")
    assert meta is not None
    assert meta.law_type == "Gesetz"
    assert meta.law_type_id == 4
    assert meta.available_languages == ["de", "fr"]


def test_sync_source_json_stored(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    _setup_mocks(httpx_mock, index_response, gemg_response)
    sync_all(tmp_path, filter_pattern="175.1")

    source_path = tmp_path / "vs" / "175.1" / "source.json"
    assert source_path.exists()
    source = json.loads(source_path.read_text(encoding="utf-8"))
    assert "text_of_law" in source
    assert source["text_of_law"]["systematic_number"] == "175.1"


def test_sync_handles_404(
    tmp_path: Path, httpx_mock, index_response,
) -> None:
    """A law the index lists but the document endpoint 404s is a failure.

    The index only lists laws with structured content, so a 404 here is an
    upstream inconsistency that must be visible — not a silent skip that
    leaves a stale or missing law behind.
    """
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/show_as_json",
        status_code=404,
    )

    stats = sync_all(
        tmp_path, filter_pattern="175.1",
    )
    assert stats.failed == 1
    assert stats.skipped == 0
    sysno, reason = stats.failures[0]
    assert sysno == "175.1"
    assert "404" in reason
    assert not (tmp_path / "vs" / "175.1").exists()


def test_sync_stats_counts(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    _setup_mocks(httpx_mock, index_response, gemg_response)

    stats = sync_all(
        tmp_path, filter_pattern="175.1",
    )
    assert stats.total >= 1
    assert stats.synced + stats.skipped + stats.failed == stats.total


def test_sync_single_lang(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """Can sync a single language only."""
    resp = _setup_mocks_no_versions(httpx_mock, index_response, gemg_response)

    stats = sync_all(
        tmp_path, filter_pattern="175.1",
        langs=("de",),
    )

    assert stats.synced >= 1
    assert (tmp_path / "vs" / "175.1" / "de.xml").exists()
    assert not (tmp_path / "vs" / "175.1" / "fr.xml").exists()


# --- Version sync ---


def test_sync_creates_version_subdirs(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """Sync creates date subdirectories for current and old versions."""
    _setup_mocks(httpx_mock, index_response, gemg_response)

    stats = sync_all(tmp_path, filter_pattern="175.1")

    assert stats.synced >= 1
    assert stats.failed == 0

    # Current version date subdir
    assert (tmp_path / "vs" / "175.1" / "2023-01-01" / "de.xml").exists()
    assert (tmp_path / "vs" / "175.1" / "2023-01-01" / "fr.xml").exists()
    assert (tmp_path / "vs" / "175.1" / "2023-01-01" / "source.json").exists()

    # Old version 2863
    assert (tmp_path / "vs" / "175.1" / "2021-05-01" / "de.xml").exists()
    assert (tmp_path / "vs" / "175.1" / "2021-05-01" / "fr.xml").exists()
    assert (tmp_path / "vs" / "175.1" / "2021-05-01" / "source.json").exists()

    # Old version 1664
    assert (tmp_path / "vs" / "175.1" / "2012-01-01" / "de.xml").exists()
    assert (tmp_path / "vs" / "175.1" / "2012-01-01" / "fr.xml").exists()
    assert (tmp_path / "vs" / "175.1" / "2012-01-01" / "source.json").exists()


def test_sync_index_has_versions_synced(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """Sync index tracks which version IDs have been synced."""
    _setup_mocks(httpx_mock, index_response, gemg_response)

    sync_all(tmp_path, filter_pattern="175.1")

    idx = read_index(tmp_path)
    entry = idx["175.1"]
    assert "versions_synced" in entry
    versions_synced = entry["versions_synced"]
    assert 3103 in versions_synced  # current
    assert 2863 in versions_synced  # old
    assert 1664 in versions_synced  # oldest


def test_sync_meta_has_versions_list(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """meta.json includes versions list sorted newest-first."""
    _setup_mocks(httpx_mock, index_response, gemg_response)

    sync_all(tmp_path, filter_pattern="175.1")

    versions = read_meta_versions(tmp_path, "175.1")
    assert len(versions) == 3
    # Newest first
    assert versions[0].date == "2023-01-01"
    assert versions[0].version_id == 3103
    assert versions[1].date == "2021-05-01"
    assert versions[1].version_id == 2863
    assert versions[2].date == "2012-01-01"
    assert versions[2].version_id == 1664


def test_sync_incremental_skips_already_synced_versions(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """Second sync does NOT re-fetch old versions that are already synced."""
    # First sync — fetches everything
    _setup_mocks(httpx_mock, index_response, gemg_response)
    stats1 = sync_all(tmp_path, filter_pattern="175.1")
    assert stats1.synced >= 1

    # Second sync — should skip entirely (uid unchanged, all versions synced)
    _setup_mocks_skip(httpx_mock, index_response, gemg_response)
    stats2 = sync_all(tmp_path, filter_pattern="175.1")
    assert stats2.skipped >= 1
    assert stats2.synced == 0


def test_sync_incremental_fetches_new_versions_only(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """If a new old version appears, only that version is fetched."""
    # First sync with no old versions
    resp_no_old = _setup_mocks_no_versions(httpx_mock, index_response, gemg_response)
    sync_all(tmp_path, filter_pattern="175.1")

    idx = read_index(tmp_path)
    assert "versions_synced" in idx["175.1"]
    # Only current version synced
    assert 3103 in idx["175.1"]["versions_synced"]
    assert 2863 not in idx["175.1"]["versions_synced"]

    # Second sync — now the response has old_versions
    _setup_mocks(httpx_mock, index_response, gemg_response)
    stats2 = sync_all(tmp_path, filter_pattern="175.1")

    # Should sync (not skip) because new versions discovered
    assert stats2.synced >= 1

    # Now all versions should be in index
    idx2 = read_index(tmp_path)
    assert 2863 in idx2["175.1"]["versions_synced"]
    assert 1664 in idx2["175.1"]["versions_synced"]

    # And date subdirs should exist
    assert (tmp_path / "vs" / "175.1" / "2021-05-01" / "de.xml").exists()
    assert (tmp_path / "vs" / "175.1" / "2012-01-01" / "de.xml").exists()


def test_sync_old_version_404_warns(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """Old version returning 404 produces a warning, not a failure."""
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/show_as_json",
        json=gemg_response,
    )
    # Version 2863 returns 404
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/versions/2863/show_as_json",
        status_code=404,
    )
    # Version 1664 is normal
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/versions/1664/show_as_json",
        json=_make_old_version_response(
            gemg_response, 1664,
            "Version in Kraft von: 01.01.2012 bis: 30.04.2021 (Beschlussdatum: 15.09.2011)",
        ),
    )

    stats = sync_all(tmp_path, filter_pattern="175.1")

    assert stats.synced >= 1
    assert stats.failed == 0
    assert stats.warnings >= 1  # Warning for 404 version


def test_sync_versions_synced_count(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """versions_synced stat counts total version syncs."""
    _setup_mocks(httpx_mock, index_response, gemg_response)

    stats = sync_all(tmp_path, filter_pattern="175.1")

    # 1 current + 2 old = 3 versions
    assert stats.versions_synced == 3


# --- Robustness: problems are loud, never silent ---


def test_sync_language_gap_is_recorded(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """A version upstream offers only in German is stored in German and the gap counted."""
    de_only = copy.deepcopy(gemg_response)
    de_only["text_of_law"]["selected_version"]["available_languages"] = [
        {"language": {"id": 2, "iso639_1_code": "de", "name_native": "Deutsch"}},
    ]
    de_only["text_of_law"]["old_versions"] = []
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/show_as_json",
        json=de_only,
    )

    stats = sync_all(tmp_path, filter_pattern="175.1")

    assert stats.failed == 0
    assert stats.synced == 1
    assert stats.lang_gaps == 1
    assert stats.gaps[0][0] == "175.1"
    assert stats.gaps[0][2] == "fr"
    assert (tmp_path / "vs" / "175.1" / "de.xml").exists()
    assert not (tmp_path / "vs" / "175.1" / "fr.xml").exists()


def test_sync_no_requested_language_available_fails(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    it_only = copy.deepcopy(gemg_response)
    it_only["text_of_law"]["selected_version"]["available_languages"] = [
        {"language": {"id": 4, "iso639_1_code": "it", "name_native": "Italiano"}},
    ]
    it_only["text_of_law"]["old_versions"] = []
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/show_as_json",
        json=it_only,
    )

    stats = sync_all(tmp_path, filter_pattern="175.1")

    assert stats.failed == 1
    assert "none of the requested languages" in stats.failures[0][1]
    assert not (tmp_path / "vs" / "175.1").exists()


def test_sync_unstructured_payload_fails_without_partial_files(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """A current version without json_content fails the law; nothing is written."""
    broken = copy.deepcopy(gemg_response)
    broken["text_of_law"]["selected_version"]["json_content"] = None
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/show_as_json",
        json=broken,
    )

    stats = sync_all(tmp_path, filter_pattern="175.1")

    assert stats.failed == 1
    assert "no structured content" in stats.failures[0][1]
    assert not (tmp_path / "vs" / "175.1").exists()
    assert "175.1" not in read_index(tmp_path)


def test_sync_non_json_payload_fails(
    tmp_path: Path, httpx_mock, index_response,
) -> None:
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/show_as_json",
        content=b"<html>maintenance</html>",
        headers={"content-type": "text/html"},
    )

    stats = sync_all(tmp_path, filter_pattern="175.1")

    assert stats.failed == 1
    assert "not JSON" in stats.failures[0][1]


def test_sync_old_version_failure_writes_nothing(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """A hard error on an old version fails the whole law atomically."""
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/show_as_json",
        json=gemg_response,
    )
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/175.1/versions/2863/show_as_json",
        status_code=403,
    )

    stats = sync_all(tmp_path, filter_pattern="175.1")

    assert stats.failed == 1
    assert "403" in stats.failures[0][1]
    # Current version was converted fine, but nothing hit the disk
    assert not (tmp_path / "vs" / "175.1" / "de.xml").exists()
    assert "175.1" not in read_index(tmp_path)


def test_sync_retries_transient_errors(
    tmp_path: Path, httpx_mock, index_response, gemg_response, monkeypatch,
) -> None:
    monkeypatch.setattr("lex_sync.api.BACKOFF_SECONDS", 0.0)
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )
    doc_url = "https://lex.vs.ch/api/de/texts_of_law/175.1/show_as_json"
    calls = {"n": 0}

    def _flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, content=b"busy")
        return httpx.Response(200, json=gemg_response)

    httpx_mock.add_callback(_flaky, url=doc_url, is_reusable=True)
    for vid, dates in (
        (2863, "Version in Kraft von: 01.05.2021 bis: 31.12.2022 (Beschlussdatum: 15.09.2011)"),
        (1664, "Version in Kraft von: 01.01.2012 bis: 30.04.2021 (Beschlussdatum: 15.09.2011)"),
    ):
        httpx_mock.add_response(
            url=f"https://lex.vs.ch/api/de/texts_of_law/175.1/versions/{vid}/show_as_json",
            json=_make_old_version_response(gemg_response, vid, dates),
        )

    stats = sync_all(tmp_path, filter_pattern="175.1")

    assert calls["n"] == 2
    assert stats.failed == 0
    assert stats.synced == 1


def test_sync_gives_up_after_retries(
    tmp_path: Path, httpx_mock, index_response, monkeypatch,
) -> None:
    from lex_sync.api import ATTEMPTS

    monkeypatch.setattr("lex_sync.api.BACKOFF_SECONDS", 0.0)
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/lightweight_index",
        json=index_response,
    )
    calls = {"n": 0}

    def _down(request):
        calls["n"] += 1
        return httpx.Response(502, content=b"bad gateway")

    httpx_mock.add_callback(
        _down, url="https://lex.vs.ch/api/de/texts_of_law/175.1/show_as_json",
        is_reusable=True,
    )

    stats = sync_all(tmp_path, filter_pattern="175.1")

    assert calls["n"] == ATTEMPTS
    assert stats.failed == 1
    assert "502" in stats.failures[0][1]


def test_repeal_check_error_does_not_abort_run(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """A failing document fetch for a dropped law is recorded; the index is still written."""
    from lex_sync.store import write_index

    write_index(tmp_path, {
        "999.9": {"version_uid": "x", "title": "Gone", "langs": ["de", "fr"],
                  "status": "in_force", "versions_synced": []},
    })
    only_gemg = {"12": [law for law in index_response["12"]
                        if law["systematic_number"] == "175.1"]}
    _setup_mocks(httpx_mock, only_gemg, gemg_response)
    httpx_mock.add_response(
        url="https://lex.vs.ch/api/de/texts_of_law/999.9/show_as_json",
        status_code=403,
    )

    stats = sync_all(tmp_path)  # full active-only run → repeal check runs

    assert stats.synced >= 1
    assert stats.failed == 1
    assert stats.failures[0][0] == "999.9"
    assert stats.failures[0][1].startswith("repeal-check")
    idx = read_index(tmp_path)
    assert "175.1" in idx
    assert idx["999.9"]["status"] == "in_force"  # untouched, retried next run


@pytest.mark.httpx_mock(can_send_already_matched_responses=True)
def test_sync_added_language_triggers_resync(
    tmp_path: Path, httpx_mock, index_response, gemg_response,
) -> None:
    """Requesting a language that was not synced before re-syncs the law."""
    _setup_mocks(httpx_mock, index_response, gemg_response)
    stats1 = sync_all(tmp_path, filter_pattern="175.1", langs=("de",))
    assert stats1.synced == 1
    assert not (tmp_path / "vs" / "175.1" / "fr.xml").exists()

    stats2 = sync_all(tmp_path, filter_pattern="175.1", langs=("de", "fr"))
    assert stats2.synced == 1
    assert (tmp_path / "vs" / "175.1" / "fr.xml").exists()
