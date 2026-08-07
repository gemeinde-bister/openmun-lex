"""Tantivy search index: schema, analyzers, query execution.

Library module — no lxml imports, usable from both web app and build script.
"""

from __future__ import annotations

import math
import re
import time
from html import escape as html_escape
from pathlib import Path

import tantivy
from tantivy import Occur, Query


# ---------------------------------------------------------------------------
# Document type classification
# ---------------------------------------------------------------------------

# Canonical doc_type values, ordered by ranking weight (highest first).
# Used for boost factors and UI display.
DOC_TYPES = (
    "verfassung",
    "gesetz",
    "verordnung",
    "reglement",
    "beschluss",
    "konkordat",
    "treaty",
    "other",
)

# Boost factors per doc_type — applied as score multiplier.
# Gesetz (primary legislation) ranks highest; treaty excluded by default.
_DOC_TYPE_BOOST: dict[str, float] = {
    "verfassung": 3.0,
    "gesetz": 2.5,
    "verordnung": 1.5,
    "reglement": 1.2,
    "beschluss": 1.0,
    "konkordat": 0.8,
    "treaty": 0.5,
    "other": 1.0,
}

# Boost factors per source level — municipal clerk perspective.
_LEVEL_BOOST: dict[str, float] = {
    "mun": 3.0,
    "vs": 2.0,
    "ch": 1.0,
}

# Title first-word → doc_type mapping.
# Covers ~95% of the corpus based on analysis.
_TITLE_GESETZ = frozenset({
    "Bundesgesetz", "Gesetz", "Ausführungsgesetz", "Einführungsgesetz",
    "Verfassung", "Bundesverfassung", "Asylgesetz", "Tierseuchengesetz",
    "Strahlenschutzgesetz",
})
_TITLE_VERORDNUNG = frozenset({
    "Verordnung", "Organisationsverordnung", "Asylverordnung",
    "Gebührenverordnung", "Zollverordnung", "Personalverordnung",
    "Vollziehungsverordnung", "Ausführungsverordnung",
    "Anwendungsverordnung", "Lärmschutz-Verordnung",
    "Milchprüfungsverordnung", "Tierseuchenverordnung",
})
_TITLE_REGLEMENT = frozenset({
    "Reglement", "Geschäftsreglement", "Vorsorgereglement",
    "Informationsreglement", "Organisationsreglement",
    "Personalreglement", "Geschäftsordnung",
    "Ausführungsreglement", "Studienreglement",
    "Spesenreglement", "Kehrichtreglement",
})
_TITLE_BESCHLUSS = frozenset({
    "Bundesbeschluss", "Bundesratsbeschluss", "Beschluss",
    "Entscheid", "Dekret",
})
_TITLE_KONKORDAT = frozenset({
    "Interkantonale", "Konkordat", "Westschweizer",
})
_TITLE_VERFASSUNG = frozenset({
    "Verfassung", "Bundesverfassung", "Kantonsverfassung",
})

# Cantonal meta.json law_type → doc_type
_VS_LAW_TYPE_MAP: dict[str, str] = {
    "Verfassung": "verfassung",
    "Gesetz": "gesetz",
    "Verordnung": "verordnung",
    "Reglement": "reglement",
    "Beschluss": "beschluss",
    "Beschluss GR": "beschluss",
    "Entscheid StR": "beschluss",
    "Dekret": "beschluss",
    "Interkantonale Vereinbarung": "konkordat",
    "Staatsvertrag": "konkordat",
}


