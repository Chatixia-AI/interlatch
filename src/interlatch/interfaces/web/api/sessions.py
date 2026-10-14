"""Sessions: the list, one session with its events, search, and exporting sessions."""

from __future__ import annotations

from ....application.records import resolve_session_id, session_record
from ....application.search import search_all, search_events, search_knowledge, search_sessions
from ....core.i18n import tr
from ....core.sessions import analysis_reason_text
from ....core.util import loads
from .artifacts import _artifacts
from .common import lesson_details, session_rows, since_days_ago, who_clause

pass
from ..context import Context
from .common import MAX_SELECTION


def sessions(ctx: Context, q: dict, limited: bool = False) -> dict:
    where, params = ["1=1"], []
    if q.get("project"):
        where.append("project_path = ?")
        params.append(q["project"])
    elif str(q.get("group") or "").isdigit():  # every project in one of your groups (groups.py)
        from ....projects.groups import paths_in

        paths = paths_in(ctx.conn, int(q["group"]))
        where.append(f"project_path IN ({','.join('?' * len(paths))})" if paths else "0")
        params += paths
    if q.get("outcome"):
        if q["outcome"] == "none":
            where.append("outcome IS NULL")
        else:
            where.append("outcome = ?")
            params.append(q["outcome"])
    if q.get("status"):
        where.append("analysis_status = ?")
        params.append(q["status"])
    if q.get("source"):
        where.append("source = ?")
        params.append(q["source"])
    if q.get("agent"):
        where.append("agent = ?")
        params.append(q["agent"])
    if q.get("who"):  # a person's or a computer's sessions, on a hub with people
        clause, p = who_clause(ctx, q["who"])
        where.append(clause)
        params += p
    if q.get("screen") in ("analyze", "maybe", "skip"):  # imported chats, as screening sorted them (screen.py)
        where.append("screen_verdict = ? AND screen_sig = files_sig")
        params.append(q["screen"])
    elif q.get("screen") == "none":
        from ....analysis.screen import SOURCES

        where.append(f"source IN ({','.join('?' * len(SOURCES))}) AND analysis_status != 'done' "
                     "AND (screen_sig IS NULL OR screen_sig != files_sig)")
        params += SOURCES
    since = q.get("since") or since_days_ago(q.get("days"))
    if since:
        where.append("started_at >= ?")
        params.append(since.replace("+00:00", "Z"))
    if q.get("until"):
        where.append("started_at < ?")
        params.append(q["until"].replace("+00:00", "Z"))
    if q.get("q") and limited:  # transcripts aren't theirs to search: titles and summaries only
        for term in q["q"].split()[:8]:
            like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')")
            params += [like, like]
    elif q.get("q"):
        ids = [s["session_id"] for s in search_sessions(ctx.conn, q["q"], project=q.get("project"), limit=500)]
        if not ids:
            return {"total": 0, "items": []}
        where.append(f"id IN ({','.join('?' * len(ids))})")
        params += ids
    sort = q.get("sort") if q.get("sort") in SORTABLE else "started_at"
    order = "ASC" if q.get("order") == "asc" else "DESC"
    w = " AND ".join(where)
    total = ctx.conn.execute(f"SELECT COUNT(*) FROM sessions WHERE {w}", params).fetchone()[0]
    if q.get("ids_only"):  # "select all matching" in the list: the ids, not the rows
        rows = ctx.conn.execute(f"SELECT id FROM sessions WHERE {w} "
                                 f"ORDER BY {sort} {order} NULLS LAST LIMIT {MAX_SELECTION}", params)
        return {"total": total, "ids": [r[0] for r in rows]}
    limit = min(int(q.get("limit") or 50), 500)
    offset = int(q.get("offset") or 0)
    items = session_rows(ctx, w, params, f"{sort} {order} NULLS LAST", limit, offset)
    return {"total": total, "items": items}


