"""Tests for AKN element → HTML rendering (render.py).

Tests cover:
- Dispatch dict completeness against known corpus elements
- Individual element handlers via synthetic AKN fragments
- render_body integration with real data files (when available)
"""

from __future__ import annotations

import pytest

from lex_akn.parse import AKN_NS, parse_string
from lex_web.render import DISPATCH, render_body, render_element

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_act(body_xml: str) -> object:
    """Build a minimal AKN act tree from body XML fragment."""
    xml = (
        f'<akomaNtoso xmlns="{AKN_NS}">'
        f'<act><body>{body_xml}</body></act>'
        f'</akomaNtoso>'
    )
    return parse_string(xml.encode())


def _make_act_full(act_xml: str) -> object:
    """Build a minimal AKN tree with full act content."""
    xml = (
        f'<akomaNtoso xmlns="{AKN_NS}">'
        f'<act>{act_xml}</act>'
        f'</akomaNtoso>'
    )
    return parse_string(xml.encode())


# ---------------------------------------------------------------------------
# Completeness: dispatch covers all corpus elements
# ---------------------------------------------------------------------------

# All 122 unique tag local names found in the 10K+ doc corpus
# (federal ch + cantonal vs + municipal mun)
CORPUS_ELEMENTS = {
    # Core structural
    "p", "num", "heading", "content", "paragraph", "article", "item",
    "blockList", "level", "section", "chapter", "title", "part", "book",
    "subsection", "subchapter", "subdivision", "transitional", "proviso",
    "disposition", "hcontainer",
    # Inline formatting
    "b", "i", "sup", "sub", "u", "s", "br", "ref", "span", "inline",
    "placeholder", "img", "eol",
    # Tables
    "table", "tr", "td", "th",
    # Preface / preamble / conclusions
    "preface", "preamble", "formula", "recitals", "recital",
    "conclusions", "signature", "role", "person",
    # Document parts
    "body", "act", "akomaNtoso",
    "preface", "container", "block", "blockContainer",
    "subheading", "intro", "wrapUp",
    "docTitle", "docNumber",
    # Metadata
    "meta", "identification", "references",
    "FRBRWork", "FRBRExpression", "FRBRManifestation",
    "FRBRcountry", "FRBRdate", "FRBRname", "FRBRprescriptive",
    "FRBRauthoritative", "FRBRlanguage", "FRBRuri", "FRBRalias",
    "FRBRnumber", "FRBRthis", "FRBRauthor", "FRBRformat",
    "TLCOrganization", "TLCRole", "TLCReference",
    "publication", "lifecycle", "eventRef",
    "componentRef",
    # Authorial notes
    "authorialNote", "noteRef",
    # Modification
    "mod", "quotedStructure", "ins", "del",
    # Foreign content
    "foreign",
    # Components
    "components", "component", "doc", "mainBody",
    # Lists
    "listIntroduction", "listWrapUp",
    # Word artifacts
    "AlternateContent", "Choice", "Fallback",
    "break", "orig", "clone", "cr", "delText",
    "smartTagPr", "attr", "moveFromRangeStart", "moveFromRangeEnd",
    "unknown",
    # HTML passthrough
    "h5",
    # SVG (inside foreign)
    "svg", "g", "path", "rect", "circle",
    # MathML (inside foreign)
    "mi", "mrow", "mo", "mn", "msup", "msub", "mfrac", "mtext",
    "munderover", "msubsup", "mroot", "mover",
    # Custom Fedlex
    "heading-info", "heading-annex", "foreign-block",
    # Additional metadata found in corpus
    "FRBRCountry",
    "classification", "classificationItem", "keyword", "proprietary",
    "temporalData", "temporalGroup", "timeInterval",
    "TLCEvent", "TLCLocation", "TLCConcept", "TLCTerm",
    "TLCObject", "TLCProcess",
    "FRBRsubtype",
    # Additional inline
    "docDate", "shortTitle", "date", "term", "organization",
    "location", "concept", "def", "remark",
    "eop", "quotedText",
}


