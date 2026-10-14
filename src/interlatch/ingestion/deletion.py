"""Forgetting sessions (they stay forgotten: a later sync never brings them back), and flagging the ones whose
source transcript is gone."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from ..core.config import Config
from ..core.util import file_lock, utcnow_iso
from ..sources.claude.files import session_dir_for
from ..storage.kv import kv_get, kv_set


def forgotten_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0].split(":", 1)[1] for r in conn.execute("SELECT key FROM kv WHERE key LIKE 'forget:%'")}


def forget_session(conn: sqlite3.Connection, cfg: Config, sid: str, *, delete_transcript: bool = False,
                   remember: bool = True) -> list[str]:
    """Remove every trace of a session from the vault (and optionally the original transcript); never re-ingest it,
    unless `remember` is off (a hub giving back what a computer took back: it may share it again later)."""
    with file_lock(cfg.locks_dir / "ingest.lock", timeout=900) as got:
        if not got:
            raise RuntimeError("a sync is still running; try again in a minute")
        if remember:
            kv_set(conn, f"forget:{sid}", utcnow_iso())  # first, so a racing ingest also skips it
            conn.commit()
        return _forget(conn, sid, delete_transcript)


def _forget(conn: sqlite3.Connection, sid: str, delete_transcript: bool) -> list[str]:
    row = conn.execute("SELECT transcript_path, archive_path FROM sessions WHERE id = ?", (sid,)).fetchone()
    removed = []
    for table in ("events", "tool_calls", "session_files", "subagents", "api_calls", "artifacts"):
        conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (sid,))
    n = conn.execute("DELETE FROM knowledge WHERE session_id = ?", (sid,)).rowcount
    conn.execute("DELETE FROM analyses WHERE target = ?", (sid,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
    removed.append(f"database rows (incl. {n} knowledge items)")
    def in_use(path: str) -> bool:  # also another session's: a chat export, Bob's or Copilot's database
        return conn.execute("SELECT 1 FROM sessions WHERE archive_path = ? OR transcript_path = ? LIMIT 1",
                            (path, path)).fetchone() is not None

    paths = []
    if row and row["archive_path"] and not in_use(row["archive_path"]):
        paths += [Path(row["archive_path"]), session_dir_for(Path(row["archive_path"]))]
    if delete_transcript and row and row["transcript_path"] and not in_use(row["transcript_path"]):
        paths += [Path(row["transcript_path"]), session_dir_for(Path(row["transcript_path"]))]
    for p in paths:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(str(p))
        elif p.exists():
            p.unlink()
            removed.append(str(p))
    note = kv_get(conn, f"export:{sid}")
    if note:
        Path(note.split("|", 1)[0]).unlink(missing_ok=True)
        conn.execute("DELETE FROM kv WHERE key = ?", (f"export:{sid}",))
        removed.append("markdown note")
    conn.execute("DELETE FROM files_state WHERE path LIKE ?", (f"%{sid}%",))
    conn.commit()
    return removed


def mark_missing_sources(conn: sqlite3.Connection) -> int:
    missing = 0
    for r in conn.execute("SELECT id, transcript_path, source_present FROM sessions "
                          "WHERE source NOT IN ('history', 'remote')").fetchall():  # remote: its transcript is elsewhere
        present = 1 if r["transcript_path"] and Path(r["transcript_path"]).exists() else 0
        if present != r["source_present"]:
            conn.execute("UPDATE sessions SET source_present = ? WHERE id = ?", (present, r["id"]))
        missing += 1 - present
    return missing
