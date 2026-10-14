"""Regression tests for issues found by the independent review (each was reproduced before the fix)."""

import json
import sqlite3

from interlatch.analysis.session import analyze_session, store_analysis
from interlatch.storage.sqlite import connect
from interlatch.storage.kv import kv_set
from interlatch.ingestion.service import sync
from interlatch.knowledge.reviews import review_ready, week_bounds
from interlatch.knowledge.synthesize import GLOBAL, synthesize_project
from interlatch.analysis.queue import pending_sessions

from conftest import CWD, PROJECT_DIR, SID, _line


def _write_session(claude_dir, project_dir, sid, lines):
    proj = claude_dir / "projects" / project_dir
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{sid}.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def _prompt(text, ts, sid=SID, cwd=CWD, uuid="x"):
    return json.dumps({"type": "user", "uuid": uuid, "timestamp": ts, "sessionId": sid, "cwd": cwd,
                       "origin": {"kind": "human"}, "message": {"role": "user", "content": text}})


def test_lone_surrogate_does_not_block_the_sync(env):
    # a truncated emoji escape: json.loads accepts it, SQLite would reject the raw string
    bad = '{"type":"user","timestamp":"2026-09-21T09:00:00Z","origin":{"kind":"human"},"cwd":"/x",' \
          '"message":{"role":"user","content":"cut emoji \\ud83d here"}}'
    _write_session(env["claude_dir"], "-a-first", "00000000-0000-0000-0000-000000000001", [bad])
    conn = connect(env["cfg"].db_path)
    report = sync(env["cfg"], conn)
    assert not report.errors
    text = conn.execute("SELECT first_prompt FROM sessions WHERE id LIKE '00000000%'").fetchone()[0]
    assert text == "cut emoji � here"
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (SID,)).fetchone()[0] == 1  # later sessions too


