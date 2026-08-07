"""Tests for multilingual search analyzers and query building."""

from __future__ import annotations

import re

import pytest
import tantivy

from lex_akn.search import (
    SUPPORTED_INDEX_LANGS,
    _accent_insensitive_pattern,
    _build_highlight_pattern,
    build_analyzers,
    build_schema,
    fold_query,
    fold_text,
    german_ae_fold,
)


# ---------------------------------------------------------------------------
# fold_query / fold_text
# ---------------------------------------------------------------------------

def test_fold_query_german_applies_ae_fold() -> None:
    assert fold_query("Bäume", "de") == "Baeume"
    assert fold_query("Übung", "de") == "Uebung"


def test_fold_query_french_identity() -> None:
    assert fold_query("procédure", "fr") == "procédure"


def test_fold_query_italian_identity() -> None:
    assert fold_query("ordinanza", "it") == "ordinanza"


def test_fold_text_german() -> None:
    assert fold_text("Straßenverkehr", "de") == "Strassenverkehr"


def test_fold_text_french_identity() -> None:
    assert fold_text("procédure pénale", "fr") == "procédure pénale"


# ---------------------------------------------------------------------------
# Analyzer construction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang", SUPPORTED_INDEX_LANGS)
def test_build_analyzers_returns_five_entries(lang: str) -> None:
    # Four language text analyzers + the case-insensitive abbreviation analyzer.
    analyzers = build_analyzers(lang=lang)
    assert len(analyzers) == 5
    for name, analyzer in analyzers.items():
        assert isinstance(analyzer, tantivy.TextAnalyzer), f"{name} is not TextAnalyzer"


def test_german_analyzers_have_correct_names() -> None:
    analyzers = build_analyzers(lang="de")
    assert set(analyzers.keys()) == {
        "de_law", "de_law_ae", "de_prefix", "de_prefix_ae", "abbrev_lc",
    }


def test_french_analyzers_have_correct_names() -> None:
    analyzers = build_analyzers(lang="fr")
    assert set(analyzers.keys()) == {
        "fr_law", "fr_law_fold", "fr_prefix", "fr_prefix_fold", "abbrev_lc",
    }


def test_italian_analyzers_have_correct_names() -> None:
    analyzers = build_analyzers(lang="it")
    assert set(analyzers.keys()) == {
        "it_law", "it_law_fold", "it_prefix", "it_prefix_fold", "abbrev_lc",
    }


def test_german_analyzers_with_compound_words() -> None:
    words = ["wasser", "schutz", "gesetz"]
    analyzers = build_analyzers(words, lang="de")
    assert len(analyzers) == 5


# ---------------------------------------------------------------------------
# Schema construction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang", SUPPORTED_INDEX_LANGS)
def test_build_schema_returns_schema(lang: str) -> None:
    schema = build_schema(lang)
    assert isinstance(schema, tantivy.Schema)
    # Field existence verified by test_create_index_with_analyzers (Document
    # construction fails if fields are missing from the schema).


# ---------------------------------------------------------------------------
# Index creation + registration (integration-like)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang", SUPPORTED_INDEX_LANGS)
def test_create_index_with_analyzers(lang: str, tmp_path) -> None:
    """Verify we can create an index and register analyzers without error."""
    index_dir = tmp_path / lang
    index_dir.mkdir()

    compound_words = ["wasser", "schutz"] if lang == "de" else None
    schema = build_schema(lang)
    index = tantivy.Index(schema, path=str(index_dir))
    for name, analyzer in build_analyzers(compound_words, lang=lang).items():
        index.register_tokenizer(name, analyzer)

    # Verify we can write and read a document
    writer = index.writer(heap_size=15_000_000)
    doc = tantivy.Document(
        title=["Test Title"],
        title_ae=[fold_text("Test Title", lang)],
        body=["Test body text"],
        body_ae=[fold_text("Test body text", lang)],
        body_prefix=["Test body text"],
        body_prefix_ae=[fold_text("Test body text", lang)],
        abbreviation=["TT"],
        eli_path=["/eli/ch/test"],
        sr_number=["test"],
        level=["ch"],
        doc_type=["gesetz"],
    )
    doc.add_facet("classification", tantivy.Facet.from_string("/ch"))
    writer.add_document(doc)
    writer.commit()
    writer.wait_merging_threads()

    index.reload()
    searcher = index.searcher()
    result = searcher.search(tantivy.Query.all_query(), 10, count=True)
    assert result.count == 1


