"""The Town & gear (knowledge/bag.py; party.clear's reward; GET /api/party/bag and /api/party/shop, POST /api/party/buy,
sell, equip and use): the gold and loot a first win pays (once, never again), the item catalog, what a shop unlocks
from real skill levels, every bag change's checks, the bag in the map and in an encounter, one bag per scope, two
quick POSTs that can't both spend or both get paid, and all of it behind the Party's sign-in. The small catalog of
test_party.py stands in for roadmap.json."""

from __future__ import annotations

import json
import threading

import pytest

from interlatch.core import i18n
from interlatch.core.agents import CODING_AGENTS
from interlatch.knowledge import bag, party
from interlatch.sharing import people
from interlatch.storage.kv import kv_get, kv_set
from interlatch.storage.sqlite import connect

from conftest import SID
from test_leaderboard import _joined
from test_party import ATTRS, BOB_PC, _lesson, _session, catalog, home  # noqa: F401  (the fixtures)
from test_people_web import _as, hubweb, team  # noqa: F401  (the fixtures)
from test_people_web import _call as _http

SWORD = "g:M-python:weapon:1"  # craft's tier I weapon


def _give(conn, key="here", **got):
    """Put a bag in the kv table as stored: {"gold", "items", "equipped", "paid"}."""
    kv_set(conn, bag.BAG_KEY + key, json.dumps({"gold": 0, "items": {}, "equipped": {}, "paid": [], **got}))
    conn.commit()


def _stored(conn, key="here"):
    return json.loads(kv_get(conn, bag.BAG_KEY + key))


def _levels(conn, sid, n, title="pytest", agent="claude"):
    """n canonical lessons of one module (6 XP each): 1 → skill 1, 3 → 3, 9 → 5."""
    for _ in range(n):
        _lesson(conn, sid, title, stage="canonical", agent=agent)


def _lesson_obj(conn, here, lid):
    return next(k for k in party.lessons_in(conn, party.load_catalog(), party._scope_of(conn, here, None)) if k.id == lid)


