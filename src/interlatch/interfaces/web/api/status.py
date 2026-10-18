"""Home and the status bar: the overview, what each agent did, what Interlatch is doing (status), and the actions that
start work: sync, analyze, and the analysis queue."""

from __future__ import annotations

from datetime import timedelta

from .... import __version__
from ....analysis.runners.factory import BACKENDS, make_runner
from ....application.records import resolve_session_id
from ....core.i18n import tr
from ....core.sessions import analysis_reason_text
from ....core.util import loads, to_iso, utcnow
from .common import knowledge_item, session_filters, session_rows, since_days_ago
from .projects import _billed
from .settings import _maybe_check_daily, analysis_backends

pass
from datetime import datetime

from ....projects.labels import project_labels
from ....storage.kv import kv_get, kv_set
from ....storage.sqlite import connect
from ..assets import UI_BUILD
from ..context import Context
from .common import MAX_SELECTION


def overview(ctx: Context, q: dict, limited: bool = False) -> dict:
    c = ctx.conn
    days = q.get("days") or "90"
    since = since_days_ago(days)
    project = q.get("project") or None
    agent = q.get("agent") or None
    mod = _local_offset_modifier()
    sw, sp = session_filters({"project": project, "since": since, "agent": agent})
    totals = dict(c.execute(
        f"SELECT COUNT(*) sessions, COALESCE(SUM(n_prompts),0) prompts, COALESCE(SUM(n_tool_calls),0) tool_calls, "
        f"COALESCE(SUM(n_tool_errors),0) tool_errors, COALESCE(SUM(active_s),0) active_s, "
        f"COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens),0) tokens, "
        f"COALESCE(SUM(est_cost_usd),0) cost, COALESCE(SUM(lines_added),0) lines_added, "
        f"COALESCE(SUM(lines_removed),0) lines_removed, COUNT(DISTINCT project_path) projects, "
        f"SUM(CASE WHEN analysis_status='done' THEN 1 ELSE 0 END) analyzed, MIN(started_at) first_at "
        f"FROM sessions WHERE {sw}", sp).fetchone())
    kw = "status = 'active'" + (" AND project_path = ?" if project else "")
    kp = [project] if project else []
    if since:
        kw += " AND created_at >= ?"
        kp.append(since)
    totals["knowledge"] = c.execute(f"SELECT COUNT(*) FROM knowledge WHERE {kw}", kp).fetchone()[0]
    # previous period for deltas
    prev = None
    if since:
        prev_since = to_iso(utcnow() - timedelta(days=2 * int(days)))
        pw, pp = session_filters({"project": project, "since": prev_since, "until": since, "agent": agent})
        prev = dict(c.execute(
            f"SELECT COUNT(*) sessions, COALESCE(SUM(n_prompts),0) prompts, COALESCE(SUM(n_tool_calls),0) tool_calls, "
            f"COALESCE(SUM(active_s),0) active_s, COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens),0) tokens, "
            f"COALESCE(SUM(est_cost_usd),0) cost, COALESCE(SUM(lines_added),0) lines_added FROM sessions WHERE {pw}", pp).fetchone())
    # daily series (sessions by start day; tokens/cost by API-call day)
    daily: dict[str, dict] = {}
    for r in c.execute(
        f"SELECT date(started_at, '{mod}') d, COUNT(*) sessions, SUM(n_prompts) prompts, SUM(active_s) active_s, "
        f"SUM(n_tool_calls) tool_calls, SUM(lines_added) lines_added "
        f"FROM sessions WHERE {sw} AND started_at IS NOT NULL GROUP BY d", sp):
        daily.setdefault(r["d"], {}).update(sessions=r["sessions"], prompts=r["prompts"] or 0, active_s=r["active_s"] or 0,
                                            tool_calls=r["tool_calls"] or 0, lines_added=r["lines_added"] or 0)
    aw = ("a.ts IS NOT NULL" + (" AND a.ts >= ?" if since else "") + (" AND s.project_path = ?" if project else "")
          + (" AND s.agent = ?" if agent else ""))
    ap = ([since] if since else []) + ([project] if project else []) + ([agent] if agent else [])
    for r in c.execute(
        f"SELECT date(a.ts, '{mod}') d, SUM(a.input_tokens+a.output_tokens+a.cache_read_tokens+a.cache_write_tokens) tokens, "
        f"SUM(a.cost_usd) cost FROM api_calls a JOIN sessions s ON s.id = a.session_id WHERE {aw} GROUP BY d", ap):
        daily.setdefault(r["d"], {}).update(tokens=r["tokens"] or 0, cost=r["cost"] or 0)
    daily_list = [{"date": d, "sessions": v.get("sessions", 0), "prompts": v.get("prompts", 0),
                   "active_s": v.get("active_s", 0), "tokens": v.get("tokens", 0), "cost": v.get("cost", 0),
                   "tool_calls": v.get("tool_calls", 0), "lines_added": v.get("lines_added", 0)}
                  for d, v in sorted(daily.items()) if d]
    # calendar: up to a year, whatever the period filter (same project scope)
    cal_since = to_iso(utcnow() - timedelta(days=372))
    cw, cp = session_filters({"project": project, "since": cal_since, "agent": agent})
    calendar = [dict(r) for r in c.execute(
        f"SELECT date(started_at, '{mod}') date, COUNT(*) sessions, SUM(active_s) active_s, SUM(n_prompts) prompts "
        f"FROM sessions WHERE {cw} GROUP BY date ORDER BY date", cp)]
    # hour-of-week from human prompts
    ew = ("e.kind = 'prompt' AND e.agent_id = ''" + (" AND e.ts >= ?" if since else "")
          + (" AND s.project_path = ?" if project else "") + (" AND s.agent = ?" if agent else ""))
    hours = [dict(r) for r in c.execute(
        f"SELECT CAST(strftime('%w', e.ts, '{mod}') AS INTEGER) dow, CAST(strftime('%H', e.ts, '{mod}') AS INTEGER) hour, "
        f"COUNT(*) n FROM events e JOIN sessions s ON s.id = e.session_id WHERE {ew} GROUP BY dow, hour", ap)]
    models = [dict(r) for r in c.execute(
        f"SELECT COALESCE(a.model, 'unknown') model, SUM(a.input_tokens+a.output_tokens+a.cache_read_tokens+a.cache_write_tokens) tokens, "
        f"SUM(a.output_tokens) output_tokens, SUM(a.cost_usd) cost, COUNT(*) calls FROM api_calls a JOIN sessions s ON s.id = a.session_id "
        f"WHERE {aw} GROUP BY model ORDER BY cost DESC", ap)]
    tw = ("1=1" + (" AND t.ts >= ?" if since else "") + (" AND s.project_path = ?" if project else "")
          + (" AND s.agent = ?" if agent else ""))
    tools = [dict(r) for r in c.execute(
        f"SELECT t.name, COUNT(*) n, SUM(t.is_error) errors, AVG(t.duration_ms) avg_ms FROM tool_calls t "
        f"JOIN sessions s ON s.id = t.session_id WHERE {tw} GROUP BY t.name ORDER BY n DESC LIMIT 200", ap)]
    labels = project_labels(c)
    projects = [dict(r) | {"label": labels.get(r["project_path"] or "", r["project_name"])} for r in c.execute(
        f"SELECT project_path, project_name, COUNT(*) sessions, SUM(active_s) active_s, SUM(est_cost_usd) cost, "
        f"MAX(started_at) last FROM sessions WHERE {sw} GROUP BY project_path ORDER BY active_s DESC LIMIT 12", sp)]
    outcomes = [dict(r) for r in c.execute(
        f"SELECT COALESCE(outcome, 'not analyzed') outcome, COUNT(*) n FROM sessions WHERE {sw} AND source != 'history' "
        f"GROUP BY 1 ORDER BY n DESC", sp)]
    work_types: dict[str, int] = {}
    for (wt,) in c.execute(f"SELECT work_types_json FROM sessions WHERE {sw} AND work_types_json IS NOT NULL", sp):
        for w in loads(wt, []) or []:
            work_types[w] = work_types.get(w, 0) + 1
    agents = _agent_rows(ctx, project, since)  # every agent, whichever one is selected
    recent = session_rows(ctx, f"{sw}", sp, "started_at DESC", 8)
    kq = "SELECT k.*, s.title session_title FROM knowledge k LEFT JOIN sessions s ON s.id = k.session_id WHERE k.status = 'active' AND k.source = 'analysis'"
    kqp = []
    if project:
        kq += " AND k.project_path = ?"
        kqp.append(project)
    knowledge = [knowledge_item(ctx, dict(r)) for r in c.execute(kq + " ORDER BY k.id DESC LIMIT 8", kqp)]
    return {
        "days": days, "project": project, "agent": agent, "agents": agents, "totals": totals, "previous": prev, "daily": daily_list,
        "calendar": calendar, "hours": hours, "models": models, "tools": tools, "projects": projects,
        "outcomes": outcomes, "work_types": sorted(work_types.items(), key=lambda x: -x[1]),
        "recent": recent, "knowledge": knowledge, "status": status_small(ctx, limited=limited),
    }


