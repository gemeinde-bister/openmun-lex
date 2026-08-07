"""Tests for lex_akn.navigate module."""

from lxml.etree import _ElementTree

from lex_akn.navigate import (
    build_toc,
    find_article,
    find_by_eid,
    flatten_toc,
    list_articles,
)
from lex_akn.parse import get_body, local_name


def test_find_article_by_eid(bv_tree: _ElementTree) -> None:
    art = find_article(bv_tree, "art_1")
    assert art is not None
    assert art.get("eId") == "art_1"


def test_find_article_shorthand(bv_tree: _ElementTree) -> None:
    """Can pass just the number part, art_ prefix is added."""
    art = find_article(bv_tree, "1")
    assert art is not None
    assert art.get("eId") == "art_1"


def test_find_article_with_suffix(bv_tree: _ElementTree) -> None:
    """BV has articles like art_197 with numbered sub-articles."""
    art = find_article(bv_tree, "art_197")
    assert art is not None


def test_find_article_not_found(bv_tree: _ElementTree) -> None:
    art = find_article(bv_tree, "art_9999")
    assert art is None


def test_find_by_eid_paragraph(bv_tree: _ElementTree) -> None:
    # Art. 1 BV has a single paragraph: eId="art_1/para" (no number suffix)
    para = find_by_eid(bv_tree, "art_1/para")
    assert para is not None
    assert local_name(para) == "paragraph"


def test_find_by_eid_numbered_paragraph(bv_tree: _ElementTree) -> None:
    # Art. 2 BV has numbered paragraphs: art_2/para_1, art_2/para_2
    para = find_by_eid(bv_tree, "art_2/para_1")
    assert para is not None
    assert local_name(para) == "paragraph"


def test_list_articles(bv_tree: _ElementTree) -> None:
    body = get_body(bv_tree)
    articles = list_articles(body)
    assert len(articles) >= 190
    # All should have eId
    for art in articles:
        assert art.get("eId") is not None


def test_build_toc(bv_tree: _ElementTree) -> None:
    toc = build_toc(bv_tree)
    assert len(toc) > 0
    # Top-level should be titles
    top_tags = {e.tag for e in toc}
    assert "title" in top_tags or "chapter" in top_tags


def test_toc_has_depth(bv_tree: _ElementTree) -> None:
    toc = build_toc(bv_tree)
    flat = flatten_toc(toc)
    depths = {e.depth for e in flat}
    # Should have at least 3 levels of nesting
    assert len(depths) >= 3


def test_toc_entries_have_num(bv_tree: _ElementTree) -> None:
    toc = build_toc(bv_tree)
    flat = flatten_toc(toc)
    # Most structural elements should have a num
    with_num = [e for e in flat if e.num]
    assert len(with_num) > len(flat) // 2