# ------------------------------------------------------------------ the catalog
def test_every_module_has_three_slots_in_three_tiers_and_the_shop_items(catalog):  # noqa: F811
    known = bag.items(catalog)
    gear = [it for it in known.values() if it["kind"] == "gear"]
    assert len(gear) == len(catalog.modules) * 9
    assert [it["id"] for it in gear[:9]] == [f"g:M-python:{s}:{t}" for s in bag.SLOTS for t in (1, 2, 3)]
    keys = {"id", "kind", "slot", "name", "about", "tier", "attr", "module", "price", "sell", "stats", "effect"}
    assert all(set(it) == keys and it["sell"] == it["price"] // 2 for it in known.values())
    w3, a2, c1 = known["g:M-sql:weapon:3"], known["g:M-sql:armor:2"], known["g:M-sql:charm:1"]
    assert w3["name"] == "Astral Trident of Sql" and w3["attr"] == "data" and w3["module"] == "M-sql"
    assert w3["stats"] == {"atk": 9, "hp": 0, "mp": 0, "def": 0.0, "skill": 3} and w3["price"] == 450
    assert a2["stats"] == {"atk": 0, "hp": 24, "mp": 0, "def": 0.1, "skill": 0} and a2["name"] == "Mithril Tidecoat of Sql"
    assert c1["stats"] == {"atk": 0, "hp": 0, "mp": 6, "def": 0.0, "skill": 1} and c1["price"] == 60
    assert known["g:M-python:armor:3"]["stats"]["def"] == 0.15 and known["g:M-python:weapon:2"]["price"] == 180
    assert [it["id"] for it in known.values() if it["kind"] == "consumable"] == [
        "c:potion", "c:ether", "c:phoenix", "c:smoke"]
    assert [known[i]["price"] for i in ("c:potion", "c:ether", "c:phoenix", "c:smoke")] == [15, 20, 60, 10]
    assert {k: v["id"] for k, v in bag.LOOT.items()} == {
        "fix": "l:patch", "gotcha": "l:sign", "decision": "l:adr", "learning": "l:insight", "pattern": "l:template",
        "command": "l:script"}
    assert set(bag.LOOT) == set(party.KINDS)
    assert known["c:phoenix"]["effect"] == {"target": "fallen", "revive": 0.3} and known["c:phoenix"]["slot"] is None
    token = i18n.lang.set("ja")
    try:
        ja = bag.items(catalog)
        assert ja["g:M-sql:weapon:3"]["name"] == "星辰の三叉槍（M-sql（日本語））"
        assert ja["g:M-python:armor:1"]["name"].startswith("鉄の前掛け") and ja["c:potion"]["name"] == "ポーション"
        assert ja["l:adr"]["about"] == "モンスターは次のターンを休む。"
        assert bag.towns(catalog)[0] == {"attr": "craft", "name": "craft（日本語）の町"}
    finally:
        i18n.lang.reset(token)
    assert bag.items(catalog)["c:potion"]["name"] == "Potion"  # the cache is per language
    json.dumps(known)


# ------------------------------------------------------------------ a win
def test_the_first_clear_pays_once_and_never_again(home):  # noqa: F811
    conn, here = home["conn"], home["here"]
    sid = _session(conn)
    lid = _lesson(conn, sid, "pytest")  # provisional, one module: xp 1 → 8 + 4 = 12
    log, reward = party.clear(conn, here, None, lid, True)
    assert log == [lid] and reward["gold"] == 12
    assert _stored(conn)["gold"] == 12 and _stored(conn)["paid"] == [lid]
    assert party.clear(conn, here, None, lid, True) == ([lid], None)  # already cleared
    assert party.clear(conn, here, None, lid, False) == ([], None)  # un-clearing pays nothing
    assert party.clear(conn, here, None, lid, True) == ([lid], None)  # nor clearing again
    assert bag.load(conn, "here")["gold"] == 12
    with pytest.raises(party.PartyError, match="no such lesson"):
        party.clear(conn, here, None, 999999, True)
    assert bag.load(conn, "here")["gold"] == 12  # a refused clear changes nothing
    assert party.set_cleared(conn, here, None, lid, True) == [lid]  # the old answer is still the log


@pytest.mark.parametrize(("title", "stage", "gold"), [
    ("pytest", "provisional", 12),  # 8 + 4·1
    ("pytest", "established", 30),  # (8 + 4·3) × 1.5
    ("pytest", "canonical", 64),  # (8 + 4·6) × 2
    ("pytest and SQL query", "canonical", 112),  # xp 12: (8 + 48) × 2
    ("pytest SQL token", "canonical", 160),  # xp 18: min(80, 80) × 2
    ("pytest SQL token", "established", 66),  # xp 9: (8 + 36) × 1.5
    ("a note that names no module", "canonical", 16),  # not counted: xp 0, (8) × 2
])
def test_a_wins_gold_follows_its_xp_and_stage(home, title, stage, gold):  # noqa: F811
    conn, here = home["conn"], home["here"]
    lid = _lesson(conn, _session(conn), title, stage=stage)
    k = _lesson_obj(conn, here, lid)
    expect = int(min(80, 8 + 4 * k.xp * len(k.modules)) * bag.STAGE_GOLD[stage] + 0.5)
    assert bag.win_gold(k) == expect == gold
    assert party.clear(conn, here, None, lid, True)[1]["gold"] == expect


def test_loot_is_the_kinds_and_the_same_every_time(home):  # noqa: F811
    conn, here = home["conn"], home["here"]
    sid = _session(conn)
    rolls = [bag.loot_of(party.Lesson(id=i, agent="claude", session_id=None, title="", kind="fix", stage="provisional",
                                      at="", created_at=None, project_name=None, head="", body="", xp=1, modules=()))
             for i in range(1, 2001)]
    assert set(rolls) == {"l:patch", None} and 0.30 < rolls.count("l:patch") / len(rolls) < 0.40
    got = {}
    for kind in party.KINDS:
        for _ in range(40):  # at 35%, one of these drops (the same ones every run: it can't flake)
            lid = _lesson(conn, sid, f"pytest {kind}", kind=kind)
            k = _lesson_obj(conn, here, lid)
            if bag.loot_of(k):
                break
        assert bag.loot_of(k) == bag.LOOT[kind]["id"] and bag.loot_of(k) == bag.loot_of(k)
        reward = party.clear(conn, here, None, lid, True)[1]
        assert [x["id"] for x in reward["loot"]] == [bag.LOOT[kind]["id"]] and reward["loot"][0]["kind"] == "loot"
        got[bag.LOOT[kind]["id"]] = 1
    assert bag.load(conn, "here")["items"] == got
    # a full stack drops nothing more, but the gold still comes
    _give(conn, gold=0, items={"l:patch": 99})
    while True:
        lid = _lesson(conn, sid, "pytest more")
        if bag.loot_of(_lesson_obj(conn, here, lid)):
            break
    reward = party.clear(conn, here, None, lid, True)[1]
    assert reward == {"gold": 12, "loot": []} and bag.load(conn, "here")["items"] == {"l:patch": 99}


# ------------------------------------------------------------------ the shop
def test_a_shop_unlocks_tiers_with_the_partys_best_real_skill_level(home):  # noqa: F811
    conn, here = home["conn"], home["here"]
    sid = _session(conn)
    shop = bag.shop(conn, here, None, "craft")
    assert set(shop) == {"region", "gold", "gear", "consumables"}
    assert shop["region"] == {"id": "craft", "name": "Craft", "about": "about craft"} and shop["gold"] == 0
    assert [g["id"] for g in shop["gear"]] == [f"g:M-python:{s}:{t}" for s in bag.SLOTS for t in (1, 2, 3)]
    assert all(g["locked"] for g in shop["gear"]) and shop["gear"][0]["unlock"] == "Lv 1 in Python"
    assert [c["id"] for c in shop["consumables"]] == ["c:potion", "c:ether", "c:phoenix", "c:smoke"]
    assert all(c["count"] == 0 for c in shop["consumables"] + shop["gear"])
    unlocked = lambda region: {g["id"] for g in bag.shop(conn, here, None, region)["gear"] if not g["locked"]}  # noqa: E731
    _levels(conn, sid, 1)  # python 6 XP: skill 1
    assert unlocked("craft") == {f"g:M-python:{s}:1" for s in bag.SLOTS}
    _levels(conn, sid, 2)  # 18: skill 3
    assert party.skill_level(18) == 3 and unlocked("craft") == {f"g:M-python:{s}:{t}" for s in bag.SLOTS for t in (1, 2)}
    _levels(conn, sid, 5, agent="codex")  # codex 30: skill 3 — the best agent counts, not the sum
    assert unlocked("craft") == {f"g:M-python:{s}:{t}" for s in bag.SLOTS for t in (1, 2)}
    _levels(conn, sid, 4, agent="codex")  # codex 54: skill 5
    assert len(unlocked("craft")) == 9 and unlocked("data") == set()
    assert bag.shop(conn, here, None, "lore")["gear"] == []  # a region with no module: consumables only
    assert bag.shop(conn, here, None, "nowhere") is None and bag.shop(conn, here, None, None) is None
    token = i18n.lang.set("ja")
    try:
        ja = bag.shop(conn, here, None, "data")
        assert ja["gear"][2]["unlock"] == "M-sql（日本語） で Lv 5" and ja["region"]["name"] == "data（日本語）"
    finally:
        i18n.lang.reset(token)


# ------------------------------------------------------------------ the bag's changes
def _err(fn, *args, **kw):
    with pytest.raises(party.PartyError) as exc:
        fn(*args, **kw)
    return str(exc.value)


def test_buying_checks_the_lock_the_gold_the_town_and_the_count(home):  # noqa: F811
    conn, here = home["conn"], home["here"]
    sid = _session(conn)
    _give(conn, gold=100)
    assert _err(bag.buy, conn, here, None, SWORD) == bag.LOCKED
    _levels(conn, sid, 1)
    assert _err(bag.buy, conn, here, None, "g:M-python:weapon:2") == bag.LOCKED
    assert _err(bag.buy, conn, here, None, SWORD, region="data") == bag.NOT_SOLD_HERE  # craft's, not data's
    assert _err(bag.buy, conn, here, None, "l:patch") == bag.NOT_SOLD_HERE  # loot is never sold
    for bad in ("g:nothing", None, 3, ["c:potion"]):
        assert _err(bag.buy, conn, here, None, bad) == bag.NO_ITEM
    for n in (0, 100, -1, "1", True, 1.0):
        assert _err(bag.buy, conn, here, None, "c:potion", n) == bag.BAD_COUNT
    out = bag.buy(conn, here, None, SWORD, region="craft")
    assert out["gold"] == 40 and [(x["id"], x["count"]) for x in out["items"]] == [(SWORD, 1)]
    assert _err(bag.buy, conn, here, None, SWORD) == bag.NO_GOLD
    assert _err(bag.buy, conn, here, None, "c:potion", 3) == bag.NO_GOLD  # 45 > 40
    out = bag.buy(conn, here, None, "c:potion", 2, region="ops")  # every town sells consumables
    assert out["gold"] == 10 and {x["id"]: x["count"] for x in out["items"]} == {SWORD: 1, "c:potion": 2}
    assert _stored(conn)["gold"] == 10
    _give(conn, gold=10_000, items={"c:smoke": 98})
    assert bag.buy(conn, here, None, "c:smoke")["items"][0]["count"] == 99
    assert _err(bag.buy, conn, here, None, "c:smoke") == bag.TOO_MANY
    assert bag.load(conn, "here")["gold"] == 9990  # a refused buy spends nothing


def test_selling_pays_half_and_never_sells_what_someone_wears(home):  # noqa: F811
    conn = home["conn"]
    _give(conn, gold=0, items={SWORD: 2, "l:adr": 1}, equipped={"claude": {"weapon": SWORD}})
    assert _err(bag.sell, conn, None, "c:potion") == bag.NOT_OWNED
    assert _err(bag.sell, conn, None, SWORD, 3) == bag.NOT_ENOUGH
    assert _err(bag.sell, conn, None, SWORD, 2) == bag.WORN  # one is on claude
    out = bag.sell(conn, None, SWORD)
    assert out["gold"] == 30 and out["equipped"]["claude"]["weapon"]["id"] == SWORD
    assert _err(bag.sell, conn, None, SWORD) == bag.WORN
    bag.equip(conn, None, "claude", "weapon", None)
    out = bag.sell(conn, None, SWORD)
    assert out["gold"] == 60 and [x["id"] for x in out["items"]] == ["l:adr"]
    assert bag.sell(conn, None, "l:adr")["gold"] == 90 and bag.load(conn, "here")["items"] == {}
    assert _err(bag.sell, conn, None, "nothing") == bag.NO_ITEM


def test_equipping_checks_the_piece_and_moves_a_worn_copy(home):  # noqa: F811
    conn = home["conn"]
    armor = "g:M-python:armor:3"
    _give(conn, items={SWORD: 1, armor: 1, "c:potion": 1})
    assert _err(bag.equip, conn, None, "nobody", "weapon", SWORD) == bag.NO_AGENT
    assert _err(bag.equip, conn, None, "claude", "helmet", SWORD) == bag.NO_SLOT
    assert _err(bag.equip, conn, None, "claude", "weapon", armor) == bag.WRONG_SLOT
    assert _err(bag.equip, conn, None, "claude", "charm", "c:potion") == bag.WRONG_SLOT
    assert _err(bag.equip, conn, None, "claude", "weapon", "g:M-sql:weapon:1") == bag.NOT_OWNED
    assert _err(bag.equip, conn, None, "claude", "weapon", "g:nothing") == bag.NO_ITEM
    out = bag.equip(conn, None, "claude", "weapon", SWORD)
    out = bag.equip(conn, None, "claude", "armor", armor)
    assert set(out["equipped"]) == set(CODING_AGENTS) and out["equipped"]["codex"] == dict.fromkeys(bag.SLOTS)
    assert out["equipped"]["claude"]["weapon"]["id"] == SWORD and out["equipped"]["claude"]["charm"] is None
    assert out["gear"]["claude"] == {"atk": 3, "hp": 36, "mp": 0, "def": 0.15, "skills": {"M-python": 1}}
    assert out["gear"]["codex"] == {"atk": 0, "hp": 0, "mp": 0, "def": 0.0, "skills": {}}
    # one copy: putting it on codex takes it off claude
    out = bag.equip(conn, None, "codex", "weapon", SWORD)
    assert out["equipped"]["codex"]["weapon"]["id"] == SWORD and out["equipped"]["claude"]["weapon"] is None
    assert _stored(conn)["equipped"]["claude"]["weapon"] is None
    # two copies: both wear one; a third agent takes the first wearer's
    _give(conn, items={SWORD: 2}, equipped={"codex": {"weapon": SWORD}})
    bag.equip(conn, None, "claude", "weapon", SWORD)
    out = bag.equip(conn, None, "codex", "weapon", SWORD)  # already on codex: nothing moves
    assert [a for a in CODING_AGENTS if out["equipped"][a]["weapon"]] == ["claude", "codex"]
    out = bag.equip(conn, None, "bob", "weapon", SWORD)
    assert [a for a in CODING_AGENTS if out["equipped"][a]["weapon"]] == ["codex", "bob"]
    out = bag.equip(conn, None, "bob", "weapon", None)
    assert out["equipped"]["bob"]["weapon"] is None and out["gear"]["codex"]["atk"] == 3
    # a stored bag wearing more copies than it holds, or a piece in the wrong slot, reads as wearing what it can
    _give(conn, items={SWORD: 1}, equipped={"claude": {"weapon": SWORD, "armor": SWORD}, "codex": {"weapon": SWORD}})
    out = bag.bag(conn, None)
    assert out["equipped"]["claude"]["weapon"]["id"] == SWORD and out["equipped"]["claude"]["armor"] is None
    assert out["equipped"]["codex"]["weapon"] is None and out["gear"]["codex"]["atk"] == 0


def test_using_takes_one_consumable_or_loot(home):  # noqa: F811
    conn = home["conn"]
    _give(conn, items={"c:potion": 2, "l:script": 1, SWORD: 1})
    assert _err(bag.use, conn, None, SWORD) == bag.NOT_USABLE
    assert _err(bag.use, conn, None, "c:ether") == bag.NONE_LEFT
    assert _err(bag.use, conn, None, "nothing") == bag.NO_ITEM
    assert {x["id"]: x["count"] for x in bag.use(conn, None, "c:potion")["items"]} == {SWORD: 1, "c:potion": 1, "l:script": 1}
    bag.use(conn, None, "c:potion")
    assert {x["id"] for x in bag.use(conn, None, "l:script")["items"]} == {SWORD}
    assert _err(bag.use, conn, None, "c:potion") == bag.NONE_LEFT


def test_a_malformed_bag_reads_as_empty(home):  # noqa: F811
    conn = home["conn"]
    empty = {"gold": 0, "items": [], "equipped": {a: dict.fromkeys(bag.SLOTS) for a in CODING_AGENTS},
             "gear": {a: {"atk": 0, "hp": 0, "mp": 0, "def": 0.0, "skills": {}} for a in CODING_AGENTS}}
    assert bag.bag(conn, None) == empty
    for raw in ("not json", "[1, 2]", json.dumps({"gold": "lots", "items": [1], "equipped": 3, "paid": "x"}),
                json.dumps({"gold": True, "items": {"c:potion": -1, "c:ether": True, "g:gone:weapon:1": 3}})):
        kv_set(conn, bag.BAG_KEY + "here", raw)
        assert bag.bag(conn, None) == empty, raw
    _give(conn, gold=5, items={"c:potion": 500})
    assert bag.bag(conn, None)["items"][0]["count"] == 99


# ------------------------------------------------------------------ in the map and the encounter
def test_the_encounter_has_each_heros_gear_and_the_bags_items(home):  # noqa: F811
    conn, here = home["conn"], home["here"]
    sid = _session(conn)
    lid = _lesson(conn, sid, "pytest")
    _give(conn, gold=7, items={"g:M-sql:charm:2": 1, "c:ether": 3, "l:sign": 1, SWORD: 1},
          equipped={"codex": {"charm": "g:M-sql:charm:2", "weapon": SWORD}})
    got = party.encounter(conn, here, None, lid)
    gear = {p["agent"]: p["gear"] for p in got["party"]}
    assert gear["codex"] == {"atk": 3, "hp": 0, "mp": 12, "def": 0.0, "skills": {"M-sql": 2, "M-python": 1}}
    assert gear["claude"] == {"atk": 0, "hp": 0, "mp": 0, "def": 0.0, "skills": {}}
    assert [(x["id"], x["count"], x["kind"]) for x in got["items"]] == [("c:ether", 3, "consumable"), ("l:sign", 1, "loot")]
    assert got["items"][1]["effect"] == {"target": "party", "guard": 0.4, "turns": 2}
    data = party.adventure_map(conn, here)
    assert data["gold"] == 7 and data["towns"] == [{"attr": a, "name": f"{a.title()} town"} for a in ATTRS]
    json.dumps(got)


# ------------------------------------------------------------------ whose bag
def test_each_hub_person_has_their_own_bag(home):  # noqa: F811
    conn, here = home["conn"], home["here"]
    ada = people.add(conn, "Ada", "ada@example.com", "admin")
    bob = people.add(conn, "Bob", "bob@example.com", "member", by=ada)
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'bob-pc', 'spoke', ?)", (BOB_PC, bob["id"]))
    conn.commit()
    bob_sid = _session(conn, machine=BOB_PC, source="remote")
    bobs = _lesson(conn, bob_sid, "SQL query on bob's computer", stage="established")  # sql 3: (8 + 12) × 1.5
    mine = _lesson(conn, _session(conn, machine=here), "pytest at the hub")
    assert party.clear(conn, here, bob, bobs, True)[1]["gold"] == 30
    assert party.clear(conn, here, None, mine, True)[1]["gold"] == 12
    assert bag.load(conn, f"person:{bob['id']}")["gold"] == 30 and bag.load(conn, "here")["gold"] == 12
    assert bag.load(conn, f"person:{ada['id']}")["gold"] == 0
    assert party.adventure_map(conn, here, bob)["gold"] == 30 and party.adventure_map(conn, here, ada)["gold"] == 0
    # Bob's shop unlocks with Bob's lessons only: SQL at skill 1 for him, nothing at the hub itself
    assert {g["id"] for g in bag.shop(conn, here, bob, "data")["gear"] if not g["locked"]} == {
        f"g:M-sql:{s}:1" for s in bag.SLOTS}
    assert not any(not g["locked"] for g in bag.shop(conn, here, None, "data")["gear"])
    assert _err(bag.buy, conn, here, None, "g:M-sql:weapon:1") == bag.LOCKED
    out = bag.buy(conn, here, bob, "c:potion", 2)
    assert out["gold"] == 0 and bag.bag(conn, None)["items"] == [] and bag.bag(conn, ada)["gold"] == 0
    assert bag.load(conn, "here")["gold"] == 12


