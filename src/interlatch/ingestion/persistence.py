"""Storing a parsed session: its row, events, tool calls, files, subagents and artifacts, replacing what an earlier
parse stored, and the rules that keep a re-parse from duplicating rows or dropping its analysis."""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from pathlib import Path

from ..sharing import news
from ..core import artifact_records
from ..core.config import Config
from ..core.machine import local_machine_id
from .. import __version__
from ..core.machine import local_machine, platform_label
from ..core.paths import project_name_for
from ..core.sessions import ParsedSession, best_title
from ..core.util import dumps, utcnow_iso
from ..sources.claude.files import decode_project_dir
from ..sources.claude.parser import PARSER_VERSION
from ..storage.kv import kv_get
from .report import SyncReport

log = logging.getLogger("interlatch.ingest")


def _json(counter) -> str:
    if isinstance(counter, Counter):
        return dumps(dict(counter.most_common()))
    return dumps(counter)


def store_parsed(
    conn: sqlite3.Connection,
    cfg: Config,
    ps: ParsedSession,
    *,
    claude_dir: Path,
    project_dir: str | None,
    transcript_path: Path,
    archive_path: Path,
    files_sig: str,
    ended: bool = False,
    agent: str = "claude",
    source: str = "transcript",
) -> str:
    """Replace a session's derived rows and upsert its metadata. Returns 'new' or 'updated', or 'foreign' (nothing
    changed) when another computer's session already has this id."""
    from .archive import machine_of
    from ..projects.routing import resolver

    prev = conn.execute(
        "SELECT analysis_status, analyzed_prompts, ended_flag, ended_at, llm_title, analysis_reason, source, n_prompts, "
        "parser_version, machine_id FROM sessions WHERE id = ?",
        (ps.id,),
    ).fetchone()
    sid = ps.id
    machine = machine_of(cfg, claude_dir)
    me = local_machine_id(cfg)
    if prev is not None and (prev["machine_id"] or me) != (machine or me):
        # a hub: a transcript a computer sent under the id of a session another computer (or the hub) has
        log.warning("session %s from %s not stored: it belongs to %s", sid, machine or me, prev["machine_id"] or me)
        return "foreign"
    for table in ("events", "tool_calls", "session_files", "subagents", "api_calls", "artifacts"):
        conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (sid,))
    artifact_records.finish(ps)

    conn.executemany(
        "INSERT INTO events(session_id, agent_id, seq, ts, role, kind, tool_name, tool_use_id, is_error, searchable, text, meta_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (sid, e.agent_id, e.seq, e.ts, e.role, e.kind, e.tool_name, e.tool_use_id, int(e.is_error),
             int(e.searchable), e.text, dumps(e.meta) if e.meta else None)
            for e in ps.events
        ],
    )
    conn.executemany(
        "INSERT INTO tool_calls(session_id, agent_id, tool_use_id, ts, name, mcp_server, summary, file_path, command, "
        "is_error, duration_ms, result_chars) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (sid, t.agent_id, t.tool_use_id, t.ts, t.name, t.mcp_server, t.summary, t.file_path,
             (t.command or "")[:4000] or None, int(t.is_error), t.duration_ms, t.result_chars)
            for t in ps.tool_calls
        ],
    )
    conn.executemany(
        "INSERT INTO session_files(session_id, path, reads, edits, writes, lines_added, lines_removed) VALUES (?,?,?,?,?,?,?)",
        [(sid, p, f.reads, f.edits, f.writes, f.lines_added, f.lines_removed) for p, f in ps.files.items()],
    )
    conn.executemany(
        "INSERT INTO subagents(session_id, agent_id, agent_type, description, workflow_id, phase, tool_use_id, model, "
        "started_at, ended_at, n_tool_calls, n_tool_errors, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, est_cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (sid, s.agent_id, s.agent_type, s.description, s.workflow_id, s.phase, s.tool_use_id, s.model,
             s.started_at, s.ended_at, s.n_tool_calls, s.n_tool_errors, s.input_tokens, s.output_tokens,
             s.cache_read_tokens, s.cache_write_tokens, round(s.est_cost_usd, 6))
            for s in ps.subagents.values()
        ],
    )
    conn.executemany(
        "INSERT INTO api_calls(session_id, agent_id, msg_id, ts, model, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, cost_usd, speed) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (sid, c.agent_id, c.msg_id, c.ts, c.model, c.input_tokens, c.output_tokens, c.cache_read_tokens,
             c.cache_write_tokens, round(c.cost_usd, 6), c.speed)
            for c in ps.api_calls
        ],
    )

    seqs = {(e.agent_id, e.tool_use_id): e.seq for e in ps.events if e.kind == "tool_use" and e.tool_use_id}
    conn.executemany(
        "INSERT INTO artifacts(session_id, agent_id, tool_use_id, seq, ts, key, kind, action, title, path, url, size, sha256, "
        "versions, meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (sid, o.get("agent_id") or "", o.get("tool_use_id"), seqs.get((o.get("agent_id") or "", o.get("tool_use_id"))),
             o.get("ts"), o["key"], o["kind"], o.get("action"), o.get("title"), o.get("path"), o.get("url"), o.get("size"),
             o.get("sha256"), o.get("versions") or 1, artifact_records.meta_json(o))
            for o in ps.outputs
        ],
    )

    totals = ps.totals()
    recorded_path = ps.project_path or (decode_project_dir(project_dir) if project_dir else None)
    # another computer's folder as this hub files it; this computer's own, under a project set up here if any
    project_path = resolver(cfg, conn).resolve(machine, recorded_path, ps.git_remote)
    excluded = cfg.is_excluded(project_path)

    # analysis state machine
    grew = prev is not None and (ps.ended_at or "") > (prev["ended_at"] or "")
    analyzed = prev["analyzed_prompts"] if prev is not None else None
    # a newer parser dropped duplicated prompts the analysis had counted: it still covered every real one
    recounted = bool(analyzed) and ps.n_prompts < analyzed and prev["parser_version"] != PARSER_VERSION
    if recounted:
        analyzed = ps.n_prompts
    if prev is None or prev["source"] == "history":
        status, reason = "pending", None  # new, or a history-only stub whose transcript has now appeared
    else:
        status, reason = prev["analysis_status"], prev["analysis_reason"]
        if status == "done" and (ps.n_prompts > (analyzed or 0) or (recounted and grew)):
            status, reason = "stale", "session continued after analysis"
        elif status == "skipped" and reason in ("too few prompts", "too little content", "excluded project") \
                and ps.n_prompts > (prev["n_prompts"] or 0):
            status, reason = "pending", None  # it has more to say now
        # "running" stays as is; store_analysis marks it stale if prompts arrived meanwhile
    if status in ("pending", "stale") and ps.n_prompts < cfg.analysis.min_prompts:
        status, reason = "skipped", "too few prompts"
    if excluded:
        status, reason = "skipped", "excluded project"
    # SessionEnd marks a session ended; if it keeps growing afterwards (claude --resume), it is live again
    ended_flag = 1 if ended else (1 if prev is not None and prev["ended_flag"] and not grew else 0)

    row = {
        "id": sid,
        "source": source,
        "agent": agent,
        "claude_dir": str(claude_dir),
        "project_dir": project_dir,
        "project_path": project_path,
        "project_name": project_name_for(project_path, project_dir or ""),
        "transcript_path": str(transcript_path),
        "archive_path": str(archive_path),
        "source_present": 1,
        "files_sig": files_sig,
        "parser_version": PARSER_VERSION,
        "ingested_at": utcnow_iso(),
        "ai_title": ps.custom_title or ps.ai_title,
        "first_prompt": ps.first_prompt,
        "last_prompt": ps.last_prompt,
        "started_at": ps.started_at,
        "ended_at": ps.ended_at,
        "duration_s": ps.duration_s,
        "active_s": ps.active_s,
        "git_branch": ps.git_branch,
        "cc_version": ps.cc_version,
        "entrypoint": ps.entrypoint,
        "permission_mode": ps.permission_mode,
        "primary_model": ps.primary_model,
        "models_json": _json(ps.models),
        "tools_json": _json(ps.tools),
        "skills_json": _json(ps.skills),
        "mcp_json": _json(ps.mcp_servers),
        "commands_json": _json(ps.commands),
        "branches_json": _json(ps.branches),
        "prs_json": dumps(ps.prs),
        "artifacts_json": dumps(ps.artifacts),
        "workflows_json": dumps(ps.workflows),
        "hooks_json": dumps(ps.hooks),
        "n_prompts": ps.n_prompts,
        "n_api_calls": ps.n_api_calls,
        "n_tool_calls": len(ps.tool_calls),
        "n_tool_errors": ps.n_tool_errors,
        "n_interrupts": ps.n_interrupts,
        "n_compactions": ps.n_compactions,
        "n_api_errors": ps.n_api_errors,
        "n_subagents": len(ps.subagents),
        "n_images": ps.n_images,
        "n_result_images": ps.n_result_images,
        "n_files": len(ps.files),
        "n_events": len(ps.events),
        "lines_added": ps.lines_added,
        "lines_removed": ps.lines_removed,
        "input_tokens": totals.get("input_tokens", 0),
        "output_tokens": totals.get("output_tokens", 0),
        "cache_read_tokens": totals.get("cache_read_tokens", 0),
        "cache_write_tokens": totals.get("cache_write_tokens", 0),
        "sub_tokens": totals.get("sub_tokens", 0),
        "est_cost_usd": round(totals.get("cost", 0.0), 4),
        "sub_cost_usd": round(totals.get("sub_cost", 0.0), 4),
        "cc_cost_usd": ps.cc_cost_usd,
        "peak_context": ps.peak_context,
        "ended_flag": ended_flag,
        "analysis_status": status,
        "analysis_reason": reason,
        "machine_id": machine or local_machine_id(cfg),
        "machine_path": recorded_path if recorded_path != project_path else None,
    }
    if prev is not None and analyzed != prev["analyzed_prompts"]:
        row["analyzed_prompts"] = analyzed
    row["title"] = best_title({"llm_title": prev["llm_title"] if prev else None, "ai_title": row["ai_title"],
                               "first_prompt": row["first_prompt"]})
    cols = list(row)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
    conn.execute(
        f"INSERT INTO sessions({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        [row[c] for c in cols],
    )
    if prev is None and machine and machine != me and source != "history":  # the Team page's news (news.py)
        news.record(conn, machine, project_path, sessions=1)
    return "new" if prev is None else "updated"


