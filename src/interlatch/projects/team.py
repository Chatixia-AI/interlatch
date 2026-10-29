"""A project's page on a team hub (api.projects.project, `team`): the team's knowledge first, and who it came from.

A hub with people keeps lessons and session summaries from several people's computers. The page reads them for a
teammate: what the knowledge base says and whose sessions taught each part, what changed since this person last
looked, the lessons two people hit separately (side by side, as cases), who has worked on what, and, for someone new to
the project, where to start. All of it is read from the archive as the request's connection sees it (someone limited
to projects sees only theirs); no model writes any of it.

People are credited as the ones a lesson was learned from, never counted: no lessons, sessions or time per person
(per-person tallies turn into a scoreboard and give people a reason to hold sessions back).
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import timedelta

from ..core.util import loads, parse_ts, to_iso, utcnow
from ..knowledge.ladder import STAGE_RANK, confirmations
from ..storage.kv import kv_get, kv_set

LANE_WEEKS = 8  # weeks of sessions in "Who's working on it"
LANE_PEOPLE = 8  # people with a lane of their own; the rest share "Others"
NEW_SHOWN = 8  # new lessons listed under "Since your last visit"
CASES = 5  # lessons more than one person hit, shown side by side
START_ITEMS = 5  # entries in each "Start here" list
TOPICS = 6  # topics shown for each person
SOLO_MIN = 2  # sessions on a topic before "only one person has worked on it" is said
SOLO_SHOWN = 8
REPLACED_SHOWN = 50
REVISIT = timedelta(minutes=30)  # a gap this long starts a new visit: what came before it is "since your last visit"
START_KINDS = {"gotcha": "gotchas", "fix": "gotchas", "decision": "decisions", "command": "commands"}
VISIT_KV = "project-visit:"  # + viewer key + ":" + project path


def who_of(who: dict, machine_id: str | None) -> tuple[str, str]:
    """(name, key) of a session's computer, as api.common.label_who names it: its person's, else the computer's."""
    return who.get(machine_id) or (who[None] if not machine_id else (machine_id[:8], f"m:{machine_id}"))


def visit(conn: sqlite3.Connection, viewer: str, path: str) -> dict | None:
    """Record that `viewer` is looking at the project now, and return when they last looked before this visit:
    {"at": iso, "kid": the newest lesson id then}, None on a first visit. Pages opened within REVISIT of each other
    are one visit, so reloading doesn't empty "Since your last visit". The caller commits."""
    key = f"{VISIT_KV}{viewer}:{path}"
    now = utcnow()
    kid = conn.execute("SELECT COALESCE(MAX(id), 0) FROM knowledge WHERE project_path = ?", (path,)).fetchone()[0]
    try:
        st = json.loads(kv_get(conn, key) or "{}")
    except ValueError:
        st = {}
    seen = parse_ts(st.get("seen")) if isinstance(st, dict) else None
    if not seen:
        new = {"seen": to_iso(now), "kid": kid, "prev": None, "prev_kid": None}
    elif now - seen > REVISIT:
        new = {"seen": to_iso(now), "kid": kid, "prev": st["seen"], "prev_kid": st.get("kid")}
    else:
        new = {**st, "seen": to_iso(now), "kid": kid}
    kv_set(conn, key, json.dumps(new))
    return {"at": new["prev"], "kid": new["prev_kid"] or 0} if new["prev"] else None


