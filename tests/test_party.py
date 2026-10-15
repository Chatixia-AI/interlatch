"""The Party (knowledge/party.py, interfaces/web/api/party.py): the formulas at their boundaries, which lessons count
and toward how many modules, whose sessions level whose agents, the sign-in gate (nothing about the agents before it),
signing in for the Party without joining the leaderboard, the quest log each viewer keeps for themselves (members too,
for that and nothing else), limited people, the journey and the weekly series. A small catalog written here stands in
for roadmap.json, so these don't depend on its content; one test computes a party with the real one. Nothing here
reaches Google or Firebase (test_leaderboard's FakeNet)."""

from __future__ import annotations

import itertools
import json
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from interlatch.core import i18n
from interlatch.core.agents import CODING_AGENTS
from interlatch.core.machine import local_machine
from interlatch.knowledge import party
from interlatch.sharing import leaderboard as lb
from interlatch.sharing import people
from interlatch.storage.kv import kv_get
from interlatch.storage.sqlite import connect

from conftest import CWD, SID
from test_leaderboard import _joined, net  # noqa: F401  (net: the fixture)
from test_people_web import REMOTE, TAILNET, _as, hubweb, team  # noqa: F401  (the fixtures)
from test_people_web import _call as _http

OTHER_CWD = "/Users/test/Projects/other-app"
TEAMMATE_PC = "99999999-2222-4333-8444-555555555555"
BOB_PC = "bbbbbbbb-0000-4000-8000-000000000002"
SAM_PC = "cccccccc-0000-4000-8000-000000000003"
ATTRS = ("craft", "data", "ops", "guard", "arcana", "lore")


def _module(mid, attr, keywords, name=None):
    return {"id": mid, "name": name or mid[2:].title(), "name_ja": f"{mid}（日本語）", "attr": attr, "part": 1,
            "prior": [], "study": [f"study {mid}"], "practice": f"practice {mid}", "gate": f"gate {mid}",
            "keywords": keywords}


def _track(tid, category, modules, critical=(), keywords=("never-matches-1", "never-matches-2", "never-matches-3",
                                                           "never-matches-4")):
    return {"id": tid, "name": f"{tid} track", "name_ja": f"{tid} のトラック", "category": category, "family": "Build",
            "owned": "owned", "modules": list(modules), "critical": list(critical), "targets": {},
            "milestones": [{"id": f"{tid}-G{n}", "text": f"{tid} milestone {n}", "text_ja": f"{tid} マイルストーン {n}",
                            "keywords": kw} for n, kw in enumerate(keywords, 1)],
            "capstones": [], "interview": "interview", "evidence": "evidence"}


