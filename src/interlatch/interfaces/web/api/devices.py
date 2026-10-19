"""Devices: this computer and the hub it sends to, the hub's projects it joined, left or shares to, what it shares,
and pushing now."""

from __future__ import annotations

import json
from pathlib import Path

from ....core.i18n import tr
from ....core.paths import abs_path_sql, is_abs_path, normalize_project_path
from ..context import Context
from .hub_admin import team_store_info


def devices(ctx: Context) -> dict:
    """This computer's role, the computers a hub hears from, and how the dashboard is reachable."""
    from ....core.machine import local_machine
    from ....sharing.identity import read_token
    from ....sharing.projects import last_folders, machines
    from ....sharing.pull import last_team
    from ....sharing.push import last_push, send_problem

    role = "spoke" if ctx.cfg.is_spoke else "hub" if read_token(ctx.cfg) else "single"
    recorded = (last_folders(ctx.cfg) or {}) if role == "spoke" else {}
    seen = recorded.get("folders") or {}
    folders = [{"folder": f, "project": p, "name": Path(p).name or p, "sessions": (seen.get(f) or {}).get("sessions"),
                "overridden": (seen.get(f) or {}).get("overridden") or []}
               for f, p in sorted(ctx.cfg.hub_folders.items())] if role == "spoke" else []
    return {
        "this": local_machine(ctx.cfg),
        "role": role,
        "hub_url": ctx.cfg.hub_url or None,
        "last_push": (pushed := last_push(ctx.cfg) if role == "spoke" else None),
        "push_problem": send_problem(pushed),
        "share": ctx.cfg.hub_share if role == "spoke" else None,
        "all_folders": ctx.cfg.hub_all_folders if role == "spoke" else None,
        "accept": ctx.cfg.hub_accept if role == "hub" else None,
        "team": last_team(ctx.cfg) if role == "spoke" else None,
        "store": team_store_info(ctx) if role != "spoke" else None,
        "folders": folders,
        "projects": _my_hub_projects(ctx, folders, recorded) if role == "spoke" else [],
        "hub_name": recorded.get("hub") if role == "spoke" else None,
        "machines": machines(ctx.conn, ctx.cfg),
        "path_map": ctx.cfg.hub_path_map,
        "allowed_hosts": ctx.cfg.server_allowed_hosts,
        "allowed_users": ctx.cfg.server_allowed_users,
        "port": ctx.cfg.server_port,
    }


def _my_hub_projects(ctx: Context, folders: list[dict], recorded: dict) -> list[dict]:
    """The hub's projects this computer is in: through a folder added to one, a repository whose git remote the hub
    files there (as of the last push), a group shared as it, or one it left. Its name as the hub gives it."""
    from ....projects.groups import groups

    names = {p["path"]: p.get("name") for p in recorded.get("projects") or [] if isinstance(p, dict) and p.get("path")}
    out: dict[str, dict] = {}

    def row(path: str) -> dict:  # [hub] left spells a Windows project one way (C:/x), the hub perhaps another
        return out.setdefault(normalize_project_path(path), {
            "path": path, "name": names.get(path) or Path(path).name or path, "folders": [], "repos": [], "groups": [],
            "left": normalize_project_path(path) in ctx.cfg.hub_left})

    for f in folders:
        row(f["project"])["folders"].append(f)
    for path, repos in (recorded.get("remotes") or {}).items():
        if isinstance(repos, list):
            row(path)["repos"] += [r for r in repos if isinstance(r, dict)]
    for g in groups(ctx.conn):
        if g["hub_project"]:
            row(g["hub_project"])["groups"].append({"id": g["id"], "name": g["name"]})
    for path in ctx.cfg.hub_left:
        row(path)
    return sorted(out.values(), key=lambda r: (r["left"], r["name"].lower()))


def hub_project_choices(ctx: Context) -> dict:
    """For Join a project: the hub's projects (asked now) and this computer's folders that have sessions."""
    from ....sharing.client import handshake
    from ....sharing.protocol import SHARED_AGENTS, HubError

    if not ctx.cfg.is_spoke:
        return {"error": tr("this computer has not joined a hub")}
    try:
        hello = handshake(ctx.cfg)
    except HubError as exc:
        return {"error": tr("Can't reach the hub: {error}", error=str(exc))}
    marks = ", ".join("?" for _ in SHARED_AGENTS)
    local = [{"path": r[0], "name": r[1] or Path(r[0]).name, "sessions": r[2]} for r in ctx.conn.execute(
        f"SELECT project_path, MAX(project_name), COUNT(*) FROM sessions WHERE {abs_path_sql('project_path')} "
        f"AND source NOT IN ('history', 'remote') AND agent IN ({marks}) GROUP BY project_path "
        "ORDER BY MAX(started_at) DESC LIMIT 300", SHARED_AGENTS)]
    projects = [{"path": p["path"], "name": p.get("name") or Path(p["path"]).name, "sessions": p.get("sessions") or 0}
                for p in hello.get("projects") or [] if isinstance(p, dict) and isinstance(p.get("path"), str)]
    return {"projects": projects, "folders": local, "hub": hello.get("hub")}


