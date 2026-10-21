"""`resume`: what an agent starting in a project needs first, read from the archive. Where the last session left
things, what waits on the user, and what other sessions did and learned since the asking agent's previous session
here; never the asking agent's own session that may still be running, and never over its length limit."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from interlatch.projects.resume import agent_of_client, resume_text

from conftest import CWD

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def _session(conn, sid, agent, started, ended, *, title, outcome="completed", ended_flag=1, threads=(), note=None):
    conn.execute(
        "INSERT INTO sessions(id, project_path, project_name, started_at, ended_at, ended_flag, title, outcome, "
        "outcome_note, goal, open_threads_json, source, agent, analysis_status) "
        "VALUES (?, ?, 'demo-app', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done')",
        (sid, CWD, started, ended, ended_flag, title, outcome, note, f"Goal of {title}", json.dumps(list(threads)),
         agent, agent))


def _lesson(conn, title, stage, updated):
    conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, status, stage, fingerprint, "
                 "created_at, updated_at) VALUES ('x', ?, 'demo-app', 'gotcha', ?, 'active', ?, ?, ?, ?)",
                 (CWD, title, stage, f"fp-{title}", updated, updated))


def _story(conn):
    """Claude worked on 10-19; Codex then fixed the hub on 10-20; a Claude session is running now."""
    _session(conn, "claude-old", "claude", "2026-10-19T09:00:00.000Z", "2026-10-19T10:00:00.000Z",
             title="Login flow", threads=["Should we keep the old token format?"])
    _session(conn, "codex-1", "codex", "2026-10-20T09:00:00.000Z", "2026-10-20T10:00:00.000Z",
             title="Fix hub token loss", note="Tokens survive restarts now",
             threads=["Add a migration test", "Decide whether to rotate the hub key"])
    _session(conn, "claude-now", "claude", "2026-10-21T11:40:00.000Z", "2026-10-21T11:55:00.000Z",
             title="Current work", ended_flag=0)
    _lesson(conn, "Hub tokens live in the volume", "established", "2026-10-20T10:05:00.000Z")
    _lesson(conn, "Old lesson", "canonical", "2026-10-01T10:00:00.000Z")
    _lesson(conn, "Unconfirmed guess", "provisional", "2026-10-20T10:06:00.000Z")
    conn.commit()


def test_mcp_clients_are_matched_to_the_agents_interlatch_records():
    assert agent_of_client("claude-code") == "claude"
    assert agent_of_client("codex-mcp-client") == "codex"
    assert agent_of_client("Visual Studio Code") == "copilot"
    assert agent_of_client("opencode") == "opencode"
    assert agent_of_client("some-other-client") is None and agent_of_client(None) is None


def test_it_says_where_things_were_left_and_what_changed_since_your_last_session(synced):
    conn = synced["conn"]
    _story(conn)
    text = resume_text(conn, CWD, "demo-app", agent="claude", now=NOW)
    left_off = next(line for line in text.splitlines() if line.startswith("Where you left off: "))
    assert "Fix hub token loss" in left_off and "Tokens survive restarts now" in left_off
    assert "Current work" not in text, "the asking agent's session that may still be running isn't news to it"
    assert "- Add a migration test" in text
    assert "Decide whether to rotate the hub key" in text.split("Waiting on the user")[1]
    since = text.split("since your last Claude session here")[1]
    assert "Codex `codex-1`" in since and "Hub tokens live in the volume" in since
    assert "Old lesson" not in since and "Unconfirmed guess" not in since


def test_without_an_agent_it_offers_to_say_what_changed(synced):
    conn = synced["conn"]
    _story(conn)
    text = resume_text(conn, CWD, "demo-app", now=NOW)
    assert "Pass `agent`" in text and "since your last" not in text


def test_a_first_session_is_told_everything_is_new(synced):
    conn = synced["conn"]
    _story(conn)
    text = resume_text(conn, CWD, "demo-app", agent="bob", now=NOW)
    assert "No earlier Bob session here" in text


def test_it_fits_its_limit_and_counts_what_it_left_out(synced):
    conn = synced["conn"]
    _story(conn)
    _session(conn, "codex-2", "codex", "2026-10-20T11:00:00.000Z", "2026-10-20T12:00:00.000Z", title="Big one",
             threads=[f"Thread {n}: " + "a long open question about the deploy " * 4 for n in range(12)])
    conn.commit()
    text = resume_text(conn, CWD, "demo-app", agent="claude", limit=1000, now=NOW)
    assert len(text) <= 1000
    assert text.splitlines()[-1].startswith("Not shown here: ") and "open thread" in text.splitlines()[-1]
    assert resume_text(conn, CWD, "demo-app", agent="claude", limit=1000, now=NOW) == text


def test_an_empty_project_says_so(synced):
    assert "No sessions recorded yet" in resume_text(synced["conn"], "/nowhere", "nowhere")


def test_the_mcp_tool_knows_the_client_from_initialize(synced):
    conn = synced["conn"]
    _story(conn)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-code", "version": "2"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "resume", "arguments": {"project": "demo-app", "max_chars": 4000}}}]
    out = subprocess.run([sys.executable, "-m", "interlatch", "mcp"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                         capture_output=True, text=True, timeout=60, env={**os.environ})
    replies = {r["id"]: r["result"] for r in map(json.loads, out.stdout.splitlines())}
    tool = next(t for t in replies[2]["tools"] if t["name"] == "resume")
    assert tool["annotations"]["readOnlyHint"] is True
    text = replies[3]["content"][0]["text"]
    assert "since your last Claude session here" in text and len(text) <= 4000