CATALOG = {
    "version": 3, "title": "Test roadmap", "title_ja": "テストのロードマップ",
    "attrs": [{"id": a, "name": a.title(), "name_ja": f"{a}（日本語）", "about": f"about {a}"} for a in ATTRS],
    "categories": [{"id": "data", "name": "Data", "name_ja": "データ", "outcome": "o"},
                   {"id": "ai", "name": "AI", "name_ja": "AI（日本語）", "outcome": "o"}],
    "modules": [
        _module("M-python", "craft", r"\bpython\b|\bpytest\b"),
        _module("M-sql", "data", r"\bsql\b|\bquer(?:y|ies)\b|クエリ"),
        _module("M-auth", "guard", r"\btoken\b|\bauth\w*"),
        _module("M-deploy", "ops", r"\bdeploy\w*|デプロイ"),
        _module("M-llm", "arcana", r"\bllm\b|\bprompts?\b"),
    ],
    "tracks": [
        _track("backend", "data", ["M-python", "M-sql", "M-auth"], critical=["M-sql"],
               keywords=(r"\bmigration\b", r"\bindex(?:es)?\b", r"\bnever-a\b", r"\bnever-b\b")),
        _track("ai-eng", "ai", ["M-python", "M-llm"]),
        _track("sre", "ai", ["M-deploy", "M-auth"]),
    ],
    "capstones": [], "routes": [], "phases": [],
}


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    path = tmp_path / "roadmap.json"
    path.write_text(json.dumps(CATALOG, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(party, "CATALOG_PATH", path)
    party.clear_cache()
    yield party.load_catalog()
    party.clear_cache()


@pytest.fixture()
def home(env, catalog):
    conn = connect(env["cfg"].db_path)
    env.update(conn=conn, here=local_machine(env["cfg"])["id"], catalog=catalog)
    yield env
    conn.close()


_ids = itertools.count(1)


def _session(conn, sid=None, *, agent="claude", machine=None, at="2026-09-01T10:00:00.000Z", project=CWD,
             source="transcript", status="done"):
    sid = sid or f"s{next(_ids):08d}-0000-4000-8000-000000000000"
    conn.execute("INSERT INTO sessions(id, source, agent, started_at, ended_at, machine_id, project_path, project_name, "
                 "analysis_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (sid, source, agent, at, at, machine, project, project.rsplit("/", 1)[-1], status))
    conn.commit()
    return sid


def _lesson(conn, sid, title, body="", *, kind="fix", stage="provisional", status="active", agent="claude",
            project=CWD, source="analysis", scope="project", tags=()):
    cur = conn.execute(
        "INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, tags_json, scope, source, agent, "
        "status, stage, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sid, project, project.rsplit("/", 1)[-1], kind, title, body, json.dumps(list(tags)), scope, source, agent,
         status, stage, "2026-10-01T00:00:00.000Z", "2026-10-01T00:00:00.000Z"))
    conn.commit()
    return cur.lastrowid


def _agent(data, agent="claude"):
    return next(a for a in data["agents"] if a["agent"] == agent)


def _skills(data, agent="claude"):
    return {s["module"]: s["xp"] for s in _agent(data, agent)["skills"]}


# ------------------------------------------------------------------ the formulas
def test_skill_and_character_levels_and_tiers_at_their_boundaries():
    for xp, level in ((0, 0), (1, 1), (5, 1), (6, 2), (15, 2), (16, 3), (30, 3), (31, 4), (50, 4), (51, 5), (225, 9),
                      (226, 10)):
        assert party.skill_level(xp) == level, xp
    for level in range(1, 60):
        t = party.skill_threshold(level)
        assert t == 5 * level * (level - 1) // 2 + 1
        assert party.skill_level(t) == level and party.skill_level(t - 1) == level - 1
    for xp, level in ((0, 1), (2, 1), (3, 2), (11, 2), (12, 3), (26, 3), (27, 4)):
        assert party.char_level(xp) == level, xp
    for level in range(1, 80):
        assert party.char_level(3 * (level - 1) ** 2) == level and party.char_level(3 * level ** 2 - 1) == level
        assert party.char_threshold(level) == 3 * (level - 1) ** 2
    assert [party.tier_of(n) for n in (1, 4, 5, 11, 12, 24, 25, 99)] == \
        ["novice", "novice", "adept", "adept", "expert", "expert", "master", "master"]


def test_track_coverage_weighs_critical_modules_double_and_caps_each_at_level_five(catalog):
    mxp = [0] * len(catalog.modules)
    mxp[catalog.module_index["M-python"]] = 200  # capped at FULL_XP
    mxp[catalog.module_index["M-sql"]] = 17  # critical: weight 2, a third of the way
    got = dict((catalog.tracks[ti]["id"], c) for c, ti in party.coverage(catalog, mxp))
    assert got["backend"] == pytest.approx((1 * 1 + 2 * 17 / 51 + 1 * 0) / 4)
    assert got["ai-eng"] == pytest.approx(0.5) and "sre" not in got  # nothing in it: not listed
    assert [catalog.tracks[ti]["id"] for _, ti in party.coverage(catalog, mxp)] == ["ai-eng", "backend"]


def test_tracks_covered_alike_rank_by_the_xp_behind_them(catalog):
    mxp = [0] * len(catalog.modules)
    for mid, xp in (("M-python", 200), ("M-llm", 100), ("M-deploy", 300), ("M-auth", 300)):
        mxp[catalog.module_index[mid]] = xp
    ranked = party.coverage(catalog, mxp)  # ai-eng and sre both fully covered; sre went deeper (mean 300 vs 150)
    assert [(catalog.tracks[ti]["id"], round(c, 3)) for c, ti in ranked] == [("sre", 1.0), ("ai-eng", 1.0),
                                                                             ("backend", 0.5)]


def test_an_agent_has_a_class_after_ten_lessons_and_five_percent_of_a_track():
    assert party.class_of([(0.05, 7), (0.04, 1)], 10) == (0.05, 7)
    assert party.class_of([(0.05, 7)], 9) is None  # too few lessons yet: it only leans towards a track
    assert party.class_of([(0.0494, 7)], 50) is None and party.class_of([(0.0496, 7)], 50) == (0.0496, 7)  # 3 decimals
    assert party.class_of([], 50) is None


# ------------------------------------------------------------------ the keyword index
TRICKY = [r"\bsql\b|\bsqlite3?\b|\bpostgres(?:ql)?\b", r"\x22(?:workflow|tooling)\x22|\bjson\b", r"(?-i:\bwindows\b)|\blinux\b",
          r"\b\d+ ?ms\b|\blatency\b", r"(?:pre-?)?commit hooks?", r"(?<!react-)\bhooks?\b|\buse[A-Z]\w+",
          r"デプロイ|\bdeploy\w*|\bship(?:ped|ping)?\b", r"\bselect\b[^.\n]{1,60}\bfrom\b|\bwhere\b", r"ab+c|\bx{2,}y",
          r"\bsession\b|\bsession store\b|\bstore\b", r"__init__\.py|\.pyc\b|\bv?env\b", r"(?P<n>token)s?|\bauth\w*",
          r"\b(?:left|inner|outer) join\b|\bjoin(?:ed)?\b", r"\bK8s\b|\bkubernetes\b|\bkubectl\b",
          r"(?<=t)ing\b|\bfoo\b"]
TEXTS = ["SQLite and sqlite3 beat Postgres; postgresql too", 'set "workflow" and "tooling" in the JSON', "Windows vs windows, Linux",
         "took 300 ms, 45ms latency", "pre-commit hooks and precommit hook, commit hooks", "react-hooks, hooks, useEffect",
         "本番にデプロイした deployed shipping", "SELECT id, name FROM t WHERE x", "abbbc ac xxy xy", "session store, session, store",
         "edit __init__.py, stale .pyc, venv and env", "tokens token auth authz", "LEFT JOIN and inner join, joined",
         "k8s and KUBERNETES via kubectl", "commit hooks only", "the env file", "testing", "nothing here", ""]


def test_the_keyword_index_finds_what_the_whole_regex_finds(tmp_path):
    cat = party.Catalog({"modules": [_module(f"M-{i}", "craft", p) for i, p in enumerate(TRICKY)]}, ("tricky",))
    for text in TEXTS + [" ".join(TEXTS)]:
        want = {i: n for i, p in enumerate(TRICKY)
                if (n := sum(1 for m in re.finditer(p, text, re.I) if m.group(0)))}
        assert party.keyword_hits(cat, text) == want, text
    assert all(kw.alternatives for kw in cat.keywords.values())  # each was split, none left whole
    assert sum(n is None for kw in cat.keywords.values() for n in kw.needs) <= 2  # nearly all are indexed


# ------------------------------------------------------------------ what counts
def test_a_lesson_counts_toward_what_its_title_and_tags_name_or_its_body_names_twice(home):
    conn, cat = home["conn"], home["catalog"]
    m = {mid: cat.module_index[mid] for mid in cat.module}
    # python: in the title (score 2); sql: twice in the body (2); deploy: once in the body, which levels nothing
    assert party.modules_of(cat, "pytest flake []", "the sql query went wrong; one deploy") == (m["M-python"], m["M-sql"])
    assert party.modules_of(cat, "a note []", "one token in passing") == ()
    assert party.modules_of(cat, 'a note ["llm"]', "") == (m["M-llm"],)  # the tags are the head too
    # scores: sql 2·2+1, auth 2·1+2, deploy 2·1, python 0+2, llm 1 (out); the best three, ties in catalog order
    assert party.modules_of(cat, "SQL query token deploy []", "auth auth; the queries; python pytest; prompt") == \
        (m["M-sql"], m["M-auth"], m["M-python"])
    sid = _session(conn)
    _lesson(conn, sid, "pytest flake", "the sql query went wrong; one deploy")
    _lesson(conn, sid, "a note", "one token in passing")
    _lesson(conn, sid, "a note", tags=["llm"])
    _lesson(conn, sid, "SQL query token deploy", "auth auth; the queries; python pytest; prompt")
    data = party.compute(conn, home["here"])
    assert _skills(data) == {"M-python": 2, "M-sql": 2, "M-auth": 1, "M-llm": 1}
    claude = _agent(data)
    assert claude["xp"] == 6 and claude["counted"] == 3 and claude["lessons"] == 4
    assert [s["module"] for s in claude["skills"]] == ["M-python", "M-sql", "M-auth", "M-llm"]  # xp, then catalog order
    assert claude["attrs"] == {"craft": 2, "data": 2, "ops": 0, "guard": 1, "arcana": 1, "lore": 0}


def test_stages_earn_their_xp_and_only_settled_lessons_of_coding_agents_count(home):
    conn = home["conn"]
    sid = _session(conn)
    _lesson(conn, sid, "pytest one", stage="provisional")  # 1
    _lesson(conn, sid, "pytest two", stage="established")  # 3
    _lesson(conn, sid, "pytest three", stage="canonical")  # 6
    _lesson(conn, sid, "pytest unsure", stage="wip")
    _lesson(conn, sid, "pytest replaced", status="superseded")
    _lesson(conn, sid, "pytest dismissed", status="dismissed")
    _lesson(conn, sid, "pytest is a fact", kind="fact")
    _lesson(conn, sid, "pytest preferred", kind="preference")
    _lesson(conn, None, "pytest from memory", source="memory")  # no session: nothing to scope it by
    _lesson(conn, None, "pytest from a teammate", source="team", agent=None)
    for chat in ("chatgpt", "claude-ai"):  # imported chats never join the party
        _lesson(conn, _session(conn, agent=chat, source=f"{chat}-export"), "pytest in a chat", agent=chat)
    _lesson(conn, _session(conn, agent="codex"), "a codex pytest", kind="command", agent="codex")
    data = party.compute(conn, home["here"])
    assert _skills(data) == {"M-python": 10}
    claude = _agent(data)
    assert claude["counted"] == 3 and claude["level"] == party.char_level(10) == 2
    assert claude["lessons"] == 6  # every active lesson of its sessions: the wip one, the fact and the preference too
    assert _skills(data, "codex") == {"M-python": 1}
    assert [a["agent"] for a in data["agents"]] == list(CODING_AGENTS)  # all five, always, in order
    bob = _agent(data, "bob")
    assert (bob["level"], bob["xp"], bob["tier"], bob["class"], bob["classes"], bob["skills"]) == (1, 0, "novice", None, [], [])
    assert bob["next_xp"] == 3 and bob["level_xp"] == 0


def test_a_teammates_session_never_levels_this_computers_agents(home):
    conn, here = home["conn"], home["here"]
    _lesson(conn, _session(conn, machine=here), "pytest here")
    _lesson(conn, _session(conn, machine=None), "pytest before machines were recorded")
    _lesson(conn, _session(conn, machine=TEAMMATE_PC, source="remote"), "pytest on a teammate's computer")
    data = party.compute(conn, here)
    assert _skills(data) == {"M-python": 2} and _agent(data)["sessions"] == 2


def test_a_hub_person_sees_the_party_of_their_own_computers_only(home):
    conn, here = home["conn"], home["here"]
    ada = people.add(conn, "Ada", "ada@example.com", "admin")
    bob = people.add(conn, "Bob", "bob@example.com", "member", by=ada)
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'bob-pc', 'spoke', ?)", (BOB_PC, bob["id"]))
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'ada-pc', 'spoke', ?)", (TEAMMATE_PC, ada["id"]))
    conn.commit()
    _lesson(conn, _session(conn, machine=here), "pytest at the hub itself")
    _lesson(conn, _session(conn, machine=BOB_PC, source="remote"), "SQL query on bob's computer")
    _lesson(conn, _session(conn, machine=TEAMMATE_PC, source="remote", agent="codex"), "deploy on ada's", agent="codex")
    assert _skills(party.compute(conn, here, bob)) == {"M-sql": 1} and _skills(party.compute(conn, here, bob), "codex") == {}
    assert _skills(party.compute(conn, here, ada), "codex") == {"M-deploy": 1} and _skills(party.compute(conn, here, ada)) == {}
    assert _skills(party.compute(conn, here)) == {"M-python": 1}  # the hub computer itself: its own sessions
    assert party.scope_key(None) == "here" and party.scope_key(bob) == f"person:{bob['id']}"


