"""Innerlatch's Town & gear: a game economy beside the Party (party.py). Gold, gear and items change only BATTLE
numbers; levels and XP still come only from lessons, and nothing here raises one. Real data decides what a town's shop
offers (a piece unlocks with the party's best skill level in its module); gold buys it.

The bag is kept per scope (party.scope_key), in the kv table: BAG_KEY + key → {"gold": int, "items": {item id: count},
"equipped": {agent: {"weapon"|"armor"|"charm": item id|null}}, "paid": [lesson id, ...]}, "paid" being the lessons
whose first clear already paid out. Every change is one read-modify-write under kv_transaction, so two quick POSTs
can't both spend the same gold or both pay one win.

- A win (party.clear, the first time a lesson is cleared and never again) pays win_gold(): min(80, 8 + 4·xp) × the
  lesson's stage (STAGE_GOLD), xp as the map shows it (its stage XP × its modules); and drops its kind's loot when the
  lesson's hash says so (LOOT_CHANCE in 100). Bosses are milestones, not lessons: they never pay.
- The catalog (items()): for every module a weapon, an armor and a charm in tiers I-III (GEAR_*); four consumables
  every town sells; one loot per lesson kind, never sold in shops, only sold back.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from typing import Any

from ..core.agents import CODING_AGENTS
from ..core.i18n import lang, tr
from ..core.util import loads
from ..storage.kv import kv_get, kv_set, kv_transaction
from . import party
from .party import Catalog, Lesson, PartyError, _t, load_catalog, scope_key

BAG_KEY = "party.bag:"  # + scope key
SLOTS = ("weapon", "armor", "charm")
TIERS = (1, 2, 3)
GEAR_PRICE = {1: 60, 2: 180, 3: 450}
GEAR_UNLOCK = {1: 1, 2: 3, 3: 5}  # the party's best skill level in the piece's module it needs
STAGE_GOLD = {"provisional": 1.0, "established": 1.5, "canonical": 2.0}
WIN_GOLD_BASE, WIN_GOLD_PER_XP, WIN_GOLD_MAX = 8, 4, 80  # before the stage's multiplier
LOOT_CHANCE = 35  # in 100
CARRY_MAX = 99  # of each item
PAID_MAX = 100_000
STATS = ("atk", "hp", "mp", "def", "skill")

TIER_WORDS = {1: ("Iron", "鉄"), 2: ("Mithril", "ミスリル"), 3: ("Astral", "星辰")}
# per attr: the weapon's, the armor's and the charm's noun, (English, Japanese)
NOUNS = {
    "craft": (("Hammer", "ハンマー"), ("Apron", "前掛け"), ("Gear charm", "歯車のお守り")),
    "data": (("Trident", "三叉槍"), ("Tidecoat", "潮の外套"), ("Pearl", "真珠")),
    "ops": (("Wrench", "レンチ"), ("Hardhat", "安全帽"), ("Beacon", "灯標")),
    "guard": (("Halberd", "斧槍"), ("Bulwark", "城壁の鎧"), ("Seal", "封印")),
    "arcana": (("Staff", "杖"), ("Robe", "法衣"), ("Crystal", "水晶")),
    "lore": (("Quill", "羽ペン"), ("Cloak", "マント"), ("Tome", "古文書")),
}
NOUNS_ELSE = (("Blade", "剣"), ("Mail", "鎧"), ("Charm", "お守り"))  # an attr the table doesn't know

# effect targets: "hero" the active hero, "fallen" a knocked-out hero, "party" every hero, "monster" the monster
CONSUMABLES = (
    {"id": "c:potion", "price": 15, "name": "Potion", "name_ja": "ポーション",
     "about": "Heals 40% of one hero's max HP.", "about_ja": "味方ひとりの HP を最大値の 40% 回復する。",
     "effect": {"target": "hero", "heal": 0.4}},
    {"id": "c:ether", "price": 20, "name": "Ether", "name_ja": "エーテル",
     "about": "Restores 50% of one hero's max MP.", "about_ja": "味方ひとりの MP を最大値の 50% 回復する。",
     "effect": {"target": "hero", "mp": 0.5}},
    {"id": "c:phoenix", "price": 60, "name": "Phoenix Down", "name_ja": "フェニックスの尾",
     "about": "Revives a knocked-out hero at 30% HP.", "about_ja": "倒れた味方ひとりを HP 30% で復活させる。",
     "effect": {"target": "fallen", "revive": 0.3}},
    {"id": "c:smoke", "price": 10, "name": "Smoke Bomb", "name_ja": "煙玉",
     "about": "The party always escapes.", "about_ja": "パーティーは必ず逃げられる。",
     "effect": {"target": "party", "escape": True}},
)
LOOT = {  # lesson kind: what its win may drop
    "fix": {"id": "l:patch", "price": 40, "name": "Patch", "name_ja": "パッチ",
            "about": "Heals 25% of max HP to every hero.", "about_ja": "味方全員の HP を最大値の 25% 回復する。",
            "effect": {"target": "party", "heal": 0.25}},
    "gotcha": {"id": "l:sign", "price": 50, "name": "Warning Sign", "name_ja": "注意の立て札",
               "about": "The party takes 40% less damage for 2 monster turns.",
               "about_ja": "モンスターの 2 ターンのあいだ、パーティーの受けるダメージが 40% 減る。",
               "effect": {"target": "party", "guard": 0.4, "turns": 2}},
    "decision": {"id": "l:adr", "price": 60, "name": "ADR Scroll", "name_ja": "ADR の巻物",
                 "about": "The monster skips its next turn.", "about_ja": "モンスターは次のターンを休む。",
                 "effect": {"target": "monster", "skip": 1}},
    "learning": {"id": "l:insight", "price": 40, "name": "Insight", "name_ja": "ひらめき",
                 "about": "Every hero regains 30% of their max MP.", "about_ja": "味方全員の MP が最大値の 30% 回復する。",
                 "effect": {"target": "party", "mp": 0.3}},
    "pattern": {"id": "l:template", "price": 50, "name": "Template", "name_ja": "テンプレート",
                "about": "The next hero action deals ×1.6 damage.", "about_ja": "味方の次の行動のダメージが 1.6 倍になる。",
                "effect": {"target": "party", "boost": 1.6}},
    "command": {"id": "l:script", "price": 70, "name": "Script", "name_ja": "スクリプト",
                "about": "Deals 25% of the monster's max HP (a boss: 10%); never below 1 HP.",
                "about_ja": "モンスターの最大 HP の 25% のダメージ（ボスには 10%）。HP は 1 より下がらない。",
                "effect": {"target": "monster", "damage": 0.25, "boss_damage": 0.1}},
}

# the errors (PartyError, translated by the API with tr())
NO_ITEM = "no such item"
NOT_SOLD_HERE = "this isn't sold here"
LOCKED = "this is still locked: the party's skill in its module isn't high enough yet"
NO_GOLD = "not enough gold"
TOO_MANY = "you can carry at most 99 of each item"
BAD_COUNT = "the number must be a whole number from 1 to 99"
NOT_OWNED = "you don't have that"
NOT_ENOUGH = "you don't have that many"
WORN = "take it off before selling it"
NO_AGENT = "no such agent"
NO_SLOT = "no such slot"
WRONG_SLOT = "that doesn't go in this slot"
NOT_USABLE = "only consumables and loot can be used"
NONE_LEFT = "none left"


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _ja() -> bool:
    return lang.get() == "ja"


# ------------------------------------------------------------------ the catalog
def _stats(**got) -> dict:
    return {"atk": 0, "hp": 0, "mp": 0, "def": 0.0, "skill": 0, **got}


def _gear(cat: Catalog, m: dict, slot: str, tier: int) -> dict:
    attr, mname = m.get("attr"), _t(m, "name")
    noun = (NOUNS.get(attr) or NOUNS_ELSE)[SLOTS.index(slot)]
    word = TIER_WORDS[tier]
    if slot == "weapon":
        stats = _stats(atk=3 * tier, skill=tier)
        about = tr("+{atk} attack, and +{skill} to the {module} skill, in battle only", atk=3 * tier, skill=tier,
                   module=mname)
    elif slot == "armor":
        stats = _stats(hp=12 * tier, **{"def": round(0.05 * tier, 2)})
        about = tr("+{hp} HP, and {cut}% less damage taken, in battle only", hp=12 * tier, cut=5 * tier)
    else:
        stats = _stats(mp=6 * tier, skill=tier)
        about = tr("+{mp} MP, and +{skill} to the {module} skill, in battle only", mp=6 * tier, skill=tier,
                   module=mname)
    return {"id": f"g:{m['id']}:{slot}:{tier}", "kind": "gear", "slot": slot,
            "name": tr("{tier} {noun} of {module}", tier=word[_ja()], noun=noun[_ja()], module=mname),
            "about": about, "tier": tier, "attr": attr, "module": m["id"], "price": GEAR_PRICE[tier],
            "sell": GEAR_PRICE[tier] // 2, "stats": stats, "effect": None}


def _thing(spec: dict, kind: str) -> dict:
    return {"id": spec["id"], "kind": kind, "slot": None, "name": _t(spec, "name"), "about": _t(spec, "about"),
            "tier": None, "attr": None, "module": None, "price": spec["price"], "sell": spec["price"] // 2,
            "stats": _stats(), "effect": dict(spec["effect"])}


_items: dict[tuple, dict[str, dict]] = {}
_items_lock = threading.Lock()


def items(cat: Catalog | None = None) -> dict[str, dict]:
    """Every item by id, in order: the gear (module by module in catalog order, weapon, armor, charm, tier I-III),
    the consumables, the loot. Named in the dashboard's language. Cached: don't change what it returns."""
    cat = cat or load_catalog()
    key = (cat.key, lang.get())
    with _items_lock:
        hit = _items.get(key)
    if hit is not None:
        return hit
    out = {}
    for m in cat.modules:
        for slot in SLOTS:
            for tier in TIERS:
                it = _gear(cat, m, slot, tier)
                out[it["id"]] = it
    for spec in CONSUMABLES:
        out[spec["id"]] = _thing(spec, "consumable")
    for spec in LOOT.values():
        out[spec["id"]] = _thing(spec, "loot")
    with _items_lock:
        if len(_items) >= 16:
            _items.clear()
        _items[key] = out
    return out


def towns(cat: Catalog) -> list[dict]:
    """One town per region (attr), for the map."""
    return [{"attr": a["id"], "name": tr("{name} town", name=_t(a, "name"))} for a in cat.attrs]


# ------------------------------------------------------------------ the bag in the kv table
def load(conn: sqlite3.Connection, key: str) -> dict:
    """The scope's bag as stored, every field checked (anything malformed reads as nothing)."""
    raw = loads(kv_get(conn, BAG_KEY + key), {})
    raw = raw if isinstance(raw, dict) else {}
    gold = raw.get("gold")
    got = raw.get("items") if isinstance(raw.get("items"), dict) else {}
    worn = raw.get("equipped") if isinstance(raw.get("equipped"), dict) else {}
    paid = raw.get("paid") if isinstance(raw.get("paid"), list) else []
    return {
        "gold": gold if _int(gold) and gold > 0 else 0,
        "items": {k: min(v, CARRY_MAX) for k, v in got.items() if isinstance(k, str) and _int(v) and v > 0},
        "equipped": {a: {s: x if isinstance(x := (worn.get(a) if isinstance(worn.get(a), dict) else {}).get(s), str)
                         else None for s in SLOTS} for a in CODING_AGENTS},
        "paid": list(dict.fromkeys(x for x in paid if _int(x))),
    }


