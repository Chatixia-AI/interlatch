"""Study battles (knowledge/study.py; GET /api/party/ask, POST /api/party/judge): a lesson's question (its case file's,
or one the model writes once and the lesson keeps), the person's answer judged by the model, the verdict kept on their
own computer only, and what they understood counted per roadmap module. The agents' levels never move."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from interlatch.analysis.runners import factory
from interlatch.knowledge import party, study
from interlatch.storage.kv import kv_get

from conftest import SID
from test_leaderboard import _joined
from test_party import BOB_PC, _lesson, _session, catalog, home  # noqa: F401  (the fixtures)
from test_people_web import _as, hubweb, team  # noqa: F401  (the fixtures)
from test_people_web import _call as _http


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


# ------------------------------------------------------------------ the question
def test_a_case_file_asks_its_own_question_and_a_written_one_is_kept(home):  # noqa: F811
    conn, cfg = home["conn"], home["cfg"]
    sid = _session(conn)
    cased = _lesson(conn, sid, "pytest flakes", kind="fix")
    _case(conn, cased, scene="Tests pass alone", question="Why do they fail together?", answer="A shared token")
    model = FakeModel({"scene": "A deploy hung", "question": "What did the job miss?", "answer": "The env var"})
    assert study.ask(conn, cfg, cased, runner=model) == {"scene": "Tests pass alone", "question": "Why do they fail together?",
                                                         "answer": "A shared token", "source": "case"}
    assert model.prompts == []
    plain = _lesson(conn, sid, "launchd jobs miss shell env", "Exported only in .zshrc.", kind="gotcha")
    first = study.ask(conn, cfg, plain, runner=model)
    assert first == {"scene": "A deploy hung", "question": "What did the job miss?", "answer": "The env var", "source": "written"}
    assert "launchd jobs miss shell env" in model.prompts[0] and "what the trap is" not in model.prompts[0]
    assert study.ask(conn, cfg, plain, runner=FakeModel()) == first  # kept: no second call
    with pytest.raises(study.StudyError, match="no such lesson"):
        study.ask(conn, cfg, 999999, runner=model)


# ------------------------------------------------------------------ the judge and the record
def test_answers_are_judged_kept_here_only_and_counted_per_module(home):  # noqa: F811
    conn, cfg, here = home["conn"], home["cfg"], home["here"]
    sid = _session(conn)
    lid = _lesson(conn, sid, "pytest and a token", kind="gotcha")
    _case(conn, lid, scene="Tests pass alone", question="Why?", answer="A shared token", principle="Isolate state",
          ruled_out=[{"lead": "Flaky network", "why": "Offline runs failed too"}])
    before = party.compute(conn, here)["agents"]
    model = FakeModel({"verdict": "partly", "feedback": "Close.", "principle": "Isolate shared state"},
                      {"verdict": "got_it", "feedback": "Yes.", "principle": "Isolate shared state"},
                      {"verdict": "who knows", "feedback": "", "principle": ""})
    got = study.judge(conn, cfg, "here", lid, "something shared?", here=True, runner=model, today=date(2026, 10, 17))
    assert got == {"verdict": "partly", "feedback": "Close.", "principle": "Isolate shared state", "answer": "A shared token",
                   "recorded": True, "back_in": 0, "owned": False}
    assert "something shared?" in model.prompts[0] and "Flaky network" in model.prompts[0] and "A shared token" in model.prompts[0]
    study.judge(conn, cfg, "here", lid, "a shared token", here=True, runner=model, today=date(2026, 10, 18))
    assert study.studied_of(conn, "here") == [{"id": lid, "on": "2026-10-17", "verdict": "partly"},
                                              {"id": lid, "on": "2026-10-18", "verdict": "got_it"}]
    m = party.adventure_map(conn, here)
    assert m["understood"] == [lid] and m["readiness"] == {"M-python": 1} and m["ready_at"] == study.READY_AT
    # a verdict the model made up reads as "not yet"; the newest answer decides
    assert study.judge(conn, cfg, "here", lid, "no idea", here=True, runner=model)["verdict"] == "not_yet"
    assert party.adventure_map(conn, here)["understood"] == []
    party.clear_cache()
    assert party.compute(conn, here)["agents"] == before  # no level moved
    # nothing for an empty answer, or away from the person's computer
    for answer in ("", "   ", None, 3):
        with pytest.raises(study.StudyError, match="write an answer"):
            study.judge(conn, cfg, "here", lid, answer, here=True, runner=FakeModel())
    with pytest.raises(study.StudyError, match="your own computer"):
        study.judge(conn, cfg, "person:1", lid, "a shared token", here=False, runner=FakeModel())
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
    for lid in (a, b):
        _case(conn, lid, scene="s", question="q?", answer="x")
    yes = lambda: FakeModel({"verdict": "got_it", "feedback": "", "principle": ""})  # noqa: E731
    got = study.judge(conn, cfg, "here", a, "x", here=True, runner=yes(), today=date(2026, 10, 1))
    assert got["back_in"] == 3 and got["owned"] is False
    got = study.judge(conn, cfg, "here", a, "x", here=True, runner=yes(), today=date(2026, 10, 4))
    assert got["back_in"] == 10 and got["owned"] is True
    study.judge(conn, cfg, "here", b, "x", here=True, runner=yes(), today=date(2026, 10, 10))
    m = party.adventure_map(conn, here, today=date(2026, 10, 14))
    assert m["understood"] == [a, b] and m["owned"] == [a] and m["back"] == [a, b]  # a: back on the 14th; b: on the 13th
    m = party.adventure_map(conn, here, today=date(2026, 10, 12))
    assert m["back"] == [] and m["readiness"] == {"M-python": 2}
    no = FakeModel({"verdict": "not_yet", "feedback": "", "principle": ""})
    assert study.judge(conn, cfg, "here", a, "?", here=True, runner=no, today=date(2026, 10, 15))["back_in"] == 0


# ------------------------------------------------------------------ the dashboard
def test_the_endpoints_ask_without_the_answer_and_judge_at_this_computer(hubweb, catalog, monkeypatch):  # noqa: F811
    url, cfg, conn = hubweb["url"], hubweb["cfg"], hubweb["conn"]
    lid = _lesson(conn, SID, "pytest secrets", kind="fix")
    _case(conn, lid, scene="Tests pass alone", question="Why?", answer="A shared token")
    model = FakeModel({"verdict": "got_it", "feedback": "Yes.", "principle": "Isolate state"})
    monkeypatch.setattr(factory, "make_runner", lambda cfg: model)
    for path, body in ((f"/api/party/ask?id={lid}", None), ("/api/party/judge", {"id": lid, "answer": "token"})):
        status, r = _http(url, path, body)
        assert status == 403 and r["signin"] is True, path
    _joined(cfg, board=False)
    status, r = _http(url, f"/api/party/ask?id={lid}")
    assert status == 200 and r == {"available": True, "scene": "Tests pass alone", "question": "Why?", "source": "case"}
    for q in ("999999", "abc", ""):
        assert _http(url, f"/api/party/ask?id={q}")[0] == 404, q
    status, r = _http(url, "/api/party/judge", {"id": lid, "answer": "a shared token"})
    assert status == 200 and r["verdict"] == "got_it" and r["answer"] == "A shared token" and r["understood"] == [lid]
    assert r["readiness"] == {"M-python": 1}
    assert _http(url, "/api/party/judge", {"id": 999999, "answer": "x"})[0] == 400
    assert _http(url, "/api/party/judge", {"id": lid, "answer": ""})[1]["error"] == "write an answer first"


def test_a_hub_person_gets_no_study_battle_and_nothing_is_kept(team, catalog, monkeypatch):  # noqa: F811
    url, cfg, conn, s = team["url"], team["cfg"], team["conn"], team["sessions"]
    _joined(cfg, board=False)
    bob = team["bob"]
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'bob-pc', 'spoke', ?)", (BOB_PC, bob["id"]))
    conn.commit()
    bobs = _lesson(conn, _session(conn, machine=BOB_PC, source="remote"), "SQL query on bob's computer")
    _case(conn, bobs, scene="s", question="q?", answer="a")
    monkeypatch.setattr(factory, "make_runner", lambda cfg: pytest.fail("no model call for a hub person"))
    status, r = _http(url, f"/api/party/ask?id={bobs}", headers=_as(s["Bob"]))
    assert status == 200 and r["available"] is False and "your own computer" in r["reason"]
    status, r = _http(url, "/api/party/judge", {"id": bobs, "answer": "a"}, _as(s["Bob"]))
    assert status == 400 and "your own computer" in r["error"]
    assert kv_get(conn, f"party.study:person:{bob['id']}") is None
    assert _http(url, "/api/party/map", headers=_as(s["Bob"]))[1]["understood"] == []
