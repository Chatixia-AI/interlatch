"""Projects on a hub: the ones set up there, the folders each computer added to them, project groups shared as one
project, the computers that send, and on a member's computer, what the hub made of its folders."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ..core.config import Config
from ..core.machine import local_machine
from ..core.paths import is_abs_path, normalize_project_path
from ..core.util import utcnow_iso
from ..ingestion.persistence import register_local
from ..projects.routing import MAX_FOLDERS, ProjectResolver, declared_projects, folders_of, resolver, under
from .protocol import PROJECT_NAME_RULE, SHARE_KV, HubError, clean_left

log = logging.getLogger("interlatch.hub")

REPOS_CACHE_S = 86400
FOLDERS_FILE = "hub-folders.json"  # on a spoke: what the hub made of its folders at the last hello
LEFT_KV = "hub-left:"  # + machine id: the projects here that computer left (spoke's [hub] left)


def left_of(conn, machine_id: str) -> list[str]:
    """The projects here another computer left, as it last reported them: it no longer shares there."""
    from ..storage.kv import kv_get

    if not machine_id:
        return []
    try:
        data = json.loads(kv_get(conn, LEFT_KV + machine_id) or "[]")
    except ValueError:
        return []
    return clean_left(data)


def hub_projects(conn, limit: int = 1000, only: list[str] | None = None) -> list[dict]:
    """The projects this hub files sessions under, busiest first, and the ones set up ahead of time: what another
    computer can add a folder to. `only`: just these project paths (a person limited to projects)."""
    from ..core.paths import project_name_for

    rows = {r[0]: {"path": r[0], "name": r[1], "sessions": r[2]} for r in conn.execute(
        "SELECT project_path, MAX(project_name), COUNT(*) FROM sessions WHERE project_path IS NOT NULL "
        "AND source != 'history' GROUP BY project_path ORDER BY COUNT(*) DESC LIMIT ?", (limit,))}
    for path in declared_projects(conn):
        rows.setdefault(path, {"path": path, "name": project_name_for(path), "sessions": 0})["set_up"] = True
    out = sorted(rows.values(), key=lambda p: -p["sessions"])
    return [p for p in out if only is None or p["path"] in only]


def shared_projects(conn) -> list[dict]:
    """The projects set up on this hub, for the dashboard: where each is, who sees it, and what each computer filed
    there. Everyone with every project (admins included) is counted, not listed."""
    from ..core.paths import project_name_for
    from .people import listing

    everyone = listing(conn)
    out = []
    for r in conn.execute("SELECT path, created_at, created_by FROM hub_projects ORDER BY path").fetchall():
        path = r["path"]
        computers = [{"id": c["machine_id"], "name": c["name"], "sessions": c["n"], "this": c["this"],
                      "left": path in left_of(conn, c["machine_id"])} for c in conn.execute(
            "SELECT s.machine_id, m.name, m.role = 'this' AS this, COUNT(*) n FROM sessions s LEFT JOIN machines m "
            "ON m.id = s.machine_id WHERE s.project_path = ? AND s.source != 'history' GROUP BY s.machine_id "
            "ORDER BY n DESC", (path,))]
        folders = []
        for m in conn.execute("SELECT id, name FROM machines WHERE role = 'spoke' ORDER BY name"):
            left = path in left_of(conn, m["id"])
            folders += [{"computer": m["name"] or m["id"][:8], "folder": f, "left": left}
                        for f, proj in folders_of(conn, m["id"]).items() if proj == path]
        limited = [p for p in everyone if p["projects"] is not None]
        out.append({"path": path, "name": project_name_for(path), "set_up_at": r["created_at"],
                    "sessions": sum(c["sessions"] for c in computers), "computers": computers, "folders": folders,
                    "people": [{"id": p["id"], "name": p["name"], "email": p["email"], "role": p["role"]}
                               for p in limited if path in p["projects"]],
                    "everyone": len(everyone) - len(limited)})
    return out


def project_folder(folder: str) -> str:
    """A folder as a project path: absolute, without a trailing slash, symlinks kept as the agents recorded them."""
    path = os.path.abspath(os.path.expanduser(str(folder or "").strip()))
    return path.rstrip("/") or "/"


def named_project_folder(cfg: Config, name: str) -> str:
    """The folder of a project a dedicated hub's admin makes by name (`[hub] dedicated`): <interlatch home>/projects/
    <name>, created here. Only a plain name: never a path, so it can't reach a folder outside that one."""
    name = " ".join(str(name or "").split())
    if (not name or len(name) > 80 or name.startswith(".") or any(c in name for c in "/\\")
            or any(ord(c) < 32 for c in name)):
        raise HubError(PROJECT_NAME_RULE)
    base = (cfg.home / "projects").resolve()
    folder = (base / name).resolve()
    if folder.parent != base:
        raise HubError(PROJECT_NAME_RULE)
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def add_project(cfg: Config, conn, folder: str, *, by: str | None = None) -> dict:
    """Set up a project on this hub ahead of time: `folder`, on this computer, and everything below it. This
    computer's sessions there are filed under it at once (their knowledge moves along), and other computers can add
    folders to it before anything was sent. Returns {path, name, moved}."""
    from ..core.paths import project_name_for
    from .people import LOCAL

    path = project_folder(folder)
    if under(os.path.expanduser("~").rstrip("/") or "/", path):  # /, /Users, the home folder itself
        raise HubError(f"{path} holds every project: pick the project's own folder")
    have = conn.execute("SELECT COUNT(*) FROM sessions WHERE source != 'history' AND (project_path = ? OR "
                        "machine_path = ? OR substr(project_path, 1, ?) = ? OR substr(machine_path, 1, ?) = ?)",
                        (path, path, len(path) + 1, path + "/", len(path) + 1, path + "/")).fetchone()[0]
    if not Path(path).is_dir() and not have:
        raise HubError(f"{path} is not a folder on this computer, and no session here ran in it")
    inside = [p for p in declared_projects(conn) if under(p, path) or under(path, p)]
    if path in inside:
        return {"path": path, "name": project_name_for(path), "moved": 0, "existed": True}
    if inside:
        raise HubError(f"{path} overlaps the project {inside[0]}: a folder belongs to one project")
    conn.execute("INSERT INTO hub_projects(path, created_at, created_by) VALUES (?, ?, ?)", (path, utcnow_iso(), by or LOCAL))
    conn.commit()
    resolver(cfg, conn, fresh=True)
    moved = refile(cfg, conn, local_machine(cfg)["id"], [path])
    conn.execute("UPDATE knowledge SET project_path = ?, project_name = ? WHERE session_id IS NULL AND "
                 "substr(project_path, 1, ?) = ?", (path, project_name_for(path), len(path) + 1, path + "/"))
    conn.commit()
    held = conn.execute("SELECT COUNT(*) FROM sessions WHERE project_path = ? AND source != 'history'", (path,)).fetchone()[0]
    return {"path": path, "name": project_name_for(path), "moved": moved, "sessions": held, "existed": False}