def test_class_quests_and_evidence(home):
    conn = home["conn"]
    sid = _session(conn)
    for n in range(10):
        _lesson(conn, sid, f"SQL query {n} pytest", stage="canonical")  # sql 60, python 60: backend
    codex = _session(conn, agent="codex")
    for _ in range(10):  # llm 10; one pytest in the body levels nothing
        _lesson(conn, codex, "an llm prompt", "pytest", agent="codex")
    copilot = _session(conn, agent="copilot")
    for _ in range(9):  # the highest level, but only nine lessons: no class yet, so not the goal
        _lesson(conn, copilot, "pytest sql query token deploy prompt", stage="canonical", agent="copilot")
    conn.execute("INSERT INTO artifacts(session_id, key, kind, title, url, ts) VALUES (?, 'pr:1', 'pr', "
                 "'PR #1 the migration', 'https://github.com/x/y/pull/1', '2026-09-02T00:00:00.000Z')", (sid,))
    conn.execute("INSERT INTO artifacts(session_id, key, kind, title, url, ts) VALUES (?, 'img', 'image', "
                 "'a migration picture', NULL, '2026-09-02T00:00:00.000Z')", (sid,))  # not a kind that shows work
    conn.commit()
    _lesson(conn, sid, "adding an index", "for the SQL")  # backend-G2 in the title
    _lesson(conn, sid, "a note", "never-a, once")  # backend-G3 once in the body: not evidence
    _lesson(conn, sid, "a note", "never-b, and never-b again")  # backend-G4 twice in the body: evidence
    data = party.compute(conn, home["here"])
    claude = _agent(data)
    assert claude["class"]["id"] == "backend" and claude["class"]["category_name"] == "Data"
    assert claude["class"]["coverage"] == round((1 + 2 + 0) / 4, 3)
    assert _agent(data, "codex")["class"]["id"] == "ai-eng" and _skills(data, "codex") == {"M-llm": 10}
    copilot = _agent(data, "copilot")
    assert copilot["level"] > claude["level"] and copilot["counted"] == 9 and copilot["class"] is None
    assert [c["id"] for c in copilot["classes"]] == ["backend", "ai-eng", "sre"]  # what it leans towards
    assert data["goal"] == {"id": "backend", "name": "backend track", "chosen": False}  # the strongest with a class
    q = {x["id"]: x for x in data["quests"]}
    assert [x["id"] for x in data["quests"]] == [f"backend-G{n}" for n in (1, 2, 3, 4)] + [f"ai-eng-G{n}" for n in (1, 2, 3, 4)]
    assert q["backend-G1"]["main"] and not q["ai-eng-G1"]["main"] and q["backend-G2"]["n"] == 2
    assert q["backend-G1"]["status"] == "evidence"
    assert [(e["kind"], e.get("artifact_kind"), e["title"]) for e in q["backend-G1"]["evidence"]] == \
        [("artifact", "pr", "PR #1 the migration")]
    assert q["backend-G1"]["evidence"][0]["url"] == "https://github.com/x/y/pull/1"
    assert q["backend-G2"]["status"] == "evidence" and q["backend-G2"]["evidence"][0]["kind"] == "lesson"
    assert q["backend-G3"]["status"] == "open" and q["backend-G3"]["evidence"] == []
    assert q["backend-G4"]["status"] == "evidence" and q["backend-G4"]["evidence"][0]["title"] == "a note"