def team_view(conn: sqlite3.Connection, path: str, who: dict, viewer: str, *, kb: dict | None, since: dict | None,
              prs: bool = True) -> dict:
    """The team's side of a project's page. `who`: api.common.who_labels; `viewer`: the key of who is looking (p:<id>,
    or m:<id> at the hub itself); `kb`: the project's project_kb row; `since`: visit()'s last visit; `prs`: count the
    pull requests sessions list (not for someone limited to projects, who isn't shown them)."""
    sessions = {}
    for r in conn.execute(
            "SELECT id, machine_id, started_at, ingested_at, active_s, outcome, outcome_note, analysis_status, "
            "COALESCE(llm_title, title) title, tags_json, work_types_json, prs_json FROM sessions "
            "WHERE project_path = ? AND source != 'history'", (path,)):
        d = dict(r)
        d["who"], d["key"] = who_of(who, d.pop("machine_id"))
        sessions[d["id"]] = d
    lessons = [dict(r) for r in conn.execute(
        "SELECT id, session_id, kind, title, body, stage, confirmed_json, basis, status, superseded_by, superseded_reason, "
        "superseded_at, created_at FROM knowledge WHERE project_path = ? AND status IN ('active', 'superseded') "
        "ORDER BY id DESC", (path,))]
    by_id = {k["id"]: k for k in lessons}

    def people(k: dict) -> list[dict]:  # whose sessions state the lesson, first finder first, each person once
        out, seen = [], set()
        for sid in confirmations(k):
            s = sessions.get(sid)
            if s and s["key"] not in seen:
                seen.add(s["key"])
                out.append({"who": s["who"], "key": s["key"]})
        return out

    learned = {k["id"]: people(k) for k in lessons}
    active = [k for k in lessons if k["status"] == "active"]
    brief = lambda k: {"id": k["id"], "kind": k["kind"], "title": k["title"], "learned_by": learned[k["id"]]}  # noqa: E731
    trusted = lambda k: (-STAGE_RANK.get(k["stage"] or "provisional", 1), -len(confirmations(k)), -k["id"])  # noqa: E731

    cases = []
    for k in sorted((k for k in active if len(learned[k["id"]]) > 1), key=trusted)[:CASES]:
        firsts = {}
        for sid in confirmations(k):  # each person's first session that states it
            s = sessions.get(sid)
            if s and s["key"] not in firsts:
                firsts[s["key"]] = {"who": s["who"], "key": s["key"], "session": {
                    "id": s["id"], "title": s["title"], "outcome": s["outcome"], "outcome_note": s["outcome_note"],
                    "started_at": s["started_at"]}}
        cases.append({**brief(k), "body": k["body"], "stage": k["stage"], "people": list(firsts.values())})

    news = None
    if since:
        cut = parse_ts(since["at"])
        fresh = [s for s in sessions.values() if (parse_ts(s["ingested_at"] or s["started_at"]) or cut) > cut]
        new = [k for k in active if k["id"] > (since["kid"] or 0)]
        moved = [k for k in lessons if k["status"] == "superseded" and (parse_ts(k["superseded_at"]) or cut) > cut]

        def replaced(k: dict) -> dict:
            by = by_id.get(k["superseded_by"])
            old = learned[k["id"]]
            return {**brief(k), "reason": k["superseded_reason"], "by": brief(by) if by else None,
                    "mine": any(p["key"] == viewer for p in old)}

        names = list(dict.fromkeys(s["who"] for s in sorted(fresh, key=lambda s: s["started_at"] or "", reverse=True)))
        news = {"at": since["at"], "sessions": len(fresh), "people": names,
                "lessons": [brief(k) for k in new[:NEW_SHOWN]], "lessons_total": len(new),
                "replaced": [replaced(k) for k in moved if k["superseded_reason"] in ("outdated", "contradicted")],
                "confirmed": [replaced(k) for k in moved if k["superseded_reason"] == "duplicate"][:NEW_SHOWN],
                "prs": len({u for s in fresh for u in _pr_urls(s["prs_json"])}) if prs else None}

    topics, solo, last = defaultdict(Counter), defaultdict(Counter), {}
    for s in sessions.values():
        tags = _tags(s["tags_json"]) or _tags(s["work_types_json"])
        topics[s["key"]].update(tags)
        for tag in tags:
            solo[tag][s["key"]] += 1
        last[s["key"]] = max(last.get(s["key"]) or "", s["started_at"] or "")
    names = {s["key"]: s["who"] for s in sessions.values()}
    crew = [{"who": names[key], "key": key, "last": last[key] or None,
             "topics": [tag for tag, _ in topics[key].most_common(TOPICS)]}
            for key in sorted(last, key=lambda key: last[key], reverse=True)]
    only = sorted(((tag, next(iter(c)), sum(c.values())) for tag, c in solo.items()
                   if len(c) == 1 and sum(c.values()) >= SOLO_MIN), key=lambda x: (-x[2], x[0]))
    solo_out = [{"topic": tag, "who": names[key], "key": key} for tag, key, _ in only[:SOLO_SHOWN]] if len(crew) > 1 else []

    start = {"gotchas": [], "decisions": [], "commands": []}
    for k in sorted((k for k in active if k["kind"] in START_KINDS), key=trusted):
        bucket = start[START_KINDS[k["kind"]]]
        if len(bucket) < START_ITEMS:
            bucket.append({**brief(k), "body": k["body"], "stage": k["stage"], "confirmations": len(confirmations(k))})

    from_ = to_iso(utcnow() - timedelta(weeks=LANE_WEEKS))
    lanes = defaultdict(list)
    for s in sessions.values():
        if (s["started_at"] or "") >= from_:
            lanes[s["key"]].append({"id": s["id"], "at": s["started_at"], "active_s": s["active_s"] or 0,
                                    "outcome": s["outcome"], "title": s["title"]})
    order = sorted(lanes, key=lambda key: (-len(lanes[key]), names[key]))
    lane_out = [{"who": names[key], "key": key, "sessions": sorted(lanes[key], key=lambda x: x["at"])} for key in order[:LANE_PEOPLE]]
    if len(order) > LANE_PEOPLE:
        lane_out.append({"who": None, "key": None,
                         "sessions": sorted((x for key in order[LANE_PEOPLE:] for x in lanes[key]), key=lambda x: x["at"])})

    cited = {i for sec in ((loads(kb["kb_json"], {}) or {}).get("sections") or [] if kb else []) if isinstance(sec, dict)
             for it in sec.get("items") or [] if isinstance(it, dict) for i in it.get("sources") or [] if isinstance(i, int)}
    shown = {k["id"] for k in active} | cited
    return {
        "viewer": {"key": viewer, "new": viewer not in last},
        "since": news,
        "kb_behind": sum(1 for k in active if k["id"] > ((kb or {}).get("knowledge_max_id") or 0)) if kb else len(active),
        "learned_by": {str(i): learned[i] for i in shown if learned.get(i)},
        "cases": cases,
        "people": crew,
        "solo": solo_out,
        "start": start,
        "lanes": {"from": from_, "weeks": LANE_WEEKS, "people": lane_out},
        "replaced": [{**brief(k), "reason": k["superseded_reason"], "at": k["superseded_at"],
                      "by": brief(by_id[k["superseded_by"]]) if k["superseded_by"] in by_id else None}
                     for k in sorted((k for k in lessons if k["status"] == "superseded"
                                      and k["superseded_reason"] in ("outdated", "contradicted")),
                                     key=lambda k: k["superseded_at"] or "", reverse=True)[:REPLACED_SHOWN]],
    }


def _tags(raw) -> list[str]:
    """A session's tags (or work types) as topics: trimmed, lower case, each once."""
    out = []
    for x in loads(raw, []) or []:
        tag = str(x).strip().lower()
        if tag and tag not in out:
            out.append(tag)
    return out


def _pr_urls(raw) -> list[str]:
    out = []
    for x in loads(raw, []) or []:
        url = x.get("url") if isinstance(x, dict) else x
        if isinstance(url, str) and url:
            out.append(url)
    return out