def classify_doc_type(
    *,
    title: str,
    sr_number: str = "",
    level: str = "",
    law_type: str = "",
) -> str:
    """Classify a document into a canonical doc_type.

    Uses multiple signals:
    - SR number prefix 0.* → treaty (federal only)
    - Cantonal meta.json law_type field (most reliable for VS)
    - Title first word (fallback, ~95% accurate)
    """
    # Federal treaties: SR 0.* is 100% reliable
    if level == "ch" and sr_number.startswith("0."):
        return "treaty"

    # Cantonal: use law_type from meta.json if available
    if law_type:
        mapped = _VS_LAW_TYPE_MAP.get(law_type)
        if mapped:
            return mapped

    # Fallback: title first word
    first_word = title.split()[0] if title else ""

    if first_word in _TITLE_VERFASSUNG:
        return "verfassung"
    if first_word in _TITLE_GESETZ:
        return "gesetz"
    if first_word in _TITLE_VERORDNUNG:
        return "verordnung"
    if first_word in _TITLE_REGLEMENT:
        return "reglement"
    if first_word in _TITLE_BESCHLUSS:
        return "beschluss"
    if first_word in _TITLE_KONKORDAT:
        return "konkordat"

    # Compound word suffix matching for German legal titles:
    # "Gewässerschutzverordnung" → verordnung, "Tierseuchengesetz" → gesetz
    # Also check first few words for "Kantonales Gewässerschutzgesetz",
    # "Schweizerisches Strafgesetzbuch", "Regierungs- und ...gesetz"
    _GESETZ_SUFFIXES = ("gesetz", "gesetzbuch")
    words = title.split()
    for w in words[:4]:
        wl = w.rstrip(",-()").lower()
        if not wl:
            continue
        if wl.endswith("verfassung"):
            return "verfassung"
        if any(wl.endswith(s) for s in _GESETZ_SUFFIXES):
            return "gesetz"
        if wl.endswith("verordnung"):
            return "verordnung"
        if wl.endswith("reglement"):
            return "reglement"

    # Treaty heuristics for titles not caught by SR prefix
    _TREATY_WORDS = {
        "Abkommen", "Übereinkommen", "Vereinbarung", "Vertrag",
        "Protokoll", "Rahmenabkommen", "Freihandelsabkommen",
        "Zusatzprotokoll", "Übereinkunft", "Europäisches",
        "Internationales", "Zusatzabkommen", "Handelsabkommen",
        "Zollabkommen", "Staatsvertrag", "Auslieferungsvertrag",
        "Änderungsprotokoll", "Fakultativprotokoll", "Konvention",
        "Notenaustausch", "Briefwechsel", "Notenwechsel",
        "Verwaltungsvereinbarung",
    }
    if first_word in _TREATY_WORDS:
        return "treaty"

    return "other"


# ---------------------------------------------------------------------------
# Analyzers
# ---------------------------------------------------------------------------

def german_ae_fold(s: str) -> str:
    """German-specific umlaut folding: ä→ae, ö→oe, ü→ue, ß→ss.

    Unlike generic ascii_fold (ä→a), this preserves German phonetic
    distinctions so that "Bau" and "Bäuer" don't collide after stemming.
    Applied to index text and query strings for the *_ae fields.
    """
    return (
        s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
        .replace("ß", "ss")
    )


def load_compound_dict(dict_path: Path) -> list[str]:
    """Load compound constituent words from a dictionary file.

    One word per line, # comments, blank lines ignored.
    Returns sorted unique words.
    """
    if not dict_path.exists():
        return []
    words = []
    for line in dict_path.read_text(encoding="utf-8").splitlines():
        w = line.strip().lower()
        if w and not w.startswith("#"):
            words.append(w)
    return sorted(set(words))


# Supported index languages
SUPPORTED_INDEX_LANGS = ("de", "fr", "it")


def _build_de_law(compound_words: list[str] | None) -> tantivy.TextAnalyzer:
    """Stemmed German analyzer.

    Pipeline: simple → remove_long → lowercase → [split_compound] →
    stopword(german) → stemmer(german).
    """
    builder = (
        tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple())
        .filter(tantivy.Filter.remove_long(100))
        .filter(tantivy.Filter.lowercase())
    )
    if compound_words:
        builder = builder.filter(tantivy.Filter.split_compound(compound_words))
    return (
        builder
        .filter(tantivy.Filter.stopword("german"))
        .filter(tantivy.Filter.stemmer("german"))
        .build()
    )


