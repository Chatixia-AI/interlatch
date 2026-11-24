"""Search text as the archive's full-text index takes it: quoted trigram phrases, and the terms too short for it.

A query arrives as groups (application.search.query_groups): every group must match, any one of its names will do."""

from __future__ import annotations


def fts_query(groups: list[dict]) -> str | None:
    """A safe FTS5 query for the groups long enough for the trigram index (3+ characters): each a quoted phrase, or
    an OR of them, ANDed."""
    out = []
    for g in groups:
        if len(g["text"]) >= 3:
            phrases = ['"' + n.replace('"', '""') + '"' for n in g["names"]]
            out.append(phrases[0] if len(phrases) == 1 else "(" + " OR ".join(phrases) + ")")
    return " AND ".join(out) if out else None


def short_terms(groups: list[dict]) -> list[str]:
    """Search terms too short for the trigram index (handled with LIKE)."""
    return [g["text"] for g in groups if len(g["text"]) < 3]
