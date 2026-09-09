"""ELI URI routing and law display."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from lex_akn.fedlex import (
    FedlexEntry,
    _sr_sort_key,
    sr_category,
)
from lex_akn.metadata import extract_metadata
from lex_akn.navigate import build_toc, find_by_eid, flatten_toc
from lex_akn.parse import parse_file
from lex_akn.uri import PubUri, parse_doc, parse_eli, parse_pub
from lex_web.aliases import normalize_doctype, normalize_entity, normalize_organ
from lex_web.context import _resolve_cookie_lang, _root_path, base_context
from lex_web.i18n import SR_CATEGORY_KEYS, SUPPORTED_LANGS, t
from lex_web.render import render_body, render_element

log = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get(
    "LEX_DATA_DIR",
    Path(__file__).parent.parent.parent.parent / "data",
))

# Edit mode: disabled by default.  Set LEX_EDIT_ENABLED=1 for local dev.
# Will be replaced by openmun-auth JWT check later.
EDIT_ENABLED = os.environ.get("LEX_EDIT_ENABLED", "") == "1"

# FRBR routing: recognized language and format path segments.
# Only languages that have actual content — no Romansh (rm) content exists.
VALID_LANGS = frozenset(SUPPORTED_LANGS)
VALID_FORMATS = frozenset({"html", "xml", "pdf"})

# Cache parsed documents (in-memory, per-process).
# Values: (mtime_ns, parsed_tree) — re-parsed when file changes on disk.
_doc_cache: dict[str, tuple[int, object]] = {}

# Search indexes per language (lazy-loaded, None if unavailable)
_search_indexes: dict[str, object | None] = {}
_search_indexes_loaded: set[str] = set()

# Cache for VS index per language (loaded once each)
_vs_index_cache: dict[str, list[dict]] = {}

# Cache for federal index (loaded once from data/ch/index.json)
_ch_index_cache: list[FedlexEntry] | None = None

# Law type ordering for VS browse page grouping
_LAW_TYPE_ORDER = [
    "Verfassung",
    "Gesetz",
    "Dekret",
    "Verordnung",
    "Reglement",
    "Beschluss",
    "Beschluss GR",
    "Entscheid StR",
    "Interkantonale Vereinbarung",
    "Staatsvertrag",
]


def _safe_data_path(*parts: str) -> Path | None:
    """Resolve a path under DATA_DIR and verify it doesn't escape."""
    candidate = (DATA_DIR / Path(*parts)).resolve()
    data_root = DATA_DIR.resolve()
    try:
        candidate.relative_to(data_root)
    except ValueError:
        return None
    return candidate


def _cached_parse(cache_key: str, xml_path: Path | None):
    """Parse and cache an AKN file, re-parsing when mtime changes."""
    if xml_path is None or not xml_path.exists():
        _doc_cache.pop(cache_key, None)
        return None
    mtime_ns = xml_path.stat().st_mtime_ns
    cached = _doc_cache.get(cache_key)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]
    tree = parse_file(xml_path)
    _doc_cache[cache_key] = (mtime_ns, tree)
    return tree


def _get_tree_ch(sr_number: str, lang: str = "de"):
    """Load and cache a federal AKN document tree from local sync store."""
    return _cached_parse(
        f"ch:{sr_number}:{lang}",
        _safe_data_path("ch", sr_number, f"{lang}.xml"),
    )


def _get_tree_vs(sysno: str, lang: str = "de"):
    """Load and cache a parsed cantonal AKN document tree."""
    return _cached_parse(
        f"vs:{sysno}:{lang}",
        _safe_data_path("vs", sysno, f"{lang}.xml"),
    )


def _get_tree_mun(bfs: str, entity: str, reg_id: str, lang: str = "de"):
    """Load and cache a parsed municipal AKN document tree."""
    return _cached_parse(
        f"mun:{bfs}:{entity}:{reg_id}:{lang}",
        _safe_data_path("mun", bfs, entity, reg_id, f"{lang}.xml"),
    )


def _get_tree_doc(doc_id: str, lang: str = "de"):
    """Load and cache a parsed non-legislative AKN document (<doc>) tree."""
    return _cached_parse(
        f"doc:{doc_id}:{lang}",
        _safe_data_path("doc", doc_id, f"{lang}.xml"),
    )


# ---------------------------------------------------------------------------
# FRBR routing helpers
# ---------------------------------------------------------------------------

def _not_found(msg: str = "Not found") -> HTMLResponse:
    """Return a 404 HTML response."""
    return HTMLResponse(content=f"<h1>404 – {msg}</h1>", status_code=404)


def _resolve_lang(request: Request, explicit_lang: str | None) -> str:
    """Determine the response language.

    Priority: explicit path segment > lang cookie > default "de".
    """
    if explicit_lang is not None:
        return explicit_lang
    cookie_lang = request.cookies.get("lang")
    if cookie_lang in VALID_LANGS:
        return cookie_lang
    return "de"


def _resolve_xml_path(*dir_parts: str, date: str | None, lang: str) -> Path | None:
    """Build the path to an XML file under DATA_DIR.

    With date: data/{dir_parts}/{date}/{lang}.xml  (historical version)
    Without:   data/{dir_parts}/{lang}.xml          (latest version)
    """
    if date is not None:
        return _safe_data_path(*dir_parts, date, f"{lang}.xml")
    return _safe_data_path(*dir_parts, f"{lang}.xml")


def _available_langs_from_path(xml_path: Path) -> list[str]:
    """Detect which language variants exist as sibling XML files."""
    parent = xml_path.parent
    if not parent.is_dir():
        return []
    return sorted(
        p.stem for p in parent.iterdir()
        if p.suffix == ".xml" and p.stem in VALID_LANGS
    )


def _law_status(xml_path: Path) -> tuple[str, str | None]:
    """Read the work's lifecycle status from its meta.json.

    The status is work-level (in_force / repealed), so it lives in the law's
    root meta.json — alongside the latest {lang}.xml, or one level up when
    serving a dated historical version ({sr}/{date}/{lang}.xml).  Repealed laws
    stay resolvable (URI-MODEL.md Principle 6); the viewer flags them.

    Returns (status, repealed_date); defaults to ("in_force", None) when no
    status is recorded (pre-lifecycle meta.json).
    """
    meta_path = xml_path.parent / "meta.json"
    if not meta_path.exists():
        meta_path = xml_path.parent.parent / "meta.json"
    if not meta_path.exists():
        return ("in_force", None)
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ("in_force", None)
    return (data.get("status", "in_force"), data.get("repealed_date"))


def _serve_document(
    request: Request,
    tree: object,
    xml_path: Path,
    eli_path: str,
    level: str,
    lang: str,
    fmt: str | None,
) -> Response:
    """Dispatch to the appropriate format handler.

    fmt=None or "html" → render HTML template
    "xml"              → serve raw AKN XML file
    "pdf"              → 501 Not Implemented
    """
    if fmt == "xml":
        return FileResponse(str(xml_path), media_type="application/xml")
    if fmt == "pdf":
        return Response(content="PDF not yet available", status_code=501)

    # HTML rendering (fmt=None or fmt="html")
    metadata = extract_metadata(tree)
    toc = build_toc(tree)
    flat_toc = flatten_toc(toc)
    body_html = render_body(tree)

    editable = EDIT_ENABLED and request.query_params.get("edit", "") == "1"
    available_langs = _available_langs_from_path(xml_path)
    status, repealed_date = _law_status(xml_path)

    ctx = base_context(request, lang=lang)
    ctx.update({
        "metadata": metadata,
        "toc": flat_toc,
        "body_html": body_html,
        "editable": editable,
        "eli_path": eli_path,
        "level": level,
        "available_langs": available_langs,
        "status": status,
        "repealed_date": repealed_date,
    })

    templates = request.app.state.templates
    return templates.TemplateResponse(request, "law.html", ctx)


