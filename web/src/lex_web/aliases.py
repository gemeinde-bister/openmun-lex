"""URI alias vocabularies and normalization.

Maps non-canonical URI path segments to their canonical equivalents.
When a request uses an alias, the route handler returns a 301 redirect
to the canonical URI.

Canonical language priority: de → fr → en (configurable per deployment).
See docs/URI-MODEL.md for the full vocabulary tables.
"""

from __future__ import annotations

# Entity type aliases: alias → canonical
ENTITY_ALIASES: dict[str, str] = {
    "cm": "eg",
    "cb": "bg",
}

# Prefix aliases for entity types with slug (gt:slug ↔ co:slug)
ENTITY_PREFIX_ALIASES: dict[str, str] = {
    "co:": "gt:",
}

# Organ type aliases: alias → canonical
ORGAN_ALIASES: dict[str, str] = {
    "versammlung": "assembly",
    "assemblee": "assembly",
    "parlament": "parliament",
    "parlement": "parliament",
    "rat": "council",
    "conseil": "council",
}

# Document type aliases: alias → canonical
DOCTYPE_ALIASES: dict[str, str] = {
    "protokoll": "protocol",
    "protocole": "protocol",
    "beschluss": "decision",
    # "decision" in French is same as canonical English — not an alias
    "mitteilung": "notice",
    "communication": "notice",
}


def normalize_entity(entity: str) -> str | None:
    """Return the canonical form if entity is an alias, else None.

    Handles both plain codes (cm→eg) and prefixed slugs (co:xyz→gt:xyz).
    """
    if entity in ENTITY_ALIASES:
        return ENTITY_ALIASES[entity]

    for prefix, canonical_prefix in ENTITY_PREFIX_ALIASES.items():
        if entity.startswith(prefix):
            return canonical_prefix + entity[len(prefix):]

    return None


def normalize_organ(organ: str) -> str | None:
    """Return the canonical form if organ is an alias, else None."""
    return ORGAN_ALIASES.get(organ)


def normalize_doctype(doctype: str) -> str | None:
    """Return the canonical form if doctype is an alias, else None."""
    return DOCTYPE_ALIASES.get(doctype)