def action_hub_project(ctx: Context, verb: str, body: dict) -> tuple[dict, int]:
    """add: file a folder here under one of the hub's projects (joining it); remove: take a wrongly added folder
    back, and what it shared; leave and rejoin: stop or start sharing to a project, keeping what it has."""
    from ....sharing.client import add_folder, handshake, leave_project, rejoin_project, remove_folder
    from ....sharing.protocol import FolderError, HubError

    if not ctx.cfg.is_spoke:
        return {"error": tr("this computer has not joined a hub")}, 400
    out: dict = {"ok": True}
    try:
        if verb == "add":
            folder = str(body.get("folder") or "").strip()
            if not (folder.startswith("~") or is_abs_path(folder)):
                return {"error": tr("Give the folder's full path, such as /Users/you/code/app.")}, 400
            folder = normalize_project_path(str(Path(folder).expanduser().resolve()))
            hello = handshake(ctx.cfg)
            project = next((p for p in hello.get("projects") or [] if isinstance(p, dict)
                            and p.get("path") == body.get("project")), None)
            if not project:
                return {"error": tr("The hub has no such project, or you don't see it.")}, 400
            ctx.cfg, remote = add_folder(ctx.cfg, folder, project, hello)
            out.update(folder=folder, name=project.get("name") or Path(project["path"]).name, remote=remote)
        elif verb == "remove":
            ctx.cfg, out["taken"] = remove_folder(ctx.cfg, str(body.get("folder") or ""))
        else:
            path = str(body.get("project") or "")
            if not is_abs_path(path) or len(path) > 1024:
                return {"error": tr("Which project?")}, 400
            path = normalize_project_path(path)
            ctx.cfg = leave_project(ctx.cfg, path) if verb == "leave" else rejoin_project(ctx.cfg, path)
    except FolderError as exc:
        return {"error": exc.shown()}, 400
    except HubError as exc:
        return {"error": tr("Can't reach the hub: {error}", error=str(exc))}, 502
    out["pushing"] = action_push(ctx)  # the hub hears of it now, and the lessons that come back change with it
    return out, 200


def action_leave_hub(ctx: Context) -> dict:
    """Settings › Devices › Leave the hub (`interlatch hub leave`)."""
    from ....sharing.client import leave_hub

    if not ctx.cfg.is_spoke:
        return {"error": tr("this computer has not joined a hub")}
    url = ctx.cfg.hub_url
    ctx.cfg = leave_hub(ctx.cfg)
    return {"ok": True, "hub": url}


def action_rejoin_hub(ctx: Context, body: dict) -> tuple[dict, int]:
    """The hub warning's Join again: a new invite code for this computer takes the place of the token the hub turned
    away (`interlatch hub join <its address> --code`), for the same hub and the same sharing; then a push."""
    from ....core.config import load_config, set_config_value
    from ....sharing.client import redeem_invite
    from ....sharing.identity import write_token
    from ....sharing.protocol import HubError

    if not ctx.cfg.is_spoke:
        return {"error": tr("this computer has not joined a hub")}, 400
    code = str(body.get("code") or "").strip()
    if not code or len(code) > 200:
        return {"error": tr("paste the invite code the hub's admin gave you")}, 400
    try:
        got = redeem_invite(ctx.cfg, ctx.cfg.hub_url, code)
    except HubError as exc:
        return {"error": str(exc)}, 400
    write_token(ctx.cfg, got["token"])
    if got.get("share") == "knowledge" and not ctx.cfg.shares_knowledge:  # what `hub join` does: this hub takes no more
        set_config_value(ctx.cfg, "hub", "share", json.dumps("knowledge"))
    ctx.cfg = load_config(ctx.cfg.home)
    action_push(ctx)
    who = got.get("person") or {}
    return {"ok": True, "person": {"name": who.get("name"), "role": who.get("role")}, "hub": got.get("hub")}, 200