def _build_de_prefix(compound_words: list[str] | None) -> tantivy.TextAnalyzer:
    """Unstemmed analyzer for prefix/typeahead matching.

    Pipeline: simple → remove_long → lowercase → [split_compound].
    """
    builder = (
        tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple())
        .filter(tantivy.Filter.remove_long(100))
        .filter(tantivy.Filter.lowercase())
    )
    if compound_words:
        builder = builder.filter(tantivy.Filter.split_compound(compound_words))
    return builder.build()


def _build_romanic_law(language: str) -> tantivy.TextAnalyzer:
    """Stemmed French or Italian analyzer.

    Pipeline: simple → remove_long → lowercase → ascii_fold →
    stopword({language}) → stemmer({language}).

    ascii_fold handles accented chars natively (é→e, ç→c, œ→oe).
    No compound splitting needed for Romance languages.
    """
    assert language in ("french", "italian"), f"Unsupported: {language}"
    return (
        tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple())
        .filter(tantivy.Filter.remove_long(100))
        .filter(tantivy.Filter.lowercase())
        .filter(tantivy.Filter.ascii_fold())
        .filter(tantivy.Filter.stopword(language))
        .filter(tantivy.Filter.stemmer(language))
        .build()
    )


def _build_romanic_prefix(language: str) -> tantivy.TextAnalyzer:
    """Unstemmed French or Italian analyzer for prefix/typeahead matching.

    Pipeline: simple → remove_long → lowercase → ascii_fold.
    """
    assert language in ("french", "italian"), f"Unsupported: {language}"
    return (
        tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple())
        .filter(tantivy.Filter.remove_long(100))
        .filter(tantivy.Filter.lowercase())
        .filter(tantivy.Filter.ascii_fold())
        .build()
    )


# Tokenizer name for the case-insensitive abbreviation (short-label) field.
# Language-independent: a single keyword token, lowercased.
ABBREV_TOKENIZER = "abbrev_lc"


def _build_abbrev_lc() -> tantivy.TextAnalyzer:
    """Case-insensitive keyword analyzer for the abbreviation field.

    Pipeline: raw → lowercase.  The raw tokenizer keeps the whole value as a
    single token (so "kStromVG" stays one term, not split on the case change),
    and lowercase makes the match case-insensitive.  This lets users look up a
    law by its official short label in any case — "stromvv", "StromVV", "GIDA",
    "or" — via an exact term query on the lowercased token.
    """
    return (
        tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.raw())
        .filter(tantivy.Filter.lowercase())
        .build()
    )


def build_analyzers(
    compound_words: list[str] | None = None,
    *,
    lang: str = "de",
) -> dict[str, tantivy.TextAnalyzer]:
    """Build analyzers for the law search index in the given language.

    Returns a dict of {tokenizer_name: analyzer} to register on the index.

    German (de): Two parallel analyzer sets for ae-fold architecture.
    French (fr) / Italian (it): Single set with ascii_fold in pipeline.
    All languages use the same field names (title, body, title_ae, etc.)
    but different tokenizer names per language.
    """
    assert lang in SUPPORTED_INDEX_LANGS, f"Unsupported lang: {lang}"

    if lang == "de":
        ae_words = (
            [german_ae_fold(w) for w in compound_words]
            if compound_words else None
        )
        if ae_words:
            ae_words = sorted(set(ae_words))
        return {
            "de_law": _build_de_law(compound_words),
            "de_law_ae": _build_de_law(ae_words),
            "de_prefix": _build_de_prefix(compound_words),
            "de_prefix_ae": _build_de_prefix(ae_words),
            ABBREV_TOKENIZER: _build_abbrev_lc(),
        }

    # French / Italian: ascii_fold is in the pipeline, so the "fold"
    # variant is the same analyzer (no separate ae-fold needed).
    tantivy_lang = {"fr": "french", "it": "italian"}[lang]
    law = _build_romanic_law(tantivy_lang)
    prefix = _build_romanic_prefix(tantivy_lang)
    return {
        f"{lang}_law": law,
        f"{lang}_law_fold": law,
        f"{lang}_prefix": prefix,
        f"{lang}_prefix_fold": prefix,
        ABBREV_TOKENIZER: _build_abbrev_lc(),
    }



# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def build_schema(lang: str = "de") -> tantivy.Schema:
    """Build the search index schema for the given language.

    Fields come in pairs: original (stored, for display/snippets) and
    fold variant (not stored, for matching with correct folding).

    German: ae-folded fields (ä→ae, ö→oe, ü→ue).
    French/Italian: fold fields use the same ascii_fold analyzer.
    """
    assert lang in SUPPORTED_INDEX_LANGS, f"Unsupported lang: {lang}"

    # Tokenizer naming: de uses de_law/de_law_ae, fr uses fr_law/fr_law_fold, etc.
    if lang == "de":
        law_name = "de_law"
        law_fold_name = "de_law_ae"
        prefix_name = "de_prefix"
        prefix_fold_name = "de_prefix_ae"
    else:
        law_name = f"{lang}_law"
        law_fold_name = f"{lang}_law_fold"
        prefix_name = f"{lang}_prefix"
        prefix_fold_name = f"{lang}_prefix_fold"

    builder = tantivy.SchemaBuilder()
    # Original text — stored for display and snippet highlighting
    builder.add_text_field("title", stored=True, tokenizer_name=law_name)
    builder.add_text_field("body", stored=True, tokenizer_name=law_name)
    builder.add_text_field("body_prefix", stored=False, tokenizer_name=prefix_name)
    # Fold text — not stored, for matching
    builder.add_text_field("title_ae", stored=False, tokenizer_name=law_fold_name)
    builder.add_text_field("body_ae", stored=False, tokenizer_name=law_fold_name)
    builder.add_text_field("body_prefix_ae", stored=False, tokenizer_name=prefix_fold_name)
    # Metadata — stored, exact match
    # abbreviation uses a case-insensitive keyword analyzer (raw + lowercase)
    # so short labels match in any case; stored value keeps original casing.
    builder.add_text_field("abbreviation", stored=True, tokenizer_name=ABBREV_TOKENIZER)
    builder.add_text_field("eli_path", stored=True, tokenizer_name="raw")
    builder.add_text_field("sr_number", stored=True, tokenizer_name="raw")
    builder.add_text_field("level", stored=True, tokenizer_name="raw")
    builder.add_text_field("doc_type", stored=True, tokenizer_name="raw")
    builder.add_facet_field("classification")
    return builder.build()


def open_index(
    index_dir: Path,
    compound_words: list[str] | None = None,
    *,
    lang: str = "de",
) -> tantivy.Index:
    """Open an existing Tantivy index and register analyzers.

    compound_words: list of constituent words for German compound splitting.
    If None, compound splitting is disabled (no split_compound filter).
    Load via load_compound_dict() before calling.
    lang: language of this index ("de", "fr", or "it").
    """
    assert index_dir.is_dir(), f"Index directory not found: {index_dir}"

    schema = build_schema(lang)
    index = tantivy.Index(schema, path=str(index_dir))
    for name, analyzer in build_analyzers(compound_words, lang=lang).items():
        index.register_tokenizer(name, analyzer)
    return index


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------

# Doc types excluded from default search (no doc_type filter active).
DEFAULT_EXCLUDED_TYPES = frozenset({"treaty"})


def fold_query(query_str: str, lang: str = "de") -> str:
    """Fold a query string for the ae/fold index fields.

    German: ae-fold (ä→ae, ö→oe, ü→ue, ß→ss).
    French/Italian: identity (ascii_fold is in the analyzer pipeline).
    """
    if lang == "de":
        return german_ae_fold(query_str)
    return query_str