def test_names_follow_the_dashboard_language(home):
    conn = home["conn"]
    _lesson(conn, _session(conn), "pytest")
    token = i18n.lang.set("ja")
    try:
        data = party.compute(conn, home["here"])
    finally:
        i18n.lang.reset(token)
    assert data["catalog"]["title"] == "テストのロードマップ" and data["attrs"][0]["name"] == "craft（日本語）"
    assert _agent(data)["skills"][0]["name"] == "M-python（日本語）"
    assert party.compute(conn, home["here"])["catalog"]["title"] == "Test roadmap"  # cached per language


# ------------------------------------------------------------------ the journey
def test_the_journey_is_newest_first_capped_and_the_series_weekly(home, tmp_path, monkeypatch):
    many = {**CATALOG, "modules": [_module(f"M-k{i}", ATTRS[i % 6], rf"\bkw{i}\b") for i in range(60)],
            "tracks": [_track("wide", "data", [f"M-k{i}" for i in range(60)])]}
    path = tmp_path / "wide.json"
    path.write_text(json.dumps(many))
    monkeypatch.setattr(party, "CATALOG_PATH", path)
    conn = home["conn"]
    weeks = ("2026-08-31T10:00:00.000Z", "2026-09-02T10:00:00.000Z", "2026-09-09T10:00:00.000Z",
             "2026-09-23T10:00:00.000Z")  # Mon and Wed of one week, then two more weeks (one quiet week between)
    for w, at in enumerate(weeks):
        sid = _session(conn, at=at)
        for i in range(60):
            _lesson(conn, sid, f"kw{i}", stage="canonical" if w else "established")
    data = party.compute(conn, home["here"])
    journey = data["journey"]
    assert len(journey) == party.JOURNEY_MAX
    assert [e["at"] for e in journey] == sorted((e["at"] for e in journey), reverse=True)
    assert journey[0]["at"] == "2026-09-23" and journey[0]["week"] == "2026-W39"
    skill = next(e for e in journey if e["type"] == "skill")
    assert skill["module"].startswith("M-k") and skill["lesson"]["title"].startswith("kw") and skill["agent"] == "claude"
    series = data["series"]
    assert set(series) == set(CODING_AGENTS) and series["codex"] == []
    assert [p[0] for p in series["claude"]] == ["2026-08-31", "2026-09-07", "2026-09-21"]  # one point per active week
    xp = [p[2] for p in series["claude"]]
    assert xp == [60 * 3 + 60 * 6, 60 * 3 + 60 * 6 * 2, 60 * 3 + 60 * 6 * 3] and xp[-1] == _agent(data)["xp"]
    assert [p[1] for p in series["claude"]] == [party.char_level(x) for x in xp]
    oldest = party._journey(party.load_catalog(), party.lessons_in(conn, party.load_catalog(), party._scope_of(
        conn, home["here"], None)), {})[0][-1]
    assert oldest["type"] in ("join", "skill", "level", "class")


# ------------------------------------------------------------------ this week: "recent" and the zone
TODAY = date(2026, 10, 15)


def test_recent_is_this_weeks_counted_lessons_newest_first(home):
    conn, here = home["conn"], home["here"]
    edge = _lesson(conn, _session(conn, at="2026-10-09T00:00:00.000Z"), "SQL query at the week's start")  # 6 days before
    _lesson(conn, _session(conn, at="2026-10-08T23:59:59.000Z"), "pytest the day before the week")
    day = _session(conn, at="2026-10-15T08:00:00.000Z")
    first = _lesson(conn, day, "pytest and a token", tags=["llm"], stage="established")
    _lesson(conn, day, "a note that names no module")  # in the week, earns nothing: not recent
    newest = _lesson(conn, day, "deploy it")
    _lesson(conn, _session(conn, at="2026-10-15T09:00:00.000Z", agent="chatgpt", source="chatgpt-export"), "pytest chat",
            agent="chatgpt")
    _lesson(conn, _session(conn, at="2026-10-15T09:00:00.000Z", machine=TEAMMATE_PC, source="remote"), "pytest elsewhere")
    undated = _session(conn, at=None)  # no session start: the lesson's own date (2026-10-01), out of this week
    _lesson(conn, undated, "pytest undated")
    recent = party.compute(conn, here, today=TODAY)["recent"]
    assert [k["id"] for k in recent] == [newest, first, edge]
    k = recent[1]
    assert {x: k[x] for x in ("title", "kind", "stage", "agent", "xp", "at", "session_id")} == {
        "title": "pytest and a token", "kind": "fix", "stage": "established", "agent": "claude", "xp": 3 * 3,
        "at": "2026-10-15", "session_id": day}
    assert k["modules"] == [{"id": "M-python", "name": "Python", "attr": "craft"},
                            {"id": "M-auth", "name": "Auth", "attr": "guard"}, {"id": "M-llm", "name": "Llm", "attr": "arcana"}]
    assert [x["id"] for x in party.compute(conn, here, today=date(2026, 10, 3))["recent"]] == \
        [party.lessons_in(conn, home["catalog"], party._scope_of(conn, here, None))[0].id]  # the undated one, by its own date
    token = i18n.lang.set("ja")
    try:
        assert party.compute(conn, here, today=TODAY)["recent"][1]["modules"][0]["name"] == "M-python（日本語）"
    finally:
        i18n.lang.reset(token)
    # the cache keeps a day's answer for that day only: a week on, the same archive has no recent lessons
    assert party.compute(conn, here, today=TODAY + timedelta(days=7))["recent"] == []
    assert len(party.compute(conn, here, today=TODAY)["recent"]) == 3


