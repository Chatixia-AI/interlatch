"""Projects: the list, project groups, one project, and the sessions and files of a file or folder in it."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from ....analysis.runners.factory import BACKENDS, make_runner
from ....core.i18n import tr
from ....core.paths import abs_path_sql, clean_abs_path, is_abs_path, normalize_project_path, path_kind, path_variants
from ....core.util import loads, to_iso, utcnow
from .artifacts import _artifact_summary
from .common import knowledge_item, label_who, session_rows

pass
import subprocess

from ....knowledge.synthesize import GLOBAL
from ....projects.labels import project_labels
from ..app import log
from ..context import Context


def projects(ctx: Context, scope: list[str] | None = None) -> list[dict]:
    """Every project with sessions here, most recent first; on a hub, then the ones set up there that have none yet
    (only those in `scope`, for a person limited to some projects)."""
    from ....core.paths import project_name_for
    from ....projects.groups import assign
    from ....projects.routing import declared_projects
    from ....sharing.projects import hub_project_of

    labels = project_labels(ctx.conn)
    shared = set(declared_projects(ctx.conn))
    # per project: active time in each of the last 12 weeks (oldest first), outcomes and agents
    weekly: dict[str, list[float]] = {}
    for r in ctx.conn.execute(
            "SELECT project_path, CAST((julianday('now') - julianday(started_at)) / 7 AS INTEGER) wk, SUM(active_s) a "
            "FROM sessions WHERE started_at >= ? GROUP BY project_path, wk", (to_iso(utcnow() - timedelta(days=84)),)):
        if r["wk"] is not None and 0 <= r["wk"] < 12:
            weekly.setdefault(r["project_path"] or "", [0.0] * 12)[11 - r["wk"]] += r["a"] or 0
    outcomes: dict[str, dict] = {}
    for r in ctx.conn.execute("SELECT project_path, COALESCE(outcome, 'not analyzed') o, COUNT(*) n FROM sessions "
                               "WHERE source != 'history' GROUP BY 1, 2"):
        outcomes.setdefault(r["project_path"] or "", {})[r["o"]] = r["n"]
    agents: dict[str, dict] = {}
    for r in ctx.conn.execute("SELECT project_path, agent, COUNT(*) n FROM sessions GROUP BY 1, 2"):
        agents.setdefault(r["project_path"] or "", {})[r["agent"] or "claude"] = r["n"]
    rows = []
    for r in ctx.conn.execute(
        "SELECT project_path, project_name, COUNT(*) sessions, SUM(n_prompts) prompts, SUM(active_s) active_s, "
        "SUM(est_cost_usd) cost, SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens) tokens, "
        "MIN(started_at) first, MAX(started_at) last, SUM(CASE WHEN analysis_status='done' THEN 1 ELSE 0 END) analyzed "
        "FROM sessions GROUP BY project_path ORDER BY last DESC"):
        d = dict(r)
        d["label"] = labels.get(d["project_path"] or "", d["project_name"])
        d["knowledge"] = ctx.conn.execute(
            "SELECT COUNT(*) FROM knowledge WHERE project_path = ? AND status = 'active'", (d["project_path"],)).fetchone()[0]
        kb = ctx.conn.execute("SELECT updated_at, n_items FROM project_kb WHERE project_path = ?", (d["project_path"],)).fetchone()
        d["kb_updated"] = kb["updated_at"] if kb else None
        d["exists"] = bool(d["project_path"]) and Path(d["project_path"]).exists()
        key = d["project_path"] or ""
        d["weekly"], d["outcomes"], d["agents"] = weekly.get(key, [0.0] * 12), outcomes.get(key, {}), agents.get(key, {})
        d["shared"] = key in shared  # set up on this hub: its sessions and lessons go to the people given it
        rows.append(d)
    have = {d["project_path"] for d in rows}
    for path in sorted(shared - have, key=lambda p: project_name_for(p).lower()):  # set up, nothing filed yet
        if scope is None or path in scope:
            rows.append({"project_path": path, "project_name": project_name_for(path), "label": project_name_for(path),
                         "sessions": 0, "prompts": 0, "active_s": 0, "cost": 0, "tokens": 0, "first": None, "last": None,
                         "analyzed": 0, "knowledge": 0, "kb_updated": None, "exists": Path(path).exists(),
                         "weekly": [0.0] * 12, "outcomes": {}, "agents": {}, "shared": True})
    on_hub = hub_project_of(ctx.cfg, [d["project_path"] for d in rows], ctx.conn)  # a computer that sends to a hub
    for d in rows:  # the hub project it's in, by the hub's name for it
        d["hub"] = on_hub.get(d["project_path"])
    placed = assign(ctx.conn, [d["project_path"] for d in rows if d["project_path"]])
    for d in rows:  # the group it's listed under (groups.py), and whether by hand or by its folder
        d["group"], d["group_by"] = placed.get(d["project_path"], (None, None))
    return rows


def project_groups(ctx: Context) -> dict:
    """The groups; home: a rule typed as ~/… shows as it'll be kept; hub: on a computer that sends to a hub, the
    hub's projects as of the last push (which a group can be shared as, and the Projects lists show this computer's
    projects under), with the team's sessions in each and the hub's address."""
    from ....projects.groups import groups

    return {"groups": groups(ctx.conn), "home": str(Path.home()),
            "hub": _hub_choices(ctx) if ctx.cfg.is_spoke else None}


def _billed(ctx: Context) -> bool:
    """Analysis goes to a model provider's API with your key, which bills each call (not a local Ollama, and not a
    coding agent's own login)."""
    from ....analysis.runners.factory import AGENTS

    backend = ctx.cfg.analysis.backend
    return backend in BACKENDS and backend not in AGENTS and not make_runner(ctx.cfg).local()


