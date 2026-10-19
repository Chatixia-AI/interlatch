"""Projects: the list, project groups, one project, and the sessions and files of a file or folder in it."""

from __future__ import annotations

import os
from datetime import date, timedelta
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
        "reanalyze": _reanalyze(ctx, "project", path),
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


WEEKLY = 26  # weeks of each project's active time on a group's page
GROUP_MAP_KINDS = {"component": "Project in the group", "store": "Data store", "external": "Service",
                   "interface": "Platform it runs on"}


def group(ctx: Context, group_id: int, here: bool = False, limited: bool = False) -> dict | None:
    """A group's page: its projects read as one, the way a project's page reads one (project()). Each project's share
    of the work, where the group's last sessions left things and what they left to a person, the work week by week, and
    each knowledge base as its project keeps it (no model writes anything for the group). here: the request comes from
    this computer, so each folder's git state and roadmap file are read too. None for a group that doesn't exist or
    whose projects have no sessions."""
    from concurrent.futures import ThreadPoolExecutor

    from ....knowledge.glossary import glossary_entries
    from ....projects import progress
    from ....projects.groups import groups, paths_in
    from ...exports.diagrams import for_kb
    from .status import _local_offset_modifier

    c = ctx.conn
    g = next((g for g in groups(c) if g["id"] == group_id), None)
    if not g:
        return None
    wanted = paths_in(c, group_id)
    if not wanted:
        return None
    marks = ",".join("?" * len(wanted))
    labels = project_labels(c)
    members = []
    for r in c.execute(
            "SELECT project_path, MAX(project_name) project_name, COUNT(*) sessions, SUM(n_prompts) prompts, "
            "SUM(active_s) active_s, SUM(est_cost_usd) cost, "
            "SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens) tokens, MIN(started_at) first, "
            f"MAX(started_at) last FROM sessions WHERE project_path IN ({marks}) GROUP BY project_path ORDER BY last DESC",
            wanted):
        d = dict(r)
        d["label"] = labels.get(d["project_path"], d["project_name"])
        d["knowledge"] = c.execute("SELECT COUNT(*) FROM knowledge WHERE project_path = ? AND status = 'active'",
                                   (d["project_path"],)).fetchone()[0]
        kb = c.execute("SELECT updated_at, n_items, kb_json FROM project_kb WHERE project_path = ?",
                       (d["project_path"],)).fetchone()
        data = (loads(kb["kb_json"], {}) or {}) if kb else {}
        d["kb"] = {"updated_at": kb["updated_at"], "n_items": kb["n_items"], "overview": data.get("overview") or "",
                   "tldr": [str(x) for x in data.get("tldr") or []][:5],
                   "sections": [{"title": s.get("title") or "", "n": len(s.get("items") or [])}
                                for s in data.get("sections") or [] if isinstance(s, dict) and s.get("items")]} if kb else None
        d["diagram"] = for_kb(kb["kb_json"]) if kb else None  # its own architecture sketch, where its KB drew one
        members.append(d)
    if not members:
        return None
    paths = [m["project_path"] for m in members]
    marks = ",".join("?" * len(paths))
    stats = c.execute(
        "SELECT COUNT(*) sessions, SUM(n_prompts) prompts, SUM(active_s) active_s, SUM(est_cost_usd) cost, "
        "SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens) tokens, SUM(lines_added) lines_added, "
        f"SUM(lines_removed) lines_removed, MIN(started_at) first, MAX(started_at) last FROM sessions "
        f"WHERE project_path IN ({marks})", paths).fetchone()
    asks = []  # each project's own last few: one busy project's sessions don't push the others' out
    for p in paths:
        asks += progress.asks(label_who(ctx, progress.recent(c, p)))
    asks.sort(key=lambda a: a["started_at"] or "", reverse=True)
    repos, roadmaps = [], []
    if here:  # the folders, and a roadmap in each, only on this computer; members in one repository read it once
        with ThreadPoolExecutor(max_workers=min(8, len(paths))) as pool:
            states = list(pool.map(progress.repo_state, paths))
        seen_roots, seen_files = set(), set()
        for p, r in zip(paths, states, strict=True):
            if r and r["root"] not in seen_roots:
                seen_roots.add(r["root"])
                repos.append({**r, "project_path": p})
            rm = progress.roadmap(p, r["root"] if r else None)
            if rm and rm["path"] not in seen_files:
                seen_files.add(rm["path"])
                roadmaps.append({**rm, "project_path": p})
    inside = set(paths)
    glossary = []
    for e in glossary_entries(c):
        uses = [u for u in e["usage"] if u["project_path"] in inside]
        if uses:
            glossary.append({"term": e["term"], "category": e["category"], "definition": e["definition"],
                             "context": uses[0]["context"], "projects": sorted({u["project_path"] for u in uses})})
    cited = sorted({i for m in members if m["diagram"] for x in m["diagram"]["nodes"] + m["diagram"]["edges"]
                    for i in x.get("sources") or []})
    diagram_sources = [dict(r) for r in c.execute(  # what the sketches cite, so each part links to its sources
        f"SELECT id, kind, title, project_name, session_id FROM knowledge WHERE id IN ({','.join('?' * len(cited))})",
        cited)] if cited else []
    # each project's active time week by week (the last WEEKLY weeks, oldest first), and the group's days for a year
    this_week = progress.week_of(to_iso(utcnow()))
    weeks = [(date.fromisoformat(this_week) - timedelta(weeks=WEEKLY - 1 - i)).isoformat() for i in range(WEEKLY)]
    at = {w: i for i, w in enumerate(weeks)}
    weekly = {p: [0.0] * WEEKLY for p in paths}
    for r in c.execute(f"SELECT project_path, started_at, active_s FROM sessions WHERE project_path IN ({marks}) "
                       "AND started_at >= ?", (*paths, to_iso(utcnow() - timedelta(weeks=WEEKLY + 1)))):
        i = at.get(progress.week_of(r["started_at"]) or "")
        if i is not None:
            weekly[r["project_path"]][i] += r["active_s"] or 0
    mod = _local_offset_modifier()
    daily = [dict(r) for r in c.execute(
        f"SELECT date(started_at, '{mod}') date, COUNT(*) sessions, SUM(active_s) active_s, SUM(n_prompts) prompts "
        f"FROM sessions WHERE project_path IN ({marks}) AND started_at >= ? GROUP BY date ORDER BY date",
        (*paths, to_iso(utcnow() - timedelta(days=372))))]
    return {
        "id": g["id"], "name": g["name"], "folders": g["folders"], "hub_project": g["hub_project"],
        "reanalyze": _reanalyze(ctx, "group", g["id"]),
        "weekly": {"weeks": weeks, "series": [{"project_path": p, "values": weekly[p]} for p in paths]},
        "daily": daily,
        "diagram_sources": diagram_sources,
        "stats": dict(stats),
        "members": members,
        "recent": label_who(ctx, progress.recent(c, paths, limit=progress.RECENT * 2)),
        "asks": asks,
        "timeline": _labelled(ctx, progress.timeline(c, paths, session_prs=not limited)),
        "repos": repos if here else None,
        "roadmaps": roadmaps if here else None,
        "artifacts": _artifact_summary(ctx, paths),
        "sessions": session_rows(ctx, f"project_path IN ({marks})", paths, "started_at DESC", 200),
        "knowledge": [knowledge_item(ctx, dict(r)) for r in c.execute(
            "SELECT k.*, s.title session_title FROM knowledge k LEFT JOIN sessions s ON s.id = k.session_id "
            f"WHERE k.project_path IN ({marks}) AND k.status = 'active' ORDER BY k.pinned DESC, k.id DESC LIMIT 300",
            paths)],
        "glossary": glossary,
        "files": [dict(r) for r in c.execute(
            "SELECT f.path, s.project_path, COUNT(*) sessions, SUM(f.edits + f.writes) edits, SUM(f.lines_added) added, "
            "SUM(f.lines_removed) removed FROM session_files f JOIN sessions s ON s.id = f.session_id "
            f"WHERE s.project_path IN ({marks}) AND (f.edits + f.writes) > 0 GROUP BY s.project_path, f.path "
            "ORDER BY edits DESC LIMIT 25", paths)],
    }


