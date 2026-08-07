"""AKN element → HTML rendering logic.

Converts lxml AKN elements into HTML strings suitable for embedding
in Jinja templates and loading into ProseMirror.

Architecture: dispatch dictionary maps AKN local tag names to handler
functions.  Every element found in the corpus must have an explicit entry
— unknown tags trigger a warning and fall through to a generic block.
"""

from __future__ import annotations

import html
import logging
import re
import warnings
from typing import TYPE_CHECKING, Callable

from lxml import etree

from lex_akn.parse import AKN_NS, FEDLEX_NS, akn_tag, local_name

if TYPE_CHECKING:
    from lxml.etree import _Element, _ElementTree

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type alias for element handlers
# ---------------------------------------------------------------------------
ElementHandler = Callable[["_Element", int], str]

# ---------------------------------------------------------------------------
# Dispatch dictionary — populated by register helpers and direct assignment
# ---------------------------------------------------------------------------
DISPATCH: dict[str, ElementHandler] = {}

_warned_tags: set[str] = set()


def _warn_unknown(tag: str) -> None:
    """Emit a one-shot warning for an unregistered AKN element."""
    if tag not in _warned_tags:
        _warned_tags.add(tag)
        warnings.warn(f"Unhandled AKN element: <{tag}>", stacklevel=3)


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------

def _register(tag: str, handler: ElementHandler) -> None:
    assert tag not in DISPATCH, f"Duplicate dispatch entry: {tag}"
    DISPATCH[tag] = handler


def _register_skip(*tags: str) -> None:
    """Register tags that produce no output (handled elsewhere or metadata)."""
    for tag in tags:
        _register(tag, _handler_skip)


def _register_structural(*tags: str) -> None:
    """Register tags that render as structural sections with num/heading."""
    for tag in tags:
        _register(tag, _handler_structural)


def _register_inline_span(*tags: str) -> None:
    """Register tags that render as <span class="akn-{tag}">."""
    for tag in tags:
        _register(tag, _make_inline_span_handler(tag))


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _tag(el: _Element) -> str:
    return local_name(el)


def _eid(el: _Element) -> str:
    return el.get("eId", "")


def _eid_attr(el: _Element) -> str:
    eid = _eid(el)
    if eid:
        return f' id="{html.escape(eid)}" data-eid="{html.escape(eid)}"'
    return ""


def _get_num_text(el: _Element) -> str:
    """Extract text from a <num> child, stripping bold tags."""
    num = el.find(akn_tag("num"))
    if num is None:
        return ""
    parts: list[str] = []
    for text in num.itertext():
        stripped = text.strip()
        if stripped:
            parts.append(stripped)
    return " ".join(parts)


def _get_heading_text(el: _Element) -> str:
    """Extract text from a <heading> child."""
    heading = el.find(akn_tag("heading"))
    if heading is None:
        return ""
    parts: list[str] = []
    for text in heading.itertext():
        stripped = text.strip()
        if stripped:
            parts.append(stripped)
    return " ".join(parts)