def _save(conn: sqlite3.Connection, key: str, bag: dict) -> None:
    kv_set(conn, BAG_KEY + key, json.dumps(bag, sort_keys=True))


def _worn(known: dict[str, dict], bag: dict) -> dict[str, dict[str, str | None]]:
    """What each agent really wears: a known piece of that slot, and no more agents wearing one than copies owned
    (in agent order, the first ones keep it)."""
    left = dict(bag["items"])
    out = {}
    for a in CODING_AGENTS:
        out[a] = {}
        for s in SLOTS:
            x = bag["equipped"][a][s]
            it = known.get(x) if x else None
            if it is not None and it["kind"] == "gear" and it["slot"] == s and left.get(x, 0) > 0:
                left[x] -= 1
                out[a][s] = x
            else:
                out[a][s] = None
    return out


def _wearing(known: dict[str, dict], worn: dict[str, str | None]) -> dict:
    """The summed battle stats of what one agent wears."""
    total: dict[str, Any] = {"atk": 0, "hp": 0, "mp": 0, "def": 0.0, "skills": {}}
    for x in worn.values():
        if not x:
            continue
        it = known[x]
        for s in ("atk", "hp", "mp"):
            total[s] += it["stats"][s]
        total["def"] = round(total["def"] + it["stats"]["def"], 2)
        if it["stats"]["skill"]:
            total["skills"][it["module"]] = total["skills"].get(it["module"], 0) + it["stats"]["skill"]
    return total


