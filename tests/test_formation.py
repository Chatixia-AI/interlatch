"""Formations (knowledge/formation.py; GET /api/party's "formations", POST /api/party/deck): the decks a party builds,
the one made for it until one is saved, every change's checks, one set of decks per scope, the deck the Arena fields,
and the sheet sent again when the team fielded changes."""

from __future__ import annotations

import json

import pytest

from interlatch.knowledge import formation
from interlatch.knowledge.party import PartyError
from interlatch.sharing import leaderboard as lb
from interlatch.storage.kv import kv_get, kv_set

from conftest import SID
from test_arena import TAILNET
from test_leaderboard import _joined, net  # noqa: F401  (net: the fixture)
from test_party import BOB_PC, _lesson, _session, catalog, home  # noqa: F401  (the fixtures)
from test_people_web import _as, hubweb, team  # noqa: F401  (the fixtures)
from test_people_web import _call as _http

pytestmark = pytest.mark.usefixtures("game_on")  # Innerlatch: game mode is off by default

LEVELS = {"claude": 19, "codex": 13, "copilot": 1, "bob": 2, "antigravity": 1, "opencode": 1}


def _stored(conn, key="here"):
    return json.loads(kv_get(conn, formation.DECKS_KEY + key))


# ------------------------------------------------------------------ the rules
def test_until_a_deck_is_saved_the_party_has_one_of_its_highest_levels(home):  # noqa: F811
    conn = home["conn"]
    assert formation.default_members(LEVELS) == ["claude", "codex", "bob"]
    assert formation.default_members(LEVELS, "opencode") == ["opencode", "claude", "codex"]  # the old champion leads
    assert formation.default_members(LEVELS, "chatgpt") == ["claude", "codex", "bob"]
    assert formation.default_members({}) == ["claude", "codex", "copilot"]  # level with each other: agent order
    got = formation.formations(conn, None, LEVELS)
    assert got == {"decks": [{"id": "d1", "name": "", "members": ["claude", "codex", "bob"]}], "arena": "d1",
                   "saved": False, "max": formation.DECKS_MAX, "team": formation.TEAM_MAX}
    assert kv_get(conn, formation.DECKS_KEY + "here") is None  # only shown, not kept
    assert formation.arena_members(conn, LEVELS, "bob") == ["bob", "claude", "codex"]


def test_a_deck_is_saved_renamed_reordered_and_added(home):  # noqa: F811
    conn = home["conn"]
    got = formation.save(conn, None, LEVELS, "d1", members=["codex", "claude"])  # the one made for it, now kept
    assert got["saved"] is True and got["decks"] == [{"id": "d1", "name": "", "members": ["codex", "claude"]}]
    got = formation.save(conn, None, LEVELS, "d1", name="  Night\tshift \n crew\x00 ")
    assert got["decks"][0] == {"id": "d1", "name": "Night shift crew", "members": ["codex", "claude"]}
    assert formation.save(conn, None, LEVELS, "d1", name="x" * 40)["decks"][0]["name"] == "x" * formation.NAME_MAX
    got = formation.save(conn, None, LEVELS, None, name="Second")  # a new deck: the highest levels, until changed
    assert got["decks"][1] == {"id": "d2", "name": "Second", "members": ["claude", "codex", "bob"]}
    assert got["arena"] == "d1" and _stored(conn)["decks"][1]["id"] == "d2"
    got = formation.save(conn, None, LEVELS, None, members=["opencode"])
    assert got["decks"][2]["members"] == ["opencode"]


@pytest.mark.parametrize("members", [[], ["claude"] * 2, ["claude", "codex", "bob", "copilot"], ["chatgpt"],
                                     ["claude", None], "claude", {"claude": 1}])
def test_a_deck_holds_one_to_three_agents_each_once(home, members):  # noqa: F811
    with pytest.raises(PartyError, match="one to three"):
        formation.save(home["conn"], None, LEVELS, "d1", members=members)
    assert kv_get(home["conn"], formation.DECKS_KEY + "here") is None


def test_at_most_five_decks_and_never_none(home):  # noqa: F811
    conn = home["conn"]
    for _ in range(formation.DECKS_MAX - 1):
        formation.save(conn, None, LEVELS)
    with pytest.raises(PartyError, match="at most 5"):
        formation.save(conn, None, LEVELS)
    formation.field(conn, None, LEVELS, "d3")
    got = formation.delete(conn, None, LEVELS, "d3")  # the arena's deck: the first one left takes its place
    assert [d["id"] for d in got["decks"]] == ["d1", "d2", "d4", "d5"] and got["arena"] == "d1"
    assert formation.save(conn, None, LEVELS)["decks"][-1]["id"] == "d3"  # the first free id
    for d in ("d2", "d3", "d4", "d5"):
        formation.delete(conn, None, LEVELS, d)
    with pytest.raises(PartyError, match="at least one"):
        formation.delete(conn, None, LEVELS, "d1")
    for bad in ("d9", None, "", "../x"):
        with pytest.raises(PartyError, match="no such deck"):
            formation.save(conn, None, LEVELS, bad, name="x") if bad is not None else formation.field(conn, None, LEVELS, bad)


