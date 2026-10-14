"""Analyses other computers already did, sent with their sessions to a hub: applied to those sessions once a sync
has ingested them, so the hub doesn't pay to analyze them again."""

from __future__ import annotations

import gzip
import json
import logging
import os
import time

from ..core.config import Config
from ..core.machine import MACHINE_RE
from ..core.sessions import best_title
from ..sharing.protocol import ANALYSES_FILE, ANALYSES_MAX_AGE, ANALYSIS_COLS, KNOWLEDGE_COLS, SESSION_ID_RE

log = logging.getLogger("interlatch.hub")  # the hub's log lines, as before


def apply_analyses(cfg: Config, conn) -> int:
    """Carry over analyses spokes sent for sessions the hub has now ingested; returns how many were applied."""
    applied = 0
    if not cfg.machines_dir.is_dir():
        return 0
    for path in cfg.machines_dir.glob(f"*/{ANALYSES_FILE}"):
        machine_id = path.parent.name  # the computer that sent this file (receive_analyses)
        if not MACHINE_RE.match(machine_id):
            continue
        try:
            data = json.loads(gzip.decompress(path.read_bytes()))
            records = data.get("sessions") or []
        except (OSError, ValueError, EOFError):
            path.unlink(missing_ok=True)
            continue
        waiting = []
        for rec in records:
            if not isinstance(rec, dict) or not isinstance(rec.get("id"), str) or not SESSION_ID_RE.match(rec["id"]):
                continue
            s = conn.execute("SELECT analysis_status, n_prompts, project_path, project_name, ai_title, first_prompt, "
                             "machine_id FROM sessions WHERE id = ?", (rec["id"],)).fetchone()
            if s is None:
                waiting.append(rec)  # its transcript has not arrived yet
                continue
            if s["machine_id"] != machine_id:
                # another computer's session (or the hub's own) under that id: only its own computer's analysis applies
                log.warning("analysis of %s from %s not applied: the session is %s's", rec["id"], machine_id,
                            s["machine_id"] or "this hub")
                continue
            if s["analysis_status"] in ("done", "running"):
                continue  # the hub's own analysis wins
            values = {c: rec.get(c) for c in ANALYSIS_COLS}
            if values["analyzed_prompts"] and (s["n_prompts"] or 0) < values["analyzed_prompts"]:
                values["analyzed_prompts"] = s["n_prompts"]  # counted by an older parser that saw replayed prompts twice
            if (s["n_prompts"] or 0) > (values["analyzed_prompts"] or 0):
                values["analysis_status"], values["analysis_reason"] = "stale", "session continued after analysis"
            values["title"] = best_title({"llm_title": values["llm_title"], "ai_title": s["ai_title"],
                                          "first_prompt": s["first_prompt"]})
            conn.execute(f"UPDATE sessions SET {', '.join(f'{c} = ?' for c in values)} WHERE id = ?",
                         [*values.values(), rec["id"]])
            for k in rec.get("knowledge") or []:
                if not isinstance(k, dict) or not k.get("title") or not k.get("kind"):
                    continue
                row = {c: k.get(c) for c in KNOWLEDGE_COLS}
                row.update(session_id=rec["id"], project_path=s["project_path"], project_name=s["project_name"])
                conn.execute(f"INSERT OR IGNORE INTO knowledge({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                             list(row.values()))
            applied += 1
        conn.commit()
        if not waiting or time.time() - path.stat().st_mtime > ANALYSES_MAX_AGE:
            path.unlink(missing_ok=True)
        elif len(waiting) != len(records):
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(gzip.compress(json.dumps({"version": 1, "sessions": waiting}).encode(), mtime=0))
            os.replace(tmp, path)
    return applied
