"""Knowledge: lessons, learning, the glossary and themes, the map and systems, reviews, the hub's knowledge,
synthesis, suggestions and friction."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from ....application.search import search_knowledge
from ....core.i18n import tr
from ....core.util import loads, to_iso, utcnow
from ....knowledge.ladder import refresh as refresh_stage
from ....knowledge.synthesize import GLOBAL
from ....projects.labels import project_labels
from ....storage.sqlite import connect
from ..context import Context
from .common import label_who, lesson_details, session_rows, who_clause, who_labels


def knowledge(ctx: Context, q: dict) -> dict:
    rows = search_knowledge(ctx.conn, q.get("q") or None, project=q.get("project") or None, kind=q.get("kind") or None,
                            include_inactive=q.get("status") == "all", limit=min(int(q.get("limit") or 200), 1000),
                            sessions=who_clause(ctx, q["who"]) if q.get("who") else None,
                            source=q.get("source") or None, cases=q.get("cases") == "1",
                            lessons=q.get("lessons") == "1", offset=max(0, int(q.get("offset") or 0)))
    if who_labels(ctx) is not None and rows:  # each lesson's person, through the session it came from
        ids = list({r["session_id"] for r in rows if r.get("session_id")})
        machine = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            machine.update(ctx.conn.execute(f"SELECT id, machine_id FROM sessions WHERE id IN ({','.join('?' * len(chunk))})",
                                            chunk).fetchall())
        for r in rows:
            r["session_machine"] = machine.get(r.get("session_id"))
        label_who(ctx, [r for r in rows if r.get("session_id") in machine], "session_machine")
        for r in rows:
            r.pop("session_machine", None)
    counts = {r["kind"]: r["n"] for r in ctx.conn.execute(
        "SELECT kind, COUNT(*) n FROM knowledge WHERE status = 'active' GROUP BY kind")}
    sources = dict(ctx.conn.execute(
        "SELECT source, COUNT(*) FROM knowledge WHERE status = 'active' GROUP BY source").fetchall())
    from ....analysis.session import CASE_FILE_SQL

    cases = ctx.conn.execute(
        "SELECT COUNT(*) FROM knowledge WHERE status = 'active' AND " + CASE_FILE_SQL).fetchone()[0]
    return {"items": [lesson_details(r) for r in rows], "counts": counts, "sources": sources, "cases": cases}


def learning_interests(ctx: Context, viewer: dict | None, q: dict) -> dict:
    from ....core.machine import local_machine_id
    from ....knowledge.learning import interest_profile

    return interest_profile(ctx.conn, viewer_id=(viewer or {}).get("id"), machine_id=local_machine_id(ctx.cfg),
                            project=q.get("project") or "")


def learning_glance(ctx: Context, viewer: dict | None, q: dict) -> dict:
    """The Knowledge page's Learn band (learning.glance), without sending every lesson to the browser."""
    from ....knowledge.learning import glance

    rows = search_knowledge(ctx.conn, project=q.get("project") or None, lessons=True, limit=10000)
    return glance([lesson_details(r) for r in rows], learning_interests(ctx, viewer, q)["interests"])


def glossary(ctx: Context, q: dict) -> dict:
    from ....knowledge.glossary import CATEGORIES, glossary_entries

    items = glossary_entries(ctx.conn, query=q.get("q") or None, project=q.get("project") or None,
                             category=q.get("category") or None)
    counts = {r[0]: r[1] for r in ctx.conn.execute("SELECT category, COUNT(*) FROM glossary GROUP BY category")}
    total = ctx.conn.execute("SELECT COUNT(*) FROM glossary").fetchone()[0]
    return {"items": items, "counts": counts, "categories": CATEGORIES, "total": total}


def glossary_terms(ctx: Context) -> list[dict]:
    """Compact term list the UI uses to link terms wherever they appear."""
    return [{"term": r["term"], "aliases": loads(r["aliases_json"], []) or [], "definition": r["definition"],
             "category": r["category"]} for r in ctx.conn.execute("SELECT term, aliases_json, definition, category FROM glossary")]