def test_dispatch_covers_all_corpus_elements():
    """Every element found in the corpus must have an explicit dispatch entry."""
    unhandled = CORPUS_ELEMENTS - set(DISPATCH.keys())
    assert unhandled == set(), f"Unhandled elements: {sorted(unhandled)}"


def test_dispatch_has_no_none_handlers():
    """Every dispatch entry must map to a callable, never None."""
    for tag, handler in DISPATCH.items():
        assert handler is not None, f"DISPATCH[{tag!r}] is None"
        assert callable(handler), f"DISPATCH[{tag!r}] is not callable"


# ---------------------------------------------------------------------------
# Structural elements
# ---------------------------------------------------------------------------

class TestStructural:
    def test_chapter_with_heading(self):
        tree = _make_act(
            '<chapter eId="chap_1">'
            '<num>1. Kapitel</num>'
            '<heading>Allgemeine Bestimmungen</heading>'
            '</chapter>'
        )
        html = render_body(tree)
        assert 'class="akn-chapter' in html
        assert "1. Kapitel Allgemeine Bestimmungen" in html
        assert 'id="chap_1"' in html

    def test_article_with_paragraph(self):
        tree = _make_act(
            '<article eId="art_1">'
            '<num>Art. 1</num>'
            '<heading>Zweck</heading>'
            '<paragraph eId="art_1__para_1">'
            '<content><p>Dieses Gesetz regelt...</p></content>'
            '</paragraph>'
            '</article>'
        )
        html = render_body(tree)
        assert 'class="akn-article"' in html
        assert "Art. 1 Zweck" in html
        assert "Dieses Gesetz regelt..." in html

    def test_repealed_article(self):
        tree = _make_act(
            '<article eId="art_2">'
            '<num>Art. 2</num>'
            '</article>'
        )
        html = render_body(tree)
        assert "akn-article-repealed" in html

    def test_subchapter(self):
        tree = _make_act(
            '<subchapter eId="subchap_1">'
            '<num>1a</num>'
            '<heading>Unterkapitel</heading>'
            '</subchapter>'
        )
        html = render_body(tree)
        assert 'class="akn-subchapter' in html

    def test_subdivision(self):
        tree = _make_act(
            '<subdivision eId="subdiv_1">'
            '<num>A</num>'
            '<heading>Erster Teil</heading>'
            '</subdivision>'
        )
        html = render_body(tree)
        assert 'class="akn-subdivision' in html


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class TestTables:
    def test_simple_table(self):
        tree = _make_act(
            '<article eId="art_1">'
            '<paragraph eId="art_1__para_1"><content>'
            '<table><tr><th>Kolonne A</th><th>Kolonne B</th></tr>'
            '<tr><td>Wert 1</td><td>Wert 2</td></tr></table>'
            '</content></paragraph>'
            '</article>'
        )
        html = render_body(tree)
        assert '<table class="akn-table">' in html
        assert "<th>Kolonne A</th>" in html
        assert "<td>" in html
        assert "Wert 1" in html

    def test_table_with_colspan(self):
        tree = _make_act(
            '<article eId="art_1"><paragraph eId="art_1__para_1"><content>'
            '<table><tr><td colspan="2">Merged</td></tr></table>'
            '</content></paragraph></article>'
        )
        html = render_body(tree)
        assert 'colspan="2"' in html

    def test_table_with_p_in_cell(self):
        tree = _make_act(
            '<article eId="art_1"><paragraph eId="art_1__para_1"><content>'
            '<table><tr><td><p>Line 1</p><p>Line 2</p></td></tr></table>'
            '</content></paragraph></article>'
        )
        html = render_body(tree)
        assert "<p>Line 1</p>" in html
        assert "<p>Line 2</p>" in html


# ---------------------------------------------------------------------------
# Inline elements
# ---------------------------------------------------------------------------