def shared_projects(ctx: Context) -> dict:
    """Settings › Devices › Shared projects: the projects set up on this hub (sharing.projects.add_project), and the
    ones that could be (this hub's projects that aren't, busiest first)."""
    from ....projects.routing import declared_projects, under
    from ....sharing.identity import read_token
    from ....sharing.projects import hub_projects, shared_projects

    shared = shared_projects(ctx.conn)
    set_up = declared_projects(ctx.conn)
    candidates = [p for p in hub_projects(ctx.conn, limit=300)
                  if not any(under(p["path"], d) or under(d, p["path"]) for d in set_up)][:200]
    return {"shared": shared, "candidates": candidates, "hub": bool(read_token(ctx.cfg)) and not ctx.cfg.is_spoke,
            "store": ctx.cfg.hub_store == "postgres", "dedicated": ctx.cfg.hub_dedicated}


def action_shared_project(ctx: Context, verb: str, body: dict, *, by: dict | None = None, here: bool = True) -> tuple[dict, int]:
    """Set up a project on this hub (its sessions and lessons then go to the team store and to the people given it), or
    stop: sharing.projects.add_project / remove_project, recorded in the people audit log. At the hub computer itself
    (`here`), any folder of it. Elsewhere, only on a dedicated hub and only for an admin (`by`; the handler checks): a
    new project by its name, never a folder (sharing.projects.named_project_folder), or removing one that is set up."""
    from ....sharing import people
    from ....sharing.projects import add_project, named_project_folder, remove_project
    from ....sharing.protocol import PROJECT_NAME_RULE, HubError

    try:
        if verb == "add" and (not here or (ctx.cfg.hub_dedicated and "name" in body)):
            folder = named_project_folder(ctx.cfg, str(body.get("name") or ""))
        else:
            folder = str(body.get("folder") or body.get("path") or "").strip()
            if not folder.startswith(("/", "~")):
                return {"error": tr("pick a project or type its folder")}, 400
        got = (add_project(ctx.cfg, ctx.conn, folder, by=people.actor_of(by)) if verb == "add"
               else remove_project(ctx.cfg, ctx.conn, folder))
    except HubError as exc:
        return {"error": tr(str(exc)) if str(exc) == PROJECT_NAME_RULE else str(exc)}, 400
    people.audit(ctx.conn, people.actor_of(by), "project-add" if verb == "add" else "project-remove", None,
                 path=got["path"])
    ctx.conn.commit()
    return {"ok": True, "result": got, **shared_projects(ctx)}, 200


def action_share_mode(ctx: Context, share: str) -> dict:
    """[hub] share on a computer that sends to a hub: its transcripts, or only what it learned."""
    from ....core.config import SHARE_MODES, load_config, set_config_value

    if not ctx.cfg.is_spoke:
        return {"error": tr("this computer has not joined a hub")}
    if share not in SHARE_MODES:
        return {"error": tr("unknown share mode {share!r}", share=share)}
    set_config_value(ctx.cfg, "hub", "share", json.dumps(share))
    ctx.cfg = load_config(ctx.cfg.home)
    return {"ok": True, "share": ctx.cfg.hub_share}


def action_share_folders(ctx: Context, all_folders) -> dict:
    """[hub] all_folders on a computer that shares knowledge: every folder's sessions, or only the hub's projects'."""
    from ....core.config import load_config, set_config_value

    if not ctx.cfg.is_spoke:
        return {"error": tr("this computer has not joined a hub")}
    if not isinstance(all_folders, bool):
        return {"error": tr("all_folders must be true or false")}
    set_config_value(ctx.cfg, "hub", "all_folders", "true" if all_folders else "false")
    ctx.cfg = load_config(ctx.cfg.home)
    return {"ok": True, "all_folders": ctx.cfg.hub_all_folders}


def action_accept(ctx: Context, accept: str, by: dict | None) -> tuple[dict, int]:
    """[hub] accept on the hub: transcripts too, or knowledge only from every computer. `by` (None: at the hub
    itself) goes in the audit."""
    from ....core.config import SHARE_MODES, load_config, set_config_value
    from ....sharing import people
    from ....sharing.identity import read_token

    if ctx.cfg.is_spoke or not read_token(ctx.cfg):
        return {"error": tr("this computer is not a hub")}, 400
    if accept not in SHARE_MODES:
        return {"error": tr("unknown share mode {share!r}", share=accept)}, 400
    set_config_value(ctx.cfg, "hub", "accept", json.dumps(accept))
    ctx.cfg = load_config(ctx.cfg.home)
    people.audit(ctx.conn, people.actor_of(by), "accept", None, accept=accept)
    ctx.conn.commit()
    return {"ok": True, "accept": ctx.cfg.hub_accept}, 200


def action_push(ctx: Context) -> bool:
    """Send to the hub now; a computer that shares knowledge also gets its teammates' lessons back."""
    from ....sharing.push import push

    return ctx.jobs.start("push", lambda progress: push(ctx.cfg, progress=progress).summary())