def remove_project(cfg: Config, conn, folder: str) -> dict:
    """Undo add_project: this computer's sessions in the folder are filed by their own folders again. What other
    computers sent stays where it was filed until they send it again; people keep the project in their lists."""
    path = project_folder(folder)
    if not conn.execute("SELECT 1 FROM hub_projects WHERE path = ?", (path,)).fetchone():
        raise HubError(f"{path} is not a project set up on this hub (`interlatch hub project list`)")
    conn.execute("DELETE FROM hub_projects WHERE path = ?", (path,))
    conn.commit()
    resolver(cfg, conn, fresh=True)
    return {"path": path, "moved": refile(cfg, conn, local_machine(cfg)["id"], [path])}


def refile(cfg: Config, conn, machine_id: str, folders) -> int:
    """File a computer's sessions in `folders` again after its folder mappings changed; their knowledge moves along.

    Both projects' knowledge bases are rebuilt at the next synthesis. Returns how many sessions moved.
    """
    from ..core.paths import project_name_for

    folders = list(folders)
    r = ProjectResolver(cfg, conn)
    moved, touched = 0, set()
    rows = conn.execute("SELECT id, project_path, machine_path, project_dir FROM sessions WHERE machine_id = ?",
                        (machine_id,)).fetchall()
    for row in rows:
        recorded = row["machine_path"] or row["project_path"]
        if not any(under(recorded, f) for f in folders):
            continue
        new = r.resolve(machine_id, recorded)
        if new == row["project_path"]:
            continue
        name = project_name_for(new, row["project_dir"])
        conn.execute("UPDATE sessions SET project_path = ?, project_name = ?, machine_path = ? WHERE id = ?",
                     (new, name, recorded if recorded != new else None, row["id"]))
        conn.execute("UPDATE knowledge SET project_path = ?, project_name = ? WHERE session_id = ? AND project_path IS ?",
                     (new, name, row["id"], row["project_path"]))
        touched.update(p for p in (row["project_path"], new) if p)
        moved += 1
    conn.executemany("UPDATE project_kb SET knowledge_max_id = 0 WHERE project_path = ?", [(p,) for p in touched])
    conn.commit()
    return moved