def fold_text(text: str, lang: str = "de") -> str:
    """Fold index text for the ae/fold fields.

    German: ae-fold (ä→ae, ö→oe, ü→ue, ß→ss).
    French/Italian: identity (ascii_fold is in the analyzer pipeline).
    """
    if lang == "de":
        return german_ae_fold(text)
    return text


# Accent character class expansion for French/Italian highlighting.
# Maps base char to regex character class matching accented variants.
_ACCENT_MAP: dict[str, str] = {
    "a": "[aàâä]", "e": "[eéèêë]", "i": "[iîïì]",
    "o": "[oôöò]", "u": "[uùûü]", "c": "[cç]",
    "A": "[AÀÂÄ]", "E": "[EÉÈÊË]", "I": "[IÎÏÌ]",
    "O": "[OÔÖÒ]", "U": "[UÙÛÜ]", "C": "[CÇ]",
}


def _accent_insensitive_pattern(word: str) -> str:
    """Convert a word to an accent-insensitive regex pattern.

    Each char that has accented variants is replaced with a character
    class: "procédure" → "proc[eéèêë]dure".
    Special regex chars are escaped per-character.
    """
    parts = []
    for ch in word:
        expanded = _ACCENT_MAP.get(ch)
        if expanded:
            parts.append(expanded)
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


def _parse_lenient(
    index: tantivy.Index,
    query_str: str,
    fields: list[str],
) -> Query:
    """Parse user input into a query, tolerating query-syntax keywords.

    Tantivy's strict parser treats bare AND/OR/NOT and characters like ':',
    '(', '*' as query DSL.  A law search box receives plain words, not the DSL,
    so a short label such as "OR" (Obligationenrecht) would otherwise raise
    ``Syntax Error: OR`` and fail the whole search.  parse_query_lenient returns
    ``(query, errors)``; we use the best-effort query and ignore the errors.
    """
    query, _errors = index.parse_query_lenient(query_str, fields)
    return query


def _build_match_query(
    index: tantivy.Index,
    query_str: str,
    *,
    lang: str = "de",
) -> Query:
    """Build the document-matching query using fold fields.

    German: Searches title_ae + body_ae (ae-folded text) to prevent
    false positives from the stemmer's ä→a folding.
    French/Italian: Searches same field names (title_ae, body_ae) but
    ascii_fold is handled by the analyzer, so query passes through as-is.

    Single-token queries additionally get a case-insensitive exact match on the
    `abbreviation` field, so a law can be found by its official short label in
    any case ("stromvv", "StromVV", "GIDA", "or").  The abbreviation field is
    indexed with a raw+lowercase analyzer, so the lookup term is the plain
    lowercased query — never ae-folded.
    """
    schema = index.schema
    q_folded = fold_query(query_str, lang)

    title_query = _parse_lenient(index, q_folded, ["title_ae"])
    body_query = _parse_lenient(index, q_folded, ["body_ae"])

    words = q_folded.lower().split()
    last_word_prefix = words[-1] + ".*" if words else ""
    parts = [
        (Occur.Should, Query.boost_query(title_query, 3.0)),
        (Occur.Should, body_query),
    ]
    if last_word_prefix:
        prefix_query = Query.regex_query(
            schema, "body_prefix_ae", last_word_prefix,
        )
        parts.append((Occur.Should, prefix_query))

    # Abbreviation (short-label) match: case-insensitive exact term on the
    # lowercased abbreviation token.  Additive and harmless for ordinary words
    # (an unknown abbreviation simply matches nothing); the high boost floats a
    # genuine short-label hit to the top.  Restricted to single-token queries —
    # a multi-word query is a phrase search, not a label lookup.
    raw_words = query_str.split()
    if len(raw_words) == 1:
        abbrev_query = Query.term_query(
            schema, "abbreviation", raw_words[0].lower(),
        )
        parts.append((Occur.Should, Query.boost_query(abbrev_query, 8.0)))

    return Query.boolean_query(parts)