class TestInline:
    def test_bold_italic(self):
        tree = _make_act(
            '<article eId="art_1"><paragraph eId="art_1__para_1"><content>'
            '<p><b>fett</b> und <i>kursiv</i></p>'
            '</content></paragraph></article>'
        )
        html = render_body(tree)
        assert "<b>fett</b>" in html
        assert "<i>kursiv</i>" in html

    def test_ref_link(self):
        tree = _make_act(
            '<article eId="art_1"><paragraph eId="art_1__para_1"><content>'
            '<p>Siehe <ref href="https://example.com">Art. 5</ref></p>'
            '</content></paragraph></article>'
        )
        html = render_body(tree)
        assert 'href="https://example.com"' in html
        assert "Art. 5" in html
        assert 'class="akn-ref"' in html

    def test_authorial_note(self):
        tree = _make_act(
            '<article eId="art_1"><paragraph eId="art_1__para_1"><content>'
            '<p>Text<authorialNote><p>Footnote text</p></authorialNote></p>'
            '</content></paragraph></article>'
        )
        html = render_body(tree)
        assert 'class="akn-footnote"' in html
        assert "Footnote text" in html


# ---------------------------------------------------------------------------
# BlockList
# ---------------------------------------------------------------------------

class TestBlockList:
    def test_blocklist_with_items(self):
        tree = _make_act(
            '<article eId="art_1"><paragraph eId="art_1__para_1"><content>'
            '<blockList>'
            '<listIntroduction>Folgende Sachen:</listIntroduction>'
            '<item eId="item_a"><num>a.</num><p>Erster Punkt</p></item>'
            '<item eId="item_b"><num>b.</num><p>Zweiter Punkt</p></item>'
            '</blockList>'
            '</content></paragraph></article>'
        )
        html = render_body(tree)
        assert "akn-list" in html
        assert "akn-item" in html
        assert "Erster Punkt" in html
        assert "Zweiter Punkt" in html


# ---------------------------------------------------------------------------
# Preface / Preamble
# ---------------------------------------------------------------------------

class TestPrefacePreamble:
    def test_preface(self):
        tree = _make_act_full(
            '<preface><p>Bundesgesetz</p></preface>'
            '<body></body>'
        )
        html = render_body(tree)
        assert 'class="akn-preface"' in html
        assert "Bundesgesetz" in html

    def test_preamble_with_formula(self):
        tree = _make_act_full(
            '<preamble>'
            '<formula name="enactingAuthority"><p>Die Bundesversammlung</p></formula>'
            '<formula name="enactingFormula"><p>beschliesst:</p></formula>'
            '</preamble>'
            '<body></body>'
        )
        html = render_body(tree)
        assert "akn-formula-enactingAuthority" in html
        assert "Die Bundesversammlung" in html
        assert "beschliesst:" in html


# ---------------------------------------------------------------------------
# Components (annexes) — the original bug fix
# ---------------------------------------------------------------------------

class TestComponents:
    def test_components_rendered(self):
        tree = _make_act_full(
            '<body>'
            '<article eId="art_1"><paragraph eId="art_1__para_1">'
            '<content><p>Main body text</p></content>'
            '</paragraph></article>'
            '</body>'
            '<components>'
            '<component>'
            '<doc>'
            '<preface><p>Anhang 1</p></preface>'
            '<mainBody><p>Annex content here</p></mainBody>'
            '</doc>'
            '</component>'
            '</components>'
        )
        html = render_body(tree)
        assert "Main body text" in html
        assert "akn-components" in html
        assert "akn-component" in html
        assert "Anhang 1" in html
        assert "Annex content here" in html

    def test_multiple_components(self):
        tree = _make_act_full(
            '<body></body>'
            '<components>'
            '<component><doc><preface><p>Anhang A</p></preface>'
            '<mainBody><p>Content A</p></mainBody></doc></component>'
            '<component><doc><preface><p>Anhang B</p></preface>'
            '<mainBody><p>Content B</p></mainBody></doc></component>'
            '</components>'
        )
        html = render_body(tree)
        assert "Anhang A" in html
        assert "Content A" in html
        assert "Anhang B" in html
        assert "Content B" in html


# ---------------------------------------------------------------------------
# Modification elements
# ---------------------------------------------------------------------------