_CSS_TOKEN_INVALID_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _sanitize_css_token(raw: str) -> str:
    """Normalize arbitrary strings into safe CSS class tokens."""
    if not raw:
        return ""
    cleaned = _CSS_TOKEN_INVALID_RE.sub("-", raw.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned.strip("-")


def _render_inline(el: _Element) -> str:
    """Render inline content (text, <ref>, <b>, <i>, <sup>, <authorialNote>).

    Handles mixed content: text nodes interspersed with child elements.
    """
    parts: list[str] = []

    if el.text:
        parts.append(html.escape(el.text))

    for child in el:
        tag = _tag(child)

        if tag == "ref":
            href = child.get("href", "")
            text = _render_inline_text(child)
            parts.append(f'<a href="{html.escape(href)}" class="akn-ref">{html.escape(text)}</a>')
        elif tag == "authorialNote":
            note_text = _render_inline_text(child)
            parts.append(
                f'<span class="akn-footnote" title="{html.escape(note_text)}">'
                f'<sup>[*]</sup></span>'
            )
        elif tag == "noteRef":
            marker = child.get("href", "")
            text = _render_inline_text(child) or marker
            parts.append(f'<sup class="akn-noteref">{html.escape(text)}</sup>')
        elif tag in ("b", "i", "sup", "sub", "u", "s"):
            text = _render_inline_text(child)
            parts.append(f"<{tag}>{html.escape(text)}</{tag}>")
        elif tag == "ins":
            text = _render_inline_text(child)
            parts.append(f"<ins>{html.escape(text)}</ins>")
        elif tag == "del":
            text = _render_inline_text(child)
            parts.append(f"<del>{html.escape(text)}</del>")
        elif tag == "br":
            parts.append("<br>")
        elif tag == "eol":
            parts.append("<br>")
        elif tag == "img":
            parts.append('<span class="akn-img-placeholder">[Bild]</span>')
        elif tag == "span":
            text = _render_inline_text(child)
            if text:
                parts.append(f'<span>{html.escape(text)}</span>')
        elif tag == "inline":
            name = _sanitize_css_token(child.get("name", ""))
            css = f"akn-inline akn-inline-{name}" if name else "akn-inline"
            text = _render_inline_text(child)
            if text:
                parts.append(f'<span class="{css}">{html.escape(text)}</span>')
        elif tag == "placeholder":
            # Content is usually [tab] or similar — render as space
            parts.append("&emsp;")
        elif tag in ("docTitle", "docNumber", "docDate", "shortTitle"):
            # Semantic wrappers that may contain inline formatting (<sub>, <br>)
            inner = _render_inline(child)
            if inner.strip():
                parts.append(f'<span class="akn-{tag}">{inner}</span>')
        elif tag in ("date", "term", "organization", "location",
                      "concept", "def", "remark"):
            text = _render_inline_text(child)
            if text:
                parts.append(
                    f'<span class="akn-{tag}">{html.escape(text)}</span>'
                )
        else:
            # Generic inline fallback
            text = _render_inline_text(child)
            if text:
                parts.append(html.escape(text))

        if child.tail:
            parts.append(html.escape(child.tail))

    return "".join(parts)


def _render_inline_text(el: _Element) -> str:
    """Extract plain text from an inline element (for titles, attributes)."""
    parts: list[str] = []
    for text in el.itertext():
        stripped = text.strip()
        if stripped:
            parts.append(stripped)
    return " ".join(parts)


def _render_children(el: _Element, depth: int) -> str:
    """Render all children of an element."""
    return "".join(render_element(c, depth) for c in el)


def _render_generic_block(el: _Element, depth: int) -> str:
    """Fallback: render as <div class="akn-{tag}"> with children."""
    tag = _tag(el)
    ea = _eid_attr(el)
    children = _render_children(el, depth)
    if children:
        return f'<div class="akn-{tag}"{ea}>{children}</div>'
    return ""


# ---------------------------------------------------------------------------
# Handler: skip (no output)
# ---------------------------------------------------------------------------

def _handler_skip(el: _Element, depth: int) -> str:
    return ""


# ---------------------------------------------------------------------------
# Handler: structural section (title/chapter/section/subsection/...)
# ---------------------------------------------------------------------------

def _handler_structural(el: _Element, depth: int) -> str:
    tag = _tag(el)
    ea = _eid_attr(el)
    css = f"akn-{tag} depth-{depth}"
    return _render_structural_impl(el, "section", css, ea, depth)


def _handler_level(el: _Element, depth: int) -> str:
    role = el.get(f"{{{FEDLEX_NS}}}role", "")
    css = f"akn-level akn-level-{role} depth-{depth}" if role else f"akn-level depth-{depth}"
    ea = _eid_attr(el)
    return _render_structural_impl(el, "section", css, ea, depth)


def _render_structural_impl(
    el: _Element, html_tag: str, css_class: str, eid_attr: str, depth: int,
) -> str:
    """Render a structural element (title, chapter, section, etc.)."""
    num = _get_num_text(el)
    heading = _get_heading_text(el)

    header_parts: list[str] = []
    h_level = min(depth + 2, 6)
    if num or heading:
        header_text = f"{num} {heading}".strip() if num and heading else (num or heading)
        header_parts.append(
            f'<h{h_level} class="akn-heading">{html.escape(header_text)}</h{h_level}>'
        )

    children_html = ""
    for child in el:
        child_tag = _tag(child)
        if child_tag in ("num", "heading"):
            continue
        children_html += render_element(child, depth + 1)

    return (
        f'<{html_tag} class="{css_class}"{eid_attr}>'
        f'{"".join(header_parts)}'
        f'{children_html}'
        f'</{html_tag}>'
    )


# ---------------------------------------------------------------------------
# Handler: article
# ---------------------------------------------------------------------------

def _handler_article(el: _Element, depth: int) -> str:
    ea = _eid_attr(el)
    num = _get_num_text(el)
    heading = _get_heading_text(el)

    header = ""
    if num or heading:
        header_text = num
        if heading:
            header_text = f"{num} {heading}" if num else heading

        footnote_html = ""
        for child in el:
            if _tag(child) == "num":
                for note in child:
                    if _tag(note) == "authorialNote":
                        note_text = _render_inline_text(note)
                        footnote_html = (
                            f' <span class="akn-footnote" '
                            f'title="{html.escape(note_text)}">'
                            f'<sup>[*]</sup></span>'
                        )
        header = (
            f'<header class="akn-article-header">'
            f'{html.escape(header_text)}{footnote_html}'
            f'</header>'
        )

    paragraphs = [c for c in el if _tag(c) == "paragraph"]
    if not paragraphs:
        return (
            f'<article class="akn-article akn-article-repealed"{ea}>'
            f'{header}'
            f'</article>'
        )

    children_html = ""
    for child in el:
        child_tag = _tag(child)
        if child_tag in ("num", "heading"):
            continue
        children_html += render_element(child, depth + 1)

    return (
        f'<article class="akn-article"{ea}>'
        f'{header}'
        f'<div class="akn-article-body">{children_html}</div>'
        f'</article>'
    )


# ---------------------------------------------------------------------------
# Handler: paragraph
# ---------------------------------------------------------------------------

def _handler_paragraph(el: _Element, depth: int) -> str:
    ea = _eid_attr(el)
    num = _get_num_text(el)
    num_html = f'<span class="akn-num">{html.escape(num)}</span> ' if num else ""

    content = el.find(akn_tag("content"))
    if content is None:
        return f'<p class="akn-paragraph"{ea}>{num_html}</p>'

    # Check if content has any block-level children (not just <p>)
    has_blocks = any(
        _tag(c) != "p" for c in content
    )

    if not has_blocks:
        inline = "".join(_render_inline(c) for c in content if _tag(c) == "p")
        return f'<p class="akn-paragraph"{ea}>{num_html}{inline}</p>'

    return _render_paragraph_with_blocks(content, num_html, ea)


def _render_paragraph_with_blocks(
    content: _Element, num_html: str, eid_attr: str
) -> str:
    """Render a paragraph that contains blockLists."""
    parts: list[str] = []
    pending_inline = num_html

    for child in content:
        tag = _tag(child)
        if tag == "p":
            pending_inline += _render_inline(child)
        elif tag == "blockList":
            intro = child.find(akn_tag("listIntroduction"))
            if intro is not None:
                pending_inline += _render_inline(intro)
            if pending_inline.strip():
                parts.append(f'<p>{pending_inline}</p>')
                pending_inline = ""
            items = [_render_item(c, "") for c in child if _tag(c) == "item"]
            if items:
                parts.append(f'<ol class="akn-list">{"".join(items)}</ol>')
            wrap = child.find(akn_tag("listWrapUp"))
            if wrap is not None:
                pending_inline += _render_inline(wrap)
        else:
            if pending_inline.strip():
                parts.append(f'<p>{pending_inline}</p>')
                pending_inline = ""
            parts.append(render_element(child, 0))

    if pending_inline.strip():
        parts.append(f'<p>{pending_inline}</p>')

    return f'<div class="akn-paragraph"{eid_attr}>{"".join(parts)}</div>'


# ---------------------------------------------------------------------------
# Handler: content (standalone)
# ---------------------------------------------------------------------------

def _handler_content(el: _Element, depth: int) -> str:
    parts: list[str] = []
    for child in el:
        tag = _tag(child)
        if tag == "p":
            inner = _render_inline(child)
            parts.append(f'<p>{inner}</p>')
        elif tag == "blockList":
            parts.append(_handler_blocklist(child, 0))
        else:
            parts.append(render_element(child, 0))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Handler: blockList / item
# ---------------------------------------------------------------------------

def _handler_blocklist(el: _Element, depth: int) -> str:
    ea = _eid_attr(el)
    parts: list[str] = []

    intro = el.find(akn_tag("listIntroduction"))
    if intro is not None:
        parts.append(f'<p class="akn-list-intro">{_render_inline(intro)}</p>')

    items_html: list[str] = []
    for child in el:
        if _tag(child) == "item":
            items_html.append(_render_item(child, ""))

    if items_html:
        parts.append(f'<ol class="akn-list">{"".join(items_html)}</ol>')

    wrap = el.find(akn_tag("listWrapUp"))
    if wrap is not None:
        parts.append(f'<p class="akn-list-wrap">{_render_inline(wrap)}</p>')

    return f'<div class="akn-blocklist"{ea}>{"".join(parts)}</div>'


def _handler_item(el: _Element, depth: int) -> str:
    return _render_item(el, _eid_attr(el))


def _render_item(el: _Element, eid_attr: str) -> str:
    """Render a <item> element inside a blockList."""
    num = _get_num_text(el)
    num_html = f'<span class="akn-num">{html.escape(num)}</span> ' if num else ""

    content_html = ""
    for child in el:
        if _tag(child) == "num":
            continue
        elif _tag(child) == "p":
            content_html += _render_inline(child)
        else:
            content_html += render_element(child, 0)

    eid = el.get("eId", "")
    eid_parts = f' id="{html.escape(eid)}"' if eid else ""

    return f'<li class="akn-item"{eid_parts}>{num_html}{content_html}</li>'


# ---------------------------------------------------------------------------
# Handler: preface / preamble
# ---------------------------------------------------------------------------

def _handler_preface(el: _Element, depth: int) -> str:
    parts: list[str] = []
    for child in el:
        tag = _tag(child)
        if tag == "p":
            inner = _render_inline(child)
            if inner.strip():
                parts.append(f'<p class="akn-preface-p">{inner}</p>')
        elif tag == "longTitle":
            # AKN longTitle wraps <p> containing <docTitle> — render children
            lt_parts = []
            for lt_child in child:
                if _tag(lt_child) == "p":
                    inner = _render_inline(lt_child)
                    if inner.strip():
                        lt_parts.append(f"<p>{inner}</p>")
            if lt_parts:
                parts.append(f'<div class="akn-longTitle">{"".join(lt_parts)}</div>')
        elif tag == "container":
            # Container in preface (common in annex <doc> preface)
            parts.append(_handler_container(child, depth))
        elif tag == "block":
            parts.append(_handler_block(child, depth))
        else:
            parts.append(render_element(child, depth))
    return f'<header class="akn-preface">{"".join(parts)}</header>' if parts else ""


def _handler_preamble(el: _Element, depth: int) -> str:
    parts: list[str] = []
    for child in el:
        tag = _tag(child)
        if tag == "p":
            inner = _render_inline(child)
            if inner.strip():
                parts.append(f'<p class="akn-preamble-p">{inner}</p>')
        elif tag == "formula":
            for p in child:
                if _tag(p) == "p":
                    inner = _render_inline(p)
                    if inner.strip():
                        name = child.get("name", "")
                        css = f"akn-formula-{name}" if name else "akn-formula"
                        parts.append(f'<p class="{css}">{inner}</p>')
        elif tag == "recitals":
            recital_parts: list[str] = []
            for recital in child:
                if _tag(recital) == "recital":
                    for p in recital:
                        if _tag(p) == "p":
                            inner = _render_inline(p)
                            if inner.strip():
                                recital_parts.append(
                                    f'<li class="akn-recital">{inner}</li>'
                                )
            if recital_parts:
                parts.append(
                    f'<ul class="akn-recitals">{"".join(recital_parts)}</ul>'
                )
        else:
            parts.append(render_element(child, depth))
    return f'<div class="akn-preamble">{"".join(parts)}</div>' if parts else ""


# ---------------------------------------------------------------------------
# Handler: conclusions / signatures
# ---------------------------------------------------------------------------

def _handler_conclusions(el: _Element, depth: int) -> str:
    parts: list[str] = []
    for child in el:
        tag = _tag(child)
        if tag == "blockContainer":
            parts.append(_render_conclusions_block(child))
        elif tag == "signature":
            parts.append(_render_signature(child))
        elif tag == "p":
            inner = _render_inline(child)
            if inner.strip():
                parts.append(f'<p class="akn-conclusions-p">{inner}</p>')
        else:
            parts.append(render_element(child, 0))
    return f'<footer class="akn-conclusions">{"".join(parts)}</footer>' if parts else ""


def _render_conclusions_block(el: _Element) -> str:
    eid = _eid(el)
    ea = f' id="{html.escape(eid)}"' if eid else ""
    parts: list[str] = []
    for child in el:
        tag = _tag(child)
        if tag == "p":
            inner = _render_inline(child)
            if inner.strip():
                parts.append(f'<p class="akn-conclusions-p">{inner}</p>')
        elif tag == "signature":
            parts.append(_render_signature(child))
        else:
            parts.append(render_element(child, 0))
    return f'<div class="akn-conclusions-block"{ea}>{"".join(parts)}</div>'


def _render_signature(el: _Element) -> str:
    eid = _eid(el)
    ea = f' id="{html.escape(eid)}"' if eid else ""
    role_el = el.find(akn_tag("role"))
    person_el = el.find(akn_tag("person"))
    role_text = _render_inline_text(role_el) if role_el is not None else ""
    person_text = _render_inline_text(person_el) if person_el is not None else ""
    parts: list[str] = []
    if role_text:
        parts.append(f'<span class="akn-role">{html.escape(role_text)}</span>')
    if person_text:
        parts.append(f'<span class="akn-person">{html.escape(person_text)}</span>')
    return f'<div class="akn-signature"{ea}>{"".join(parts)}</div>'


# ---------------------------------------------------------------------------
# Handler: tables
# ---------------------------------------------------------------------------

def _handler_table(el: _Element, depth: int) -> str:
    ea = _eid_attr(el)
    border = el.get("border", "")
    border_attr = f' border="{html.escape(border)}"' if border else ""
    children = _render_children(el, depth)
    return f'<table class="akn-table"{border_attr}{ea}>{children}</table>'


def _handler_tr(el: _Element, depth: int) -> str:
    children = _render_children(el, depth)
    return f'<tr>{children}</tr>'


def _handler_td(el: _Element, depth: int) -> str:
    return _render_table_cell(el, "td", depth)


def _handler_th(el: _Element, depth: int) -> str:
    return _render_table_cell(el, "th", depth)


def _render_table_cell(el: _Element, html_tag: str, depth: int) -> str:
    colspan = el.get("colspan", "")
    rowspan = el.get("rowspan", "")
    attrs = ""
    if colspan:
        attrs += f' colspan="{html.escape(colspan)}"'
    if rowspan:
        attrs += f' rowspan="{html.escape(rowspan)}"'
    # Table cells contain mixed inline + block content
    inner = _render_cell_content(el, depth)
    return f'<{html_tag}{attrs}>{inner}</{html_tag}>'


def _render_cell_content(el: _Element, depth: int) -> str:
    """Render content inside a table cell — mix of <p>, blocks, inline."""
    parts: list[str] = []
    if el.text and el.text.strip():
        parts.append(html.escape(el.text))
    for child in el:
        tag = _tag(child)
        if tag == "p":
            inner = _render_inline(child)
            parts.append(f'<p>{inner}</p>')
        else:
            parts.append(render_element(child, depth))
        if child.tail and child.tail.strip():
            parts.append(html.escape(child.tail))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Handler: container / block / subheading / intro / wrapUp
# ---------------------------------------------------------------------------

def _handler_container(el: _Element, depth: int) -> str:
    name = _sanitize_css_token(el.get("name", ""))
    css = f"akn-container akn-container-{name}" if name else "akn-container"
    ea = _eid_attr(el)
    children = _render_children(el, depth)
    return f'<div class="{css}"{ea}>{children}</div>' if children else ""


def _handler_block(el: _Element, depth: int) -> str:
    name = _sanitize_css_token(el.get("name", ""))
    ea = _eid_attr(el)
    # Block can contain inline content or children
    inner = _render_inline(el)
    if not inner.strip():
        inner = _render_children(el, depth)
    css = f"akn-block akn-block-{name}" if name else "akn-block"
    return f'<div class="{css}"{ea}>{inner}</div>' if inner.strip() else ""


def _handler_subheading(el: _Element, depth: int) -> str:
    inner = _render_inline(el)
    return f'<p class="akn-subheading">{inner}</p>' if inner.strip() else ""


def _handler_intro(el: _Element, depth: int) -> str:
    inner = _render_inline(el)
    return f'<div class="akn-intro">{inner}</div>' if inner.strip() else ""


def _handler_wrapup(el: _Element, depth: int) -> str:
    inner = _render_inline(el)
    return f'<div class="akn-wrapup">{inner}</div>' if inner.strip() else ""


# ---------------------------------------------------------------------------
# Handler: modification elements
# ---------------------------------------------------------------------------

def _handler_mod(el: _Element, depth: int) -> str:
    ea = _eid_attr(el)
    children = _render_children(el, depth)
    return f'<div class="akn-mod"{ea}>{children}</div>'


def _handler_quoted_structure(el: _Element, depth: int) -> str:
    ea = _eid_attr(el)
    children = _render_children(el, depth)
    return f'<blockquote class="akn-quoted-structure"{ea}>{children}</blockquote>'


def _handler_quoted_text(el: _Element, depth: int) -> str:
    inner = _render_inline(el)
    return f'<q class="akn-quoted-text">{inner}</q>'


# ---------------------------------------------------------------------------
# Handler: foreign content (SVG, MathML passthrough)
# ---------------------------------------------------------------------------

_SAFE_SVG_TAGS = {
    "svg", "g", "path", "rect", "circle", "line", "polyline", "polygon",
    "ellipse", "text", "tspan", "defs", "clipPath", "mask",
    "linearGradient", "radialGradient", "stop", "use",
}

_SAFE_MATHML_TAGS = {
    "mi", "mrow", "mo", "mn", "msup", "msub", "mfrac", "mtext",
    "munderover", "msubsup", "mroot", "mover",
}

_SAFE_FOREIGN_TAGS = _SAFE_SVG_TAGS | _SAFE_MATHML_TAGS

_SAFE_SVG_ATTRS = {
    "id", "class",
    "d", "fill", "fill-rule", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-dasharray",
    "stroke-dashoffset", "opacity", "transform",
    "x", "y", "x1", "y1", "x2", "y2",
    "cx", "cy", "r", "rx", "ry",
    "width", "height", "viewBox", "points",
    "preserveAspectRatio",
    "font-size", "font-family", "text-anchor", "dominant-baseline",
}

_SAFE_MATHML_ATTRS = {
    "display", "mathvariant", "stretchy", "accent", "fence", "form",
}


def _attr_local(attr_name: str) -> str:
    if "}" in attr_name:
        return attr_name.split("}", 1)[1]
    return attr_name


def _is_safe_attr(attr_name: str, value: str) -> bool:
    local = _attr_local(attr_name)
    if local.lower().startswith("on"):
        return False
    if local == "style":
        return False
    if local in ("href", "xlink:href"):
        return value.startswith("#")
    if local in _SAFE_SVG_ATTRS or local in _SAFE_MATHML_ATTRS:
        return True
    return False


def _sanitize_foreign_element(el: _Element) -> _Element | None:
    tag = _tag(el)
    if tag not in _SAFE_FOREIGN_TAGS:
        return None

    sanitized = etree.Element(el.tag, nsmap=el.nsmap)
    for name, value in el.attrib.items():
        if _is_safe_attr(name, value):
            sanitized.set(name, value)

    if el.text:
        sanitized.text = el.text

    for child in el:
        safe_child = _sanitize_foreign_element(child)
        if safe_child is not None:
            sanitized.append(safe_child)
            if child.tail:
                safe_child.tail = (safe_child.tail or "") + child.tail
        else:
            if child.tail:
                if sanitized.text:
                    sanitized.text += child.tail
                else:
                    sanitized.text = child.tail

    return sanitized


def _handler_foreign(el: _Element, depth: int) -> str:
    ea = _eid_attr(el)
    # Serialize sanitized children as XML for SVG/MathML passthrough
    parts: list[str] = []
    if el.text and el.text.strip():
        parts.append(html.escape(el.text))
    for child in el:
        safe_child = _sanitize_foreign_element(child)
        if safe_child is not None:
            raw = etree.tostring(safe_child, encoding="unicode")
            parts.append(raw)
        if child.tail and child.tail.strip():
            parts.append(html.escape(child.tail))
    return f'<div class="akn-foreign"{ea}>{"".join(parts)}</div>'


# ---------------------------------------------------------------------------
# Handler: <p> (inline paragraph)
# ---------------------------------------------------------------------------

def _handler_p(el: _Element, depth: int) -> str:
    """Render a standalone <p> — typically inside content, level, etc."""
    inner = _render_inline(el)
    return f'<p>{inner}</p>' if inner.strip() else ""


# ---------------------------------------------------------------------------
# Handler: blockContainer
# ---------------------------------------------------------------------------

def _handler_block_container(el: _Element, depth: int) -> str:
    ea = _eid_attr(el)
    children = _render_children(el, depth)
    return f'<div class="akn-block-container"{ea}>{children}</div>'


# ---------------------------------------------------------------------------
# Handler: hcontainer (hierarchical container — generic AKN wrapper)
# ---------------------------------------------------------------------------

def _handler_hcontainer(el: _Element, depth: int) -> str:
    name = _sanitize_css_token(el.get("name", ""))
    css = f"akn-hcontainer akn-hcontainer-{name}" if name else "akn-hcontainer"
    ea = _eid_attr(el)
    return _render_structural_impl(el, "section", css, ea, depth)


# ---------------------------------------------------------------------------
# Handler: Word/OOXML artifacts (AlternateContent)
# ---------------------------------------------------------------------------

def _handler_alternate_content(el: _Element, depth: int) -> str:
    """Word AlternateContent: render Fallback child, skip Choice."""
    for child in el:
        tag = _tag(child)
        if tag == "Fallback":
            return _render_children(child, depth)
    # No fallback — try to render children generically
    return _render_children(el, depth)


# ---------------------------------------------------------------------------
# Handler: HTML passthrough (h5, etc.)
# ---------------------------------------------------------------------------

def _handler_html_passthrough(el: _Element, depth: int) -> str:
    tag = _tag(el)
    inner = _render_inline(el)
    return f'<{tag}>{inner}</{tag}>'


# ---------------------------------------------------------------------------
# make_inline_span_handler for semantic inline elements
# ---------------------------------------------------------------------------

def _make_inline_span_handler(tag_name: str) -> ElementHandler:
    """Create a handler that renders as <span class="akn-{tag}">."""
    def handler(el: _Element, depth: int) -> str:
        inner = _render_inline_text(el)
        if inner:
            return f'<span class="akn-{tag_name}">{html.escape(inner)}</span>'
        return ""
    return handler


# ===========================================================================
# Register all known AKN elements
# ===========================================================================

# --- Structural sections ---
_register_structural(
    "title", "book", "part",
    "chapter", "section", "subsection",
    "subchapter", "subdivision", "transitional", "proviso", "disposition",
)
_register("level", _handler_level)
_register("article", _handler_article)
_register("paragraph", _handler_paragraph)
_register("hcontainer", _handler_hcontainer)

# --- Content / block ---
_register("content", _handler_content)
_register("blockList", _handler_blocklist)
_register("item", _handler_item)
_register("p", _handler_p)
_register("container", _handler_container)
_register("block", _handler_block)
_register("blockContainer", _handler_block_container)
_register("subheading", _handler_subheading)
_register("intro", _handler_intro)
_register("wrapUp", _handler_wrapup)

# --- Preface / preamble / conclusions ---
_register("preface", _handler_preface)
_register("preamble", _handler_preamble)
_register("conclusions", _handler_conclusions)
_register("formula", lambda el, d: "")  # handled inside preamble
_register("recitals", lambda el, d: "")  # handled inside preamble
_register("recital", lambda el, d: "")  # handled inside preamble
_register("signature", lambda el, d: _render_signature(el))
_register("role", _handler_skip)  # handled inside signature
_register("person", _handler_skip)  # handled inside signature

# --- Tables ---
_register("table", _handler_table)
_register("tr", _handler_tr)
_register("td", _handler_td)
_register("th", _handler_th)

# --- Modification ---
_register("mod", _handler_mod)
_register("quotedStructure", _handler_quoted_structure)
_register("quotedText", _handler_quoted_text)

# --- Foreign content ---
_register("foreign", _handler_foreign)

# --- Inline elements (handled by _render_inline, skip when standalone) ---
_register_skip(
    "ref", "authorialNote", "noteRef",
    "b", "i", "sup", "sub", "u", "s",
    "ins", "del",
    "br", "eol", "eop",
    "img", "span", "inline", "placeholder",
    "docTitle", "docNumber", "docDate", "shortTitle",
    "date", "term", "organization", "location", "concept",
    "def", "remark",
)

# --- Metadata (skip in body rendering) ---
_register_skip(
    "meta", "identification", "references",
    "FRBRWork", "FRBRExpression", "FRBRManifestation",
    "FRBRCountry", "FRBRcountry",  # both cases exist in corpus
    "FRBRdate", "FRBRname", "FRBRprescriptive",
    "FRBRauthoritative", "FRBRlanguage", "FRBRuri", "FRBRalias",
    "FRBRsubtype", "FRBRnumber", "FRBRthis",
    "FRBRauthor", "FRBRformat",
    "publication", "lifecycle", "temporalData",
    "eventRef", "temporalGroup", "timeInterval",
    "TLCOrganization", "TLCRole", "TLCEvent", "TLCLocation",
    "TLCConcept", "TLCTerm", "TLCObject", "TLCProcess",
    "TLCReference",
    "componentRef",
    "classificationItem", "classification",
    "keyword",
    "proprietary",
)

# --- num / heading (handled by parent structural renderers) ---
_register_skip("num", "heading")

# --- listIntroduction / listWrapUp (handled by blockList/paragraph) ---
_register_skip("listIntroduction", "listWrapUp")

# --- Components (rendered by render_body, not dispatch) ---
_register_skip("components", "component", "doc", "mainBody")

# --- Word/OOXML artifacts ---
_register("AlternateContent", _handler_alternate_content)
_register_skip(
    "Choice", "Fallback",
    "break", "orig", "clone", "cr", "delText",
    "smartTagPr", "attr",
    "moveFromRangeStart", "moveFromRangeEnd",
    "unknown",
)

# --- HTML passthrough (found in some Fedlex docs) ---
_register("h5", _handler_html_passthrough)

# --- Root / structural containers (handled by render_body) ---
_register_skip("akomaNtoso", "act", "body")

# --- Custom Fedlex elements ---
_register_skip("heading-info", "heading-annex")
_register("foreign-block", _handler_foreign)  # same treatment as foreign

# --- SVG elements (inside <foreign>, serialized as raw XML) ---
# Registered as skip in case they appear outside <foreign> context
_register_skip(
    "svg", "g", "path", "rect", "circle",
)

# --- MathML elements (inside <foreign>, serialized as raw XML) ---
_register_skip(
    "mi", "mrow", "mo", "mn", "msup", "msub", "mfrac", "mtext",
    "munderover", "msubsup", "mroot", "mover",
)


# ===========================================================================
# Main dispatch entry point
# ===========================================================================

def render_element(el: _Element, depth: int = 0) -> str:
    """Render an AKN element to HTML recursively.

    Returns an HTML string. Each structural element gets a data-eid
    attribute for ProseMirror integration and fragment URI scrolling.
    """
    tag = _tag(el)
    handler = DISPATCH.get(tag)
    if handler is not None:
        return handler(el, depth)
    _warn_unknown(tag)
    return _render_generic_block(el, depth)


# ===========================================================================
# Top-level document renderer
# ===========================================================================

def render_body(tree: _ElementTree) -> str:
    """Render the full document body to HTML.

    Returns the complete HTML for the act, including preface, preamble,
    body, conclusions, and components (annexes).
    """
    from lex_akn.parse import get_act

    act = get_act(tree)
    parts: list[str] = []

    # Preface
    preface = act.find(akn_tag("preface"))
    if preface is not None:
        parts.append(render_element(preface, 0))

    # Preamble
    preamble = act.find(akn_tag("preamble"))
    if preamble is not None:
        parts.append(render_element(preamble, 0))

    # Body
    body = act.find(akn_tag("body"))
    if body is not None:
        for child in body:
            parts.append(render_element(child, 0))

    # Conclusions (signatures)
    conclusions = act.find(akn_tag("conclusions"))
    if conclusions is not None:
        parts.append(_handler_conclusions(conclusions, 0))

    # Components (annexes) — sub-documents with their own preface + mainBody
    components = act.find(akn_tag("components"))
    if components is not None:
        parts.append(_render_components(components))

    return "\n".join(parts)


def _render_components(components: _Element) -> str:
    """Render <components> — each <component>/<doc> as a section."""
    parts: list[str] = []
    for comp in components:
        if _tag(comp) != "component":
            continue
        doc = comp.find(akn_tag("doc"))
        if doc is None:
            continue
        parts.append(_render_component_doc(doc))
    if parts:
        return f'<div class="akn-components">{"".join(parts)}</div>'
    return ""


def _render_component_doc(doc: _Element) -> str:
    """Render a single <doc> inside a component (annex)."""
    doc_parts: list[str] = []

    # Preface (annex title)
    preface = doc.find(akn_tag("preface"))
    if preface is not None:
        doc_parts.append(_handler_preface(preface, 0))

    # mainBody (annex content)
    main_body = doc.find(akn_tag("mainBody"))
    if main_body is not None:
        for child in main_body:
            doc_parts.append(render_element(child, 0))

    # Conclusions
    conclusions = doc.find(akn_tag("conclusions"))
    if conclusions is not None:
        doc_parts.append(_handler_conclusions(conclusions, 0))

    return f'<section class="akn-component">{"".join(doc_parts)}</section>'
