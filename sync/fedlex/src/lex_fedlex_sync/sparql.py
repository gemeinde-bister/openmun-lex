"""Fedlex SPARQL client for federal law metadata and XML retrieval.

Trilingual sync: each query fetches all requested languages (de, fr, it)
in a single SPARQL call, returning per-language titles, abbreviations,
and XML download URLs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

SPARQL_ENDPOINT = "https://fedlex.data.admin.ch/sparqlendpoint"

LANG_URI = {
    "de": "http://publications.europa.eu/resource/authority/language/DEU",
    "fr": "http://publications.europa.eu/resource/authority/language/FRA",
    "it": "http://publications.europa.eu/resource/authority/language/ITA",
}

# Default languages to sync.
SYNC_LANGS = ("de", "fr", "it")

# Language URI suffix → short code mapping
_LANG_SUFFIX_TO_CODE = {"DEU": "de", "FRA": "fr", "ITA": "it"}

# SR number is sourced from the law's legal-taxonomy classification
# (?tax skos:notation), NOT from the expression's jolux:historicalLegalId.
# Reason: a totally-revised act that keeps its SR number is published by Fedlex
# as a *new* ConsolidationAbstract whose expression has NO historicalLegalId —
# the SR only exists on the taxonomy entry.  Requiring historicalLegalId
# silently dropped 369 in-force acts (incl. the 2020 DSG, SR 235.1).  The
# taxonomy notation is a strict superset of historicalLegalId (verified: every
# historicalLegalId law also carries a matching taxonomy notation) and is the
# canonical SR shown on fedlex.admin.ch.  The notation triple lives in the
# vocabulary graph, so it sits OUTSIDE the per-law `GRAPH ?g` block.
_INDEX_QUERY_MULTILANG = """\
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT ?sr ?law ?lang ?title ?short WHERE {{
  GRAPH ?g {{
    ?law a jolux:ConsolidationAbstract ;
         jolux:inForceStatus <https://fedlex.data.admin.ch/vocabulary/enforcement-status/0> ;
         jolux:classifiedByTaxonomyEntry ?tax ;
         jolux:isRealizedBy ?expr .
    ?expr jolux:language ?lang ;
          jolux:title ?title .
    OPTIONAL {{ ?expr jolux:titleShort ?short . }}
    FILTER(?lang IN ({lang_filter}))
  }}
  ?tax skos:notation ?sr .
}} ORDER BY ?sr"""

_ALL_XML_URLS_MULTILANG_QUERY = """\
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
SELECT ?lang ?url ?date WHERE {{
  GRAPH ?g {{
    ?version jolux:isMemberOf <{law_uri}> ;
             jolux:dateApplicability ?date ;
             jolux:isRealizedBy ?vExpr .
    ?vExpr jolux:language ?lang ;
           jolux:isEmbodiedBy ?manifest .
    ?manifest jolux:format <http://publications.europa.eu/resource/authority/file-type/XML> ;
              jolux:isExemplifiedBy ?url .
    FILTER(?date <= NOW())
    FILTER(?lang IN ({lang_filter}))
  }}
}} ORDER BY DESC(?date) ?lang"""

# Regex to extract abbreviation from title parentheses, e.g. "... (BV)"
_ABBR_RE = re.compile(r"\(([A-ZÄÖÜ][A-Za-zäöüéèê0-9.]{0,14})\)\s*$")


@dataclass(frozen=True)
class FedlexEntry:
    """A federal law entry from the SPARQL index.

    Titles and abbreviations are stored per-language.  The ``title``
    and ``abbreviation`` properties return the primary (first) language
    for backward compatibility.
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


@dataclass(frozen=True)
class VersionInfo:
    """A version of a federal law with per-language XML download URLs."""

    date: str
    urls: dict[str, str] = field(default_factory=dict)


def _lang_uri_to_code(uri: str) -> str | None:
    """Convert a language URI to a short code, or None if unknown."""
    suffix = uri.rsplit("/", 1)[-1]
    return _LANG_SUFFIX_TO_CODE.get(suffix)


def _lang_filter(langs: tuple[str, ...]) -> str:
    """Build a SPARQL IN(...) filter value for the given languages."""
    return ", ".join(f"<{LANG_URI[lang]}>" for lang in langs)