def _build_highlight_pattern(
    query_str: str,
    *,
    lang: str = "de",
) -> re.Pattern | None:
    """Build a regex pattern for language-aware snippet highlighting.

    German: Substring matching on original text that correctly distinguishes
    ä from a (and ö/ü from o/u) — no false positives from stemmer folding.

    French/Italian: Accent-insensitive matching via character class expansion
    so "procedure" highlights "procédure" and vice versa.

    Examples (de):
        "bäu"   → matches "bäuerlich" but NOT "Bauzone"
    Examples (fr):
        "procédure" → matches "procedure", "procédure"
    """
    words = query_str.split()
    if not words:
        return None
    # Longer terms first so they match greedily before shorter substrings
    words = sorted(set(words), key=len, reverse=True)

    if lang == "de":
        # German: exact substring matching (umlaut-aware)
        alternatives = [rf"\w*{re.escape(w)}\w*" for w in words]
    else:
        # French/Italian: accent-insensitive via character class expansion
        alternatives = [
            r"\w*" + _accent_insensitive_pattern(w) + r"\w*"
            for w in words
        ]

    return re.compile(
        r"\b(?:" + "|".join(alternatives) + r")\b",
        re.IGNORECASE | re.UNICODE,
    )


def _highlight_text(
    text: str,
    pattern: re.Pattern,
    max_chars: int = 0,
) -> str:
    """Generate HTML with <b>-highlighted matches.

    If max_chars > 0, selects the best fragment (highest match density)
    and truncates with ellipsis.  If max_chars == 0, highlights the full text.
    """
    if not text:
        return ""

    matches = list(pattern.finditer(text))

    if max_chars and len(text) > max_chars:
        if matches:
            # Find window with most matches
            best_start = max(0, matches[0].start() - 60)
            best_count = 0
            for m in matches:
                window_start = max(0, m.start() - 60)
                window_end = window_start + max_chars
                count = sum(
                    1 for m2 in matches
                    if window_start <= m2.start() < window_end
                )
                if count > best_count:
                    best_count = count
                    best_start = window_start
            frag_start = best_start
        else:
            frag_start = 0
        frag_end = min(len(text), frag_start + max_chars)
        fragment = text[frag_start:frag_end]
        # Re-find matches within fragment
        matches = list(pattern.finditer(fragment))
        prefix = "\u2026 " if frag_start > 0 else ""
        suffix = " \u2026" if frag_end < len(text) else ""
    else:
        fragment = text
        prefix = suffix = ""

    # Build HTML: split at match boundaries, escape each part
    parts = []
    last_end = 0
    for m in matches:
        parts.append(html_escape(fragment[last_end:m.start()]))
        parts.append(f"<b>{html_escape(m.group())}</b>")
        last_end = m.end()
    parts.append(html_escape(fragment[last_end:]))

    return prefix + "".join(parts) + suffix


def _apply_filters(
    schema,
    base_query: Query,
    *,
    level: str | list[str] | None = None,
    doc_type: str | list[str] | None = None,
    include_treaties: bool = False,
) -> Query:
    """Wrap base_query with level/doc_type filters.

    level/doc_type accept a single value or a list for multi-select.
    When no doc_type filter is active and include_treaties is False,
    treaties are excluded from results by default.
    """
    parts = [(Occur.Must, base_query)]

    # Normalize to list
    levels = [level] if isinstance(level, str) else (level or [])
    doc_types = [doc_type] if isinstance(doc_type, str) else (doc_type or [])

    if len(levels) == 1:
        parts.append((Occur.Must, Query.term_query(schema, "level", levels[0])))
    elif len(levels) > 1:
        level_clause = Query.boolean_query([
            (Occur.Should, Query.term_query(schema, "level", lv))
            for lv in levels
        ])
        parts.append((Occur.Must, level_clause))

    if len(doc_types) == 1:
        parts.append((Occur.Must, Query.term_query(schema, "doc_type", doc_types[0])))
    elif len(doc_types) > 1:
        dt_clause = Query.boolean_query([
            (Occur.Should, Query.term_query(schema, "doc_type", dt))
            for dt in doc_types
        ])
        parts.append((Occur.Must, dt_clause))
    elif not include_treaties:
        # No doc_type filter — exclude treaties by default
        for excluded in DEFAULT_EXCLUDED_TYPES:
            parts.append((
                Occur.MustNot,
                Query.term_query(schema, "doc_type", excluded),
            ))

    if len(parts) == 1:
        return base_query
    return Query.boolean_query(parts)


