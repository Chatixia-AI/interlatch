"""Bringing an archive up to the current schema: every statement in SCHEMA is idempotent, so an upgrade re-runs
it, adds the columns an older archive lacks, and then does the few data changes a version needed."""

from __future__ import annotations

import sqlite3

from .schema import SCHEMA, SCHEMA_VERSION


def init_schema(conn: sqlite3.Connection) -> None:
    """Create or upgrade the schema. Every statement is idempotent, so additive migrations just re-run it."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    statements = list(_statements(SCHEMA))
    tables = [st for st in statements if st.upper().startswith("CREATE TABLE")]
    # tables first, then columns an older database lacks, then indexes/triggers that may reference them
    for st in tables:
        conn.execute(st)
    _add_missing_columns(conn)
    for st in statements:
        if st not in tables:
            conn.execute(st)
    if current < 8:
        _derive_maturity_stages(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def _statements(script: str):
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf.strip()
            buf = ""


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS never alters an existing table: add any column SCHEMA declares that the DB lacks."""
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(SCHEMA)
        tables = [r[0] for r in ref.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE '%fts%' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for _cid, name, ctype, notnull, default, _pk in ref.execute(f"PRAGMA table_info({table})"):
                if name in have:
                    continue
                decl = f"{name} {ctype}".strip()
                if default is not None:
                    decl += f" DEFAULT {default}" + (" NOT NULL" if notnull else "")
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {decl}")
    finally:
        ref.close()


def _derive_maturity_stages(conn: sqlite3.Connection) -> None:
    """Schema 8 gave knowledge a maturity stage: derive it for the items an older archive has, from the evidence they
    already carry. The ladder's own rules do it, so this is the one place storage calls up into a feature."""
    from ..ladder import refresh_all

    refresh_all(conn)
