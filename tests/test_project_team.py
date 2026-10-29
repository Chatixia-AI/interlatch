"""A project's page on a hub with people (/api/project → team, projects/team.py): whose sessions each lesson came from,
lessons two people hit separately, what changed since this person's last visit, who has worked on what with no count
per person, and where to start for someone new. Someone limited to projects gets only theirs."""

import json
from datetime import timedelta

from interlatch.core.machine import local_machine
from interlatch.core.util import to_iso, utcnow
from interlatch.projects.team import REVISIT, VISIT_KV
from interlatch.sharing import people

from test_hub_projects import PROJECTS, limited  # noqa: F401  (the fixture)
from test_people_web import MACHINE, _as, _call

BOB_SID = "88888888-2222-4333-8444-555555555551"
BOB_SID2 = "88888888-2222-4333-8444-555555555552"
BOX_SID = "66666666-2222-4333-8444-555555555551"
BOX = "22222222-2222-4333-8444-555555555555"  # a computer no one joined
ELSEWHERE = "/work/elsewhere"
ELSE_SID = "66666666-2222-4333-8444-555555555559"
RAW_SID = "66666666-2222-4333-8444-555555555558"


def _session(conn, sid, machine, project, title, *, days_ago=1, tags=(), outcome="completed"):
    at = to_iso(utcnow() - timedelta(days=days_ago))
    conn.execute("INSERT INTO sessions(id, source, agent, project_path, project_name, title, llm_title, analysis_status, "
                 "started_at, ended_at, ingested_at, active_s, machine_id, tags_json, outcome, prs_json) "
                 "VALUES (?, 'remote', 'claude', ?, ?, ?, ?, 'done', ?, ?, ?, 600, ?, ?, ?, ?)",
                 (sid, project, project.rsplit("/", 1)[-1], title, title, at, at, at, machine, json.dumps(list(tags)),
                  outcome, json.dumps([{"url": f"https://github.com/acme/x/pull/{sid[-1]}", "title": "PR"}])))


def _lesson(conn, sid, project, title, *, kind="gotcha", stage="provisional", confirmed=(), status="active"):
    at = to_iso(utcnow())
    cur = conn.execute(
        "INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, confidence, source, "
        "fingerprint, created_at, updated_at, status, stage, confirmed_json) VALUES (?, ?, ?, ?, ?, 'body', 'project', "
        "'high', 'analysis', ?, ?, ?, ?, ?, ?)",
        (sid, project, project.rsplit("/", 1)[-1], kind, title, title, at, at, status, stage, json.dumps([sid, *confirmed])))
    return cur.lastrowid


def _team(env):
    """Bob's computer and a build box no one joined both send PROJECTS sessions; the box also sends ELSEWHERE."""
    conn = env["conn"]
    now = to_iso(utcnow())
    for mid, name in ((MACHINE, "bob-laptop"), (BOX, "build-box")):
        conn.execute("INSERT INTO machines(id, name, role, first_seen, last_seen, last_push) VALUES (?, ?, 'spoke', ?, ?, ?)",
                     (mid, name, now, now, now))
    people.join_computer(conn, people.invite(conn, env["bob"]["id"], machine=MACHINE), MACHINE, "bob-laptop")
    _session(conn, BOB_SID, MACHINE, PROJECTS, "Bob fixes the deploy", days_ago=3, tags=["deploy", "ariba-api"])
    _session(conn, BOB_SID2, MACHINE, PROJECTS, "Bob again", days_ago=2, tags=["ariba-api"], outcome="blocked")
    _session(conn, BOX_SID, BOX, PROJECTS, "The box hits it too", days_ago=1, tags=["deploy"])
    _session(conn, ELSE_SID, BOX, ELSEWHERE, "ELSE-CANARY session", tags=["ELSE-CANARY"])
    # what someone limited to the project must still not see: a session not analyzed yet (its title is its first
    # prompt) and a lesson about the person rather than the project
    _session(conn, RAW_SID, BOX, PROJECTS, "PROMPT-CANARY", tags=["PROMPT-CANARY"])
    conn.execute("UPDATE sessions SET llm_title = NULL, analysis_status = 'pending' WHERE id = ?", (RAW_SID,))
    _lesson(conn, BOB_SID, PROJECTS, "PREF-CANARY", kind="preference")
    ids = {
        "shared": _lesson(conn, BOB_SID, PROJECTS, "Deploys need the role", stage="established", confirmed=[BOX_SID]),
        "bob": _lesson(conn, BOB_SID2, PROJECTS, "Ariba PUT needs the SPM token", kind="decision"),
        "else": _lesson(conn, ELSE_SID, ELSEWHERE, "ELSE-CANARY lesson"),
    }
    conn.commit()
    return ids


def _project(env, path=PROJECTS, headers=None):
    from urllib.parse import quote

    return _call(env["url"], f"/api/project?path={quote(path)}", headers=headers)


def test_each_lesson_names_whose_sessions_taught_it(limited):  # noqa: F811
    ids = _team(limited)
    code, p = _project(limited)
    assert code == 200
    team = p["team"]
    assert [x["who"] for x in team["learned_by"][str(ids["shared"])]] == ["Bob", "build-box"]
    assert [x["who"] for x in team["learned_by"][str(ids["bob"])]] == ["Bob"]  # each person once, first finder first
    assert str(ids["else"]) not in team["learned_by"]  # another project's lesson
    # two people hit it separately: side by side, each with the first session of theirs that states it
    (case,) = team["cases"]
    assert case["id"] == ids["shared"]
    assert [(x["who"], x["session"]["id"]) for x in case["people"]] == [("Bob", BOB_SID), ("build-box", BOX_SID)]


