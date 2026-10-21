"""What an agent starting work in a project needs first: where the last session left things, what waits on the user,
and what other sessions did and learned since this agent's previous session here. Read from the archive only; the
agent writes nothing for it, so it is never more than the sessions and their analyses say."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from ..core.agents import AGENTS, short_name
from ..core.budget import Block, counted, fit
from ..core.redact import redact
from ..core.util import local_str, one_line, parse_ts, to_iso
from ..knowledge.ladder import TRUSTED
from .progress import asks, recent

DEFAULT_CHARS, MIN_CHARS, MAX_CHARS = 6000, 1000, 20000
LIVE = timedelta(minutes=30)  # a session of the asking agent that moved this recently may be the one asking
SINCE_ITEMS = 10  # sessions, and lessons, listed under "since your last session"

# how MCP clients name themselves in `initialize`, matched in this order against the agents Interlatch records
_CLIENT_NAMES = (("claude", "claude"), ("codex", "codex"), ("copilot", "copilot"), ("visual studio code", "copilot"),
                 ("vscode", "copilot"), ("opencode", "opencode"), ("antigravity", "antigravity"), ("bob", "bob"))

_OPEN, _ASK, _SESSION, _LESSON = "open thread", "question for the user", "session", "lesson"


def agent_of_client(name: str | None) -> str | None:
    """The agent an MCP client's `clientInfo.name` belongs to ("claude-code" → "claude"), or None when unknown."""
    low = (name or "").lower()
    return next((agent for part, agent in _CLIENT_NAMES if part in low), None)


def _maybe_live(row, agent: str | None, now: datetime) -> bool:
    if not agent or (row["agent"] or "claude") != agent or row["ended_flag"]:
        return False
    ended = parse_ts(row["ended_at"])
    return ended is None or now - ended < LIVE


def resume_text(conn: sqlite3.Connection, path: str, name: str, agent: str | None = None,
                limit: int = DEFAULT_CHARS, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    limit = max(MIN_CHARS, min(MAX_CHARS, int(limit or DEFAULT_CHARS)))
    rows = conn.execute(
        "SELECT id, agent, ended_at, ended_flag FROM sessions WHERE project_path = ? AND source != 'history' "
        "ORDER BY started_at DESC LIMIT 50", (path,)).fetchall()
    if not rows:
        return f"No sessions recorded yet for {name} ({path})."
    live = {r["id"] for r in rows if _maybe_live(r, agent, now)}
    sessions = [s for s in recent(conn, path, limit=len(live) + 5) if s["id"] not in live]
    header = [f"Resume {name} ({path}), from Interlatch's archive of past sessions:"]
    blocks: list[Block] = []
    if sessions:
        last = sessions[0]
        note = f": {one_line(last['outcome_note'], 200)}" if last.get("outcome_note") else ""
        header.append(redact(f"Where you left off: {local_str(last['started_at'], '%m-%d %H:%M')} "
                             f"{short_name(last['agent'])} session `{last['id'][:8]}` "
                             f"\"{one_line(last['title'] or '', 100)}\" ({last['outcome'] or 'not analyzed'}){note}"))
        if last.get("goal"):
            header.append(redact(f"Its goal: {one_line(last['goal'], 300)}"))
        blocks.append(Block("It left open:", [(80 - i, _OPEN, redact(f"- {one_line(t, 300)}"))
                                               for i, t in enumerate(last["open_threads"])]))
        blocks.append(Block("Waiting on the user (from the last analyzed sessions):", [
            (90 - i, _ASK, redact(f"- {one_line(a['text'], 300)} (session `{a['session_id'][:8]}`, "
                                 f"{local_str(a['started_at'], '%m-%d')})"))
            for i, a in enumerate(asks(sessions))]))
    footer_hint = "get_session for one session in full, search_sessions and search_knowledge for the rest"
    if agent:
        prev = next((r for r in rows if (r["agent"] or "claude") == agent and r["id"] not in live), None)
        if prev is None:
            header.append(f"No earlier {short_name(agent)} session here, so everything above is new to you.")
        else:
            since = to_iso(parse_ts(prev["ended_at"])) or prev["ended_at"]
            others = conn.execute(
                "SELECT id, agent, started_at, title, outcome FROM sessions WHERE project_path = ? "
                "AND source != 'history' AND started_at > ? AND id != ? ORDER BY started_at DESC LIMIT ?",
                (path, since, prev["id"], SINCE_ITEMS + len(live))).fetchall()
            others = [o for o in others if o["id"] not in live][:SINCE_ITEMS]
            lessons = conn.execute(
                "SELECT kind, title, stage FROM knowledge WHERE project_path = ? AND status = 'active' "
                f"AND stage IN ({','.join('?' * len(TRUSTED))}) AND updated_at > ? ORDER BY updated_at DESC LIMIT ?",
                (path, *TRUSTED, since, SINCE_ITEMS)).fetchall()
            when = local_str(since, '%m-%d %H:%M')
            blocks.append(Block(f"Other sessions since your last {short_name(agent)} session here (it ended {when}):", [
                (60 - i, _SESSION, redact(f"- {local_str(o['started_at'], '%m-%d')} {short_name(o['agent'])} "
                                          f"`{o['id'][:8]}` {one_line(o['title'] or '', 100)} "
                                          f"({o['outcome'] or 'not analyzed'})"))
                for i, o in enumerate(others)]))
            blocks.append(Block("Lessons that became trusted since then:", [
                (70 - i, _LESSON, redact(f"- [{k['kind']} · {k['stage']}] {one_line(k['title'], 200)}"))
                for i, k in enumerate(lessons)]))
            if not others and not lessons:
                blocks.append(Block(None, [(1000, _SESSION, f"Nothing new from other sessions since your last "
                                                              f"{short_name(agent)} session here (it ended {when}).")]))
    else:
        header.append("Pass `agent` (" + ", ".join(AGENTS) + ") to also see what other sessions did since yours.")

    def footer(left: dict[str, int]) -> str:
        shown = counted(left, (_ASK, _OPEN, _LESSON, _SESSION))
        return (f"Not shown here: {shown}. " if shown else "") + f"Use {footer_hint}."

    return fit(header, blocks, footer, limit)