def _load_ch_index() -> list[FedlexEntry]:
    """Load federal SR index from local sync store.

    Tries ``sync_index.json`` first (new trilingual format), then falls
    back to ``index.json`` (old single-language format).  Handles both
    multilingual (``titles`` dict) and legacy (``title`` string) entries.
    """
    global _ch_index_cache
    if _ch_index_cache is not None:
        return _ch_index_cache

    index_path = DATA_DIR / "ch" / "sync_index.json"
    if not index_path.exists():
        index_path = DATA_DIR / "ch" / "index.json"
    if not index_path.exists():
        log.warning("No federal index at %s — run fedlex-sync first", index_path)
        _ch_index_cache = []
        return _ch_index_cache

    data = json.loads(index_path.read_text(encoding="utf-8"))
    entries = []
    for sr, info in data.items():
        # New format: titles/abbreviations are dicts
        if "titles" in info:
            titles = info["titles"]
            abbreviations = info.get("abbreviations", {})
        else:
            # Old format: single title/abbreviation strings
            titles = {"de": info.get("title", "")}
            abbr = info.get("abbreviation", "")
            abbreviations = {"de": abbr} if abbr else {}
        entries.append(FedlexEntry(
            sr=sr,
            law_uri=info["law_uri"],
            titles=titles,
            abbreviations=abbreviations,
        ))
    entries.sort(key=_sr_sort_key)
    log.info("Loaded %d federal laws from sync index", len(entries))

    _ch_index_cache = entries
    return entries


def _load_vs_index(lang: str = "de") -> list[dict]:
    """Load all VS meta.json files with titles in the given language.

    Caches per language. Falls back to German title if target language
    XML is not available.
    """
    if lang in _vs_index_cache:
        return _vs_index_cache[lang]

    vs_dir = DATA_DIR / "vs"
    entries = []
    if vs_dir.is_dir():
        for meta_path in sorted(vs_dir.glob("*/meta.json")):
            sysno = meta_path.parent.name
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            # Extract title in target language, fall back to de
            tree = _get_tree_vs(sysno, lang)
            if tree is None and lang != "de":
                tree = _get_tree_vs(sysno, "de")
            title = ""
            abbreviation = ""
            if tree is not None:
                try:
                    md = extract_metadata(tree)
                    title = md.title
                    abbreviation = md.abbreviation
                except ValueError:
                    pass
            entries.append({
                "sysno": sysno,
                "title": title or f"({sysno})",
                "abbreviation": abbreviation,
                "law_type": meta.get("law_type", ""),
                "abrogated": meta.get("abrogated", False),
            })

    # Sort by numeric sysno
    def _sort_key(e: dict) -> tuple:
        parts = e["sysno"].split(".")
        nums = []
        for p in parts:
            try:
                nums.append(int(p))
            except ValueError:
                nums.append(0)
        return tuple(nums)

    entries.sort(key=_sort_key)
    _vs_index_cache[lang] = entries
    return entries


async def federal_law(request: Request) -> Response:
    """Display a federal law: /eli/ch/{sr}[/{date}][/{lang}][/{format}]"""
    captured = request.path_params["sr_number"]
    try:
        eli = parse_eli(f"/eli/ch/{captured}")
    except ValueError:
        return _not_found()

    if eli.lang is not None and eli.lang not in VALID_LANGS:
        return _not_found()
    if eli.format is not None and eli.format not in VALID_FORMATS:
        return _not_found()

    lang = _resolve_lang(request, eli.lang)
    xml_path = _resolve_xml_path("ch", eli.identifier, date=eli.date, lang=lang)
    tree = _cached_parse(f"ch:{eli.identifier}:{eli.date}:{lang}", xml_path)

    # Fallback: implicit lang from cookie not found → try default "de"
    if tree is None and eli.lang is None and lang != "de":
        lang = "de"
        xml_path = _resolve_xml_path("ch", eli.identifier, date=eli.date, lang=lang)
        tree = _cached_parse(f"ch:{eli.identifier}:{eli.date}:{lang}", xml_path)

    if tree is None:
        return _not_found()

    assert xml_path is not None  # tree ≠ None ⟹ path valid and file exists
    return _serve_document(
        request, tree, xml_path,
        eli_path=f"/eli/ch/{eli.identifier}",
        level="ch", lang=lang, fmt=eli.format,
    )


