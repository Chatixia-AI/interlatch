"""Every session waiting for analysis has a named reason, and the queue counts only what will really run."""

from dataclasses import replace
from datetime import timedelta

from interlatch.storage.kv import kv_set
from interlatch.core.util import to_iso, utcnow
from interlatch.analysis.queue import PAUSE_KEY, count_pending, pending_sessions, waiting_reason

from conftest import CWD, SID
from interlatch.interfaces.web import api


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
    s = api.sessions.session(app.context(), SID)
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
    assert api.status.action_analyze_queue(app.context(), "everything") == {"started": False, "error": "unknown queue"}
    assert not api.status.action_analyze_queue(app.context(), "active")["started"]  # nothing is still going

    r = api.status.action_analyze_queue(app.context(), "ready")
    assert r["started"] and r["count"] == len(pending_sessions(conn, cfg, 1000)) >= 1
    for _ in range(200):
        if app.jobs.snapshot()["analyze:queue"]["state"] != "running":
            break
        time.sleep(0.05)
    assert app.jobs.snapshot()["analyze:queue"]["state"] == "done", app.jobs.snapshot()["analyze:queue"]
    assert conn.execute("SELECT analysis_status FROM sessions WHERE id = ?", (SID,)).fetchone()[0] == "done"
    assert not kv_get(conn, PAUSE_KEY)
    assert not api.status.action_analyze_queue(app.context(), "ready")["started"]  # nothing left


def test_queue_list_plays_the_runs_forward(synced):
    """The queue the status bar opens: queued sessions only, in the worker's order, each with when a background run
    should reach it; a pause moves the times, a block that needs a change leaves them out."""
    from interlatch.analysis.queue import queue_list, typical_analysis_s
    from interlatch.core.util import parse_ts

    conn, cfg = synced["conn"], synced["cfg"]
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM analyses")
    now = utcnow()
    _add(conn, "r1", ended_at=to_iso(now - timedelta(hours=1)))
    _add(conn, "r2", ended_at=to_iso(now - timedelta(hours=2)))
    _add(conn, "r3", ended_at=to_iso(now - timedelta(hours=3)))
    _add(conn, "active", ended_flag=0, ended_at=to_iso(now - timedelta(minutes=1)))
    _add(conn, "retry", analysis_status="error", analysis_attempts=1, analysis_not_before=to_iso(now + timedelta(hours=2)))
    _add(conn, "too_short", n_prompts=0)  # held: not in the queue
    for took in (30, 40, 500):
        conn.execute("INSERT INTO analyses(kind, target, started_at, finished_at, status) VALUES ('session', 'x', ?, ?, 'done')",
                     (to_iso(now), to_iso(now + timedelta(seconds=took))))
    kv_set(conn, "last_sync", to_iso(now - timedelta(minutes=5)))
    conn.commit()
    cfg = replace(cfg, analysis=replace(cfg.analysis, idle_minutes=20, max_per_run=2))
    assert typical_analysis_s(conn) == 40

    def mins(q):  # minutes from now until each session is analyzed
        return {s["id"]: round((parse_ts(s["eta"]) - now).total_seconds() / 60) if s["eta"] else None for s in q["sessions"]}

    q = queue_list(conn, cfg)
    assert [s["id"] for s in q["sessions"]] == ["r1", "r2", "r3", "active", "retry"]
    assert (q["total"], q["ready"], q["active"], q["block"]) == (5, 3, 1, None)
    # runs at +10m (r1, r2), +25m (r3, and active, idle from +19m), then the first run after the retry time (+130m)
    assert mins(q) == {"r1": 11, "r2": 11, "r3": 26, "active": 26, "retry": 131}
    assert round((parse_ts(q["next_run"]) - now).total_seconds() / 60) == 10

    kv_set(conn, PAUSE_KEY, to_iso(now + timedelta(minutes=35)))
    conn.commit()
    q = queue_list(conn, cfg)
    assert "paused" in q["block"] and mins(q)["r1"] == 41  # the first run after the pause ends

    kv_set(conn, PAUSE_KEY, None)
    conn.commit()
    off = queue_list(conn, replace(cfg, analysis=replace(cfg.analysis, auto=False)))
    assert "automatic analysis is off" in off["block"] and set(mins(off).values()) == {None} and off["next_run"] is None


