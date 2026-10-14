"""The team Home of a hub with people (/api/team): team projects only (set up on the hub, or sent to by other
computers), who did what, and what each viewer may see of it: admins get people, computers and invites; someone
limited to projects gets their projects only."""

from datetime import timedelta

from interlatch.sharing import people
from interlatch.core.config import load_config, set_config_value
from interlatch.core.util import to_iso, utcnow
from interlatch.core.machine import local_machine

from test_hub_projects import OTHER, PROJECTS, limited  # noqa: F401  (the fixture)
from test_people_web import MACHINE, _as, _call

BOB_SID = "88888888-2222-4333-8444-555555555555"
ELSEWHERE = "/work/elsewhere"  # a project only another computer sends to, outside Bob's
ELSE_SID = "66666666-2222-4333-8444-555555555555"
ELSE_MACHINE = "22222222-2222-4333-8444-555555555555"
SET_UP = "/work/SETUP-CANARY"  # set up on the hub, no sessions yet, not one of Bob's


def _remote(conn, sid, machine, project, title, lesson, *, days_ago=1):
    """A session another computer analyzed and shared, with one project lesson, `days_ago` days back."""
    at = to_iso(utcnow() - timedelta(days=days_ago))
    conn.execute("INSERT INTO sessions(id, source, agent, project_path, project_name, title, llm_title, analysis_status, "
                 "started_at, ended_at, active_s, machine_id) VALUES (?, 'remote', 'claude', ?, ?, ?, ?, 'done', ?, ?, 600, ?)",
                 (sid, project, project.rsplit("/", 1)[-1], title, title, at, at, machine))
    conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, confidence, "
                 "source, fingerprint, created_at, updated_at, status) VALUES (?, ?, ?, 'gotcha', ?, 'b', 'project', "
                 "'high', 'analysis', ?, ?, ?, 'active')", (sid, project, project.rsplit("/", 1)[-1], lesson, lesson, at, at))


def _team(env):
    """Bob's computer sends a session to PROJECTS; another computer, nobody's, sends one to ELSEWHERE."""
    conn = env["conn"]
    now = to_iso(utcnow())
    for mid, name in ((MACHINE, "bob-laptop"), (ELSE_MACHINE, "build-box")):
        conn.execute("INSERT INTO machines(id, name, role, first_seen, last_seen, last_push) VALUES (?, ?, 'spoke', ?, ?, ?)",
                     (mid, name, now, now, now))
    people.join_computer(conn, people.invite(conn, env["bob"]["id"], machine=MACHINE), MACHINE, "bob-laptop")
    _remote(conn, BOB_SID, MACHINE, PROJECTS, "Bob's session", "Bob's lesson")
    _remote(conn, ELSE_SID, ELSE_MACHINE, ELSEWHERE, "ELSE-CANARY session", "ELSE-CANARY lesson")
    conn.execute("INSERT INTO hub_projects(path, created_at) VALUES (?, ?)", (SET_UP, now))
    conn.commit()


def test_me_names_the_hub(limited):  # noqa: F811
    url, cfg = limited["url"], limited["cfg"]
    _, me = _call(url, "/api/me")
    assert me["hub"] == {"name": local_machine(cfg)["name"], "team": True, "address": None, "knowledge_only": False,
                         "dedicated": False}
    set_config_value(cfg, "hub", "name", '"Resona team"')
    limited["app"].cfg = load_config(cfg.home)
    _, me = _call(url, "/api/me")
    assert me["hub"]["name"] == "Resona team"
    _, me = _call(url, "/api/me", headers=_as(limited["bob_session"]))
    assert me["hub"]["team"] and not me["can_admin"]


def test_a_computer_that_is_no_hub_has_no_team(synced):
    from test_hub import _serve

    app, httpd, url = _serve(synced["cfg"])
    try:
        assert _call(url, "/api/me")[1]["hub"] is None
    finally:
        httpd.shutdown()


def test_the_team_home_for_an_admin(limited):  # noqa: F811
    _team(limited)
    code, r = _call(limited["url"], "/api/team")
    assert code == 200 and r["days"] == "7"
    # OTHER is a project only the hub computer works on: personal, so not on the team Home
    assert {p["path"] for p in r["projects"]} == {PROJECTS, ELSEWHERE, SET_UP}
    assert r["projects"][-1]["path"] == SET_UP and r["projects"][-1]["sessions"] == 0  # busiest first
    mine = next(p for p in r["projects"] if p["path"] == PROJECTS)
    assert mine["recent"] == 1 and mine["lessons"] == 1 and [x["who"] for x in mine["people"]] == ["Bob"]
    # who worked on what: each person's projects, and no count per person (sessions, time or lessons)
    assert {x["who"]: [p["path"] for p in x["projects"]] for x in r["who"]} == {"Bob": [PROJECTS], "build-box": [ELSEWHERE]}
    assert all(set(x) == {"who_key", "who", "last", "projects"} for x in r["who"])
    assert {k["title"] for k in r["lessons"][:2]} == {"Bob's lesson", "ELSE-CANARY lesson"}  # the newest first
    assert next(k for k in r["lessons"] if k["title"] == "Bob's lesson")["who"] == "Bob"
    for canary in ("OTHER-CANARY", "PREF-CANARY", "GLOBAL-CANARY"):  # personal work and lessons about a person
        assert canary not in str(r), canary
    assert r["totals"] == {"sessions": 2, "lessons": 2, "people": 2, "projects": 2, "team_projects": 3}
    comps = {c["name"]: c for c in r["computers"]}
    assert comps["bob-laptop"]["person"] == "Bob" and comps["build-box"]["person"] is None
    assert any(c["this"] for c in r["computers"])
    by = {p["name"]: p for p in r["people"]}
    assert by["Bob"]["computers"] == 1 and by["Bob"]["last_push"] and by["Ada"]["invites"] == []
    assert by["Bob"]["projects"] == [PROJECTS]

    _, r30 = _call(limited["url"], "/api/team?days=30")
    assert r30["days"] == "30"
    _, odd = _call(limited["url"], "/api/team?days=all")  # not a period of the team Home: the default
    assert odd["days"] == "7"