def test_recent_keeps_the_newest_sixty(home):
    conn = home["conn"]
    sid = _session(conn, at="2026-10-15T08:00:00.000Z")
    ids = [_lesson(conn, sid, f"pytest {n}") for n in range(party.RECENT_MAX + 5)]
    data = party.compute(conn, home["here"], today=TODAY)
    assert [k["id"] for k in data["recent"]] == ids[::-1][:party.RECENT_MAX]
    # the week's tally isn't cut by the cap: the page says how many there really were
    week = data["week"]
    assert week["lessons"] == party.RECENT_MAX + 5 and week["by_agent"]["claude"] == party.RECENT_MAX + 5
    assert week["until"] == TODAY.isoformat() and week["since"] == (TODAY - timedelta(days=6)).isoformat()
    assert set(week["by_agent"]) == set(CODING_AGENTS)


def test_the_zone_is_where_this_weeks_xp_went(home):
    conn, here = home["conn"], home["here"]
    assert party.compute(conn, here, today=TODAY)["zone"] is None  # nothing yet: nowhere
    old = _session(conn, at="2026-09-01T08:00:00.000Z")
    _lesson(conn, old, "deploy a while ago")
    assert party.compute(conn, here, today=TODAY)["zone"] == {"attr": "ops", "module": "M-deploy", "module_name": "Deploy"}
    _lesson(conn, old, "pytest a while ago")  # the newest skill event now: python's first level
    assert party.compute(conn, here, today=TODAY)["zone"]["module"] == "M-python"
    week = _session(conn, at="2026-10-14T08:00:00.000Z")
    _lesson(conn, week, "SQL query", stage="established")  # sql 3 this week
    _lesson(conn, _session(conn, at="2026-10-15T08:00:00.000Z"), "a token and a prompt", stage="established")  # auth 3, llm 3
    zone = party.compute(conn, here, today=TODAY)["zone"]
    assert zone == {"attr": "guard", "module": "M-auth", "module_name": "Auth"}  # three ways tied at 3: the newest wins
    _lesson(conn, week, "more SQL queries")
    assert party.compute(conn, here, today=TODAY)["zone"]["module"] == "M-sql"  # 4: the most this week


def test_journey_events_join_level_skill_class_and_quest(home):
    conn = home["conn"]
    sid = _session(conn, at="2026-09-01T10:00:00.000Z")
    for _ in range(9):
        _lesson(conn, sid, "pytest", stage="canonical")
    later = _session(conn, at="2026-09-08T10:00:00.000Z")  # the tenth lesson, a week on: the class comes with it
    _lesson(conn, later, "pytest", stage="canonical")  # python 60: skill 5, level 5
    party.set_quest(conn, None, "ai-eng-G1", True)
    journey = party.compute(conn, home["here"])["journey"]
    kinds = [(e["type"], e["level"]) for e in reversed(journey) if e["type"] != "quest"]
    assert kinds[0] == ("join", 1)
    assert [lv for t, lv in kinds if t == "skill"] == [1, 3, 5]
    assert [lv for t, lv in kinds if t == "level"] == [2, 3, 4, 5]
    cls = [e for e in journey if e["type"] == "class"]
    assert len(cls) == 1 and cls[0]["track"] == "ai-eng" and cls[0]["track_name"] == "ai-eng track"
    assert cls[0]["at"] == "2026-09-08" and cls[0]["lesson"]["session_id"] == later
    quest = journey[0]  # marked just now: the newest
    assert quest["type"] == "quest" and quest["agent"] is None and quest["quest"]["id"] == "ai-eng-G1"
    assert quest["text"] == "ai-eng milestone 1" and quest["track"] == "ai-eng" and quest["lesson"] is None
    assert all(e["lesson"] == {"id": e["lesson"]["id"], "title": "pytest", "session_id": e["lesson"]["session_id"]}
               and e["lesson"]["session_id"] in (sid, later) for e in journey if e["type"] != "quest")
    assert all(e["text"] is None for e in journey if e["type"] != "quest")
    token = i18n.lang.set("ja")
    try:
        assert party.compute(conn, home["here"])["journey"][0]["text"] == "ai-eng マイルストーン 1"
    finally:
        i18n.lang.reset(token)


# ------------------------------------------------------------------ the dashboard: the gate
def _sign_in(cfg, **extra):
    _joined(cfg, **extra)


def test_nothing_about_the_agents_before_signing_in(hubweb, catalog):  # noqa: F811
    url, cfg, conn = hubweb["url"], hubweb["cfg"], hubweb["conn"]
    _lesson(conn, SID, "pytest secrets")  # the synced session: this computer's
    status, data = _http(url, "/api/party")
    assert status == 200 and data == {"signed_in": False, "signing_in": False, "can_sign_in": True}
    for path in ("/api/party/skill?agent=claude&module=M-python", "/api/party/track?id=backend"):
        status, r = _http(url, path)
        assert status == 403 and r["signin"] is True and "pytest" not in json.dumps(r), path
    for path, body in (("/api/party/quest", {"id": "backend-G1", "done": True}), ("/api/party/goal", {"track": "sre"})):
        status, r = _http(url, path, body)
        assert status == 403 and r["signin"] is True, path
    assert conn.execute("SELECT COUNT(*) FROM kv WHERE key LIKE 'party.%'").fetchone()[0] == 0
    _sign_in(cfg, board=False)
    status, data = _http(url, "/api/party")
    assert status == 200 and data["signed_in"] and data["who"] == {"kind": "google", "name": "Ada", "uid": "uid1",
                                                                   "photo": True, "board": False}
    assert _skills(data) == {"M-python": 1} and "refresh_token" not in json.dumps(data) and "R0" not in json.dumps(data)
    status, sk = _http(url, "/api/party/skill?agent=claude&module=M-python")
    assert status == 200 and sk["level"] == 1 and [k["title"] for k in sk["lessons"]] == ["pytest secrets"]
    assert sk["about"] == ["study M-python"] and sk["gate"] == "gate M-python"
    assert _http(url, "/api/party/skill?agent=chatgpt&module=M-python")[0] == 404
    assert _http(url, "/api/party/skill?agent=claude&module=M-nothing")[0] == 404
    status, tr_ = _http(url, "/api/party/track?id=backend")
    assert status == 200 and tr_["modules"][0]["agents"] == {"claude": {"level": 1, "xp": 1}}
    assert tr_["modules"][1]["critical"] is True and [m["id"] for m in tr_["milestones"]][0] == "backend-G1"
    assert set(tr_["coverage"]) == set(CODING_AGENTS) and not tr_["milestones"][0]["main"]
    assert _http(url, "/api/party/track?id=nothing")[0] == 404
    status, tk = _http(url, "/api/party/tracks")
    assert status == 200 and [c["id"] for c in tk["categories"]] == ["data", "ai"]
    assert tk["categories"][1]["tracks"] == [{"id": "ai-eng", "name": "ai-eng track"}, {"id": "sre", "name": "sre track"}]


