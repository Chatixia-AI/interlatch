"""The Arena (knowledge/arena.py, sharing/arena.py): the battle rules (deterministic, the same whichever player
computes it, a new seed each week, the stat formulas, specials once, the round cap and its tiebreak, the log's size),
the sheet a party publishes (exactly the contract's fields, nothing about any lesson), the opt-in (only from the board,
sent with the row only while in, gone when leaving), the arena board and a battle through the dashboard, and the
Firestore rules. Nothing reaches Firebase: test_leaderboard's FakeNet answers."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest

from interlatch.core.agents import CODING_AGENTS
from interlatch.knowledge import arena as rules
from interlatch.knowledge import formation
from interlatch.sharing import arena
from interlatch.sharing import leaderboard as lb

from test_leaderboard import _joined, net  # noqa: F401  (net: the fixture)
from test_party import _lesson, _session, catalog, home  # noqa: F401  (fixtures)
from test_people_web import TAILNET, hubweb  # noqa: F401
from test_people_web import _call as _http

pytestmark = pytest.mark.usefixtures("game_on")  # Innerlatch: game mode is off by default

RULES = Path(__file__).resolve().parent.parent / "leaderboard" / "firestore.rules"
WEEK = "2026-W42"


def _agent(level=10, tier="novice", cls=None, **attrs):
    return {"level": level, "tier": tier, "cls": cls, "attrs": {k: attrs.get(k, 0) for k in rules.ATTRS}}


def _party(**per_agent):
    return {n: per_agent.get(n, _agent(level=1)) for n in CODING_AGENTS}


STRONG = _party(claude=_agent(29, "master", "backend", craft=21, data=8, ops=15, guard=11, arcana=8, lore=8),
                codex=_agent(13, "expert", "frontend", craft=9, data=3, ops=6, guard=5, arcana=4, lore=3),
                copilot=_agent(4, "novice", craft=2, ops=1), bob=_agent(3), antigravity=_agent(3, craft=1))
WEAK = _party(claude=_agent(6, "adept", craft=4, data=2, ops=2, guard=2, arcana=1, lore=1), codex=_agent(2))


# ------------------------------------------------------------------ the rules
def _sheet(agents, champion=None, lineup=None):
    return {"agents": agents, "champion": champion, **({"lineup": lineup} if lineup is not None else {})}


def test_stats_follow_the_formulas():
    s = rules.stats(_agent(10, "adept", craft=5, data=20, ops=7, guard=3, arcana=9, lore=12))
    assert (s["hp"], s["atk"], s["def"], s["spd"], s["crit"], s["heal"]) == (20 + 40 + 6, 5 + 5 + 4, 3 + 6, 7 + 5,
                                                                            5 + 10, 6)
    assert rules.stats(_agent(1, data=100))["crit"] == 30  # capped
    assert rules.stats(_agent(1, lore=9))["heal"] == 0 and rules.stats(_agent(1, lore=10))["heal"] == 5
    odd = rules.clean_agent({"level": 999, "tier": "god", "cls": "Not A Track!", "attrs": {"craft": -3, "data": "9"}})
    assert odd == {"level": 200, "tier": "novice", "cls": None, "attrs": dict.fromkeys(rules.ATTRS, 0)}
    assert rules.power(STRONG) == 29 + 13 + 4 + 3 + 3 + 1  # opencode: level 1 by default


def test_the_champion_is_the_one_picked_else_the_highest_level():
    assert rules.default_champion(STRONG) == "claude"
    assert rules.default_champion(_party(codex=_agent(9), bob=_agent(9))) == "codex"  # ties: CODING_AGENTS order
    assert rules.default_champion(_party()) == "claude"
    assert rules.champion_of(STRONG, "bob") == "bob"
    assert rules.champion_of(STRONG, "chatgpt") == rules.champion_of(STRONG, None) == "claude"  # not one of the five
    out = rules.match("x", _sheet(STRONG, "bob"), "y", _sheet(WEAK), WEEK)
    assert out["champions"] == {"a": "bob", "b": "claude"} and set(out["units"]) == {"a:bob", "b:claude"}


def test_a_duel_is_the_same_whoever_computes_it():
    one = rules.match("uid-b", _sheet(WEAK), "uid-a", _sheet(STRONG), WEEK)
    two = rules.match("uid-a", _sheet(STRONG), "uid-b", _sheet(WEAK), WEEK)
    assert one == two and one["a"] == "uid-a" and one["winner_uid"] == "uid-a"  # sides: the uids sorted
    random.seed(7)
    expected = random.random()
    random.seed(7)
    again = rules.battle(_sheet(STRONG), _sheet(WEAK), rules.seed_for(WEEK, "uid-a", "uid-b"))
    assert again == {k: two[k] for k in ("winner", "rounds", "log", "units", "champions", "lineups")}
    assert random.random() == expected  # the global random generator is never touched
    claude = two["units"]["a:claude"]
    assert {k: claude[k] for k in ("hp", "tier", "level")} == {"hp": 20 + 4 * 29 + 2 * 11, "tier": "master", "level": 29}
    assert two["units"]["b:claude"]["end_hp"] == 0 and claude["end_hp"] > 0  # where each ended: b fell
    assert {e["src"] for e in two["log"] if e["src"]} <= {"a:claude", "b:claude"}  # one champion a side


def test_each_week_has_its_own_seed():
    assert rules.seed_for(WEEK, "x", "y") == rules.seed_for(WEEK, "y", "x") != rules.seed_for("2026-W43", "x", "y")
    even = _sheet(_party(claude=_agent(8, craft=4, ops=3, guard=2, data=6)))
    logs = {json.dumps(rules.match("x", even, "y", even, f"2026-W{w}")["log"]) for w in range(40, 46)}
    assert len(logs) > 1  # the same two champions fight a different duel from week to week


@pytest.mark.parametrize("tier", ["adept", "expert", "master"])
def test_a_tier_special_fires_once(tier):
    fighter = _sheet(_party(claude=_agent(30, tier, craft=8, guard=3, ops=3)))
    for w in range(30, 40):
        log = rules.match("x", fighter, "y", fighter, f"2026-W{w}")["log"]
        specials = [e for e in log if e["t"] == "special"]
        assert specials and all(e["name"] == rules.SPECIALS[tier] for e in specials)
        assert len({e["src"] for e in specials}) == len(specials) <= 2  # once for each champion
        if tier == "adept":
            assert all(e["dst"] == e["src"] and e["n"] > 0 for e in specials)  # a heal of its own
        if tier == "master":
            assert all(e["dst"] != e["src"] and e["n"] > 0 for e in specials)  # one big hit on the other
    novice = _sheet(_party(claude=_agent(30, craft=8, guard=3)))
    assert not [e for e in rules.match("x", novice, "y", novice, WEEK)["log"] if e["t"] == "special"]


def test_overdrive_hits_two_and_a_half_times_as_hard():
    master = _sheet(_party(claude=_agent(30, "master", craft=10, guard=4)))
    log = rules.match("x", master, "y", master, WEEK)["log"]
    od = next(e for e in log if e["t"] == "special")
    hits = [e["n"] for e in log if e["t"] == "hit" and e["src"] == od["src"]]
    assert hits and od["n"] >= 2 * max(hits)  # 2.5 × a normal hit (each ±15%)


def test_a_lore_champion_heals_itself():
    healer = _sheet(_party(claude=_agent(30, guard=6, lore=40, craft=6)))
    heals = [e for e in rules.match("x", healer, "y", healer, WEEK)["log"] if e["t"] == "heal"]
    assert heals and all(e["src"] == e["dst"] and e["n"] <= 20 for e in heals)


def test_a_stalemate_ends_after_forty_rounds_and_the_seed_breaks_a_tie():
    wall = _sheet(_party(claude=_agent(200, guard=100, lore=100, ops=10)))  # 1 damage a hit, big heals
    winners = set()
    for w in range(30, 50):
        out = rules.match("x", wall, "y", wall, f"2026-W{w}")
        assert out["rounds"] == rules.MAX_ROUNDS and len(out["log"]) <= rules.LOG_MAX
        assert out["log"][-1]["t"] == "end" and out["log"][-1]["name"] == out["winner"]
        winners.add(out["winner"])
    assert winners == {"a", "b"}  # level on HP: the week's seed decides, not the side
    out = rules.match("x", _sheet(STRONG), "y", _sheet(WEAK), WEEK)
    assert out["winner_uid"] == "x" and out["rounds"] < rules.MAX_ROUNDS and len(out["log"]) <= rules.LOG_MAX
    assert {e["t"] for e in out["log"]} <= {"hit", "crit", "heal", "special", "ko", "end"}
    assert all(set(e) == {"r", "t", "src", "dst", "n", "hp", "name"} for e in out["log"])


def test_the_log_is_cut_at_400_and_the_end_says_where_they_stood(monkeypatch):
    monkeypatch.setattr(rules, "MAX_ROUNDS", 400)
    wall = _sheet(_party(claude=_agent(200, guard=100, lore=100, ops=10)))
    out = rules.match("x", wall, "y", wall, WEEK)
    assert len(out["log"]) == rules.LOG_MAX and out["log"][-1]["t"] == "end"
    assert all(0 < u["end_hp"] <= u["hp"] for u in out["units"].values())


def test_a_lineup_is_up_to_three_agents_each_once_else_the_champion():
    assert rules.lineup_of(STRONG, ["codex", "bob", "claude"]) == ["codex", "bob", "claude"]
    assert rules.lineup_of(STRONG, ["codex", "codex", "chatgpt", 7, ["x"], "bob"]) == ["codex", "bob"]
    assert rules.lineup_of(STRONG, ["bob", "codex", "claude", "copilot"]) == ["bob", "codex", "claude"]  # at most three
    assert rules.lineup_of(STRONG, [], "bob") == rules.lineup_of(STRONG, None, "bob") == ["bob"]  # a sheet without one
    assert rules.lineup_of(STRONG, "claude") == ["claude"]  # not a list: the champion (the highest level)
    one = rules.battle(_sheet(STRONG, lineup=["codex"]), _sheet(WEAK, lineup=["claude"]), "ab12")
    assert one == rules.battle(_sheet(STRONG, "codex"), _sheet(WEAK), "ab12")  # a lineup of one: the champion's duel


def test_lineups_fight_in_order_and_the_winner_stays_on():
    a, b = ["copilot", "codex", "claude"], ["claude", "codex"]
    out = rules.match("x", _sheet(STRONG, lineup=a), "y", _sheet(WEAK, lineup=b), WEEK)
    assert out["lineups"] == {"a": a, "b": b} and out["champions"] == {"a": "copilot", "b": "claude"}
    assert {k: u["order"] for k, u in out["units"].items()} == {
        "a:copilot": 0, "a:codex": 1, "a:claude": 2, "b:claude": 0, "b:codex": 1}
    assert out["winner"] == "a" and all(out["units"][f"b:{n}"]["end_hp"] == 0 for n in b)  # all of b fell
    # bout by bout: the two in the ring are the first of each side still standing; a newcomer steps in ("in")
    fighting, hp = {"a": 0, "b": 0}, {}
    for e in out["log"][:-1]:
        ring = {f"a:{a[fighting['a']]}", f"b:{b[fighting['b']]}"}
        if e["t"] == "in":
            assert e["src"] in ring and e["src"] not in hp  # its first time in
        else:
            assert {e["src"], e["dst"]} - {None} <= ring, e
        if e["dst"]:
            hp[e["dst"]] = e["hp"]
        if e["t"] in ("hit", "crit", "heal", "special"):
            hp.setdefault(e["src"], None)
        if e["t"] == "ko":
            fighting[e["dst"][0]] += 1
    ins = [e["src"] for e in out["log"] if e["t"] == "in"]
    assert ins and len(ins) == len(set(ins)) and "a:copilot" not in ins and "b:claude" not in ins  # the firsts start
    # whoever won a bout went on with the HP it had left: never healed back to full in between
    first_ko = next(i for i, e in enumerate(out["log"]) if e["t"] == "ko")
    winner = out["log"][first_ko]["src"]
    later = next((e for e in out["log"][first_ko + 1:] if e["dst"] == winner), None)
    assert later is None or later["hp"] < out["units"][winner]["hp"]


def test_a_bout_out_of_time_puts_the_one_with_less_hp_out():
    wall = _agent(200, guard=100, lore=100, ops=10)  # 1 damage a hit, big heals
    sheet = _sheet(_party(claude=wall, codex=_agent(2)), lineup=["claude", "codex"])
    outs = []
    for w in range(30, 40):
        out = rules.match("x", sheet, "y", _sheet(_party(claude=wall)), f"2026-W{w}")
        (gone,) = [e for e in out["log"] if e["t"] == "out"][:1]
        assert gone["r"] == rules.MAX_ROUNDS and gone["dst"] in ("a:claude", "b:claude") and gone["hp"] > 0
        assert out["units"][gone["dst"]]["out"] is True and out["units"][gone["dst"]]["end_hp"] > 0
        if gone["dst"] == "b:claude":  # b had one: a wins at once
            assert out["winner"] == "a" and out["rounds"] == rules.MAX_ROUNDS
        else:  # a's codex steps in against b's wall
            assert {"t": "in", "src": "a:codex"}.items() <= next(e for e in out["log"] if e["t"] == "in").items()
            assert out["winner"] == "b" and out["rounds"] > rules.MAX_ROUNDS
        outs.append(gone["dst"])
    assert set(outs) == {"a:claude", "b:claude"}  # level on HP: the seed decides


def test_standings_play_every_party_against_every_other():
    table = rules.standings({"x": _sheet(STRONG), "y": _sheet(WEAK), "z": _sheet(_party())}, WEEK)
    assert table["x"] == {"wins": 2, "losses": 0} and table["z"] == {"wins": 0, "losses": 2}
    assert sum(t["wins"] for t in table.values()) == 3 == sum(t["losses"] for t in table.values())


# ------------------------------------------------------------------ the sheet
def test_the_sheet_holds_levels_tiers_classes_and_attributes_only(home, net):  # noqa: F811
    conn, cfg = home["conn"], home["cfg"]
    sid = _session(conn)
    for n in range(12):
        _lesson(conn, sid, f"pytest SECRET-TITLE-{n}", "SECRET-BODY sql query", stage="canonical")
    _joined(cfg, board=True, arena=True)
    sheet = arena.sheet(cfg, conn, lb.read_state(cfg))
    assert set(sheet) == {"name", "photo", "machine", "v", "agents", "lineup", "champion"} and sheet["v"] == rules.SHEET_V == 3
    # no deck saved: the one made for the party, its highest levels (ties: agent order); the first is the champion
    assert sheet["lineup"] == ["claude", "codex", "copilot"] and sheet["champion"] == "claude"
    lb.write_state(cfg, {**lb.read_state(cfg), "champion": "not-an-agent"})  # a hand-edited state: the default
    assert arena.sheet(cfg, conn, lb.read_state(cfg))["lineup"] == ["claude", "codex", "copilot"]
    lb.write_state(cfg, {**lb.read_state(cfg), "champion": "bob"})  # the champion picked before decks leads
    assert arena.sheet(cfg, conn, lb.read_state(cfg))["lineup"] == ["bob", "claude", "codex"]
    formation.save(conn, None, {}, "d1", members=["opencode", "codex"])  # a deck saved: it, in its order
    assert arena.sheet(cfg, conn, lb.read_state(cfg))["lineup"] == ["opencode", "codex"]
    assert arena.sheet(cfg, conn, lb.read_state(cfg))["champion"] == "opencode"
    formation.save(conn, None, {}, None, members=["claude"])
    formation.field(conn, None, {}, "d2")
    assert arena.sheet(cfg, conn, lb.read_state(cfg))["lineup"] == ["claude"]
    lb.write_state(cfg, {**lb.read_state(cfg), "champion": None})
    assert set(sheet["agents"]) == set(CODING_AGENTS)
    assert all(set(a) == {"level", "tier", "cls", "attrs"} and set(a["attrs"]) == set(rules.ATTRS)
               for a in sheet["agents"].values())
    assert sheet["agents"]["claude"]["level"] > 1 and "SECRET" not in json.dumps(sheet)
    assert sheet["machine"] == lb.machine_tag(cfg) and sheet["name"] == "Ada"
    net.on("POST", lb.FIRESTORE + ":commit", {})
    lb.send(cfg, conn, force=True)
    (commit,) = net.commits()
    players, sent = commit["body"]["writes"]
    assert sent["update"]["name"].endswith("/documents/arena/uid1")
    assert sent["updateTransforms"] == [{"fieldPath": "at", "setToServerValue": "REQUEST_TIME"}]
    assert lb._plain(sent["update"]["fields"]) == arena.sheet(cfg, conn, lb.read_state(cfg))
    assert sent["update"]["fields"]["lineup"] == {"arrayValue": {"values": [{"stringValue": "claude"}]}}
    assert "SECRET" not in json.dumps(commit)


def test_the_sheet_goes_only_while_in_the_arena(home, net):  # noqa: F811
    conn, cfg = home["conn"], home["cfg"]
    net.on("POST", lb.FIRESTORE + ":commit", {})
    net.on("GET", lb.FIRESTORE + "/players/uid1", {"fields": {"machine": {"stringValue": lb.machine_tag(cfg)}}})
    _joined(cfg)  # on the board, never opted in (a state from before the arena)
    lb.send(cfg, conn, force=True)
    assert net.commits()[-1]["body"]["writes"][1] == {"delete": f"{lb.DB}/arena/uid1"}  # no stale sheet survives
    assert lb.status(cfg)["arena"] is False
    lb.write_state(cfg, {**lb.read_state(cfg), "arena": True, "board": False})  # off the board: never sent
    assert arena.in_arena(lb.read_state(cfg)) is False and lb.status(cfg)["arena"] is False
    with pytest.raises(lb.LeaderboardError, match="board"):
        arena.join(cfg, conn)
    lb.write_state(cfg, {**lb.read_state(cfg), "board": True, "arena": False})
    arena.join(cfg, conn)
    assert "update" in net.commits()[-1]["body"]["writes"][1] and lb.status(cfg)["arena"] is True
    net.on("DELETE", lb.FIRESTORE + "/arena/uid1", {})
    arena.leave(cfg)
    assert net.calls[-1]["method"] == "DELETE" and lb.status(cfg)["arena"] is False
    lb.send(cfg, conn, force=True)
    assert net.commits()[-1]["body"]["writes"][1] == {"delete": f"{lb.DB}/arena/uid1"}


def _mid_send(monkeypatch, then):
    """Run `then` while a send is building its commit (after it read the state, before it commits)."""
    from interlatch.knowledge import party

    real = party.compute

    def slow(*a, **k):
        then()
        return real(*a, **k)

    monkeypatch.setattr(party, "compute", slow)


def test_leaving_the_arena_during_a_send_is_never_undone(home, net, monkeypatch):  # noqa: F811
    conn, cfg = home["conn"], home["cfg"]
    _joined(cfg, arena=True)
    net.on("POST", lb.FIRESTORE + ":commit", {})
    net.on("DELETE", lb.FIRESTORE + "/arena/", {})
    _mid_send(monkeypatch, lambda: arena.leave(cfg))  # the person leaves while the sync is mid-send
    lb.send(cfg, conn, force=True)
    (commit,) = net.commits()
    assert commit["body"]["writes"][1] == {"delete": f"{lb.DB}/arena/uid1"}  # the sheet isn't written back
    assert lb.read_state(cfg)["arena"] is False


def test_a_send_while_leaving_the_arena_deletes_the_sheet(home, net):  # noqa: F811
    conn, cfg = home["conn"], home["cfg"]
    _joined(cfg, arena=True)
    net.on("POST", lb.FIRESTORE + ":commit", {})
    net.on("DELETE", lb.FIRESTORE + "/arena/uid1", lambda body, form: lb.send(cfg, conn, force=True) and {})
    arena.leave(cfg)  # the sync sends while the delete is on its way: the flag is already off
    (commit,) = net.commits()
    assert commit["body"]["writes"][1] == {"delete": f"{lb.DB}/arena/uid1"}


def test_leaving_the_board_during_a_send_sends_nothing(home, net, monkeypatch):  # noqa: F811
    conn, cfg = home["conn"], home["cfg"]
    _joined(cfg, arena=True)
    net.on("POST", lb.FIRESTORE + ":commit", {})
    net.on("DELETE", lb.FIRESTORE + "/", {})
    _mid_send(monkeypatch, lambda: lb.leave(cfg))
    assert lb.send(cfg, conn, force=True) is None
    assert net.commits() == [] and lb.read_state(cfg) is None  # neither the row nor the sheet comes back


def test_another_computer_reporting_can_still_leave_the_arena(home, net):  # noqa: F811
    cfg = home["cfg"]
    _joined(cfg, arena=True, paused="another computer reports for this account now")
    net.on("DELETE", lb.FIRESTORE + "/arena/uid1", {})
    arena.leave(cfg)
    assert net.calls[-1]["method"] == "DELETE" and lb.read_state(cfg)["arena"] is False


def test_the_standings_cache_keeps_one_slot(monkeypatch):
    calls = []
    real = rules.standings
    monkeypatch.setattr(rules, "standings", lambda s, w: calls.append(w) or real(s, w))
    arena.clear()
    for agents in (STRONG, WEAK, STRONG):
        monkeypatch.setattr(arena, "sheets", lambda now=None, a=agents: {
            "x": {"name": "X", "photo": False, "agents": a, "lineup": ["claude"], "champion": "claude"},
            "y": {"name": "Y", "photo": False, "agents": WEAK, "lineup": ["claude"], "champion": "claude"}})
        arena.board("x")
        arena.board("x")  # the same sheets: from the slot
    assert len(calls) == 3 and [k for k in arena._cache if k.startswith("standings")] == ["standings"]


def test_a_new_sign_in_never_opts_in_by_itself(home, net):  # noqa: F811
    cfg = home["cfg"]
    lb.sign_in(cfg, "R0-from-the-hosted-page")
    assert lb.read_state(cfg)["arena"] is False
    lb.write_state(cfg, {**lb.read_state(cfg), "arena": True})
    lb.sign_in(cfg, "R0-from-the-hosted-page")  # the same account signing in again keeps its choice
    assert lb.read_state(cfg)["arena"] is True
    lb.sign_in(cfg, "R0-from-the-hosted-page", board=False)
    assert lb.read_state(cfg)["arena"] is False


# ------------------------------------------------------------------ the dashboard
def _doc(uid, name, agents, v=3, champion=None, lineup=None):
    champion = champion or rules.default_champion(agents)
    fields = {"name": name, "photo": "", "machine": "m" * 16, "v": v, "agents": agents, "champion": champion,
              **({"lineup": lineup or [champion]} if v == 3 else {})}
    return {"document": {"name": f"{lb.DB}/arena/{uid}", "fields": {k: lb._value(x) for k, x in fields.items()}}}


def test_the_arena_board_and_a_battle(hubweb, catalog, net):  # noqa: F811
    url, cfg = hubweb["url"], hubweb["cfg"]
    arena.clear()
    net.on("POST", lb.FIRESTORE + ":runQuery", [
        _doc("uid1", "Ada", STRONG), _doc("uid2", "Ben", WEAK, lineup=["claude", "codex"]), _doc("uid3", "Cy", _party()),
        _doc("uid4", "Old", STRONG, v=1), _doc("uid5", "Was", STRONG, v=2), {"readTime": "x"}])
    _joined(cfg, arena=True)
    status, data = _http(url, "/api/leaderboard?by=arena")
    assert status == 200 and data["by"] == "arena" and data["me"]["arena"] is True and data["week"].startswith("20")
    assert [(r["rank"], r["name"], r["wins"], r["losses"], r["me"]) for r in data["rows"]] == [
        (1, "Ada", 2, 0, True), (2, "Ben", 1, 1, False), (3, "Cy", 0, 2, False)]  # older sheet formats: left out
    assert data["rows"][0]["power"] == rules.power(STRONG)
    champ = data["rows"][0]["champion"]  # the champion's own level and tier come along, so its sprite isn't a Lv 1 novice
    assert data["rows"][0]["agents"] == {champ: rules.clean_agent(STRONG[champ])} and data["rows"][0]["agents"][champ]["level"] > 1
    ben = data["rows"][1]  # the whole lineup, each with its level and tier
    assert ben["lineup"] == ["claude", "codex"] and set(ben["agents"]) == {"claude", "codex"}
    status, fight = _http(url, "/api/leaderboard/arena/battle?uid=uid2")
    assert status == 200 and fight["me"]["name"] == "Ada" and fight["rival"]["uid"] == "uid2"
    assert fight["me"]["champion"] == "claude" and fight["rival"]["champion"] == "claude"
    assert fight["rival"]["lineup"] == ["claude", "codex"] and fight["result"]["lineups"]["b"] == ["claude", "codex"]
    assert fight["result"]["winner_uid"] == "uid1" and fight["result"]["log"][-1]["t"] == "end"
    assert fight["result"] == rules.match("uid2", fight["rival"], "uid1", fight["me"], fight["week"])
    assert _http(url, "/api/leaderboard/arena/battle?uid=uid4")[0] == 404  # not in the arena
    arena.clear()

    def down(body, form):
        raise lb.LeaderboardError("no connection: offline")

    net.on("POST", lb.FIRESTORE + ":runQuery", down)
    status, r = _http(url, "/api/leaderboard/arena/battle?uid=uid2")
    assert status == 502 and r["error"] == "no connection: offline"  # out of reach: said, not a crash
    net.on("POST", lb.FIRESTORE + ":runQuery", [_doc("uid1", "Ada", STRONG), _doc("uid2", "Ben", WEAK)])
    arena.clear()
    assert _http(url, "/api/leaderboard/arena/battle?uid=uid1")[0] == 404  # not against yourself
    lb.write_state(cfg, {**lb.read_state(cfg), "arena": False})
    assert _http(url, "/api/leaderboard/arena/battle?uid=uid2")[0] == 404  # out of the arena: no battles
    net.on("POST", lb.FIRESTORE + ":runQuery", [_doc("uid1", "Ada", STRONG), _doc("uid2", "Ben", WEAK)])
    arena.clear()
    assert not [r for r in _http(url, "/api/leaderboard?by=arena")[1]["rows"] if r["me"]]


def test_entering_the_arena_from_the_dashboard(hubweb, catalog, net):  # noqa: F811
    url, cfg = hubweb["url"], hubweb["cfg"]
    net.on("POST", lb.FIRESTORE + ":commit", {})
    _joined(cfg, board=False)  # signed in for the Party only
    status, r = _http(url, "/api/leaderboard/arena/join", {})
    assert status == 502 and "board" in r["error"] and lb.read_state(cfg).get("arena") is None
    assert net.commits() == []
    lb.write_state(cfg, {**lb.read_state(cfg), "board": True})
    status, r = _http(url, "/api/leaderboard/arena/join", {})
    assert status == 200 and r["arena"] is True and "update" in net.commits()[-1]["body"]["writes"][1]
    for path in ("/api/leaderboard/arena/join", "/api/leaderboard/arena/leave"):  # another device: never
        assert _http(url, path, {}, headers=TAILNET)[0] == 403
    assert _http(url, "/api/leaderboard/arena/battle?uid=uid2", headers=TAILNET)[0] == 403
    assert lb.read_state(cfg)["arena"] is True
    net.on("DELETE", lb.FIRESTORE + "/arena/uid1", {})
    status, r = _http(url, "/api/leaderboard/arena/leave", {})
    assert status == 200 and r["arena"] is False and net.calls[-1]["url"] == lb.FIRESTORE + "/arena/uid1"


# ------------------------------------------------------------------ the rules file
PLAYERS = '''    match /players/{userId} {
      // The board is public on purpose (anyone running Interlatch may open it); queries are capped at 100 rows.
      allow get: if true;
      allow list: if request.query.limit <= 100;

      allow create: if isOwner(userId) && isPlayerAccount() && isValidPlayer(request.resource.data);
      // provider and pid never change: the uid stays tied to the account it was made with
      allow update: if isOwner(userId) && isPlayerAccount() && isValidPlayer(request.resource.data)
        && request.resource.data.provider == resource.data.provider && request.resource.data.pid == resource.data.pid;
      // leaving the board
      allow delete: if isOwner(userId);
    }
'''
DENY = '''    match /{document=**} {
      allow read, write: if false;
    }
'''


def test_the_rules_keep_the_board_and_add_the_arena():
    text = RULES.read_text()
    assert PLAYERS in text and text.rstrip().endswith(DENY + "  }\n}".rstrip())
    block = re.search(r"    match /arena/\{userId\} \{.*?\n    \}\n", text, re.S).group(0)
    assert "allow create, update: if isOwner(userId) && isPlayerAccount() && isValidArena(request.resource.data);" in block
    assert "allow delete: if isOwner(userId);" in block and "allow list: if request.query.limit <= 100;" in block
    assert block.count("allow ") == 4
    valid = re.search(r"function isValidArena\(data\) \{.*?\n    \}", text, re.S).group(0)
    assert "['name', 'photo', 'machine', 'v', 'agents', 'champion', 'at']" in valid and "data.at == request.time" in valid
    assert "hasOnlyAllowedFields(fields.concat(['lineup'])) && hasRequiredFields(fields)" in valid
    # sheets from versions before decks (v 2) still pass, without a lineup; v 3 needs one
    assert "((data.v == 2 && !('lineup' in data)) || (data.v == 3 && isValidArenaLineup(data)))" in valid
    assert "data.champion in agents" in valid
    lineup = re.search(r"function isValidArenaLineup\(data\) \{.*?\n    \}", text, re.S).group(0)
    assert "data.lineup.size() >= 1 && data.lineup.size() <= 3" in lineup
    assert "data.lineup.toSet().size() == data.lineup.size()" in lineup and "data.lineup[0] == data.champion" in lineup
    assert "data.lineup.hasOnly(data.agents.keys())" in lineup
    assert "let agents = ['claude', 'codex', 'copilot', 'bob', 'antigravity', 'opencode'];" in valid
    # sheets from versions before OpenCode joined still pass: opencode is allowed, never required
    assert "let required = ['claude', 'codex', 'copilot', 'bob', 'antigravity'];" in valid
    assert "data.agents.keys().hasOnly(agents) && data.agents.keys().hasAll(required)" in valid
    assert "(!('opencode' in data.agents) || isValidArenaAgent(data.agents.opencode))" in valid
    agent = re.search(r"function isValidArenaAgent\(a\) \{.*?\n    \}", text, re.S).group(0)
    assert "a.level >= 1 && a.level <= 200" in agent and "a.cls.size() <= 64" in agent
    # the 1000-expression budget: per-attribute bounds put five agents over it (the readers clamp them instead)
    assert "isCount" not in agent and agent.count("is int") == 7


def test_fielding_a_deck(hubweb, catalog, net):  # noqa: F811
    url, cfg, conn = hubweb["url"], hubweb["cfg"], hubweb["conn"]
    net.on("POST", lb.FIRESTORE + ":commit", {})
    assert _http(url, "/api/leaderboard/arena/deck", {"deck": "d1"})[0] == 502  # not signed in
    _joined(cfg)  # on the board, not in the arena: kept, nothing sent
    status, r = _http(url, "/api/leaderboard/arena/deck", {"deck": "d1"})  # the deck made for the party, saved
    assert status == 200 and r["decks"]["arena"] == "d1" and r["decks"]["saved"] is True and net.commits() == []
    formation.save(conn, None, {}, None, name="Fast", members=["codex", "bob"])
    status, r = _http(url, "/api/leaderboard/arena/deck", {"deck": "d2"})
    assert status == 200 and r["lineup"] == ["codex", "bob"] and r["champion"] == "codex" and net.commits() == []
    for bad in ("d9", "", None, ["d1"], 3):
        status, r = _http(url, "/api/leaderboard/arena/deck", {"deck": bad})
        assert status == 502 and "deck" in r["error"], bad
    assert formation.load(conn, "here")["arena"] == "d2"
    lb.write_state(cfg, {**lb.read_state(cfg), "arena": True})
    status, r = _http(url, "/api/leaderboard/arena/deck", {"deck": "d1"})  # in the arena: sent now
    (commit,) = net.commits()
    sent = lb._plain(commit["body"]["writes"][1]["update"]["fields"])
    assert status == 200 and sent["lineup"] == r["lineup"] and sent["champion"] == r["lineup"][0]
    assert _http(url, "/api/leaderboard/arena/deck", {"deck": "d2"}, headers=TAILNET)[0] == 403
    assert formation.load(conn, "here")["arena"] == "d1"
    net.on("POST", lb.FIRESTORE + ":runQuery", [])
    arena.clear()
    status, data = _http(url, "/api/leaderboard?by=arena")  # the Arena page: the team fielded and the decks to pick
    assert data["me"]["lineup"] == sent["lineup"] and [d["id"] for d in data["me"]["decks"]["decks"]] == ["d1", "d2"]
    assert set(data["me"]["agents"]) == set(CODING_AGENTS) and set(data["me"]["agents"]["claude"]) == {"level", "tier"}