def map(ctx: Context) -> dict:
    from ....knowledge.glossary import map_data

    return map_data(ctx.conn)


def systems(ctx: Context) -> dict:
    """The Systems map's top level: groups, systems and the links between them (without each system's parts)."""
    from ....projects.systems import cached, landscape

    data = landscape(cached(ctx.conn, ctx.cfg))
    labels = project_labels(ctx.conn)
    for s in data["systems"]:
        s["project"] = next((p for p in s["project_paths"] if p in labels), None)
    return data


def system(ctx: Context, key: str) -> dict | None:
    """One system's parts, connections and links, with the evidence for each."""
    from ....projects.systems import CHAT_SOURCES, cached, system

    out = system(cached(ctx.conn, ctx.cfg), key)
    if out:
        labels = project_labels(ctx.conn)
        out["project"] = next((p for p in out["project_paths"] if p in labels), None)
        out["recent"] = session_rows(
            ctx, f"project_path IN ({','.join('?' * len(out['project_paths']))}) AND source NOT IN ({','.join('?' * len(CHAT_SOURCES))})",
            [*out["project_paths"], *CHAT_SOURCES], "started_at DESC", 6) if out["project_paths"] else []
    return out


def diagram_file(ctx: Context, path: str) -> tuple[str, str, bytes] | None:
    """A project's architecture sketch as an .excalidraw file, to open and edit in Excalidraw."""
    from ....core.diagrams import normalize_diagram
    from ...exports.diagrams import to_excalidraw

    kb = ctx.conn.execute("SELECT project_name, kb_json FROM project_kb WHERE project_path = ?", (path,)).fetchone()
    d = normalize_diagram((loads(kb["kb_json"], {}) or {}).get("diagram")) if kb and path != GLOBAL else None
    if not d:
        return None
    name = project_labels(ctx.conn).get(path) or kb["project_name"] or Path(path).name
    scene = to_excalidraw(d, f"{name}: architecture")
    slug = re.sub(r"[^\w.-]+", "-", name).strip("-") or "project"
    return f"{slug}-architecture.excalidraw", "application/vnd.excalidraw+json", json.dumps(scene, ensure_ascii=False, indent=1).encode()


def action_themes(ctx: Context, force: bool) -> bool:
    from ....knowledge.glossary import build_themes

    def job(progress):
        results = build_themes(ctx.cfg, force=force, concurrency=ctx.cfg.analysis.concurrency + 1, progress=progress)
        done = sum(1 for v in results.values() if isinstance(v, int))
        failed = [f"{c}: {v}" for c, v in results.items() if not isinstance(v, int)]
        return f"themes for {done}/{len(results)} categories" + (f" ({'; '.join(failed)[:200]})" if failed else "")

    return ctx.jobs.start("themes", job)


def action_glossary(ctx: Context, path: str | None) -> bool:
    from ....knowledge.glossary import build_glossaries

    def job(progress):
        conn = connect(ctx.cfg.db_path)
        try:
            targets = [path] if path else [r[0] for r in conn.execute(
                "SELECT DISTINCT project_path FROM knowledge WHERE status = 'active' AND project_path IS NOT NULL")] + [GLOBAL]
        finally:
            conn.close()
        results = build_glossaries(ctx.cfg, targets, concurrency=ctx.cfg.analysis.concurrency + 1, progress=progress)
        built = sum(1 for v in results.values() if isinstance(v, int))
        return f"glossary rebuilt for {built}/{len(targets)} projects"

    return ctx.jobs.start(f"glossary:{path or 'all'}", job)


