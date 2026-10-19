"""Study battles (knowledge/study.py; GET /api/party/ask, POST /api/party/pick): a lesson's question with options
written for it by the model once and kept with the lesson (the right one and plausible wrong ones, each with why), the
person's first pick kept on their own computer only, and what they understood counted per roadmap module and brought
back as it would fade. The agents' levels never move."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from interlatch.analysis.runners import factory
from interlatch.knowledge import party, study
from interlatch.storage.kv import kv_get, kv_set

from conftest import SID
from test_leaderboard import _joined
from test_party import BOB_PC, _lesson, _session, catalog, home  # noqa: F401  (the fixtures)
from test_people_web import _as, hubweb, team  # noqa: F401  (the fixtures)
from test_people_web import _call as _http

pytestmark = pytest.mark.usefixtures("game_on")  # Innerlatch: game mode is off by default


class FakeModel:
    """Answers each call with the next reply, and keeps the prompts."""

    label = "Fake"

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def available(self):
        return True

    def unavailable_reason(self):
        return ""

    def run(self, prompt, schema, **kw):
        self.prompts.append(prompt)
        return SimpleNamespace(data=self.replies.pop(0))


def QUESTION(right=1, **over):
    """A written question as the model returns it: four options, `right` the right one."""
    opts = [{"text": t, "right": i == right, "why": f"why {t}"} for i, t in enumerate(("A flaky network", "A shared token",
                                                                                     "A stale cache", "A slow disk"))]
    return {"scene": "Tests pass alone", "question": "Why do they fail together?", "options": opts,
            "principle": "Isolate shared state", **over}


def _case(conn, lid, **case):
    conn.execute("UPDATE knowledge SET case_json = ? WHERE id = ?", (json.dumps(case), lid))
    conn.commit()


def _tags(conn, lid, tags, scope="project", stage=None):
    conn.execute("UPDATE knowledge SET tags_json = ?, scope = ?, stage = COALESCE(?, stage) WHERE id = ?",
                 (json.dumps(tags), scope, stage, lid))
    conn.commit()


# ------------------------------------------------------------------ which lessons
def test_study_material_and_the_agents_friction(home):  # noqa: F811
    conn = home["conn"]
    sid = _session(conn)
    cased = _lesson(conn, sid, "pytest flakes", kind="fix")
    _case(conn, cased, scene="s", question="q?", answer="a")
    principled = _lesson(conn, sid, "pytest pattern", kind="pattern")
    _case(conn, principled, principle="Mutation-check each fix")
    plain = _lesson(conn, sid, "pytest note", kind="learning")
    shell = _lesson(conn, sid, "rm is aliased", kind="gotcha")
    _tags(conn, shell, ["Shell", "zsh"])
    recurring = _lesson(conn, sid, "socket path too long", kind="gotcha")
    _tags(conn, recurring, ["macos"], scope="global", stage="canonical")
    once = _lesson(conn, sid, "socket path once", kind="gotcha")
    _tags(conn, once, ["macos"], scope="global")  # provisional: not (yet) a trap the agents keep hitting
    got = study.flags(conn, [cased, principled, plain, shell, recurring, once])
    assert got == {cased: (True, False), principled: (True, False), plain: (False, False), shell: (False, True),
                   recurring: (False, True), once: (False, False)}
    assert study.friction("fix", "global", "canonical", ["shell"]) is False  # only gotchas are the agents' traps


# ------------------------------------------------------------------ the question and its options
def test_a_lessons_question_and_options_are_written_once_from_its_own_material(home):  # noqa: F811
    conn, cfg = home["conn"], home["cfg"]
    sid = _session(conn)
    lid = _lesson(conn, sid, "pytest flakes on a token", "Two tests shared one token.", kind="gotcha")
    _case(conn, lid, scene="Tests pass alone", question="Why?", answer="A shared token", principle="Isolate state",
          ruled_out=[{"lead": "A flaky network", "why": "Offline runs failed too"}])
    model = FakeModel(QUESTION())
    got = study.ask(conn, cfg, lid, runner=model)
    assert got == {"scene": "Tests pass alone", "question": "Why do they fail together?", "options": QUESTION()["options"],
                   "principle": "Isolate shared state"}
    prompt = model.prompts[0]
    assert "pytest flakes on a token" in prompt and "A flaky network" in prompt and "Offline runs failed too" in prompt
    assert study.ask(conn, cfg, lid, runner=FakeModel()) == got  # kept: no second call
    assert json.loads(kv_get(conn, f"party.ask:{lid}"))["v"] == study.ASK_VERSION
    # a question kept in another shape (the typed-answer one) is written again
    kv_set(conn, f"party.ask:{lid}", json.dumps({"scene": "s", "question": "q", "answer": "a"}))
    assert study.ask(conn, cfg, lid, runner=FakeModel(QUESTION(right=2)))["options"][2]["right"] is True
    with pytest.raises(study.StudyError, match="no such lesson"):
        study.ask(conn, cfg, 999999, runner=model)


def test_options_that_dont_hold_together_are_asked_for_once_more_then_refused(home):  # noqa: F811
    conn, cfg = home["conn"], home["cfg"]
    lid = _lesson(conn, _session(conn), "pytest flakes", kind="fix")
    two_right = QUESTION()
    two_right["options"][0]["right"] = True
    three = QUESTION(options=QUESTION()["options"][:3])
    alike = QUESTION()
    alike["options"][3]["text"] = " a SHARED  token "
    for bad in (two_right, three, alike, QUESTION(scene=" "), QUESTION(right=9)):
        model = FakeModel(bad, QUESTION())
        assert study.ask(conn, cfg, lid, runner=model)["options"][1]["right"] is True and len(model.prompts) == 2
        conn.execute("DELETE FROM kv WHERE key = ?", (f"party.ask:{lid}",))
    with pytest.raises(study.StudyError, match="no usable question"):
        study.ask(conn, cfg, lid, runner=FakeModel(two_right, two_right))


# ------------------------------------------------------------------ the pick and the record
def test_the_first_pick_is_kept_here_only_and_counted_per_module(home):  # noqa: F811
    conn, cfg, here = home["conn"], home["cfg"], home["here"]
    lid = _lesson(conn, _session(conn), "pytest and a token", kind="gotcha", stage="established")
    before = party.compute(conn, here)["agents"]
    model = FakeModel(QUESTION())
    got = study.pick(conn, cfg, "here", lid, 0, here=True, runner=model, today=date(2026, 10, 17))
    assert got == {"verdict": "not_yet", "right": 1, "answer": "A shared token", "feedback": "why A flaky network",
                   "principle": "Isolate shared state", "back_in": 0, "owned": False}
    got = study.pick(conn, cfg, "here", lid, 1, here=True, runner=FakeModel(), today=date(2026, 10, 18))
    assert got["verdict"] == "got_it" and got["feedback"] == "why A shared token" and got["back_in"] == 3
    assert study.studied_of(conn, "here") == [{"id": lid, "on": "2026-10-17", "verdict": "not_yet"},
                                              {"id": lid, "on": "2026-10-18", "verdict": "got_it"}]
    m = party.adventure_map(conn, here, today=date(2026, 10, 18))
    assert m["understood"] == [lid] and m["readiness"] == {"M-python": 1} and m["ready_at"] == study.READY_AT
    party.clear_cache()
    assert party.compute(conn, here)["agents"] == before  # no level moved
    for bad in (None, -1, 4, "1", 1.0, True):
        with pytest.raises(study.StudyError, match="pick one"):
            study.pick(conn, cfg, "here", lid, bad, here=True, runner=FakeModel())
    with pytest.raises(study.StudyError, match="your own computer"):
        study.pick(conn, cfg, "person:1", lid, 1, here=False, runner=FakeModel())
    assert kv_get(conn, "party.study:person:1") is None


# ------------------------------------------------------------------ coming back
def _log(*answers, lid=1):
    return [{"id": lid, "on": on, "verdict": v} for on, v in answers]


def test_a_lesson_got_right_rests_then_comes_back_and_is_owned_after_two_days():
    day = date(2026, 10, 20)
    assert study.schedule(_log(("2026-10-17", "got_it")), day) == {
        1: {"streak": 1, "owned": False, "due": "2026-10-20", "resting": False}}  # 3 days: back today
    assert study.schedule(_log(("2026-10-18", "got_it")), day)[1]["resting"] is True
    two = study.schedule(_log(("2026-10-10", "got_it"), ("2026-10-14", "got_it")), day)[1]
    assert two == {"streak": 2, "owned": True, "due": "2026-10-24", "resting": True}  # then 10 days
    many = _log(*[(f"2026-0{m}-01", "got_it") for m in range(1, 7)])
    assert study.schedule(many, day)[1]["due"] == "2026-08-30"  # 90 days, at most
    # partly or not yet: back on the field at once, and the streak starts over
    assert study.schedule(_log(("2026-10-10", "got_it"), ("2026-10-14", "partly")), day) == {}
    again = study.schedule(_log(("2026-10-01", "got_it"), ("2026-10-05", "not_yet"), ("2026-10-19", "got_it")), day)[1]
    assert again["streak"] == 1 and again["owned"] is False and again["due"] == "2026-10-22"
    # two answers on one day are one: the day's last counts
    same = study.schedule(_log(("2026-10-19", "got_it"), ("2026-10-19", "got_it")), day)[1]
    assert same["streak"] == 1 and not same["owned"]
    assert study.schedule(_log(("2026-10-19", "got_it"), ("2026-10-19", "not_yet")), day) == {}
    assert study.schedule([], day) == {}


def test_the_map_brings_back_what_is_due_and_names_what_is_owned(home):  # noqa: F811
    conn, cfg, here = home["conn"], home["cfg"], home["here"]
    sid = _session(conn)
    a, b = _lesson(conn, sid, "pytest one", kind="fix"), _lesson(conn, sid, "pytest two", kind="fix")
    model = FakeModel(QUESTION(), QUESTION())
    got = study.pick(conn, cfg, "here", a, 1, here=True, runner=model, today=date(2026, 10, 1))
    assert got["back_in"] == 3 and got["owned"] is False
    got = study.pick(conn, cfg, "here", a, 1, here=True, runner=model, today=date(2026, 10, 4))
    assert got["back_in"] == 10 and got["owned"] is True
    study.pick(conn, cfg, "here", b, 1, here=True, runner=model, today=date(2026, 10, 10))
    m = party.adventure_map(conn, here, today=date(2026, 10, 14))
    assert m["understood"] == [a, b] and m["owned"] == [a] and m["back"] == [a, b]  # a: back on the 14th; b: on the 13th
    m = party.adventure_map(conn, here, today=date(2026, 10, 12))
    assert m["back"] == [] and m["readiness"] == {"M-python": 2}
    assert study.pick(conn, cfg, "here", a, 0, here=True, runner=model, today=date(2026, 10, 15))["back_in"] == 0


# ------------------------------------------------------------------ the dashboard
def test_the_endpoints_ask_without_saying_which_is_right_and_keep_the_pick_here(hubweb, catalog, monkeypatch):  # noqa: F811
    url, cfg, conn = hubweb["url"], hubweb["cfg"], hubweb["conn"]
    lid = _lesson(conn, SID, "pytest secrets", kind="fix")
    model = FakeModel(QUESTION())
    monkeypatch.setattr(factory, "make_runner", lambda cfg: model)
    for path, body in ((f"/api/party/ask?id={lid}", None), ("/api/party/pick", {"id": lid, "choice": 1})):
        status, r = _http(url, path, body)
        assert status == 403 and r["signin"] is True, path
    _joined(cfg, board=False)
    status, r = _http(url, f"/api/party/ask?id={lid}")
    assert status == 200 and r == {"available": True, "scene": "Tests pass alone", "question": "Why do they fail together?",
                                   "options": ["A flaky network", "A shared token", "A stale cache", "A slow disk"]}
    for q in ("999999", "abc", ""):
        assert _http(url, f"/api/party/ask?id={q}")[0] == 404, q
    status, r = _http(url, "/api/party/pick", {"id": lid, "choice": 1})
    assert status == 200 and r["verdict"] == "got_it" and r["answer"] == "A shared token" and r["understood"] == [lid]
    assert r["readiness"] == {"M-python": 1}
    assert _http(url, "/api/party/pick", {"id": 999999, "choice": 1})[0] == 400
    assert _http(url, "/api/party/pick", {"id": lid, "choice": 7})[1]["error"] == "pick one of the answers"
    monkeypatch.setattr(factory, "make_runner", lambda cfg: FakeModel(QUESTION(right=9), QUESTION(right=9)))
    other = _lesson(conn, SID, "pytest again", kind="fix")
    status, r = _http(url, f"/api/party/ask?id={other}")
    assert status == 200 and r["available"] is False and "no usable question" in r["reason"]


def test_a_hub_person_gets_no_study_battle_and_nothing_is_kept(team, catalog, monkeypatch):  # noqa: F811
    url, cfg, conn, s = team["url"], team["cfg"], team["conn"], team["sessions"]
    _joined(cfg, board=False)
    bob = team["bob"]
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'bob-pc', 'spoke', ?)", (BOB_PC, bob["id"]))
    conn.commit()
    bobs = _lesson(conn, _session(conn, machine=BOB_PC, source="remote"), "SQL query on bob's computer")
    monkeypatch.setattr(factory, "make_runner", lambda cfg: pytest.fail("no model call for a hub person"))
    status, r = _http(url, f"/api/party/ask?id={bobs}", headers=_as(s["Bob"]))
    assert status == 200 and r["available"] is False and "your own computer" in r["reason"]
    status, r = _http(url, "/api/party/pick", {"id": bobs, "choice": 1}, _as(s["Bob"]))
    assert status == 400 and "your own computer" in r["error"]
    assert kv_get(conn, f"party.study:person:{bob['id']}") is None
    assert _http(url, "/api/party/map", headers=_as(s["Bob"]))[1]["understood"] == []
