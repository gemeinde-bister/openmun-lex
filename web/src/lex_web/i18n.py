"""Centralized i18n translations for openmunlex.

Single source of truth for all translatable UI strings.
Python dict (not .po files) — ~80 strings across 3 languages.
"""

from __future__ import annotations

SUPPORTED_LANGS: tuple[str, ...] = ("de", "fr", "it")

# ---------------------------------------------------------------------------
# Translation table: key -> {lang -> string}
# Every key MUST have de + fr + it entries.
# ---------------------------------------------------------------------------

T: dict[str, dict[str, str]] = {
    # --- Lifecycle status ---
    "repealed_banner": {
        "de": "Dieser Erlass ist aufgehoben und nicht mehr in Kraft.",
        "fr": "Cet acte est abrogé et n'est plus en vigueur.",
        "it": "Questo atto è abrogato e non è più in vigore.",
    },
    "repealed_on": {
        "de": "Aufgehoben am",
        "fr": "Abrogé le",
        "it": "Abrogato il",
    },
    # --- Search UI ---
    "search_placeholder": {
        "de": "Suche...",
        "fr": "Recherche...",
        "it": "Ricerca...",
    },
    "no_results": {
        "de": "Keine Ergebnisse.",
        "fr": "Aucun resultat.",
        "it": "Nessun risultato.",
    },
    "search_error": {
        "de": "Fehler bei der Suche.",
        "fr": "Erreur lors de la recherche.",
        "it": "Errore durante la ricerca.",
    },
    "search_unavailable": {
        "de": "Suchindex nicht verfügbar.",
        "fr": "Index de recherche indisponible.",
        "it": "Indice di ricerca non disponibile.",
    },
    "search_invalid": {
        "de": "Ungültige Suchanfrage",
        "fr": "Requête de recherche invalide",
        "it": "Richiesta di ricerca non valida",
    },
    "hits": {
        "de": "Treffer",
        "fr": "résultats",
        "it": "risultati",
    },
    "page_of": {
        "de": "Seite {page} von {total}",
        "fr": "Page {page} sur {total}",
        "it": "Pagina {page} di {total}",
    },

    # --- Level filter labels ---
    "level_all": {
        "de": "Alle",
        "fr": "Tous",
        "it": "Tutti",
    },
    "level_ch": {
        "de": "Bund",
        "fr": "Confédération",
        "it": "Confederazione",
    },
    "level_vs": {
        "de": "Kanton",
        "fr": "Canton",
        "it": "Cantone",
    },
    "level_mun": {
        "de": "Gemeinde",
        "fr": "Commune",
        "it": "Comune",
    },

    # --- Doc type labels ---
    "doctype_verfassung": {
        "de": "Verfassung",
        "fr": "Constitution",
        "it": "Costituzione",
    },
    "doctype_gesetz": {
        "de": "Gesetz",
        "fr": "Loi",
        "it": "Legge",
    },
    "doctype_verordnung": {
        "de": "Verordnung",
        "fr": "Ordonnance",
        "it": "Ordinanza",
    },
    "doctype_reglement": {
        "de": "Reglement",
        "fr": "Règlement",
        "it": "Regolamento",
    },
    "doctype_beschluss": {
        "de": "Beschluss",
        "fr": "Arrêté",
        "it": "Decreto",
    },
    "doctype_konkordat": {
        "de": "Konkordat",
        "fr": "Concordat",
        "it": "Concordato",
    },
    "doctype_treaty": {
        "de": "Staatsvertrag",
        "fr": "Traité",
        "it": "Trattato",
    },
    "doctype_other": {
        "de": "Andere",
        "fr": "Autres",
        "it": "Altri",
    },

    # --- Browse page: index ---
    "index_intro": {
        "de": "Durchsuchbare Sammlung von Bundesrecht, kantonalem Recht und Gemeinderecht. Erlasse werden im Akoma-Ntoso-Format gespeichert und sind unter permanenten ELI-URIs zugänglich.",
        "fr": "Collection consultable de droit fédéral, cantonal et communal. Les actes sont stockés au format Akoma Ntoso et accessibles via des URI ELI permanentes.",
        "it": "Raccolta consultabile di diritto federale, cantonale e comunale. Gli atti sono archiviati in formato Akoma Ntoso e accessibili tramite URI ELI permanenti.",
    },
    "browse_federal": {
        "de": "Bundesrecht",
        "fr": "Droit fédéral",
        "it": "Diritto federale",
    },
    "browse_federal_source": {
        "de": "Quelle: <a href=\"https://www.fedlex.admin.ch\" rel=\"noopener\">Fedlex</a> &middot; Systematische Rechtssammlung (SR)",
        "fr": "Source : <a href=\"https://www.fedlex.admin.ch\" rel=\"noopener\">Fedlex</a> &middot; Recueil systématique (RS)",
        "it": "Fonte: <a href=\"https://www.fedlex.admin.ch\" rel=\"noopener\">Fedlex</a> &middot; Raccolta sistematica (RS)",
    },
    "browse_cantonal": {
        "de": "Kanton Wallis",
        "fr": "Canton du Valais",
        "it": "Canton Vallese",
    },
    "browse_cantonal_source": {
        "de": "Quelle: <a href=\"https://lex.vs.ch\" rel=\"noopener\">lex.vs.ch</a> &middot; Systematische Gesetzessammlung (SGS)",
        "fr": "Source : <a href=\"https://lex.vs.ch\" rel=\"noopener\">lex.vs.ch</a> &middot; Recueil systématique des lois (RSL)",
        "it": "Fonte: <a href=\"https://lex.vs.ch\" rel=\"noopener\">lex.vs.ch</a> &middot; Raccolta sistematica delle leggi (RSL)",
    },
    "browse_municipal": {
        "de": "Gemeinden",
        "fr": "Communes",
        "it": "Comuni",
    },
    "browse_municipal_source": {
        "de": "Eigene Erfassung im AKN-Format",
        "fr": "Saisie propre au format AKN",
        "it": "Inserimento proprio in formato AKN",
    },
    "browse_documents": {
        "de": "Referenzdokumente",
        "fr": "Documents de référence",
        "it": "Documenti di riferimento",
    },
    "browse_documents_source": {
        "de": "Nicht-legislative Referenztexte zu Terminologie, Verfahren und Organisation",
        "fr": "Textes de référence non législatifs sur la terminologie, les procédures et l'organisation",
        "it": "Testi di riferimento non legislativi su terminologia, procedure e organizzazione",
    },
    "enactments": {
        "de": "Erlasse",
        "fr": "actes législatifs",
        "it": "atti normativi",
    },

    # --- Browse CH page ---
    "sr_title": {
        "de": "Systematische Rechtssammlung (SR)",
        "fr": "Recueil systématique (RS)",
        "it": "Raccolta sistematica (RS)",
    },
    "sr_in_force": {
        "de": "{count} Erlasse in Kraft",
        "fr": "{count} actes en vigueur",
        "it": "{count} atti in vigore",
    },
    "col_title": {
        "de": "Titel",
        "fr": "Titre",
        "it": "Titolo",
    },

    # --- Browse VS page ---
    "vs_collection": {
        "de": "Systematische Sammlung",
        "fr": "Recueil systématique",
        "it": "Raccolta sistematica",
    },
    "vs_count": {
        "de": "{count} Erlasse",
        "fr": "{count} actes législatifs",
        "it": "{count} atti normativi",
    },

    # --- Law template (existing _I18N from app.py) ---
    "toc_heading": {
        "de": "Inhaltsverzeichnis",
        "fr": "Table des matières",
        "it": "Indice",
    },
    "date_prefix": {
        "de": "vom",
        "fr": "du",
        "it": "del",
    },
    "stand_prefix": {
        "de": "Stand am",
        "fr": "état au",
        "it": "stato al",
    },
    "edit_mode": {
        "de": "Bearbeitungsmodus",
        "fr": "Mode édition",
        "it": "Modalità modifica",
    },

    # --- Cite toolbar ---
    "cite_text": {
        "de": "Text mit Zitat kopieren",
        "fr": "Copier le texte avec citation",
        "it": "Copia testo con citazione",
    },
    "cite_citation": {
        "de": "Nur Zitat kopieren",
        "fr": "Copier uniquement la citation",
        "it": "Copia solo la citazione",
    },
    "cite_url": {
        "de": "URL kopieren",
        "fr": "Copier l'URL",
        "it": "Copia URL",
    },
    "cite_akn": {
        "de": "AKN-Referenz kopieren",
        "fr": "Copier la référence AKN",
        "it": "Copia riferimento AKN",
    },
    "cite_bibtex": {
        "de": "BibTeX kopieren",
        "fr": "Copier BibTeX",
        "it": "Copia BibTeX",
    },

    # --- SR categories (from fedlex.py) ---
    "sr_cat_1": {
        "de": "Staat - Volk - Behörden",
        "fr": "État - Peuple - Autorités",
        "it": "Stato - Popolo - Autorità",
    },
    "sr_cat_2": {
        "de": "Privatrecht - Zivilrechtspflege - Vollstreckung",
        "fr": "Droit privé - Procédure civile - Exécution",
        "it": "Diritto privato - Procedura civile - Esecuzione",
    },
    "sr_cat_3": {
        "de": "Strafrecht - Strafrechtspflege - Strafvollzug",
        "fr": "Droit pénal - Procédure pénale - Exécution",
        "it": "Diritto penale - Procedura penale - Esecuzione",
    },
    "sr_cat_4": {
        "de": "Schule - Wissenschaft - Kultur",
        "fr": "École - Science - Culture",
        "it": "Scuola - Scienza - Cultura",
    },
    "sr_cat_5": {
        "de": "Landesverteidigung",
        "fr": "Défense nationale",
        "it": "Difesa nazionale",
    },
    "sr_cat_6": {
        "de": "Finanzen",
        "fr": "Finances",
        "it": "Finanze",
    },
    "sr_cat_7": {
        "de": "Öffentliche Werke - Energie - Verkehr",
        "fr": "Travaux publics - Énergie - Transports",
        "it": "Opere pubbliche - Energia - Trasporti",
    },
    "sr_cat_8": {
        "de": "Gesundheit - Arbeit - Soziale Sicherheit",
        "fr": "Santé - Travail - Sécurité sociale",
        "it": "Sanità - Lavoro - Sicurezza sociale",
    },
    "sr_cat_9": {
        "de": "Wirtschaft - Technische Zusammenarbeit",
        "fr": "Économie - Coopération technique",
        "it": "Economia - Cooperazione tecnica",
    },
    "sr_cat_0": {
        "de": "Internationale Verträge",
        "fr": "Traités internationaux",
        "it": "Trattati internazionali",
    },

    # --- VS law type labels ---
    "lawtype_Verfassung": {
        "de": "Verfassung",
        "fr": "Constitution",
        "it": "Costituzione",
    },
    "lawtype_Gesetz": {
        "de": "Gesetz",
        "fr": "Loi",
        "it": "Legge",
    },
    "lawtype_Dekret": {
        "de": "Dekret",
        "fr": "Décret",
        "it": "Decreto",
    },
    "lawtype_Verordnung": {
        "de": "Verordnung",
        "fr": "Ordonnance",
        "it": "Ordinanza",
    },
    "lawtype_Reglement": {
        "de": "Reglement",
        "fr": "Règlement",
        "it": "Regolamento",
    },
    "lawtype_Beschluss": {
        "de": "Beschluss",
        "fr": "Arrêté",
        "it": "Decreto",
    },
    "lawtype_Beschluss GR": {
        "de": "Beschluss GR",
        "fr": "Arrêté GC",
        "it": "Decreto GP",
    },
    "lawtype_Entscheid StR": {
        "de": "Entscheid StR",
        "fr": "Décision CE",
        "it": "Decisione CS",
    },
    "lawtype_Interkantonale Vereinbarung": {
        "de": "Interkantonale Vereinbarung",
        "fr": "Convention intercantonale",
        "it": "Convenzione intercantonale",
    },
    "lawtype_Staatsvertrag": {
        "de": "Staatsvertrag",
        "fr": "Traité",
        "it": "Trattato",
    },
    "lawtype_Andere": {
        "de": "Andere",
        "fr": "Autres",
        "it": "Altri",
    },
}