def _agent_rows(ctx: Context, project: str | None, since: str | None) -> list[dict]:
    """Home's Agents card: each agent used in this project scope, its totals for the period (0 when it was idle),
    when it was last used, and `records`: the metrics of AGENT_METRICS its sessions carry at all, in any project."""
    c = ctx.conn
    gw, gp = session_filters({"project": project, "since": since})
    totals = {r["agent"]: dict(r) for r in c.execute(
        f"SELECT agent, COUNT(*) sessions, COALESCE(SUM(active_s),0) active_s, COALESCE(SUM(n_prompts),0) prompts, "
        f"COALESCE(SUM(n_tool_calls),0) tool_calls, COALESCE(SUM(n_tool_errors),0) tool_errors, "
        f"COALESCE(SUM(lines_added),0) lines_added, COALESCE(SUM(lines_removed),0) lines_removed, "
        f"COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens),0) tokens, "
        f"COALESCE(SUM(est_cost_usd),0) cost FROM sessions WHERE {gw} GROUP BY agent", gp)}
    kw = "k.status = 'active'" + (" AND k.project_path = ?" if project else "") + (" AND k.created_at >= ?" if since else "")
    knowledge = dict(c.execute(f"SELECT s.agent, COUNT(*) FROM knowledge k JOIN sessions s ON s.id = k.session_id "
                               f"WHERE {kw} GROUP BY s.agent", [x for x in (project, since) if x]).fetchall())
    flags = ", ".join(f"MAX({expr}) {k}" for k, expr in AGENT_METRICS.items())
    rows = []
    for r in c.execute(f"SELECT agent, MAX(CASE WHEN {'project_path = ?' if project else '1'} THEN started_at END) last, "
                       f"{flags} FROM sessions GROUP BY agent", [project] if project else []):
        if r["agent"] in totals or r["last"]:
            rows.append({"agent": r["agent"], **dict.fromkeys(AGENT_TOTALS, 0), **totals.get(r["agent"], {}),
                         "knowledge": knowledge.get(r["agent"], 0), "last": r["last"],
                         "records": [k for k in AGENT_METRICS if r[k]]})
    return sorted(rows, key=lambda x: (x["active_s"], x["sessions"], x["last"] or ""), reverse=True)  # busiest first


