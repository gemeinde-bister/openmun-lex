"""Structural navigation: TOC generation, article/element lookup by eId."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lex_akn.parse import akn_tag, find_all, find_first, local_name, text_content

if TYPE_CHECKING:
    from lxml.etree import _Element, _ElementTree

# Structural element types that form the document hierarchy.
# Order matters: outer to inner.
STRUCTURAL_TAGS = (
    "book", "part", "title", "chapter", "subchapter", "section", "subsection",
    "subdivision", "transitional", "proviso", "disposition",
    "level", "article",
)

# Tags that can appear as TOC entries
TOC_TAGS = frozenset(STRUCTURAL_TAGS)


@dataclass
class TOCEntry:
    """A single entry in the table of contents."""

    eid: str
    tag: str
    num: str
    heading: str
    depth: int
    children: list[TOCEntry] = field(default_factory=list)


def find_by_eid(tree: _ElementTree, eid: str) -> _Element | None:
    """Find an element by its eId attribute anywhere in the document.

    Returns None if no element with that eId exists.
    """
    # XPath with namespace-aware attribute search
    results = tree.xpath(
        f".//*[@eId='{eid}']",
    )
    if results:
        return results[0]
    return None


def find_article(tree: _ElementTree, eid: str) -> _Element | None:
    """Find an article by eId (e.g. 'art_1', 'art_19_a').

    Convenience wrapper that validates the eId looks like an article.
    """
    if not eid.startswith("art_"):
        eid = f"art_{eid}"
    return find_by_eid(tree, eid)


def list_articles(body: _Element) -> list[_Element]:
    """Return all article elements in document order."""
    return find_all(body, "article")


def _extract_num(element: _Element) -> str:
    """Extract the <num> text from an element."""
    num_el = element.find(akn_tag("num"))
    if num_el is None:
        return ""
    return text_content(num_el).strip()


def _extract_heading(element: _Element) -> str:
    """Extract the <heading> text from an element."""
    heading_el = element.find(akn_tag("heading"))
    if heading_el is None:
        return ""
    return text_content(heading_el).strip()


def _build_toc_recursive(element: _Element, depth: int) -> list[TOCEntry]:
    """Recursively build TOC entries from structural elements."""
    entries: list[TOCEntry] = []

    for child in element:
        tag = local_name(child)
        if tag not in TOC_TAGS:
            continue

        eid = child.get("eId", "")
        num = _extract_num(child)
        heading = _extract_heading(child)

        # For articles, the "heading" is often in the num element itself
        # (e.g. "Art. 1" with separate heading)
        # Skip articles without eId
        if not eid:
            continue

        entry = TOCEntry(
            eid=eid,
            tag=tag,
            num=num,
            heading=heading,
            depth=depth,
        )

        # Recurse into structural children (but not into article internals)
        if tag != "article":
            entry.children = _build_toc_recursive(child, depth + 1)

        entries.append(entry)

    return entries


def build_toc(tree: _ElementTree) -> list[TOCEntry]:
    """Build the full table of contents from a parsed AKN document.

    Returns a list of top-level TOC entries, each with nested children.
    """
    body = tree.find(f".//{akn_tag('body')}")
    if body is None:
        return []
    return _build_toc_recursive(body, depth=0)


def flatten_toc(entries: list[TOCEntry]) -> list[TOCEntry]:
    """Flatten a nested TOC into a flat list preserving depth info."""
    result: list[TOCEntry] = []
    for entry in entries:
        result.append(entry)
        result.extend(flatten_toc(entry.children))
    return result