def computers_of(conn, person_id: int) -> list[str]:
    """The computers that joined with one of this person's invites."""
    return [r[0] for r in conn.execute("SELECT DISTINCT machine_id FROM people_tokens WHERE person_id = ? AND "
                                       "kind = 'computer' AND machine_id IS NOT NULL", (person_id,))]


def folders_report(conn, machine_id: str, folders: dict[str, str], repos: dict, remotes: dict[str, str]) -> dict:
    """Per folder: its project, this computer's sessions filed there, and the repositories inside it that their git
    remote files under another project (a remote the hub knows wins over a folder around it). A session counts for
    the most specific folder it ran in, so two folders of one project don't each show the project's total."""
    recorded = {project: [r[0] for r in conn.execute(
        "SELECT COALESCE(machine_path, project_path) FROM sessions WHERE machine_id = ? AND project_path = ? "
        "AND source != 'history'", (machine_id, project))] for project in set(folders.values())}
    out = {}
    for folder, project in folders.items():
        n = sum(max((f for f in folders if under(p, f)), key=len, default=None) == folder for p in recorded[project])
        other = sorted({(top, remotes[rem]) for top, rem in repos.values()
                        if under(top, folder) and remotes.get(rem) and remotes[rem] != project})
        out[folder] = {"project": project, "sessions": n, "overridden": [{"repo": t, "project": p} for t, p in other]}
    return out