def status_small(ctx: Context, limited: bool = False) -> dict:
    from ....analysis.queue import PAUSE_KEY, count_pending
    from ....runtime.update import available, recall

    if limited:  # the hub's own queue, jobs and updates are not theirs to see
        return {"pending": {"ready": 0, "queued": 0}, "analysis": {"auto": False, "max_per_run": 0, "label": ""}, "paused_until": None,
                "last_sync": None, "jobs": {}, "version": __version__, "update": None, "ui_build": UI_BUILD,
                "hub_url": None, "limited": True}

    recall(ctx.conn)  # a check made by another dashboard process, or before a restart
    _maybe_check_daily(ctx)

    pending = count_pending(ctx.conn, ctx.cfg)
    return {
        "pending": pending,
        "analysis": {"auto": ctx.cfg.analysis.auto, "max_per_run": ctx.cfg.analysis.max_per_run,
                     "label": BACKENDS.get(ctx.cfg.analysis.backend, BACKENDS["claude"]),
                     "billed": _billed(ctx)},  # an API provider off this computer: every call costs
        "paused_until": kv_get(ctx.conn, PAUSE_KEY),
        "last_sync": kv_get(ctx.conn, "last_sync"),
        "jobs": ctx.jobs.snapshot(),
        "version": __version__,
        "update": available(),
        "ui_build": UI_BUILD,
        "hub_url": ctx.cfg.hub_url or None,  # set on a computer that sends its sessions to a hub
    }


def status(ctx: Context) -> dict:
    from ....runtime.install import UI_LABEL, hooks_installed, launchd_status, mcp_registered, statusline_installed
    from ....runtime.statusline import plan_usage

    c = ctx.conn
    st = status_small(ctx)
    st["counts"] = {r["analysis_status"]: r["n"] for r in c.execute(
        "SELECT analysis_status, COUNT(*) n FROM sessions GROUP BY analysis_status")}
    st["analysis_cost"] = c.execute("SELECT COALESCE(SUM(cost_usd),0) FROM analyses").fetchone()[0]
    st["hooks"] = hooks_installed(ctx.cfg)
    st["launchd"] = launchd_status()
    st["ui_agent"] = launchd_status(UI_LABEL)
    st["mcp"] = mcp_registered()
    st["statusline"] = {"installed": statusline_installed(ctx.cfg), "plan": plan_usage(c)}
    from ....runtime.platforms.menubar import setting_info

    st["menu_bar"] = setting_info(ctx.cfg)
    st["db_size"] = ctx.cfg.db_path.stat().st_size if ctx.cfg.db_path.exists() else 0
    st["archive_dir"] = str(ctx.cfg.archive_dir)
    st["notes_dir"] = str(ctx.cfg.notes_dir)
    st["config"] = {"model": make_runner(ctx.cfg).model_label(), "auto": ctx.cfg.analysis.auto,
                    "backfill": ctx.cfg.analysis.backfill, "max_per_run": ctx.cfg.analysis.max_per_run}
    st["analysis"].update(analysis_backends(ctx))
    st["errors"] = [{**dict(r), "analysis_reason": analysis_reason_text(r["analysis_reason"])} for r in c.execute(
        "SELECT id, title, analysis_reason FROM sessions WHERE analysis_status = 'error' ORDER BY ended_at DESC LIMIT 10")]
    return st


def action_sync(ctx: Context) -> bool:
    from ....application.workflows import run_worker
    from ....ingestion.service import sync
    from ...exports.vault import export_markdown

    if ctx.cfg.sends_files:
        from ....sharing.push import push

        return ctx.jobs.start("sync", lambda progress: push(ctx.cfg, progress=progress).summary())

    def job(progress):
        progress("syncing transcripts…")
        conn = connect(ctx.cfg.db_path)
        try:
            report = sync(ctx.cfg, conn)
        finally:
            conn.rollback()
            conn.close()
        progress("processing…")
        work = run_worker(ctx.cfg, export=export_markdown, progress=progress)
        conn = connect(ctx.cfg.db_path)
        try:
            from ....sharing.leaderboard import send_safely

            send_safely(ctx.cfg, conn)  # once signed in to the leaderboard
        finally:
            conn.close()
        return f"{report.summary()} | {work.summary()}"

    return ctx.jobs.start("sync", job)


def analysis_queue(ctx: Context) -> dict:
    """What the status bar's queue line opens: the queued sessions with their reasons and when each is analyzed."""
    from ....analysis.queue import queue_list

    return queue_list(ctx.conn, ctx.cfg)


