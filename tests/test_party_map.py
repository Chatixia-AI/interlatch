"""The Adventure (knowledge/party.py adventure_map, encounter, set_cleared; GET /api/party/map, GET /api/party/encounter,
POST /api/party/cleared): the map's regions, lessons and bosses, one lesson as an encounter, and the log of cleared
encounters each viewer keeps for themselves, all behind the Party's sign-in and in the viewer's scope. The small
catalog of test_party.py stands in for roadmap.json."""

from __future__ import annotations

import json

import pytest

from interlatch.core import i18n
from interlatch.core.agents import CODING_AGENTS
from interlatch.knowledge import party
from interlatch.sharing import people
from interlatch.storage.kv import kv_get

from conftest import SID
from test_leaderboard import _joined
from test_party import ATTRS, BOB_PC, TEAMMATE_PC, _lesson, _session, catalog, home  # noqa: F401  (the fixtures)
from test_people_web import _as, hubweb, team  # noqa: F401  (the fixtures)
from test_people_web import _call as _http

LESSON_KEYS = {"id", "title", "kind", "stage", "agent", "at", "xp", "modules", "session_id", "project", "study", "friction"}


def _counts(data):
    return {m["id"]: (m["lessons"], m["xp"]) for r in data["regions"] for m in r["modules"]}


# ------------------------------------------------------------------ the map
def test_the_map_has_every_region_and_module_and_every_counted_lesson_oldest_first(home):  # noqa: F811
    conn, here = home["conn"], home["here"]
    old = _session(conn, at="2026-09-01T10:00:00.000Z")
    new = _session(conn, at="2026-10-01T10:00:00.000Z", agent="codex")
    first = _lesson(conn, old, "pytest and SQL query", stage="established")  # sql 2·2, python 2·1: it stands in sql
    long = _lesson(conn, old, "SQL query " + "x" * 300, stage="canonical")
    _lesson(conn, old, "a note that names no module")  # not counted: not on the map
    last = _lesson(conn, new, "deploy it", agent="codex", project="/Users/test/Projects/other-app")
    data = party.adventure_map(conn, here)
    assert set(data) == {"regions", "lessons", "cleared", "bosses", "newest", "agents", "gold", "towns", "goal", "understood",
                         "readiness", "ready_at", "studying"}
    assert [r["id"] for r in data["regions"]] == list(ATTRS)
    craft = data["regions"][0]
    assert {k: craft[k] for k in ("name", "about")} == {"name": "Craft", "about": "about craft"}
    assert [r["modules"] for r in data["regions"] if r["id"] == "lore"] == [[]]  # no module there: an empty region
    # every module, the empty ones too; a lesson stands in its best module, with all the XP it earned
    assert _counts(data) == {"M-python": (0, 0), "M-sql": (2, 12), "M-auth": (0, 0), "M-deploy": (1, 1), "M-llm": (0, 0)}
    assert [x["id"] for x in data["lessons"]] == [first, long, last]
    k = data["lessons"][0]
    assert set(k) == LESSON_KEYS and k == {
        "id": first, "title": "pytest and SQL query", "kind": "fix", "stage": "established", "agent": "claude",
        "at": "2026-09-01", "xp": 6, "modules": ["M-sql", "M-python"], "session_id": old, "project": "demo-app",
        "study": False, "friction": False}
    assert len(data["lessons"][1]["title"]) == party.MAP_TITLE_MAX and data["lessons"][2]["project"] == "other-app"
    assert data["newest"] == last and data["cleared"] == []
    assert [a["agent"] for a in data["agents"]] == list(CODING_AGENTS)
    assert data["agents"][0] == {"agent": "claude", "name": "Claude Code", "short": "Claude",
                                 "level": party.char_level(12), "tier": "novice"}
    token = i18n.lang.set("ja")
    try:
        ja = party.adventure_map(conn, here)
    finally:
        i18n.lang.reset(token)
    assert ja["regions"][0]["name"] == "craft（日本語）" and ja["regions"][0]["modules"][0]["name"] == "M-python（日本語）"
    json.dumps(data)


