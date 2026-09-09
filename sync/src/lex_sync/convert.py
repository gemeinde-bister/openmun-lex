"""Convert lex.vs.ch JSON document tree to AKN XML."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from lxml import etree

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
NSMAP = {None: AKN_NS}

# AKN structural element names by title nesting depth (1-indexed).
# depth 0 is the root container (skipped).
_STRUCTURAL_TAGS = {
    1: "title",
    2: "chapter",
    3: "section",
    4: "subdivision",
}
_MAX_STRUCTURAL_DEPTH = max(_STRUCTURAL_TAGS)

# --- text_of_law_type_id → name mapping ---
# Fetched from /api/{lang}/text_of_law_types; hardcoded for offline use.
LAW_TYPE_NAMES: dict[int, str] = {
    1: "Staatsvertrag",
    2: "Interkantonale Vereinbarung",
    3: "Verfassung",
    4: "Gesetz",
    5: "Verordnung",
    6: "Reglement",
    7: "Richtlinie",
    9: "Dekret",
    10: "Beschluss",
    11: "Entscheid StR",
    13: "Beschluss GR",
}


# --- Sidecar metadata (stored alongside AKN XML, not inside it) ---


@dataclass(frozen=True)
class VersionRecord:
    """A historical or future version of a law."""

    version_id: int
    structured_document_id: int | None
    version_dates_str: str


@dataclass(frozen=True)
class ChangeDocument:
    """An amendment gazette entry."""

    id: int
    number: str
    title: str
    date_of_decision: str
    date_of_publication: str
    pdf_link: str
    for_selected_version: bool


@dataclass(frozen=True)
class Material:
    """A cross-reference to a related law or document."""

    id: int
    title: str
    url: str


@dataclass(frozen=True)
class DocumentMeta:
    """Sidecar metadata extracted from the API response."""

    systematic_number: str
    law_type: str
    law_type_id: int | None
    pdf_link: str
    pdf_link_tol: str
    pdf_link_tol_size: int
    available_languages: list[str]
    abrogated: bool
    abrogated_scheduled: bool
    abrogated_dates_str: str
    old_versions: list[VersionRecord] = field(default_factory=list)
    change_documents: list[ChangeDocument] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)


@dataclass(frozen=True)
class ConvertResult:
    """Result of converting a document."""

    xml: etree._Element
    meta: DocumentMeta
    warnings: list[str]


def parse_in_force_date(dates_str: str) -> str:
    """Extract the in-force start date from a version_dates_str field.

    Handles two formats from the lex.vs.ch API:
    - Current: "Aktuelle Version in Kraft seit: DD.MM.YYYY (...)"
    - Old: "Version in Kraft von: DD.MM.YYYY bis: DD.MM.YYYY (...)"

    Returns ISO date string (YYYY-MM-DD).

    Raises:
        ValueError: If no date can be extracted.
    """
    # Match "seit: DD.MM.YYYY" (current version)
    m = re.search(r"seit:\s*(\d{2})\.(\d{2})\.(\d{4})", dates_str)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    # Match "von: DD.MM.YYYY" (old version)
    m = re.search(r"von:\s*(\d{2})\.(\d{2})\.(\d{4})", dates_str)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    raise ValueError(f"Cannot extract in-force date from: {dates_str!r}")


def parse_abrogated_date(dates_str: str) -> str | None:
    """Extract the abrogation (repeal) date from an abrogated_dates_str field.

    The lex.vs.ch API returns human strings such as
    "Aufgehoben am: DD.MM.YYYY" or "Aufgehoben per: DD.MM.YYYY".  Returns the
    first DD.MM.YYYY found as an ISO date (YYYY-MM-DD), or None if none is
    present.  Never invents a date — absence is recorded as None (zero data
    invention).
    """
    if not dates_str:
        return None
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", dates_str)
    if not m:
        return None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"


def convert_document(
    api_response: dict,
    lang: str = "de",
    *,
    in_force_date: str | None = None,
) -> ConvertResult:
    """Convert a lex.vs.ch show_as_json response to AKN XML.

    Args:
        api_response: Full API response from show_as_json endpoint.
        lang: Language to extract (de or fr).
        in_force_date: Optional ISO date override for the in-force date.
            When set, overrides ``publication_enactment`` for FRBR
            ``inForceSince`` and lifecycle ``evt_inforce``.

    Returns:
        ConvertResult with AKN XML, sidecar metadata, and any warnings.
    """
    warnings: list[str] = []

    tol = api_response["text_of_law"]
    sv = tol["selected_version"]
    if sv.get("json_content") is None:
        raise ValueError(
            f"{tol.get('systematic_number', '?')}: no structured content "
            "(json_content is null — PDF-only version?)"
        )
    doc = sv["json_content"]["document"]
    content = doc["content"]
    header_html = doc.get("header", {}).get(lang, "")

    sysno = tol["systematic_number"]
    # Extract language-specific title and abbreviation from header HTML,
    # falling back to top-level values (which are always primary language).
    title_raw = _extract_header_field(header_html, "title") or tol.get("title", "")
    # Collapse newlines (from <br/>) to spaces for FRBR title
    title = " ".join(title_raw.split())
    abbreviation = _extract_header_abbreviation(header_html) or tol.get("abbreviation", "")

    # Date fields for FRBR metadata
    date_of_decision = tol.get("date_of_decision", "")
    enactment = tol.get("enactment", "")
    publication_enactment = in_force_date or tol.get("publication_enactment", "")
    version_uid = tol.get("version_uid", "")
    version_id = sv.get("id")

    # Available languages
    available_langs = [
        al["language"]["iso639_1_code"]
        for al in sv.get("available_languages", [])
    ]

    # Abrogation
    abrogated = tol.get("abrogated", False)
    abrogated_scheduled = tol.get("abrogated_scheduled", False)
    abrogated_dates_str = tol.get("abrogated_dates_str", "") or ""

    # Build AKN skeleton
    akn_root = _make_element("akomaNtoso")
    act = _sub(akn_root, "act", name="publicLaw")
    meta = _sub(act, "meta")
    _build_meta(
        meta, sysno, title, abbreviation, lang,
        date_of_decision=date_of_decision,
        enactment=enactment,
        publication_enactment=publication_enactment,
        version_uid=version_uid,
        version_id=version_id,
        available_languages=available_langs,
        abrogated=abrogated,
        abrogated_dates_str=abrogated_dates_str,
    )

    # Preamble (between meta and body)
    _build_preamble(act, header_html)

    body = _sub(act, "body")

    # Convert content tree (skip the root "title" container at depth 0)
    _convert_children(body, content, lang, depth=0, warnings=warnings)
    if len(body) == 0:
        warnings.append(f"Empty body in {lang}: no articles or sections converted")

    # Sidecar metadata
    doc_meta = _extract_document_meta(tol, sv)

    return ConvertResult(xml=akn_root, meta=doc_meta, warnings=warnings)


def serialize(akn_root: etree._Element, pretty: bool = True) -> bytes:
    """Serialize an AKN element tree to UTF-8 XML bytes."""
    return etree.tostring(
        akn_root,
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=pretty,
    )


# --- Internal conversion ---


def _convert_children(
    parent_el: etree._Element,
    node: dict,
    lang: str,
    depth: int,
    warnings: list[str],
) -> None:
    """Convert child nodes of a JSON node into AKN elements under parent_el."""
    children = node.get("children", [])
    i = 0
    while i < len(children):
        child = children[i]
        ntype = child.get("type", "")

        if ntype == "title":
            _convert_title(parent_el, child, lang, depth + 1, warnings)
            i += 1
        elif ntype == "article":
            _convert_article(parent_el, child, lang, warnings)
            i += 1
        elif ntype == "paragraph":
            _convert_paragraph(parent_el, child, lang, warnings)
            i += 1
        elif ntype == "enumeration":
            # Should not appear at this level (should be under paragraph).
            warnings.append(
                f"Unexpected enumeration at depth {depth}: {child.get('uid', '?')}"
            )
            i += 1
        else:
            warnings.append(
                f"Unknown node type {ntype!r}: {child.get('uid', '?')}"
            )
            i += 1


def _convert_title(
    parent_el: etree._Element,
    node: dict,
    lang: str,
    depth: int,
    warnings: list[str],
) -> None:
    """Convert a title node to <title>, <chapter>, <section>, or <subdivision>."""
    tag = _STRUCTURAL_TAGS.get(depth)
    if tag is None:
        if depth > _MAX_STRUCTURAL_DEPTH:
            tag = "subdivision"
            warnings.append(
                f"Title depth {depth} exceeds max, using <subdivision>: "
                f"{node.get('uid', '?')}"
            )
        else:
            warnings.append(f"Unexpected title depth {depth}: {node.get('uid', '?')}")
            return

    eid = _title_eid(parent_el, tag, node, lang)
    el = _sub(parent_el, tag, eId=eid)

    # Number
    num_text = _extract_title_number(node, lang)
    if num_text:
        num_el = _sub(el, "num")
        num_el.text = num_text

    # Heading
    heading_text = _extract_title_heading(node, lang)
    if heading_text:
        heading_el = _sub(el, "heading")
        heading_el.text = heading_text

    # Recurse into children
    _convert_children(el, node, lang, depth, warnings)


def _convert_article(
    parent_el: etree._Element,
    node: dict,
    lang: str,
    warnings: list[str],
) -> None:
    """Convert an article node to <article>."""
    art_num = _extract_article_number(node, lang)
    eid = f"art_{art_num}" if art_num else f"art_{node.get('uid', 'unknown')}"

    el = _sub(parent_el, "article", eId=eid)

    # Num (e.g., "Art. 3")
    num_raw = node.get("number", {}).get(lang, "")
    num_text = _strip_html(num_raw).strip()
    if num_text:
        num_el = _sub(el, "num")
        b_el = _sub(num_el, "b")
        b_el.text = num_text

    # Heading (article title)
    heading_text = _extract_text_field(node, lang)
    if heading_text:
        heading_el = _sub(el, "heading")
        heading_el.text = heading_text

    # Paragraphs
    para_children = [c for c in node.get("children", []) if c.get("type") == "paragraph"]

    for para_node in para_children:
        _convert_paragraph_in_article(el, para_node, eid, lang, len(para_children), warnings)


def _convert_paragraph_in_article(
    article_el: etree._Element,
    node: dict,
    article_eid: str,
    lang: str,
    total_paras: int,
    warnings: list[str],
) -> None:
    """Convert a paragraph node inside an article."""
    para_num = _extract_para_number(node, lang)

    if total_paras == 1:
        # Single paragraph: eId without number suffix
        eid = f"{article_eid}/para"
    else:
        eid = f"{article_eid}/para_{para_num}" if para_num else f"{article_eid}/para"

    el = _sub(article_el, "paragraph", eId=eid)

    # Num element (only for multi-paragraph articles)
    if total_paras > 1 and para_num:
        num_el = _sub(el, "num")
        num_el.text = _clean_para_num(para_num)

    # Content
    content_el = _sub(el, "content")

    # Check for enumeration children
    enum_children = [c for c in node.get("children", []) if c.get("type") == "enumeration"]

    para_text = _extract_para_text(node, lang)

    if enum_children:
        # Paragraph with enumerations -> blockList
        block_list = _sub(content_el, "blockList")

        if para_text:
            intro_el = _sub(block_list, "listIntroduction", eId=f"{eid}/listintro")
            intro_el.text = f" {para_text}"

        for enum_node in enum_children:
            _convert_enumeration(block_list, enum_node, eid, lang, warnings)
    elif para_text:
        p_el = _sub(content_el, "p")
        p_el.text = f" {para_text}"


def _convert_enumeration(
    block_list_el: etree._Element,
    node: dict,
    para_eid: str,
    lang: str,
    warnings: list[str],
) -> None:
    """Convert an enumeration node to <item> inside a <blockList>."""
    letter, text = _extract_enum_content(node, lang)

    if not letter:
        warnings.append(f"Enumeration without letter: {node.get('uid', '?')}")
        letter = "?"

    # eId: strip trailing ) from letter for eId
    letter_clean = letter.rstrip(")").rstrip(".").strip()
    eid = f"{para_eid}/lbl_{letter_clean}"

    item_el = _sub(block_list_el, "item", eId=eid)

    num_el = _sub(item_el, "num")
    num_el.text = f"{letter_clean}. "

    p_el = _sub(item_el, "p")
    p_el.text = text if text else ""


def _convert_paragraph(
    parent_el: etree._Element,
    node: dict,
    lang: str,
    warnings: list[str],
) -> None:
    """Convert a standalone paragraph (not inside an article)."""
    warnings.append(f"Standalone paragraph: {node.get('uid', '?')}")


# --- Meta ---


def _build_meta(
    meta_el: etree._Element,
    sysno: str,
    title: str,
    abbreviation: str,
    lang: str,
    *,
    date_of_decision: str = "",
    enactment: str = "",
    publication_enactment: str = "",
    version_uid: str = "",
    version_id: int | None = None,
    available_languages: list[str] | None = None,
    abrogated: bool = False,
    abrogated_dates_str: str = "",
) -> None:
    """Build <meta> section with FRBR identification, dates, and lifecycle."""
    ident = _sub(meta_el, "identification", source="#lex.vs.ch")

    work = _sub(ident, "FRBRWork")
    _sub(work, "FRBRthis", value=f"/eli/vs/{sysno}")
    _sub(work, "FRBRuri", value=f"/eli/vs/{sysno}")
    # Work-level date: original decision date
    if date_of_decision:
        _sub(work, "FRBRdate", date=date_of_decision, name="decision")
    if enactment:
        _sub(work, "FRBRdate", date=enactment, name="enactment")
    _sub(work, "FRBRalias", value=title, name="title")
    if abbreviation:
        _sub(work, "FRBRalias", value=abbreviation, name="abbreviation")
    _sub(work, "FRBRcountry", value="CH-VS")
    _sub(work, "FRBRnumber", value=sysno)

    expr = _sub(ident, "FRBRExpression")
    _sub(expr, "FRBRthis", value=f"/eli/vs/{sysno}/{lang}")
    _sub(expr, "FRBRuri", value=f"/eli/vs/{sysno}/{lang}")
    # Expression-level date: current version in-force date
    if publication_enactment:
        _sub(expr, "FRBRdate", date=publication_enactment, name="inForceSince")
    _sub(expr, "FRBRlanguage", language=lang)
    # Record all available languages
    if available_languages:
        for al in available_languages:
            if al != lang:
                _sub(expr, "FRBRlanguage", language=al)
    if version_uid:
        _sub(expr, "FRBRalias", value=version_uid, name="versionHash")
    if version_id is not None:
        _sub(expr, "FRBRalias", value=str(version_id), name="versionId")

    # Lifecycle events
    lifecycle = _sub(meta_el, "lifecycle", source="#lex.vs.ch")
    if enactment:
        _sub(lifecycle, "eventRef", eId="evt_enactment",
             date=enactment, type="generation", source="#lex.vs.ch")
    if publication_enactment and publication_enactment != enactment:
        _sub(lifecycle, "eventRef", eId="evt_inforce",
             date=publication_enactment, type="amendment", source="#lex.vs.ch")
    if abrogated:
        _sub(lifecycle, "eventRef", eId="evt_repeal",
             date=abrogated_dates_str if abrogated_dates_str else "unknown",
             type="repeal", source="#lex.vs.ch")


# --- Preamble ---


def _build_preamble(act_el: etree._Element, header_html: str) -> None:
    """Build <preamble> from the header HTML.

    Extracts enacting authority, legal basis (recitals), and enacting formula.
    """
    author = _extract_header_field(header_html, "ingress_author")
    foundation = _extract_header_field(header_html, "ingress_foundation")
    action = _extract_header_field(header_html, "ingress_action")

    if not (author or foundation or action):
        return

    preamble = _sub(act_el, "preamble")

    if author:
        formula_auth = _sub(preamble, "formula", name="enactingAuthority")
        p = _sub(formula_auth, "p")
        p.text = author

    if foundation:
        recitals = _sub(preamble, "recitals")
        # Split on <p> tags in the foundation HTML to get individual recitals
        paragraphs = _extract_header_paragraphs(header_html, "ingress_foundation")
        if paragraphs:
            for i, para_text in enumerate(paragraphs, 1):
                recital = _sub(recitals, "recital", eId=f"rec_{i}")
                p = _sub(recital, "p")
                p.text = para_text
        else:
            recital = _sub(recitals, "recital", eId="rec_1")
            p = _sub(recital, "p")
            p.text = foundation

    if action:
        formula_act = _sub(preamble, "formula", name="enactingFormula")
        p = _sub(formula_act, "p")
        p.text = action


def _extract_header_field(header_html: str, css_class: str) -> str:
    """Extract text content from a header div by CSS class."""
    match = re.search(
        rf"<(?:div|h1|h2)\s+class='{css_class}'>(.*?)</(?:div|h1|h2)>",
        header_html, re.DOTALL,
    )
    if not match:
        return ""
    text = _strip_html(match.group(1))
    return text.strip()


def _extract_header_abbreviation(header_html: str) -> str:
    """Extract abbreviation from header, stripping surrounding parentheses."""
    raw = _extract_header_field(header_html, "abbreviation")
    # Header format: "(GemG)" or "(LCo)" — strip outer parens
    if raw.startswith("(") and raw.endswith(")"):
        return raw[1:-1]
    return raw


def _extract_header_paragraphs(header_html: str, css_class: str) -> list[str]:
    """Extract individual <p> blocks from a header div."""
    match = re.search(
        rf"<div\s+class='{css_class}'>(.*?)</div>",
        header_html, re.DOTALL,
    )
    if not match:
        return []
    inner = match.group(1)
    paragraphs = re.findall(r"<p>(.*?)</p>", inner, re.DOTALL)
    return [_strip_html(p).strip() for p in paragraphs if _strip_html(p).strip()]


# --- Sidecar metadata extraction ---


def _extract_document_meta(tol: dict, sv: dict) -> DocumentMeta:
    """Extract sidecar metadata from the API response."""
    sysno = tol["systematic_number"]
    type_id = tol.get("text_of_law_type_id")
    law_type = LAW_TYPE_NAMES.get(type_id, f"unknown ({type_id})") if type_id else ""

    available_langs = [
        al["language"]["iso639_1_code"]
        for al in sv.get("available_languages", [])
    ]

    old_versions = [
        VersionRecord(
            version_id=ov["id"],
            structured_document_id=ov.get("structured_document_id"),
            version_dates_str=ov.get("version_dates_str", ""),
        )
        for ov in tol.get("old_versions", [])
    ]

    change_documents = [
        ChangeDocument(
            id=cd["id"],
            number=cd.get("number", ""),
            title=cd.get("document_title", ""),
            date_of_decision=cd.get("date_of_decision_string", ""),
            date_of_publication=cd.get("date_of_publication_string", ""),
            pdf_link=cd.get("pdf_link", ""),
            for_selected_version=cd.get("for_selected_version", False),
        )
        for cd in tol.get("change_documents", [])
    ]

    materials = [
        Material(
            id=m["id"],
            title=m.get("title", ""),
            url=m.get("url", ""),
        )
        for m in sv.get("materials", [])
    ]

    return DocumentMeta(
        systematic_number=sysno,
        law_type=law_type,
        law_type_id=type_id,
        pdf_link=tol.get("pdf_link", ""),
        pdf_link_tol=sv.get("pdf_link_tol", ""),
        pdf_link_tol_size=sv.get("pdf_link_tol_size", 0) or 0,
        available_languages=available_langs,
        abrogated=tol.get("abrogated", False),
        abrogated_scheduled=tol.get("abrogated_scheduled", False),
        abrogated_dates_str=tol.get("abrogated_dates_str", "") or "",
        old_versions=old_versions,
        change_documents=change_documents,
        materials=materials,
    )


# --- HTML extraction ---


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<br\s*/?>", "\n", text)
    # Handle <sup> by keeping content inline (e.g., "2<sup>bis</sup>" -> "2bis")
    text = re.sub(r"<sup>(.*?)</sup>", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text.strip()


def _extract_article_number(node: dict, lang: str) -> str:
    """Extract clean article number from UID like 't-0--a-3' -> '3'."""
    uid = node.get("uid", "")
    match = re.search(r"--a-(\w+)(?:--|$)", uid)
    if match:
        return match.group(1)
    # Fallback: try the number field in the requested language
    num_raw = node.get("number", {}).get(lang, "")
    match = re.search(r"(\d+\w*)\s*$", _strip_html(num_raw))
    return match.group(1) if match else ""


def _extract_title_number(node: dict, lang: str) -> str:
    """Extract title number from number field."""
    num_raw = node.get("number", {}).get(lang, "")
    return _strip_html(num_raw).strip()


def _extract_title_heading(node: dict, lang: str) -> str:
    """Extract title heading from text field."""
    text = node.get("text", {}).get(lang, "")
    text = _strip_html(text).strip()
    # Remove amendment marker
    text = re.sub(r"\s*\*\s*$", "", text)
    return text


def _extract_text_field(node: dict, lang: str) -> str:
    """Extract clean text from the text field."""
    text = node.get("text", {}).get(lang, "")
    text = _strip_html(text).strip()
    text = re.sub(r"\s*\*\s*$", "", text)
    return text


def _extract_para_number(node: dict, lang: str) -> str:
    """Extract paragraph number from html_content.

    Handles plain numbers and suffixed numbers like "2bis".
    """
    h = node.get("html_content", {}).get(lang, "")
    # Match <span class='number'>1</span> or <span class='number'>2<sup>bis</sup></span>
    match = re.search(r"<span class='number'>(.*?)</span>", h)
    if match:
        return _strip_html(match.group(1))
    return ""


def _clean_para_num(raw: str) -> str:
    """Clean paragraph number for display (just the raw number string)."""
    return raw.strip()


def _extract_para_text(node: dict, lang: str) -> str:
    """Extract paragraph body text from html_content."""
    h = node.get("html_content", {}).get(lang, "")
    match = re.search(r"<span class='text_content'>(.*?)</span>", h, re.DOTALL)
    text = match.group(1) if match else ""
    text = _strip_html(text)
    text = re.sub(r"\s*\*\s*$", "", text)
    return text.strip()


def _extract_enum_content(node: dict, lang: str) -> tuple[str, str]:
    """Extract letter and text from an enumeration node.

    Returns (letter, text), e.g., ("a)", "die Einwohnergemeinden;")
    """
    h = node.get("html_content", {}).get(lang, "")

    letter_match = re.search(
        r"<td class='number'>\s*(.*?)\s*</td>", h, re.DOTALL,
    )
    text_match = re.search(
        r"<td class='left_col[^']*'[^>]*>\s*(.*?)\s*</td>", h, re.DOTALL,
    )

    letter = _strip_html(letter_match.group(1)) if letter_match else ""
    text = _strip_html(text_match.group(1)) if text_match else ""
    text = re.sub(r"\s*\*\s*$", "", text)

    return letter, text


# --- eId helpers ---


def _title_eid(
    parent_el: etree._Element,
    tag: str,
    node: dict,
    lang: str,
) -> str:
    """Build eId for a structural element (title/chapter/section/subdivision).

    Uses sequential position among siblings of same tag.
    """
    # Count existing siblings of same tag to determine position
    existing = [
        child for child in parent_el
        if _local_name(child) == tag
    ]
    position = len(existing) + 1

    local_part = f"{_eid_prefix(tag)}_{position}"

    parent_eid = parent_el.get("eId")
    if parent_eid:
        return f"{parent_eid}/{local_part}"
    return local_part


def _eid_prefix(tag: str) -> str:
    """Map AKN tag name to eId prefix."""
    return {
        "title": "tit",
        "chapter": "chap",
        "section": "sec",
        "subdivision": "subdiv",
        "article": "art",
        "paragraph": "para",
        "item": "lbl",
    }.get(tag, tag)


# --- lxml helpers ---


def _make_element(tag: str, **attrs: str) -> etree._Element:
    """Create an AKN namespace element."""
    return etree.Element(f"{{{AKN_NS}}}{tag}", nsmap=NSMAP, **attrs)


def _sub(parent: etree._Element, tag: str, **attrs: str) -> etree._Element:
    """Create a subelement in the AKN namespace."""
    return etree.SubElement(parent, f"{{{AKN_NS}}}{tag}", **attrs)


def _local_name(el: etree._Element) -> str:
    """Get local name of an element (strip namespace)."""
    tag = el.tag
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag
