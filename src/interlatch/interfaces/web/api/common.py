"""What several dashboard pages share: filters and periods, whose sessions are whose on a hub with people, a
session's list row, a lesson's details, and a runner for any analysis backend."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from ....analysis.runners.factory import make_runner
from ....core.sessions import analysis_reason_text
from ....core.util import loads, to_iso, utcnow

pass
import re

from ....knowledge.ladder import reason_text
from ..context import Context


def session_filters(q: dict) -> tuple[str, list]:
    where, params = ["1=1"], []
    if q.get("project"):
        where.append("project_path = ?")
        params.append(q["project"])
    if q.get("agent"):
        where.append("agent = ?")
        params.append(q["agent"])
    if q.get("since"):
        where.append("started_at >= ?")
        params.append(q["since"])
    if q.get("until"):
        where.append("started_at < ?")
        params.append(q["until"])
    return " AND ".join(where), params


def since_days_ago(days: str | None) -> str | None:
    if not days or days == "all":
        return None
    return to_iso(utcnow() - timedelta(days=int(days)))


def this_computer(ctx: Context) -> dict:
    """This computer, recorded in the machines table, which whose-session joins go through."""
    from ....core.machine import local_machine
    from ....ingestion.persistence import register_local

    here = local_machine(ctx.cfg)
    if not ctx.conn.execute("SELECT 1 FROM machines WHERE id = ?", (here["id"],)).fetchone():
        register_local(ctx.conn, ctx.cfg)
        ctx.conn.commit()
    return here


def who_labels(ctx: Context) -> dict | None:
    """{computer id: (who, key)} for labelling rows on a hub with people, None elsewhere; the hub's own sessions,
    whose machine_id may be empty, under None."""
    from ....sharing.people import has_people

    if not has_people(ctx.conn):
        return None
    here = this_computer(ctx)
    out = {r["id"]: (r["pname"] or r["name"] or r["id"][:8], f"p:{r['pid']}" if r["pid"] else f"m:{r['id']}")
           for r in ctx.conn.execute("SELECT m.id, m.name, p.id pid, p.name pname FROM machines m LEFT JOIN people p "
                                      "ON p.id = m.person_id AND p.removed_at IS NULL")}
    out[None] = out.get(here["id"]) or (here["name"], f"m:{here['id']}")
    return out


def label_who(ctx: Context, rows: list[dict], col: str = "machine_id") -> list[dict]:
    whos = who_labels(ctx)
    if whos is not None:
        for r in rows:
            mid = r.get(col)
            r["who"], r["who_key"] = whos.get(mid) or (whos[None] if not mid else (mid[:8], f"m:{mid}"))
    return rows


def who_clause(ctx: Context, key: str, col: str = "machine_id") -> tuple[str, list]:
    """SQL on sessions for "this person's or this computer's sessions" (a WHO_KEY); matches nothing if malformed."""
    if not WHO_KEY.fullmatch(key or ""):
        return "0", []
    here = this_computer(ctx)["id"]
    if key.startswith("p:"):
        return f"COALESCE({col}, ?) IN (SELECT id FROM machines WHERE person_id = ?)", [here, int(key[2:])]
    return f"COALESCE({col}, ?) = ?", [here, key[2:]]


def knowledge_item(ctx: Context, k: dict) -> dict:
    k["tags"] = loads(k.pop("tags_json", None), []) or []
    return lesson_details(k)


def lesson_details(k: dict) -> dict:
    from ....analysis.session import case_of
    from ....knowledge.learning import topics_for

    raw_case = loads(k.pop("case_json", None), k.get("case"))  # lesson material: a case file, principle, diagram
    k["case"] = case_of({"kind": k.get("kind"), "case": raw_case}) if isinstance(raw_case, dict) else None
    k["learning_topics"] = topics_for(k)
    if k.get("stage_reason"):
        k["stage_reason"] = reason_text(k["stage_reason"])
    if k.get("source") == "team":  # a teammate's lesson: the computers whose sessions stated it
        from ....sharing.pull import team_from

        k["team_from"] = team_from(k)
    return k


def session_rows(ctx: Context, where: str, params: list, order: str, limit: int, offset: int = 0) -> list[dict]:
    rows = []
    for r in ctx.conn.execute(
        f"SELECT {SESSION_LIST_COLS} FROM sessions WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, limit, offset]):
        d = dict(r)
        d["tags"] = loads(d.pop("tags_json", None), []) or []
        d["analysis_reason"] = analysis_reason_text(d.get("analysis_reason"))
        rows.append(d)
    return label_who(ctx, rows)


def runner_for(ctx: Context, backend: str):
    return make_runner(replace(ctx.cfg, analysis=replace(ctx.cfg.analysis, backend=backend)))


MAX_SELECTION = 1000  # sessions one "analyze selected" may cover

SESSION_LIST_COLS = (
    "id, source, agent, title, project_name, project_path, started_at, ended_at, duration_s, active_s, n_prompts, "
    "n_tool_calls, n_tool_errors, n_subagents, n_compactions, n_interrupts, lines_added, lines_removed, n_files, "
    "input_tokens + output_tokens + cache_read_tokens + cache_write_tokens AS tokens, est_cost_usd, primary_model, "
    "git_branch, outcome, sentiment, analysis_status, analysis_reason, tags_json, summary, source_present, peak_context, "
    "CASE WHEN screen_sig = files_sig THEN screen_verdict END screen_verdict, screen_topic, screen_reason, machine_id"
)
# whose a session is, on a hub with people: its computer's person, else that computer; keyed "p:<person id>" or
# "m:<computer id>" (the `who` filter). The hub's own sessions may have no machine_id. WHO_SELECT's one placeholder
# takes the hub computer's name, WHO_JOIN's its id.
WHO_SELECT = "p.id AS person_id, COALESCE(p.name, m.name, ?) AS who, COALESCE('p:' || p.id, 'm:' || m.id) AS who_key"
WHO_JOIN = ("LEFT JOIN machines m ON m.id = COALESCE(s.machine_id, ?) "
            "LEFT JOIN people p ON p.id = m.person_id AND p.removed_at IS NULL")
WHO_KEY = re.compile(r"p:\d{1,12}|m:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