async def federal_index(request: Request) -> Response:
    """Browse page for federal laws: /eli/ch/"""
    ctx = base_context(request)
    lang = ctx["lang"]
    entries = _load_ch_index()

    # Group by SR main category (translated label)
    # Category order: 1-9 then 0 (treaties)
    _CAT_ORDER = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
    groups_by_cat: dict[str, list[FedlexEntry]] = {}
    for entry in entries:
        cat = sr_category(entry.sr)
        groups_by_cat.setdefault(cat, [])
        groups_by_cat[cat].append(entry)

    # Build sorted groups with translated category labels
    sorted_groups = []
    for cat in _CAT_ORDER:
        if cat in groups_by_cat:
            i18n_key = SR_CATEGORY_KEYS.get(cat, "")
            label = t(i18n_key, lang) if i18n_key else "Andere"
            sorted_groups.append((label, groups_by_cat[cat]))

    ctx.update({
        "groups": sorted_groups,
        "total_count": len(entries),
    })

    templates = request.app.state.templates
    return templates.TemplateResponse(request, "browse_ch.html", ctx)


async def cantonal_law(request: Request) -> Response:
    """Display a cantonal law: /eli/vs/{sysno}[/{date}][/{lang}][/{format}]"""
    captured = request.path_params["sysno"]
    try:
        eli = parse_eli(f"/eli/vs/{captured}")
    except ValueError:
        return _not_found()

    if eli.lang is not None and eli.lang not in VALID_LANGS:
        return _not_found()
    if eli.format is not None and eli.format not in VALID_FORMATS:
        return _not_found()

    lang = _resolve_lang(request, eli.lang)
    xml_path = _resolve_xml_path("vs", eli.identifier, date=eli.date, lang=lang)
    tree = _cached_parse(f"vs:{eli.identifier}:{eli.date}:{lang}", xml_path)

    # Fallback: implicit lang from cookie not found → try default "de"
    if tree is None and eli.lang is None and lang != "de":
        lang = "de"
        xml_path = _resolve_xml_path("vs", eli.identifier, date=eli.date, lang=lang)
        tree = _cached_parse(f"vs:{eli.identifier}:{eli.date}:{lang}", xml_path)

    if tree is None:
        return _not_found()

    assert xml_path is not None
    return _serve_document(
        request, tree, xml_path,
        eli_path=f"/eli/vs/{eli.identifier}",
        level="vs", lang=lang, fmt=eli.format,
    )


async def cantonal_index(request: Request) -> Response:
    """Browse page for cantonal laws: /eli/vs/"""
    ctx = base_context(request)
    lang = ctx["lang"]
    entries = _load_vs_index(lang)

    # Group by law_type (use German key for grouping, translate for display)
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        lt = entry["law_type"] or "Andere"
        groups.setdefault(lt, [])
        groups[lt].append(entry)

    # Sort groups by defined order, unknown types at end
    type_order = {tp: i for i, tp in enumerate(_LAW_TYPE_ORDER)}
    sorted_groups = sorted(
        groups.items(),
        key=lambda kv: (type_order.get(kv[0], len(_LAW_TYPE_ORDER)), kv[0]),
    )

    ctx.update({
        "groups": sorted_groups,
        "total_count": len(entries),
    })

    templates = request.app.state.templates
    return templates.TemplateResponse(request, "browse_vs.html", ctx)


def _resolve_municipality_name(bfs: str) -> str:
    """Resolve BFS number to municipality name via openmun-opendata."""
    try:
        from openmun_opendata import MunicipalitiesAPI
        api = MunicipalitiesAPI(fallback_allowed=True)
        mun = api.get_by_bfs_code(bfs)
        if mun is not None:
            return mun.name
    except Exception:
        pass
    return bfs


# Cache for municipality name lookups
_bfs_name_cache: dict[str, str] = {}

