"""Periodic (weekly) reviews: the analysis model writes an engineering review of a week's sessions."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, time, timedelta

from . import ladder
from .config import Config
from .i18n import tr
from .llm import Runner, make_runner, written_in
from .util import dumps, human_cost, human_count, human_duration, loads, one_line, to_iso, utcnow_iso

log = logging.getLogger("interlatch.reviews")

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "tldr", "summary", "themes", "accomplishments", "learnings", "open_threads", "friction", "suggestions"],
    "properties": {
        "headline": {"type": "string", "description": "The week in at most 12 words"},
        "tldr": {"type": "array", "items": {"type": "string"},
                 "description": "Exactly 3 bullets, at most 14 words each: what someone skimming must know"},
        "summary": {"type": "string", "description": "At most 3 plain sentences for anyone who wants more than the TL;DR"},
        "themes": {"type": "array", "description": "2-4 themes", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "detail"],
            "properties": {"title": {"type": "string", "description": "2-5 words"},
                           "detail": {"type": "string", "description": "One sentence, at most 25 words"},
                           "projects": {"type": "array", "items": {"type": "string"}}}}},
        "accomplishments": {"type": "array", "items": {"type": "string"}, "description": "At most 5, each at most 15 words"},
        "learnings": {"type": "array", "items": {"type": "string"},
                      "description": "The most valuable things learned; at most 4, each at most 15 words"},
        "open_threads": {"type": "array", "items": {"type": "string"}, "description": "At most 5, each at most 12 words"},
        "friction": {"type": "array", "items": {"type": "string"},
                     "description": "Recurring slowdowns worth fixing; at most 3, each at most 15 words"},
        "suggestions": {"type": "array", "items": {"type": "string"},
                        "description": "Concrete workflow improvements, e.g. a CLAUDE.md rule, a script, a skill; "
                                       "at most 3, each at most 20 words"},
    },
}

SYSTEM = """\
You write a developer's weekly engineering review from the Claude Code sessions they ran that week.

You receive the week's usage statistics, a digest of every session (title, project, outcome, summary, \
highlights, open threads, friction) and the knowledge extracted that week. The developer skims this on \
Monday morning next to charts of the same numbers, so never restate the statistics and keep every line \
short: fragments are fine, lead with the concrete thing (project, feature, bug), no filler or hedging, and \
respect the word limits. Cover what they worked on and achieved, the recurring themes, the most valuable \
learnings, what is left open, what kept slowing them down, and a few concrete, specific suggestions to work \
better with Claude Code (a CLAUDE.md rule, a script, a skill, a habit). Be specific and grounded in the \
sessions; no generic productivity advice. Write in English; keep project names and identifiers verbatim."""
WRITE_ENGLISH = "Write in English; keep project names and identifiers verbatim."


def week_bounds(key: str | None = None) -> tuple[str, datetime, datetime]:
    """ISO week key ('2026-W39', 'current', 'last' or None=last completed) -> (key, local start, local end)."""
    today = date.today()
    if key in (None, "", "last"):
        ref = today - timedelta(days=7)
    elif key == "current":
        ref = today
    else:
        year, week = key.upper().split("-W")
        ref = date.fromisocalendar(int(year), int(week), 1)
    monday = ref - timedelta(days=ref.isoweekday() - 1)
    start = datetime.combine(monday, time.min).astimezone()
    end = start + timedelta(days=7)
    iso = monday.isocalendar()
    return f"{iso.year}-W{iso.week:02d}", start, end


def _stats(conn, since: str, until: str) -> dict:
    r = conn.execute(
        "SELECT COUNT(*) sessions, COALESCE(SUM(n_prompts),0) prompts, COALESCE(SUM(active_s),0) active_s, "
        "COALESCE(SUM(n_tool_calls),0) tool_calls, COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens),0) tokens, "
        "COALESCE(SUM(est_cost_usd),0) cost, COALESCE(SUM(lines_added),0) lines_added, COUNT(DISTINCT project_path) projects "
        "FROM sessions WHERE source != 'history' AND started_at >= ? AND started_at < ?", (since, until)).fetchone()
    return dict(r)