def _hub_choices(ctx: Context) -> dict:
    from ....sharing.projects import last_folders

    seen = last_folders(ctx.cfg) or {}
    return {"name": seen.get("hub"), "url": ctx.cfg.hub_url or None, "share": ctx.cfg.hub_share,
            "projects": [{"path": p["path"], "name": p.get("name") or Path(p["path"]).name,
                          "sessions": p["sessions"] if isinstance(p.get("sessions"), int) else None}
                         for p in seen.get("projects") or [] if isinstance(p, dict) and p.get("path")]}


def action_project_groups(ctx: Context, verb: str, body: dict) -> tuple[dict, int]:
    """Make, change or remove a group of projects, or move projects into one (groups.py)."""
    from ....projects import groups

    def gid(v):
        try:
            return int(v) if v is not None and v != "" else None
        except (TypeError, ValueError):
            raise groups.GroupError("That group no longer exists.") from None

    try:
        if verb == "save":
            members = body.get("members")
            new = groups.save(ctx.conn, gid(body.get("id")), str(body.get("name") or ""), body.get("folders") or [],
                              [str(p) for p in members] if isinstance(members, list) else None)
            return {"ok": True, "id": new}, 200
        if verb == "delete":
            groups.delete(ctx.conn, gid(body.get("id")))
            return {"ok": True}, 200
        if verb == "share":  # shared as one project on the hub this computer sends to, from its next push
            project = str(body.get("hub_project") or "") or None
            if project and not ctx.cfg.is_spoke:
                return {"error": tr("This computer doesn't send to a hub.")}, 400
            if project and project not in {p["path"] for p in _hub_choices(ctx)["projects"]}:
                return {"error": tr("The hub has no such project, or you don't see it.")}, 400
            groups.share(ctx.conn, gid(body.get("id")), project)
            return {"ok": True}, 200
        paths = body.get("paths")
        if not isinstance(paths, list) or not paths:
            return {"error": tr("Pick a project to move.")}, 400
        groups.move(ctx.conn, [str(p) for p in paths], gid(body.get("group")))
        return {"ok": True}, 200
    except groups.GroupError as exc:
        return {"error": tr(str(exc))}, 400


