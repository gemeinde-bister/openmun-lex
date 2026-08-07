"""Template context helpers for reverse-proxy-aware URL generation."""

from __future__ import annotations

from starlette.requests import Request

from lex_web.i18n import SUPPORTED_LANGS


def _root_path(request: Request) -> str:
    """Extract the reverse proxy prefix from the request.

    Reads X-Forwarded-Prefix header directly — do NOT set root_path
    in the ASGI scope, it breaks StaticFiles mount routing.

    Returns empty string if no prefix is configured.
    """
    prefix = request.headers.get("x-forwarded-prefix", "")
    if not prefix:
        return ""
    prefix = prefix.strip()
    if not prefix.startswith("/"):
        return ""
    if "://" in prefix or "\\" in prefix or "\x00" in prefix:
        return ""
    if "//" in prefix:
        return ""
    return prefix.rstrip("/")


def _resolve_cookie_lang(request: Request) -> str:
    """Read and validate the language cookie, default to 'de'."""
    cookie_lang = request.cookies.get("lang", "")
    if cookie_lang in SUPPORTED_LANGS:
        return cookie_lang
    return "de"


def base_context(request: Request, *, lang: str | None = None) -> dict:
    """Build the base template context with root_path, lang, and request.

    If lang is not explicitly provided, reads from cookie (validated
    against SUPPORTED_LANGS), defaulting to "de".
    """
    if lang is None:
        lang = _resolve_cookie_lang(request)
    return {
        "request": request,
        "root_path": _root_path(request),
        "lang": lang,
    }
