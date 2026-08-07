"""Tests for lex_sync.convert module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lxml import etree

from lex_sync.convert import (
    AKN_NS,
    ConvertResult,
    DocumentMeta,
    convert_document,
    parse_abrogated_date,
    parse_in_force_date,
    serialize,
    _extract_article_number,
    _extract_enum_content,
    _extract_header_field,
    _extract_para_number,
    _extract_para_text,
    _strip_html,
)

FIXTURES = Path(__file__).parent / "fixtures"
NS = {"akn": AKN_NS}


@pytest.fixture(scope="module")
def gemg_response() -> dict:
    with open(FIXTURES / "gemg_subset.json") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def gemg_result(gemg_response: dict) -> ConvertResult:
    return convert_document(gemg_response, lang="de")


@pytest.fixture(scope="module")
def gemg_xml(gemg_result: ConvertResult) -> etree._Element:
    return gemg_result.xml


@pytest.fixture(scope="module")
def gemg_meta(gemg_result: ConvertResult) -> DocumentMeta:
    return gemg_result.meta


# --- HTML extraction ---


def test_strip_html_basic() -> None:
    assert _strip_html("<b>hello</b>") == "hello"


def test_strip_html_entities() -> None:
    assert _strip_html("Art.&nbsp;1") == "Art.\xa01"


def test_strip_html_umlaut() -> None:
    assert _strip_html("&ouml;ffentlich") == "öffentlich"


def test_strip_html_sup() -> None:
    assert _strip_html("2<sup>bis</sup>") == "2bis"


def test_extract_article_number_from_uid() -> None:
    node = {"uid": "t-0--t-1--t-1\u20101--a-14"}
    assert _extract_article_number(node, "de") == "14"


def test_extract_article_number_simple() -> None:
    node = {"uid": "t-0--a-3"}
    assert _extract_article_number(node, "de") == "3"


def test_extract_para_number_plain() -> None:
    node = {"html_content": {"de": "<span class='number'>1</span>"}}
    assert _extract_para_number(node, "de") == "1"


def test_extract_para_number_suffix() -> None:
    node = {"html_content": {"de": "<span class='number'>2<sup>bis</sup></span>"}}
    assert _extract_para_number(node, "de") == "2bis"


def test_extract_para_text() -> None:
    node = {"html_content": {
        "de": "<div class='paragraph'><span class='number'>1</span>"
              "<p><span class='text_content'>Hello world</span></p></div>"
    }}
    assert _extract_para_text(node, "de") == "Hello world"


def test_extract_para_text_strips_amendment_marker() -> None:
    node = {"html_content": {
        "de": "<span class='text_content'>Some text *</span>"
    }}
    assert _extract_para_text(node, "de") == "Some text"


def test_extract_enum_content() -> None:
    node = {"html_content": {
        "de": "<table class='enumeration_item'><tr>"
              "<td class='number'>a)</td>"
              "<td class='left_col last' colspan='3'>die Einwohnergemeinden;</td>"
              "</tr></table>"
    }}
    letter, text = _extract_enum_content(node, "de")
    assert letter == "a)"
    assert text == "die Einwohnergemeinden;"


# --- Document structure ---


def test_convert_no_warnings(gemg_result: ConvertResult) -> None:
    assert gemg_result.warnings == []


def test_root_is_akoma_ntoso(gemg_xml: etree._Element) -> None:
    assert gemg_xml.tag == f"{{{AKN_NS}}}akomaNtoso"


def test_has_act(gemg_xml: etree._Element) -> None:
    act = gemg_xml.find("akn:act", NS)
    assert act is not None
    assert act.get("name") == "publicLaw"


def test_has_meta(gemg_xml: etree._Element) -> None:
    meta = gemg_xml.find(".//akn:meta", NS)
    assert meta is not None


def test_has_body(gemg_xml: etree._Element) -> None:
    body = gemg_xml.find(".//akn:body", NS)
    assert body is not None


# --- Meta ---


def test_meta_frbr_work(gemg_xml: etree._Element) -> None:
    work = gemg_xml.find(".//akn:FRBRWork", NS)
    assert work is not None

    this = work.find("akn:FRBRthis", NS)
    assert this is not None
    assert this.get("value") == "/eli/vs/175.1"


def test_meta_frbr_number(gemg_xml: etree._Element) -> None:
    number = gemg_xml.find(".//akn:FRBRnumber", NS)
    assert number is not None
    assert number.get("value") == "175.1"


def test_meta_frbr_language(gemg_xml: etree._Element) -> None:
    lang = gemg_xml.find(".//akn:FRBRlanguage", NS)
    assert lang is not None
    assert lang.get("language") == "de"


def test_meta_country(gemg_xml: etree._Element) -> None:
    country = gemg_xml.find(".//akn:FRBRcountry", NS)
    assert country is not None
    assert country.get("value") == "CH-VS"


def test_meta_abbreviation(gemg_xml: etree._Element) -> None:
    aliases = gemg_xml.findall(".//akn:FRBRalias", NS)
    abbr = [a for a in aliases if a.get("name") == "abbreviation"]
    assert len(abbr) == 1
    assert abbr[0].get("value") == "GemG"


# --- Dates ---


def test_meta_work_decision_date(gemg_xml: etree._Element) -> None:
    work = gemg_xml.find(".//akn:FRBRWork", NS)
    dates = work.findall("akn:FRBRdate", NS)
    decision = [d for d in dates if d.get("name") == "decision"]
    assert len(decision) == 1
    assert decision[0].get("date") == "2004-02-05"


def test_meta_work_enactment_date(gemg_xml: etree._Element) -> None:
    work = gemg_xml.find(".//akn:FRBRWork", NS)
    dates = work.findall("akn:FRBRdate", NS)
    enactment = [d for d in dates if d.get("name") == "enactment"]
    assert len(enactment) == 1
    assert enactment[0].get("date") == "2004-07-01"


def test_meta_expr_in_force_date(gemg_xml: etree._Element) -> None:
    expr = gemg_xml.find(".//akn:FRBRExpression", NS)
    dates = expr.findall("akn:FRBRdate", NS)
    in_force = [d for d in dates if d.get("name") == "inForceSince"]
    assert len(in_force) == 1
    assert in_force[0].get("date") == "2023-01-01"


def test_meta_version_hash(gemg_xml: etree._Element) -> None:
    expr = gemg_xml.find(".//akn:FRBRExpression", NS)
    aliases = expr.findall("akn:FRBRalias", NS)
    vh = [a for a in aliases if a.get("name") == "versionHash"]
    assert len(vh) == 1
    assert vh[0].get("value") == "3755d9213407ec27e6beb366118da90a"


def test_meta_version_id(gemg_xml: etree._Element) -> None:
    expr = gemg_xml.find(".//akn:FRBRExpression", NS)
    aliases = expr.findall("akn:FRBRalias", NS)
    vid = [a for a in aliases if a.get("name") == "versionId"]
    assert len(vid) == 1
    assert vid[0].get("value") == "3103"


# --- Articles ---


def test_articles_present(gemg_xml: etree._Element) -> None:
    articles = gemg_xml.findall(".//akn:article", NS)
    assert len(articles) == 4


def test_article_has_eid(gemg_xml: etree._Element) -> None:
    article = gemg_xml.find(".//akn:article", NS)
    assert article is not None
    assert article.get("eId") == "art_1"


def test_article_has_num(gemg_xml: etree._Element) -> None:
    article = gemg_xml.find(".//akn:article", NS)
    num = article.find("akn:num", NS)
    assert num is not None
    # Num contains <b> child
    b = num.find("akn:b", NS)
    assert b is not None
    assert "Art." in b.text


def test_article_has_heading(gemg_xml: etree._Element) -> None:
    article = gemg_xml.find(".//akn:article", NS)
    heading = article.find("akn:heading", NS)
    assert heading is not None
    assert heading.text == "Geltungsbereich"


# --- Paragraphs ---


def test_paragraphs_present(gemg_xml: etree._Element) -> None:
    paragraphs = gemg_xml.findall(".//akn:paragraph", NS)
    assert len(paragraphs) == 10


def test_paragraph_eid_multi(gemg_xml: etree._Element) -> None:
    """Multi-paragraph article: eId has numbered suffix."""
    para = gemg_xml.find(".//akn:paragraph[@eId='art_1/para_1']", NS)
    assert para is not None


def test_paragraph_eid_single(gemg_xml: etree._Element) -> None:
    """Single-paragraph article: eId without number."""
    # Art 3 in fixture (first article inside the section) - check if it's single-para
    articles = gemg_xml.findall(".//akn:article", NS)
    for art in articles:
        paras = art.findall("akn:paragraph", NS)
        if len(paras) == 1:
            eid = paras[0].get("eId", "")
            assert eid.endswith("/para"), f"Single-para eId should end with /para, got {eid}"
            return
    pytest.skip("No single-paragraph article in fixture")


def test_paragraph_has_num(gemg_xml: etree._Element) -> None:
    para = gemg_xml.find(".//akn:paragraph[@eId='art_1/para_1']", NS)
    num = para.find("akn:num", NS)
    assert num is not None
    assert num.text == "1"


def test_paragraph_has_content(gemg_xml: etree._Element) -> None:
    para = gemg_xml.find(".//akn:paragraph[@eId='art_1/para_2']", NS)
    content = para.find("akn:content", NS)
    assert content is not None
    p = content.find("akn:p", NS)
    assert p is not None
    assert "Gesetz" in p.text


# --- Enumerations ---


def test_block_list_present(gemg_xml: etree._Element) -> None:
    bl = gemg_xml.find(".//akn:blockList", NS)
    assert bl is not None


def test_block_list_has_items(gemg_xml: etree._Element) -> None:
    bl = gemg_xml.find(".//akn:blockList", NS)
    items = bl.findall("akn:item", NS)
    assert len(items) >= 2


def test_item_has_eid(gemg_xml: etree._Element) -> None:
    item = gemg_xml.find(".//akn:item", NS)
    assert item is not None
    eid = item.get("eId", "")
    assert "/lbl_" in eid


def test_item_has_num_and_text(gemg_xml: etree._Element) -> None:
    item = gemg_xml.find(".//akn:item", NS)
    num = item.find("akn:num", NS)
    assert num is not None
    assert "a" in num.text

    p = item.find("akn:p", NS)
    assert p is not None
    assert len(p.text) > 0


def test_list_introduction(gemg_xml: etree._Element) -> None:
    intro = gemg_xml.find(".//akn:listIntroduction", NS)
    assert intro is not None
    assert len(intro.text.strip()) > 0


# --- Structural hierarchy ---


def test_has_title(gemg_xml: etree._Element) -> None:
    titles = gemg_xml.findall(".//akn:title", NS)
    assert len(titles) >= 1


def test_title_has_num_and_heading(gemg_xml: etree._Element) -> None:
    title = gemg_xml.find(".//akn:title", NS)
    num = title.find("akn:num", NS)
    heading = title.find("akn:heading", NS)
    assert num is not None
    assert heading is not None
    assert heading.text == "Organisation"


def test_has_chapter(gemg_xml: etree._Element) -> None:
    chapters = gemg_xml.findall(".//akn:chapter", NS)
    assert len(chapters) >= 1


def test_chapter_eid_nested(gemg_xml: etree._Element) -> None:
    chapter = gemg_xml.find(".//akn:chapter", NS)
    eid = chapter.get("eId", "")
    assert eid.startswith("tit_")
    assert "/chap_" in eid


def test_has_section(gemg_xml: etree._Element) -> None:
    sections = gemg_xml.findall(".//akn:section", NS)
    assert len(sections) >= 1


# --- Serialization ---


def test_serialize_produces_xml(gemg_xml: etree._Element) -> None:
    xml_bytes = serialize(gemg_xml)
    assert xml_bytes.startswith(b"<?xml")
    assert b"akomaNtoso" in xml_bytes


def test_serialize_roundtrips(gemg_xml: etree._Element) -> None:
    """Serialized XML can be parsed back."""
    xml_bytes = serialize(gemg_xml)
    parsed = etree.fromstring(xml_bytes)
    assert parsed.tag == f"{{{AKN_NS}}}akomaNtoso"
    articles = parsed.findall(".//{%s}article" % AKN_NS)
    assert len(articles) == 4


# --- Preamble ---


def test_preamble_present(gemg_xml: etree._Element) -> None:
    preamble = gemg_xml.find(".//akn:preamble", NS)
    assert preamble is not None


def test_preamble_enacting_authority(gemg_xml: etree._Element) -> None:
    formula = gemg_xml.find(".//akn:preamble/akn:formula[@name='enactingAuthority']", NS)
    assert formula is not None
    p = formula.find("akn:p", NS)
    assert p is not None
    assert "Grosse Rat" in p.text


def test_preamble_recitals(gemg_xml: etree._Element) -> None:
    recitals = gemg_xml.find(".//akn:preamble/akn:recitals", NS)
    assert recitals is not None
    recs = recitals.findall("akn:recital", NS)
    assert len(recs) == 2
    # First recital: legal basis
    p1 = recs[0].find("akn:p", NS)
    assert "Artikel 31" in p1.text
    # Second recital: proposal
    p2 = recs[1].find("akn:p", NS)
    assert "Staatsrat" in p2.text


def test_preamble_recital_eids(gemg_xml: etree._Element) -> None:
    recitals = gemg_xml.findall(".//akn:recital", NS)
    eids = [r.get("eId") for r in recitals]
    assert eids == ["rec_1", "rec_2"]


def test_preamble_enacting_formula(gemg_xml: etree._Element) -> None:
    formula = gemg_xml.find(".//akn:preamble/akn:formula[@name='enactingFormula']", NS)
    assert formula is not None
    p = formula.find("akn:p", NS)
    assert p.text == "verordnet:"


def test_preamble_before_body(gemg_xml: etree._Element) -> None:
    """Preamble must appear between meta and body in the act."""
    act = gemg_xml.find("akn:act", NS)
    children = [_local_name(c) for c in act]
    assert children == ["meta", "preamble", "body"]


def _local_name(el: etree._Element) -> str:
    tag = el.tag
    return tag.split("}", 1)[1] if "}" in tag else tag


# --- Header field extraction ---


def test_extract_header_field_author() -> None:
    h = "<div class='ingress_author'>\n  Der Grosse Rat\n</div>"
    assert _extract_header_field(h, "ingress_author") == "Der Grosse Rat"


def test_extract_header_field_missing() -> None:
    assert _extract_header_field("<div>nothing</div>", "ingress_author") == ""


# --- Languages ---


def test_meta_available_languages(gemg_xml: etree._Element) -> None:
    expr = gemg_xml.find(".//akn:FRBRExpression", NS)
    langs = expr.findall("akn:FRBRlanguage", NS)
    lang_codes = [l.get("language") for l in langs]
    assert "de" in lang_codes
    assert "fr" in lang_codes


# --- Lifecycle ---


def test_lifecycle_present(gemg_xml: etree._Element) -> None:
    lifecycle = gemg_xml.find(".//akn:lifecycle", NS)
    assert lifecycle is not None


def test_lifecycle_enactment_event(gemg_xml: etree._Element) -> None:
    evt = gemg_xml.find(".//akn:eventRef[@eId='evt_enactment']", NS)
    assert evt is not None
    assert evt.get("date") == "2004-07-01"
    assert evt.get("type") == "generation"


def test_lifecycle_inforce_event(gemg_xml: etree._Element) -> None:
    evt = gemg_xml.find(".//akn:eventRef[@eId='evt_inforce']", NS)
    assert evt is not None
    assert evt.get("date") == "2023-01-01"
    assert evt.get("type") == "amendment"


def test_lifecycle_no_repeal_for_active_law(gemg_xml: etree._Element) -> None:
    evt = gemg_xml.find(".//akn:eventRef[@eId='evt_repeal']", NS)
    assert evt is None


# --- Sidecar metadata ---


def test_meta_law_type(gemg_meta: DocumentMeta) -> None:
    assert gemg_meta.law_type == "Gesetz"


def test_meta_pdf_links(gemg_meta: DocumentMeta) -> None:
    assert "pdf_file_with_annexes" in gemg_meta.pdf_link
    assert "pdf_file" in gemg_meta.pdf_link_tol
    assert gemg_meta.pdf_link_tol_size == 783811


def test_meta_available_langs(gemg_meta: DocumentMeta) -> None:
    assert gemg_meta.available_languages == ["de", "fr"]


def test_meta_not_abrogated(gemg_meta: DocumentMeta) -> None:
    assert gemg_meta.abrogated is False
    assert gemg_meta.abrogated_scheduled is False


def test_meta_old_versions(gemg_meta: DocumentMeta) -> None:
    assert len(gemg_meta.old_versions) == 2
    v = gemg_meta.old_versions[0]
    assert v.version_id == 2863
    assert v.structured_document_id == 9679
    assert "01.05.2021" in v.version_dates_str


def test_meta_change_documents(gemg_meta: DocumentMeta) -> None:
    assert len(gemg_meta.change_documents) == 1
    cd = gemg_meta.change_documents[0]
    assert cd.number == "2021-026"
    assert "pdf_file" in cd.pdf_link


def test_meta_materials(gemg_meta: DocumentMeta) -> None:
    assert len(gemg_meta.materials) == 1
    m = gemg_meta.materials[0]
    assert "EGZGB" in m.title
    assert "materials/599" in m.url


# --- parse_in_force_date ---


def test_parse_in_force_date_current() -> None:
    """Current version format: 'seit: DD.MM.YYYY'."""
    s = "Aktuelle Version in Kraft seit: 01.01.2023 (Beschlussdatum: 17.12.2020)"
    assert parse_in_force_date(s) == "2023-01-01"


def test_parse_in_force_date_old() -> None:
    """Old version format: 'von: DD.MM.YYYY bis: DD.MM.YYYY'."""
    s = "Version in Kraft von: 01.05.2021 bis: 31.12.2022 (Beschlussdatum: 15.09.2011)"
    assert parse_in_force_date(s) == "2021-05-01"


def test_parse_in_force_date_old_earliest() -> None:
    """Oldest version with different date."""
    s = "Version in Kraft von: 01.01.2012 bis: 30.04.2021 (Beschlussdatum: 15.09.2011)"
    assert parse_in_force_date(s) == "2012-01-01"


def test_parse_abrogated_date_extracts_iso() -> None:
    assert parse_abrogated_date("Aufgehoben am: 28.02.2026") == "2026-02-28"


def test_parse_abrogated_date_none_when_absent() -> None:
    """No date present → None (never invent a repeal date)."""
    assert parse_abrogated_date("aufgehoben") is None
    assert parse_abrogated_date("") is None


def test_parse_in_force_date_raises_on_garbage() -> None:
    with pytest.raises(ValueError, match="Cannot extract in-force date"):
        parse_in_force_date("no date here")


def test_parse_in_force_date_raises_on_empty() -> None:
    with pytest.raises(ValueError, match="Cannot extract in-force date"):
        parse_in_force_date("")


# --- in_force_date override ---


def test_in_force_date_override(gemg_response: dict) -> None:
    """in_force_date overrides publication_enactment in FRBR metadata."""
    result = convert_document(gemg_response, lang="de", in_force_date="2021-05-01")
    expr = result.xml.find(".//akn:FRBRExpression", NS)
    dates = expr.findall("akn:FRBRdate", NS)
    in_force = [d for d in dates if d.get("name") == "inForceSince"]
    assert len(in_force) == 1
    assert in_force[0].get("date") == "2021-05-01"


def test_in_force_date_override_lifecycle(gemg_response: dict) -> None:
    """in_force_date override also affects lifecycle evt_inforce."""
    result = convert_document(gemg_response, lang="de", in_force_date="2021-05-01")
    evt = result.xml.find(".//akn:eventRef[@eId='evt_inforce']", NS)
    assert evt is not None
    assert evt.get("date") == "2021-05-01"


def test_in_force_date_none_uses_original(gemg_response: dict) -> None:
    """Without override, uses original publication_enactment."""
    result = convert_document(gemg_response, lang="de", in_force_date=None)
    expr = result.xml.find(".//akn:FRBRExpression", NS)
    dates = expr.findall("akn:FRBRdate", NS)
    in_force = [d for d in dates if d.get("name") == "inForceSince"]
    assert len(in_force) == 1
    assert in_force[0].get("date") == "2023-01-01"


# --- Language-specific title and abbreviation ---


def test_fr_title_in_frbr(gemg_response: dict) -> None:
    """French conversion uses French title from header, not German."""
    result = convert_document(gemg_response, lang="fr")
    aliases = result.xml.findall(".//akn:FRBRWork/akn:FRBRalias", NS)
    title_alias = [a for a in aliases if a.get("name") == "title"]
    assert len(title_alias) == 1
    assert title_alias[0].get("value") == "Loi sur les communes"


def test_fr_abbreviation_in_frbr(gemg_response: dict) -> None:
    """French conversion uses French abbreviation from header."""
    result = convert_document(gemg_response, lang="fr")
    aliases = result.xml.findall(".//akn:FRBRWork/akn:FRBRalias", NS)
    abbr_alias = [a for a in aliases if a.get("name") == "abbreviation"]
    assert len(abbr_alias) == 1
    assert abbr_alias[0].get("value") == "LCo"


def test_de_title_in_frbr(gemg_response: dict) -> None:
    """German conversion uses German title from header."""
    result = convert_document(gemg_response, lang="de")
    aliases = result.xml.findall(".//akn:FRBRWork/akn:FRBRalias", NS)
    title_alias = [a for a in aliases if a.get("name") == "title"]
    assert len(title_alias) == 1
    assert title_alias[0].get("value") == "Gemeindegesetz"


def test_de_abbreviation_in_frbr(gemg_response: dict) -> None:
    """German conversion uses German abbreviation from header."""
    result = convert_document(gemg_response, lang="de")
    aliases = result.xml.findall(".//akn:FRBRWork/akn:FRBRalias", NS)
    abbr_alias = [a for a in aliases if a.get("name") == "abbreviation"]
    assert len(abbr_alias) == 1
    assert abbr_alias[0].get("value") == "GemG"