class GroupRoutes:
    """Where this computer's groups that are shared on the hub (project_groups.hub_project) send its projects.

    Each project in such a group is a folder of that hub project, as if added with add-folder, so the hub files its
    sessions there, and whichever project joins the group later goes too (`active`). A folder a group shared before
    stays one (`kept`, from group_shares), so what it sent stays where it is on the hub, but nothing more goes from it:
    the project left the group, or the group stopped sharing. A project is sent through a group only if it is in that
    group itself, so one kept out of the group, or in another, stays here even inside a shared project's folder.
    """

    def __init__(self, active: dict[str, tuple[str, int]] | None = None, kept: dict[str, str] | None = None,
                 group_of: dict[str, int | None] | None = None):
        self.active = active or {}  # {project path: (hub project, group id)}
        self.kept = kept or {}  # {folder shared before: hub project}
        self.group_of = group_of or {}  # {project path: its group id}

    def __bool__(self) -> bool:
        return bool(self.active or self.kept)

    def folders(self) -> dict[str, str]:
        """{folder: hub project} for the hub to file by: a folder inside another of the same project adds nothing."""
        every = {**self.kept, **{f: v[0] for f, v in self.active.items()}}
        return {f: p for f, p in every.items() if not any(g != f and under(f, g) and every[g] == p for g in every)}

    def projects(self) -> set[str]:
        return {v[0] for v in self.active.values()}

    def decide(self, path: str | None, added: dict[str, str]) -> tuple[str, bool] | None:
        """(hub project, whether it goes) when a group's folder is the most specific one `path` is in, and no folder
        added by hand (`added`, [hub] folders) is as specific; None otherwise."""
        keys = [k for k in (*self.active, *self.kept) if under(path, k)]
        if not keys:
            return None
        key = max(keys, key=len)
        if any(under(path, f) and len(f) >= len(key) for f in added):
            return None
        if key in self.active:
            project, gid = self.active[key]
            return project, self.group_of.get(path) == gid
        return self.kept[key], False

    def remember(self, cfg: Config) -> None:
        """Note the folders just sent to the hub, so they stay filed there after they leave their group."""
        if not self.active:
            return
        from ..storage.sqlite import connect

        conn = connect(cfg.db_path)
        try:
            conn.executemany(
                "INSERT INTO group_shares(path, hub_project, group_id, shared_at) VALUES (?, ?, ?, ?) ON CONFLICT(path) "
                "DO UPDATE SET hub_project = excluded.hub_project, group_id = excluded.group_id",
                [(f, project, gid, utcnow_iso()) for f, (project, gid) in self.active.items()])
            conn.commit()
        finally:
            conn.close()


def group_routes(cfg: Config, conn=None) -> GroupRoutes:
    """This computer's GroupRoutes, from its groups and what they shared before. Empty on a computer that doesn't
    send to a hub. Folders only: a path another computer recorded (host:/…) isn't one the hub files by."""
    if not cfg.is_spoke or (conn is None and not cfg.db_path.exists()):
        return GroupRoutes()
    from ..projects.groups import assign, groups
    from ..storage.sqlite import connect

    own = conn is None
    conn = conn or connect(cfg.db_path)
    try:
        linked = {g["id"]: g["hub_project"] for g in groups(conn) if g.get("hub_project")}
        paths = [r[0] for r in conn.execute("SELECT DISTINCT project_path FROM sessions WHERE project_path IS NOT NULL")]
        group_of = {p: gid for p, (gid, _) in assign(conn, paths).items()}
        active = {p: (linked[gid], gid) for p, gid in group_of.items() if gid in linked and is_abs_path(p)}
        kept = {r[0]: r[1] for r in conn.execute("SELECT path, hub_project FROM group_shares") if r[0] not in active}
    finally:
        if own:
            conn.close()
    return GroupRoutes(active, kept, group_of)


def sent_folders(cfg: Config, routes: GroupRoutes) -> dict[str, str]:
    """The folders this computer tells the hub to file by: the ones added by hand, then its groups', up to what the
    hub takes (MAX_FOLDERS)."""
    out = dict(cfg.hub_folders)
    for f, project in routes.folders().items():
        if len(out) >= MAX_FOLDERS:
            log.warning("more than %d folders to file on the hub: some of the groups' are left out", MAX_FOLDERS)
            break
        out.setdefault(f, project)
    return out


def destination(cfg: Config, path: str | None, remote: str | None, remotes: dict) -> str | None:
    """The hub project a session of this computer is filed under, as far as this computer can tell: the project the
    hub files its repository's remote under, else the project of the folder it was added to (the longest one)."""
    if remote and remotes.get(remote):
        return remotes[remote]
    folder = max((f for f in cfg.hub_folders if under(path, f)), key=len, default=None)
    return cfg.hub_folders[folder] if folder else None


