"""The menu-bar item's state and text (menubar.py), worked out without AppKit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chronicle.menubar import ATTENTION, OK, PAUSED, WORKING, ago, day_label, summarize, when
from chronicle.util import to_iso

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def status(**kw) -> dict:
    base = {"pending": {"ready": 0, "queued": 0}, "paused_until": None, "last_sync": "2026-10-10T11:30:00Z",
            "jobs": {}, "update": None, "hub_url": None}
    return {**base, **kw}


def test_quiet_when_nothing_needs_a_look():
    snap = summarize(status(), now=NOW)
    assert (snap.state, snap.title, snap.sub) == (OK, "All caught up", "Synced 30 min ago")
    assert snap.headline == "All caught up · Synced 30 min ago"
    assert snap.detail == "" and snap.update is None and not snap.syncing and snap.note == ""


def test_never_synced():
    assert summarize(status(last_sync=None), now=NOW).sub == "Not synced yet"


def test_a_queue_is_the_title_and_does_not_change_the_icon():
    snap = summarize(status(pending={"ready": 3, "queued": 12}), now=NOW)
    assert (snap.state, snap.title, snap.sub) == (OK, "12 sessions to analyze", "Synced 30 min ago")
    assert snap.detail == "12 sessions waiting for analysis" and snap.stats == {"waiting": 12}
    assert summarize(status(pending={"queued": 1}), now=NOW).title == "1 session to analyze"


def test_what_stops_the_queue_is_noted():
    st = status(pending={"queued": 3, "block": "automatic analysis is off (analysis.auto)"})
    assert summarize(st, now=NOW).note == "automatic analysis is off (analysis.auto)"
    assert summarize(status(pending={"queued": 0, "block": "off"}), now=NOW).note == ""


def test_a_running_job_with_progress():
    jobs = {"sync": {"state": "running", "done": 2, "total": 5}}
    snap = summarize(status(jobs=jobs), now=NOW)
    assert (snap.state, snap.title, snap.sub, snap.progress, snap.syncing) == (WORKING, "Syncing", "2 of 5", (2, 5), True)


def test_a_running_job_says_what_it_is_doing():
    snap = summarize(status(jobs={"themes": {"state": "running", "message": "grouping 40 terms…"}}), now=NOW)
    assert (snap.title, snap.sub, snap.progress) == ("Grouping glossary themes", "grouping 40 terms…", None)


def test_analysis_by_another_process_counts_as_working():
    snap = summarize(status(), now=NOW, analyzing=2)
    assert (snap.state, snap.title, snap.syncing) == (WORKING, "Analyzing 2 sessions", False)


def test_the_apps_own_sync_loop_counts_as_syncing():
    snap = summarize(status(), now=NOW, sync_running=True)
    assert (snap.state, snap.title, snap.syncing) == (WORKING, "Syncing", True)


def test_finished_jobs_are_not_work():
    snap = summarize(status(jobs={"themes": {"state": "done"}}), now=NOW)
    assert snap.state == OK


@pytest.mark.parametrize("kw, title, sub", [
    ({"sync_error": "database is locked"}, "Sync failed", "database is locked"),
    ({"push": {"at": "2026-10-10T11:00:00Z", "errors": ["hub unreachable"]}}, "Sending to the hub failed", "hub unreachable"),
])
def test_failures_need_a_look_even_while_something_runs(kw, title, sub):
    snap = summarize(status(jobs={"analyze:abc": {"state": "running"}}), now=NOW, **kw)
    assert (snap.state, snap.title, snap.sub) == (ATTENTION, title, sub)


def test_a_failed_sync_job_needs_a_look_until_the_next_one():
    st = status(jobs={"sync": {"state": "error", "result": "no space left on device"}})
    assert summarize(st, now=NOW).state == ATTENTION
    st["jobs"]["sync"] = {"state": "running"}
    assert summarize(st, now=NOW).state == WORKING


def test_long_errors_are_cut_to_fit():
    snap = summarize(status(), now=NOW, sync_error="x " * 200)
    assert len(snap.sub) <= 90 and snap.sub.endswith("…")


def test_paused_until_later_but_not_once_it_has_passed():
    snap = summarize(status(paused_until="2026-10-10T13:00:00Z"), now=NOW)
    assert (snap.state, snap.title) == (PAUSED, "Analysis paused")
    assert snap.sub == f"Resumes {when('2026-10-10T13:00:00Z', NOW)} (usage limit)"
    assert summarize(status(paused_until="2026-10-10T11:00:00Z"), now=NOW).state == OK


def test_a_spoke_that_sends_its_sessions_says_where():
    st = status(hub_url="https://hub.example.com:8443", pending={"queued": 4})
    snap = summarize(st, now=NOW, sends_files=True)
    assert (snap.title, snap.sub) == ("Sending to hub.example.com", "Nothing sent yet")
    assert snap.detail == "" and snap.hub == "" and snap.stats == {"waiting": None}  # the hub analyzes them
    snap = summarize(st, now=NOW, sends_files=True, hub_name="Team hub", push={"at": "2026-10-10T11:45:00Z", "errors": []})
    assert (snap.state, snap.title, snap.sub) == (OK, "Sending to Team hub", "Last sent 15 min ago")


def test_a_spoke_that_shares_knowledge_syncs_and_analyzes_here():
    st = status(hub_url="https://hub.example.com", pending={"queued": 4})
    snap = summarize(st, now=NOW, hub_name="Team hub")
    assert snap.title == "4 sessions to analyze"
    assert snap.hub == "Sharing knowledge with Team hub · nothing sent yet"
    snap = summarize(st, now=NOW, push={"at": "2026-10-10T11:45:00Z", "errors": []})
    assert snap.hub == "Sharing knowledge with hub.example.com · last sent 15 min ago"


def test_no_hub_no_hub_line():
    assert summarize(status(), now=NOW).hub == ""


def test_an_update_shows_in_the_menu_only():
    snap = summarize(status(update={"to": "0.19.0", "key": "0.19.0"}), now=NOW)
    assert (snap.state, snap.update) == (OK, "0.19.0")


def test_when_says_the_day_only_before_today():
    assert when("2026-10-10T11:30:00Z", NOW).startswith("at ")
    assert not when("2026-10-01T11:30:00Z", NOW).startswith("at ")
    assert when(None, NOW) == ""


@pytest.mark.parametrize("iso, expected", [
    ("2026-10-10T11:59:30Z", "just now"),
    ("2026-10-10T11:05:00Z", "55 min ago"),
    (None, ""),
])
def test_ago(iso, expected):
    assert ago(iso, NOW) == expected


def test_ago_counts_calendar_days():
    local_noon = NOW.astimezone().replace(hour=12, minute=0)
    assert ago(to_iso(local_noon - timedelta(hours=3)), local_noon) == "3 h ago"
    assert ago(to_iso(local_noon - timedelta(days=1)), local_noon) == "yesterday"
    assert ago(to_iso(local_noon - timedelta(days=3)), local_noon) == "3 days ago"
    assert ago(to_iso(local_noon - timedelta(days=30)), local_noon) == f"{(local_noon - timedelta(days=30)):%b} {(local_noon - timedelta(days=30)).day}"


def test_day_label():
    local_noon = NOW.astimezone().replace(hour=12, minute=0)
    assert day_label(to_iso(local_noon), local_noon) == "Today"
    assert day_label(to_iso(local_noon - timedelta(days=1)), local_noon) == "Yesterday"
    assert day_label(to_iso(local_noon - timedelta(days=4)), local_noon).count(",") == 1


def test_the_page_gets_the_state_and_its_picture():
    page = summarize(status(), now=NOW, sync_error="boom").to_page()
    assert page["state"] == ATTENTION and page["art"] == "art-glitch.webp"
    assert {"title", "sub", "hub", "recent", "update", "syncing", "stats", "note", "progress"} <= set(page)


def test_snapshot_from_the_dashboards_own_app(synced):
    from chronicle.menubar import snapshot
    from chronicle.server import make_server

    httpd = make_server(synced["cfg"], port=0)
    try:
        snap = snapshot(httpd.app)
    finally:
        httpd.server_close()
    assert snap.state in (OK, WORKING)
    assert snap.recent and snap.recent[0]["id"]
    assert all(set(r) == {"id", "title", "project_name", "agent", "outcome", "analysis_status", "at", "day", "time"}
               for r in snap.recent)
    assert set(snap.stats) == {"waiting", "today", "lessons"}


def test_stats_count_today_and_the_last_week(synced):
    from chronicle.menubar import stats

    conn = synced["conn"]
    n = conn.execute("SELECT COUNT(*) FROM sessions WHERE source NOT IN ('history')").fetchone()[0]
    conn.execute("UPDATE sessions SET ended_at = ?", (to_iso(NOW),))
    conn.execute("INSERT INTO knowledge (kind, title, source, status, created_at) VALUES ('fix', 'a', 'analysis', 'active', ?)",
                 (to_iso(NOW - timedelta(days=2)),))
    conn.execute("INSERT INTO knowledge (kind, title, source, status, created_at) VALUES ('fix', 'b', 'analysis', 'active', ?)",
                 (to_iso(NOW - timedelta(days=9)),))
    conn.execute("INSERT INTO knowledge (kind, title, source, status, created_at) VALUES ('fix', 'c', 'manual', 'active', ?)",
                 (to_iso(NOW),))
    got = stats(conn, NOW)
    assert got["today"] == n and got["lessons"] >= 1
    before = got["lessons"]
    conn.execute("UPDATE knowledge SET status = 'dismissed' WHERE title = 'a'")
    assert stats(conn, NOW)["lessons"] == before - 1


def test_recent_sessions_leave_out_imported_chats(synced):
    from chronicle.menubar import recent_sessions

    conn = synced["conn"]
    sid = recent_sessions(conn)[0]["id"]
    conn.execute("UPDATE sessions SET source = 'chatgpt-export'")
    assert recent_sessions(conn) == []
    conn.execute("UPDATE sessions SET source = 'transcript' WHERE id = ?", (sid,))
    assert [r["id"] for r in recent_sessions(conn)] == [sid]


def test_only_the_login_item_shows_the_icon_by_itself(env, monkeypatch):
    from chronicle.install import UI_LABEL
    from chronicle.menubar import wants_menu_bar

    cfg = env["cfg"]
    cfg.server_menu_bar = True  # turned on at install
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    assert not wants_menu_bar(cfg)  # a dashboard started in a terminal
    monkeypatch.setenv("XPC_SERVICE_NAME", UI_LABEL)
    assert wants_menu_bar(cfg)
    cfg.server_menu_bar = False
    assert not wants_menu_bar(cfg)
    cfg.server_menu_bar = True
    monkeypatch.setattr("sys.platform", "linux")
    assert not wants_menu_bar(cfg)


def test_menu_bar_is_off_until_turned_on_in_config(env):
    from chronicle.config import load_config

    cfg = env["cfg"]
    assert load_config(cfg.home).server_menu_bar is False
    cfg.config_path.write_text("[server]\nmenu_bar = true\n")
    assert load_config(cfg.home).server_menu_bar is True


def test_agent_sync_error_reads_the_launchd_exit_code(monkeypatch):
    from chronicle import menubar

    for info, expected in [({"loaded": True, "last_exit": "0"}, None),
                           ({"loaded": True, "last_exit": "(never exited)"}, None),
                           ({"loaded": False, "last_exit": "1"}, None),
                           ({"installed": False}, None)]:
        monkeypatch.setattr("chronicle.install.background_status", lambda info=info: info)
        assert menubar.agent_sync_error() is expected
    monkeypatch.setattr("chronicle.install.background_status", lambda: {"loaded": True, "last_exit": "78"})
    assert "78" in menubar.agent_sync_error()


def test_without_pyobjc_the_dashboard_serves_as_before(env, monkeypatch):
    import builtins

    from chronicle import menubar

    real = builtins.__import__

    def no_appkit(name, *a, **kw):
        if name in ("AppKit", "PyObjCTools"):
            raise ImportError(name)
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_appkit)
    assert menubar.run_with_server(env["cfg"], object(), "http://127.0.0.1:1/") is False


# ------------------------------------------------------------------------------------- opt-in at `chronicle install`
@pytest.fixture
def setup(env, monkeypatch):
    """`chronicle install` on a Mac whose dashboard runs at login."""
    monkeypatch.setattr("chronicle.update.compares_online", lambda: False)  # no release-notification question
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("chronicle.install.launchd_status", lambda label="": {"loaded": True})
    monkeypatch.setattr("chronicle.install.install_launchd", lambda *a, **k: [])
    monkeypatch.setattr("chronicle.install.install_ui_agent", lambda *a, **k: [])
    monkeypatch.setattr("chronicle.install.install_mcp", lambda *a, **k: [])
    return env


def _install(monkeypatch, answers, *extra):
    import sys

    from chronicle.cli import main

    asked = []

    class Tty:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr("builtins.input", lambda q: asked.append(q) or next((a for k, a in answers.items() if k in q), ""))
    assert main(["install", "--exe", "/opt/bin/chronicle", "--no-sync", *extra]) == 0
    return [q for q in asked if "menu bar" in q]


def test_install_asks_once_and_defaults_to_no(setup, monkeypatch, capsys):
    from chronicle.config import load_config

    asked = _install(monkeypatch, {"Open": "n"})  # Enter: the default
    assert len(asked) == 1 and "[y/N]" in asked[0]
    assert load_config(setup["home"]).server_menu_bar is False
    assert "no menu-bar icon" in capsys.readouterr().out
    assert _install(monkeypatch, {"Open": "n"}) == []  # a re-run does not ask again


def test_install_turns_it_on_when_asked(setup, monkeypatch, capsys):
    from chronicle.config import load_config

    monkeypatch.setattr("importlib.util.find_spec", lambda name, *a: None if name == "AppKit" else object())
    assert len(_install(monkeypatch, {"menu bar": "y", "Open": "n"})) == 1
    assert load_config(setup["home"]).server_menu_bar is True
    assert "interlatch[app]" in capsys.readouterr().out  # without the app extra it says how to add it
    assert _install(monkeypatch, {"Open": "n"}) == []  # on already: nothing to ask


def test_install_flag_sets_it_without_asking(setup, monkeypatch):
    from chronicle.config import load_config

    assert _install(monkeypatch, {"Open": "n"}, "--menu-bar") == []
    assert load_config(setup["home"]).server_menu_bar is True
    assert _install(monkeypatch, {"Open": "n"}, "--no-menu-bar") == []
    assert load_config(setup["home"]).server_menu_bar is False


def test_install_does_not_ask_without_the_login_dashboard_a_terminal_or_a_mac(setup, monkeypatch):
    from chronicle.cli import main
    from chronicle.config import load_config

    assert _install(monkeypatch, {"Open": "n"}, "--no-ui") == []
    monkeypatch.setattr("platform.system", lambda: "Linux")
    assert _install(monkeypatch, {"Open": "n"}) == []
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    assert main(["install", "--exe", "/opt/bin/chronicle", "--no-sync", "--yes"]) == 0
    assert load_config(setup["home"]).server_menu_bar is False


# ------------------------------------------------------------------------------ the switch in Status › Recording
@pytest.fixture
def mac(env, monkeypatch):
    """A uv tool install from PyPI with the team extra, whose dashboard is the login item, without the icon yet."""
    import types

    from chronicle import install, menubar, update

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(update, "_method", {"kind": "uv", "label": "uv tool from PyPI", "source": None, "local": False,
                                            "extras": ["team"], "command": ["/opt/uv", "tool", "upgrade", "interlatch"]})
    monkeypatch.setattr(update, "RESTARTABLE", True)
    monkeypatch.setattr(menubar, "SHOWN", False)
    monkeypatch.setattr(menubar, "app_extra", lambda: False)
    monkeypatch.setenv("XPC_SERVICE_NAME", install.UI_LABEL)
    restarts, ran = [], []
    monkeypatch.setattr("threading.Timer", lambda delay, fn: types.SimpleNamespace(start=lambda: restarts.append(fn)))

    def run(cmd, **kw):
        if cmd[0] in ("launchctl", "systemctl"):  # status checks: no agent (as conftest answers)
            return types.SimpleNamespace(returncode=113, stdout="", stderr="")
        ran.append(cmd)
        return types.SimpleNamespace(returncode=env.get("uv_exit", 0), stdout="", stderr=env.get("uv_err", ""))

    monkeypatch.setattr("chronicle.menubar.subprocess.run", run)
    env.update(restarts=restarts, ran=ran)
    return env


def _on(env) -> bool:
    from chronicle.config import load_config

    return load_config(env["home"]).server_menu_bar


def test_the_extra_is_added_keeping_the_others_and_the_version(mac, monkeypatch):
    import sys

    from chronicle import __version__, menubar, update

    assert menubar.extra_command() == ["/opt/uv", "tool", "install", "--force", "--python",
                                       f"{sys.version_info[0]}.{sys.version_info[1]}", f"interlatch[app,team]=={__version__}"]
    info = menubar.setting_info(mac["cfg"])
    assert info["supported"] and info["can_install"] and not info["app_extra"] and not info["on"]
    assert info["listed_as"].startswith("python")  # what System Settings › Menu Bar calls it
    monkeypatch.setitem(update._method, "source", "/Users/me/agents-chronicle")  # a checkout: says how instead
    assert menubar.extra_command() is None and not menubar.setting_info(mac["cfg"])["can_install"]
    assert "interlatch[app]" in menubar.setting_info(mac["cfg"])["command"]


def test_no_switch_off_a_mac_or_in_the_app(mac, monkeypatch):
    from chronicle import menubar, update

    monkeypatch.setitem(update._method, "kind", "app")  # Chronicle.app always shows its icon
    assert menubar.setting_info(mac["cfg"]) == {"supported": False}
    monkeypatch.setitem(update._method, "kind", "uv")
    monkeypatch.setattr("sys.platform", "linux")
    assert menubar.setting_info(mac["cfg"]) == {"supported": False}


def test_turning_it_on_installs_the_missing_extra_then_restarts(mac):
    from chronicle import menubar, update

    said, expected = [], menubar.extra_command()
    out = menubar.apply_setting(mac["cfg"], True, said.append)
    assert mac["ran"] == [expected] and "interlatch[app,team]" in expected[-1]
    assert said == ["installing the app extra…"] and "restarting" in out
    assert _on(mac) and mac["restarts"] == [update.restart]


def test_a_failed_install_leaves_it_off(mac):
    from chronicle import menubar

    mac.update(uv_exit=2, uv_err="resolving…\nerror: no network")
    with pytest.raises(RuntimeError, match="error: no network"):
        menubar.apply_setting(mac["cfg"], True, lambda m: None)
    assert not _on(mac) and mac["restarts"] == []


def test_the_dashboard_restarts_only_when_the_icon_has_to_show_or_go(mac, monkeypatch):
    from chronicle import menubar

    monkeypatch.setattr(menubar, "app_extra", lambda: True)
    monkeypatch.setattr(menubar, "SHOWN", True)  # shown: turning it on changes nothing here
    assert menubar.apply_setting(mac["cfg"], True, lambda m: None) == "The menu-bar icon is on"
    assert mac["restarts"] == [] and mac["ran"] == []
    assert "restarting" in menubar.apply_setting(mac["cfg"], False, lambda m: None)  # shown: it has to go
    assert not _on(mac) and len(mac["restarts"]) == 1
    monkeypatch.delenv("XPC_SERVICE_NAME")  # a dashboard started in a terminal never shows it: just saved
    monkeypatch.setattr(menubar, "SHOWN", False)
    out = menubar.apply_setting(mac["cfg"], True, lambda m: None)
    assert out == "The menu-bar icon is on; it shows while the dashboard runs at login"
    assert _on(mac) and len(mac["restarts"]) == 1


def test_the_switch_works_only_from_this_computer(mac, monkeypatch):
    import json
    import threading
    import time
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from chronicle import menubar, update
    from chronicle.server import App, make_handler

    app = App(mac["cfg"])
    assert app.status()["menu_bar"]["supported"]
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def post(body, **headers):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/menu-bar", data=json.dumps(body).encode(),
                                     headers={"X-Interlatch": "1", "Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    try:
        assert post({"on": True}, **{"X-Forwarded-For": "100.64.0.7"})[0] == 403  # installs software: not from afar
        assert mac["ran"] == []
        code, r = post({"on": True})
        assert code == 200 and r == {"started": True, "restarts": True}
        for _ in range(100):
            if app.jobs.snapshot()["menu-bar"]["state"] != "running":
                break
            time.sleep(0.05)
        assert app.jobs.snapshot()["menu-bar"]["state"] == "done" and app.cfg.server_menu_bar
        assert mac["restarts"] == [update.restart]
        monkeypatch.setattr(update, "_method", {"kind": "pip", "label": "pip", "command": None})
        monkeypatch.setattr(menubar, "app_extra", lambda: False)
        code, r = post({"on": True})  # nothing it can install with: it says how instead
        assert not r["started"] and "interlatch[app]" in r["error"]
    finally:
        httpd.shutdown()


def test_the_login_item_restarts_through_launchd(monkeypatch):
    """Re-executed in place (same pid), the login item came back with its menu-bar icon hidden by macOS; a process
    launchd starts afresh shows it. Elsewhere the dashboard still replaces itself in place."""
    import os
    import subprocess

    from chronicle import install, update

    started, execd = [], []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw.get("start_new_session"))))
    monkeypatch.setattr(os, "execv", lambda *a: execd.append(a))
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv("XPC_SERVICE_NAME", install.UI_LABEL)
    update.restart()
    assert started == [(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{install.UI_LABEL}"], True)] and not execd
    monkeypatch.delenv("XPC_SERVICE_NAME")  # a dashboard started in a terminal
    update.restart()
    assert len(started) == 1 and len(execd) == 1
    monkeypatch.setenv("XPC_SERVICE_NAME", install.UI_LABEL)
    monkeypatch.setattr("sys.platform", "linux")  # systemd: in place, as before
    update.restart()
    assert len(started) == 1 and len(execd) == 2
