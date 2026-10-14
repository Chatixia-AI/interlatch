"""Search text as the archive's full-text index takes it: quoted trigram phrases, and the terms too short for it."""

from __future__ import annotations

import re


def fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query (AND of quoted phrases, trigram needs >= 3 chars)."""
    terms = [t for t in re.findall(r'"[^"]+"|\S+', text.strip())]
    out = []
    for t in terms:
        t = t.strip('"').replace('"', '""')
        if len(t) >= 3:
            out.append(f'"{t}"')
    return " AND ".join(out) if out else None


def short_terms(text: str) -> list[str]:
    """Search terms too short for the trigram index (handled with LIKE)."""
    return [t.strip('"') for t in re.findall(r'"[^"]+"|\S+', text.strip()) if 0 < len(t.strip('"')) < 3]
