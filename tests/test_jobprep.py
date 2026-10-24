"""Job monsters (knowledge/jobprep.py; the map's `prep`, GET/POST /api/party/prep): a job goal brings its own enemies,
written per requirement (more the bigger the gap), which roam its zones, fight like study battles and count toward the
module's gate, even where the person has no lessons of their own. A lit tower's monsters stop appearing."""

from __future__ import annotations

import json
from datetime import date

import pytest

from interlatch.analysis.runners import factory
from interlatch.knowledge import gates, hero, jobprep, jobs, party

from test_jobs import JD, MAPPING
from test_leaderboard import _joined
from test_party import _lesson, _session, catalog, home  # noqa: F401  (the fixtures)
from test_people_web import _as, hubweb, team  # noqa: F401  (the fixtures)
from test_people_web import _call as _http
from test_study import QUESTION, FakeModel
from interlatch.storage.kv import kv_get, kv_set

pytestmark = pytest.mark.usefixtures("game_on")  # Innerlatch: game mode is off by default

DAY = date(2026, 10, 20)


def SET(n, right=1):
    return {"questions": [QUESTION(right=right, question=f"Question {i}?") for i in range(n)]}


def _job(conn, cfg, cat):
    return jobs.add(conn, cfg, cat, None, title="", company="Acme", text=JD, runner=FakeModel(MAPPING))


def test_the_count_grows_with_the_gap():
    assert jobprep.count(need=3, have=0, must=True, own=0) == 10  # 2 + 6 + 2
    assert jobprep.count(need=2, have=0, must=False, own=0) == 6
    assert jobprep.count(need=2, have=1, must=False, own=0) == 4
    assert jobprep.count(need=1, have=1, must=False, own=50) == 3  # never fewer than COUNT_MIN
    assert jobprep.count(need=3, have=0, must=True, own=50) == 8  # plenty of their own: two fewer
    assert jobprep.count(need=4, have=0, must=True, own=0) == 10  # never more than COUNT_MAX


def test_monsters_are_written_per_requirement_must_haves_first(home):  # noqa: F811
    conn, cfg, here, cat = home["conn"], home["cfg"], home["here"], home["catalog"]
    _lesson(conn, _session(conn), "pytest flakes from a shared token", "Isolate the fixture", stage="canonical")
    _job(conn, cfg, cat)
    with pytest.raises(jobprep.PrepError, match="own computer"):
        jobprep.forge(conn, cfg, cat, here, here=False, runner=FakeModel())
    model = FakeModel({"questions": SET(10)["questions"] + [QUESTION(right=9)]})  # one that doesn't hold together
    got = jobprep.forge(conn, cfg, cat, here, here=True, runner=model)
    assert got == {"module": "M-python", "made": 10, "left": 2}  # python: rank 3, must-have, one own lesson
    assert "Write 10 questions" in model.prompts[0] and "Strong Python" in model.prompts[0]
    assert "pytest flakes from a shared token" in model.prompts[0]
    assert jobprep.forge(conn, cfg, cat, here, here=True, runner=FakeModel(SET(10)))["module"] == "M-sql"
    assert jobprep.forge(conn, cfg, cat, here, here=True, runner=FakeModel(SET(6)))["left"] == 0
    assert jobprep.forge(conn, cfg, cat, here, here=True, runner=FakeModel()) == {"module": None, "made": 0, "left": 0}
    assert jobs.view(conn, cat, here)["job"]["monsters"] == {"out": 26, "pending": 0}


def test_none_usable_is_asked_once_more_then_refused(home):  # noqa: F811
    conn, cfg, here, cat = home["conn"], home["cfg"], home["here"], home["catalog"]
    _job(conn, cfg, cat)
    bad = {"questions": [QUESTION(right=9)]}
    with pytest.raises(jobprep.PrepError, match="no usable questions"):
        jobprep.forge(conn, cfg, cat, here, here=True, runner=FakeModel(bad, bad))
    assert jobprep.sets_of(conn, jobs.load(conn)["active"]) == {}