def hub_project_of(cfg: Config, paths, conn=None) -> dict[str, dict]:
    """For a computer that sends to a hub: the hub project each of these projects here is in, {path: {path, name}},
    as destination() finds it, with the repositories whose remote the hub files somewhere as of the last push, and the
    hub's name for it. None for a project in none of the hub's projects, one this computer left, or an excluded one."""
    if not cfg.is_spoke:
        return {}
    seen = last_folders(cfg) or {}
    names = {p["path"]: p.get("name") for p in seen.get("projects") or [] if isinstance(p, dict) and p.get("path")}
    repos = {r["folder"]: project for project, rs in (seen.get("remotes") or {}).items() if isinstance(rs, list)
             for r in rs if isinstance(r, dict) and r.get("folder")}  # a repository's top folder: the hub project
    routes = group_routes(cfg, conn)
    out = {}
    for path in paths:
        if not path or cfg.off_hub(path):
            continue
        top = max((f for f in repos if under(path, f)), key=len, default=None)
        via = None if top else routes.decide(path, cfg.hub_folders)
        goes = repos[top] if top else (via[0] if via[1] else None) if via else destination(cfg, path, None, {})
        if goes and normalize_project_path(goes) not in cfg.hub_left:
            out[path] = {"path": goes, "name": names.get(goes) or Path(goes).name or goes}
    return out


def _record_folders(cfg: Config, hello: dict, repos: dict | None = None) -> None:
    """The spoke's note of what the hub made of its folders, for `interlatch hub folders` (an older hub sends none),
    with the hub's projects and the ones this computer's repositories go to by their git remote, for Devices."""
    if not isinstance(hello.get("folders"), dict):
        return
    remotes = hello.get("remotes") if isinstance(hello.get("remotes"), dict) else {}
    by_remote: dict[str, list[dict]] = {}
    for top, remote in sorted({tuple(v) for v in (repos or {}).values() if isinstance(v, list) and len(v) == 2}):
        if remote and isinstance(remotes.get(remote), str):
            by_remote.setdefault(remotes[remote], []).append({"remote": remote, "folder": top})
    projects = [{"path": p["path"], "name": p.get("name") or Path(p["path"]).name, "sessions": p.get("sessions") or 0}
                for p in hello.get("projects") or [] if isinstance(p, dict) and isinstance(p.get("path"), str)]
    scope = [p for p in hello["scope"] if isinstance(p, str)] if isinstance(hello.get("scope"), list) else None
    try:
        (cfg.home / FOLDERS_FILE).write_text(json.dumps({"at": utcnow_iso(), "folders": hello["folders"],
                                                          "moved": int(hello.get("moved") or 0), "hub": hello.get("hub"),
                                                          "projects": projects, "remotes": by_remote,
                                                          "scope": scope}))
    except OSError:
        pass


def last_folders(cfg: Config) -> dict | None:
    try:
        return json.loads((cfg.home / FOLDERS_FILE).read_text())
    except (OSError, ValueError):
        return None


def machines(conn, cfg: Config) -> list[dict]:
    """Every computer the hub knows, with its session counts and whose it is, this one first."""
    from ..storage.kv import kv_get

    register_local(conn, cfg)
    conn.commit()
    me = local_machine(cfg)["id"]
    counts = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT COALESCE(machine_id, ?), COUNT(*), MAX(ended_at) FROM sessions WHERE source != 'history' GROUP BY 1", (me,))}
    out = []
    for r in conn.execute("SELECT m.*, p.name AS person FROM machines m LEFT JOIN people p ON p.id = m.person_id "
                          "AND p.removed_at IS NULL ORDER BY m.role = 'this' DESC, m.last_seen DESC").fetchall():
        m = dict(r)
        m.pop("repos_json", None)
        m["folders"] = [] if m["id"] == me else [
            {"folder": f, "project": p, "name": Path(p).name or p} for f, p in sorted(folders_of(conn, m["id"]).items())]
        m["share"] = None if m["id"] == me else (kv_get(conn, SHARE_KV + m["id"]) or "everything")
        m["sessions"], m["last_session"] = counts.get(m["id"], (0, None))
        m["this"] = m["id"] == me
        out.append(m)
    return out
