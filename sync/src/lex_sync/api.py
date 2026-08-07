"""lex.vs.ch API client for sync operations."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

BASE_URL = "https://lex.vs.ch/api"
TIMEOUT = 30.0


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
    resp = client.get(f"{BASE_URL}/{lang}/texts_of_law/lightweight_index")
    resp.raise_for_status()
    data = resp.json()

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
    resp = client.get(f"{BASE_URL}/{lang}/systematic_categories")
    resp.raise_for_status()
    return [_parse_category(cat) for cat in resp.json()]


def fetch_document_json(
    client: httpx.Client, systematic_number: str, lang: str = "de",
) -> dict | None:
    """Fetch a law's structured JSON content.

    Returns the full API response dict, or None if not found.
    """
    resp = client.get(
        f"{BASE_URL}/{lang}/texts_of_law/{systematic_number}/show_as_json",
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


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
    resp = client.get(
        f"{BASE_URL}/{lang}/texts_of_law/{sysno}/versions/{version_id}/show_as_json",
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


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