# Mapping from SR category digit to i18n key
SR_CATEGORY_KEYS: dict[str, str] = {
    "1": "sr_cat_1",
    "2": "sr_cat_2",
    "3": "sr_cat_3",
    "4": "sr_cat_4",
    "5": "sr_cat_5",
    "6": "sr_cat_6",
    "7": "sr_cat_7",
    "8": "sr_cat_8",
    "9": "sr_cat_9",
    "0": "sr_cat_0",
}

# Mapping from doc_type key to i18n key
DOC_TYPE_KEYS: dict[str, str] = {
    "verfassung": "doctype_verfassung",
    "gesetz": "doctype_gesetz",
    "verordnung": "doctype_verordnung",
    "reglement": "doctype_reglement",
    "beschluss": "doctype_beschluss",
    "konkordat": "doctype_konkordat",
    "treaty": "doctype_treaty",
    "other": "doctype_other",
}

# Mapping from level key to i18n key
LEVEL_KEYS: dict[str, str] = {
    "": "level_all",
    "ch": "level_ch",
    "vs": "level_vs",
    "mun": "level_mun",
}


def t(key: str, lang: str = "de") -> str:
    """Look up a translated string.

    Falls back to German if the requested language is missing.
    Returns the key itself if the key is unknown (avoids silent failures).
    """
    entry = T.get(key)
    if entry is None:
        return key
    return entry.get(lang, entry.get("de", key))