def reviews(ctx: Context) -> dict:
    from ....knowledge.reviews import _stats, normalize_review, review_ready, week_bounds, week_glance

    items = [dict(r) for r in ctx.conn.execute(
        "SELECT period, start, end, created_at, model, n_sessions, markdown, review_json, stats_json FROM reviews "
        "ORDER BY period DESC")]
    for r in items:
        r["stats"] = loads(r.pop("stats_json", None), {}) or {}
        r["review"] = normalize_review(loads(r.pop("review_json", None), {}) or {})
        if r["start"] and r["end"]:  # the charts come from the sessions table, so they follow later analysis
            r["glance"] = week_glance(ctx.conn, r["start"], r["end"])
            r["stats"] = _stats(ctx.conn, r["start"], r["end"])
            prev_start = to_iso(datetime.fromisoformat(r["start"].replace("Z", "+00:00")) - timedelta(days=7))
            r["previous"] = _stats(ctx.conn, prev_start, r["start"])
    ready, why = review_ready(ctx.conn)
    return {"items": items, "last_week": week_bounds(None)[0], "current_week": week_bounds("current")[0],
            "auto_ready": ready, "auto_note": why}


def knowledge_hub(ctx: Context) -> dict:
    """The Knowledge section's landing page: a glance at each of its tools."""
    from ....knowledge.reviews import _stats, normalize_review, week_glance

    counts = {r["kind"]: r["n"] for r in ctx.conn.execute(
        "SELECT kind, COUNT(*) n FROM knowledge WHERE status = 'active' GROUP BY kind")}
    week_ago = to_iso(utcnow() - timedelta(days=7))
    recent = [dict(r) for r in ctx.conn.execute(
        "SELECT id, kind, title, project_name FROM knowledge WHERE status = 'active' ORDER BY created_at DESC, id DESC LIMIT 4")]
    new_week = ctx.conn.execute(  # by when the session ran: a backfill imports old sessions' knowledge today
        "SELECT COUNT(*) FROM knowledge k JOIN sessions s ON s.id = k.session_id WHERE k.status = 'active' "
        "AND s.started_at >= ?", (week_ago,)).fetchone()[0]
    cats = {r[0] or "other": r[1] for r in ctx.conn.execute("SELECT category, COUNT(*) FROM glossary GROUP BY category")}
    top_terms = [dict(r) for r in ctx.conn.execute(
        "SELECT term, category, n_sessions FROM glossary ORDER BY n_sessions DESC, n_mentions DESC LIMIT 16")]
    map_projects = ctx.conn.execute("SELECT COUNT(DISTINCT project_path) FROM glossary_usage WHERE project_path != ?",
                                    (GLOBAL,)).fetchone()[0]
    themes = ctx.conn.execute("SELECT COUNT(*) FROM glossary_themes").fetchone()[0]
    row = ctx.conn.execute("SELECT period, start, end, n_sessions, review_json, stats_json FROM reviews "
                            "ORDER BY period DESC LIMIT 1").fetchone()
    review = None
    if row:
        data = normalize_review(loads(row["review_json"], {}) or {})
        review = {"period": row["period"], "start": row["start"], "end": row["end"], "headline": data["headline"],
                  "tldr": data["tldr"],
                  "stats": _stats(ctx.conn, row["start"], row["end"]) if row["start"] else loads(row["stats_json"], {}) or {},
                  "daily": week_glance(ctx.conn, row["start"], row["end"])["daily"] if row["start"] else []}
    from ....analysis.session import CASE_FILE_SQL

    cases = ctx.conn.execute(
        "SELECT COUNT(*) FROM knowledge WHERE status = 'active' AND " + CASE_FILE_SQL).fetchone()[0]
    return {"knowledge": {"total": sum(counts.values()), "counts": counts, "new_week": new_week, "recent": recent,
                          "cases": cases},
            "glossary": {"total": sum(cats.values()), "categories": cats, "top": top_terms},
            "map": {"projects": map_projects, "themes": themes},
            "review": review, "reviews": ctx.conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]}


def action_review(ctx: Context, week: str | None) -> bool:
    from ....knowledge.reviews import generate_review

    def job(progress):
        conn = connect(ctx.cfg.db_path)
        try:
            progress("writing weekly review…")
            generate_review(conn, ctx.cfg, week)
        finally:
            conn.rollback()
            conn.close()
        return "review written"

    return ctx.jobs.start(f"review:{week or 'last'}", job)


