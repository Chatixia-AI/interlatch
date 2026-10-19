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
    if 0 < current < 20:
        scrub_analyses(conn)
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
    from ..knowledge.ladder import refresh_all

    refresh_all(conn)


# What a hub keeps of a session another computer shared as knowledge (source 'remote'): never that computer's raw
# analysis, the person's mood or friction notes. Hubs before schema 20 kept what computers sent of them.
_REMOTE_CLEARED = ("analysis_json", "sentiment", "friction_json", "analysis_reason")
_SESSION_TEXT = ("llm_title", "title", "summary", "goal", "outcome_note")
_SESSION_JSON = ("work_types_json", "tags_json", "highlights_json", "open_threads_json", "friction_json")


def scrub_analyses(conn: sqlite3.Connection) -> dict:
    """Schema 20: sessions shared with this computer as a hub (source 'remote') lose the raw analysis, mood and
    friction notes their computers used to send, and every session's title, analysis text and friction notes, and the
    shared sessions' lessons, are redacted again (titles and friction notes used to be stored as the model wrote them).
    Rows already clean are left alone, so a copy in Postgres (mirror.py) writes again only the rows that changed.
    Returns how many rows changed, by table."""
    from ..core.redact import redact, redact_json

    cleared = conn.execute(f"UPDATE sessions SET {', '.join(f'{c} = NULL' for c in _REMOTE_CLEARED)} "
                           f"WHERE source = 'remote' AND ({' OR '.join(f'{c} IS NOT NULL' for c in _REMOTE_CLEARED)})"
                           ).rowcount
    sessions = set()
    cols = _SESSION_TEXT + tuple(c for c in _SESSION_JSON if c not in _SESSION_TEXT)
    for r in conn.execute(f"SELECT id, {', '.join(cols)} FROM sessions WHERE llm_title IS NOT NULL "
                          "OR friction_json IS NOT NULL OR source = 'remote'").fetchall():
        new = {c: redact(r[c]) if isinstance(r[c], str) else r[c] for c in _SESSION_TEXT}
        new.update({c: redact_json(r[c]) for c in _SESSION_JSON})
        changed = {c: v for c, v in new.items() if v != r[c]}
        if changed:
            conn.execute(f"UPDATE sessions SET {', '.join(f'{c} = ?' for c in changed)} WHERE id = ?",
                         [*changed.values(), r["id"]])
            sessions.add(r["id"])
    lessons = 0
    for r in conn.execute("SELECT id, title, body, evidence, tags_json, case_json FROM knowledge WHERE session_id IN "
                          "(SELECT id FROM sessions WHERE source = 'remote')").fetchall():
        new = {c: redact(r[c]) if isinstance(r[c], str) else r[c] for c in ("title", "body", "evidence")}
        new.update({c: redact_json(r[c]) for c in ("tags_json", "case_json")})
        changed = {c: v for c, v in new.items() if v != r[c]}
        if changed:
            conn.execute(f"UPDATE knowledge SET {', '.join(f'{c} = ?' for c in changed)} WHERE id = ?",
                         [*changed.values(), r["id"]])
            lessons += 1
    return {"sessions_cleared": cleared, "sessions_redacted": len(sessions), "lessons_redacted": lessons}
