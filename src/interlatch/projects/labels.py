"""The name each project goes by: its folder's, or a longer one where two projects share a folder name."""

from __future__ import annotations

import sqlite3
from collections import Counter


def project_labels(conn: sqlite3.Connection) -> dict[str, str]:
    """Unique display label per project path (basename, disambiguated by parent when needed)."""
    paths = [r[0] for r in conn.execute("SELECT DISTINCT project_path FROM sessions WHERE project_path IS NOT NULL")]
    names = Counter(p.rstrip("/").rsplit("/", 1)[-1] for p in paths)
    labels = {}
    for p in paths:
        parts = p.rstrip("/").split("/")
        labels[p] = parts[-1] if names[parts[-1]] == 1 or len(parts) < 2 else f"{parts[-2]}-{parts[-1]}"
    return labels