def test_an_empty_map_still_has_its_regions(home):  # noqa: F811
    data = party.adventure_map(home["conn"], home["here"])
    assert data["lessons"] == [] and data["newest"] is None and data["bosses"] == []
    assert sum(len(r["modules"]) for r in data["regions"]) == len(home["catalog"].modules)


def test_the_bosses_are_the_main_quests_milestones(home):  # noqa: F811
    conn, here = home["conn"], home["here"]
    sid = _session(conn)
    _lesson(conn, sid, "adding an index", "for the SQL")  # evidence for backend-G2
    party.set_goal(conn, None, "backend")
    party.set_quest(conn, None, "backend-G1", True)
    party.set_quest(conn, None, "sre-G1", True)  # not the main quest: no boss
    bosses = party.adventure_map(conn, here)["bosses"]
    assert [b["id"] for b in bosses] == [f"backend-G{n}" for n in (1, 2, 3, 4)]
    assert bosses[0] == {"id": "backend-G1", "n": 1, "text": "backend milestone 1", "track": "backend",
                         "track_name": "backend track", "attr": "data", "status": "done"}  # sql is critical: weighs 2
    assert [b["status"] for b in bosses] == ["done", "evidence", "open", "open"]
    party.set_goal(conn, None, "sre")  # deploy (ops) and auth (guard) weigh alike: the first attr in catalog order
    bosses = party.adventure_map(conn, here)["bosses"]
    assert {b["attr"] for b in bosses} == {"ops"} and bosses[0]["status"] == "done"
    token = i18n.lang.set("ja")
    try:
        assert party.adventure_map(conn, here)["bosses"][0]["text"] == "sre マイルストーン 1"
    finally:
        i18n.lang.reset(token)


# ------------------------------------------------------------------ an encounter
def test_an_encounter_is_one_lesson_with_its_case_and_the_party_that_meets_it(home):  # noqa: F811
    conn, here = home["conn"], home["here"]
    sid = _session(conn, at="2026-10-14T08:00:00.000Z")
    for _ in range(3):
        _lesson(conn, sid, "pytest", stage="canonical")  # python 18 for claude: skill 3
    lid = _lesson(conn, sid, "pytest and a token", "y" * 5000, kind="gotcha", stage="established")
    case = {"scene": "the tests pass alone", "question": "why?", "answer": "a shared token", "ruled_out": []}
    conn.execute("UPDATE knowledge SET case_json = ? WHERE id = ?", (json.dumps(case), lid))
    conn.commit()
    got = party.encounter(conn, here, None, lid)
    assert set(got) == {"lesson", "party", "cleared", "items"} and got["cleared"] is False and got["items"] == []
    k = got["lesson"]
    assert {x: k[x] for x in ("id", "title", "kind", "stage", "agent", "at", "xp", "session_id", "project")} == {
        "id": lid, "title": "pytest and a token", "kind": "gotcha", "stage": "established", "agent": "claude",
        "at": "2026-10-14", "xp": 6, "session_id": sid, "project": "demo-app"}
    assert len(k["body"]) == party.ENCOUNTER_BODY_MAX
    assert k["case"] == {"scene": "the tests pass alone", "question": "why?", "answer": "a shared token"}
    assert k["modules"] == [{"id": "M-python", "name": "Python", "attr": "craft"},
                            {"id": "M-auth", "name": "Auth", "attr": "guard"}]
    assert [p["agent"] for p in got["party"]] == list(CODING_AGENTS)
    claude, codex = got["party"][0], got["party"][1]
    assert set(claude) == {"agent", "name", "short", "level", "tier", "attr_levels", "skills", "gear"}
    assert claude["skills"] == {"M-python": party.skill_level(18 + 3), "M-auth": party.skill_level(3)}
    assert claude["attr_levels"]["craft"] == party.skill_level(21) and set(claude["attr_levels"]) == set(ATTRS)
    assert codex["skills"] == {"M-python": 0, "M-auth": 0} and codex["level"] == 1
    # a case file without its answer isn't one; a lesson that counts toward nothing still is an encounter
    conn.execute("UPDATE knowledge SET case_json = ? WHERE id = ?", (json.dumps({"scene": "s", "question": "q"}), lid))
    plain = _lesson(conn, sid, "a note that names no module")
    conn.commit()
    assert party.encounter(conn, here, None, lid)["lesson"]["case"] is None
    other = party.encounter(conn, here, None, plain)
    assert other["lesson"]["modules"] == [] and other["lesson"]["xp"] == 0 and other["party"][0]["skills"] == {}
    assert party.encounter(conn, here, None, 999999) is None
    _lesson(conn, sid, "pytest unsure", stage="wip")
    wip = conn.execute("SELECT MAX(id) FROM knowledge").fetchone()[0]
    assert party.encounter(conn, here, None, wip) is None  # not a lesson the Party knows
    token = i18n.lang.set("ja")
    try:
        assert party.encounter(conn, here, None, lid)["lesson"]["modules"][0]["name"] == "M-python（日本語）"
    finally:
        i18n.lang.reset(token)