# ------------------------------------------------------------------ two quick POSTs
def _at_once(env, n, work):
    """Run work(conn) n times at once, each on its own connection (as each request thread has)."""
    gate, out = threading.Barrier(n), [None] * n

    def run(i):
        c = connect(env["cfg"].db_path)
        try:
            gate.wait()
            out[i] = work(c)
        except party.PartyError as exc:
            out[i] = exc
        finally:
            c.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def test_two_quick_clears_pay_once_and_two_quick_buys_spend_once(home):  # noqa: F811
    conn, here = home["conn"], home["here"]
    lid = _lesson(conn, _session(conn), "pytest")
    party.lessons_in(conn, home["catalog"], party._scope_of(conn, here, None))  # warm, so they race on the write
    got = _at_once(home, 6, lambda c: party.clear(c, here, None, lid, True)[1])
    assert sum(1 for r in got if r) == 1 and bag.load(conn, "here")["gold"] == 12
    assert bag.load(conn, "here")["paid"] == [lid] and party.cleared_of(conn, "here") == [lid]
    _give(conn, gold=15)
    got = _at_once(home, 6, lambda c: bag.buy(c, here, None, "c:potion"))
    assert sum(1 for r in got if isinstance(r, dict)) == 1 and all(str(r) == bag.NO_GOLD for r in got
                                                                     if not isinstance(r, dict))
    held = bag.load(conn, "here")
    assert held["gold"] == 0 and held["items"] == {"c:potion": 1}


