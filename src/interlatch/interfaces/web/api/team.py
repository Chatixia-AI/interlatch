"""A hub's team Home: what the team did lately, by project and by person, and its news."""

from __future__ import annotations

from datetime import timedelta

from ....core.util import to_iso, utcnow
from .common import this_computer

pass
from ....projects.labels import project_labels
from ....sharing import news
from ..context import Context
from .common import WHO_JOIN, WHO_SELECT


def hub_info(ctx: Context) -> dict | None:
    """This computer as a hub: the name its dashboard shows, whether it has people (then Home is the team's), its
    address, whether it takes knowledge only, and whether it is a dedicated hub (`[hub] dedicated`: a server for
    the team, whose dashboard leaves out a person's own computer). None on a computer that isn't a hub."""
    from ....core.machine import local_machine
    from ....sharing.identity import read_token
    from ....sharing.people import has_people

    if ctx.cfg.is_spoke or not read_token(ctx.cfg):
        return None
    return {"name": ctx.cfg.hub_name or local_machine(ctx.cfg)["name"], "team": has_people(ctx.conn),
            "address": ctx.cfg.hub_address or None, "knowledge_only": ctx.cfg.hub_accept == "knowledge",
            "dedicated": ctx.cfg.hub_dedicated}


def who_options(ctx: Context) -> dict:
    """Who the Sessions and Knowledge pages can be narrowed to on a hub with people: each person and each computer
    no one joined, with the sessions this viewer can see (through the request's connection)."""
    from ....sharing.people import has_people

    if not has_people(ctx.conn):
        return {"items": []}
    here = this_computer(ctx)
    return {"items": [{"key": r["who_key"], "name": r["who"], "person": r["person_id"] is not None, "sessions": r["n"]}
                      for r in ctx.conn.execute(
                          f"SELECT {WHO_SELECT}, COUNT(*) n FROM sessions s {WHO_JOIN} GROUP BY who_key ORDER BY n DESC, who", [here["name"], here["id"]]) if r["who_key"]]}