def store_source_session(conn, cfg, ps: ParsedSession, report: SyncReport, *, root: Path, transcript: Path, archive: Path,
                 sig: str, agent: str, source: str = "transcript") -> None:
    """Store a parsed Copilot/Bob/Antigravity/Codex Cloud session (the tail every non-Claude source shares)."""
    if cfg.is_excluded(ps.project_path) or kv_get(conn, f"forget:{ps.id}"):
        return
    try:
        result = store_parsed(conn, cfg, ps, claude_dir=root, project_dir=None, transcript_path=transcript,
                              archive_path=archive, files_sig=sig, agent=agent, source=source)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        log.exception("store failed: %s", transcript)
        report.errors.append(f"store {transcript}: {exc}")
        return
    if result == "foreign":
        return
    report.touched.append(ps.id)
    if result == "new":
        report.sessions_new += 1
    else:
        report.sessions_updated += 1


def is_unchanged(conn, sid: str, sig: str, force: bool) -> bool:
    """Whether the session is stored with these files' signature, so parsing them again would change nothing."""
    row = conn.execute("SELECT files_sig FROM sessions WHERE id = ?", (sid,)).fetchone()
    return bool(row) and row["files_sig"] == sig and not force


def register_local(conn, cfg: Config) -> str:
    """Record this computer in the machines table; returns its id."""
    me = local_machine(cfg)
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO machines(id, name, platform, version, role, first_seen, last_seen) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name = excluded.name, platform = excluded.platform, version = excluded.version, "
        "role = excluded.role, last_seen = excluded.last_seen",
        (me["id"], me["name"], platform_label(), __version__, "this", now, now),
    )
    return me["id"]