# ------------------------------------------------------------------ whose lessons
def test_a_hub_person_meets_and_clears_only_their_own_computers_lessons(home):  # noqa: F811
    conn, here = home["conn"], home["here"]
    ada = people.add(conn, "Ada", "ada@example.com", "admin")
    bob = people.add(conn, "Bob", "bob@example.com", "member", by=ada)
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'bob-pc', 'spoke', ?)", (BOB_PC, bob["id"]))
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'ada-pc', 'spoke', ?)", (TEAMMATE_PC, ada["id"]))
    conn.commit()
    mine = _lesson(conn, _session(conn, machine=here), "pytest at the hub itself")
    bobs = _lesson(conn, _session(conn, machine=BOB_PC, source="remote"), "SQL query on bob's computer")
    adas = _lesson(conn, _session(conn, machine=TEAMMATE_PC, source="remote"), "deploy on ada's")
    assert [k["id"] for k in party.adventure_map(conn, here, bob)["lessons"]] == [bobs]
    assert [k["id"] for k in party.adventure_map(conn, here, ada)["lessons"]] == [adas]
    assert [k["id"] for k in party.adventure_map(conn, here)["lessons"]] == [mine]
    assert party.encounter(conn, here, bob, adas) is None and party.encounter(conn, here, bob, bobs) is not None
    with pytest.raises(party.PartyError, match="no such lesson"):
        party.set_cleared(conn, here, bob, adas, True)
    assert party.set_cleared(conn, here, bob, bobs, True) == [bobs]
    assert party.set_cleared(conn, here, None, mine, True) == [mine]
    assert json.loads(kv_get(conn, f"party.cleared:person:{bob['id']}")) == [bobs]
    assert json.loads(kv_get(conn, "party.cleared:here")) == [mine]
    assert party.adventure_map(conn, here, bob)["cleared"] == [bobs] and party.adventure_map(conn, here, ada)["cleared"] == []
    assert party.encounter(conn, here, bob, bobs)["cleared"] is True


def test_the_cleared_log_dedupes_validates_and_shows_at_once(home, monkeypatch):  # noqa: F811
    conn, here = home["conn"], home["here"]
    sid = _session(conn)
    a, b, c = (_lesson(conn, sid, f"pytest {n}") for n in range(3))
    party.adventure_map(conn, here)  # cached: the next answer still has the newest log
    assert party.set_cleared(conn, here, None, a, True) == [a]
    assert party.set_cleared(conn, here, None, a, True) == [a]  # once
    assert party.set_cleared(conn, here, None, b, True) == [a, b]
    assert party.adventure_map(conn, here)["cleared"] == [a, b]
    assert party.set_cleared(conn, here, None, a, False) == [b]
    assert party.set_cleared(conn, here, None, c, False) == [b]  # not in the log: nothing to take out
    for bad in (None, "1", True, 1.0, 999999, [a]):
        with pytest.raises(party.PartyError):
            party.set_cleared(conn, here, None, bad, True)
    monkeypatch.setattr(party, "CLEARED_MAX", 2)
    assert party.set_cleared(conn, here, None, a, True) == [b, a]
    assert party.set_cleared(conn, here, None, c, True) == [a, c]  # the oldest goes
    # a lesson gone from the Party since it was cleared can still be taken out of the log
    conn.execute("UPDATE knowledge SET status = 'dismissed', updated_at = '2026-10-02T00:00:00.000Z' WHERE id = ?", (c,))
    conn.commit()
    with pytest.raises(party.PartyError):
        party.set_cleared(conn, here, None, c, True)
    assert party.set_cleared(conn, here, None, c, False) == [a]
    conn.execute("UPDATE kv SET value = '{\"not\": \"a list\"}' WHERE key = 'party.cleared:here'")
    assert party.cleared_of(conn, "here") == []