def week_glance(conn, since: str, until: str) -> dict:
    """The numbers a review is drawn with: active time per local day, top projects, outcomes and new knowledge."""
    from .views import project_labels

    start = datetime.fromisoformat(since.replace("Z", "+00:00")).astimezone()
    daily = [0.0] * 7
    projects: dict[str, dict] = {}
    outcomes: dict[str, int] = {}
    labels = project_labels(conn)
    for r in conn.execute(
            "SELECT started_at, project_path, project_name, active_s, outcome FROM sessions "
            "WHERE source != 'history' AND started_at >= ? AND started_at < ?", (since, until)):
        day = (datetime.fromisoformat(r["started_at"].replace("Z", "+00:00")).astimezone().date() - start.date()).days
        daily[min(max(day, 0), 6)] += r["active_s"] or 0
        p = projects.setdefault(r["project_path"] or "", {
            "path": r["project_path"] or "", "label": labels.get(r["project_path"], r["project_name"] or tr("(no project)")),
            "active_s": 0.0, "sessions": 0})
        p["active_s"] += r["active_s"] or 0
        p["sessions"] += 1
        outcomes[r["outcome"] or "not analyzed"] = outcomes.get(r["outcome"] or "not analyzed", 0) + 1
    knowledge = {r["kind"]: r["n"] for r in conn.execute(
        "SELECT k.kind, COUNT(*) n FROM knowledge k JOIN sessions s ON s.id = k.session_id "
        "WHERE k.status = 'active' AND s.started_at >= ? AND s.started_at < ? GROUP BY k.kind", (since, until))}
    return {"daily": [round(v) for v in daily],
            "projects": sorted(projects.values(), key=lambda p: -p["active_s"])[:6],
            "outcomes": outcomes, "knowledge": knowledge}


def review_ready(conn, key: str | None = None) -> tuple[bool, str]:
    key, start, end = week_bounds(key)
    since, until = to_iso(start), to_iso(end)
    existing = conn.execute("SELECT created_at FROM reviews WHERE period = ?", (key,)).fetchone()
    if existing and (existing[0] or "") >= until:
        return False, tr("exists")  # a "so far" review written mid-week does not count as the week's review
    row = conn.execute(
        "SELECT SUM(CASE WHEN analysis_status = 'done' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN analysis_status IN ('pending','stale','running') THEN 1 ELSE 0 END) FROM sessions "
        "WHERE source != 'history' AND started_at >= ? AND started_at < ?", (since, until)).fetchone()
    done, waiting = row[0] or 0, row[1] or 0
    if done < 2:
        return False, tr("fewer than 2 analyzed sessions")
    if waiting:
        return False, tr("{n} sessions still waiting for analysis", n=waiting)
    return True, key


def generate_review(conn: sqlite3.Connection, cfg: Config, key: str | None = None, runner: Runner | None = None) -> dict:
    runner = runner or make_runner(cfg)
    key, start, end = week_bounds(key)
    since, until = to_iso(start), to_iso(end)
    stats = _stats(conn, since, until)
    sessions = conn.execute(
        "SELECT id, started_at, project_name, title, outcome, summary, highlights_json, open_threads_json, friction_json, "
        "first_prompt, active_s, analysis_status FROM sessions WHERE source != 'history' AND started_at >= ? AND started_at < ? "
        "ORDER BY started_at", (since, until)).fetchall()
    if not sessions:
        raise ValueError(f"no sessions in {key}")
    lines = []
    for s in sessions:
        head = f"- [{s['started_at'][:10]}] {s['project_name']} · {s['title']} · {s['outcome'] or 'not analyzed'} · {human_duration(s['active_s'])} active"
        lines.append(head)
        if s["summary"]:
            lines.append(f"  summary: {s['summary']}")
            for label, col in (("highlights", "highlights_json"), ("open", "open_threads_json")):
                items = loads(s[col], []) or []
                if items:
                    lines.append(f"  {label}: " + " | ".join(one_line(x, 200) for x in items[:6]))
            friction = loads(s["friction_json"], []) or []
            if friction:
                lines.append("  friction: " + " | ".join(one_line(f.get("note") or "", 160) for f in friction[:4]))
        else:
            lines.append(f"  first prompt: {one_line(s['first_prompt'] or '', 300)}")
    knowledge = conn.execute(
        "SELECT k.kind, k.title, k.project_name FROM knowledge k JOIN sessions s ON s.id = k.session_id "
        "WHERE k.status = 'active' AND s.started_at >= ? AND s.started_at < ? ORDER BY k.id", (since, until)).fetchall()
    stats_txt = (f"{stats['sessions']} sessions in {stats['projects']} projects, {stats['prompts']} prompts, "
                 f"{human_duration(stats['active_s'])} active, {human_count(stats['tool_calls'])} tool calls, "
                 f"{human_count(stats['tokens'])} tokens (est. {human_cost(stats['cost'])} API-equivalent), +{stats['lines_added']} lines")
    prompt = (
        f"<week key=\"{key}\" from=\"{start:%Y-%m-%d}\" to=\"{(end - timedelta(days=1)):%Y-%m-%d}\" />\n\n"
        f"<stats>\n{stats_txt}\n</stats>\n\n<sessions>\n" + "\n".join(lines) + "\n</sessions>\n\n"
        "<knowledge_extracted>\n" + "\n".join(f"- [{k['kind']}] {k['title']} ({k['project_name']})" for k in knowledge[:200])
        + "\n</knowledge_extracted>\n\nWrite the weekly review."
    )
    res = runner.run(prompt, REVIEW_SCHEMA, system=written_in(cfg, SYSTEM, WRITE_ENGLISH, "Keep project names verbatim."),
                     model=cfg.synthesis.model)
    data = normalize_review(res.data)
    data["overturned"] = [overturned_line(k, cfg.analysis.language) for k in ladder.overturned(conn, since, until)]
    markdown = render_review(key, start, end, data, stats)
    try:
        _store_review(conn, key, since, until, res, len(sessions), markdown, data, stats, len(prompt))
    except BaseException:
        conn.rollback()
        raise
    log.info("weekly review %s written (%d sessions, $%.3f)", key, len(sessions), res.cost_usd)
    return data


