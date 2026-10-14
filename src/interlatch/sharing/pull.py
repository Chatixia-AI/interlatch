"""Teammates' lessons coming back from the hub after a push, kept read-only in this archive (knowledge.source
'team') for the projects this computer works on."""

from __future__ import annotations

import json
from pathlib import Path

from ..core.config import Config
from ..core.machine import local_machine
from ..core.util import utcnow_iso
from .client import HubClient
from .projects import group_routes
from .protocol import TEAM_SOURCE

TEAM_FILE = "team-lessons.json"  # on a computer that shares knowledge: the team lessons it holds, as of the last pull


def pull_team_lessons(cfg: Config, client: HubClient, repos: dict) -> int:
    """Ask the hub for teammates' lessons in this computer's projects and keep them here, read-only (source 'team').
    Returns how many this computer holds. Nothing changes when the hub's answer is the one it already has."""
    from ..storage.sqlite import connect

    state = last_team(cfg) or {}
    left = sorted(cfg.hub_left)
    if state.get("left", []) != left:  # left or rejoined a project since: ask afresh
        state = {}
    if state.get("version") and cfg.db_path.exists():  # "unchanged" only counts if the lessons are still here
        conn = connect(cfg.db_path, readonly=True)
        try:
            held = conn.execute("SELECT COUNT(*) FROM knowledge WHERE source = ?", (TEAM_SOURCE,)).fetchone()[0]
        finally:
            conn.close()
        if held != state.get("lessons"):
            state = {}
    remotes = sorted({v[1] for v in repos.values() if isinstance(v, list) and len(v) == 2 and v[1]})
    data = client.request("POST", "/api/hub/lessons", body={
        "machine": local_machine(cfg)["id"], "remotes": remotes,
        "projects": sorted((set(cfg.hub_folders.values()) | group_routes(cfg).projects()) - set(left)), "left": left,
        "version": state.get("version")})
    if not data.get("team"):
        return 0
    if data.get("unchanged"):
        held = int(state.get("lessons") or 0)
    else:
        if isinstance(data.get("lessons"), list):  # a hub older than [hub] left sends them too
            data["lessons"] = [x for x in data["lessons"] if not isinstance(x, dict) or x.get("project") not in left]
        held = apply_team_lessons(cfg, data, repos)
        state = {"version": data.get("version"), "lessons": held, "hub": data.get("hub"), "left": left}
    try:  # "at" is when this computer last asked, whether or not the answer changed
        (cfg.home / TEAM_FILE).write_text(json.dumps({**state, "at": utcnow_iso()}))
    except OSError:
        pass
    return held


def team_from(row) -> list[str]:
    """The computers whose sessions stated a teammate's lesson (knowledge.source_ref of a 'team' item)."""
    try:
        ref = json.loads(row["source_ref"] or "{}")
    except (ValueError, TypeError, KeyError, IndexError):
        return []
    return [str(c) for c in ref.get("from") or []] if isinstance(ref, dict) else []


def last_team(cfg: Config) -> dict | None:
    try:
        return json.loads((cfg.home / TEAM_FILE).read_text())
    except (OSError, ValueError):
        return None