def gear_of(cat: Catalog, bag: dict) -> dict[str, dict]:
    """{agent: {atk, hp, mp, def, skills: {module id: extra levels in battle}}}, for every agent."""
    known = items(cat)
    return {a: _wearing(known, w) for a, w in _worn(known, bag).items()}


def usable(cat: Catalog, bag: dict) -> list[dict]:
    """The consumables and loot the bag holds, each with its count, in catalog order (for a battle's Item menu)."""
    return [{**it, "count": bag["items"][i]} for i, it in items(cat).items()
            if it["kind"] != "gear" and bag["items"].get(i)]


def bag_out(cat: Catalog, bag: dict) -> dict:
    """GET /api/party/bag's answer (and every bag change's): the gold, every item held with its count, what each agent
    wears, and the stats that adds up to."""
    known = items(cat)
    worn = _worn(known, bag)
    return {"gold": bag["gold"],
            "items": [{**it, "count": bag["items"][i]} for i, it in known.items() if bag["items"].get(i)],
            "equipped": {a: {s: (known[x] if x else None) for s, x in w.items()} for a, w in worn.items()},
            "gear": {a: _wearing(known, w) for a, w in worn.items()}}


def _change(conn: sqlite3.Connection, cat: Catalog, key: str, change) -> dict:
    """Read the bag, change it (`change(bag)` raises PartyError to refuse), write it back: one transaction."""
    with kv_transaction(conn):
        bag = load(conn, key)
        change(bag)
        _save(conn, key, bag)
    return bag_out(cat, bag)