def session_summary(ctx: Context, sid: str) -> dict | None:
    """A session as someone limited to projects sees it (access.py): its summary and project lessons."""
    from ....sharing.access import session_page

    real = ctx.conn.execute("SELECT id FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not real:
        return None
    s = session_record(ctx.conn, real[0])
    s["knowledge"] = [lesson_details(k) for k in s["knowledge"]]
    return session_page(s)


def session(ctx: Context, sid: str) -> dict | None:
    real = resolve_session_id(ctx.conn, sid)
    if not real:
        return None
    s = session_record(ctx.conn, real)
    s["knowledge"] = [lesson_details(k) for k in s["knowledge"]]
    s["analysis_reason"] = analysis_reason_text(s.get("analysis_reason"))
    from ....analysis.queue import waiting_reason

    row = ctx.conn.execute("SELECT * FROM sessions WHERE id = ?", (real,)).fetchone()
    why = waiting_reason(ctx.conn, ctx.cfg, row) if row else None
    s["waiting"] = {"code": why[0], "text": why[1]} if why else None
    from ....runtime.statusline import session_usage

    s["usage"] = session_usage(row["statusline_json"]) if row else None  # None: not recorded, never zero
    s["api_calls"] = [dict(r) for r in ctx.conn.execute(
        "SELECT ts, agent_id, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd "
        "FROM api_calls WHERE session_id = ? ORDER BY ts", (real,))]
    s["markers"] = [dict(r) for r in ctx.conn.execute(
        "SELECT seq, ts, kind, text FROM events WHERE session_id = ? AND agent_id = '' AND kind IN ('prompt', 'compact', 'interrupt') "
        "ORDER BY seq", (real,))]
    s["tool_stats"] = [dict(r) for r in ctx.conn.execute(
        "SELECT name, COUNT(*) n, SUM(is_error) errors, AVG(duration_ms) avg_ms, SUM(duration_ms) total_ms "
        "FROM tool_calls WHERE session_id = ? GROUP BY name ORDER BY n DESC", (real,))]
    s["analyses"] = [dict(r) for r in ctx.conn.execute(
        "SELECT kind, started_at, finished_at, model, status, error, input_chars, chunks, cost_usd, duration_ms "
        "FROM analyses WHERE target = ? ORDER BY id DESC LIMIT 10", (real,))]
    s["outputs"] = _artifacts(ctx, session_id=real)[0]
    s["agents"] = [{"agent_id": "", "label": tr("Main thread")}] + [
        {"agent_id": a["agent_id"], "label": f"{a.get('agent_type') or 'agent'}: {a.get('description') or a['agent_id']}"}
        for a in s["subagents"]]
    return s


def events(ctx: Context, sid: str, q: dict) -> dict:
    real = resolve_session_id(ctx.conn, sid)
    if not real:
        return {"total": 0, "items": []}
    agent = q.get("agent", "")
    kinds = [k for k in (q.get("kinds") or "").split(",") if k]
    where, params = ["session_id = ?", "agent_id = ?"], [real, agent]
    if kinds:
        where.append(f"kind IN ({','.join('?' * len(kinds))})")
        params += kinds
    w = " AND ".join(where)
    total = ctx.conn.execute(f"SELECT COUNT(*) FROM events WHERE {w}", params).fetchone()[0]
    limit = min(int(q.get("limit") or 400), 2000)
    offset = int(q.get("offset") or 0)
    if q.get("around"):  # jump to a specific seq
        pos = ctx.conn.execute(f"SELECT COUNT(*) FROM events WHERE {w} AND seq < ?", [*params, int(q["around"])]).fetchone()[0]
        offset = max(0, pos - 20)
    items = []
    for r in ctx.conn.execute(
        f"SELECT seq, ts, role, kind, tool_name, tool_use_id, is_error, text, meta_json FROM events WHERE {w} "
        f"ORDER BY seq LIMIT ? OFFSET ?", [*params, limit, offset]):
        d = dict(r)
        d["meta"] = loads(d.pop("meta_json", None), None)
        items.append(d)
    return {"total": total, "offset": offset, "items": items}


def matches(ctx: Context, sid: str, q: dict) -> list[dict]:
    """Every transcript event in one session that mentions the query, in transcript order (main thread first)."""
    real = resolve_session_id(ctx.conn, sid)
    if not real or not (q.get("q") or "").strip():
        return []
    hits = search_events(ctx.conn, q["q"], session_id=real, limit=500)
    hits.sort(key=lambda e: (e["agent_id"] != "", e["agent_id"], e["seq"]))
    return [{k: e[k] for k in ("seq", "agent_id", "kind", "tool_name", "ts", "snippet")} for e in hits]


def search(ctx: Context, q: dict) -> dict:
    query = q.get("q") or ""
    project = q.get("project") or None
    offset = int(q.get("offset") or 0)
    found = search_all(ctx.conn, query, project=project, sort=q.get("sort") or "hits", offset=offset, limit=25)
    found["knowledge"] = [lesson_details(r) for r in search_knowledge(ctx.conn, query, project=project, limit=20)] \
        if not offset else []
    return found


def _export_ids(ctx: Context, ids: str) -> list[str]:
    from ...exports.sessions import MAX_SESSIONS, ExportError

    real = [r for r in (resolve_session_id(ctx.conn, x.strip()) for x in ids.split(",")[:MAX_SESSIONS] if x.strip()) if r]
    if not real:
        raise ExportError(tr("no such sessions"))
    return list(dict.fromkeys(real))


def export(ctx: Context, ids: str, fmt: str) -> tuple[str, str, bytes]:
    from ...exports.sessions import export_sessions

    return export_sessions(ctx.conn, _export_ids(ctx, ids), fmt)


def export_check(ctx: Context, ids: str, fmt: str) -> dict:
    from ...exports.sessions import FORMATS, ExportError, has_original

    if fmt not in FORMATS:
        raise ExportError(tr("unknown format {fmt!r}", fmt=fmt))
    real = _export_ids(ctx, ids)
    without = 0 if fmt != "raw" else sum(not has_original(ctx.conn, sid) for sid in real)
    if without == len(real):
        raise ExportError(tr("No original transcript to export: claude.ai chats share one export file and "
                             "prompt-history sessions have none. Export as Markdown or JSON instead."))
    return {"ok": True, "count": len(real), "without_original": without}


SORTABLE = {"started_at", "ended_at", "active_s", "duration_s", "n_prompts", "n_tool_calls", "tokens", "est_cost_usd",
            "title", "project_name", "agent", "lines_added", "n_tool_errors"}
