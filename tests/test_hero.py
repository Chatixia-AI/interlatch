"""The hero (knowledge/hero.py; the `hero` block of GET /api/party, POST /api/party/hero): the person as a character
of their own, made once on their own computer, on a path (a track or the Generalist path), levelled only by what
they answered right in study battles. The agents are companions: their levels never move."""

from __future__ import annotations

import json
from datetime import date

import pytest

from interlatch.analysis.runners import factory
from interlatch.knowledge import hero, party, study
from interlatch.storage.kv import kv_get

from conftest import SID
from test_leaderboard import _joined
from test_party import BOB_PC, _lesson, _session, catalog, home  # noqa: F401  (the fixtures)
from test_people_web import _as, hubweb, team  # noqa: F401  (the fixtures)
from test_people_web import _call as _http
from test_study import QUESTION, FakeModel

pytestmark = pytest.mark.usefixtures("game_on")  # Innerlatch: game mode is off by default

LOOK = {"body": 1, "skin": 2, "hair": 3, "hair_color": 4, "outfit": 5, "accessory": 1}


def _log(*answers, lid=1):
    return [{"id": lid, "on": on, "verdict": v} for on, v in answers]


# ------------------------------------------------------------------ the formulas
def test_xp_comes_from_right_answers_returns_and_owning_never_from_wrong_ones():
    assert hero.xp_of([]) == 0
    assert hero.xp_of(_log(("2026-10-01", "not_yet"), ("2026-10-02", "partly"))) == 0
    assert hero.xp_of(_log(("2026-10-01", "got_it"))) == hero.XP_FIRST
    # right on two separate days in a row: a return, and owned
    assert hero.xp_of(_log(("2026-10-01", "got_it"), ("2026-10-04", "got_it"))) == hero.XP_FIRST + hero.XP_AGAIN + hero.XP_OWNED
    # one answer a day counts: the day's last
    assert hero.xp_of(_log(("2026-10-01", "got_it"), ("2026-10-01", "got_it"))) == hero.XP_FIRST
    assert hero.xp_of(_log(("2026-10-01", "not_yet"), ("2026-10-01", "got_it"))) == hero.XP_FIRST
    # a miss in between: the streak starts over, the right answers still count, owned once only
    log_ = _log(("2026-10-01", "got_it"), ("2026-10-02", "not_yet"), ("2026-10-03", "got_it"), ("2026-10-04", "got_it"),
                ("2026-10-05", "got_it"))
    assert hero.xp_of(log_) == hero.XP_FIRST + 3 * hero.XP_AGAIN + hero.XP_OWNED
    # per lesson: two lessons each earn their own first
    assert hero.xp_of(_log(("2026-10-01", "got_it"), lid=1) + _log(("2026-10-01", "got_it"), lid=2)) == 2 * hero.XP_FIRST


def test_the_level_curve():
    assert [hero.level_of(x) for x in (0, 9, 10, 39, 40, 90)] == [1, 1, 2, 2, 3, 4]
    for lv in range(1, 30):
        assert hero.level_of(hero.threshold(lv)) == lv and hero.level_of(hero.threshold(lv + 1) - 1) == lv


# ------------------------------------------------------------------ the paths
def test_the_generalist_path_comes_from_the_catalog(catalog):  # noqa: F811
    # three tracks: python and auth are in two of them (at least half); none is critical in five
    assert hero.generalist(catalog) == ["M-python", "M-auth"]
    assert hero.path_modules(catalog, hero.GENERALIST) == [{"id": "M-python", "target": 2, "critical": False},
                                                            {"id": "M-auth", "target": 2, "critical": False}]
    assert [m["id"] for m in hero.path_modules(catalog, "backend")] == ["M-python", "M-sql", "M-auth"]
    assert [m["critical"] for m in hero.path_modules(catalog, "backend")] == [False, True, False]
    assert hero.path_modules(catalog, None) == [] and hero.path_modules(catalog, "nope") == []


