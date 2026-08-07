"""ELI URI parsing and building.

Supports three namespaces:
- /eli/  — enacted legislation (ELI standard)
- /doc/  — non-legislative reference documents
- /pub/  — mandatory municipal publications
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class EliUri:
    """Parsed European Legislation Identifier URI.

    Supports three levels:
    - /eli/ch/{sr_number}           federal law
    - /eli/vs/{sysno}               cantonal law (Valais)
    - /eli/mun/{bfs}/{id}           municipal law

    Optional suffixes: /{date}/{lang}/{format}
    Fragment: #art_1, #art_1__para_1
    """

    level: str  # "ch", "vs", "mun"
    identifier: str  # SR number, sysno, or "{bfs}/{id}"
    date: str | None = None
    lang: str | None = None
    format: str | None = None
    fragment: str | None = None

    def __str__(self) -> str:
        parts = ["/eli", self.level, self.identifier]
        if self.date is not None:
            parts.append(self.date)
        if self.lang is not None:
            parts.append(self.lang)
        if self.format is not None:
            parts.append(self.format)
        result = "/".join(parts)
        if self.fragment is not None:
            result += f"#{self.fragment}"
        return result

    @property
    def path(self) -> str:
        """URI path without fragment."""
        parts = ["/eli", self.level, self.identifier]
        if self.date is not None:
            parts.append(self.date)
        if self.lang is not None:
            parts.append(self.lang)
        if self.format is not None:
            parts.append(self.format)
        return "/".join(parts)


# Pattern: /eli/{level}/{identifier}[/{date}[/{lang}[/{format}]]][#fragment]
_CH_PATTERN = re.compile(
    r"^/eli/ch/"
    r"(?P<id>[^/#]+)"
    r"(?:/(?P<date>\d{4}-\d{2}-\d{2}))?"
    r"(?:/(?P<lang>[a-z]{2}))?"
    r"(?:/(?P<fmt>\w+))?"
    r"(?:#(?P<frag>.+))?$"
)

_VS_PATTERN = re.compile(
    r"^/eli/vs/"
    r"(?P<id>[^/#]+)"
    r"(?:/(?P<date>\d{4}-\d{2}-\d{2}))?"
    r"(?:/(?P<lang>[a-z]{2}))?"
    r"(?:/(?P<fmt>\w+))?"
    r"(?:#(?P<frag>.+))?$"
)

_MUN_PATTERN = re.compile(
    r"^/eli/mun/"
    r"(?P<bfs>\d+)/"
    r"(?P<entity>[a-z]+(?::[^/#]+)?)/"
    r"(?P<id>[^/#]+)"
    r"(?:/(?P<date>\d{4}-\d{2}-\d{2}))?"
    r"(?:/(?P<lang>[a-z]{2}))?"
    r"(?:/(?P<fmt>\w+))?"
    r"(?:#(?P<frag>.+))?$"
)


def parse_eli(uri: str) -> EliUri:
    """Parse an ELI URI string into an EliUri object.

    Raises ValueError if the URI doesn't match any known pattern.
    """
    for pattern, level, id_builder in [
        (_CH_PATTERN, "ch", lambda m: m.group("id")),
        (_VS_PATTERN, "vs", lambda m: m.group("id")),
        (_MUN_PATTERN, "mun", lambda m: f"{m.group('bfs')}/{m.group('entity')}/{m.group('id')}"),
    ]:
        match = pattern.match(uri)
        if match is not None:
            return EliUri(
                level=level,
                identifier=id_builder(match),
                date=match.group("date"),
                lang=match.group("lang"),
                format=match.group("fmt"),
                fragment=match.group("frag"),
            )

    msg = f"Not a valid ELI URI: {uri}"
    raise ValueError(msg)


def build_eli(
    level: str,
    identifier: str,
    *,
    date: str | None = None,
    lang: str | None = None,
    fmt: str | None = None,
    fragment: str | None = None,
) -> str:
    """Build an ELI URI string from components."""
    assert level in ("ch", "vs", "mun"), f"Invalid level: {level}"
    uri = EliUri(
        level=level,
        identifier=identifier,
        date=date,
        lang=lang,
        format=fmt,
        fragment=fragment,
    )
    return str(uri)


# ===========================================================================
# /doc/ namespace — non-legislative reference documents
# ===========================================================================

@dataclass(frozen=True)
class DocUri:
    """Parsed document URI for non-legislative reference documents.

    Scopes:
    - /doc/{doc_id}                         platform (no jurisdiction)
    - /doc/vs/{doc_id}                      cantonal
    - /doc/bez/{bfs_bez}/{doc_id}           district (future)
    - /doc/mun/{bfs}/{entity}/{doc_id}      municipal

    Optional suffixes: [/{date}][/{lang}][/{format}]
    """

    scope: str  # "platform", "vs", "bez", "mun"
    doc_id: str  # kebab-case slug
    scope_id: str | None = None  # BFS number for mun/bez
    entity: str | None = None  # entity code for mun (eg, bg, gt:slug)
    date: str | None = None
    lang: str | None = None
    format: str | None = None

    def __str__(self) -> str:
        return self._build_parts()

    @property
    def path(self) -> str:
        """URI path (same as __str__ — doc URIs have no fragment)."""
        return self._build_parts()

    @property
    def work_uri(self) -> str:
        """Work-level URI (no date/lang/format)."""
        return str(DocUri(
            scope=self.scope, doc_id=self.doc_id,
            scope_id=self.scope_id, entity=self.entity,
        ))

    def _build_parts(self) -> str:
        if self.scope == "mun":
            assert self.scope_id is not None and self.entity is not None
            parts = ["/doc/mun", self.scope_id, self.entity, self.doc_id]
        elif self.scope == "vs":
            parts = ["/doc/vs", self.doc_id]
        elif self.scope == "bez":
            assert self.scope_id is not None
            parts = ["/doc/bez", self.scope_id, self.doc_id]
        else:
            parts = ["/doc", self.doc_id]
        if self.date is not None:
            parts.append(self.date)
        if self.lang is not None:
            parts.append(self.lang)
        if self.format is not None:
            parts.append(self.format)
        return "/".join(parts)


# doc_id: kebab-case slug (lowercase alphanumeric + hyphens)
_DOC_ID = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_FRBR_SUFFIX = (
    r"(?:/(?P<date>\d{4}-\d{2}-\d{2}))?"
    r"(?:/(?P<lang>[a-z]{2}))?"
    r"(?:/(?P<fmt>\w+))?"
)

_DOC_MUN_PATTERN = re.compile(
    r"^/doc/mun/"
    r"(?P<bfs>\d+)/"
    r"(?P<entity>[a-z]+(?::[^/#]+)?)/"
    rf"(?P<id>{_DOC_ID})"
    rf"{_FRBR_SUFFIX}$"
)

_DOC_VS_PATTERN = re.compile(
    r"^/doc/vs/"
    rf"(?P<id>{_DOC_ID})"
    rf"{_FRBR_SUFFIX}$"
)

_DOC_BEZ_PATTERN = re.compile(
    r"^/doc/bez/"
    r"(?P<bfs_bez>\d+)/"
    rf"(?P<id>{_DOC_ID})"
    rf"{_FRBR_SUFFIX}$"
)

# Platform scope — tried LAST to avoid consuming "vs"/"mun" as doc_id
_DOC_PLATFORM_PATTERN = re.compile(
    r"^/doc/"
    rf"(?P<id>{_DOC_ID})"
    rf"{_FRBR_SUFFIX}$"
)


def parse_doc(uri: str) -> DocUri:
    """Parse a /doc/ URI string into a DocUri object.

    Raises ValueError if the URI doesn't match any known /doc/ pattern.
    """
    m = _DOC_MUN_PATTERN.match(uri)
    if m is not None:
        return DocUri(
            scope="mun", doc_id=m.group("id"),
            scope_id=m.group("bfs"), entity=m.group("entity"),
            date=m.group("date"), lang=m.group("lang"),
            format=m.group("fmt"),
        )

    m = _DOC_VS_PATTERN.match(uri)
    if m is not None:
        return DocUri(
            scope="vs", doc_id=m.group("id"),
            date=m.group("date"), lang=m.group("lang"),
            format=m.group("fmt"),
        )

    m = _DOC_BEZ_PATTERN.match(uri)
    if m is not None:
        return DocUri(
            scope="bez", doc_id=m.group("id"),
            scope_id=m.group("bfs_bez"),
            date=m.group("date"), lang=m.group("lang"),
            format=m.group("fmt"),
        )

    m = _DOC_PLATFORM_PATTERN.match(uri)
    if m is not None:
        return DocUri(
            scope="platform", doc_id=m.group("id"),
            date=m.group("date"), lang=m.group("lang"),
            format=m.group("fmt"),
        )

    msg = f"Not a valid /doc/ URI: {uri}"
    raise ValueError(msg)


# ===========================================================================
# /pub/ namespace — mandatory municipal publications
# ===========================================================================

VALID_ORGANS = frozenset({"assembly", "parliament", "council"})
VALID_DOCTYPES = frozenset({"protocol", "decision", "notice"})


@dataclass(frozen=True)
class PubUri:
    """Parsed publication URI for mandatory municipal publications.

    Pattern: /pub/mun/{bfs}/{entity}/{organ}/{doctype}/{year}/{number}[/{lang}][/{format}]

    No temporal versioning — publications are immutable records.
    """

    bfs: str
    entity: str
    organ: str  # assembly, parliament, council
    doctype: str  # protocol, decision, notice
    year: str
    number: str
    lang: str | None = None
    format: str | None = None

    def __str__(self) -> str:
        parts = [
            "/pub/mun", self.bfs, self.entity,
            self.organ, self.doctype, self.year, self.number,
        ]
        if self.lang is not None:
            parts.append(self.lang)
        if self.format is not None:
            parts.append(self.format)
        return "/".join(parts)

    @property
    def path(self) -> str:
        """URI path (same as __str__ — pub URIs have no fragment)."""
        return str(self)

    @property
    def work_uri(self) -> str:
        """Work-level URI (no lang/format)."""
        return str(PubUri(
            bfs=self.bfs, entity=self.entity,
            organ=self.organ, doctype=self.doctype,
            year=self.year, number=self.number,
        ))


_PUB_MUN_PATTERN = re.compile(
    r"^/pub/mun/"
    r"(?P<bfs>\d+)/"
    r"(?P<entity>[a-z]+(?::[^/#]+)?)/"
    r"(?P<organ>[a-z]+)/"
    r"(?P<doctype>[a-z]+)/"
    r"(?P<year>\d{4})/"
    r"(?P<number>\d+)"
    r"(?:/(?P<lang>[a-z]{2}))?"
    r"(?:/(?P<fmt>\w+))?$"
)


def parse_pub(uri: str) -> PubUri:
    """Parse a /pub/ URI string into a PubUri object.

    Raises ValueError if the URI doesn't match the /pub/mun/ structural
    pattern. Organ and doctype are parsed but NOT validated — the route
    handler is responsible for checking canonical values and resolving
    aliases (to allow 301 redirect before rejection).
    """
    m = _PUB_MUN_PATTERN.match(uri)
    if m is None:
        msg = f"Not a valid /pub/ URI: {uri}"
        raise ValueError(msg)

    return PubUri(
        bfs=m.group("bfs"),
        entity=m.group("entity"),
        organ=m.group("organ"),
        doctype=m.group("doctype"),
        year=m.group("year"),
        number=m.group("number"),
        lang=m.group("lang"),
        format=m.group("fmt"),
    )