def _count_facets(
    index: tantivy.Index,
    base_query: Query,
    searcher: tantivy.Searcher,
    *,
    doc_type: list[str] | None = None,
    include_treaties: bool = False,
) -> dict[str, int]:
    """Count matching documents per level using intersected queries.

    Respects active doc_type filters and treaty exclusion.
    """
    schema = index.schema
    facets: dict[str, int] = {}
    for lvl in ("ch", "vs", "mun"):
        filtered = _apply_filters(
            schema, base_query,
            level=lvl,
            doc_type=doc_type,
            include_treaties=include_treaties,
        )
        result = searcher.search(filtered, 1, count=True)
        facets[lvl] = result.count
    return facets


def _count_doc_type_facets(
    index: tantivy.Index,
    base_query: Query,
    searcher: tantivy.Searcher,
    *,
    level: list[str] | None = None,
) -> dict[str, int]:
    """Count matching documents per doc_type.

    Always includes all types (including treaty) for facet display.
    Respects active level filters.
    """
    schema = index.schema
    facets: dict[str, int] = {}
    for dt in DOC_TYPES:
        filtered = _apply_filters(
            schema, base_query,
            level=level,
            doc_type=dt,
            include_treaties=True,
        )
        result = searcher.search(filtered, 1, count=True)
        count = result.count
        if count > 0:
            facets[dt] = count
    return facets


# ---------------------------------------------------------------------------
# Score boosting
# ---------------------------------------------------------------------------

# Over-fetch factor for post-retrieval re-ranking.  We fetch more results
# from Tantivy than requested so that boosted docs from deeper ranks can
# float up into the final result window.
_OVERSAMPLE = 3
_MAX_RERANK = 200


def _boost_score(bm25: float, doc_type: str, level: str) -> float:
    """Apply logarithmic boost to a raw BM25 score.

    Formula: bm25 * (1 + (combined_boost - 1) / ln(e + bm25))

    When BM25 is high (clear relevance signal) the boost barely matters,
    preserving Tantivy's ranking.  When BM25 scores are close the boost
    breaks ties toward preferred doc_type / level combinations.
    """
    type_boost = _DOC_TYPE_BOOST.get(doc_type, 1.0)
    level_boost = _LEVEL_BOOST.get(level, 1.0)
    combined = type_boost * level_boost
    if combined == 1.0:
        return bm25
    return bm25 * (1.0 + (combined - 1.0) / math.log(math.e + bm25))


# ---------------------------------------------------------------------------
# Main search function
# ---------------------------------------------------------------------------

