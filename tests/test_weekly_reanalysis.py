"""Weekly re-analysis (knowledge.weekly): turned on per project or group, the background worker refreshes each one once
a week on the chosen day, with no model call in a week with nothing new, and without [synthesis] auto."""

from datetime import datetime, timedelta

from conftest import CWD, fake_log
from test_group_graph import _world

from interlatch.application.workflows import run_worker
from interlatch.core.util import dumps, loads, to_iso, utcnow
from interlatch.interfaces.web import api
from interlatch.interfaces.web.app import App
from interlatch.knowledge import weekly
from interlatch.projects import groups
from interlatch.storage.kv import kv_get, kv_set


def _age(conn, kind, target, days=8):
    """Move the last run back, as if a week had passed."""
    key = f"weekly:{kind}:{target}"
    rec = loads(kv_get(conn, key))
    rec["last_run"] = to_iso(utcnow() - timedelta(days=days))
    kv_set(conn, key, dumps(rec))
    conn.commit()


def _today(cfg):
    """Make today the re-analysis day, so what is turned on is due at once."""
    cfg.synthesis.weekly_day = weekly.DAYS[datetime.now().astimezone().weekday()]


def _add_item(conn, title):
    conn.execute("INSERT INTO knowledge(project_path, project_name, kind, title, body, confidence, source) "
                 "VALUES (?, 'demo-app', 'gotcha', ?, 'body', 0.9, 'manual')", (CWD, title))
    conn.commit()


def test_a_project_refreshes_weekly_and_only_when_it_has_something_new(synced):
    cfg, conn = synced["cfg"], synced["conn"]
    assert CWD in run_worker(cfg, export=None).synthesized  # its first knowledge base, the usual way
    cfg.synthesis.auto, cfg.synthesis.min_new_items = False, 50  # nothing would refresh it by itself now
    _today(cfg)
    calls = len(fake_log(synced))

    assert weekly.status(conn, cfg, "project", CWD) == {"on": False}
    on = weekly.turn(conn, cfg, "project", CWD, True)
    assert on["on"] and on["last_run"] is None and on["next"] is None
    assert weekly.due(conn, cfg) == [("project", CWD)]

    # nothing new since the knowledge base: this week's run costs no model call, and counts
    report = run_worker(cfg, analyze=False, export=None)
    assert not report.synthesized and len(fake_log(synced)) == calls
    st = weekly.status(conn, cfg, "project", CWD)
    assert st["result"] == "unchanged" and st["last_run"] and st["next"] > st["last_run"]
    assert weekly.due(conn, cfg) == []

    # a new item, but the week isn't up: nothing runs
    _add_item(conn, "New gotcha")
    assert not run_worker(cfg, analyze=False, export=None).synthesized

    # a week later it re-synthesizes, though below min_new_items and with [synthesis] auto off
    _age(conn, "project", CWD)
    report = run_worker(cfg, analyze=False, export=None)
    assert report.synthesized == [CWD] and not report.failed
    st = weekly.status(conn, cfg, "project", CWD)
    assert st["result"] == "refreshed" and st["detail"] == {"projects": 1, "graph": False, "error": None}
    assert len(fake_log(synced)) > calls

    # off: never due again; on again (on the day itself) starts over, at the next sync
    assert weekly.turn(conn, cfg, "project", CWD, False) == {"on": False}
    assert weekly.due(conn, cfg) == [] and kv_get(conn, f"weekly:project:{CWD}") is None
    assert weekly.turn(conn, cfg, "project", CWD, True)["last_run"] is None


def test_a_run_for_specific_sessions_leaves_the_weekly_refresh_for_later(synced):
    cfg, conn = synced["cfg"], synced["conn"]
    cfg.synthesis.auto = False
    _today(cfg)
    weekly.turn(conn, cfg, "project", CWD, True)
    run_worker(cfg, session_ids=[], analyze=False, export=None)  # an empty list is a background run
    assert weekly.status(conn, cfg, "project", CWD)["last_run"]
    _age(conn, "project", CWD)
    run_worker(cfg, session_ids=["some-session"], analyze=False, export=None)
    assert weekly.due(conn, cfg) == [("project", CWD)]


def test_a_group_checks_its_links_again_only_when_they_changed(synced):
    cfg, conn = synced["cfg"], synced["conn"]
    cfg.synthesis.auto = False
    _today(cfg)
    gid = _world(conn)
    weekly.turn(conn, cfg, "group", gid, True)

    report = run_worker(cfg, analyze=False, export=None)  # never drawn: the first run draws it
    assert not report.failed
    st = weekly.status(conn, cfg, "group", gid)
    assert st["result"] == "refreshed" and st["detail"]["graph"] is True
    assert len(loads(kv_get(conn, f"group_graph:{gid}"))["links"]) == 2
    calls = len(fake_log(synced))

    _age(conn, "group", gid)  # the same candidate links a week later: no model call
    run_worker(cfg, analyze=False, export=None)
    assert weekly.status(conn, cfg, "group", gid)["result"] == "unchanged" and len(fake_log(synced)) == calls

    conn.execute("UPDATE glossary SET related_json = '[\"Quote API\", \"Postgres\"]' WHERE term = 'pytest'")
    conn.commit()
    _age(conn, "group", gid)  # a new candidate link: checked again
    run_worker(cfg, analyze=False, export=None)
    assert weekly.status(conn, cfg, "group", gid)["detail"]["graph"] is True and len(fake_log(synced)) > calls

    # a removed group's weekly re-analysis goes with it
    _age(conn, "group", gid)
    groups.delete(conn, gid)
    assert weekly.due(conn, cfg) == [] and kv_get(conn, f"weekly:group:{gid}") is None


