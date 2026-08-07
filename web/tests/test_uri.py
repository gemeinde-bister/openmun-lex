"""Tests for lex_akn.uri module."""

import pytest

from lex_akn.uri import (
    DocUri,
    EliUri,
    PubUri,
    build_eli,
    parse_doc,
    parse_eli,
    parse_pub,
)


class TestParseEli:
    def test_federal_bare(self) -> None:
        uri = parse_eli("/eli/ch/101")
        assert uri.level == "ch"
        assert uri.identifier == "101"
        assert uri.date is None
        assert uri.lang is None
        assert uri.format is None
        assert uri.fragment is None

    def test_federal_with_date(self) -> None:
        uri = parse_eli("/eli/ch/101/2024-01-01")
        assert uri.identifier == "101"
        assert uri.date == "2024-01-01"

    def test_federal_full(self) -> None:
        uri = parse_eli("/eli/ch/101/2024-01-01/de/xml")
        assert uri.level == "ch"
        assert uri.identifier == "101"
        assert uri.date == "2024-01-01"
        assert uri.lang == "de"
        assert uri.format == "xml"

    def test_federal_with_fragment(self) -> None:
        uri = parse_eli("/eli/ch/101#art_1")
        assert uri.identifier == "101"
        assert uri.fragment == "art_1"

    def test_federal_full_with_fragment(self) -> None:
        uri = parse_eli("/eli/ch/101/2024-01-01/de/html#art_2__para_1")
        assert uri.date == "2024-01-01"
        assert uri.fragment == "art_2__para_1"

    def test_cantonal_bare(self) -> None:
        uri = parse_eli("/eli/vs/175.1")
        assert uri.level == "vs"
        assert uri.identifier == "175.1"

    def test_cantonal_with_date_and_lang(self) -> None:
        uri = parse_eli("/eli/vs/175.1/2026-01-01/de")
        assert uri.identifier == "175.1"
        assert uri.date == "2026-01-01"
        assert uri.lang == "de"

    def test_municipal(self) -> None:
        uri = parse_eli("/eli/mun/6172/eg/610.100")
        assert uri.level == "mun"
        assert uri.identifier == "6172/eg/610.100"

    def test_municipal_full(self) -> None:
        uri = parse_eli("/eli/mun/6172/eg/610.100/2026-01-01/de/html")
        assert uri.identifier == "6172/eg/610.100"
        assert uri.date == "2026-01-01"
        assert uri.lang == "de"
        assert uri.format == "html"

    def test_municipal_entity_with_slug(self) -> None:
        uri = parse_eli("/eli/mun/6172/bg:oberried/200.1")
        assert uri.level == "mun"
        assert uri.identifier == "6172/bg:oberried/200.1"

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Not a valid ELI URI"):
            parse_eli("/invalid/path")

    def test_sr_with_dots(self) -> None:
        uri = parse_eli("/eli/ch/210")
        assert uri.identifier == "210"


class TestBuildEli:
    def test_federal_bare(self) -> None:
        assert build_eli("ch", "101") == "/eli/ch/101"

    def test_federal_full(self) -> None:
        result = build_eli("ch", "101", date="2024-01-01", lang="de", fmt="xml")
        assert result == "/eli/ch/101/2024-01-01/de/xml"

    def test_with_fragment(self) -> None:
        result = build_eli("ch", "101", fragment="art_1")
        assert result == "/eli/ch/101#art_1"

    def test_municipal(self) -> None:
        result = build_eli("mun", "6172/eg/610.100")
        assert result == "/eli/mun/6172/eg/610.100"


class TestEliUriStr:
    def test_roundtrip_federal(self) -> None:
        original = "/eli/ch/101/2024-01-01/de/xml"
        uri = parse_eli(original)
        assert str(uri) == original

    def test_roundtrip_cantonal(self) -> None:
        original = "/eli/vs/175.1"
        uri = parse_eli(original)
        assert str(uri) == original

    def test_roundtrip_with_fragment(self) -> None:
        original = "/eli/ch/101#art_1"
        uri = parse_eli(original)
        assert str(uri) == original

    def test_path_without_fragment(self) -> None:
        uri = parse_eli("/eli/ch/101#art_1")
        assert uri.path == "/eli/ch/101"


# ===========================================================================
# /doc/ URI tests
# ===========================================================================