def test_who_has_worked_on_what_without_counting_anyone(limited):  # noqa: F811
    _team(limited)
    team = _project(limited)[1]["team"]
    hub = local_machine(limited["cfg"])["name"]  # the hub's own session, filed under PROJECTS when it was set up
    assert [x["who"] for x in team["people"]][:2] == ["build-box", "Bob"] and hub in [x["who"] for x in team["people"]]
    assert all(set(x) == {"who", "key", "last", "topics"} for x in team["people"])  # no sessions, time or lessons per person
    bob = next(x for x in team["people"] if x["who"] == "Bob")
    assert bob["topics"] == ["ariba-api", "deploy"]
    # a topic only one person has worked on, in more than one session
    assert team["solo"] == [{"topic": "ariba-api", "who": "Bob", "key": bob["key"]}]
    lanes = {x["who"]: [s["id"] for s in x["sessions"]] for x in team["lanes"]["people"] if x["who"] != hub}
    assert lanes == {"Bob": [BOB_SID, BOB_SID2], "build-box": [BOX_SID, RAW_SID]}  # an admin sees the one not analyzed


def test_since_your_last_visit(limited):  # noqa: F811
    ids = _team(limited)
    conn = limited["conn"]
    assert _project(limited)[1]["team"]["since"] is None  # a first visit
    assert _project(limited)[1]["team"]["since"] is None  # a reload is the same visit
    # an hour later: Bob's lesson is replaced as outdated, and a new one arrives
    key = next(k for (k,) in conn.execute("SELECT key FROM kv WHERE key LIKE ?", (f"{VISIT_KV}%",)))
    st = json.loads(conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()[0])
    st["seen"] = to_iso(utcnow() - REVISIT - timedelta(minutes=30))
    conn.execute("UPDATE kv SET value = ? WHERE key = ?", (json.dumps(st), key))
    new = _lesson(conn, BOX_SID, PROJECTS, "Use the SPM token from the vault", kind="decision")
    conn.execute("UPDATE knowledge SET status = 'superseded', superseded_by = ?, superseded_reason = 'outdated', "
                 "superseded_at = ? WHERE id = ?", (new, to_iso(utcnow()), ids["bob"]))
    conn.commit()
    news = _project(limited)[1]["team"]["since"]
    assert [x["id"] for x in news["lessons"]] == [new] and news["lessons_total"] == 1
    (moved,) = news["replaced"]
    assert moved["id"] == ids["bob"] and moved["reason"] == "outdated" and moved["by"]["id"] == new
    assert [x["who"] for x in moved["learned_by"]] == ["Bob"] and not moved["mine"]  # the admin at the hub isn't Bob


def test_start_here_puts_trusted_lessons_first(limited):  # noqa: F811
    ids = _team(limited)
    assert not _project(limited)[1]["team"]["viewer"]["new"]  # the hub computer has a session in PROJECTS
    ada = people.open_browser(limited["conn"], people.invite(limited["conn"], limited["ada"]["id"]), "test")[1]
    team = _project(limited, headers=_as(ada))[1]["team"]
    assert team["viewer"] == {"key": f"p:{limited['ada']['id']}", "new": True}  # Ada has none: Start here is for her
    assert team["start"]["gotchas"][0]["id"] == ids["shared"]  # established, ahead of the hub's own provisional ones
    assert [x["id"] for x in team["start"]["decisions"]] == [ids["bob"]]
    active = limited["conn"].execute("SELECT COUNT(*) FROM knowledge WHERE project_path = ? AND status = 'active'",
                                     (PROJECTS,)).fetchone()[0]
    assert team["kb_behind"] == active  # no knowledge base yet: every active lesson is waiting for one


def test_someone_limited_sees_only_their_project(limited):  # noqa: F811
    ids = _team(limited)
    conn = limited["conn"]
    code, p = _project(limited, headers=_as(limited["bob_session"]))
    assert code == 200
    team = p["team"]
    assert team["viewer"] == {"key": f"p:{limited['bob']['id']}", "new": False}  # Bob has sessions here
    seen = json.dumps(team)
    assert "ELSE-CANARY" not in seen and "PROMPT-CANARY" not in seen and "PREF-CANARY" not in seen
    assert str(ids["shared"]) in team["learned_by"]
    # another project: refused, and no visit recorded for it
    code, _ = _project(limited, ELSEWHERE, headers=_as(limited["bob_session"]))
    assert code == 404
    assert not conn.execute("SELECT 1 FROM kv WHERE key LIKE ?", (f"%{ELSEWHERE}",)).fetchone()


def test_pull_requests_are_counted_only_for_those_shown_them(limited):  # noqa: F811
    _team(limited)
    conn = limited["conn"]
    for who in (None, limited["bob_session"]):
        headers = _as(who) if who else None
        _project(limited, headers=headers)
        conn.execute("UPDATE kv SET value = json_set(value, '$.seen', ?, '$.kid', 0) WHERE key LIKE ?",
                     (to_iso(utcnow() - timedelta(days=5)), f"{VISIT_KV}%"))
        conn.commit()
        news = _project(limited, headers=headers)[1]["team"]["since"]
        assert news["prs"] == (None if who else 4)
        assert news["sessions"] == (3 if who else 5)  # the hub's own and the one not analyzed: not in Bob's view


def test_a_computer_that_is_no_team_hub_gets_no_team_side(synced):
    from test_hub import _serve
    from test_hub_projects import CWD

    app, httpd, url = _serve(synced["cfg"])
    try:
        from urllib.parse import quote

        code, p = _call(url, f"/api/project?path={quote(CWD)}")
        assert code == 200 and p["team"] is None
    finally:
        httpd.shutdown()
