"""Starlette application factory."""

from __future__ import annotations

import base64
import datetime
import hashlib
import logging
from pathlib import Path

from openmun_editor import static_path as editor_static_path
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from lex_web.i18n import t
from lex_web.routes import routes

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def _compute_sri_hashes(static_dir: Path) -> dict[str, str]:
    """Compute SHA-384 SRI hashes for JS/CSS files at startup."""
    hashes: dict[str, str] = {}
    for pattern in ("*.js", "*.css"):
        for f in static_dir.glob(pattern):
            digest = hashlib.sha384(f.read_bytes()).digest()
            b64 = base64.b64encode(digest).decode("ascii")
            key = str(f.relative_to(static_dir))
            hashes[key] = f"sha384-{b64}"
    log.info("Computed SRI hashes for %d static files", len(hashes))
    return hashes


_MONTH_NAMES = {
    "de": [
        "", "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ],
    "fr": [
        "", "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ],
    "it": [
        "", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
    ],
}


def _format_date(
    dt: "datetime.date | str | None", fmt: str = "long", lang: str = "de",
) -> str:
    """Format a date for display in the given language.

    Accepts a date object or an ISO "YYYY-MM-DD" string (e.g. meta.json
    repealed_date).  Unparseable strings are returned verbatim.

    fmt="long":  05. Februar 2004 / 05 février 2004 / 05 febbraio 2004
    fmt="short": 05.02.2004 (same for all languages)
    """
    if dt is None or dt == "":
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.date.fromisoformat(dt)
        except ValueError:
            return dt
    if fmt == "short":
        return dt.strftime("%d.%m.%Y")
    months = _MONTH_NAMES.get(lang, _MONTH_NAMES["de"])
    if lang == "de":
        return f"{dt.day:02d}. {months[dt.month]} {dt.year}"
    # French and Italian: no period after day
    return f"{dt.day:02d} {months[dt.month]} {dt.year}"


def create_app() -> ASGIApp:
    """Create and configure the Starlette application."""
    starlette = Starlette(
        routes=[
            *routes,
            # Shared editor assets from the openmun-editor package. Must be
            # mounted before /static: Starlette matches mounts in order.
            Mount(
                "/static/editor",
                app=StaticFiles(directory=str(editor_static_path())),
                name="editor",
            ),
            Mount(
                "/static",
                app=StaticFiles(directory=str(STATIC_DIR)),
                name="static",
            ),
        ],
    )

    # Set up Jinja2 templates with SRI hash helper + i18n
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    sri_hashes = _compute_sri_hashes(STATIC_DIR)
    templates.env.globals["sri"] = lambda name: sri_hashes.get(name, "")
    templates.env.globals["t"] = t
    templates.env.filters["format_date"] = _format_date
    starlette.state.templates = templates

    # Security headers are owned by the Angie reverse proxy (lex.conf).
    # The app does not set its own security headers to avoid duplication.
    return starlette


app = create_app()