def test_the_real_catalogs_generalist_path():
    party.clear_cache()
    cat = party.load_catalog()
    # what docs/design/innerlatch.md section 2.3 lists
    assert hero.generalist(cat) == ["M-python", "M-sql", "M-swe", "M-systems", "M-cloud", "M-stats", "M-eval",
                                    "M-product", "M-customer", "M-architecture", "M-g-linux"]


# ------------------------------------------------------------------ the sheet
def test_the_sheet_is_made_here_only_and_checked(home):  # noqa: F811
    conn = home["conn"]
    assert hero.load(conn) is None
    got = hero.save(conn, None, "  Ada   Lovelace ", LOOK)
    assert got["name"] == "Ada Lovelace" and got["look"] == LOOK
    assert hero.load(conn) == {"name": "Ada Lovelace", "look": LOOK, "at": got["at"]}
    again = hero.save(conn, None, "Ada", {"body": 0})  # a part left out is the first choice; made when first made
    assert again["look"]["skin"] == 0 and again["at"] == got["at"]
    assert len(hero.save(conn, None, "x" * 99, LOOK)["name"]) == hero.NAME_MAX
    for name in ("", "   ", None, 3):
        with pytest.raises(hero.HeroError, match="name"):
            hero.save(conn, None, name, LOOK)
    for look in (None, [], {"body": 3}, {"body": -1}, {"body": True}, {"body": "1"}, {"wings": 1}):
        with pytest.raises(hero.HeroError, match="look"):
            hero.save(conn, None, "Ada", look)
    with pytest.raises(hero.HeroError, match="your own computer"):
        hero.save(conn, {"id": 1}, "Ada", LOOK)
    assert hero.load(conn)["name"] == "x" * hero.NAME_MAX  # nothing refused changed it


def test_the_generalist_path_is_a_goal_without_a_tracks_milestones(home):  # noqa: F811
    conn, here = home["conn"], home["here"]
    _lesson(conn, _session(conn), "pytest fixtures", kind="fix")
    party.set_goal(conn, None, hero.GENERALIST)
    data = party.compute(conn, here)
    assert data["goal"] == {"id": "generalist", "name": "Generalist", "chosen": True, "generalist": True}
    assert not [q for q in data["quests"] if q["main"]]
    assert "generalist" not in party.payload_tracks(data)
    assert party.adventure_map(conn, here)["goal"]["modules"] == ["M-python", "M-auth"]
    party.set_goal(conn, None, "backend")
    party.clear_cache()
    assert party.compute(conn, here)["goal"] == {"id": "backend", "name": "backend track", "chosen": True}


def test_the_hero_levels_from_study_and_the_agents_never_move(home):  # noqa: F811
    conn, cfg, here = home["conn"], home["cfg"], home["here"]
    lid = _lesson(conn, _session(conn), "pytest and a token", kind="fix")
    data = party.compute(conn, here)
    before = data["agents"]
    made = hero.view(conn, here, None, data)
    assert made["made"] is False and made["looks"] == hero.LOOK and made["generalist"]["modules"] == 2
    assert hero.view(conn, here, {"id": 1}, data) is None  # a hub person: no hero
    hero.save(conn, None, "Ada", LOOK)
    party.set_goal(conn, None, "backend")
    party.clear_cache()
    data = party.compute(conn, here)
    v = hero.view(conn, here, None, data)
    assert (v["made"], v["name"], v["level"], v["xp"], v["understood"]) == (True, "Ada", 1, 0, 0)
    assert v["path"]["id"] == "backend" and [m["id"] for m in v["path"]["modules"]] == ["M-python", "M-sql", "M-auth"]
    model = FakeModel(QUESTION())
    study.pick(conn, cfg, "here", lid, 1, here=True, runner=model, today=date(2026, 10, 1))
    study.pick(conn, cfg, "here", lid, 1, here=True, runner=model, today=date(2026, 10, 4))
    v = hero.view(conn, here, None, data, today=date(2026, 10, 5))
    assert v["xp"] == 35 and v["level"] == 2 and (v["level_xp"], v["next_xp"]) == (10, 40)
    assert v["understood"] == 1 and v["owned"] == 1
    assert next(m for m in v["path"]["modules"] if m["id"] == "M-python")["ready"] == 1
    assert all(m["rank"] == 0 for m in v["path"]["modules"])  # ranks come only from gates
    party.clear_cache()
    assert party.compute(conn, here)["agents"] == before