def _group_map(ctx: Context, group_id: int) -> tuple[str, dict] | None:
    """The group's name and its map (systems.group_map), or None for no such group or one with nothing on the map."""
    from ....projects import systems
    from ....projects.groups import groups, paths_in

    g = next((g for g in groups(ctx.conn) if g["id"] == group_id), None)
    paths = paths_in(ctx.conn, group_id) if g else []
    if not paths:
        return None
    labels = project_labels(ctx.conn)
    names = {r["project_path"]: labels.get(r["project_path"], r["project_name"]) for r in ctx.conn.execute(
        f"SELECT project_path, MAX(project_name) project_name FROM sessions WHERE project_path IN ({','.join('?' * len(paths))}) "
        "GROUP BY project_path", paths)}
    d = systems.group_map(systems.cached(ctx.conn, ctx.cfg), names)
    d.update(wrap=20, gap=11)  # project names run long (ai_quotation_processor); many lines cross the one gap
    return (g["name"], d) if any(n["kind"] == "component" for n in d["nodes"]) else None


def group_map(ctx: Context, group_id: int) -> dict | None:
    """A group's map for its page: the projects, the services and platforms they use and the links between them, laid
    out left to right with the links as arcs to the projects' left (exports.diagrams)."""
    from ...exports.diagrams import layout, side_links

    found = _group_map(ctx, group_id)
    if not found:
        return None
    d = found[1]
    lay = layout(d)
    arcs, reach = side_links(lay, d["links"])
    lay.update(arcs=arcs, x0=-reach, width=round(lay["width"] + reach, 1))  # room for the arcs left of the projects
    return {**d, "layout": lay, "read_manifests": bool(ctx.cfg.systems_read_manifests)}


