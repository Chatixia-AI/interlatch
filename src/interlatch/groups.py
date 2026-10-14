"""Your own groups of projects, one level deep: a name, folder rules, and projects put in by hand.

A project's group is its hand pick when it has one (a pick can also keep it out of every group), else the group whose
folder rule is the longest one its path sits under. Picks are kept only where they differ from what the rules say,
so a project moved back where its folder puts it follows the rules again.

Groups only change how the dashboard lists projects: each project keeps its own sessions, knowledge base and hub
sharing, and groups stay in this dashboard's database (a hub never sends or receives them).
"""

from __future__ import annotations

import json
import sqlite3

from .util import utcnow_iso

NAME_MAX = 60


class GroupError(ValueError):
    """A change the person asked for that can't be made, said in their words."""


def under(path: str, folder: str) -> bool:
    """`path` is `folder` or inside it, by whole folder names (`/a/Aktio` is not under `/a/Akt`)."""
    return path == folder or path.startswith(folder + "/") or path.startswith(folder + "\\")


def clean_folder(folder: str) -> str | None:
    """A folder rule as stored: `~` spelled out, no trailing slash; None for an empty one or the whole disk."""
    from pathlib import Path

    f = str(folder or "").strip()
    if f.startswith("~"):
        f = str(Path(f).expanduser())
    f = f.rstrip("/\\")
    return f if f and f not in (".", "~") and not f.endswith(":") else None


def groups(conn: sqlite3.Connection) -> list[dict]:
    """Every group, A to Z: {id, name, folders, hub_project} (hub_project: the hub project it's shared as, or None)."""
    return [{"id": r["id"], "name": r["name"], "folders": json.loads(r["folders_json"] or "[]"), "hub_project": r["hub_project"]}
            for r in conn.execute("SELECT id, name, folders_json, hub_project FROM project_groups "
                                  "ORDER BY name COLLATE NOCASE, id")]


def share(conn: sqlite3.Connection, group_id: int, hub_project: str | None) -> None:
    """Share a group as one project on the hub this computer sends to, or (None) stop: from the next push, the
    group's projects go to that hub project, whichever join it later (hub.group_routes)."""
    if not conn.execute("SELECT 1 FROM project_groups WHERE id = ?", (group_id,)).fetchone():
        raise GroupError("That group no longer exists.")
    conn.execute("UPDATE project_groups SET hub_project = ? WHERE id = ?", (hub_project or None, group_id))
    conn.commit()


def _picks(conn: sqlite3.Connection) -> dict[str, int | None]:
    return {r["project_path"]: r["group_id"] for r in conn.execute("SELECT project_path, group_id FROM project_group_picks")}


def _by_rule(path: str, rules: list[tuple[str, int]]) -> int | None:
    return next((gid for folder, gid in rules if under(path, folder)), None)


def _rules(gs: list[dict]) -> list[tuple[str, int]]:
    return sorted(((f, g["id"]) for g in gs for f in g["folders"]), key=lambda r: -len(r[0]))  # most specific first


def assign(conn: sqlite3.Connection, paths) -> dict[str, tuple[int | None, str | None]]:
    """Each path's (group id, "hand" | "folder"): (None, "hand") when kept out of every group, (None, None) when no
    rule takes it in."""
    gs = groups(conn)
    ids = {g["id"] for g in gs}
    rules, picks = _rules(gs), _picks(conn)
    out = {}
    for p in paths:
        if p in picks and (picks[p] is None or picks[p] in ids):
            out[p] = (picks[p], "hand")
        else:
            gid = _by_rule(p, rules)
            out[p] = (gid, "folder" if gid is not None else None)
    return out


def _all_paths(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT project_path FROM sessions WHERE project_path IS NOT NULL "
                                       "UNION SELECT path FROM hub_projects")]  # a hub's projects with no sessions yet too


def paths_in(conn: sqlite3.Connection, group_id: int) -> list[str]:
    """The projects in a group now, for a filter by group."""
    return [p for p, (gid, _) in assign(conn, _all_paths(conn)).items() if gid == group_id]


