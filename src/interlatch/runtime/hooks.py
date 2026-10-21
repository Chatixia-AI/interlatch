"""Claude Code hook entrypoints. These must return fast: heavy work is handed to a detached process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ..core.process import INTERNAL_ENVS, self_command


def _read_payload() -> dict:
    try:
        data = sys.stdin.read()
        return json.loads(data) if data.strip() else {}
    except (ValueError, OSError):
        return {}


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
    from ..core.config import interlatch_home, load_config

    transcript = payload.get("transcript_path")
    home = interlatch_home()
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
    from ..core.config import load_config

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

    from ..projects.routing import under

    try:
        declared = [r[0] for r in conn.execute("SELECT path FROM hub_projects")]
    except sqlite3.OperationalError:  # a database from before projects could be set up
        return cwd
    return max((d for d in declared if under(cwd, d)), key=len, default=cwd)


# What a SessionStart text may hold, least useful to the next agent first: the oldest recent session goes before a
# teammate's lesson, which goes before a knowledge bullet; within a group the later (weaker) one goes first.
_SESSION, _TEAM, _NOTE = "session", "teammate lesson", "note"
STALE_COST = 25  # a note whose files changed since it was learned ranks like one two or three places further down


def _value(group: str, index: int, stage_rank: int = 0) -> int:
    if group == _SESSION:
        return 10 - index
    if group == _TEAM:
        return 75 - 10 * index
    return 100 - 10 * index + stage_rank


def _footer(left_out: dict[str, int]) -> str:
    from ..core.budget import counted

    tools = "the interlatch MCP tools (search_sessions, search_knowledge, get_session)"
    if not (left := counted(left_out, (_NOTE, _TEAM, _SESSION))):
        return f"Use {tools} for details."
    return f"Not shown here: {left}. Use {tools} to find them and for details."


def build_session_context(cfg, cwd: str) -> str | None:
    """A compact digest of what previous sessions learned about this project, at most `inject_max_chars` long."""
    from ..storage.sqlite import connect
    from ..sharing.pull import team_from
    from ..knowledge.ladder import STAGE_ORDER_SQL, STAGE_RANK, TRUSTED, stage_label

    def trust_tag(i: dict) -> str:
        return i["stage"] + (f" ×{i['sessions']}" if (i.get("sessions") or 0) > 1 else "")
    from ..knowledge.synthesize import kb_for_path, kb_sections
    from ..core.budget import Block, fit
    from ..knowledge.freshness import stale_ids
    from ..core.redact import redact
    from ..core.util import local_str, one_line

    from ..core.profiles import WAITING
    from ..projects.scope import scoped_for

    per_section = 6
    conn = connect(cfg.db_path, readonly=True)
    try:
        # named profiles: only what this folder's owner has; nothing while it waits for you to choose
        if scoped_for(cfg, conn, cwd) == WAITING:
            return None
        kb = kb_for_path(conn, cwd)
        cwd = _project_here(conn, cwd)  # a folder inside a project set up on this hub belongs to that project
        header: list[str] = []
        blocks: list[Block] = []
        left_out: dict[str, int] = {}
        # every line is redacted before it is measured, so redaction can't push the text over its limit; the items
        # were redacted when they were stored, and this catches any stored before a rule existed
        if kb:
            header.append(f"Interlatch knowledge base for {kb['project_name']} (from past coding-agent sessions, "
                          f"updated {local_str(kb['updated_at'], '%Y-%m-%d')}):")
            # a bullet whose lessons' files changed since is marked, and goes before the others when space is short
            stale = stale_ids(conn, [k for sec in kb_sections(kb) for i in sec.get("items") or [] for k in i.get("sources") or []])

            def stale_of(i: dict) -> str:
                return next((stale[k] for k in i.get("sources") or [] if k in stale), "")
            for section in kb_sections(kb):
                # tentative bullets stay out: this text is read at the start of every session, and
                # search_knowledge still finds them
                items = [i for i in section.get("items") or [] if i.get("stage") != "wip"]
                if not items:
                    continue
                # the best-established bullets first, each with its trust when it has earned some
                items = sorted(items, key=lambda i: -STAGE_RANK.get(i.get("stage") or "provisional", 1))
                if len(items) > per_section:
                    left_out[_NOTE] = left_out.get(_NOTE, 0) + len(items) - per_section
                blocks.append(Block(f"{section.get('title')}:", [
                    (_value(_NOTE, n, STAGE_RANK.get(i.get("stage") or "provisional", 1)) - (STALE_COST if stale_of(i) else 0),
                     _NOTE, redact(f"- {one_line(i.get('text') or '', 300)}"
                                   + (f" ({trust_tag(i)})" if i.get("stage") in TRUSTED else "") + stale_of(i)))
                    for n, i in enumerate(items[:per_section])]))
        else:
            rows = conn.execute(
                "SELECT k.id, k.kind, k.title, k.stage, k.session_id, k.confirmed_json FROM knowledge k WHERE k.status = 'active' "
                "AND k.project_path = ? AND k.source != 'team' AND k.kind IN ('gotcha', 'fix', 'fact', 'decision', 'preference') "
                "AND COALESCE(k.stage, '') != 'wip' "
                f"ORDER BY k.pinned DESC, {STAGE_ORDER_SQL}, k.id DESC LIMIT 12",
                (cwd,),
            ).fetchall()
            if rows:
                stale = stale_ids(conn, [r["id"] for r in rows])
                header.append("Interlatch notes from past coding-agent sessions in this project:")
                blocks.append(Block(None, [
                    (100 - 5 * n - (STALE_COST if r["id"] in stale else 0), _NOTE,
                     redact(f"- [{r['kind']}" + (f" · {stage_label(r)}" if r["stage"] in TRUSTED else "")
                            + f"] {one_line(r['title'], 200)}" + stale.get(r["id"], "")))
                    for n, r in enumerate(rows)]))
        team = conn.execute(  # teammates' lessons the team hub sent (sharing.pull.apply_team_lessons)
            "SELECT k.kind, k.title, k.stage, k.session_id, k.confirmed_json, k.source_ref FROM knowledge k "
            f"WHERE k.status = 'active' AND k.source = 'team' AND k.project_path = ? AND COALESCE(k.stage, '') != 'wip' "
            f"ORDER BY k.pinned DESC, {STAGE_ORDER_SQL}, k.updated_at DESC LIMIT 6", (cwd,),
        ).fetchall()
        if team:
            blocks.append(Block("From teammates' sessions in this project (via the team hub):", [
                (_value(_TEAM, n), _TEAM, redact(
                    f"- [{r['kind']}" + (f" · {stage_label(r)}" if r["stage"] in TRUSTED else "") + f"] "
                    f"{one_line(r['title'], 200)}" + (f" (from {', '.join(who)})" if (who := team_from(r)) else "")))
                for n, r in enumerate(team)]))
        recent = [(_value(_SESSION, n), _SESSION, redact(f"{local_str(r['started_at'], '%m-%d')} "
                                                         f"{one_line(r['title'] or '', 80)} ({r['outcome'] or 'not analyzed'})"))
                  for n, r in enumerate(conn.execute(
                      "SELECT started_at, title, outcome FROM sessions WHERE project_path = ? AND source != 'history' "
                      "ORDER BY started_at DESC LIMIT 3", (cwd,)))]
        if recent:
            blocks.append(Block("Recent sessions here: ", recent, inline=True))
        if not header and not blocks:
            return None
        return fit(header, blocks, _footer, cfg.inject_max_chars, left_out)
    finally:
        conn.close()