class TestParseDoc:
    def test_platform_bare(self) -> None:
        doc = parse_doc("/doc/terminologie-erlassformen")
        assert doc.scope == "platform"
        assert doc.doc_id == "terminologie-erlassformen"
        assert doc.date is None
        assert doc.lang is None
        assert doc.format is None

    def test_platform_with_lang(self) -> None:
        doc = parse_doc("/doc/terminologie-erlassformen/de")
        assert doc.doc_id == "terminologie-erlassformen"
        assert doc.lang == "de"

    def test_platform_with_date_and_lang(self) -> None:
        doc = parse_doc("/doc/terminologie-erlassformen/2026-02-14/de")
        assert doc.date == "2026-02-14"
        assert doc.lang == "de"

    def test_platform_full(self) -> None:
        doc = parse_doc("/doc/terminologie-erlassformen/2026-02-14/de/xml")
        assert doc.date == "2026-02-14"
        assert doc.lang == "de"
        assert doc.format == "xml"

    def test_vs_scope(self) -> None:
        doc = parse_doc("/doc/vs/leitfaden-xyz")
        assert doc.scope == "vs"
        assert doc.doc_id == "leitfaden-xyz"

    def test_vs_with_lang(self) -> None:
        doc = parse_doc("/doc/vs/leitfaden-xyz/fr")
        assert doc.scope == "vs"
        assert doc.lang == "fr"

    def test_bez_scope(self) -> None:
        doc = parse_doc("/doc/bez/2301/some-guide")
        assert doc.scope == "bez"
        assert doc.scope_id == "2301"
        assert doc.doc_id == "some-guide"

    def test_mun_scope(self) -> None:
        doc = parse_doc("/doc/mun/6172/eg/leitbild")
        assert doc.scope == "mun"
        assert doc.scope_id == "6172"
        assert doc.entity == "eg"
        assert doc.doc_id == "leitbild"

    def test_mun_with_lang_and_format(self) -> None:
        doc = parse_doc("/doc/mun/6172/eg/leitbild/de/xml")
        assert doc.scope == "mun"
        assert doc.lang == "de"
        assert doc.format == "xml"

    def test_mun_with_geteilschaft(self) -> None:
        doc = parse_doc("/doc/mun/6172/gt:bisteralpe/alpreglement")
        assert doc.scope == "mun"
        assert doc.entity == "gt:bisteralpe"
        assert doc.doc_id == "alpreglement"

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Not a valid /doc/ URI"):
            parse_doc("/invalid/path")

    def test_invalid_doc_id_with_dots(self) -> None:
        """Systematic numbers (like 1.001) are not valid doc_ids."""
        with pytest.raises(ValueError):
            parse_doc("/doc/1.001")


class TestDocUriStr:
    def test_roundtrip_platform(self) -> None:
        original = "/doc/terminologie-erlassformen"
        assert str(parse_doc(original)) == original

    def test_roundtrip_platform_full(self) -> None:
        original = "/doc/terminologie-erlassformen/2026-02-14/de/xml"
        assert str(parse_doc(original)) == original

    def test_roundtrip_vs(self) -> None:
        original = "/doc/vs/leitfaden-xyz/fr"
        assert str(parse_doc(original)) == original

    def test_roundtrip_mun(self) -> None:
        original = "/doc/mun/6172/eg/leitbild"
        assert str(parse_doc(original)) == original

    def test_work_uri_strips_frbr(self) -> None:
        doc = parse_doc("/doc/terminologie-erlassformen/2026-02-14/de/xml")
        assert doc.work_uri == "/doc/terminologie-erlassformen"

    def test_work_uri_mun(self) -> None:
        doc = parse_doc("/doc/mun/6172/eg/leitbild/de")
        assert doc.work_uri == "/doc/mun/6172/eg/leitbild"


# ===========================================================================
# /pub/ URI tests
# ===========================================================================


class TestParsePub:
    def test_basic(self) -> None:
        pub = parse_pub("/pub/mun/6172/eg/assembly/protocol/2026/1")
        assert pub.bfs == "6172"
        assert pub.entity == "eg"
        assert pub.organ == "assembly"
        assert pub.doctype == "protocol"
        assert pub.year == "2026"
        assert pub.number == "1"
        assert pub.lang is None
        assert pub.format is None

    def test_with_lang(self) -> None:
        pub = parse_pub("/pub/mun/6172/eg/council/decision/2026/3/de")
        assert pub.organ == "council"
        assert pub.doctype == "decision"
        assert pub.lang == "de"

    def test_with_lang_and_format(self) -> None:
        pub = parse_pub("/pub/mun/6172/eg/council/decision/2026/3/de/pdf")
        assert pub.lang == "de"
        assert pub.format == "pdf"

    def test_burgergemeinde(self) -> None:
        pub = parse_pub("/pub/mun/6172/bg/assembly/protocol/2026/1")
        assert pub.entity == "bg"
        assert pub.organ == "assembly"

    def test_geteilschaft(self) -> None:
        pub = parse_pub("/pub/mun/6172/gt:bisteralpe/assembly/protocol/2026/1")
        assert pub.entity == "gt:bisteralpe"

    def test_multi_digit_number(self) -> None:
        pub = parse_pub("/pub/mun/6172/eg/council/decision/2026/12")
        assert pub.number == "12"

    def test_aliased_organ_parses(self) -> None:
        """Aliased organ names should parse (handler does the redirect)."""
        pub = parse_pub("/pub/mun/6172/eg/rat/beschluss/2026/1")
        assert pub.organ == "rat"
        assert pub.doctype == "beschluss"

    def test_invalid_structure_raises(self) -> None:
        with pytest.raises(ValueError, match="Not a valid /pub/ URI"):
            parse_pub("/pub/mun/6172/eg/council")  # missing doctype/year/number

    def test_invalid_no_mun_prefix(self) -> None:
        with pytest.raises(ValueError):
            parse_pub("/pub/6172/eg/council/decision/2026/1")


class TestPubUriStr:
    def test_roundtrip(self) -> None:
        original = "/pub/mun/6172/eg/assembly/protocol/2026/1"
        assert str(parse_pub(original)) == original

    def test_roundtrip_with_lang_format(self) -> None:
        original = "/pub/mun/6172/eg/council/decision/2026/3/de/pdf"
        assert str(parse_pub(original)) == original

    def test_work_uri(self) -> None:
        pub = parse_pub("/pub/mun/6172/eg/council/decision/2026/3/de/pdf")
        assert pub.work_uri == "/pub/mun/6172/eg/council/decision/2026/3"