# ------------------------------------------------------------------ a win
def win_gold(lesson: Lesson) -> int:
    """The gold a lesson's first clear pays: min(80, 8 + 4·xp) × its stage's multiplier, rounded."""
    xp = lesson.xp * len(lesson.modules)
    base = min(WIN_GOLD_MAX, WIN_GOLD_BASE + WIN_GOLD_PER_XP * xp)
    return int(base * STAGE_GOLD.get(lesson.stage, 1.0) + 0.5)


def loot_of(lesson: Lesson) -> str | None:
    """The loot a lesson's win drops, the same every time: its kind's, when its id hashes under LOOT_CHANCE."""
    spec = LOOT.get(lesson.kind)
    if spec is None:
        return None
    roll = int.from_bytes(hashlib.sha256(f"loot:{lesson.id}".encode()).digest()[:8], "big") % 100
    return spec["id"] if roll < LOOT_CHANCE else None


def pay_first_clear(conn: sqlite3.Connection, cat: Catalog, key: str, lesson: Lesson) -> dict | None:
    """The reward for clearing `lesson` for the first time: {"gold", "loot": [item]}, added to the bag; None when it
    already paid once. Runs inside the caller's kv_transaction (party.clear), which writes the log beside it."""
    bag = load(conn, key)
    if lesson.id in bag["paid"]:
        return None
    gold = win_gold(lesson)
    bag["gold"] += gold
    loot = []
    drop = loot_of(lesson)
    if drop and bag["items"].get(drop, 0) < CARRY_MAX:
        bag["items"][drop] = bag["items"].get(drop, 0) + 1
        loot.append(items(cat)[drop])
    bag["paid"] = (bag["paid"] + [lesson.id])[-PAID_MAX:]
    _save(conn, key, bag)
    return {"gold": gold, "loot": loot}