def test_another_device_never_opens_this_computers_party(hubweb, catalog):  # noqa: F811
    url, cfg = hubweb["url"], hubweb["cfg"]
    _sign_in(cfg)
    for path, body in (("/api/party", None), ("/api/party/skill?agent=claude&module=M-python", None),
                       ("/api/party/track?id=backend", None), ("/api/party/tracks", None), ("/api/party/signin", {}),
                       ("/api/party/signout", {}), ("/api/party/quest", {"id": "backend-G1", "done": True}),
                       ("/api/party/goal", {"track": "sre"})):
        assert _http(url, path, body, headers=TAILNET)[0] == 403, path  # a phone through Tailscale: not a person
    assert lb.read_state(hubweb["cfg"]) is not None and kv_get(hubweb["conn"], "party.goal:here") is None


# ------------------------------------------------------------------ signing in for the Party
def test_signing_in_for_the_party_keeps_the_sign_in_off_the_board(hubweb, catalog, net):  # noqa: F811
    url, cfg, conn = hubweb["url"], hubweb["cfg"], hubweb["conn"]
    net.on("POST", lb.FIRESTORE + ":commit", {})
    net.on("POST", lb.FIRESTORE + ":runQuery", [])
    status, r = _http(url, "/api/party/signin", {})
    q = {k: v[0] for k, v in parse_qs(urlparse(r["url"]).query).items()}
    assert status == 200 and r["url"].startswith(lb.SIGNIN_PAGE) and q["return"] == url + "/"
    assert _http(url, "/api/party")[1] == {"signed_in": False, "signing_in": True, "can_sign_in": True}
    status, r = _http(url, "/api/leaderboard/finish", {"state": q["state"], "token": "R0-from-the-hosted-page"})
    assert status == 200 and r["next"] == "party" and r["signed_in"] is True and r["joined"] is False
    assert net.commits() == [] and lb.read_state(cfg)["board"] is False  # not on the board: nothing sent
    lb.send(cfg, conn, force=True)
    lb.send_safely(cfg, conn)  # the sync's send
    assert net.commits() == []
    data = _http(url, "/api/party")[1]
    assert data["signed_in"] and not data["signing_in"] and data["who"]["board"] is False
    me = _http(url, "/api/leaderboard")[1]["me"]
    assert me["signed_in"] is True and me["joined"] is False and me["name"] == "Ada"
    status, r = _http(url, "/api/leaderboard/join", {})
    assert status == 200 and r["joined"] is True and len(net.commits()) == 1 and lb.read_state(cfg)["board"] is True
    net.on("DELETE", lb.FIRESTORE + "/players/uid1", {})
    status, r = _http(url, "/api/party/signout", {})  # on the board: signing out is leaving it
    assert status == 200 and r == {"signed_in": False, "signing_in": False, "can_sign_in": True}
    assert [c["method"] for c in net.calls].count("DELETE") == 1 and lb.read_state(cfg) is None
    assert _http(url, "/api/leaderboard/join", {})[0] == 502  # nobody signed in to join with


def test_the_leaderboards_own_sign_in_still_joins_the_board(hubweb, net):  # noqa: F811
    url, cfg = hubweb["url"], hubweb["cfg"]
    net.on("POST", lb.FIRESTORE + ":commit", {})
    state = parse_qs(urlparse(_http(url, "/api/leaderboard/signin", {})[1]["url"]).query)["state"][0]
    status, r = _http(url, "/api/leaderboard/finish", {"state": state, "token": "R0-from-the-hosted-page"})
    assert status == 200 and r["joined"] and "next" not in r and len(net.commits()) == 1
    assert lb.read_state(cfg)["board"] is True


def test_a_party_sign_in_keeps_an_account_already_on_the_board_on_it(home, net):  # noqa: F811
    cfg = home["cfg"]
    _joined(cfg)  # signed in on the board before the Party: no "board" in the state
    assert "board" not in lb.read_state(cfg) and lb.status(cfg)["joined"] is True and lb.status(cfg)["signed_in"]
    lb.sign_in(cfg, "R0-from-the-hosted-page", board=None)
    assert lb.read_state(cfg)["board"] is True
    lb.write_state(cfg, {**lb.read_state(cfg), "uid": "someone-else"})  # another account was on the board here
    lb.sign_in(cfg, "R0-from-the-hosted-page", board=None)
    assert lb.read_state(cfg)["board"] is False


def test_a_sign_in_from_before_the_party_still_sends(home, net):  # noqa: F811
    conn, cfg = home["conn"], home["cfg"]
    _joined(cfg)
    net.on("POST", lb.FIRESTORE + ":commit", {})
    lb.send(cfg, conn)
    assert len(net.commits()) == 1
    lb.write_state(cfg, {**lb.read_state(cfg), "board": False, "last_sent": None})
    assert lb.send(cfg, conn, force=True)["last_sent"] is None and len(net.commits()) == 1


def test_signing_out_off_the_board_forgets_the_sign_in_and_touches_no_row(hubweb, catalog, net):  # noqa: F811
    url, cfg = hubweb["url"], hubweb["cfg"]
    _sign_in(cfg, board=False)
    status, r = _http(url, "/api/party/signout", {})
    assert status == 200 and r["signed_in"] is False
    assert net.calls == [] and lb.read_state(cfg) is None  # no DELETE: another computer's row may be the account's


