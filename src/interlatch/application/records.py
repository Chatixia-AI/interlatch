"""Sessions as the dashboard, the CLI, the MCP server and the exports read them: a stored session with everything
attached to it (files, subagents, knowledge, prompts, errors), and the session an id or an id's prefix names."""

from __future__ import annotations

import sqlite3

from ..core.util import loads


def resolve_session_id(conn: sqlite3.Connection, ref: str) -> str | None:
    ref = ref.strip()
    if not ref:
        return None
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", (ref,)).fetchone()
    if row:
        return row[0]
    matches = conn.execute("SELECT id FROM sessions WHERE id LIKE ? LIMIT 2", (ref + "%",)).fetchall()
    return matches[0][0] if len(matches) == 1 else None


def session_record(conn: sqlite3.Connection, sid: str) -> dict | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not row:
        return None
    s = dict(row)
    for key in ("models", "tools", "skills", "mcp", "commands", "branches", "hooks"):
        s[key] = loads(s.pop(f"{key}_json", None), {}) or {}
    for key in ("prs", "artifacts", "workflows", "work_types", "tags", "highlights", "open_threads", "friction"):
        s[key] = loads(s.pop(f"{key}_json", None), []) or []
    s.pop("analysis_json", None)
    s["files"] = [dict(r) for r in conn.execute(
        "SELECT path, reads, edits, writes, lines_added, lines_removed FROM session_files WHERE session_id = ? "
        "ORDER BY (edits + writes) DESC, (lines_added + lines_removed) DESC, reads DESC", (sid,))]
    s["subagents"] = [dict(r) for r in conn.execute(
        "SELECT * FROM subagents WHERE session_id = ? ORDER BY started_at", (sid,))]
    s["knowledge"] = [dict(r) for r in conn.execute(
        "SELECT * FROM knowledge WHERE session_id = ? AND status != 'dismissed' ORDER BY "
        "CASE kind WHEN 'fix' THEN 0 WHEN 'gotcha' THEN 1 WHEN 'decision' THEN 2 ELSE 3 END, id", (sid,))]
    for k in s["knowledge"]:
        k["tags"] = loads(k.pop("tags_json", None), []) or []
    s["prompts"] = [dict(r) for r in conn.execute(
        "SELECT seq, ts, kind, text FROM events WHERE session_id = ? AND agent_id = '' AND kind IN ('prompt', 'command') "
        "ORDER BY seq", (sid,))]
    s["tool_errors"] = [dict(r) for r in conn.execute(
        "SELECT t.name, t.summary, e.text FROM tool_calls t LEFT JOIN events e ON e.session_id = t.session_id "
        "AND e.tool_use_id = t.tool_use_id AND e.kind = 'tool_result' WHERE t.session_id = ? AND t.is_error = 1 "
        "AND t.agent_id = '' ORDER BY t.ts LIMIT 50", (sid,))]
    s["total_tokens"] = sum(s.get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"))
    # the computer it ran on, named only when more than one sends sessions here (sharing/)
    machine = conn.execute("SELECT name, role, (SELECT COUNT(*) FROM machines) n FROM machines WHERE id = ?",
                           (s.get("machine_id"),)).fetchone()
    s["machine_name"] = machine["name"] if machine and machine["n"] > 1 else None
    return s
