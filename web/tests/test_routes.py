"""Tests for lex_web routes."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lex_web.app import create_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


# --- Landing page ---


def test_index(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "lex" in resp.text
    assert "/eli/ch/" in resp.text
    assert "/eli/vs/" in resp.text
    assert "Bundesrecht" in resp.text
    assert "Kanton Wallis" in resp.text


# --- Federal routes ---


def test_federal_law_200(client: TestClient) -> None:
    resp = client.get("/eli/ch/101")
    assert resp.status_code == 200
    assert "Bundesverfassung" in resp.text


def test_federal_law_has_articles(client: TestClient) -> None:
    resp = client.get("/eli/ch/101")
    assert "art_1" in resp.text
    assert "Art. 1" in resp.text


def test_federal_law_has_toc(client: TestClient) -> None:
    resp = client.get("/eli/ch/101")
    assert "Inhaltsverzeichnis" in resp.text
    assert "toc-entry" in resp.text


def test_federal_law_not_found(client: TestClient) -> None:
    resp = client.get("/eli/ch/999.999.999")
    assert resp.status_code == 404


def test_federal_law_editable_mode(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("lex_web.routes.EDIT_ENABLED", True)
    resp = client.get("/eli/ch/101?edit=1")
    assert resp.status_code == 200
    assert "Bearbeitungsmodus" in resp.text
    assert "law-body-editable" in resp.text
    assert "editor.js" in resp.text


def test_federal_law_edit_disabled_by_default(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("lex_web.routes.EDIT_ENABLED", False)
    resp = client.get("/eli/ch/101?edit=1")
    assert resp.status_code == 200
    assert "editor.js" not in resp.text
    assert "Bearbeitungsmodus" not in resp.text


def test_federal_law_readonly_no_editor(client: TestClient) -> None:
    resp = client.get("/eli/ch/101")
    assert "editor.js" not in resp.text
    assert "Bearbeitungsmodus" not in resp.text


def test_federal_law_sr_prefix(client: TestClient) -> None:
    """Federal law shows 'SR' prefix."""
    resp = client.get("/eli/ch/101")
    assert "SR 101" in resp.text


def test_federal_law_abbreviation(client: TestClient) -> None:
    """Federal law shows abbreviation in parentheses."""
    resp = client.get("/eli/ch/101")
    assert "(BV)" in resp.text


def test_federal_index_200(client: TestClient) -> None:
    """Federal browse page loads and has categories."""
    resp = client.get("/eli/ch/")
    assert resp.status_code == 200
    assert "Bundesrecht" in resp.text
    assert "Staat" in resp.text
    assert "101" in resp.text


def test_federal_index_has_groups(client: TestClient) -> None:
    resp = client.get("/eli/ch/")
    assert "Privatrecht" in resp.text
    assert "Finanzen" in resp.text


# --- Template details ---


def test_static_css(client: TestClient) -> None:
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "akn-article" in resp.text


def test_reverse_proxy_prefix(client: TestClient) -> None:
    """Root path from X-Forwarded-Prefix appears in links."""
    resp = client.get("/", headers={"X-Forwarded-Prefix": "/lex"})
    assert resp.status_code == 200
    assert "/lex/" in resp.text or 'href="/lex' in resp.text


def test_fragment_ids_present(client: TestClient) -> None:
    """Articles have id attributes for fragment URI scrolling."""
    resp = client.get("/eli/ch/101")
    assert 'id="art_1"' in resp.text
    assert 'id="art_2"' in resp.text


def test_paragraph_renders_as_p(client: TestClient) -> None:
    """Simple paragraphs render as <p> for ProseMirror compatibility."""
    resp = client.get("/eli/ch/101")
    assert '<p class="akn-paragraph" id="art_1/para"' in resp.text


def test_numbered_paragraph_has_num_span(client: TestClient) -> None:
    """Numbered paragraphs include num as inline span."""
    resp = client.get("/eli/ch/101")
    assert '<span class="akn-num">' in resp.text
    assert '<p class="akn-paragraph" id="art_2/para_1"' in resp.text


def test_article_header_uses_header_tag(client: TestClient) -> None:
    """Article headers use <header> element, not <div>."""
    resp = client.get("/eli/ch/101")
    assert '<header class="akn-article-header">' in resp.text
    assert '<div class="akn-article-header">' not in resp.text


def test_no_importmap_in_edit_mode(client: TestClient, monkeypatch) -> None:
    """Edit mode loads editor.js directly without importmap."""
    monkeypatch.setattr("lex_web.routes.EDIT_ENABLED", True)
    resp = client.get("/eli/ch/101?edit=1")
    assert "importmap" not in resp.text
    assert "editor.js" in resp.text


# --- Cantonal routes ---


def test_cantonal_law_200(client: TestClient) -> None:
    resp = client.get("/eli/vs/101.1")
    assert resp.status_code == 200
    assert "Verfassung des Kantons Wallis" in resp.text
    assert "(KV)" in resp.text


def test_cantonal_law_not_found(client: TestClient) -> None:
    resp = client.get("/eli/vs/999.999")
    assert resp.status_code == 404


def test_cantonal_law_no_sr_prefix(client: TestClient) -> None:
    """Cantonal laws show plain number, not 'SR' prefix."""
    resp = client.get("/eli/vs/101.1")
    assert "SR 101.1" not in resp.text
    assert ">101.1<" in resp.text


def test_cantonal_index_200(client: TestClient) -> None:
    resp = client.get("/eli/vs/")
    assert resp.status_code == 200
    assert "Kanton Wallis" in resp.text
    assert "Verfassung" in resp.text
    assert "101.1" in resp.text


def test_cantonal_index_has_groups(client: TestClient) -> None:
    resp = client.get("/eli/vs/")
    assert "Gesetz" in resp.text
    assert "Verordnung" in resp.text


# --- Municipal routes ---


def test_municipal_law_200(client: TestClient) -> None:
    resp = client.get("/eli/mun/6172/eg/610.100")
    assert resp.status_code == 200
    assert "Spezialfinanzierungen" in resp.text


def test_path_traversal_blocked(client: TestClient) -> None:
    """Path traversal attempts should be rejected (no escape from DATA_DIR)."""
    resp = client.get("/eli/ch/../../etc/passwd")
    assert resp.status_code == 404


def test_municipal_law_has_articles(client: TestClient) -> None:
    resp = client.get("/eli/mun/6172/eg/610.100")
    assert "art_1" in resp.text
    assert "Art. 1" in resp.text
    assert "Art. 2" in resp.text


def test_municipal_law_not_found(client: TestClient) -> None:
    resp = client.get("/eli/mun/6172/eg/999.999")
    assert resp.status_code == 404


def test_municipal_law_bad_path(client: TestClient) -> None:
    resp = client.get("/eli/mun/6172/nope")
    assert resp.status_code == 404


def test_index_has_municipal_section(client: TestClient) -> None:
    resp = client.get("/")
    assert "Gemeinden" in resp.text
    assert "Bister" in resp.text
    assert "Munizipalgemeinde" in resp.text
    assert "/eli/mun/6172/eg/610.100" in resp.text


# --- FRBR routing: explicit language ---


def test_federal_law_explicit_lang(client: TestClient) -> None:
    """Explicit /de lang segment serves existing German document."""
    resp = client.get("/eli/ch/101/de")
    assert resp.status_code == 200
    assert "Bundesverfassung" in resp.text


def test_federal_law_missing_lang(client: TestClient) -> None:
    """Requesting a non-existent language returns 404."""
    # Municipal laws only have de.xml — fr should 404
    resp = client.get("/eli/mun/6172/eg/610.100/fr")
    assert resp.status_code == 404


def test_cantonal_law_explicit_lang(client: TestClient) -> None:
    resp = client.get("/eli/vs/101.1/de")
    assert resp.status_code == 200
    assert "Verfassung" in resp.text


def test_municipal_law_explicit_lang(client: TestClient) -> None:
    resp = client.get("/eli/mun/6172/eg/610.100/de")
    assert resp.status_code == 200
    assert "Spezialfinanzierungen" in resp.text


def test_invalid_lang_404(client: TestClient) -> None:
    """Invalid language code returns 404."""
    resp = client.get("/eli/ch/101/xx")
    assert resp.status_code == 404


# --- FRBR routing: XML format ---


def test_federal_law_xml(client: TestClient) -> None:
    """XML format returns raw AKN XML."""
    resp = client.get("/eli/ch/101/de/xml")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/xml"
    assert "akomaNtoso" in resp.text


def test_cantonal_law_xml(client: TestClient) -> None:
    resp = client.get("/eli/vs/101.1/de/xml")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/xml"


def test_municipal_law_xml(client: TestClient) -> None:
    resp = client.get("/eli/mun/6172/eg/610.100/de/xml")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/xml"


def test_federal_law_html_explicit(client: TestClient) -> None:
    """Explicit /html format serves the same HTML as bare URI."""
    resp = client.get("/eli/ch/101/de/html")
    assert resp.status_code == 200
    assert "Bundesverfassung" in resp.text
    assert "text/html" in resp.headers["content-type"]


# --- FRBR routing: PDF format (501) ---


def test_federal_law_pdf_501(client: TestClient) -> None:
    """PDF format returns 501 Not Implemented."""
    resp = client.get("/eli/ch/101/de/pdf")
    assert resp.status_code == 501


# --- FRBR routing: invalid format ---


def test_federal_law_invalid_format(client: TestClient) -> None:
    resp = client.get("/eli/ch/101/de/docx")
    assert resp.status_code == 404


# --- FRBR routing: historical date ---


def test_federal_law_historical_version(client: TestClient) -> None:
    """Historical version with existing date subdirectory returns 200."""
    resp = client.get("/eli/ch/101/2024-01-01")
    assert resp.status_code == 200
    assert "Bundesverfassung" in resp.text


def test_federal_law_historical_version_with_lang(client: TestClient) -> None:
    resp = client.get("/eli/ch/101/2024-01-01/de")
    assert resp.status_code == 200


def test_federal_law_historical_version_xml(client: TestClient) -> None:
    resp = client.get("/eli/ch/101/2024-01-01/de/xml")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/xml"


def test_federal_law_nonexistent_date(client: TestClient) -> None:
    """Non-existent date returns 404."""
    resp = client.get("/eli/ch/101/1900-01-01")
    assert resp.status_code == 404


# --- FRBR routing: backward compatibility ---


def test_bare_uris_unchanged(client: TestClient) -> None:
    """Bare URIs (no date/lang/format) still work."""
    resp = client.get("/eli/ch/101")
    assert resp.status_code == 200
    resp = client.get("/eli/vs/101.1")
    assert resp.status_code == 200
    resp = client.get("/eli/mun/6172/eg/610.100")
    assert resp.status_code == 200


# --- FRBR routing: html lang attribute ---


def test_html_lang_attribute_default(client: TestClient) -> None:
    """Default lang attribute is 'de'."""
    resp = client.get("/eli/ch/101")
    assert 'lang="de"' in resp.text


def test_html_lang_attribute_explicit(client: TestClient) -> None:
    """Explicit lang in path sets html lang attribute."""
    resp = client.get("/eli/ch/101/de")
    assert 'lang="de"' in resp.text


# --- /doc/ namespace ---


def test_doc_platform_200(client: TestClient) -> None:
    """Platform-level doc loads."""
    resp = client.get("/doc/terminologie-erlassformen")
    assert resp.status_code == 200


def test_doc_platform_with_lang(client: TestClient) -> None:
    resp = client.get("/doc/terminologie-erlassformen/de")
    assert resp.status_code == 200


def test_doc_platform_xml(client: TestClient) -> None:
    resp = client.get("/doc/terminologie-erlassformen/de/xml")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/xml"


def test_doc_not_found(client: TestClient) -> None:
    resp = client.get("/doc/nonexistent-doc")
    assert resp.status_code == 404


def test_doc_vs_scope_404(client: TestClient) -> None:
    """VS-scoped doc returns 404 (no VS doc data exists)."""
    resp = client.get("/doc/vs/some-guide")
    assert resp.status_code == 404


def test_doc_mun_scope_404(client: TestClient) -> None:
    """Municipal doc returns 404 (no mun doc data exists)."""
    resp = client.get("/doc/mun/6172/eg/leitbild")
    assert resp.status_code == 404


# --- /pub/ namespace ---


def test_pub_valid_structure_404(client: TestClient) -> None:
    """Valid /pub/ URI structure returns 404 (no pub data exists)."""
    resp = client.get("/pub/mun/6172/eg/assembly/protocol/2026/1")
    assert resp.status_code == 404


def test_pub_council_decision_404(client: TestClient) -> None:
    resp = client.get("/pub/mun/6172/eg/council/decision/2026/3")
    assert resp.status_code == 404


def test_pub_invalid_organ_404(client: TestClient) -> None:
    """Invalid organ name returns 404."""
    resp = client.get("/pub/mun/6172/eg/invalidorgan/protocol/2026/1")
    assert resp.status_code == 404


def test_pub_invalid_doctype_404(client: TestClient) -> None:
    """Invalid doctype name returns 404."""
    resp = client.get("/pub/mun/6172/eg/council/invalidtype/2026/1")
    assert resp.status_code == 404


def test_pub_invalid_structure_404(client: TestClient) -> None:
    """Malformed pub URI returns 404."""
    resp = client.get("/pub/mun/6172/eg/council")
    assert resp.status_code == 404


# --- Alias normalization ---


def test_eli_entity_alias_redirect(client: TestClient) -> None:
    """French entity alias 'cm' redirects to canonical 'eg'."""
    resp = client.get("/eli/mun/6172/cm/610.100", follow_redirects=False)
    assert resp.status_code == 301
    assert "/eli/mun/6172/eg/610.100" in resp.headers["location"]


def test_eli_entity_alias_redirect_with_lang(client: TestClient) -> None:
    """Alias redirect preserves lang/format suffixes."""
    resp = client.get("/eli/mun/6172/cm/610.100/de/xml", follow_redirects=False)
    assert resp.status_code == 301
    assert "/eli/mun/6172/eg/610.100/de/xml" in resp.headers["location"]


def test_eli_entity_alias_bg_not_redirected(client: TestClient) -> None:
    """Canonical entity 'bg' is not an alias — no redirect."""
    resp = client.get("/eli/mun/6172/bg/200.1", follow_redirects=False)
    # bg is canonical, so no redirect — just 404 (no data)
    assert resp.status_code == 404


def test_pub_organ_alias_redirect(client: TestClient) -> None:
    """German organ alias 'rat' redirects to canonical 'council'."""
    resp = client.get(
        "/pub/mun/6172/eg/rat/beschluss/2026/1", follow_redirects=False,
    )
    assert resp.status_code == 301
    assert "/pub/mun/6172/eg/council/decision/2026/1" in resp.headers["location"]


def test_pub_entity_alias_redirect(client: TestClient) -> None:
    """French entity alias in /pub/ redirects."""
    resp = client.get(
        "/pub/mun/6172/cm/assembly/protocol/2026/1", follow_redirects=False,
    )
    assert resp.status_code == 301
    assert "/pub/mun/6172/eg/assembly/protocol/2026/1" in resp.headers["location"]


# --- Language cookie ---


def test_lang_cookie_set() -> None:
    """POST /lang sets language cookie."""
    app = create_app()
    with TestClient(app) as c:
        resp = c.post("/lang", data={"lang": "fr"}, follow_redirects=False)
        assert resp.status_code == 303
        assert "lang=fr" in resp.headers.get("set-cookie", "")


def test_lang_cookie_invalid_rejected(client: TestClient) -> None:
    """POST /lang with invalid language returns 400."""
    resp = client.post("/lang", data={"lang": "xx"})
    assert resp.status_code == 400


def test_lang_cookie_affects_resolution() -> None:
    """When lang cookie is set and no explicit lang in path, cookie lang is used."""
    app = create_app()
    with TestClient(app, cookies={"lang": "de"}) as c:
        resp = c.get("/eli/ch/101")
        assert resp.status_code == 200
        assert "Bundesverfassung" in resp.text


def test_lang_cookie_fallback_to_de() -> None:
    """Cookie lang=fr on a German-only doc falls back to de (not 404)."""
    app = create_app()
    with TestClient(app, cookies={"lang": "fr"}) as c:
        # Municipal laws only have de.xml — cookie lang=fr should fall back
        resp = c.get("/eli/mun/6172/eg/610.100")
        assert resp.status_code == 200
        assert "Spezialfinanzierungen" in resp.text


def test_explicit_lang_no_fallback(client: TestClient) -> None:
    """Explicit /fr in path does NOT fall back — 404 if file missing."""
    # Municipal laws only have de.xml — explicit /fr must 404
    resp = client.get("/eli/mun/6172/eg/610.100/fr")
    assert resp.status_code == 404


# --- Language toggle in nav ---


def test_lang_toggle_always_visible(client: TestClient) -> None:
    """Language toggle shows DE/FR/IT on single-lang docs (for UI language)."""
    resp = client.get("/eli/mun/6172/eg/610.100")
    assert resp.status_code == 200
    assert "lang-toggle" in resp.text
    assert 'value="de"' in resp.text
    assert 'value="fr"' in resp.text
    assert 'value="it"' in resp.text


def test_lang_toggle_shown_multi_lang(client: TestClient, tmp_path: Path) -> None:
    """Language toggle shows buttons for each available language."""
    import shutil
    from lex_web import routes

    # Create a temporary data dir with two language files
    ch_dir = tmp_path / "ch" / "101"
    ch_dir.mkdir(parents=True)
    original = routes.DATA_DIR / "ch" / "101" / "de.xml"
    shutil.copy(original, ch_dir / "de.xml")
    shutil.copy(original, ch_dir / "fr.xml")  # fake FR variant

    old_data_dir = routes.DATA_DIR
    routes.DATA_DIR = tmp_path
    routes._doc_cache.clear()
    try:
        with TestClient(create_app()) as c:
            resp = c.get("/eli/ch/101")
        assert resp.status_code == 200
        assert "lang-toggle" in resp.text
        assert 'value="de"' in resp.text
        assert 'value="fr"' in resp.text
    finally:
        routes.DATA_DIR = old_data_dir
        routes._doc_cache.clear()