# ------------------------------------------------------------------ the dashboard
def test_the_adventure_is_behind_the_partys_sign_in(hubweb, catalog):  # noqa: F811
    url, cfg, conn = hubweb["url"], hubweb["cfg"], hubweb["conn"]
    lid = _lesson(conn, SID, "pytest secrets")
    for path, body in (("/api/party/map", None), (f"/api/party/encounter?id={lid}", None),
                       ("/api/party/cleared", {"id": lid, "cleared": True})):
        status, r = _http(url, path, body)
        assert status == 403 and r["signin"] is True and "pytest" not in json.dumps(r), path
    assert kv_get(conn, "party.cleared:here") is None
    _joined(cfg, board=False)
    status, data = _http(url, "/api/party/map")
    assert status == 200 and [k["id"] for k in data["lessons"]] == [lid] and data["cleared"] == []
    status, enc = _http(url, f"/api/party/encounter?id={lid}")
    assert status == 200 and enc["lesson"]["title"] == "pytest secrets" and enc["cleared"] is False
    for q in ("999999", "abc", ""):
        assert _http(url, f"/api/party/encounter?id={q}")[0] == 404, q
    assert _http(url, "/api/party/encounter")[0] == 404
    status, r = _http(url, "/api/party/cleared", {"id": lid, "cleared": True})
    assert status == 200 and r["cleared"] == [lid] and r["reward"]["gold"] == 12 and r["bag"]["gold"] == 12
    assert _http(url, "/api/party/map")[1]["cleared"] == [lid]  # at once
    assert _http(url, f"/api/party/encounter?id={lid}")[1]["cleared"] is True
    status, r = _http(url, "/api/party/cleared", {"id": 999999, "cleared": True})
    assert status == 400 and r["error"] == "no such lesson"
    assert _http(url, "/api/party/cleared", {"id": str(lid), "cleared": True})[0] == 400
    status, r = _http(url, "/api/party/cleared", {"id": lid, "cleared": False})
    assert status == 200 and r["cleared"] == [] and r["reward"] is None and r["bag"]["gold"] == 12


def test_each_hub_person_keeps_their_own_cleared_log(team, catalog):  # noqa: F811
    url, cfg, conn, s = team["url"], team["cfg"], team["conn"], team["sessions"]
    _joined(cfg, board=False)
    bob = team["bob"]
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'bob-pc', 'spoke', ?)", (BOB_PC, bob["id"]))
    conn.commit()
    bobs = _lesson(conn, _session(conn, machine=BOB_PC, source="remote"), "SQL query on bob's computer")
    here = _lesson(conn, SID, "pytest at the hub")
    status, data = _http(url, "/api/party/map", headers=_as(s["Bob"]))
    assert status == 200 and [k["id"] for k in data["lessons"]] == [bobs]
    assert _http(url, f"/api/party/encounter?id={here}", headers=_as(s["Bob"]))[0] == 404  # not one of Bob's
    assert _http(url, "/api/party/cleared", {"id": here, "cleared": True}, _as(s["Bob"]))[0] == 400
    # a member POSTs their own log (not only admins may)
    assert _http(url, "/api/party/cleared", {"id": bobs, "cleared": True}, _as(s["Bob"]))[1]["cleared"] == [bobs]
    assert json.loads(kv_get(conn, f"party.cleared:person:{bob['id']}")) == [bobs]
    assert _http(url, "/api/party/map")[1]["cleared"] == []  # this computer's log is its own
    assert _http(url, "/api/party/cleared", {"id": here, "cleared": True})[1]["cleared"] == [here]
    assert _http(url, "/api/party/map", headers=_as(s["Bob"]))[1]["cleared"] == [bobs]
    assert _http(url, "/api/party/cleared", {"id": bobs, "cleared": True}, _as(s["Vic"]))[0] == 400  # Vic's own scope
