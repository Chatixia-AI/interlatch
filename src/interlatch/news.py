"""On a hub: what other computers sent lately, for the Team overview, the rail's badge and the dashboard's toasts.

A row per computer, project and kind, which a computer that keeps sending adds to (FOLD_MINUTES): sessions this hub
had not had before and project lessons new to it ("shared"), or a computer that joined with an invite ("joined"). A
session sent again because it grew is not news; a lesson it gained is. Recorded where sessions arrive: a computer's
shared knowledge (hub.receive_sessions), its transcripts once stored here (ingest.store_parsed), and the lessons this
hub's own analysis of those finds (analyze.store_analysis).

Who hears of what follows the Team page: someone limited to projects, of theirs only (access.py shadows this table
for them as well); joins, admins only. What each viewer saw last is kept here, per person ("here" for the hub
computer itself), so it follows them from browser to browser.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import timedelta

from .db import kv_get, kv_set
from .util import to_iso, utcnow

FOLD_MINUTES = 30
KEEP_DAYS = 90
FIRST_DAYS = 7  # someone who never opened the Team overview: what came in this recently is new to them
LIMIT = 30
SEEN_KV = "news-seen:"  # + person id, or "here"
ISO_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")
LESSON = "source = 'analysis' AND scope = 'project' AND kind != 'preference'"  # what the Team page counts as one


def lesson_keys(conn: sqlite3.Connection, session_id: str, *, active: bool = False) -> set[tuple[str, str]]:
    """A session's project lessons, before it is stored again: learned() tells which of them are new."""
    return {(r[0], r[1]) for r in conn.execute(
        f"SELECT kind, title FROM knowledge WHERE session_id = ? AND {LESSON}" + (" AND status = 'active'" if active else ""),
        (session_id,))}


def learned(conn: sqlite3.Connection, session_id: str, before: set[tuple[str, str]]) -> int:
    return len(lesson_keys(conn, session_id, active=True) - before)


def record(conn: sqlite3.Connection, machine_id: str, project_path: str | None, *, sessions: int = 0, lessons: int = 0,
           kind: str = "shared") -> None:
    """Add to another computer's news: to its row for this project if it sent within FOLD_MINUTES, else a new row.
    The caller commits."""
    if kind == "shared" and not (sessions or lessons):
        return
    now = utcnow()
    at = to_iso(now)
    row = conn.execute("SELECT id FROM hub_news WHERE kind = 'shared' AND machine_id = ? AND project_path IS ? AND at >= ? "
                       "ORDER BY at DESC LIMIT 1",
                       (machine_id, project_path, to_iso(now - timedelta(minutes=FOLD_MINUTES)))).fetchone() \
        if kind == "shared" else None
    if row:
        conn.execute("UPDATE hub_news SET at = ?, sessions = sessions + ?, lessons = lessons + ? WHERE id = ?",
                     (at, sessions, lessons, row[0]))
    else:
        conn.execute("INSERT INTO hub_news(at, since, kind, machine_id, project_path, sessions, lessons) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (at, at, kind, machine_id, project_path, sessions, lessons))
    conn.execute("DELETE FROM hub_news WHERE at < ?", (to_iso(now - timedelta(days=KEEP_DAYS)),))


def _visible(scope: list[str] | None, admin: bool) -> tuple[list[str], list]:
    where, params = [], []
    if not admin:
        where.append("n.kind = 'shared'")
    if scope is not None:
        where.append(f"n.project_path IN ({', '.join('?' for _ in scope) or 'NULL'})")
        params += scope
    return where, params


def items(conn: sqlite3.Connection, *, scope: list[str] | None, admin: bool, after: str | None = None,
          limit: int = LIMIT) -> list[dict]:
    """The latest news this viewer may see, newest first; with `after`, only rows added to since then."""
    where, params = _visible(scope, admin)
    if after:
        where.append("n.at >= ?")  # >=: a row added to in the same millisecond is sent again, never missed
        params.append(after)
    rows = conn.execute(
        "SELECT n.id, n.at, n.since, n.kind, n.project_path, n.sessions, n.lessons, m.name AS computer, p.name AS person "
        "FROM hub_news n LEFT JOIN machines m ON m.id = n.machine_id "
        "LEFT JOIN people p ON p.id = m.person_id AND p.removed_at IS NULL "
        f"WHERE {' AND '.join(where) or '1'} ORDER BY n.at DESC, n.id DESC LIMIT ?", [*params, limit])
    return [dict(r) for r in rows]


def unseen(conn: sqlite3.Connection, viewer: dict | None, *, scope: list[str] | None, admin: bool) -> int:
    where, params = _visible(scope, admin)
    where.append("n.at > ?")
    return conn.execute(f"SELECT COUNT(*) FROM hub_news n WHERE {' AND '.join(where)}",
                        [*params, seen_at(conn, viewer)]).fetchone()[0]


def _key(viewer: dict | None) -> str:
    return SEEN_KV + (str(viewer["id"]) if viewer and viewer.get("id") is not None else "here")


def seen_at(conn: sqlite3.Connection, viewer: dict | None) -> str:
    return kv_get(conn, _key(viewer)) or to_iso(utcnow() - timedelta(days=FIRST_DAYS))


def mark_seen(conn: sqlite3.Connection, viewer: dict | None, at) -> None:
    """The viewer saw the news up to `at` (the time the page they saw was made; never later than now, never back)."""
    now = to_iso(utcnow())
    at = at if isinstance(at, str) and ISO_RE.fullmatch(at) and at <= now else now
    if at > (kv_get(conn, _key(viewer)) or ""):
        kv_set(conn, _key(viewer), at)
        conn.commit()
