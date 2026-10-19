"""Weekly re-analysis, turned on per project or per group: the background worker refreshes each one once a week, on
the day set in Settings › Re-analysis ([synthesis] weekly_day, this computer's time zone), and only what changed since.

A project's refresh re-synthesizes its knowledge base when it has gained knowledge items since the last synthesis
(below [synthesis] min_new_items, which would refresh it by itself), and the worker then rebuilds its glossary as after
any synthesis. A group's refreshes each of its projects that way, then checks its Mindmap and Connections links again
(group_graph.analyze) when they differ from the ones last checked, or were never checked. A week with nothing new
costs no model call and still counts as that week's refresh. A computer off or asleep all that day catches up at its
next sync. Kept in kv: `weekly:project:<path>`, `weekly:group:<id>`.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta

from ..core.config import Config
from ..core.util import dumps, loads, parse_ts, to_iso, utcnow, utcnow_iso
from ..storage.kv import kv_get, kv_set, kv_transaction

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # [synthesis] weekly_day, Monday first as datetime.weekday()
KINDS = ("project", "group")
PREFIX = "weekly:"


def day_of(cfg: Config) -> str:
    day = str(getattr(cfg.synthesis, "weekly_day", "") or "").strip().lower()[:3]
    return day if day in DAYS else DAYS[0]


def _midnight(d: date) -> datetime:
    """The start of a day in this computer's time zone, as an aware datetime (right across a daylight-saving change)."""
    return datetime.combine(d, time()).astimezone()


def week_start(cfg: Config, now: datetime | None = None) -> datetime:
    """The start of this week's re-analysis day: the latest weekly_day at or before now, from midnight local time."""
    today = (now or utcnow()).astimezone().date()
    return _midnight(today - timedelta(days=(today.weekday() - DAYS.index(day_of(cfg))) % 7))


def _is_due(rec: dict, start: datetime) -> bool:
    """Not yet run since this week's day began. Never run: due once a re-analysis day comes that began on or after the
    day it was turned on (turned on on the day itself, it runs that day)."""
    last = parse_ts(rec.get("last_run"))
    if last:
        return last < start
    since = parse_ts(rec.get("since"))
    return since is None or start >= _midnight(since.astimezone().date())


def _key(kind: str, target) -> str:
    return f"{PREFIX}{kind}:{target}"


def _record(conn: sqlite3.Connection, kind: str, target) -> dict | None:
    rec = loads(kv_get(conn, _key(kind, target)), None)
    return rec if isinstance(rec, dict) else None


def _view(cfg: Config, rec: dict | None, now: datetime | None = None) -> dict:
    """What the page shows: on or off, when it last ran and what that did, and when it runs next (None: at the next
    background sync)."""
    if not rec:
        return {"on": False}
    start = week_start(cfg, now)
    nxt = None if _is_due(rec, start) else _midnight(start.date() + timedelta(days=7))
    return {"on": True, "since": rec.get("since"), "last_run": rec.get("last_run"), "result": rec.get("result"),
            "detail": rec.get("detail"), "next": to_iso(nxt)}


def status(conn: sqlite3.Connection, cfg: Config, kind: str, target) -> dict:
    return _view(cfg, _record(conn, kind, target))


def listing(conn: sqlite3.Connection, cfg: Config) -> list[tuple[str, str, dict]]:
    """(kind, target, status) of every weekly re-analysis that is on, projects first."""
    rows = conn.execute("SELECT key, value FROM kv WHERE key LIKE ? ORDER BY key", (PREFIX + "%",)).fetchall()
    out = []
    for key, value in rows:
        kind, _, target = key[len(PREFIX):].partition(":")
        rec = loads(value, None)
        if kind in KINDS and target and isinstance(rec, dict):
            out.append((kind, target, _view(cfg, rec)))
    return sorted(out, key=lambda x: (KINDS.index(x[0]), x[1]))


def turn(conn: sqlite3.Connection, cfg: Config, kind: str, target, on: bool) -> dict:
    """Turn a project's or group's weekly re-analysis on or off. Turned on, it first runs on the next re-analysis day,
    or at the next sync when that is today (free when nothing is new); turned off, it is forgotten."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    with kv_transaction(conn):
        if on:
            rec = _record(conn, kind, target) or {"since": utcnow_iso(), "last_run": None, "result": None}
            kv_set(conn, _key(kind, target), dumps(rec))
        else:
            conn.execute("DELETE FROM kv WHERE key = ?", (_key(kind, target),))
    return status(conn, cfg, kind, target)


def due(conn: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> list[tuple[str, str]]:
    """(kind, target) of each weekly re-analysis not yet run since this week's day began, groups that no longer exist
    left out (and dropped)."""
    from ..projects.groups import groups

    start = week_start(cfg, now)
    have = {str(g["id"]) for g in groups(conn)}
    out, gone = [], []
    for key, value in conn.execute("SELECT key, value FROM kv WHERE key LIKE ? ORDER BY key", (PREFIX + "%",)).fetchall():
        kind, _, target = key[len(PREFIX):].partition(":")
        rec = loads(value, None)
        if kind not in KINDS or not target or not isinstance(rec, dict):
            continue
        if kind == "group" and target not in have:
            gone.append(key)
            continue
        if _is_due(rec, start):
            out.append((kind, target))
    if gone:
        conn.executemany("DELETE FROM kv WHERE key = ?", [(k,) for k in gone])
        conn.commit()
    return out


def members(conn: sqlite3.Connection, kind: str, target: str) -> list[str]:
    """The projects a weekly re-analysis covers: the project itself, or the group's projects now."""
    from ..projects.groups import paths_in

    return [target] if kind == "project" else paths_in(conn, int(target))


def project_changed(conn: sqlite3.Connection, cfg: Config, path: str) -> bool:
    """The project has active knowledge items its knowledge base hasn't synthesized yet."""
    if cfg.is_excluded(path):
        return False
    kb = conn.execute("SELECT knowledge_max_id FROM project_kb WHERE project_path = ?", (path,)).fetchone()
    return conn.execute("SELECT 1 FROM knowledge WHERE project_path = ? AND status = 'active' AND id > ? LIMIT 1",
                        (path, (kb["knowledge_max_id"] or 0) if kb else 0)).fetchone() is not None


def graph_changed(conn: sqlite3.Connection, group_id: int, paths: list[str]) -> bool:
    """The group's Mindmap and Connections were never drawn, or its candidate links differ from the ones last checked."""
    from . import group_graph as gg

    rec = loads(kv_get(conn, f"group_graph:{group_id}"), None)
    if not isinstance(rec, dict):
        return bool(paths)
    _, pairs = gg.candidates(conn, paths)
    return gg._fingerprint(pairs) != rec.get("fingerprint")


def record(conn: sqlite3.Connection, kind: str, target: str, result: str, detail: dict | None = None) -> None:
    """This week's refresh is done: `result` is "refreshed", "unchanged" or "failed"; `detail` has how many knowledge
    bases it re-synthesized (`projects`), whether it checked a group's links again (`graph`), and what failed (`error`)."""
    with kv_transaction(conn):
        rec = _record(conn, kind, target)
        if rec is None:  # turned off while it ran
            return
        rec.update(last_run=utcnow_iso(), result=result, detail=detail)
        kv_set(conn, _key(kind, target), dumps(rec))
