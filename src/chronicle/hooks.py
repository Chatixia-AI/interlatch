"""Claude Code hook entrypoints. These must return fast: heavy work is handed to a detached process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

INTERNAL_ENV = "INTERLATCH_INTERNAL"  # on the agents Interlatch runs itself (analyses), so their hooks record nothing
INTERNAL_ENVS = (INTERNAL_ENV, "CHRONICLE_INTERNAL")  # and its name before the rename


def internal_env() -> dict[str, str]:
    """The environment for an agent Interlatch runs itself."""
    return {**os.environ, INTERNAL_ENV: "1"}


def _read_payload() -> dict:
    try:
        data = sys.stdin.read()
        return json.loads(data) if data.strip() else {}
    except (ValueError, OSError):
        return {}


def self_command() -> list[str]:
    """argv that re-runs this program: the bundled executable inside Interlatch.app, else `python -m chronicle`."""
    return [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "chronicle"]


def spawn_detached(args: list[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Windows: a console of its own without a window (what it starts shares it), outside Claude Code's process group
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    with open(log_path, "a") as log:
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
            env={k: v for k, v in os.environ.items() if k not in INTERNAL_ENVS},
            creationflags=flags,
        )


def hook_main(event: str) -> int:
    """Entry for `interlatch hook <event>`; never fails loudly (a hook error must not disturb Claude Code)."""
    if any(os.environ.get(name) for name in INTERNAL_ENVS):
        return 0
    payload = _read_payload()
    try:
        if event in ("session-end", "stop", "pre-compact"):
            return _on_session_end(payload, ended=event == "session-end")
        if event == "session-start":
            return _on_session_start(payload)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"interlatch hook {event} failed: {exc}", file=sys.stderr)
    return 0


def _on_session_end(payload: dict, *, ended: bool) -> int:
    from .config import chronicle_home, load_config

    transcript = payload.get("transcript_path")
    home = chronicle_home()
    if load_config(home, create=False).sends_files:  # this computer sends its sessions to a hub, which records them
        spawn_detached([*self_command(), "push", "--quiet"], home / "logs" / "hooks.log")
        return 0
    args = [*self_command(), "ingest-session"]
    if transcript:
        args.append(transcript)
    if ended:
        args.append("--ended")
    args.append("--work")
    spawn_detached(args, home / "logs" / "hooks.log")
    return 0


def _on_session_start(payload: dict) -> int:
    from .config import load_config

    cfg = load_config(create=False)
    if not cfg.inject_session_start or not cfg.db_path.exists():
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    context = build_session_context(cfg, cwd)
    if context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}))
    return 0


def _project_here(conn, cwd: str) -> str:
    """The project `cwd` belongs to: the folder set up as a project on this hub that holds it, else cwd itself."""
    import sqlite3

    from .hub import under

    try:
        declared = [r[0] for r in conn.execute("SELECT path FROM hub_projects")]
    except sqlite3.OperationalError:  # a database from before projects could be set up
        return cwd
    return max((d for d in declared if under(cwd, d)), key=len, default=cwd)


def build_session_context(cfg, cwd: str) -> str | None:
    """A compact digest of what previous sessions learned about this project."""
    from .db import connect
    from .hub import team_from
    from .ladder import STAGE_ORDER_SQL, STAGE_RANK, TRUSTED, stage_label

    def trust_tag(i: dict) -> str:
        return i["stage"] + (f" ×{i['sessions']}" if (i.get("sessions") or 0) > 1 else "")
    from .synthesize import kb_for_path, kb_sections
    from .util import local_str, one_line

    conn = connect(cfg.db_path, readonly=True)
    try:
        kb = kb_for_path(conn, cwd)
        cwd = _project_here(conn, cwd)  # a folder inside a project set up on this hub belongs to that project
        lines: list[str] = []
        if kb:
            lines.append(f"Interlatch knowledge base for {kb['project_name']} (from past coding-agent sessions, "
                         f"updated {local_str(kb['updated_at'], '%Y-%m-%d')}):")
            for section in kb_sections(kb):
                items = section.get("items") or []
                if not items:
                    continue
                lines.append(f"{section.get('title')}:")
                # the best-established bullets first, each with its trust when it has earned some
                items = sorted(items, key=lambda i: -STAGE_RANK.get(i.get("stage") or "provisional", 1))
                lines += [f"- {one_line(i.get('text') or '', 300)}"
                          + (f" ({trust_tag(i)})" if i.get("stage") in TRUSTED else "") for i in items[:6]]
        else:
            rows = conn.execute(
                "SELECT k.kind, k.title, k.stage, k.session_id, k.confirmed_json FROM knowledge k WHERE k.status = 'active' "
                "AND k.project_path = ? AND k.source != 'team' AND k.kind IN ('gotcha', 'fix', 'fact', 'decision', 'preference') "
                f"ORDER BY k.pinned DESC, {STAGE_ORDER_SQL}, k.id DESC LIMIT 12",
                (cwd,),
            ).fetchall()
            if rows:
                lines.append("Interlatch notes from past coding-agent sessions in this project:")
                lines += [f"- [{r['kind']}" + (f" · {stage_label(r)}" if r["stage"] in TRUSTED else "") + f"] {one_line(r['title'], 200)}"
                          for r in rows]
        team = conn.execute(  # teammates' lessons the team hub sent (hub.apply_team_lessons)
            "SELECT k.kind, k.title, k.stage, k.session_id, k.confirmed_json, k.source_ref FROM knowledge k "
            f"WHERE k.status = 'active' AND k.source = 'team' AND k.project_path = ? "
            f"ORDER BY k.pinned DESC, {STAGE_ORDER_SQL}, k.updated_at DESC LIMIT 6", (cwd,),
        ).fetchall()
        if team:
            lines.append("From teammates' sessions in this project (via the team hub):")
            lines += [f"- [{r['kind']}" + (f" · {stage_label(r)}" if r["stage"] in TRUSTED else "") + f"] "
                      f"{one_line(r['title'], 200)}" + (f" (from {', '.join(who)})" if (who := team_from(r)) else "")
                      for r in team]
        recent = conn.execute(
            "SELECT started_at, title, outcome FROM sessions WHERE project_path = ? AND source != 'history' "
            "ORDER BY started_at DESC LIMIT 3", (cwd,),
        ).fetchall()
        if recent:
            lines.append("Recent sessions here: " + "; ".join(
                f"{local_str(r['started_at'], '%m-%d')} {one_line(r['title'] or '', 80)} ({r['outcome'] or 'not analyzed'})"
                for r in recent))
        if not lines:
            return None
        lines.append("Use the interlatch MCP tools (search_sessions, search_knowledge, get_session) for details.")
        text = "\n".join(lines)
        return text[: cfg.inject_max_chars]
    finally:
        conn.close()
