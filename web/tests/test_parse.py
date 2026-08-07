"""Tests for lex_akn.parse module."""

from pathlib import Path

import pytest
from lxml.etree import _ElementTree

from lex_akn.parse import (
    find_all,
    find_first,
    get_act,
    get_body,
    local_name,
    parse_file,
    text_content,
)


def test_parse_file_returns_tree(bv_tree: _ElementTree) -> None:
    assert bv_tree is not None
    root = bv_tree.getroot()
    assert local_name(root) == "akomaNtoso"


def test_parse_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        parse_file("/nonexistent/path.xml")


def test_get_act(bv_tree: _ElementTree) -> None:
    act = get_act(bv_tree)
    assert act.get("name") == "publicLaw"


def test_get_body(bv_tree: _ElementTree) -> None:
    body = get_body(bv_tree)
    assert local_name(body) == "body"


def test_find_all_articles(bv_tree: _ElementTree) -> None:
    body = get_body(bv_tree)
    articles = find_all(body, "article")
    # BV has 232 articles (including repealed placeholders)
    assert len(articles) >= 190
    assert len(articles) <= 250


def test_find_first_article(bv_tree: _ElementTree) -> None:
    body = get_body(bv_tree)
    art = find_first(body, "article")
    assert art is not None
    assert art.get("eId") == "art_1"


def test_structural_elements_present(bv_tree: _ElementTree) -> None:
    body = get_body(bv_tree)
    titles = find_all(body, "title")
    chapters = find_all(body, "chapter")
    sections = find_all(body, "section")
    assert len(titles) >= 5
    assert len(chapters) >= 10
    assert len(sections) >= 1


def test_local_name(bv_tree: _ElementTree) -> None:
    act = get_act(bv_tree)
    assert local_name(act) == "act"


def test_text_content(bv_tree: _ElementTree) -> None:
    body = get_body(bv_tree)
    art1 = find_first(body, "article")
    assert art1 is not None
    text = text_content(art1)
    # Art. 1 BV contains "Schweizerische Eidgenossenschaft"
    assert "Schweizerische Eidgenossenschaft" in text or "Art. 1" in text
