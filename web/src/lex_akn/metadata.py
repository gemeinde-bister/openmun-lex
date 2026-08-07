"""FRBR metadata extraction from AKN documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from lex_akn.parse import AKN_NS, akn_tag, find_first, text_content, text_content_compact

_HTML_TAG_RE = re.compile(r"<[^>]+>")

if TYPE_CHECKING:
    from lxml.etree import _Element, _ElementTree


@dataclass(frozen=True)
class FRBRMetadata:
    """Core metadata extracted from an AKN document's FRBR identification."""

    work_uri: str
    expression_uri: str
    manifestation_uri: str
    sr_number: str
    title: str
    short_title: str
    abbreviation: str
    language: str
    date_document: date | None
    date_entry_in_force: date | None
    date_applicability: date | None
    country: str


def _parse_date(date_str: str | None) -> date | None:
    """Parse an ISO date string, returning None if missing or invalid."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return None


def _frbr_uri(identification: _Element, level: str, which: str) -> str:
    """Extract a FRBR URI from identification/FRBRWork|Expression|Manifestation."""
    frbr_level = identification.find(f"{akn_tag(level)}", None)
    if frbr_level is None:
        return ""
    el = frbr_level.find(f"{akn_tag(which)}", None)
    if el is None:
        return ""
    return el.get("value", "")


def _frbr_date(identification: _Element, level: str, name_attr: str) -> date | None:
    """Extract a date from FRBRdate elements by name attribute."""
    frbr_level = identification.find(f"{akn_tag(level)}", None)
    if frbr_level is None:
        return None
    for date_el in frbr_level.findall(f"{akn_tag('FRBRdate')}", None):
        if date_el.get("name") == name_attr:
            return _parse_date(date_el.get("date"))
    return None


def _frbr_date_chain(
    identification: _Element,
    *lookups: tuple[str, str],
) -> date | None:
    """Try multiple (level, name) pairs, return first match."""
    for level, name_attr in lookups:
        result = _frbr_date(identification, level, name_attr)
        if result is not None:
            return result
    return None


def _frbr_alias(frbr_level: _Element | None, name: str) -> str:
    """Extract value from FRBRalias by name attribute.

    Strips any embedded HTML tags (e.g. <sub>2</sub> in CO2 laws).
    """
    if frbr_level is None:
        return ""
    for alias_el in frbr_level.findall(f"{akn_tag('FRBRalias')}", None):
        if alias_el.get("name") == name:
            raw = (alias_el.get("value") or "").strip()
            return _HTML_TAG_RE.sub("", raw)
    return ""


def extract_metadata(tree: _ElementTree) -> FRBRMetadata:
    """Extract FRBR metadata from an AKN document tree.

    Raises ValueError if required identification block is missing.
    """
    identification = tree.find(
        f".//{akn_tag('meta')}/{akn_tag('identification')}"
    )
    if identification is None:
        msg = "No <meta>/<identification> found in AKN document"
        raise ValueError(msg)

    # FRBR URIs
    work_uri = _frbr_uri(identification, "FRBRWork", "FRBRuri")
    expression_uri = _frbr_uri(identification, "FRBRExpression", "FRBRuri")
    manifestation_uri = _frbr_uri(identification, "FRBRManifestation", "FRBRuri")

    # SR number from FRBRnumber
    frbr_work = identification.find(f"{akn_tag('FRBRWork')}", None)
    sr_number = ""
    if frbr_work is not None:
        num_el = frbr_work.find(f"{akn_tag('FRBRnumber')}", None)
        if num_el is not None:
            sr_number = (num_el.get("value") or "").strip()

    # Country
    country = ""
    if frbr_work is not None:
        country_el = frbr_work.find(f"{akn_tag('FRBRcountry')}", None)
        if country_el is not None:
            country = country_el.get("value", "")

    # Language
    language = ""
    frbr_expr = identification.find(f"{akn_tag('FRBRExpression')}", None)

    if frbr_expr is not None:
        lang_el = frbr_expr.find(f"{akn_tag('FRBRlanguage')}", None)
        if lang_el is not None:
            language = lang_el.get("language", "")

    # Title: FRBRname (fedlex) → FRBRalias name="title" (cantonal)
    # FRBRname value attributes may contain embedded HTML (e.g. CO<sub>2</sub>)
    title = ""
    short_title = ""
    if frbr_work is not None:
        for name_el in frbr_work.findall(f"{akn_tag('FRBRname')}", None):
            name_lang = name_el.get("{http://www.w3.org/XML/1998/namespace}lang", "")
            if name_lang == language or (not title and name_lang):
                title = _HTML_TAG_RE.sub("", name_el.get("value", ""))
                short_title = _HTML_TAG_RE.sub("", name_el.get("shortForm", ""))
    if not title:
        title = _frbr_alias(frbr_work, "title")

    # Abbreviation from FRBRalias name="abbreviation" or shortForm on FRBRname
    abbreviation = _frbr_alias(frbr_work, "abbreviation")
    if not abbreviation:
        abbreviation = short_title

    # Also check preface for docTitle (may be more readable).
    # docTitle is often nested inside <p>, so search anywhere under preface.
    preface_title_el = tree.find(
        f".//{akn_tag('preface')}//{akn_tag('docTitle')}"
    )
    if preface_title_el is not None:
        preface_title = text_content_compact(preface_title_el)
        if preface_title:
            title = preface_title

    # Dates with fallback chains: jolux (fedlex) → standard (cantonal)
    date_document = _frbr_date_chain(
        identification,
        ("FRBRWork", "jolux:dateDocument"),
        ("FRBRWork", "decision"),
    )
    date_entry_in_force = _frbr_date_chain(
        identification,
        ("FRBRWork", "jolux:dateEntryInForce"),
        ("FRBRWork", "enactment"),
    )
    date_applicability = _frbr_date_chain(
        identification,
        ("FRBRWork", "jolux:dateApplicability"),
        ("FRBRExpression", "inForceSince"),
    )

    return FRBRMetadata(
        work_uri=work_uri,
        expression_uri=expression_uri,
        manifestation_uri=manifestation_uri,
        sr_number=sr_number,
        title=title,
        short_title=short_title,
        abbreviation=abbreviation,
        language=language,
        date_document=date_document,
        date_entry_in_force=date_entry_in_force,
        date_applicability=date_applicability,
        country=country,
    )