def test_a_spoke_that_shares_knowledge_analyzes_here(synced):
    """Analyze now hides only where the hub analyzes the sessions: joining a hub to share knowledge keeps it."""
    from interlatch.interfaces.web.app import App

    cfg = synced["cfg"]
    for share, on_hub in (("knowledge", False), ("everything", True)):
        app = App(replace(cfg, hub_url="https://hub.example.com", hub_share=share))
        st = api.status.status_small(app.context())
        assert st["hub_url"] and st["analysis"]["on_hub"] is on_hub
        assert api.status.analysis_queue(app.context())["on_hub"] is on_hub
    assert api.status.status_small(App(cfg).context())["analysis"]["on_hub"] is False


def _plan(conn, five_hour, seven_day=10.0):
    import json

    later = to_iso(utcnow() + timedelta(hours=2))
    kv_set(conn, "plan_usage_latest", json.dumps({"as_of": to_iso(utcnow()), "limits": {
        "five_hour": {"used_pct": five_hour, "resets_at": later},
        "seven_day": {"used_pct": seven_day, "resets_at": to_iso(utcnow() + timedelta(days=3))}}}))
    conn.commit()


def test_the_plan_limit_the_user_set_stops_automatic_runs(synced):
    """analysis.plan_limit_pct: at or above it, the queue says why and a background run analyzes nothing; a session
    asked for still runs, and the cap means nothing while another analyzer runs or when it is 0."""
    from interlatch.analysis.queue import plan_over_limit
    from interlatch.application.workflows import run_worker

    conn, cfg = synced["conn"], synced["cfg"]
    conn.execute("UPDATE sessions SET analysis_status = 'pending', analysis_attempts = 0, ended_flag = 1")
    _plan(conn, five_hour=85.0)
    capped = replace(cfg, analysis=replace(cfg.analysis, plan_limit_pct=80))
    assert plan_over_limit(conn, capped)["window"] == "five_hour"
    assert "session (5hr) limit is at 85%, at or above the 80% you set" in count_pending(conn, capped)["block"]
    assert count_pending(conn, replace(capped, analysis=replace(capped.analysis, plan_limit_pct=90)))["block"] is None
    assert count_pending(conn, cfg)["block"] is None  # 0: off
    assert plan_over_limit(conn, replace(capped, analysis=replace(capped.analysis, backend="codex"))) is None

    report = run_worker(capped, synthesize=False)
    assert not report.analyzed and "plan_limit_pct = 80" in report.note
    assert conn.execute("SELECT analysis_status FROM sessions WHERE id = ?", (SID,)).fetchone()[0] == "pending"
    report = run_worker(capped, session_ids=[SID], synthesize=False)
    assert report.analyzed
    assert conn.execute("SELECT analysis_status FROM sessions WHERE id = ?", (SID,)).fetchone()[0] == "done"


def test_the_status_bar_shows_usage_and_saves_the_limits(synced):
    from interlatch.core.config import load_config
    from interlatch.interfaces.web.app import App

    conn, cfg = synced["conn"], synced["cfg"]
    _plan(conn, five_hour=42.0)
    conn.execute("INSERT INTO analyses(kind, target, started_at, cost_usd) VALUES ('session', 'x', ?, 0.5), "
                 "('session', 'y', '2020-01-01T00:00:00Z', 2.0)", (to_iso(utcnow()),))
    conn.commit()
    ctx = App(cfg).context()
    u = api.status.status_small(ctx)["usage"]
    assert u["spent"]["today"] == 0.5 and u["spent"]["total"] >= 2.5 and u["calls_today"] >= 1
    assert u["plan"]["limits"]["five_hour"]["used_pct"] == 42.0 and u["over"] is None and u["collector"] is False
    assert "usage" not in api.status.status_small(ctx, limited=True)  # someone the hub limits to projects

    r = api.status.action_limits(ctx, {"max_per_run": 3, "max_budget_usd": 1.5, "plan_limit_pct": 40})
    assert r["limits"] == {"max_per_run": 3, "max_budget_usd": 1.5, "plan_limit_pct": 40} and "error" not in r
    assert r["over"]["window"] == "five_hour" and "at or above the 40% you set" in r["block"]
    saved = load_config(cfg.home).analysis
    assert (saved.max_per_run, saved.max_budget_usd, saved.plan_limit_pct) == (3, 1.5, 40)

    r = api.status.action_limits(ctx, {"max_per_run": 0, "max_budget_usd": "lots", "plan_limit_pct": True})
    assert r["error"] == "not saved: max_per_run, max_budget_usd, plan_limit_pct"
    assert r["limits"] == {"max_per_run": 3, "max_budget_usd": 1.5, "plan_limit_pct": 40}
