"""Fedlex SPARQL client for federal law metadata and XML retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import json

SPARQL_ENDPOINT = "https://fedlex.data.admin.ch/sparqlendpoint"

_LANG_URI = {
    "de": "http://publications.europa.eu/resource/authority/language/DEU",
    "fr": "http://publications.europa.eu/resource/authority/language/FRA",
    "it": "http://publications.europa.eu/resource/authority/language/ITA",
    "rm": "http://publications.europa.eu/resource/authority/language/ROH",
    "en": "http://publications.europa.eu/resource/authority/language/ENG",
}

# SR main categories for grouping
SR_CATEGORIES = {
    "1": "Staat - Volk - Behörden",
    "2": "Privatrecht - Zivilrechtspflege - Vollstreckung",
    "3": "Strafrecht - Strafrechtspflege - Strafvollzug",
    "4": "Schule - Wissenschaft - Kultur",
    "5": "Landesverteidigung",
    "6": "Finanzen",
    "7": "Öffentliche Werke - Energie - Verkehr",
    "8": "Gesundheit - Arbeit - Soziale Sicherheit",
    "9": "Wirtschaft - Technische Zusammenarbeit",
    "0": "Internationale Verträge",
}

_INDEX_QUERY = """\
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
SELECT ?sr ?law ?title ?short WHERE {
  GRAPH ?g {
    ?law a jolux:ConsolidationAbstract ;
         jolux:inForceStatus <https://fedlex.data.admin.ch/vocabulary/enforcement-status/0> ;
         jolux:isRealizedBy ?expr .
    ?expr jolux:historicalLegalId ?sr ;
          jolux:language <{lang_uri}> ;
          jolux:title ?title .
    OPTIONAL { ?expr jolux:titleShort ?short . }
  }
} ORDER BY ?sr"""

_XML_URL_QUERY = """\
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
SELECT ?url ?date WHERE {
  GRAPH ?g {
    ?version jolux:isMemberOf <{law_uri}> ;
             jolux:dateApplicability ?date ;
             jolux:isRealizedBy ?vExpr .
    ?vExpr jolux:language <{lang_uri}> ;
           jolux:isEmbodiedBy ?manifest .
    ?manifest jolux:format <http://publications.europa.eu/resource/authority/file-type/XML> ;
              jolux:isExemplifiedBy ?url .
    FILTER(?date <= NOW())
  }
} ORDER BY DESC(?date) LIMIT 1"""

# Regex to extract abbreviation from title parentheses, e.g. "... (BV)"
_ABBR_RE = re.compile(r"\(([A-ZÄÖÜ][A-Za-zäöüéèê0-9]{1,10})\)\s*$")


@dataclass(frozen=True)
class FedlexEntry:
    """A federal law entry from the SPARQL index.

    Titles and abbreviations are stored per-language.  The ``title``
    and ``abbreviation`` properties return the primary (first) language
    for backward compatibility (browse page, search index).
    """

    sr: str
    law_uri: str
    titles: dict[str, str]
    abbreviations: dict[str, str]

    @property
    def title(self) -> str:
        """Primary language title (first available)."""
        if not self.titles:
            return ""
        return next(iter(self.titles.values()))

    @property
    def abbreviation(self) -> str:
        """Primary language abbreviation (first available)."""
        if not self.abbreviations:
            return ""
        return next(iter(self.abbreviations.values()))

    def title_for(self, lang: str) -> str:
        """Title in the requested language, falling back to primary."""
        return self.titles.get(lang, self.title)

    def abbreviation_for(self, lang: str) -> str:
        """Abbreviation in the requested language, falling back to primary."""
        return self.abbreviations.get(lang, self.abbreviation)


def _sparql_query(query: str, timeout: int = 30) -> list[dict]:
    """Execute a SPARQL query and return bindings."""
    data = urlencode({"query": query}).encode()
    req = Request(
        SPARQL_ENDPOINT,
        data=data,
        headers={"Accept": "application/sparql-results+json"},
    )
    with urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
    return result["results"]["bindings"]


def fetch_index(lang: str = "de") -> list[FedlexEntry]:
    """Fetch the full SR index of in-force federal laws from Fedlex SPARQL.

    Returns deduplicated entries sorted by SR number. Multiple SPARQL rows
    per SR (historical title versions) are collapsed, preferring entries
    that have an abbreviation.
    """
    lang_uri = _LANG_URI[lang]
    query = _INDEX_QUERY.replace("{lang_uri}", lang_uri)
    bindings = _sparql_query(query, timeout=60)

    # Deduplicate: multiple rows per SR due to historical versions.
    # Keep the last title per SR (newest version) but collect abbreviation
    # from any row that has one.
    by_sr: dict[str, tuple[str, str, str]] = {}  # sr → (law_uri, title, short)
    for b in bindings:
        sr = b["sr"]["value"]
        law_uri = b["law"]["value"]
        title = b["title"]["value"]
        short = b.get("short", {}).get("value", "")
        prev = by_sr.get(sr)
        if prev is None:
            by_sr[sr] = (law_uri, title, short)
        else:
            # Always take the latest title, but preserve abbreviation
            kept_short = short or prev[2]
            by_sr[sr] = (law_uri, title, kept_short)

    entries = []
    for sr, (law_uri, title, short) in by_sr.items():
        abbr = short
        if not abbr:
            m = _ABBR_RE.search(title)
            if m:
                abbr = m.group(1)
        entries.append(FedlexEntry(
            sr=sr, law_uri=law_uri,
            titles={lang: title}, abbreviations={lang: abbr} if abbr else {},
        ))

    entries.sort(key=_sr_sort_key)
    return entries


def fetch_xml_url(law_uri: str, lang: str = "de") -> str | None:
    """Find the XML download URL for the latest version of a federal law.

    Returns None if no XML is available.
    """
    lang_uri = _LANG_URI[lang]
    query = _XML_URL_QUERY.replace("{law_uri}", law_uri).replace("{lang_uri}", lang_uri)
    bindings = _sparql_query(query, timeout=30)
    if not bindings:
        return None
    return bindings[0]["url"]["value"]


def fetch_xml(law_uri: str, lang: str = "de") -> bytes | None:
    """Download the AKN XML for the latest version of a federal law.

    Returns raw XML bytes, or None if not available.
    """
    url = fetch_xml_url(law_uri, lang)
    if url is None:
        return None
    req = Request(url)
    with urlopen(req, timeout=60) as resp:
        return resp.read()


def sr_category(sr: str) -> str:
    """Return the main category digit for an SR number."""
    if sr.startswith("0."):
        return "0"
    return sr[0] if sr and sr[0].isdigit() else ""


def _sr_sort_key(entry: FedlexEntry) -> tuple:
    """Sort key for SR numbers: numeric parts, with 0.* (treaties) last."""
    sr = entry.sr
    # Treaties (0.*) sort after domestic law (1-9)
    if sr.startswith("0."):
        prefix = (10,)
        rest = sr[2:]
    else:
        prefix = ()
        rest = sr
    parts: list[int] = []
    for segment in rest.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return prefix + tuple(parts)
