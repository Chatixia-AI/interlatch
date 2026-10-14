"""Every session waiting for analysis has a named reason, and the queue counts only what will really run."""

from dataclasses import replace
from datetime import timedelta

from interlatch.storage.kv import kv_set
from interlatch.core.util import to_iso, utcnow
from interlatch.analysis.queue import PAUSE_KEY, count_pending, pending_sessions, waiting_reason

from conftest import CWD, SID


def _add(conn, sid, **kw):
    row = {"id": sid, "project_path": CWD, "project_name": "demo-app", "source": "claude", "agent": "claude",
           "started_at": "2026-09-20T10:00:00Z", "ended_at": "2026-09-20T11:00:00Z", "ended_flag": 1, "n_prompts": 3,
           "analysis_status": "pending", "analysis_attempts": 0}
    row.update(kw)
    conn.execute(f"INSERT INTO sessions({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))


def _why(conn, cfg, sid):
    got = waiting_reason(conn, cfg, conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone())
    return got[0] if got else None


def test_each_reason_is_named_and_matches_the_worker(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    conn.execute("DELETE FROM sessions")
    soon = to_iso(utcnow() + timedelta(hours=2))
    _add(conn, "ready")
    _add(conn, "stale", analysis_status="stale")
    _add(conn, "active", ended_flag=0, ended_at=to_iso(utcnow()))
    _add(conn, "retry", analysis_status="error", analysis_attempts=1, analysis_not_before=soon, analysis_reason="timeout")
    _add(conn, "gave_up", analysis_status="error", analysis_attempts=4, analysis_reason="boom")
    _add(conn, "too_short", n_prompts=0)
    _add(conn, "excluded", project_path="/Users/test/Projects/secret")
    _add(conn, "old", started_at="2020-01-01T00:00:00Z")
    _add(conn, "done", analysis_status="done")
    _add(conn, "history", source="history")
    kv_set(conn, "installed_at", "2026-01-01T00:00:00Z")
    conn.commit()
    cfg = replace(cfg, analysis=replace(cfg.analysis, idle_minutes=20, backfill=False),
                  exclude_projects=["/Users/test/Projects/secret"])

    codes = {sid: _why(conn, cfg, sid) for sid in
             ("ready", "stale", "active", "retry", "gave_up", "too_short", "excluded", "old", "done", "history")}
    assert codes == {"ready": "ready", "stale": "ready", "active": "active", "retry": "retry", "gave_up": "gave_up",
                     "too_short": "too_short", "excluded": "excluded", "old": "before_install", "done": None, "history": None}
    # the reason and the worker never disagree about what runs next
    assert set(pending_sessions(conn, cfg, 1000)) == {sid for sid, c in codes.items() if c == "ready"}

    q = count_pending(conn, cfg)
    assert (q["ready"], q["queued"], q["held"]) == (2, 4, 4)
    assert q["reasons"]["gave_up"] == 1 and q["block"] is None


def test_a_stopped_queue_says_why(synced, monkeypatch):
    conn, cfg = synced["conn"], synced["cfg"]
    conn.execute("UPDATE sessions SET analysis_status = 'pending', ended_flag = 1")
    kv_set(conn, PAUSE_KEY, to_iso(utcnow() + timedelta(hours=1)))
    conn.commit()
    code, text = waiting_reason(conn, cfg, conn.execute("SELECT * FROM sessions WHERE id = ?", (SID,)).fetchone())
    assert code == "ready" and "paused until" in text
    assert "paused" in count_pending(conn, cfg)["block"]

    kv_set(conn, PAUSE_KEY, None)
    conn.commit()
    off = replace(cfg, analysis=replace(cfg.analysis, auto=False))
    assert "automatic analysis is off" in count_pending(conn, off)["block"]
    from interlatch.analysis.runners.claude import ClaudeRunner

    missing = ClaudeRunner(cfg)
    missing.bin = None  # not installed
    monkeypatch.setattr("interlatch.analysis.queue.make_runner", lambda cfg: missing)
    assert count_pending(conn, cfg)["block"] == "Claude Code (`claude`) was not found, so nothing can be analyzed"


def test_session_api_carries_the_reason(synced):
    from interlatch.interfaces.web.app import App

    conn, cfg = synced["conn"], synced["cfg"]
    conn.execute("UPDATE sessions SET analysis_status = 'pending', n_prompts = 0 WHERE id = ?", (SID,))
    conn.commit()
    app = App(cfg)
    s = app.session(SID)
    assert s["waiting"]["code"] == "too_short" and "fewer than" in s["waiting"]["text"]


def test_queued_ids_follow_the_reasons(synced):
    """The Queue's Analyze now takes exactly the sessions the reasons name: ready ones, or the ones still active."""
    from interlatch.analysis.queue import queued_ids

    conn, cfg = synced["conn"], synced["cfg"]
    conn.execute("DELETE FROM sessions")
    _add(conn, "ready")
    _add(conn, "active-1", ended_flag=0, ended_at=to_iso(utcnow()))
    _add(conn, "active-2", ended_flag=0, ended_at=to_iso(utcnow() - timedelta(minutes=1)))
    _add(conn, "too_short", ended_flag=0, ended_at=to_iso(utcnow()), n_prompts=0)
    conn.commit()
    cfg = replace(cfg, analysis=replace(cfg.analysis, idle_minutes=20))
    assert queued_ids(conn, cfg, "ready", 10) == ["ready"]
    assert queued_ids(conn, cfg, "active", 10) == ["active-1", "active-2"]  # newest first
    assert queued_ids(conn, cfg, "active", 1) == ["active-1"]


def test_analyze_the_queue_now(synced):
    """Analyze now runs the ready sessions through a usage-limit pause, and a run that gets through lifts the pause."""
    import time

    from interlatch.storage.kv import kv_get
    from interlatch.interfaces.web.app import App

    conn, cfg = synced["conn"], synced["cfg"]
    conn.execute("UPDATE sessions SET analysis_status = 'pending', analysis_attempts = 0, ended_flag = 1")
    kv_set(conn, PAUSE_KEY, to_iso(utcnow() + timedelta(hours=1)))
    conn.commit()
    app = App(cfg)
    assert app.action_analyze_queue("everything") == {"started": False, "error": "unknown queue"}
    assert not app.action_analyze_queue("active")["started"]  # nothing is still going

    r = app.action_analyze_queue("ready")
    assert r["started"] and r["count"] == len(pending_sessions(conn, cfg, 1000)) >= 1
    for _ in range(200):
        if app.jobs.snapshot()["analyze:queue"]["state"] != "running":
            break
        time.sleep(0.05)
    assert app.jobs.snapshot()["analyze:queue"]["state"] == "done", app.jobs.snapshot()["analyze:queue"]
    assert conn.execute("SELECT analysis_status FROM sessions WHERE id = ?", (SID,)).fetchone()[0] == "done"
    assert not kv_get(conn, PAUSE_KEY)
    assert not app.action_analyze_queue("ready")["started"]  # nothing left