def project(ctx: Context, path: str, here: bool = False, limited: bool = False) -> dict | None:
    """One project's page. here: the request comes from this computer, so its folder's git state and roadmap file
    are read too.
    limited: someone limited to some projects, who isn't shown the pull requests a session lists (as on its page)."""
    c = ctx.conn
    if path == GLOBAL:
        kb = c.execute("SELECT * FROM project_kb WHERE project_path = ?", (GLOBAL,)).fetchone()
        cited = sorted({i for sec in (loads(kb["kb_json"], {}) or {}).get("sections", []) if isinstance(sec, dict)
                        for it in sec.get("items") or [] if isinstance(it, dict)
                        for i in it.get("sources") or [] if isinstance(i, int)}) if kb else []
        knowledge = []
        for i in range(0, len(cited), 500):  # the sources the playbook cites, so each bullet links to them
            chunk = cited[i:i + 500]
            knowledge += [dict(r) for r in c.execute(
                f"SELECT id, kind, title, project_name, session_id, agent FROM knowledge WHERE id IN ({','.join('?' * len(chunk))})",
                chunk)]
        return {"project_path": GLOBAL, "label": tr("Global playbook"), "kb": dict(kb) if kb else None,
                "sessions": [], "knowledge": knowledge, "stats": {}}
    stats = c.execute(
        "SELECT project_name, COUNT(*) sessions, SUM(n_prompts) prompts, SUM(active_s) active_s, SUM(est_cost_usd) cost, "
        "SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens) tokens, SUM(lines_added) lines_added, "
        "SUM(lines_removed) lines_removed, MIN(started_at) first, MAX(started_at) last FROM sessions WHERE project_path = ?",
        (path,)).fetchone()
    if not stats or not stats["sessions"]:
        return None
    from ....knowledge.glossary import glossary_entries
    from ...exports.diagrams import for_kb
    kb = c.execute("SELECT * FROM project_kb WHERE project_path = ?", (path,)).fetchone()
    labels = project_labels(c)
    from ....projects import progress
    from ....projects.groups import group_of
    from ....projects.routing import declared_projects
    from ....sharing.projects import hub_project_of

    recent = label_who(ctx, progress.recent(c, path))  # on a hub with people, whose session each one is
    repo = progress.repo_state(path) if here else None  # the folder, and a roadmap in it, only on this computer
    return {
        "project_path": path,
        "shared": path in declared_projects(c),
        "hub": hub_project_of(ctx.cfg, [path], c).get(path),
        "group": group_of(c, path),
        "label": labels.get(path, stats["project_name"]),
        "stats": dict(stats),
        "kb": dict(kb) if kb else None,
        "diagram": for_kb(kb["kb_json"]) if kb else None,
        "artifacts": _artifact_summary(ctx, path),
        "recent": recent,
        "asks": progress.asks(recent),
        "timeline": _labelled(ctx, progress.timeline(c, path, session_prs=not limited)),
        "repo": repo,
        "roadmap": progress.roadmap(path, repo["root"] if repo else None) if here else None,
        "sessions": session_rows(ctx, "project_path = ?", [path], "started_at DESC", 200),
        "knowledge": [knowledge_item(ctx, dict(r)) for r in c.execute(
            "SELECT k.*, s.title session_title FROM knowledge k LEFT JOIN sessions s ON s.id = k.session_id "
            "WHERE k.project_path = ? AND k.status = 'active' ORDER BY k.pinned DESC, k.id DESC LIMIT 300", (path,))],
        "glossary": [{"term": g["term"], "category": g["category"], "definition": g["definition"],
                      "context": next((u["context"] for u in g["usage"] if u["project_path"] == path), "")}
                     for g in glossary_entries(c, project=path)],
        "files": [dict(r) for r in c.execute(
            "SELECT f.path, COUNT(*) sessions, SUM(f.edits + f.writes) edits, SUM(f.lines_added) added, SUM(f.lines_removed) removed "
            "FROM session_files f JOIN sessions s ON s.id = f.session_id WHERE s.project_path = ? AND (f.edits + f.writes) > 0 "
            "GROUP BY f.path ORDER BY edits DESC LIMIT 25", (path,))],
    }


def _labelled(ctx: Context, timeline: dict) -> dict:
    label_who(ctx, [x for w in timeline["weeks"] for x in w["items"]])  # all weeks at once: one look-up of who's who
    return timeline


def file_sessions(ctx: Context, path: str, limit: int = 50) -> dict:
    """The sessions that read or changed one file, newest first (the VS Code extension's per-file history)."""
    path = clean_abs_path(path)
    if not path:
        return {"path": path, "total": 0, "sessions": []}
    c = ctx.conn
    # most agents record absolute paths, on Windows with either slash and either case of drive letter (C:\x, c:/x);
    # Codex sometimes records them relative to the session's folder
    spelled = path_variants(path)
    where, params = [f"f.path IN ({','.join('?' * len(spelled))})"], spelled
    for (root,) in c.execute(f"SELECT DISTINCT project_path FROM sessions WHERE {abs_path_sql('project_path')}"):
        base = normalize_project_path(root).rstrip("/") + "/"
        if is_abs_path(root) and path.startswith(base):
            rel = path[len(base):]
            rels = [rel] if path_kind(path) == "posix" or "/" not in rel else [rel, rel.replace("/", "\\")]
            where.append(f"(s.project_path = ? AND f.path IN ({','.join('?' * len(rels))}))")
            params += [root, *rels]
    stats = {r["session_id"]: dict(r) for r in c.execute(
        "SELECT f.session_id, SUM(f.reads) reads, SUM(f.edits + f.writes) changes, SUM(f.lines_added) added, "
        "SUM(f.lines_removed) removed FROM session_files f JOIN sessions s ON s.id = f.session_id "
        f"WHERE {' OR '.join(where)} GROUP BY f.session_id", params)}
    ids = list(stats)[:5000]
    rows = session_rows(ctx, f"id IN ({','.join('?' * len(ids))})", ids, "started_at DESC", limit) if ids else []
    for r in rows:
        r["file"] = {k: stats[r["id"]][k] for k in ("reads", "changes", "added", "removed")}
    return {"path": path, "total": len(stats), "sessions": rows}