def action_synthesize(ctx: Context, path: str) -> bool:
    from ....knowledge.synthesize import synthesize_project

    def job(progress):
        conn = connect(ctx.cfg.db_path)
        try:
            progress("synthesizing…")
            synthesize_project(conn, ctx.cfg, path)
        finally:
            conn.rollback()
            conn.close()
        return "done"

    return ctx.jobs.start(f"synthesize:{path}", job)


def action_knowledge(ctx: Context, kid: int, body: dict) -> bool:
    sets, params = [], []
    if body.get("status") in ("active", "dismissed", "superseded"):
        sets.append("status = ?")
        params.append(body["status"])
        if body["status"] == "active":  # restored by hand: it no longer points at a successor
            sets.append("superseded_by = NULL, superseded_reason = NULL, superseded_at = NULL")
    if "pinned" in body:
        sets.append("pinned = ?")
        params.append(1 if body["pinned"] else 0)
    if not sets:
        return False
    ctx.conn.execute(f"UPDATE knowledge SET {', '.join(sets)}, updated_at = ? WHERE id = ?", [*params, to_iso(utcnow()), kid])
    refresh_stage(ctx.conn, [kid])  # a pin makes an item canonical; unpinning returns it to what its evidence earns
    ctx.conn.commit()
    return True


def suggestions(ctx: Context, q: dict) -> dict:
    from ....knowledge import suggest

    status = q.get("status") if q.get("status") in suggest.STATUSES else None
    rows = suggest.list_suggestions(ctx.conn, status=status, project=q.get("project") or None)
    limit = int(q["limit"]) if str(q.get("limit") or "").isdigit() else None  # the Home card needs only the top few
    return {"suggestions": [suggest.display(r) for r in (rows[:limit] if limit else rows)], "total": len(rows),
            "counts": suggest.counts(ctx.conn)}


def suggestion_preview(ctx: Context, sid: int, q: dict) -> tuple[dict, int]:
    from ....knowledge import suggest

    if suggest.get(ctx.conn, sid) is None:
        return {"ok": False, "error": tr("no such suggestion")}, 404
    return suggest.preview(ctx.conn, ctx.cfg, sid, (q.get("text") or "").strip() or None), 200


def action_suggestion(ctx: Context, sid: int, verb: str, body: dict) -> tuple[dict, int]:
    """apply / unapply / dismiss / done / edit / move. A refusal is a 400 carrying the reason; an unknown id a 404."""
    from ....knowledge import suggest

    if suggest.get(ctx.conn, sid) is None:
        return {"ok": False, "error": tr("no such suggestion")}, 404
    text = body.get("text")
    text = text if isinstance(text, str) and text.strip() else None
    if verb == "apply":
        got = suggest.apply(ctx.conn, ctx.cfg, sid, text)
    elif verb == "unapply":
        got = suggest.unapply(ctx.conn, ctx.cfg, sid)
    elif verb == "dismiss":
        got = suggest.dismiss(ctx.conn, sid, str(body.get("reason") or "") or None)
    elif verb == "done":
        got = suggest.mark_done(ctx.conn, sid)
    elif verb == "move":  # to the user-level file, or back to the project files
        got = suggest.move(ctx.conn, ctx.cfg, sid, str(body.get("to") or ""))
    else:  # edit: keep the text you changed, so a refresh does not replace it
        got = suggest.edit_text(ctx.conn, sid, text or "")
    return got, (200 if got.get("ok") else 400)


def friction(ctx: Context, q: dict) -> dict:
    from ....knowledge.friction import display, report

    days = q.get("days")
    days = int(days) if days and days.isdigit() and int(days) > 0 else None
    got = report(ctx.conn, days=days, project=q.get("project") or None, noise=q.get("noise") in ("1", "true"))
    return {**got, "causes": [display(c) for c in got["causes"]]}