def test_the_cli_tells_a_party_sign_in_from_the_board_and_joins_from_it(home, net, capsys):  # noqa: F811
    from interlatch.interfaces.cli.main import main

    cfg = home["cfg"]
    net.on("POST", lb.FIRESTORE + ":commit", {})
    _sign_in(cfg, board=False)
    assert main(["leaderboard"]) == 0
    assert "for the Party, not on the leaderboard" in " ".join(capsys.readouterr().out.split())
    assert main(["leaderboard", "send"]) == 1 and net.commits() == []
    assert main(["leaderboard", "join"]) == 0 and len(net.commits()) == 1  # signed in already: joins without a browser
    assert lb.status(cfg)["joined"] and "On the leaderboard as" in capsys.readouterr().out
    lb.write_state(cfg, {**lb.read_state(cfg), "board": False})
    assert main(["leaderboard", "leave"]) == 0 and lb.read_state(cfg) is None
    assert "Signed out" in capsys.readouterr().out and not [c for c in net.calls if c["method"] == "DELETE"]


# ------------------------------------------------------------------ the quest log: each viewer's own
def test_each_viewer_keeps_their_own_quests_and_goal(team, catalog):  # noqa: F811
    url, cfg, conn, s = team["url"], team["cfg"], team["conn"], team["sessions"]
    _sign_in(cfg, board=False)
    status, data = _http(url, "/api/party/goal", {"track": "sre"})
    assert status == 200 and data["goal"] == {"id": "sre", "name": "sre track", "chosen": True}
    status, data = _http(url, "/api/party/quest", {"id": "sre-G1", "done": True})
    q = {x["id"]: x for x in data["quests"]}
    assert status == 200 and q["sre-G1"]["status"] == "done" and q["sre-G1"]["done_at"] and q["sre-G1"]["main"]
    assert set(json.loads(kv_get(conn, "party.quests:here"))) == {"sre-G1"}
    for who in ("Bob", "Vic"):  # a member, and a read-only person: signed in to the hub, their own log
        me = team[who.lower()]
        status, data = _http(url, "/api/party", headers=_as(s[who]))
        assert status == 200 and data["signed_in"] and data["can_sign_in"] is False
        assert data["who"] == {"kind": "hub", "name": who, "uid": None, "photo": False, "board": False}
        assert data["goal"] is None and data["quests"] == []  # no lessons of their own yet, no goal picked
        status, data = _http(url, "/api/party/goal", {"track": "backend"}, _as(s[who]))
        assert status == 200 and data["goal"] == {"id": "backend", "name": "backend track", "chosen": True}
        status, data = _http(url, "/api/party/quest", {"id": "backend-G2", "done": True}, _as(s[who]))
        assert status == 200 and {x["id"]: x["status"] for x in data["quests"]}["backend-G2"] == "done"
        assert data["journey"][0]["type"] == "quest"
        status, data = _http(url, "/api/party/quest", {"id": "backend-G2", "done": False}, _as(s[who]))
        assert status == 200 and {x["id"]: x["status"] for x in data["quests"]}["backend-G2"] == "open"
        assert _http(url, "/api/party/quest", {"id": "backend-G1", "done": True}, _as(s[who]))[0] == 200
        assert set(json.loads(kv_get(conn, f"party.quests:person:{me['id']}"))) == {"backend-G1"}
        status, r = _http(url, "/api/party/quest", {"id": "nope-G9", "done": True}, _as(s[who]))
        assert status == 400 and r["error"] == "no such quest"
        assert _http(url, "/api/party/goal", {"track": "nope"}, _as(s[who]))[0] == 400
        big = {"id": "backend-G3", "done": True, "padding": "x" * 5000}  # too big to read, though it's a fine quest
        assert _http(url, "/api/party/quest", big, _as(s[who])) == (400, {"error": "bad json"})
        assert "backend-G3" not in json.loads(kv_get(conn, f"party.quests:person:{me['id']}"))
        # and nothing more: a member still changes nothing else on the hub
        for path, body in (("/api/project-groups/save", {"name": "x"}), ("/api/suggestions/seen", {}),
                           ("/api/party/signout", {}), ("/api/party/signin", {}), ("/api/leaderboard/join", {})):
            status, r = _http(url, path, body, _as(s[who]))
            assert status == 403, (who, path)
    status, data = _http(url, "/api/party/goal", {"track": None}, _as(s["Bob"]))  # back to the default
    assert status == 200 and data["goal"] is None and kv_get(conn, f"party.goal:person:{team['bob']['id']}") is None
    data = _http(url, "/api/party")[1]  # this computer's log is still its own
    assert data["goal"]["id"] == "sre" and {x["id"]: x["status"] for x in data["quests"]}["sre-G1"] == "done"
    assert lb.read_state(cfg) is not None