def test_they_join_the_field_count_toward_the_gate_and_retire_once_lit(home):  # noqa: F811
    conn, cfg, here, cat = home["conn"], home["cfg"], home["here"], home["catalog"]
    job = _job(conn, cfg, cat)
    jobprep.forge(conn, cfg, cat, here, here=True, runner=FakeModel(SET(10)))  # python: no lessons of their own here
    party.clear_cache()
    m = party.adventure_map(conn, here, today=DAY)
    ids = [x["id"] for x in m["prep"]["lessons"]]
    assert len(ids) == 10 and all(x["prep"] and x["modules"] == ["M-python"] for x in m["prep"]["lessons"])
    assert m["prep"]["job"]["towers"][0]["module"] == "M-python" and m["prep"]["pending"] == 2
    assert m["readiness"].get("M-python", 0) == 0 and "M-python" not in m["gates"]
    hero.save(conn, None, "Ada", {})
    xp0 = hero.view(conn, here, None, party.compute(conn, here), today=DAY)["xp"]
    for i in ids[:5]:
        assert jobprep.pick(conn, i, 1, here=True, today=DAY)["verdict"] == "got_it"
    assert jobprep.pick(conn, ids[5], 0, here=True, today=DAY)["verdict"] == "not_yet"
    party.clear_cache()
    m = party.adventure_map(conn, here, today=DAY)
    assert m["readiness"]["M-python"] == 5 and m["gates"] == {"M-python": 1}  # job monsters open its gate
    assert sorted(m["prep"]["understood"]) == sorted(ids[:5])
    assert gates.readiness(conn, cat, here)["M-python"] == 5
    assert hero.view(conn, here, None, party.compute(conn, here), today=DAY)["xp"] == xp0 + 5 * hero.XP_FIRST
    with pytest.raises(jobprep.PrepError):
        jobprep.pick(conn, ids[0], 9, here=True, today=DAY)
    for bad in ("p:nope:M-python:0", "p:x", 3, f"p:{job['id']}:M-python:99"):
        with pytest.raises(jobprep.PrepError, match="no such"):
            jobprep.pick(conn, bad, 0, here=True, today=DAY)
    # python lit at rank 3: its monsters stop appearing, the record stays
    kv_set(conn, gates.GATES_KEY, json.dumps([{"module": "M-python", "rank": r, "on": "2026-10-01", "verdict": "passed"} for r in (1, 2, 3)]))
    party.clear_cache()
    assert party.adventure_map(conn, here, today=DAY)["prep"]["lessons"] == []
    jobs.remove(conn, None, job["id"])
    assert jobprep.sets_of(conn, job["id"]) == {} and len(jobprep.log_of(conn)) == 6


def test_an_encounter_looks_like_a_lessons_and_never_says_which_is_right(home):  # noqa: F811
    conn, cfg, here, cat = home["conn"], home["cfg"], home["here"], home["catalog"]
    job = _job(conn, cfg, cat)
    jobprep.forge(conn, cfg, cat, here, here=True, runner=FakeModel(SET(10)))
    mid = jobprep.monster_id(job["id"], "M-python", 0)
    e = jobprep.encounter(conn, cat, here, mid)
    assert e["lesson"]["id"] == mid and e["lesson"]["prep"] and e["lesson"]["modules"][0]["id"] == "M-python"
    assert e["lesson"]["body"] == "Strong Python, including async and testing" and e["party"]
    a = jobprep.ask(conn, mid)
    assert a["options"] == ["A flaky network", "A shared token", "A stale cache", "A slow disk"] and "right" not in json.dumps(a)


# ------------------------------------------------------------------ the dashboard
def test_job_monsters_over_http(hubweb, catalog, monkeypatch):  # noqa: F811
    url, cfg = hubweb["url"], hubweb["cfg"]
    assert _http(url, "/api/party/prep?id=p:x:M-python:0")[0] == 403  # not signed in
    _joined(cfg, board=False)
    monkeypatch.setattr(factory, "make_runner", lambda cfg: FakeModel(MAPPING))
    jid = _http(url, "/api/party/job", {"action": "add", "text": JD})[1]["active"]
    assert _http(url, "/api/party/jobs")[1]["job"]["monsters"] == {"out": 0, "pending": 3}
    monkeypatch.setattr(factory, "make_runner", lambda cfg: FakeModel(SET(10)))
    status, r = _http(url, "/api/party/prep", {"action": "forge"})
    assert status == 200 and r["module"] == "M-python" and r["left"] == 2
    mid = f"p:{jid}:M-python:0"
    status, r = _http(url, f"/api/party/prep?id={mid}")
    assert status == 200 and r["encounter"]["lesson"]["id"] == mid and len(r["ask"]["options"]) == 4
    assert _http(url, "/api/party/prep?id=p:nope:M-python:0")[0] == 404
    status, r = _http(url, "/api/party/prep", {"action": "pick", "id": mid, "choice": 1})
    assert status == 200 and r["verdict"] == "got_it" and r["readiness"]["M-python"] == 1
    assert _http(url, "/api/party/prep", {"action": "pick", "id": mid, "choice": 7})[0] == 400
    assert _http(url, "/api/party/prep", {"action": "fly"})[0] == 400


def test_a_hub_person_has_no_job_monsters(team, catalog, monkeypatch):  # noqa: F811
    url, cfg, conn, s = team["url"], team["cfg"], team["conn"], team["sessions"]
    _joined(cfg, board=False)
    monkeypatch.setattr(factory, "make_runner", lambda cfg: pytest.fail("no model call for a hub person"))
    for path, body in (("/api/party/prep?id=p:x:M-python:0", None), ("/api/party/prep", {"action": "forge"})):
        status, r = _http(url, path, body, _as(s["Bob"]))
        assert status == 403 and "own computer" in r["error"], path
    assert kv_get(conn, jobprep.PREP_KEY) is None
    assert _http(url, "/api/party/map", headers=_as(s["Bob"]))[1]["prep"] is None