def group_map_file(ctx: Context, group_id: int) -> tuple[str, str, bytes] | None:
    """A group's map as an .excalidraw file, to open and edit in Excalidraw."""
    import json
    import re

    from ...exports.diagrams import to_excalidraw

    found = _group_map(ctx, group_id)
    if not found:
        return None
    name, d = found
    scene = to_excalidraw(d, f"{name}: projects and the services they use", GROUP_MAP_KINDS)
    slug = re.sub(r"[^\w.-]+", "-", name).strip("-") or "group"
    return f"{slug}-map.excalidraw", "application/vnd.excalidraw+json", json.dumps(scene, ensure_ascii=False, indent=1).encode()


def group_graph(ctx: Context, group_id: int) -> dict | None:
    """A group's Mindmap and Connections (knowledge.group_graph): drawn only once someone has analyzed it."""
    from ....knowledge import group_graph as gg
    from ....projects.groups import groups, paths_in

    g = next((g for g in groups(ctx.conn) if g["id"] == group_id), None)
    paths = paths_in(ctx.conn, group_id) if g else []
    return {"name": g["name"], **gg.graph(ctx.conn, group_id, paths)} if paths else None


def action_group_graph(ctx: Context, group_id: int) -> bool | None:
    """Analyze a group's links in the background (one job per group). None: no such group, or one with no projects."""
    from ....knowledge import group_graph as gg
    from ....projects.groups import groups, paths_in
    from ....storage.sqlite import connect

    if not any(g["id"] == group_id for g in groups(ctx.conn)) or not paths_in(ctx.conn, group_id):
        return None

    def job(progress):
        conn = connect(ctx.cfg.db_path)
        try:
            rec = gg.analyze(ctx.cfg, conn, group_id, paths_in(conn, group_id),
                             concurrency=ctx.cfg.analysis.concurrency + 1, progress=progress)
        finally:
            conn.close()
        return f"kept {len(rec['links'])} of {len(rec['checked'])} links"

    return ctx.jobs.start(f"group-graph:{group_id}", job)


def _reanalyze(ctx: Context, kind: str, target) -> dict | None:
    """A project's or group's weekly re-analysis (knowledge.weekly). None where this computer's sessions go to a hub,
    which analyzes them."""
    from ....knowledge import weekly

    return None if ctx.cfg.sends_files else weekly.status(ctx.conn, ctx.cfg, kind, target)


def action_reanalyze(ctx: Context, kind: str, target, on: bool) -> dict | None:
    """Turn a project's or group's weekly re-analysis on or off. None: no such project or group, or this computer's
    sessions go to a hub."""
    from ....knowledge import weekly
    from ....projects.groups import groups

    if ctx.cfg.sends_files:
        return None
    if kind == "group":
        try:
            target = int(target)
        except (TypeError, ValueError):
            return None
        if not any(g["id"] == target for g in groups(ctx.conn)):
            return None
    elif kind != "project" or not isinstance(target, str) or target == GLOBAL or not ctx.conn.execute(
            "SELECT 1 FROM sessions WHERE project_path = ? LIMIT 1", (target,)).fetchone():
        return None
    return weekly.turn(ctx.conn, ctx.cfg, kind, target, on)


def reanalysis(ctx: Context) -> dict:
    """Settings › Re-analysis: the day weekly re-analyses run, each one that is on, and the projects and groups it can
    still be turned on for (most recent first). on_hub: this computer's sessions go to a hub, which analyzes them."""
    from ....knowledge import weekly
    from ....projects.groups import groups

    out = {"on_hub": ctx.cfg.sends_files, "day": weekly.day_of(ctx.cfg), "days": list(weekly.DAYS), "items": [],
           "choices": {"projects": [], "groups": []}}
    if ctx.cfg.sends_files:
        return out
    c = ctx.conn
    labels = project_labels(c)
    projects = {r["project_path"]: labels.get(r["project_path"], r["project_name"]) for r in c.execute(
        "SELECT project_path, MAX(project_name) project_name, MAX(started_at) last FROM sessions "
        "WHERE project_path IS NOT NULL AND project_path != '' GROUP BY project_path ORDER BY last DESC")}
    named = {str(g["id"]): g["name"] for g in groups(c)}
    for kind, target, st in weekly.listing(c, ctx.cfg):
        name = (projects if kind == "project" else named).get(target)
        if kind == "group" and name is None:
            continue  # a removed group's: the next run drops it
        out["items"].append({"kind": kind, "target": int(target) if kind == "group" else target,
                             "name": name or target.rstrip("/").rsplit("/", 1)[-1], **st})
    on = {(i["kind"], str(i["target"])) for i in out["items"]}
    out["choices"]["projects"] = [{"path": p, "name": n} for p, n in projects.items()
                                  if ("project", p) not in on and not ctx.cfg.is_excluded(p)]
    out["choices"]["groups"] = [{"id": int(i), "name": n} for i, n in named.items() if ("group", i) not in on]
    return out


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