def team(ctx: Context, q: dict, *, scope: list[str] | None, admin: bool, viewer: dict | None = None) -> dict:
    """The team Home of a hub. Team projects are the ones set up on the hub and the ones other computers send
    sessions to; a project only this computer works on stays personal (Home › Activity shows it). Sessions and
    lessons are read through this request's connection, so someone limited to projects sees theirs only
    (access.py), and `scope` keeps the set-up projects to theirs as well. Who: the person whose computer ran the
    session, else that computer's name. People, computers and open invites: admins only. News: what other
    computers sent lately, and what `viewer` saw of it (news.py)."""
    from ....core.paths import project_name_for
    from ....projects.routing import declared_projects
    from ....sharing import people
    from ....sharing.projects import machines
    from ....sharing.protocol import SHARED_AGENTS

    c = ctx.conn
    days = q.get("days") if q.get("days") in TEAM_DAYS else TEAM_DAYS[0]
    since = to_iso(utcnow() - timedelta(days=int(days)))
    here = this_computer(ctx)
    paths = set(declared_projects(c)) | {r[0] for r in c.execute(
        "SELECT DISTINCT project_path FROM sessions WHERE project_path IS NOT NULL AND machine_id IS NOT NULL "
        "AND machine_id != ?", (here["id"],))}
    if scope is not None:
        paths &= set(scope)
    paths = sorted(paths)[:TEAM_MAX_PROJECTS]
    base = (f"s.project_path IN ({', '.join('?' for _ in paths) or 'NULL'}) AND s.source != 'history' "
            f"AND s.agent IN ({', '.join('?' for _ in SHARED_AGENTS)})")
    bp = [*paths, *SHARED_AGENTS]
    lesson = f"k.status = 'active' AND k.source = 'analysis' AND k.scope = 'project' AND k.kind != 'preference' AND {base}"
    who, join, wp = WHO_SELECT, WHO_JOIN, [here["name"], here["id"]]  # who's placeholder comes before the join's

    per = {r["project_path"]: dict(r) for r in c.execute(
        f"SELECT s.project_path, COUNT(*) sessions, SUM(s.started_at >= ?) recent, MAX(s.started_at) last "
        f"FROM sessions s WHERE {base} GROUP BY s.project_path", [since, *bp])}
    learned = dict(c.execute(f"SELECT s.project_path, COUNT(*) FROM knowledge k JOIN sessions s ON s.id = k.session_id "
                             f"WHERE {lesson} AND k.created_at >= ? GROUP BY s.project_path", [*bp, since]).fetchall())
    active: dict[str, list] = {}
    for r in c.execute(f"SELECT s.project_path, {who}, COUNT(*) n FROM sessions s {join} WHERE {base} "
                       f"AND s.started_at >= ? GROUP BY s.project_path, who_key ORDER BY n DESC", [*wp, *bp, since]):
        active.setdefault(r["project_path"], []).append({"key": r["who_key"], "who": r["who"], "sessions": r["n"]})
    labels = project_labels(c)
    projects = [{"path": path, "label": labels.get(path) or project_name_for(path),
                 "sessions": (per.get(path) or {}).get("sessions", 0), "recent": (per.get(path) or {}).get("recent") or 0,
                 "last": (per.get(path) or {}).get("last"), "lessons": learned.get(path, 0), "people": active.get(path, [])}
                for path in paths]
    projects.sort(key=lambda x: (x["recent"], x["last"] or ""), reverse=True)

    # who worked on what: each person's projects in the period, latest first. No counts per person: a tally of
    # sessions or lessons reads as a score, and a lesson count mostly counts what went wrong in someone's sessions
    # (docs/design/learning.md).
    by_who: dict[str, dict] = {}
    for r in c.execute(f"SELECT {who}, s.project_path, MAX(s.started_at) last FROM sessions s {join} WHERE {base} "
                       f"AND s.started_at >= ? GROUP BY who_key, s.project_path ORDER BY last DESC", [*wp, *bp, since]):
        x = by_who.setdefault(r["who_key"], {"who_key": r["who_key"], "who": r["who"], "last": r["last"], "projects": []})
        x["projects"].append({"path": r["project_path"], "label": labels.get(r["project_path"]) or project_name_for(r["project_path"])})
    everyone = sorted(by_who.values(), key=lambda x: x["last"] or "", reverse=True)[:100]
    lessons = [dict(r) for r in c.execute(
        f"SELECT k.id, k.kind, k.title, k.confidence, k.created_at, k.session_id, s.project_path, s.project_name, {who} "
        f"FROM knowledge k JOIN sessions s ON s.id = k.session_id {join} WHERE {lesson} "
        f"ORDER BY k.created_at DESC, k.id DESC LIMIT 12", [*wp, *bp])]
    recent = [dict(r) for r in c.execute(
        f"SELECT s.id, s.title, s.agent, s.source, s.project_name, s.project_path, s.started_at, s.ended_at, s.active_s, "
        f"s.n_prompts, s.outcome, s.analysis_status, {who} FROM sessions s {join} WHERE {base} "
        f"ORDER BY s.started_at DESC LIMIT 10", [*wp, *bp])]
    out = {"days": days, "hub": hub_info(ctx), "projects": projects, "who": everyone, "lessons": lessons,
           "recent": recent, "people": None, "computers": None, "news": team_news(ctx, viewer, scope=scope, admin=admin),
           "totals": {"sessions": sum(x["recent"] for x in projects), "lessons": sum(learned.values()),
                      "people": len(everyone), "projects": sum(1 for x in projects if x["recent"]),
                      "team_projects": len(projects)}}
    if not admin:
        return out
    listing = people.listing(c)
    computers = machines(c, ctx.cfg)
    sent = {m["id"]: m["last_push"] for m in computers}
    out["computers"] = [{"id": m["id"], "name": m["name"] or m["id"][:8], "person": m["person"],
                         "this": m["this"], "share": m["share"], "sessions": m["sessions"], "last_session": m["last_session"],
                         "last_push": m["last_push"], "last_seen": m["last_seen"]} for m in computers]
    out["people"] = [{"id": x["id"], "name": x["name"], "role": x["role"], "projects": x["projects"],
                      "computers": len(x["computers"]), "browsers": len(x["browsers"]),
                      "last_push": max((sent.get(m["machine_id"]) or "" for m in x["computers"]), default="") or None,
                      "last_dashboard": max((b["last_used"] or "" for b in x["browsers"]), default="") or None,
                      "invites": [i["expires_at"] for i in x["invites"]]} for x in listing]
    return out


def team_news(ctx: Context, viewer: dict | None, *, scope: list[str] | None, admin: bool, after: str | None = None) -> dict:
    """What other computers sent lately (news.py): the newest items, or with `after` those added to since; how
    many `viewer` hasn't seen; and `now`, which the page sends back once they saw it (mark_seen)."""
    from ....core.paths import project_name_for

    c = ctx.conn
    now = to_iso(utcnow())  # before reading: what is added meanwhile stays unseen
    labels = project_labels(c)
    items = news.items(c, scope=scope, admin=admin, after=after)
    for x in items:
        x["project"] = (labels.get(x["project_path"]) or project_name_for(x["project_path"])) if x["project_path"] else None
    return {"now": now, "seen": news.seen_at(c, viewer), "unseen": news.unseen(c, viewer, scope=scope, admin=admin),
            "items": items}


TEAM_DAYS = ("7", "30", "90")  # the team Home's periods, first the default
TEAM_MAX_PROJECTS = 500