def search(
    index: tantivy.Index,
    query_str: str,
    *,
    lang: str = "de",
    level: list[str] | None = None,
    doc_type: list[str] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """Execute a search query against the law index.

    lang: language of the index being searched.
    level/doc_type accept a list for multi-select filtering.

    Returns dict with:
        hits: list of result dicts (rank, score, title, eli_path, etc.)
        facets: {ch: N, vs: N, mun: N} — counts per level
        doc_type_facets: {gesetz: N, verordnung: N, ...} — counts per type
        query_ms: search latency in milliseconds
        total: total matching documents (for the active filters)
    """
    schema = index.schema
    index.reload()
    searcher = index.searcher()

    match_base = _build_match_query(index, query_str, lang=lang)
    highlight_pattern = _build_highlight_pattern(query_str, lang=lang)

    # When any doc_type is explicitly selected, include treaties too
    include_treaties = doc_type is not None
    search_query = _apply_filters(
        schema, match_base,
        level=level,
        doc_type=doc_type,
        include_treaties=include_treaties,
    )

    # Boost re-ranking only applies when the requested page fits inside
    # the re-rank window.  Deep pages fall back to Tantivy's native BM25
    # ordering — the boost only matters for the first few pages where
    # close scores benefit from type/level tie-breaking.
    use_rerank = offset + limit <= _MAX_RERANK

    if use_rerank:
        fetch_limit = min((offset + limit) * _OVERSAMPLE, _MAX_RERANK)
        t0 = time.monotonic()
        result = searcher.search(search_query, fetch_limit, count=True)
        query_ms = (time.monotonic() - t0) * 1000
    else:
        t0 = time.monotonic()
        result = searcher.search(
            search_query, limit, count=True, offset=offset,
        )
        query_ms = (time.monotonic() - t0) * 1000

    total = result.count

    # Facet counts: level facets respect active doc_type filter,
    # doc_type facets respect active level filter.
    facets = _count_facets(
        index, match_base, searcher,
        doc_type=doc_type,
        include_treaties=include_treaties,
    )

    doc_type_facets = _count_doc_type_facets(
        index, match_base, searcher,
        level=level,
    )

    # Both paths produce page_hits as (score, doc_address, level, doc_type)
    # so phase 2 doesn't re-read level/doc_type from the doc store.
    if use_rerank:
        # Phase 1: lightweight pass — extract only the fields needed for
        # boosting (level, doc_type) plus the doc_address for phase 2.
        # Snippet generation is deferred to avoid expensive text analysis
        # on results that will be discarded after re-ranking.
        candidates = []
        for bm25_score, doc_address in result.hits:
            doc = searcher.doc(doc_address)
            doc_level = doc.get_first("level") or ""
            doc_dt = doc.get_first("doc_type") or ""
            boosted = _boost_score(bm25_score, doc_dt, doc_level)
            candidates.append((boosted, doc_address, doc_level, doc_dt))

        # Re-sort by boosted score and select the requested page
        candidates.sort(key=lambda c: c[0], reverse=True)
        page_hits = candidates[offset:offset + limit]
    else:
        # Deep page: use BM25 scores directly, no re-ranking
        page_hits = []
        for bm25_score, doc_address in result.hits:
            doc = searcher.doc(doc_address)
            doc_level = doc.get_first("level") or ""
            doc_dt = doc.get_first("doc_type") or ""
            page_hits.append((bm25_score, doc_address, doc_level, doc_dt))

    # Phase 2: full extraction + snippet generation for visible results only.
    # Custom highlighting instead of SnippetGenerator — the German stemmer's
    # internal ä→a folding causes false highlights (e.g. "bäu" highlighting
    # "Bauzone").  Substring matching on original text is umlaut-correct.
    hits = []
    for i, (score, doc_address, hit_level, hit_dt) in enumerate(page_hits):
        doc = searcher.doc(doc_address)
        title = doc.get_first("title") or ""
        body = doc.get_first("body") or ""

        if highlight_pattern is not None:
            snippet_title = _highlight_text(title, highlight_pattern)
            snippet_body = _highlight_text(body, highlight_pattern, max_chars=500)
        else:
            snippet_title = html_escape(title)
            snippet_body = html_escape(body[:500])

        hits.append({
            "rank": offset + i + 1,
            "score": round(score, 3),
            "sr_number": doc.get_first("sr_number") or "",
            "title": title,
            "abbreviation": doc.get_first("abbreviation") or "",
            "eli_path": doc.get_first("eli_path") or "",
            "level": hit_level,
            "doc_type": hit_dt,
            "snippet_title": snippet_title,
            "snippet_body": snippet_body,
        })

    return {
        "hits": hits,
        "facets": facets,
        "doc_type_facets": doc_type_facets,
        "query_ms": round(query_ms, 1),
        "total": total,
    }