# ------------------------------------------------------------------ the dashboard
def test_the_town_is_behind_the_partys_sign_in(hubweb, catalog):  # noqa: F811
    url, cfg, conn = hubweb["url"], hubweb["cfg"], hubweb["conn"]
    lid = _lesson(conn, SID, "pytest secrets")
    calls = (("/api/party/bag", None), ("/api/party/shop?region=craft", None), ("/api/party/buy", {"id": "c:potion"}),
             ("/api/party/sell", {"id": "c:potion"}), ("/api/party/use", {"id": "c:potion"}),
             ("/api/party/equip", {"agent": "claude", "slot": "weapon", "id": None}))
    for path, body in calls:
        status, r = _http(url, path, body)
        assert status == 403 and r["signin"] is True, path
    assert kv_get(conn, bag.BAG_KEY + "here") is None
    _joined(cfg, board=False)
    status, r = _http(url, "/api/party/bag")
    assert status == 200 and r["gold"] == 0 and set(r) == {"gold", "items", "equipped", "gear"}
    status, r = _http(url, "/api/party/cleared", {"id": lid, "cleared": True})
    assert status == 200 and r["reward"]["gold"] == 12 and r["bag"]["gold"] == 12
    status, r = _http(url, "/api/party/buy", {"id": "c:smoke"})
    assert status == 200 and r["bag"]["gold"] == 2 and r["bag"]["items"][0]["id"] == "c:smoke"
    status, r = _http(url, "/api/party/buy", {"id": "c:smoke"})
    assert status == 400 and r["error"] == "not enough gold"
    status, r = _http(url, "/api/party/buy", {"id": "c:smoke"}, {"X-Interlatch-Lang": "ja"})
    assert status == 400 and r["error"] == "ゴールドが足りません"
    assert _http(url, "/api/party/buy", {"id": "c:smoke", "n": "2"})[0] == 400
    status, r = _http(url, "/api/party/use", {"id": "c:smoke"})
    assert status == 200 and r["bag"]["items"] == [] and r["bag"]["gold"] == 2
    assert _http(url, "/api/party/use", {"id": "c:smoke"})[1]["error"] == "none left"
    assert _http(url, "/api/party/sell", {"id": "c:smoke"})[1]["error"] == "you don't have that"
    assert _http(url, "/api/party/equip", {"agent": "claude", "slot": "weapon", "id": SWORD})[0] == 400
    status, r = _http(url, "/api/party/equip", {"agent": "claude", "slot": "weapon", "id": None})
    assert status == 200 and r["bag"]["equipped"]["claude"]["weapon"] is None
    status, shop = _http(url, "/api/party/shop?region=craft")
    assert status == 200 and shop["gold"] == 2 and shop["region"]["id"] == "craft"
    assert [g["locked"] for g in shop["gear"][:3]] == [False, True, True]  # python at skill 1
    assert _http(url, "/api/party/shop?region=nowhere")[0] == 404 and _http(url, "/api/party/shop")[0] == 404
    status, data = _http(url, "/api/party/map")
    assert data["gold"] == 2 and len(data["towns"]) == len(ATTRS)


