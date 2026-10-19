"""Who owns each of this computer's sessions once named profiles exist (core.profiles): the session_owner table in
Personal's archive. The table is made when the first named profile is, so an archive without profiles never changes.

A session's owner is decided once, as it is recorded, and kept (route()'s "recorded" rule): adding a profile's folder
around it later doesn't move it. Only `interlatch profile own` / `choose` move sessions, the folder's history
included, and "waiting" is never kept: a waiting session is routed again until a rule settles it.
"""

from __future__ import annotations

import json
import sqlite3

from ..core.config import Config
from ..core.profiles import PERSONAL, WAITING, Registry, Route, route, under
from ..core.util import utcnow_iso

TABLE = """CREATE TABLE IF NOT EXISTS session_owner (
    session_id TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,      -- 'personal', a profile's id, or 'waiting'
    rule       TEXT NOT NULL,      -- what decided: recorded, folder, remote, conflict, default, moved
    folder     TEXT,               -- where it ran: its repository's top folder when it is one
    remote     TEXT,               -- the repository's normalized git remote, when known
    detail     TEXT,
    decided_at TEXT NOT NULL
)"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(TABLE)
    conn.execute("CREATE INDEX IF NOT EXISTS session_owner_owner ON session_owner(owner)")


def has_table(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'session_owner'").fetchone() is not None


def owner_of(conn: sqlite3.Connection, session_id: str) -> str | None:
    if not has_table(conn):
        return None
    r = conn.execute("SELECT owner FROM session_owner WHERE session_id = ?", (session_id,)).fetchone()
    return r[0] if r else None


def known_remotes(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """{remote: profiles} of the repositories seen in a named profile's folders: the same repository cloned elsewhere
    belongs to that profile too (route()'s remote rule)."""
    out: dict[str, set[str]] = {}
    if not has_table(conn):
        return out
    for remote, owner in conn.execute("SELECT DISTINCT remote, owner FROM session_owner WHERE rule IN ('folder', 'moved') "
                                      "AND remote IS NOT NULL AND owner NOT IN (?, ?)", (PERSONAL, WAITING)):
        out.setdefault(remote, set()).add(owner)
    return out


def repo_of(conn: sqlite3.Connection, folder: str | None, remote: str | None = None) -> tuple[str | None, str | None]:
    """(top folder, normalized remote) of the repository at `folder`, cached in kv as the project resolver does;
    a remote the transcript recorded wins."""
    from ..storage.kv import kv_get, kv_set
    from .routing import git_info, normalize_remote

    remote = normalize_remote(remote) if remote else None
    if not folder:
        return None, remote
    key = f"git:{folder}"
    cached = kv_get(conn, key)
    if cached is None:
        info = git_info(folder)
        cached = json.dumps(list(info)) if info else ""
        kv_set(conn, key, cached)
    if cached:
        top, found = json.loads(cached)
        return top, remote or found
    return folder, remote


def decide(conn: sqlite3.Connection, reg: Registry, session_id: str, folder: str | None, remote: str | None = None) -> Route:
    """Route one session (keeping the owner it has) and record the answer."""
    ensure_table(conn)
    prev = conn.execute("SELECT owner FROM session_owner WHERE session_id = ?", (session_id,)).fetchone()
    top, remote = repo_of(conn, folder, remote)
    r = route(reg, folder, remote, recorded=prev[0] if prev else None, known=known_remotes(conn))
    if not prev or r.rule != "recorded":
        _record(conn, session_id, r, top or folder, remote)
    return r


def _record(conn, session_id: str, r: Route, folder: str | None, remote: str | None) -> None:
    conn.execute("INSERT INTO session_owner(session_id, owner, rule, folder, remote, detail, decided_at) "
                 "VALUES (?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET owner = excluded.owner, "
                 "rule = excluded.rule, folder = excluded.folder, remote = excluded.remote, detail = excluded.detail, "
                 "decided_at = excluded.decided_at",
                 (session_id, r.owner, r.rule, folder, remote, r.detail, utcnow_iso()))


def assign(conn: sqlite3.Connection, cfg: Config, session_id: str, folder: str | None, remote: str | None = None) -> str | None:
    """As a session is recorded here: its owner, or None when there are no named profiles (nothing is recorded) or
    profiles.toml can't be read: then it has no owner, and whatever shares only Personal's sessions leaves it out."""
    from ..core.profiles import RegistryError

    try:
        reg = cfg.profiles
    except RegistryError:
        return None
    if reg is None:
        return None
    return decide(conn, reg, session_id, folder, remote).owner


def assign_all(conn: sqlite3.Connection, cfg: Config, reg: Registry | None = None) -> dict[str, int]:
    """Give every session of this computer that has none (or waits) an owner: after the first profile is made, and on
    each sync. Sessions other computers sent here (a hub's) are Personal's: their folders are theirs, not this
    computer's. Returns {owner: sessions} of what it decided."""
    from ..core.machine import local_machine_id

    reg = reg or cfg.profiles
    if reg is None:
        return {}
    ensure_table(conn)
    me = local_machine_id(cfg)
    todo = conn.execute(
        "SELECT s.id, COALESCE(s.machine_path, s.project_path) folder, s.machine_id FROM sessions s "
        "LEFT JOIN session_owner o ON o.session_id = s.id WHERE o.session_id IS NULL OR o.owner = ?", (WAITING,)).fetchall()
    counts: dict[str, int] = {}
    for sid, folder, machine in todo:
        if machine and machine != me:
            r = Route(PERSONAL, "default", "sent by another computer")
            _record(conn, sid, r, folder, None)
        else:
            r = decide(conn, reg, sid, folder)
        counts[r.owner] = counts.get(r.owner, 0) + 1
    return counts


def sessions_in(conn: sqlite3.Connection, folder: str, cfg: Config) -> list[dict]:
    """This computer's sessions that ran in `folder` or below it, with their owner (None: not decided yet)."""
    from ..core.machine import local_machine_id

    ensure_table(conn)
    me = local_machine_id(cfg)
    rows = conn.execute(
        "SELECT s.id, COALESCE(s.machine_path, s.project_path) folder, s.project_path, o.owner, o.remote "
        "FROM sessions s LEFT JOIN session_owner o ON o.session_id = s.id "
        "WHERE (s.machine_id IS NULL OR s.machine_id = ?) AND s.project_path IS NOT NULL", (me,)).fetchall()
    return [dict(r) for r in rows if under(r["folder"], folder)]


def move(conn: sqlite3.Connection, session_ids: list[str], owner: str, folder: str) -> int:
    """Give these sessions to `owner` (`profile own` / `choose`): the one way an owner changes."""
    ensure_table(conn)
    for sid in session_ids:
        row = conn.execute("SELECT folder, remote FROM session_owner WHERE session_id = ?", (sid,)).fetchone()
        top, remote = (row[0], row[1]) if row else (folder, None)
        _record(conn, sid, Route(owner, "moved", folder), top, remote)
    return len(session_ids)


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """{owner: sessions}."""
    if not has_table(conn):
        return {}
    return {o: n for o, n in conn.execute("SELECT owner, COUNT(*) FROM session_owner GROUP BY owner")}


def waiting(conn: sqlite3.Connection) -> list[dict]:
    """The folders whose sessions wait for you to choose an owner, most sessions first."""
    if not has_table(conn):
        return []
    return [dict(r) for r in conn.execute(
        "SELECT folder, remote, MAX(detail) why, COUNT(*) sessions, MAX(decided_at) at FROM session_owner "
        "WHERE owner = ? GROUP BY folder, remote ORDER BY sessions DESC, folder", (WAITING,))]


def not_personal_sql(alias: str = "") -> str:
    """A WHERE condition that keeps a sessions row only if it is Personal's (or no profiles exist yet): for every
    query whose rows may leave this computer. `alias`: the sessions table's alias in the query ("s")."""
    col = f"{alias}.id" if alias else "id"
    return (f"{col} NOT IN (SELECT session_id FROM session_owner WHERE owner != '{PERSONAL}')")


def foreign_ids(conn: sqlite3.Connection) -> set[str]:
    """The sessions that are not Personal's: a named profile's, or waiting."""
    if not has_table(conn):
        return set()
    return {r[0] for r in conn.execute("SELECT session_id FROM session_owner WHERE owner != ?", (PERSONAL,))}


class HubFilter:
    """Which folders stay off this profile's hub (cfg.off_hub), also by the repository's git remote and the
    repositories already seen in a named profile's folders: for what names folders or repositories to the hub (the
    hello). One per hello: remotes are looked up once per folder."""

    def __init__(self, cfg: Config):
        from ..core.profiles import RegistryError

        self.cfg = cfg
        self.known: dict[str, set[str]] = {}
        self._remotes: dict[str, str | None] = {}
        self.reg = cfg.profiles  # RegistryError goes up: nothing is named to the hub when profiles.toml is unreadable
        if self.reg is not None and cfg.db_path.exists():
            from ..storage.sqlite import connect

            conn = connect(cfg.db_path, readonly=True)
            try:
                self.known = known_remotes(conn)
            except sqlite3.Error:
                raise RegistryError("can't read which repositories named profiles own") from None
            finally:
                conn.close()

    def off(self, folder: str | None, remote: str | None = None, *, lookup: bool = True) -> bool:
        """`lookup`: find the folder's git remote when none is given (False: decide by the folder alone)."""
        if self.cfg.is_excluded(folder):
            return True
        if self.reg is None:
            return self.cfg.owner != PERSONAL
        if remote is None and folder and lookup:
            if folder not in self._remotes:
                from .routing import git_info

                info = git_info(folder)
                self._remotes[folder] = info[1] if info else None
            remote = self._remotes[folder]
        return route(self.reg, folder, remote, known=self.known).owner != self.cfg.owner