# ------------------------------------------------------------------ the shop
def best_levels(conn: sqlite3.Connection, here_id: str, viewer: dict | None, cat: Catalog) -> dict[str, int]:
    """{module id: the best skill level any agent of the viewer's party has there} (real levels, from lessons)."""
    best: dict[str, int] = {}
    for a in party.compute(conn, here_id, viewer, catalog=cat)["agents"]:
        for s in a["skills"]:
            best[s["module"]] = max(best.get(s["module"], 0), s["level"])
    return best


def _offer(cat: Catalog, it: dict, levels: dict[str, int]) -> dict:
    need = GEAR_UNLOCK[it["tier"]]
    return {**it, "locked": levels.get(it["module"], 0) < need,
            "unlock": tr("Lv {level} in {module}", level=need, module=_t(cat.module[it["module"]], "name"))}


def shop(conn: sqlite3.Connection, here_id: str, viewer: dict | None, region, *,
         catalog: Catalog | None = None) -> dict | None:
    """GET /api/party/shop: a region's town. Its gear (every piece of its modules, by module, slot and tier, locked
    until the party's best skill level in the module reaches GEAR_UNLOCK), the consumables, the gold. Each with how
    many the bag holds (`count`). None for a region the catalog doesn't have."""
    cat = catalog or load_catalog()
    attr = next((a for a in cat.attrs if a["id"] == region), None)
    if attr is None:
        return None
    levels = best_levels(conn, here_id, viewer, cat)
    bag = load(conn, scope_key(viewer))
    known = items(cat)
    return {"region": {"id": attr["id"], "name": _t(attr, "name"), "about": _t(attr, "about")},
            "gold": bag["gold"],
            "gear": [{**_offer(cat, it, levels), "count": bag["items"].get(i, 0)} for i, it in known.items()
                     if it["kind"] == "gear" and it["attr"] == region],
            "consumables": [{**it, "count": bag["items"].get(i, 0)} for i, it in known.items()
                            if it["kind"] == "consumable"]}


def _n(n) -> int:
    if n is None:
        return 1
    if not _int(n) or not 1 <= n <= CARRY_MAX:
        raise PartyError(BAD_COUNT)
    return n


def _known(cat: Catalog, item_id) -> dict:
    it = items(cat).get(item_id) if isinstance(item_id, str) else None
    if it is None:
        raise PartyError(NO_ITEM)
    return it


def bag(conn: sqlite3.Connection, viewer: dict | None, *, catalog: Catalog | None = None) -> dict:
    """GET /api/party/bag."""
    cat = catalog or load_catalog()
    return bag_out(cat, load(conn, scope_key(viewer)))