def _post_length(url, path, headers, length, body=b""):
    """A POST that states its own Content-Length, which urllib won't let a test get wrong."""
    import http.client

    u = urlparse(url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    conn.putrequest("POST", path)
    for k, v in {"Content-Type": "application/json", "X-Interlatch": "1", **headers, "Content-Length": length}.items():
        conn.putheader(k, v)
    conn.endheaders()
    if body:
        conn.send(body)
    r = conn.getresponse()
    try:
        return r.status, json.loads(r.read() or b"{}")
    finally:
        conn.close()


def test_a_body_any_person_may_post_is_refused_unless_its_length_is_sound(team, catalog):  # noqa: F811
    url, conn, s = team["url"], team["conn"], team["sessions"]
    vic = team["vic"]  # read-only: these two paths are all a read-only person may POST
    fine = json.dumps({"id": "backend-G1", "done": True}).encode()
    for path in ("/api/party/quest", "/api/team/news/seen"):
        for length in ("-1", "abc", "4097"):  # -1 would read until the caller hangs up
            status, r = _post_length(url, path, _as(s["Vic"]), length, fine + b" " * 5000)
            assert (status, r) == (400, {"error": "bad json"}), (path, length)
    assert kv_get(conn, f"party.quests:person:{vic['id']}") is None
    assert _post_length(url, "/api/party/quest", _as(s["Vic"]), str(len(fine)), fine)[0] == 200
    assert set(json.loads(kv_get(conn, f"party.quests:person:{vic['id']}"))) == {"backend-G1"}


def test_a_hub_person_isnt_told_of_a_sign_in_running_at_the_hub(team, catalog):  # noqa: F811
    url, s = team["url"], team["sessions"]
    assert _http(url, "/api/party/signin", {})[0] == 200  # someone at the hub computer starts one
    assert _http(url, "/api/party")[1]["signing_in"] is True
    status, data = _http(url, "/api/party", headers=_as(s["Bob"]))
    assert status == 200 and data["signing_in"] is False and data["can_sign_in"] is False


def test_the_mac_app_bundles_every_data_file_the_package_reads():
    """The DMG copies only what Interlatch.spec lists: a file the code reads at run time that isn't there fails in the
    app alone (roadmap.json, the Party's catalog, was the first outside web/)."""
    from pathlib import Path

    root = Path(party.__file__).resolve().parents[3]
    spec = (root / "packaging" / "macos" / "Interlatch.spec").read_text()
    pkg = root / "src" / "interlatch"
    data = [p for p in pkg.rglob("*") if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts
            and pkg / "web" not in p.parents]
    assert data, "expected roadmap.json at least"
    for p in data:
        parts = '" / "'.join(p.relative_to(root).parts)
        assert f'"{parts}"' in spec, f"{p.relative_to(root)} isn't in packaging/macos/Interlatch.spec's datas"


def test_a_person_limited_to_projects_sees_the_party_of_those_projects(team, catalog):  # noqa: F811
    url, conn = team["url"], team["conn"]
    sam = people.add(conn, "Sam", "sam@example.com", "member", by=team["ada"], projects=[CWD])
    cookie = _as(people.open_browser(conn, people.invite(conn, sam["id"]), "test")[1])
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'sam-pc', 'spoke', ?)", (SAM_PC, sam["id"]))
    conn.commit()
    mine = _session(conn, machine=SAM_PC, source="remote")
    _lesson(conn, mine, "pytest in the project they see")
    _lesson(conn, _session(conn, machine=SAM_PC, source="remote", project=OTHER_CWD), "SQL query elsewhere",
            project=OTHER_CWD)
    conn.execute("INSERT INTO artifacts(session_id, key, kind, title, ts) VALUES (?, 'doc:1', 'doc', 'the migration', "
                 "'2026-09-02T00:00:00.000Z')", (mine,))
    conn.commit()
    status, data = _http(url, "/api/party", headers=cookie)
    assert status == 200 and _skills(data) == {"M-python": 1} and "SQL query" not in json.dumps(data)
    status, data = _http(url, "/api/party/goal", {"track": "backend"}, cookie)  # the POST answers the same way
    assert status == 200 and _skills(data) == {"M-python": 1} and "SQL query" not in json.dumps(data)
    assert {x["id"]: x for x in data["quests"]}["backend-G1"]["evidence"] == []  # artifacts read as empty for them
    status, sk = _http(url, "/api/party/skill?agent=claude&module=M-sql", headers=cookie)
    assert status == 200 and sk["xp"] == 0 and sk["lessons"] == []
    status, tk = _http(url, "/api/party/track?id=backend", headers=cookie)
    assert status == 200 and tk["coverage"]["claude"] > 0 and "SQL query" not in json.dumps(tk)
    assert _http(url, "/api/party/tracks", headers=cookie)[0] == 200


# ------------------------------------------------------------------ the catalog that ships
def test_the_real_roadmap_loads_and_computes_a_party(synced):
    if not party.CATALOG_PATH.exists():
        pytest.skip("roadmap.json isn't there yet")
    party.clear_cache()
    cat = party.load_catalog()
    raw = party.CATALOG_PATH.read_text(encoding="utf-8")
    for personal in ("Your connection", "Gap to check", "Your possible bridge", "For you", "Your starting hypothesis",
                     "Adrian"):
        assert personal not in raw
    assert {a["id"] for a in cat.attrs} == set(ATTRS)
    assert all(m.get("attr") in {a["id"] for a in cat.attrs} for m in cat.modules)
    assert all(m in cat.module for t in cat.tracks for m in t.get("modules") or [])
    assert all(t.get("category") in cat.categories for t in cat.tracks)
    assert all(m.get("keywords") for m in cat.modules)
    assert all(len(t.get("milestones") or []) == 4 and all(ms.get("keywords") for ms in t["milestones"]) for t in cat.tracks)
    # the index finds what each whole regex finds, on the catalog's own words (they hit its keywords a lot)
    texts = [str(v) for m in cat.modules for k, v in m.items() if k not in ("keywords", "id", "attr")]
    texts += [str(v) for t in cat.tracks for k, v in t.items() if k in ("name", "name_ja", "owned", "interview", "evidence")]
    texts += [ms["text"] + " " + ms.get("text_ja", "") for t in cat.tracks for ms in t["milestones"]]
    for text in texts + [" ".join(texts[i:i + 25]) for i in range(0, len(texts), 25)]:
        want = {i: n for i, kw in cat.keywords.items() if isinstance(i, int)
                and (n := sum(1 for m in kw.exact.finditer(text) if m.group(0)))}
        assert party.keyword_hits(cat, text) == want, text[:200]
        for key, kw in cat.keywords.items():
            if isinstance(key, str):
                assert party.milestone_hits(cat, key, text) == sum(1 for m in kw.exact.finditer(text) if m.group(0)), key
    conn, here = synced["conn"], local_machine(synced["cfg"])["id"]
    for title, body in (("Token TTL compared in seconds vs ms", "auth.py compared TTL in seconds against ms timestamps."),
                        ("Retry the deploy idempotently", "SQL migrations run once; pytest covers the retry."),
                        ("Prompt the LLM with the schema", "Evaluate the agent's answers before shipping.")):
        _lesson(conn, SID, title, body, stage="established")
    data = party.compute(conn, here)
    assert [a["agent"] for a in data["agents"]] == list(CODING_AGENTS) and data["catalog"]["modules"] == len(cat.modules)
    assert _agent(data)["counted"] >= 1 and all(len(_lesson_modules) <= party.MAX_MODULES for _lesson_modules in (
        k.modules for k in party.lessons_in(conn, cat, party._scope_of(conn, here, None))))
    json.dumps(data)
    token = i18n.lang.set("ja")
    try:
        json.dumps(party.compute(conn, here))
        assert party.tracks()["categories"]
    finally:
        i18n.lang.reset(token)
    party.clear_cache()
