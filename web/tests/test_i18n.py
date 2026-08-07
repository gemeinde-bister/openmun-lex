"""Tests for the i18n module."""

from lex_web.i18n import SUPPORTED_LANGS, T, t


def test_t_returns_german_by_default() -> None:
    assert t("search_placeholder") == "Suche..."


def test_t_returns_french() -> None:
    assert t("search_placeholder", "fr") == "Recherche..."


def test_t_returns_italian() -> None:
    assert t("search_placeholder", "it") == "Ricerca..."


def test_t_falls_back_to_german_for_unknown_lang() -> None:
    result = t("search_placeholder", "rm")
    assert result == "Suche..."


def test_t_returns_key_for_unknown_key() -> None:
    result = t("nonexistent_key", "de")
    assert result == "nonexistent_key"


def test_all_keys_have_all_supported_langs() -> None:
    """Every key in T must have entries for all supported languages."""
    missing = []
    for key, translations in T.items():
        for lang in SUPPORTED_LANGS:
            if lang not in translations:
                missing.append(f"{key} missing {lang}")
    assert missing == [], f"Missing translations: {missing}"


def test_no_empty_translations() -> None:
    """No translation value should be an empty string."""
    empty = []
    for key, translations in T.items():
        for lang, value in translations.items():
            if not value.strip():
                empty.append(f"{key}[{lang}]")
    assert empty == [], f"Empty translations: {empty}"


def test_sr_categories_complete() -> None:
    """SR category keys 0-9 must all exist in T."""
    for digit in "0123456789":
        key = f"sr_cat_{digit}"
        assert key in T, f"Missing SR category key: {key}"
        assert "de" in T[key]
        assert "fr" in T[key]
        assert "it" in T[key]


def test_doc_type_labels_complete() -> None:
    """All 8 doc types must have i18n entries."""
    doc_types = [
        "verfassung", "gesetz", "verordnung", "reglement",
        "beschluss", "konkordat", "treaty", "other",
    ]
    for dt in doc_types:
        key = f"doctype_{dt}"
        assert key in T, f"Missing doc type key: {key}"


def test_law_template_keys_exist() -> None:
    """Keys used in law.html must exist."""
    for key in ("toc_heading", "date_prefix", "stand_prefix", "edit_mode"):
        assert key in T
        assert t(key, "de") != key
        assert t(key, "fr") != key
        assert t(key, "it") != key