# Cache for municipal index
_mun_index_cache: list[dict] | None = None


def _load_mun_index() -> list[dict]:
    """Load all municipal meta.json files, resolve BFS names, cache and return."""
    global _mun_index_cache
    if _mun_index_cache is not None:
        return _mun_index_cache

    mun_dir = DATA_DIR / "mun"
    entries = []
    if not mun_dir.is_dir():
        _mun_index_cache = entries
        return entries

    for meta_path in sorted(mun_dir.glob("**/meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        # Only a law's meta.json carries a systematic number; annex metas
        # (…/anhang/N/meta.json) describe a component, not a work.
        reg_id = meta.get("systematic_number", "")
        if not reg_id:
            continue

        # Drafts are reachable via their /draft URI only and never appear in
        # the listing (docs/URI-MODEL.md § Drafts).
        if meta.get("status") == "draft":
            continue

        bfs = meta.get("municipality_bfs", "")
        entity = meta.get("entity", "")

        # Resolve BFS to name (cached)
        if bfs not in _bfs_name_cache:
            _bfs_name_cache[bfs] = _resolve_municipality_name(bfs)
        mun_name = _bfs_name_cache[bfs]

        # Extract title from AKN if available
        title = ""
        abbreviation = ""
        tree = _get_tree_mun(bfs, entity, reg_id)
        if tree is not None:
            try:
                md = extract_metadata(tree)
                title = md.title
                abbreviation = md.abbreviation
            except ValueError:
                pass

        entries.append({
            "bfs": bfs,
            "entity": entity,
            "reg_id": reg_id,
            "municipality_name": mun_name,
            "title": title or f"({reg_id})",
            "abbreviation": abbreviation,
            "law_type": meta.get("law_type", ""),
            "abrogated": meta.get("abrogated", False),
        })

    _mun_index_cache = entries
    return entries


async def municipal_law(request: Request) -> Response:
    """Display a municipal law: /eli/mun/{bfs}/{entity}/{id}[/{date}][/{lang}][/{format}]"""
    bfs = request.path_params["bfs"]
    rest = request.path_params["rest"]
    try:
        eli = parse_eli(f"/eli/mun/{bfs}/{rest}")
    except ValueError:
        return _not_found()

    if eli.lang is not None and eli.lang not in VALID_LANGS:
        return _not_found()
    if eli.format is not None and eli.format not in VALID_FORMATS:
        return _not_found()

    # identifier is "bfs/entity/id" — split into path components
    id_parts = eli.identifier.split("/")
    if len(id_parts) != 3:
        return _not_found()
    id_bfs, entity, reg_id = id_parts

    # Alias normalization: non-canonical entity → 301 redirect
    canonical_entity = normalize_entity(entity)
    if canonical_entity is not None:
        canonical_id = f"{id_bfs}/{canonical_entity}/{reg_id}"
        canonical = f"/eli/mun/{canonical_id}"
        if eli.date is not None:
            canonical += f"/{eli.date}"
        if eli.lang is not None:
            canonical += f"/{eli.lang}"
        if eli.format is not None:
            canonical += f"/{eli.format}"
        return RedirectResponse(
            url=_root_path(request) + canonical,
            status_code=301,
        )

    lang = _resolve_lang(request, eli.lang)
    xml_path = _resolve_xml_path(
        "mun", id_bfs, entity, reg_id, date=eli.date, lang=lang,
    )
    tree = _cached_parse(f"mun:{eli.identifier}:{eli.date}:{lang}", xml_path)

    # Fallback: implicit lang from cookie not found → try default "de"
    if tree is None and eli.lang is None and lang != "de":
        lang = "de"
        xml_path = _resolve_xml_path(
            "mun", id_bfs, entity, reg_id, date=eli.date, lang=lang,
        )
        tree = _cached_parse(f"mun:{eli.identifier}:{eli.date}:{lang}", xml_path)

    if tree is None:
        return _not_found()

    assert xml_path is not None
    return _serve_document(
        request, tree, xml_path,
        eli_path=f"/eli/mun/{eli.identifier}",
        level="mun", lang=lang, fmt=eli.format,
    )


# Entity code → display name
_ENTITY_LABELS = {
    "eg": "Munizipalgemeinde",
    "bg": "Burgergemeinde",
    "gt": "Geteilschaft(en)",
}

# Entity display order
_ENTITY_ORDER = ["eg", "bg", "gt"]


def _load_doc_index() -> list[dict]:
    """Load platform-level /doc/ entries (title from AKN metadata)."""
    doc_dir = DATA_DIR / "doc"
    entries = []
    if not doc_dir.is_dir():
        return entries

    for xml_path in sorted(doc_dir.glob("*/de.xml")):
        doc_id = xml_path.parent.name
        tree = _get_tree_doc(doc_id)
        if tree is None:
            continue
        try:
            md = extract_metadata(tree)
            title = md.title
        except ValueError:
            title = doc_id
        entries.append({"doc_id": doc_id, "title": title})
    return entries


async def index(request: Request) -> Response:
    """Landing page."""
    mun_entries = _load_mun_index()

    # Group: municipality name → entity → laws
    mun_tree: dict[str, dict[str, list[dict]]] = {}
    for entry in mun_entries:
        name = entry["municipality_name"]
        entity = entry["entity"]
        mun_tree.setdefault(name, {})
        mun_tree[name].setdefault(entity, [])
        mun_tree[name][entity].append(entry)

    # Sort municipalities, then sort entities within each by defined order
    mun_groups = []
    for name in sorted(mun_tree):
        entity_groups = []
        for entity in _ENTITY_ORDER:
            if entity in mun_tree[name]:
                label = _ENTITY_LABELS.get(entity, entity)
                entity_groups.append((label, mun_tree[name][entity]))
        # Include any entities not in the predefined order
        for entity, laws in sorted(mun_tree[name].items()):
            if entity not in _ENTITY_ORDER:
                label = _ENTITY_LABELS.get(entity, entity)
                entity_groups.append((label, laws))
        mun_groups.append((name, entity_groups))

    ctx = base_context(request)
    lang = ctx["lang"]
    ctx["vs_count"] = len(_load_vs_index(lang))
    ctx["ch_count"] = len(_load_ch_index())
    ctx["mun_count"] = len(mun_entries)
    ctx["mun_groups"] = mun_groups
    ctx["doc_entries"] = _load_doc_index()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", ctx)


def _get_search_index(lang: str = "de"):
    """Lazy-load a per-language Tantivy search index. Returns None if unavailable.

    Tries language-specific index first (data/search_index/{lang}/),
    falls back to legacy flat index (data/search_index/) for backward compat.
    """
    if lang in _search_indexes_loaded:
        return _search_indexes.get(lang)

    _search_indexes_loaded.add(lang)

    # New per-language directory structure
    index_dir = DATA_DIR / "search_index" / lang
    if not index_dir.is_dir():
        # Fallback: legacy flat structure (only for German)
        if lang == "de":
            index_dir = DATA_DIR / "search_index"
        if not index_dir.is_dir():
            log.warning(
                "No search index for %s at %s — run build_search_index.py",
                lang, DATA_DIR / "search_index" / lang,
            )
            return None

    try:
        from lex_akn.search import load_compound_dict, open_index
        compound_words = load_compound_dict(DATA_DIR / "compound_dict.txt") if lang == "de" else None
        idx = open_index(index_dir, compound_words, lang=lang)
        _search_indexes[lang] = idx
        log.info(
            "Search index (%s) loaded from %s",
            lang, index_dir,
        )
    except Exception:
        log.exception("Failed to open search index (%s) at %s", lang, index_dir)
        return None
    return _search_indexes.get(lang)


async def search_endpoint(request: Request) -> Response:
    """Search endpoint: GET /search?q=...&level=ch|vs|mun&limit=25&offset=0

    Uses the search index matching the user's language cookie.
    Falls back to German index if the target language index is unavailable.
    """
    lang = _resolve_cookie_lang(request)
    idx = _get_search_index(lang)
    if idx is None and lang != "de":
        # Fall back to German index
        idx = _get_search_index("de")
        lang = "de"
    if idx is None:
        return JSONResponse(
            {"error": t("search_unavailable", lang)},
            status_code=503,
        )

    q = request.query_params.get("q", "").strip()
    if len(q) < 2:
        return JSONResponse({"hits": [], "facets": {"ch": 0, "vs": 0, "mun": 0}, "query_ms": 0, "total": 0})

    from lex_akn.search import DOC_TYPES

    _VALID_LEVELS = frozenset({"ch", "vs", "mun"})
    _VALID_DOC_TYPES = frozenset(DOC_TYPES)

    # Multi-select: comma-separated values, e.g. ?level=ch,vs
    level_raw = request.query_params.get("level", "")
    level = [v for v in level_raw.split(",") if v in _VALID_LEVELS] or None

    doc_type_raw = request.query_params.get("doc_type", "")
    doc_type = [v for v in doc_type_raw.split(",") if v in _VALID_DOC_TYPES] or None

    limit_str = request.query_params.get("limit", "25")
    try:
        limit = max(1, min(int(limit_str), 100))
    except ValueError:
        limit = 25

    offset_str = request.query_params.get("offset", "0")
    try:
        offset = max(0, min(int(offset_str), 10000))
    except ValueError:
        offset = 0

    from lex_akn.search import search
    try:
        result = search(idx, q, lang=lang, level=level, doc_type=doc_type, limit=limit, offset=offset)
    except Exception:
        log.exception("Search failed for q=%r lang=%s", q, lang)
        return JSONResponse({"error": t("search_invalid", lang)}, status_code=400)
    return JSONResponse(result)


async def doc_view(request: Request) -> Response:
    """Display a non-legislative document: /doc/[{scope}/]{doc_id}[/{date}][/{lang}][/{format}]"""
    rest = request.path_params["rest"]
    try:
        doc = parse_doc(f"/doc/{rest}")
    except ValueError:
        return _not_found("Document not found")

    if doc.lang is not None and doc.lang not in VALID_LANGS:
        return _not_found("Document not found")
    if doc.format is not None and doc.format not in VALID_FORMATS:
        return _not_found("Document not found")

    lang = _resolve_lang(request, doc.lang)

    # Build file path based on scope
    if doc.scope == "mun":
        assert doc.scope_id is not None and doc.entity is not None
        dir_parts = ("doc", "mun", doc.scope_id, doc.entity, doc.doc_id)
    elif doc.scope == "vs":
        dir_parts = ("doc", "vs", doc.doc_id)
    elif doc.scope == "bez":
        assert doc.scope_id is not None
        dir_parts = ("doc", "bez", doc.scope_id, doc.doc_id)
    else:
        dir_parts = ("doc", doc.doc_id)

    xml_path = _resolve_xml_path(*dir_parts, date=doc.date, lang=lang)
    tree = _cached_parse(
        f"doc:{doc.scope}:{doc.scope_id}:{doc.entity}:{doc.doc_id}:{doc.date}:{lang}",
        xml_path,
    )

    # Fallback: implicit lang from cookie not found → try default "de"
    if tree is None and doc.lang is None and lang != "de":
        lang = "de"
        xml_path = _resolve_xml_path(*dir_parts, date=doc.date, lang=lang)
        tree = _cached_parse(
            f"doc:{doc.scope}:{doc.scope_id}:{doc.entity}:{doc.doc_id}:{doc.date}:{lang}",
            xml_path,
        )

    if tree is None:
        return _not_found("Document not found")

    assert xml_path is not None
    return _serve_document(
        request, tree, xml_path,
        eli_path=doc.work_uri,
        level="doc", lang=lang, fmt=doc.format,
    )


async def pub_view(request: Request) -> Response:
    """Display a municipal publication: /pub/mun/{bfs}/{entity}/{organ}/{doctype}/{year}/{number}[/{lang}][/{format}]"""
    rest = request.path_params["rest"]
    try:
        pub = parse_pub(f"/pub/{rest}")
    except ValueError:
        return _not_found("Publication not found")

    if pub.lang is not None and pub.lang not in VALID_LANGS:
        return _not_found("Publication not found")
    if pub.format is not None and pub.format not in VALID_FORMATS:
        return _not_found("Publication not found")

    # Alias normalization: non-canonical entity/organ/doctype → 301 redirect
    from lex_akn.uri import VALID_DOCTYPES, VALID_ORGANS

    c_entity = normalize_entity(pub.entity)
    c_organ = normalize_organ(pub.organ)
    c_doctype = normalize_doctype(pub.doctype)

    if c_entity is not None or c_organ is not None or c_doctype is not None:
        canonical = str(PubUri(
            bfs=pub.bfs,
            entity=c_entity or pub.entity,
            organ=c_organ or pub.organ,
            doctype=c_doctype or pub.doctype,
            year=pub.year, number=pub.number,
            lang=pub.lang, format=pub.format,
        ))
        return RedirectResponse(
            url=_root_path(request) + canonical,
            status_code=301,
        )

    # Validate canonical organ/doctype
    if pub.organ not in VALID_ORGANS:
        return _not_found("Publication not found")
    if pub.doctype not in VALID_DOCTYPES:
        return _not_found("Publication not found")

    lang = _resolve_lang(request, pub.lang)

    # File path: data/pub/mun/{bfs}/{entity}/{organ}/{doctype}/{year}/{number}/{lang}.xml
    pub_dir_parts = (
        "pub", "mun", pub.bfs, pub.entity,
        pub.organ, pub.doctype, pub.year, pub.number,
    )
    xml_path = _resolve_xml_path(*pub_dir_parts, date=None, lang=lang)
    cache_key = f"pub:{pub.bfs}:{pub.entity}:{pub.organ}:{pub.doctype}:{pub.year}:{pub.number}"
    tree = _cached_parse(f"{cache_key}:{lang}", xml_path)

    # Fallback: implicit lang from cookie not found → try default "de"
    if tree is None and pub.lang is None and lang != "de":
        lang = "de"
        xml_path = _resolve_xml_path(*pub_dir_parts, date=None, lang=lang)
        tree = _cached_parse(f"{cache_key}:{lang}", xml_path)

    if tree is None:
        return _not_found("Publication not found")

    assert xml_path is not None
    return _serve_document(
        request, tree, xml_path,
        eli_path=pub.work_uri,
        level="pub", lang=lang, fmt=pub.format,
    )


async def set_lang(request: Request) -> Response:
    """Set language preference cookie: POST /lang with form field 'lang'."""
    form = await request.form()
    lang = form.get("lang", "")
    if lang not in VALID_LANGS:
        return Response(content="Invalid language", status_code=400)

    # Redirect back to referrer if same-origin, else root
    referer = request.headers.get("referer", "")
    target = ""
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            req_url = request.url
            if (
                parsed.scheme == req_url.scheme
                and parsed.hostname == req_url.hostname
                and parsed.port == req_url.port
            ):
                target = parsed.path or "/"
                if parsed.query:
                    target += f"?{parsed.query}"
                if parsed.fragment:
                    target += f"#{parsed.fragment}"
        elif referer.startswith("/"):
            target = referer
    if not target:
        target = _root_path(request) + "/"
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(
        key="lang",
        value=lang,
        max_age=365 * 24 * 3600,  # 1 year
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


routes = [
    Route("/", endpoint=index),
    Route("/search", endpoint=search_endpoint),
    Route("/lang", endpoint=set_lang, methods=["POST"]),
    Route("/doc/{rest:path}", endpoint=doc_view),
    Route("/pub/{rest:path}", endpoint=pub_view),
    Route("/eli/ch/", endpoint=federal_index),
    Route("/eli/ch/{sr_number:path}", endpoint=federal_law),
    Route("/eli/vs/", endpoint=cantonal_index),
    Route("/eli/vs/{sysno:path}", endpoint=cantonal_law),
    Route("/eli/mun/{bfs}/{rest:path}", endpoint=municipal_law),
]