def test_a_member_buys_into_their_own_bag(team, catalog):  # noqa: F811
    url, conn, s = team["url"], team["conn"], team["sessions"]
    bob = team["bob"]
    conn.execute("INSERT INTO machines(id, name, role, person_id) VALUES (?, 'bob-pc', 'spoke', ?)", (BOB_PC, bob["id"]))
    conn.commit()
    bobs = _lesson(conn, _session(conn, machine=BOB_PC, source="remote"), "SQL query on bob's computer")
    status, r = _http(url, "/api/party/cleared", {"id": bobs, "cleared": True}, _as(s["Bob"]))
    assert status == 200 and r["reward"]["gold"] == 12
    status, r = _http(url, "/api/party/buy", {"id": "c:smoke"}, _as(s["Bob"]))  # members may: their own bag
    assert status == 200 and r["bag"]["gold"] == 2
    assert _http(url, "/api/party/bag", headers=_as(s["Vic"]))[1]["gold"] == 0  # Vic's own
    assert _http(url, "/api/party/buy", {"id": "c:smoke"}, _as(s["Vic"]))[1]["error"] == "not enough gold"
    assert bag.load(conn, f"person:{bob['id']}")["items"] == {"c:smoke": 1}
    assert kv_get(conn, bag.BAG_KEY + "here") is None