def _store_review(conn, key, since, until, res, n_sessions, markdown, data, stats, prompt_chars) -> None:
    conn.execute(
        "INSERT INTO reviews(period, start, end, created_at, model, n_sessions, markdown, review_json, stats_json) "
        "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(period) DO UPDATE SET created_at=excluded.created_at, model=excluded.model, "
        "n_sessions=excluded.n_sessions, markdown=excluded.markdown, review_json=excluded.review_json, stats_json=excluded.stats_json",
        (key, since, until, utcnow_iso(), res.model, n_sessions, markdown, dumps(data), dumps(stats)),
    )
    conn.execute(
        "INSERT INTO analyses(kind, target, started_at, finished_at, model, status, input_chars, chunks, cost_usd, duration_ms) "
        "VALUES ('review', ?, ?, ?, ?, 'done', ?, 1, ?, ?)",
        (key, utcnow_iso(), utcnow_iso(), res.model, prompt_chars, res.cost_usd, res.duration_ms),
    )
    conn.commit()


OVERTURNED_JA = {"wip": "仮", "provisional": "1 回のみ", "established": "定着", "canonical": "確定", "outdated": "古くなった",
                 "contradicted": "否定された", "duplicate": "重複"}


def overturned_line(k: dict, language: str = "en") -> str:
    """One overturned item: what it said, how trusted it was, and what replaced it (in the language the review is in)."""
    what = f"{k['title']}" + (f" ({k['project_name']})" if k.get("project_name") else "")
    reason = k.get("superseded_reason") or "outdated"
    if language == "ja":
        why = f"{OVERTURNED_JA.get(k['stage'], k['stage'])}（{OVERTURNED_JA.get(reason, reason)}）"
        return f"{what}：{why}" + (f" →「{k['successor_title']}」" if k.get("successor_title") else "")
    why = f"{k['stage']}, {reason}"
    return f"{what}: was {why}" + (f"; now \u201c{k['successor_title']}\u201d" if k.get("successor_title") else "")


def normalize_review(data: dict) -> dict:
    def strings(values) -> list[str]:
        if isinstance(values, str):
            values = [values]
        return [str(v).strip() for v in values if str(v).strip()] if isinstance(values, list) else []

    themes = []
    for t in data.get("themes") if isinstance(data.get("themes"), list) else []:
        if isinstance(t, str) and t.strip():
            themes.append({"title": t.strip(), "detail": "", "projects": []})
        elif isinstance(t, dict) and (t.get("title") or t.get("detail")):
            themes.append({"title": str(t.get("title") or ""), "detail": str(t.get("detail") or ""),
                           "projects": strings(t.get("projects"))})
    out = {"headline": str(data.get("headline") or "").strip(), "tldr": strings(data.get("tldr"))[:3],
           "summary": str(data.get("summary") or "").strip(), "themes": themes}
    for key in ("accomplishments", "learnings", "open_threads", "friction", "suggestions"):
        out[key] = strings(data.get(key))
    return out


def render_review(key: str, start: datetime, end: datetime, data: dict, stats: dict) -> str:
    out = [f"# Week {key}: {data.get('headline') or ''}".rstrip(": "), "",
           f"_{start:%b %d} – {(end - timedelta(days=1)):%b %d, %Y} · {stats['sessions']} sessions · "
           f"{human_duration(stats['active_s'])} active · {stats['prompts']} prompts · est. {human_cost(stats['cost'])}_", ""]
    if data.get("tldr"):
        out += ["**TL;DR**", ""] + [f"- {x}" for x in data["tldr"]] + [""]
    if data.get("summary"):
        out += [data["summary"].strip(), ""]
    if data.get("themes"):
        out += ["## Themes", ""]
        for t in data["themes"]:
            projects = f" _({', '.join(t.get('projects') or [])})_" if t.get("projects") else ""
            out.append(f"- **{t.get('title')}**{projects}: {t.get('detail')}")
        out.append("")
    for title, key_ in (("Accomplishments", "accomplishments"), ("Learnings", "learnings"), ("Open threads", "open_threads"),
                        ("Friction", "friction"), ("Suggestions", "suggestions"), ("Overturned", "overturned")):
        items = data.get(key_) or []
        if items:
            prefix = "- [ ] " if key_ == "open_threads" else "- "
            out += [f"## {title}", ""] + [prefix + str(i) for i in items] + [""]
    return "\n".join(out).rstrip() + "\n"