# ------------------------------------------------------------------ the dashboard
def test_the_hero_is_made_over_http_with_its_path(hubweb, catalog, monkeypatch):  # noqa: F811
    url, cfg, conn = hubweb["url"], hubweb["cfg"], hubweb["conn"]
    lid = _lesson(conn, SID, "pytest secrets", kind="fix")
    status, r = _http(url, "/api/party/hero", {"name": "Ada", "look": LOOK})
    assert status == 403 and r["signin"] is True
    _joined(cfg, board=False)
    status, r = _http(url, "/api/party")
    assert status == 200 and r["hero"]["made"] is False
    status, r = _http(url, "/api/party/hero", {"name": "Ada", "look": LOOK, "path": "no-such-track"})
    assert status == 400 and r["error"] == "no such track" and kv_get(conn, hero.HERO_KEY + "here") is None
    status, r = _http(url, "/api/party/hero", {"name": "", "look": LOOK, "path": "backend"})
    assert status == 400 and r["error"] == "give your hero a name"
    status, r = _http(url, "/api/party/hero", {"name": "Ada", "look": LOOK, "path": "generalist"})
    assert status == 200 and r["hero"]["made"] is True and r["hero"]["name"] == "Ada"
    assert r["goal"]["generalist"] is True and r["hero"]["path"]["generalist"] is True
    assert [m["id"] for m in r["hero"]["path"]["modules"]] == ["M-python", "M-auth"]
    status, r = _http(url, "/api/party/hero", {"name": "Ada B", "look": {**LOOK, "hair": 0}})  # no path: it stays
    assert status == 200 and r["hero"]["name"] == "Ada B" and r["hero"]["look"]["hair"] == 0
    assert r["goal"]["id"] == "generalist"
    monkeypatch.setattr(factory, "make_runner", lambda cfg: FakeModel(QUESTION()))
    _http(url, f"/api/party/ask?id={lid}")
    status, r = _http(url, "/api/party/pick", {"id": lid, "choice": 1})
    assert status == 200 and r["hero"] == {"xp": 10, "gained": 10, "level": 2, "was_level": 1}
    status, r = _http(url, "/api/party/pick", {"id": lid, "choice": 1})  # the same day: nothing more
    assert r["hero"]["gained"] == 0
    assert json.loads(kv_get(conn, hero.HERO_KEY + "here"))["name"] == "Ada B"


def test_a_hub_person_has_no_hero(team, catalog):  # noqa: F811
    url, cfg, conn, s = team["url"], team["cfg"], team["conn"], team["sessions"]
    _joined(cfg, board=False)
    bob = team["bob"]
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'bob-pc', 'spoke', ?)", (BOB_PC, bob["id"]))
    conn.commit()
    _lesson(conn, _session(conn, machine=BOB_PC, source="remote"), "SQL query on bob's computer")
    status, r = _http(url, "/api/party", headers=_as(s["Bob"]))
    assert status == 200 and r["signed_in"] is True and r["hero"] is None
    status, r = _http(url, "/api/party/hero", {"name": "Bob", "look": LOOK}, _as(s["Bob"]))
    assert status == 403 and "your own computer" in r["error"]
    assert kv_get(conn, hero.HERO_KEY + "here") is None
    # a hub person still picks a path of their own: the Generalist one too
    status, r = _http(url, "/api/party/goal", {"track": "generalist"}, _as(s["Bob"]))
    assert status == 200 and r["goal"]["generalist"] is True
