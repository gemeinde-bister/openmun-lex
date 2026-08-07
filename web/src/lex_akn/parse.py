"""AKN XML parsing with namespace handling."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from lxml import etree

if TYPE_CHECKING:
    from lxml.etree import _Element, _ElementTree

# Akoma Ntoso 3.0 + fedlex extension namespaces
AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
FEDLEX_NS = "http://fedlex.admin.ch/"

NSMAP: dict[str, str] = {
    "akn": AKN_NS,
    "fedlex": FEDLEX_NS,
}


def akn_tag(local_name: str) -> str:
    """Return the fully-qualified AKN tag name for use with lxml find/iter."""
    return f"{{{AKN_NS}}}{local_name}"


def fedlex_attr(local_name: str) -> str:
    """Return the fully-qualified fedlex attribute name."""
    return f"{{{FEDLEX_NS}}}{local_name}"


def parse_file(path: str | Path) -> _ElementTree:
    """Parse an AKN XML file and return the element tree.

    Raises FileNotFoundError if path does not exist.
    Raises etree.XMLSyntaxError if the XML is malformed.
    """
    path = Path(path)
    if not path.exists():
        msg = f"AKN file not found: {path}"
        raise FileNotFoundError(msg)
    return etree.parse(str(path))


def parse_string(xml_bytes: bytes) -> _ElementTree:
    """Parse AKN XML from bytes and return the element tree."""
    assert isinstance(xml_bytes, bytes), "Expected bytes, not str"
    return etree.ElementTree(etree.fromstring(xml_bytes))


def get_act(tree: _ElementTree) -> _Element:
    """Extract the top-level document element (<act> or <doc>) from an AKN tree.

    Raises ValueError if neither element is found.
    """
    for tag in ("act", "doc"):
        el = tree.find(f".//{akn_tag(tag)}")
        if el is not None:
            return el
    msg = "No <act> or <doc> element found in AKN document"
    raise ValueError(msg)


def get_body(tree: _ElementTree) -> _Element:
    """Extract the <body> element from an AKN document tree.

    Raises ValueError if no <body> element is found.
    """
    body = tree.find(f".//{akn_tag('body')}")
    if body is None:
        msg = "No <body> element found in AKN document"
        raise ValueError(msg)
    return body


def find_all(element: _Element, local_name: str) -> list[_Element]:
    """Find all descendant elements with the given AKN local name."""
    return element.findall(f".//{akn_tag(local_name)}")


def find_first(element: _Element, local_name: str) -> _Element | None:
    """Find the first descendant element with the given AKN local name."""
    return element.find(f".//{akn_tag(local_name)}")


def local_name(element: _Element) -> str:
    """Return the local tag name (without namespace) of an element."""
    tag = element.tag
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.split("}", 1)[1]
    return str(tag)


def text_content(element: _Element) -> str:
    """Extract all text content from an element and its descendants.

    Concatenates all text nodes, stripping leading/trailing whitespace.
    Words are joined with spaces.
    """
    parts: list[str] = []
    for text in element.itertext():
        stripped = text.strip()
        if stripped:
            parts.append(stripped)
    return " ".join(parts)


def text_content_compact(element: _Element) -> str:
    """Extract text preserving inline element boundaries.

    Unlike text_content(), does not insert spaces between inline child
    elements and their surrounding text.  This gives correct output for
    cases like ``CO<sub>2</sub>-Emissionen`` → ``CO2-Emissionen``
    while still handling ``Verordnung <br/>über`` → ``Verordnung über``.

    Collects raw text/tail, then collapses whitespace at the end.
    """
    parts: list[str] = []
    _collect_text(element, parts)
    # Collapse whitespace runs (newlines, tabs, multi-space → single space)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _collect_text(element: _Element, parts: list[str]) -> None:
    """Recursively collect raw text/tail preserving boundaries."""
    if element.text:
        parts.append(element.text)
    for child in element:
        tag = child.tag
        local = tag.split("}", 1)[1] if isinstance(tag, str) and "{" in tag else str(tag)
        if local == "br":
            # <br> represents a line break — emit a space in compact text
            parts.append(" ")
        _collect_text(child, parts)
        if child.tail:
            parts.append(child.tail)