def buy(conn: sqlite3.Connection, here_id: str, viewer: dict | None, item_id, n=None, region=None, *,
        catalog: Catalog | None = None) -> dict:
    """Buy `n` of an item: a consumable anywhere, gear unlocked by coverage (in its own region's town, when `region`
    says which town); loot is never sold. Answers the bag."""
    cat = catalog or load_catalog()
    it, n = _known(cat, item_id), _n(n)
    if it["kind"] == "loot" or (region is not None and it["kind"] == "gear" and region != it["attr"]):
        raise PartyError(NOT_SOLD_HERE)
    if it["kind"] == "gear" and _offer(cat, it, best_levels(conn, here_id, viewer, cat))["locked"]:
        raise PartyError(LOCKED)

    def change(bag: dict) -> None:
        if bag["items"].get(it["id"], 0) + n > CARRY_MAX:
            raise PartyError(TOO_MANY)
        if bag["gold"] < it["price"] * n:
            raise PartyError(NO_GOLD)
        bag["gold"] -= it["price"] * n
        bag["items"][it["id"]] = bag["items"].get(it["id"], 0) + n

    return _change(conn, cat, scope_key(viewer), change)


def sell(conn: sqlite3.Connection, viewer: dict | None, item_id, n=None, *, catalog: Catalog | None = None) -> dict:
    """Sell `n` of an item for its sell price each; a piece someone wears can't be sold until it's taken off."""
    cat = catalog or load_catalog()
    it, n = _known(cat, item_id), _n(n)

    def change(bag: dict) -> None:
        have = bag["items"].get(it["id"], 0)
        if have < n:
            raise PartyError(NOT_ENOUGH if have else NOT_OWNED)
        worn = sum(1 for w in _worn(items(cat), bag).values() for x in w.values() if x == it["id"])
        if have - worn < n:
            raise PartyError(WORN)
        bag["gold"] += it["sell"] * n
        if have == n:
            del bag["items"][it["id"]]
        else:
            bag["items"][it["id"]] = have - n

    return _change(conn, cat, scope_key(viewer), change)


def equip(conn: sqlite3.Connection, viewer: dict | None, agent, slot, item_id, *,
          catalog: Catalog | None = None) -> dict:
    """Put a piece the bag holds on an agent (None: take that slot's off). Each copy is worn by one agent at a time:
    with every copy worn, putting it on another agent moves one (from the first agent wearing it, in agent order)."""
    cat = catalog or load_catalog()
    if agent not in CODING_AGENTS:
        raise PartyError(NO_AGENT)
    if slot not in SLOTS:
        raise PartyError(NO_SLOT)
    it = None if item_id is None else _known(cat, item_id)
    if it is not None and (it["kind"] != "gear" or it["slot"] != slot):
        raise PartyError(WRONG_SLOT)

    def change(bag: dict) -> None:
        worn = _worn(items(cat), bag)
        if it is not None:
            have = bag["items"].get(it["id"], 0)
            if not have:
                raise PartyError(NOT_OWNED)
            if worn[agent][slot] != it["id"]:
                others = [a for a in CODING_AGENTS if a != agent and worn[a][slot] == it["id"]]
                if len(others) >= have:
                    worn[others[0]][slot] = None
        worn[agent][slot] = it["id"] if it is not None else None
        bag["equipped"] = worn

    return _change(conn, cat, scope_key(viewer), change)


def use(conn: sqlite3.Connection, viewer: dict | None, item_id, *, catalog: Catalog | None = None) -> dict:
    """Use up one consumable or loot (the battle applies its effect)."""
    cat = catalog or load_catalog()
    it = _known(cat, item_id)
    if it["kind"] == "gear":
        raise PartyError(NOT_USABLE)

    def change(bag: dict) -> None:
        have = bag["items"].get(it["id"], 0)
        if not have:
            raise PartyError(NONE_LEFT)
        if have == 1:
            del bag["items"][it["id"]]
        else:
            bag["items"][it["id"]] = have - 1

    return _change(conn, cat, scope_key(viewer), change)