def test_old_work_counts_as_a_project_but_not_as_recent(limited):  # noqa: F811
    conn = limited["conn"]
    now = to_iso(utcnow())
    conn.execute("INSERT INTO machines(id, name, role, first_seen, last_seen) VALUES (?, 'old-box', 'spoke', ?, ?)",
                 (ELSE_MACHINE, now, now))
    _remote(conn, ELSE_SID, ELSE_MACHINE, ELSEWHERE, "old session", "old lesson", days_ago=40)
    conn.commit()
    _, r = _call(limited["url"], "/api/team")
    old = next(p for p in r["projects"] if p["path"] == ELSEWHERE)
    assert old["sessions"] == 1 and old["recent"] == 0 and old["lessons"] == 0 and old["people"] == []
    assert r["who"] == [] and r["totals"]["sessions"] == 0
    assert {k["title"] for k in r["lessons"]} == {"old lesson", "Resona lesson"}  # the latest, whatever the period
    _, r90 = _call(limited["url"], "/api/team?days=90")
    assert "old-box" in {x["who"] for x in r90["who"]}


def test_someone_limited_sees_their_projects_only(limited):  # noqa: F811
    _team(limited)
    code, r = _call(limited["url"], "/api/team", headers=_as(limited["bob_session"]))
    assert code == 200
    assert [p["path"] for p in r["projects"]] == [PROJECTS]
    assert r["people"] is None and r["computers"] is None
    assert [x["who"] for x in r["who"]] == ["Bob"]
    assert {k["title"] for k in r["lessons"]} == {"Bob's lesson", "Resona lesson"}
    text = str(r)
    for canary in ("ELSE-CANARY", "build-box", "SETUP-CANARY", "OTHER-CANARY", "PREF-CANARY", "GLOBAL-CANARY", "Ada"):
        assert canary not in text, canary


def test_a_member_with_every_project_sees_the_team_but_not_its_admin(limited):  # noqa: F811
    _team(limited)
    people.set_projects(limited["conn"], limited["bob"]["id"], None)
    _, r = _call(limited["url"], "/api/team", headers=_as(limited["bob_session"]))
    assert {p["path"] for p in r["projects"]} == {PROJECTS, ELSEWHERE, SET_UP}
    assert r["people"] is None and r["computers"] is None
    assert OTHER not in {p["path"] for p in r["projects"]}


def test_narrowing_sessions_and_lessons_to_a_person(limited):  # noqa: F811
    _team(limited)
    url, here = limited["url"], local_machine(limited["cfg"])
    bob = f"p:{limited['bob']['id']}"
    _, opts = _call(url, "/api/team/who")
    by = {x["key"]: x for x in opts["items"]}
    assert by[bob]["name"] == "Bob" and by[bob]["person"] and by[bob]["sessions"] == 1
    assert by[f"m:{ELSE_MACHINE}"]["name"] == "build-box" and not by[f"m:{ELSE_MACHINE}"]["person"]
    _, mine = _call(url, f"/api/sessions?who=m:{here['id']}")  # the hub's own, with or without a machine_id
    assert by[f"m:{here['id']}"]["sessions"] == mine["total"] >= 2

    _, r = _call(url, f"/api/sessions?who={bob}")
    assert [x["id"] for x in r["items"]] == [BOB_SID] and r["items"][0]["who"] == "Bob" and r["items"][0]["who_key"] == bob
    assert {x["who"] for x in mine["items"]} == {here["name"]} and OTHER in {x["project_path"] for x in mine["items"]}
    _, r = _call(url, "/api/sessions?who=p:1%20OR%201")  # not a key: nothing, never everything
    assert r["total"] == 0
    _, r = _call(url, f"/api/knowledge?who={bob}")
    assert [(k["title"], k["who"]) for k in r["items"]] == [("Bob's lesson", "Bob")]
    _, r = _call(url, f"/api/knowledge?who=m:{ELSE_MACHINE}")
    assert [k["title"] for k in r["items"]] == ["ELSE-CANARY lesson"]


def test_someone_limited_narrows_within_their_projects(limited):  # noqa: F811
    _team(limited)
    url, s = limited["url"], _as(limited["bob_session"])
    _, opts = _call(url, "/api/team/who", headers=s)
    assert "build-box" not in {x["name"] for x in opts["items"]} and "Bob" in {x["name"] for x in opts["items"]}
    _, r = _call(url, f"/api/sessions?who=m:{ELSE_MACHINE}", headers=s)
    assert r["total"] == 0
    _, r = _call(url, f"/api/knowledge?who=m:{ELSE_MACHINE}", headers=s)
    assert r["items"] == []
    assert "ELSE-CANARY" not in str(_call(url, f"/api/knowledge?who=p:{limited['bob']['id']}", headers=s)[1])


def test_no_who_on_a_hub_without_people(synced):
    from test_hub import _serve

    app, httpd, url = _serve(synced["cfg"])
    try:
        assert _call(url, "/api/team/who")[1] == {"items": []}
        items = _call(url, "/api/sessions")[1]["items"]
        assert items and all("who" not in x for x in items)
    finally:
        httpd.shutdown()