# ---------------------------------------------------------------------------
# Accent-insensitive highlighting
# ---------------------------------------------------------------------------

def test_accent_insensitive_pattern_basic() -> None:
    pattern = _accent_insensitive_pattern("procedure")
    # Should match "procédure" because 'e' expands to [eéèêë]
    assert "[eéèêë]" in pattern


def test_highlight_pattern_french_matches_accented() -> None:
    pat = _build_highlight_pattern("procedure", lang="fr")
    assert pat is not None
    # Should match "procédure" (accented)
    assert pat.search("La procédure est définie")
    # Should match "procedure" (unaccented)
    assert pat.search("La procedure est définie")


def test_highlight_pattern_german_preserves_umlauts() -> None:
    pat = _build_highlight_pattern("bäu", lang="de")
    assert pat is not None
    # Should match "bäuerlich" but NOT "Bauzone"
    assert pat.search("bäuerlich")
    assert not pat.search("Bauzone")


def test_highlight_pattern_italian() -> None:
    pat = _build_highlight_pattern("ordinanza", lang="it")
    assert pat is not None
    assert pat.search("L'ordinanza federale")


def test_highlight_pattern_empty_query() -> None:
    assert _build_highlight_pattern("", lang="de") is None
    assert _build_highlight_pattern("", lang="fr") is None


# ---------------------------------------------------------------------------
# Abbreviation (short-label) matching
# ---------------------------------------------------------------------------

def _build_abbrev_index(tmp_path):
    """Build a tiny German index with a few laws addressable by short label."""
    index_dir = tmp_path / "de"
    index_dir.mkdir()
    schema = build_schema("de")
    index = tantivy.Index(schema, path=str(index_dir))
    for name, analyzer in build_analyzers(lang="de").items():
        index.register_tokenizer(name, analyzer)

    writer = index.writer(heap_size=15_000_000, num_threads=1)
    # (title, abbreviation) — abbreviation does NOT appear in title/body, so a
    # hit can only come from the abbreviation field, not free-text matching.
    laws = [
        ("Stromversorgungsverordnung", "StromVV"),
        ("Gesetz über die Information der Öffentlichkeit", "GIDA"),
        ("Bundesgesetz betreffend die Ergänzung des ZGB", "OR"),
        ("Kantonales Gesetz über die Stromversorgung", "kStromVG"),
    ]
    for title, abbr in laws:
        doc = tantivy.Document(
            title=[title],
            title_ae=[fold_text(title, "de")],
            body=["Platzhalter Text ohne Kürzel."],
            body_ae=[fold_text("Platzhalter Text ohne Kürzel.", "de")],
            body_prefix=["Platzhalter Text ohne Kürzel."],
            body_prefix_ae=[fold_text("Platzhalter Text ohne Kürzel.", "de")],
            abbreviation=[abbr],
            eli_path=[f"/eli/test/{abbr}"],
            sr_number=[abbr],
            level=["ch"],
            doc_type=["gesetz"],
        )
        doc.add_facet("classification", tantivy.Facet.from_string("/ch"))
        writer.add_document(doc)
    writer.commit()
    writer.wait_merging_threads()
    index.reload()
    return index


@pytest.mark.parametrize("query", ["stromvv", "StromVV", "STROMVV"])
def test_abbreviation_match_is_case_insensitive(query, tmp_path) -> None:
    """A short label matches in any case, even when absent from title/body."""
    from lex_akn.search import search

    index = _build_abbrev_index(tmp_path)
    result = search(index, query, lang="de", limit=5)
    assert result["hits"], f"no hits for {query!r}"
    assert result["hits"][0]["abbreviation"] == "StromVV"


def test_abbreviation_match_mixed_case_label(tmp_path) -> None:
    """Mixed-case labels (kStromVG) match regardless of typed case."""
    from lex_akn.search import search

    index = _build_abbrev_index(tmp_path)
    for query in ("kstromvg", "kStromVG", "KSTROMVG"):
        top = search(index, query, lang="de", limit=5)["hits"][0]
        assert top["abbreviation"] == "kStromVG", query


def test_query_syntax_keyword_does_not_crash(tmp_path) -> None:
    """`OR` (Obligationenrecht) must not be parsed as a boolean operator."""
    from lex_akn.search import search

    index = _build_abbrev_index(tmp_path)
    # Would previously raise ValueError: Syntax Error: OR
    for query in ("OR", "or"):
        top = search(index, query, lang="de", limit=5)["hits"][0]
        assert top["abbreviation"] == "OR", query