def action_analyze(ctx: Context, sid: str) -> bool:
    from ....application.workflows import run_worker
    from ...exports.vault import export_markdown

    real = resolve_session_id(ctx.conn, sid)
    if not real:
        return False

    def job(progress):
        report = run_worker(ctx.cfg, session_ids=[real], synthesize=True, export=export_markdown,
                            progress=progress, wait=True)
        if report.failed:
            raise RuntimeError(report.failed[0][1])
        return report.summary()

    return ctx.jobs.start(f"analyze:{real}", job)


def action_analyze_many(ctx: Context, refs: list) -> dict:
    """Analyze the sessions picked in the list, in one background job (history-only ones have nothing to send)."""
    from ....application.workflows import run_worker
    from ...exports.vault import export_markdown

    ids = []
    for ref in refs[:MAX_SELECTION]:
        real = resolve_session_id(ctx.conn, str(ref))
        row = ctx.conn.execute("SELECT source FROM sessions WHERE id = ?", (real,)).fetchone() if real else None
        if row and row["source"] != "history" and real not in ids:
            ids.append(real)
    if not ids:
        return {"started": False, "error": tr("Nothing in the selection can be analyzed")}

    def job(progress):
        report = run_worker(ctx.cfg, session_ids=ids, synthesize=True, export=export_markdown, progress=progress,
                            wait=True)
        if report.paused_until and report.failed:
            left = len(ids) - len(report.analyzed) - len(report.skipped)
            return f"{report.summary()}; hit the Claude usage limit: {left} not analyzed, select them again later"
        return report.summary()

    started = ctx.jobs.start("analyze:selection", job)
    return {"started": started, "count": len(ids), "dropped": len(refs) - len(ids),
            **({} if started else {"error": tr("A batch analysis is already running")})}


def action_analyze_queue(ctx: Context, which: str) -> dict:
    """The Queue's Analyze now: the sessions ready now, or ("active") the ones that may still be going, analyzed
    at once instead of on the background run. It runs with automatic analysis off and through a usage-limit
    pause (the limit may be over by now); a run that gets through lifts the pause for the background run too."""
    from ....analysis.queue import PAUSE_KEY, queued_ids
    from ....application.workflows import run_worker
    from ...exports.vault import export_markdown

    if which not in ("ready", "active"):
        return {"started": False, "error": tr("unknown queue")}
    if ctx.cfg.sends_files:
        return {"started": False, "error": tr("analysis runs on the hub ({hub})", hub=ctx.cfg.hub_url)}
    ids = queued_ids(ctx.conn, ctx.cfg, which, MAX_SELECTION)
    if not ids:
        return {"started": False, "error": tr("Nothing in the queue to analyze")}

    def job(progress):
        report = run_worker(ctx.cfg, session_ids=ids, synthesize=True, export=export_markdown, progress=progress,
                            wait=True)
        if report.analyzed and not report.paused_until:
            conn = connect(ctx.cfg.db_path)
            try:
                kv_set(conn, PAUSE_KEY, None)
                conn.commit()
            finally:
                conn.close()
        if report.paused_until and report.failed:
            left = len(ids) - len(report.analyzed) - len(report.skipped)
            return f"{report.summary()}; hit the usage limit again: {left} not analyzed, they stay queued"
        return report.summary()

    started = ctx.jobs.start("analyze:queue", job)
    return {"started": started, "count": len(ids),
            **({} if started else {"error": tr("The queue is already being analyzed")})}


# Home's Agents card: the metrics a session may carry. An agent none of whose sessions ever had one does not record it
# (a chat export has no tokens), so the card shows a dash there rather than a 0 that reads as "none"
AGENT_METRICS = {"tool_calls": "n_tool_calls > 0", "lines": "lines_added + lines_removed > 0",
                 "tokens": "input_tokens + output_tokens + cache_read_tokens + cache_write_tokens > 0", "cost": "est_cost_usd > 0"}
AGENT_TOTALS = ("sessions", "active_s", "prompts", "tool_calls", "tool_errors", "lines_added", "lines_removed", "tokens", "cost")


def _local_offset_modifier() -> str:
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    return f"{int(offset.total_seconds() // 60):+d} minutes"