class TestModification:
    def test_quoted_structure(self):
        tree = _make_act(
            '<article eId="art_1"><paragraph eId="art_1__para_1"><content>'
            '<mod><p>Art. 5 wird wie folgt geändert:</p>'
            '<quotedStructure>'
            '<article eId="art_5"><paragraph eId="art_5__para_1">'
            '<content><p>Neuer Text</p></content>'
            '</paragraph></article>'
            '</quotedStructure></mod>'
            '</content></paragraph></article>'
        )
        html = render_body(tree)
        assert "akn-mod" in html
        assert "akn-quoted-structure" in html
        assert "Neuer Text" in html


# ---------------------------------------------------------------------------
# Foreign content
# ---------------------------------------------------------------------------

class TestForeign:
    def test_foreign_svg(self):
        tree = _make_act(
            '<article eId="art_1"><paragraph eId="art_1__para_1"><content>'
            '<foreign><svg xmlns="http://www.w3.org/2000/svg">'
            '<circle r="10"/></svg></foreign>'
            '</content></paragraph></article>'
        )
        html = render_body(tree)
        assert "akn-foreign" in html
        assert "<circle" in html

    def test_foreign_svg_sanitized(self):
        tree = _make_act(
            '<article eId="art_1"><paragraph eId="art_1__para_1"><content>'
            '<foreign><svg xmlns="http://www.w3.org/2000/svg">'
            '<circle r="10" onload="alert(1)"/>'
            '<a href="javascript:alert(1)"><text>bad</text></a>'
            '</svg></foreign>'
            '</content></paragraph></article>'
        )
        html = render_body(tree)
        assert "onload" not in html
        assert "javascript:" not in html
        assert "<circle" in html


# ---------------------------------------------------------------------------
# Container / block
# ---------------------------------------------------------------------------

class TestContainerBlock:
    def test_container_with_name(self):
        tree = _make_act(
            '<article eId="art_1"><paragraph eId="art_1__para_1"><content>'
            '<container name="footnotes"><p>Fussnote 1</p></container>'
            '</content></paragraph></article>'
        )
        html = render_body(tree)
        assert "akn-container-footnotes" in html
        assert "Fussnote 1" in html

    def test_block_with_name(self):
        tree = _make_act(
            '<article eId="art_1"><paragraph eId="art_1__para_1"><content>'
            '<block name="num">Anhang 1</block>'
            '</content></paragraph></article>'
        )
        html = render_body(tree)
        assert "akn-block-num" in html
        assert "Anhang 1" in html


# ---------------------------------------------------------------------------
# Real data integration tests (require data files)
# ---------------------------------------------------------------------------

class TestRealData:
    """Integration tests using real AKN files from the data directory."""

    def test_bv_renders_without_error(self, bv_tree):
        """Bundesverfassung renders without exceptions."""
        html = render_body(bv_tree)
        assert len(html) > 1000
        assert "akn-article" in html

    def test_kv_renders_without_error(self, kv_tree):
        """Kantonsverfassung VS renders without exceptions."""
        html = render_body(kv_tree)
        assert len(html) > 1000
        assert "akn-article" in html


class TestNHVComponents:
    """SR 451.1 (NHV) — the original bug: annexes with Hundszahn."""

    @pytest.fixture(scope="class")
    def nhv_tree(self):
        from pathlib import Path
        nhv_path = Path(__file__).parent.parent.parent / "data" / "ch" / "451.1" / "de.xml"
        if not nhv_path.exists():
            pytest.skip(f"NHV test data not found: {nhv_path}")
        from lex_akn.parse import parse_file
        return parse_file(nhv_path)

    def test_components_present(self, nhv_tree):
        """render_body includes annexes (components)."""
        html = render_body(nhv_tree)
        assert "akn-components" in html
        assert "akn-component" in html

    def test_hundszahn_visible(self, nhv_tree):
        """Hundszahn (the search term) must appear in rendered HTML."""
        html = render_body(nhv_tree)
        assert "Hundszahn" in html
