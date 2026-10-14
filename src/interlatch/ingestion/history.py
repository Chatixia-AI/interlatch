"""Claude Code's history.jsonl: the prompts of sessions whose transcripts were deleted before Interlatch archived them."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..core.config import Config
from ..core.machine import local_machine_id
from ..core.paths import project_name_for
from ..core.util import one_line, parse_ts, safe_text, utcnow_iso
from ..sources.claude.parser import PARSER_VERSION, parse_history
from ..storage.kv import kv_get, kv_set
from .deletion import forgotten_ids


def import_history(conn: sqlite3.Connection, cfg: Config, path: Path) -> int:
    """Create 'history-only' sessions for prompts whose transcripts no longer exist."""
    st = path.stat()
    marker = f"{st.st_size}:{int(st.st_mtime)}"
    if kv_get(conn, f"history_sig:{path}") == marker:
        return 0
    created = 0
    from .archive import machine_of
    from ..projects.routing import resolver

    machine = machine_of(cfg, path)
    me = local_machine_id(cfg)
    # a stub another computer's history made is that computer's, as a transcript is
    known = {r[0] for r in conn.execute("SELECT id FROM sessions WHERE source != 'history' OR COALESCE(machine_id, ?) != ?",
                                        (me, machine or me))} | forgotten_ids(conn)
    for sid, rec in parse_history(path).items():
        rec["project"] = resolver(cfg, conn).resolve(machine, rec.get("project"))
        if sid in known or cfg.is_excluded(rec.get("project")):
            continue
        prompts = [(ts, safe_text(text)) for ts, text in rec["prompts"] if str(text).strip()]
        if not prompts:
            continue
        project = rec.get("project")
        real = [p for p in prompts if not p[1].startswith("/") or " " in p[1]]
        first = (real or prompts)[0][1]
        started, ended = prompts[0][0], prompts[-1][0]
        conn.execute("DELETE FROM events WHERE session_id = ?", (sid,))
        conn.executemany(
            "INSERT INTO events(session_id, agent_id, seq, ts, role, kind, searchable, text) VALUES (?,?,?,?,?,?,?,?)",
            [(sid, "", i, ts, "user", "command" if text.startswith("/") and " " not in text else "prompt", 1, text[:20000])
             for i, (ts, text) in enumerate(prompts)],
        )
        dur = 0.0
        if started and ended:
            dur = (parse_ts(ended) - parse_ts(started)).total_seconds()
        conn.execute(
            "INSERT INTO sessions(id, source, claude_dir, project_path, project_name, source_present, title, first_prompt, "
            "last_prompt, started_at, ended_at, duration_s, active_s, n_prompts, n_events, analysis_status, analysis_reason, "
            "ingested_at, parser_version, machine_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET n_prompts = excluded.n_prompts, n_events = excluded.n_events, "
            "last_prompt = excluded.last_prompt, ended_at = excluded.ended_at, duration_s = excluded.duration_s "
            "WHERE sessions.source = 'history'",
            (sid, "history", str(path.parent), project, project_name_for(project), 0, one_line(first, 90), first,
             prompts[-1][1], started, ended, dur, min(dur, len(prompts) * 300.0), len(real), len(prompts), "skipped",
             "history only (transcript deleted before Interlatch)", utcnow_iso(), PARSER_VERSION,
             machine or me),
        )
        created += 1
    kv_set(conn, f"history_sig:{path}", marker)
    return created