def group_of(conn: sqlite3.Connection, path: str) -> dict | None:
    gid, how = assign(conn, [path])[path]
    g = next((g for g in groups(conn) if g["id"] == gid), None) if gid is not None else None
    return {"id": g["id"], "name": g["name"], "by": how} if g else None


def _pick(conn: sqlite3.Connection, path: str, group_id: int | None, rules: list[tuple[str, int]]) -> None:
    """Put `path` in `group_id` (None: in no group), keeping a pick only where the rules would say otherwise."""
    if _by_rule(path, rules) == group_id:
        conn.execute("DELETE FROM project_group_picks WHERE project_path = ?", (path,))
    else:
        conn.execute("INSERT INTO project_group_picks(project_path, group_id) VALUES (?, ?) "
                     "ON CONFLICT(project_path) DO UPDATE SET group_id = excluded.group_id", (path, group_id))


def move(conn: sqlite3.Connection, paths: list[str], group_id: int | None) -> None:
    """Put projects in a group by hand, or (None) in no group."""
    if group_id is not None and not conn.execute("SELECT 1 FROM project_groups WHERE id = ?", (group_id,)).fetchone():
        raise GroupError("That group no longer exists.")
    rules = _rules(groups(conn))
    for p in paths:
        if p:
            _pick(conn, p, group_id, rules)
    conn.commit()


def _check(conn: sqlite3.Connection, name: str, folders: list, group_id: int | None) -> tuple[str, list[str]]:
    name = " ".join(str(name or "").split())
    if not name:
        raise GroupError("Give the group a name.")
    if len(name) > NAME_MAX:
        raise GroupError("Keep the name to 60 characters or fewer.")
    clash = conn.execute("SELECT id FROM project_groups WHERE name = ? COLLATE NOCASE AND id IS NOT ?", (name, group_id)).fetchone()
    if clash:
        raise GroupError("There is already a group with that name.")
    if not isinstance(folders, list):
        raise GroupError("Folder rules must be a list of folders.")
    cleaned: list[str] = []
    for f in folders:
        c = clean_folder(f)
        if c is None:
            raise GroupError("A folder rule can't be empty or the whole disk.")
        if c not in cleaned:
            cleaned.append(c)
    return name, cleaned


def save(conn: sqlite3.Connection, group_id: int | None, name: str, folders: list, members: list[str] | None = None) -> int:
    """Create (group_id None) or change a group. `members`, when given, is the whole group as the person ticked it:
    ticked projects are put in it, and those it held that aren't ticked are put in no group."""
    if group_id is not None and not conn.execute("SELECT 1 FROM project_groups WHERE id = ?", (group_id,)).fetchone():
        raise GroupError("That group no longer exists.")
    name, folders = _check(conn, name, folders, group_id)
    if group_id is None:
        group_id = conn.execute("INSERT INTO project_groups(name, folders_json, created_at) VALUES (?, ?, ?)",
                                (name, json.dumps(folders), utcnow_iso())).lastrowid
    else:
        conn.execute("UPDATE project_groups SET name = ?, folders_json = ? WHERE id = ?", (name, json.dumps(folders), group_id))
    if members is not None:
        want = {str(p) for p in members if p}
        now = assign(conn, _all_paths(conn))  # with the new rules
        rules = _rules(groups(conn))
        for p in want:
            if now.get(p, (None, None))[0] != group_id:
                _pick(conn, p, group_id, rules)
        for p, (gid, _) in now.items():
            if gid == group_id and p not in want:
                _pick(conn, p, None, rules)
    _prune(conn)
    conn.commit()
    return group_id


def _prune(conn: sqlite3.Connection) -> None:
    """Drop the picks that new rules made the same as what the rules say."""
    rules = _rules(groups(conn))
    for p, gid in _picks(conn).items():
        if _by_rule(p, rules) == gid:
            conn.execute("DELETE FROM project_group_picks WHERE project_path = ?", (p,))


def delete(conn: sqlite3.Connection, group_id: int) -> None:
    """Remove a group: its projects go back to what the other groups' rules say."""
    conn.execute("DELETE FROM project_group_picks WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM project_groups WHERE id = ?", (group_id,))
    _prune(conn)
    conn.commit()