def sparql_query(
    client: httpx.Client, query: str, timeout: int = 30,
) -> list[dict]:
    """Execute a SPARQL query and return bindings."""
    resp = client.post(
        SPARQL_ENDPOINT,
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["results"]["bindings"]


def fetch_index(
    client: httpx.Client,
    langs: tuple[str, ...] = SYNC_LANGS,
) -> list[FedlexEntry]:
    """Fetch the full SR index of in-force federal laws.

    Fetches titles and abbreviations in all requested languages in a
    single SPARQL call.  Returns deduplicated entries sorted by SR number.
    """
    query = _INDEX_QUERY_MULTILANG.format(lang_filter=_lang_filter(langs))
    bindings = sparql_query(client, query, timeout=120)

    # Collect per-SR, per-language data
    by_sr: dict[str, dict] = {}
    for b in bindings:
        sr = b["sr"]["value"]
        law_uri = b["law"]["value"]
        lang_code = _lang_uri_to_code(b["lang"]["value"])
        if lang_code is None:
            continue
        title = b["title"]["value"]
        short = b.get("short", {}).get("value", "")

        if sr not in by_sr:
            by_sr[sr] = {"law_uri": law_uri, "titles": {}, "abbreviations": {}}
        entry = by_sr[sr]
        # Keep first title per language (dedup across multiple SPARQL rows)
        if lang_code not in entry["titles"]:
            entry["titles"][lang_code] = title
        # Accumulate abbreviation: explicit titleShort wins, then regex from title
        if lang_code not in entry["abbreviations"]:
            if short:
                entry["abbreviations"][lang_code] = short
            else:
                m = _ABBR_RE.search(title)
                if m:
                    entry["abbreviations"][lang_code] = m.group(1)
        elif short and not entry["abbreviations"].get(lang_code):
            entry["abbreviations"][lang_code] = short

    entries = []
    for sr, data in by_sr.items():
        entries.append(FedlexEntry(
            sr=sr,
            law_uri=data["law_uri"],
            titles=data["titles"],
            abbreviations=data["abbreviations"],
        ))

    entries.sort(key=sr_sort_key)
    return entries


def fetch_all_xml_urls(
    client: httpx.Client,
    law_uri: str,
    langs: tuple[str, ...] = SYNC_LANGS,
) -> list[VersionInfo]:
    """Find XML URLs for all in-force versions, all languages (newest first).

    Returns a list of VersionInfo, each with a dict of per-language URLs.
    Returns empty list if no XML versions are available.
    """
    query = _ALL_XML_URLS_MULTILANG_QUERY.format(
        law_uri=law_uri,
        lang_filter=_lang_filter(langs),
    )
    bindings = sparql_query(client, query, timeout=30)

    # Group by date → {lang: url}
    by_date: dict[str, dict[str, str]] = {}
    for b in bindings:
        date = b["date"]["value"]
        lang_code = _lang_uri_to_code(b["lang"]["value"])
        url = b["url"]["value"]
        if lang_code is None:
            continue
        if date not in by_date:
            by_date[date] = {}
        by_date[date][lang_code] = url

    # Build VersionInfo list, sorted by date descending
    versions = [
        VersionInfo(date=date, urls=urls)
        for date, urls in sorted(by_date.items(), reverse=True)
    ]
    return versions


_LAW_STATUS_QUERY = """\
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
SELECT ?status (MAX(?dnf) AS ?repealed) WHERE {{
  GRAPH ?g {{
    <{law_uri}> jolux:inForceStatus ?status .
    OPTIONAL {{ <{law_uri}> jolux:dateNoLongerInForce ?dnf . }}
  }}
}} GROUP BY ?status"""


def fetch_law_status(
    client: httpx.Client, law_uri: str,
) -> tuple[str, str | None]:
    """Fetch a law's current enforcement status and repeal date.

    Used to classify acts that have dropped out of the in-force index: a
    previously-synced act that is now ``inForceStatus`` != 0 has been
    repealed (3), made obsolete (2), suspended (5), etc.

    Returns ``(status_code, repealed_date)`` where status_code is the
    enforcement-status vocabulary suffix ("0".."5") and repealed_date is the
    latest ``dateNoLongerInForce`` (ISO date) or None if the act is still in
    force / has no such date.  Returns ("0", None) if the URI is unknown.
    """
    query = _LAW_STATUS_QUERY.format(law_uri=law_uri)
    bindings = sparql_query(client, query, timeout=30)
    if not bindings:
        return ("0", None)
    # An abstract has a single inForceStatus; take the first binding.
    b = bindings[0]
    status_code = b["status"]["value"].rsplit("/", 1)[-1]
    repealed = b.get("repealed", {}).get("value") or None
    return (status_code, repealed)


def sr_sort_key(entry: FedlexEntry) -> tuple:
    """Sort key for SR numbers: numeric parts, with 0.* (treaties) last."""
    sr = entry.sr
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


def make_client() -> httpx.Client:
    """Create a configured httpx client for Fedlex SPARQL."""
    return httpx.Client(
        timeout=30.0,
        follow_redirects=True,
    )
