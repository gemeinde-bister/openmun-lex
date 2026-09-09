"""lex.vs.ch API client for sync operations."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from lex_sync.runlog import log

BASE_URL = "https://lex.vs.ch/api"
TIMEOUT = 30.0

# Retry policy for GET requests: transient transport errors and 429/5xx are
# retried with a short backoff; a 404 is a valid answer and never retried.
ATTEMPTS = 3
BACKOFF_SECONDS = 1.0
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def _get(client: httpx.Client, url: str) -> httpx.Response:
    """GET with retries for transient failures.

    Returns the response for any 2xx or 404; raises ``httpx.HTTPStatusError``
    for other status codes and ``httpx.TransportError`` once the retries are
    exhausted.
    """
    last_exc: httpx.HTTPError | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            resp = client.get(url)
        except httpx.TransportError as exc:
            last_exc = exc
            reason = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code == 404 or resp.is_success:
                return resp
            if resp.status_code not in _RETRY_STATUS:
                resp.raise_for_status()
            reason = f"HTTP {resp.status_code}"
            last_exc = httpx.HTTPStatusError(
                reason, request=resp.request, response=resp,
            )
        if attempt < ATTEMPTS:
            log.warning("  retry %d/%d for %s (%s)", attempt, ATTEMPTS, url, reason)
            time.sleep(BACKOFF_SECONDS * attempt)
    assert last_exc is not None
    raise last_exc


@dataclass(frozen=True)
class LawEntry:
    """A law from the lightweight index."""

    id: int
    systematic_number: str
    title: str
    category_id: int
    abrogated: bool
    structured_document_id: int


@dataclass(frozen=True)
class Category:
    """A systematic category (table of contents entry)."""

    id: int
    systematic_number: str
    name: str
    children: tuple[Category, ...]


def fetch_index(client: httpx.Client, lang: str = "de") -> list[LawEntry]:
    """Fetch the lightweight index of all laws.

    Returns a flat list of LawEntry sorted by systematic_number.
    """
    resp = _get(client, f"{BASE_URL}/{lang}/texts_of_law/lightweight_index")
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or not data:
        raise ValueError("lightweight_index returned no categories")

    entries: list[LawEntry] = []
    for _cat_id, laws in data.items():
        for law in laws:
            entries.append(LawEntry(
                id=law["id"],
                systematic_number=law["systematic_number"],
                title=law["title"],
                category_id=law["systematic_category_id"],
                abrogated=law.get("abrogated", False),
                structured_document_id=law["structured_document_id"],
            ))

    entries.sort(key=lambda e: _sysno_sort_key(e.systematic_number))
    return entries


def fetch_categories(client: httpx.Client, lang: str = "de") -> list[Category]:
    """Fetch the systematic category tree."""
    resp = _get(client, f"{BASE_URL}/{lang}/systematic_categories")
    resp.raise_for_status()
    return [_parse_category(cat) for cat in resp.json()]


def fetch_document_json(
    client: httpx.Client, systematic_number: str, lang: str = "de",
) -> dict | None:
    """Fetch a law's structured JSON content.

    Returns the full API response dict, or None if not found.
    """
    resp = _get(
        client,
        f"{BASE_URL}/{lang}/texts_of_law/{systematic_number}/show_as_json",
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return _document_payload(resp)


def fetch_version_json(
    client: httpx.Client,
    sysno: str,
    version_id: int,
    lang: str = "de",
) -> dict | None:
    """Fetch a specific historical version's structured JSON content.

    Args:
        client: httpx client instance.
        sysno: Systematic number (e.g. "175.1").
        version_id: Version ID from old_versions[].id.
        lang: Language code (de or fr).

    Returns the full API response dict, or None if not found.
    """
    resp = _get(
        client,
        f"{BASE_URL}/{lang}/texts_of_law/{sysno}/versions/{version_id}/show_as_json",
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return _document_payload(resp)


def _document_payload(resp: httpx.Response) -> dict:
    """Decode a show_as_json response and check its shape.

    Raises ``ValueError`` when the body is not the expected document
    envelope, so a changed or broken upstream API fails loudly instead of
    producing empty AKN files.
    """
    try:
        data = resp.json()
    except ValueError as exc:
        raise ValueError(f"{resp.url}: response is not JSON") from exc
    if not isinstance(data, dict) or "text_of_law" not in data:
        raise ValueError(f"{resp.url}: unexpected payload (no text_of_law)")
    return data


def _parse_category(raw: dict) -> Category:
    """Parse a category from the API response (recursive)."""
    cat = raw["systematic_category"]
    children = tuple(
        _parse_category(child) for child in cat.get("children", [])
    )
    return Category(
        id=cat["id"],
        systematic_number=cat["systematic_number"],
        name=cat["name"],
        children=children,
    )


def _sysno_sort_key(sysno: str) -> tuple[tuple[int, str], ...]:
    """Sort key for systematic numbers like '175.1', '175.100', '101.1'.

    Splits on dots. Each part becomes (int_value, original_str) so numeric
    parts sort numerically and non-numeric parts sort lexically without
    mixed-type comparison errors.
    """
    parts: list[tuple[int, str]] = []
    for part in sysno.split("."):
        try:
            parts.append((int(part), ""))
        except ValueError:
            parts.append((0, part))
    return tuple(parts)


def make_client() -> httpx.Client:
    """Create a configured httpx client for lex.vs.ch API."""
    return httpx.Client(
        timeout=TIMEOUT,
        headers={"Accept": "application/json"},
        follow_redirects=True,
    )