def test_wrongly_typed_tool_input_keeps_the_session(env):
    lines = [
        _prompt("read it", "2026-09-21T09:00:00Z"),
        _line(type="assistant", uuid="a", timestamp="2026-09-21T09:00:05Z", message={
            "id": "m1", "model": "claude-opus-5-5", "role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": ["not", "a", "str"], "offset": "120", "limit": None}},
                {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": {"weird": 1}}},
                {"type": "tool_use", "id": "t3", "name": "WebFetch", "input": {"url": 42}}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}),
    ]
    sid = "00000000-0000-0000-0000-000000000002"
    _write_session(env["claude_dir"], "-a-typed", sid, [line.replace(SID, sid) for line in lines])
    conn = connect(env["cfg"].db_path)
    report = sync(env["cfg"], conn)
    assert not report.errors
    row = conn.execute("SELECT source, n_tool_calls FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert tuple(row) == ("transcript", 3)


def test_resumed_session_is_live_again(synced):
    conn, cfg, main = synced["conn"], synced["cfg"], synced["main"]
    sync(cfg, conn, only=main, ended=True)
    assert conn.execute("SELECT ended_flag FROM sessions WHERE id=?", (SID,)).fetchone()[0] == 1
    with open(main, "a") as fh:  # claude --resume appends to the same transcript
        fh.write(_prompt("continuing", "2026-09-21T09:00:00.000Z") + "\n")
    sync(cfg, conn)
    assert conn.execute("SELECT ended_flag FROM sessions WHERE id=?", (SID,)).fetchone()[0] == 0
    sync(cfg, conn)  # unchanged: stays live, no churn
    assert conn.execute("SELECT ended_flag FROM sessions WHERE id=?", (SID,)).fetchone()[0] == 0


def test_prompts_arriving_during_analysis_leave_it_stale(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    conn.execute("UPDATE sessions SET n_prompts = 4 WHERE id = ?", (SID,))  # the sync saw 2 more meanwhile
    store_analysis(conn, cfg, SID, {"title": "t", "summary": "s", "outcome": "completed", "knowledge": []}, "m", 2)
    row = conn.execute("SELECT analysis_status, analyzed_prompts FROM sessions WHERE id=?", (SID,)).fetchone()
    assert tuple(row) == ("stale", 2)
    assert SID in pending_sessions(conn, cfg, 10)


def test_history_stub_gets_analyzed_once_its_transcript_appears(env):
    sid = "old-session-1"  # present in the fixture's history.jsonl
    conn = connect(env["cfg"].db_path)
    sync(env["cfg"], conn)
    assert conn.execute("SELECT source, analysis_status FROM sessions WHERE id=?", (sid,)).fetchone()[1] == "skipped"
    _write_session(env["claude_dir"], "-Users-test", sid, [_prompt("set up git", "2026-09-21T09:00:00Z", sid=sid, cwd="/Users/test")])
    sync(env["cfg"], conn)
    assert tuple(conn.execute("SELECT source, analysis_status FROM sessions WHERE id=?", (sid,)).fetchone()) == ("transcript", "pending")


def test_skipped_session_is_revived_when_it_grows(synced):
    conn, cfg, main = synced["conn"], synced["cfg"], synced["main"]
    conn.execute("UPDATE sessions SET analysis_status='skipped', analysis_reason='too little content' WHERE id=?", (SID,))
    conn.commit()
    with open(main, "a") as fh:
        fh.write(_prompt("now a real question", "2026-09-21T09:00:00.000Z") + "\n")
    sync(cfg, conn)
    assert conn.execute("SELECT analysis_status FROM sessions WHERE id=?", (SID,)).fetchone()[0] == "pending"


def test_malformed_kb_reply_is_normalized_and_leaves_no_lock(synced, monkeypatch):
    conn, cfg = synced["conn"], synced["cfg"]
    analyze_session(conn, cfg, SID)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "badkb")
    data = synthesize_project(conn, cfg, CWD)
    assert data["overview"] == "7"
    assert [i["text"] for i in data["sections"][0]["items"]] == ["plain string bullet", "ok"]  # a bare number is dropped
    assert data["sections"][0]["items"][1]["sources"] == [2] and data["superseded_ids"] == []
    other = sqlite3.connect(cfg.db_path, timeout=1)
    other.execute("UPDATE kv SET value = value")  # would raise 'database is locked' if a transaction leaked
    other.commit()
    other.close()


def test_forget_wins_over_a_racing_sync(synced, monkeypatch):
    import interlatch.ingestion.service as ingest

    conn, cfg = synced["conn"], synced["cfg"]
    conn.execute("DELETE FROM sessions WHERE id=?", (SID,))
    conn.execute("DELETE FROM files_state")
    kv_set(conn, f"forget:{SID}", "now")
    conn.commit()
    monkeypatch.setattr(ingest, "forgotten_ids", lambda c: set())  # the sync computed its skip list too early
    sync(cfg, conn)
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id=?", (SID,)).fetchone()[0] == 0


def test_exclusion_uses_the_recorded_cwd(env):
    cwd = "/Users/test/secret.work/app"  # '.' is lost in the folder-name encoding
    sid = "00000000-0000-0000-0000-000000000003"
    _write_session(env["claude_dir"], "-Users-test-secret-work-app", sid, [_prompt("secret", "2026-09-21T09:00:00Z", sid=sid, cwd=cwd)])
    mem = env["claude_dir"] / "projects" / "-Users-test-secret-work-app" / "memory"
    mem.mkdir()
    (mem / "note.md").write_text("---\nname: n\ndescription: secret note\n---\nbody")
    cfg = env["cfg"]
    cfg.exclude_projects = ["/Users/test/secret.work/*"]
    conn = connect(cfg.db_path)
    sync(cfg, conn)
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id=?", (sid,)).fetchone()[0] == 0
    assert not [p for p in cfg.archive_dir.rglob("*") if "secret" in str(p)]
    assert conn.execute("SELECT COUNT(*) FROM knowledge WHERE title = 'secret note'").fetchone()[0] == 0


def test_newly_excluded_project_is_not_analyzed(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    assert pending_sessions(conn, cfg, 10) == [SID]
    cfg.exclude_projects = [CWD]
    assert pending_sessions(conn, cfg, 10) == []


def test_mid_week_review_does_not_block_the_weekly_one(synced):
    conn = synced["conn"]
    key, start, end = week_bounds("2026-W38")
    conn.execute("INSERT INTO sessions(id, source, project_path, started_at, analysis_status) VALUES "
                 "('w1','transcript','/p','2026-09-15T10:00:00Z','done'), ('w2','transcript','/p','2026-09-16T10:00:00Z','done')")
    conn.execute("UPDATE sessions SET analysis_status='done' WHERE id=?", (SID,))
    conn.execute("INSERT INTO reviews(period, created_at) VALUES (?, '2026-09-17T10:00:00Z')", (key,))  # "so far" review
    conn.commit()
    assert review_ready(conn, key) == (True, key)
    conn.execute("UPDATE reviews SET created_at = '2026-09-28T10:00:00Z' WHERE period = ?", (key,))
    conn.commit()
    assert review_ready(conn, key) == (False, "exists")


def test_only_project_synthesis_supersedes(synced, monkeypatch):
    conn, cfg = synced["conn"], synced["cfg"]
    analyze_session(conn, cfg, SID)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "supersede")
    synthesize_project(conn, cfg, GLOBAL)
    assert conn.execute("SELECT COUNT(*) FROM knowledge WHERE status='superseded'").fetchone()[0] == 0
    synthesize_project(conn, cfg, CWD)
    statuses = dict(conn.execute("SELECT source, status FROM knowledge WHERE source='analysis'").fetchall())
    assert statuses == {"analysis": "superseded"}
    assert conn.execute("SELECT status FROM knowledge WHERE source='memory'").fetchone()[0] == "active"


def test_deleted_memory_note_is_retired(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    note = synced["claude_dir"] / "projects" / PROJECT_DIR / "memory" / "deploy.md"
    note.unlink()
    sync(cfg, conn)
    assert conn.execute("SELECT status FROM knowledge WHERE source='memory'").fetchone()[0] == "superseded"


def test_schema_adds_columns_to_existing_databases(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
    old.execute("PRAGMA user_version = 1")
    old.commit()
    old.close()
    conn = connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert {"sentiment", "analysis_status", "ended_flag"} <= cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 3


def test_mcp_batch_requests(synced):
    import os
    import subprocess
    import sys

    batch = [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    out = subprocess.run([sys.executable, "-m", "interlatch", "mcp"], input=json.dumps(batch) + "\n", capture_output=True,
                         text=True, timeout=60, env={**os.environ})
    replies = json.loads(out.stdout.strip())
    assert [r["id"] for r in replies] == [1, 2] and replies[1]["result"]["tools"]


def test_dashboard_port_default_and_existing_installs(tmp_path):
    """New installs serve the dashboard on 11524; a config.toml written before keeps the 8765 it names."""
    from interlatch.core.config import load_config

    fresh = load_config(tmp_path / "new")
    assert fresh.server_port == 11524 and "port = 11524" in fresh.config_path.read_text()
    old = tmp_path / "old"
    old.mkdir()
    (old / "config.toml").write_text("[server]\nhost = \"127.0.0.1\"\nport = 8765\n")
    assert load_config(old).server_port == 8765
    (old / "config.toml").write_text("[server]\n")  # no port at all: the default
    assert load_config(old).server_port == 11524