def test_what_is_stored_is_checked_as_it_is_read(home):  # noqa: F811
    conn = home["conn"]
    kv_set(conn, formation.DECKS_KEY + "here", json.dumps({"arena": "d7", "decks": [
        {"id": "d1", "name": "ok", "members": ["codex"]}, {"id": "d1", "name": "twin", "members": ["bob"]},
        {"id": "x", "members": ["bob"]}, {"id": "d2", "members": ["bob", "bob"]}, "junk",
        {"id": "d3", "name": 7, "members": ["bob", "claude"]}]}))
    assert formation.load(conn, "here") == {"arena": None, "decks": [
        {"id": "d1", "name": "ok", "members": ["codex"]}, {"id": "d3", "name": "", "members": ["bob", "claude"]}]}
    assert formation.formations(conn, None, LEVELS)["arena"] == "d1"  # an arena deck that isn't there: the first
    kv_set(conn, formation.DECKS_KEY + "here", "not json")
    assert formation.formations(conn, None, LEVELS)["saved"] is False


# ------------------------------------------------------------------ the dashboard
def test_decks_are_behind_the_partys_sign_in_and_kept_here(hubweb, catalog, net):  # noqa: F811
    url, cfg, conn = hubweb["url"], hubweb["cfg"], hubweb["conn"]
    for _ in range(3):
        _lesson(conn, SID, "pytest secrets")
    status, r = _http(url, "/api/party/deck", {"id": "d1", "name": "x"})
    assert status == 403 and r["signin"] is True
    _joined(cfg, board=False)
    status, p = _http(url, "/api/party")
    assert status == 200 and p["formations"]["saved"] is False and p["formations"]["decks"][0]["members"][0] == "claude"
    status, r = _http(url, "/api/party/deck", {"id": "d1", "members": ["codex", "claude"], "name": "Main"})
    assert status == 200 and r["formations"]["decks"] == [{"id": "d1", "name": "Main", "members": ["codex", "claude"]}]
    status, r = _http(url, "/api/party/deck", {})  # a new deck
    assert status == 200 and [d["id"] for d in r["formations"]["decks"]] == ["d1", "d2"]
    status, r = _http(url, "/api/party/deck", {"id": "d2", "arena": True})
    assert status == 200 and r["formations"]["arena"] == "d2" and net.commits() == []  # not in the arena: nothing sent
    status, r = _http(url, "/api/party/deck", {"id": "d2", "delete": True})
    assert status == 200 and r["formations"]["arena"] == "d1"
    status, r = _http(url, "/api/party/deck", {"id": "d1", "members": ["codex"] * 2}, {"X-Interlatch-Lang": "ja"})
    assert status == 400 and r["error"] == "デッキには 1〜3 人のエージェントを、重複なしで入れてください"
    for bad in ({"id": "d1", "delete": True}, {"id": ["d1"], "name": "x"}, {"id": 1, "name": "x"}):
        assert _http(url, "/api/party/deck", bad)[0] == 400, bad
    assert _http(url, "/api/party")[1]["formations"]["decks"][0]["name"] == "Main"


def test_changing_the_fielded_team_sends_the_sheet_when_in_the_arena(hubweb, catalog, net):  # noqa: F811
    url, cfg = hubweb["url"], hubweb["cfg"]
    net.on("POST", lb.FIRESTORE + ":commit", {})
    net.on("GET", lb.FIRESTORE + "/players/uid1", {"fields": {"machine": {"stringValue": lb.machine_tag(cfg)}}})
    _joined(cfg, board=True, arena=True)
    status, r = _http(url, "/api/party/deck", {"id": "d1", "name": "Renamed"})  # the same team: nothing to send
    assert status == 200 and net.commits() == []
    _http(url, "/api/party/deck", {})
    assert net.commits() == []  # another deck: the arena still fields d1
    _http(url, "/api/party/deck", {"id": "d2", "members": ["bob", "codex"]})
    assert net.commits() == []
    _http(url, "/api/party/deck", {"id": "d2", "arena": True})
    (commit,) = net.commits()
    assert lb._plain(commit["body"]["writes"][1]["update"]["fields"])["lineup"] == ["bob", "codex"]
    _http(url, "/api/party/deck", {"id": "d2", "members": ["codex", "bob"]})  # the fielded deck reordered
    assert lb._plain(net.commits()[-1]["body"]["writes"][1]["update"]["fields"])["lineup"] == ["codex", "bob"]
    assert _http(url, "/api/party/deck", {"id": "d1", "arena": True}, headers=TAILNET)[0] == 403  # from here only


def test_a_member_keeps_their_own_decks_and_never_fields_one(team, catalog):  # noqa: F811
    url, conn, s = team["url"], team["conn"], team["sessions"]
    bob = team["bob"]
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'bob-pc', 'spoke', ?)", (BOB_PC, bob["id"]))
    conn.commit()
    _lesson(conn, _session(conn, machine=BOB_PC, source="remote"), "SQL query on bob's computer")
    status, r = _http(url, "/api/party/deck", {"id": "d1", "members": ["bob"]}, _as(s["Bob"]))  # members may
    assert status == 200 and r["formations"]["decks"][0]["members"] == ["bob"]
    assert formation.load(conn, f"person:{bob['id']}")["decks"][0]["members"] == ["bob"]
    assert kv_get(conn, formation.DECKS_KEY + "here") is None
    assert _http(url, "/api/party", headers=_as(s["Vic"]))[1]["formations"]["saved"] is False  # Vic's own
    status, r = _http(url, "/api/party/deck", {"id": "d1", "arena": True}, _as(s["Bob"]))
    assert status == 400 and r["error"] == "no such deck"  # a hub person's party never enters the arena