def test_it_runs_on_the_chosen_day_and_catches_up_after(synced):
    cfg, conn = synced["cfg"], synced["conn"]
    cfg.synthesis.weekly_day = "wed"
    local = lambda *a: datetime(*a).astimezone()  # noqa: E731  this computer's time zone, as the day is
    weekly.turn(conn, cfg, "project", CWD, True)
    key = f"weekly:project:{CWD}"

    def at(now, since, last=None):
        rec = loads(kv_get(conn, key))
        rec.update(since=to_iso(since), last_run=to_iso(last) if last else None)
        kv_set(conn, key, dumps(rec))
        conn.commit()
        return weekly.due(conn, cfg, now) == [("project", CWD)], weekly._view(cfg, rec, now)["next"]

    # 2026-10-21 is a Wednesday. Turned on a Monday: not before Wednesday's first minute, then due
    assert at(local(2026, 10, 20, 23, 59), local(2026, 10, 19, 9)) == (False, to_iso(local(2026, 10, 21)))
    assert at(local(2026, 10, 21, 0, 1), local(2026, 10, 19, 9)) == (True, None)
    # turned on on the Wednesday itself: runs that day
    assert at(local(2026, 10, 21, 15), local(2026, 10, 21, 14))[0] is True
    # ran Wednesday morning: done until next Wednesday
    assert at(local(2026, 10, 24, 12), local(2026, 10, 19), local(2026, 10, 21, 8)) == (False, to_iso(local(2026, 10, 28)))
    # asleep all of Wednesday the 28th: caught up on Friday
    assert at(local(2026, 10, 30, 7), local(2026, 10, 19), local(2026, 10, 21, 8))[0] is True
    # a day name it doesn't know falls back to Monday
    cfg.synthesis.weekly_day = "someday"
    assert weekly.day_of(cfg) == "mon"


def test_settings_lists_them_and_sets_the_day(synced, monkeypatch):
    cfg, conn = synced["cfg"], synced["conn"]
    gid = _world(conn)
    ctx = App(cfg).context()
    got = api.projects.reanalysis(ctx)
    assert got["day"] == "mon" and got["days"][0] == "mon" and got["items"] == [] and not got["on_hub"]
    assert {"id": gid, "name": "Aktio"} in got["choices"]["groups"]
    assert CWD in [p["path"] for p in got["choices"]["projects"]]

    api.projects.action_reanalyze(ctx, "group", gid, True)
    api.projects.action_reanalyze(ctx, "project", CWD, True)
    got = api.projects.reanalysis(ctx)
    assert [(i["kind"], i["target"]) for i in got["items"]] == [("project", CWD), ("group", gid)]
    assert got["items"][1]["name"] == "Aktio" and got["items"][1]["on"]
    assert not got["choices"]["groups"] and CWD not in [p["path"] for p in got["choices"]["projects"]]

    got = api.settings.action_weekly_day(ctx, "fri")
    assert got["day"] == "fri" and "error" not in got
    assert 'weekly_day = "fri"' in (cfg.home / "config.toml").read_text()
    assert api.settings.action_weekly_day(ctx, "friday")["error"] and api.projects.reanalysis(ctx)["day"] == "fri"

    monkeypatch.setattr(type(ctx.cfg), "sends_files", property(lambda self: True))
    assert api.projects.reanalysis(ctx)["on_hub"] is True and api.projects.reanalysis(ctx)["items"] == []


def test_the_switch_on_project_and_group_pages(synced, monkeypatch):
    cfg, conn = synced["cfg"], synced["conn"]
    gid = _world(conn)
    ctx = App(cfg).context()
    assert api.projects.project(ctx, CWD)["reanalyze"] == {"on": False}
    assert api.projects.group(ctx, gid)["reanalyze"] == {"on": False}

    assert api.projects.action_reanalyze(ctx, "project", CWD, True)["on"] is True
    assert api.projects.action_reanalyze(ctx, "group", str(gid), True)["on"] is True
    assert api.projects.project(ctx, CWD)["reanalyze"]["on"] and api.projects.group(ctx, gid)["reanalyze"]["on"]
    assert api.projects.action_reanalyze(ctx, "group", gid, False) == {"on": False}

    for kind, target in (("project", "/no/such/project"), ("project", "__global__"), ("group", 999999),
                         ("group", "x"), ("everything", CWD), ("project", None)):
        assert api.projects.action_reanalyze(ctx, kind, target, True) is None

    # where the hub analyzes this computer's sessions there is nothing to turn on
    monkeypatch.setattr(type(cfg), "sends_files", property(lambda self: True))
    assert api.projects.project(ctx, CWD)["reanalyze"] is None
    assert api.projects.action_reanalyze(ctx, "project", CWD, True) is None