def folder_files(ctx: Context, root: str, limit: int = 200, existing: bool = False, ignored: bool = True) -> dict:
    """The files under a folder that sessions read or changed, most recently touched first (the extension's file list)."""
    root = clean_abs_path(root)
    if not root or root == "/" or (path_kind(root) == "drive" and len(root) == 3):  # a whole disk: C:/
        return {"root": root, "total": 0, "files": []}
    base, c = root.rstrip("/") + "/", ctx.conn
    cols = "f.path, f.session_id, f.edits + f.writes changes, s.started_at"
    # a range on the indexed path for each way it may be spelled (C:\x, c:/x, …; the character after the separator
    # sorts right after it: '0' after '/', ']' after '\\'), so a big archive isn't scanned
    rows = []
    for spelled in path_variants(root):
        sep = "\\" if path_kind(root) != "posix" and "\\" in spelled else "/"
        rows += [{**dict(r), "path": normalize_project_path(r["path"])} for r in c.execute(
            f"SELECT {cols} FROM session_files f JOIN sessions s ON s.id = f.session_id WHERE f.path >= ? AND f.path < ?",
            (spelled + sep, spelled + chr(ord(sep) + 1)))]
    # Codex sometimes records paths relative to the session's folder: a folder inside this one, or one containing it
    for (folder,) in c.execute(f"SELECT DISTINCT project_path FROM sessions WHERE {abs_path_sql('project_path')}"):
        folder_base = normalize_project_path(folder).rstrip("/") + "/"
        if not is_abs_path(folder) or (not folder_base.startswith(base) and not base.startswith(folder_base)):
            continue
        windows = path_kind(folder) != "posix"
        for r in c.execute(f"SELECT {cols} FROM session_files f JOIN sessions s ON s.id = f.session_id "
                           f"WHERE s.project_path = ? AND NOT {abs_path_sql('f.path')}", (folder,)):
            full = clean_abs_path(folder_base + (r["path"].replace("\\", "/") if windows else r["path"]))
            if full.startswith(base):
                rows.append({**dict(r), "path": full})
    files: dict[str, dict] = {}
    for r in rows:
        f = files.setdefault(r["path"], {"sessions": set(), "changed": set(), "last": ""})
        f["sessions"].add(r["session_id"])
        if r["changes"]:
            f["changed"].add(r["session_id"])
        f["last"] = max(f["last"], r["started_at"] or "")
    paths = sorted(files, key=lambda p: (files[p]["last"], len(files[p]["sessions"])), reverse=True)
    if existing:  # leave out files since deleted, and the scratch paths agents wrote along the way
        paths = [p for p in paths if os.path.isfile(p)]
    if not ignored:  # and what git ignores: screenshots, build output, caches
        hidden = git_ignored(root, paths)
        paths = [p for p in paths if p not in hidden]
    # the ids let a folder tree count each session once across the files beneath it
    return {"root": root, "total": len(paths), "files": [
        {"path": p, "rel": p[len(base):], "sessions": len(files[p]["sessions"]), "changed": len(files[p]["changed"]),
         "last": files[p]["last"], "session_ids": sorted(files[p]["sessions"]),
         "changed_ids": sorted(files[p]["changed"])} for p in paths[:limit]]}


def git_ignored(root: str, paths: list[str]) -> set[str]:
    """The paths git ignores in the repository holding root; none when it isn't one, or git fails."""
    if not paths:
        return set()
    try:
        # root comes from the request: a repository's own config may not run anything (fsmonitor, hooks)
        out = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", root,
                              "check-ignore", "-z", "--stdin"], input="\0".join(paths) + "\0",
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode not in (0, 1):  # 1 means nothing is ignored; 128 is not a repository, or a path outside it
        log.debug("git check-ignore in %s: %s", root, out.stderr.strip())
        return set()
    return {p for p in out.stdout.split("\0") if p}