def apply_team_lessons(cfg: Config, data: dict, repos: dict) -> int:
    """Keep the hub's lessons in this computer's own database, each filed under the folder here that holds its place
    (a repository, or a hub project without one): where this computer's own sessions there were filed, else the clone
    of that repository with the most sessions, else a folder added to that hub project. Lessons with no folder here
    are skipped. A lesson the hub no longer sends is removed; one dismissed here stays dismissed. Returns how many are
    held."""
    from ..core.util import dumps
    from ..knowledge import ladder
    from ..storage.sqlite import connect

    places = data.get("places") if isinstance(data.get("places"), dict) else {}
    clones: dict[str, list[str]] = {}
    for cwd, v in repos.items():
        if isinstance(v, list) and len(v) == 2 and v[1]:
            clones.setdefault(v[1], []).append(cwd)
    added = {}
    for folder, project in sorted(cfg.hub_folders.items()):
        added.setdefault(project, folder)
    # a group shared as a hub project: its lessons go to the group's project with the shortest folder (its top, often)
    for folder, (project, _) in sorted(group_routes(cfg).active.items(), key=lambda kv: (len(kv[0]), kv[0])):
        added.setdefault(project, folder)
    conn = connect(cfg.db_path)
    try:
        def name_of(path: str) -> str:
            r = conn.execute("SELECT project_name FROM sessions WHERE project_path = ? AND project_name IS NOT NULL "
                             "LIMIT 1", (path,)).fetchone()
            return r[0] if r else (Path(path).name or path)

        def here(at: str, project: str | None) -> tuple[str, str] | None:
            info = places.get(at) if isinstance(places.get(at), dict) else {}
            mine = info.get("mine")
            if isinstance(mine, str):
                r = conn.execute("SELECT project_path, project_name FROM sessions WHERE id = ?", (mine,)).fetchone()
                if r and r[0]:
                    return r[0], r[1] or name_of(r[0])
            found = sorted(clones.get(info.get("remote"), [])) if isinstance(info.get("remote"), str) else []
            if found:
                marks = ",".join("?" * len(found))
                used = dict(conn.execute(f"SELECT project_path, COUNT(*) FROM sessions WHERE project_path IN ({marks}) "
                                         "GROUP BY project_path", found).fetchall())
                best = max(found, key=lambda c: used.get(c, 0))
                return best, name_of(best)
            for p in (info.get("project"), project):
                if isinstance(p, str) and p in added:
                    return added[p], name_of(added[p])
            return None

        where: dict[tuple, tuple[str, str] | None] = {}
        held: set[str] = set()
        changed: list[int] = []
        now = utcnow_iso()
        for k in data.get("lessons") or []:
            if not isinstance(k, dict) or not isinstance(k.get("id"), int) or not k.get("title") or not k.get("kind"):
                continue
            spot = (str(k.get("place") or ""), k.get("project") if isinstance(k.get("project"), str) else None)
            if spot not in where:
                where[spot] = here(*spot)
            if not where[spot]:
                continue
            path, name = where[spot]
            fp = f"{TEAM_SOURCE}:{k['id']}"
            held.add(fp)
            sessions = [str(s) for s in k.get("session_ids") or [] if isinstance(s, str)][:ladder.MAX_CONFIRMATIONS]
            ref = dumps({"lesson": k["id"], "hub": data.get("hub"), "from": [str(c) for c in k.get("computers") or []][:10],
                         "sessions": int(k.get("sessions") or len(sessions)), "language": k.get("language")})
            tags = k.get("tags") if isinstance(k.get("tags"), list) else None
            values = {"project_path": path, "project_name": name, "kind": str(k["kind"])[:40], "title": str(k["title"]),
                      "body": k.get("body") if isinstance(k.get("body"), str) else None,
                      "tags_json": dumps(tags) if tags else None, "scope": "project", "source": TEAM_SOURCE,
                      "agent": None, "source_ref": ref, "confirmed_json": dumps(sessions) if sessions else None,
                      "created_at": k.get("created_at") or now, "updated_at": k.get("updated_at") or now}
            have = conn.execute("SELECT id FROM knowledge WHERE fingerprint = ?", (fp,)).fetchone()
            if have:  # its status and pin are this computer's own choice: kept
                conn.execute("UPDATE knowledge SET " + ", ".join(f"{c} = ?" for c in values) + " WHERE id = ?",
                             [*values.values(), have[0]])
                changed.append(have[0])
            else:
                cols = [*values, "fingerprint", "session_id"]
                cur = conn.execute(f"INSERT INTO knowledge({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                   [*values.values(), fp, None])
                changed.append(cur.lastrowid)
        gone = [r[0] for r in conn.execute("SELECT id, fingerprint FROM knowledge WHERE source = ?", (TEAM_SOURCE,))
                if r[1] not in held]
        if gone:
            conn.execute(f"DELETE FROM knowledge WHERE id IN ({','.join('?' * len(gone))})", gone)
        ladder.refresh(conn, changed)
        conn.commit()
        return len(held)
    finally:
        conn.close()
