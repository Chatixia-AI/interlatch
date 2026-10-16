"""The Arena: team against team. Two published party sheets each field a lineup, one to TEAM_MAX of their agents in
order (a deck from the Party page, knowledge/formation.py), and the two lineups fight a deterministic relay, so everyone
computes the same result for the same pair and week (sharing/arena.py publishes and reads the sheets). Pure functions:
no clock, no global random.

A sheet holds, per coding agent, its level (1..200), tier, class (a track id or None) and six attribute levels (0..100),
its lineup, and its champion (the lineup's first): nothing about any lesson. Stats come from those (stats()). Each side's
first agent fights the other's in a bout: rounds of turns by speed until one falls or MAX_ROUNDS pass (then the one with
less of its HP left is out). The one left standing stays on, with the HP it has, against the next agent of the side
that lost one; the side that runs out of agents loses. A lineup of one against a lineup of one is a plain duel.
"""

from __future__ import annotations

import hashlib
import random
import re
from datetime import date

from ..core.agents import CODING_AGENTS

SHEET_V = 3  # 2: a champion; 3: a lineup (its first is the champion)
ATTRS = ("craft", "data", "ops", "guard", "arcana", "lore")
TIERS = ("novice", "adept", "expert", "master")
LEVEL_MAX, ATTR_MAX = 200, 100
TEAM_MAX = 3  # agents in a lineup
MAX_ROUNDS = 40  # a bout's
LOG_MAX = 400
HEAL_EVERY = 3  # a fighter with lore >= HEAL_LORE heals itself on every 3rd turn of its own
HEAL_LORE = 10
SPECIALS = {"adept": "Second wind", "expert": "Focus", "master": "Overdrive"}
OVERDRIVE = 2.5  # the master's special: one hit this much harder
CLS_RE = re.compile(r"^[a-z0-9-]{1,64}$")


# ------------------------------------------------------------------ sheets
def _int(v, lo: int, hi: int) -> int:
    return max(lo, min(hi, v)) if isinstance(v, int) and not isinstance(v, bool) else lo


def clean_agent(a) -> dict:
    """One agent of a sheet within the rules' bounds (a sheet read from the board may be anything)."""
    a = a if isinstance(a, dict) else {}
    attrs = a.get("attrs") if isinstance(a.get("attrs"), dict) else {}
    cls = a.get("cls")
    return {"level": _int(a.get("level"), 1, LEVEL_MAX), "tier": a.get("tier") if a.get("tier") in TIERS else "novice",
            "cls": cls if isinstance(cls, str) and CLS_RE.match(cls) else None,
            "attrs": {k: _int(attrs.get(k), 0, ATTR_MAX) for k in ATTRS}}


def sheet_agents(party: dict) -> dict:
    """The agents of a computed party (party.compute) as a sheet holds them: level, tier, class, attr levels."""
    by = {a["agent"]: a for a in party.get("agents") or []}
    out = {}
    for name in CODING_AGENTS:
        a = by.get(name) or {}
        out[name] = clean_agent({"level": a.get("level", 1), "tier": a.get("tier"),
                                 "cls": (a.get("class") or {}).get("id"), "attrs": a.get("attr_levels") or {}})
    return out


def default_champion(agents: dict) -> str:
    """The champion when none was picked: the highest-level agent (ties: CODING_AGENTS order)."""
    return max(CODING_AGENTS, key=lambda n: (clean_agent((agents or {}).get(n))["level"], -CODING_AGENTS.index(n)))


def champion_of(agents: dict, picked) -> str:
    """`picked` when it is one of the five, else the default."""
    return picked if picked in CODING_AGENTS else default_champion(agents)


def lineup_of(agents: dict, lineup, champion=None) -> list[str]:
    """`lineup` as the rules take it: distinct coding agents in its order, at most TEAM_MAX; none of them, the champion
    alone (a sheet without a lineup)."""
    got = list(dict.fromkeys(a for a in lineup if isinstance(a, str) and a in CODING_AGENTS))[:TEAM_MAX] \
        if isinstance(lineup, list) else []
    return got or [champion_of(agents, champion)]


def power(agents: dict) -> int:
    """A party's power, as the board shows it: the sum of its agents' levels."""
    return sum(clean_agent(agents.get(n)).get("level", 1) for n in CODING_AGENTS)


# ------------------------------------------------------------------ the week's pairings
def week_of(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def seed_for(week: str, uid_a: str, uid_b: str) -> str:
    lo, hi = sorted((uid_a, uid_b))
    return hashlib.sha256(f"{week}:{lo}:{hi}".encode()).hexdigest()


def match(uid_1: str, sheet_1: dict, uid_2: str, sheet_2: dict, week: str) -> dict:
    """This week's battle between two players (sheets: {"agents", "lineup", "champion"}): sides a and b are the two
    uids sorted, so whichever of them asks, it is the same battle. Adds "a" and "b" (their uids) and "winner_uid"."""
    (ua, sa), (ub, sb) = sorted(((uid_1, sheet_1), (uid_2, sheet_2)), key=lambda p: p[0])
    out = battle(sa, sb, seed_for(week, ua, ub))
    return {**out, "a": ua, "b": ub, "winner_uid": ua if out["winner"] == "a" else ub}


def standings(sheets: dict[str, dict], week: str) -> dict[str, dict]:
    """{uid: {"wins", "losses"}}: every lineup against every other this week (sheets: {uid: sheet})."""
    out = {u: {"wins": 0, "losses": 0} for u in sheets}
    uids = sorted(sheets)
    for i, ua in enumerate(uids):
        for ub in uids[i + 1:]:
            won = match(ua, sheets[ua], ub, sheets[ub], week)["winner_uid"]
            out[won]["wins"] += 1
            out[ub if won == ua else ua]["losses"] += 1
    return out


# ------------------------------------------------------------------ the battle
def stats(agent: dict) -> dict:
    """A fighter's stats from its level and attribute levels."""
    a = clean_agent(agent)
    lv, at = a["level"], a["attrs"]
    return {"hp": 20 + 4 * lv + 2 * at["guard"], "atk": 5 + at["craft"] + at["arcana"] // 2,
            "def": at["guard"] + at["lore"] // 2, "spd": at["ops"] + lv // 2, "crit": min(30, 5 + at["data"] // 2),
            "heal": at["lore"] // 2 if at["lore"] >= HEAL_LORE else 0, "tier": a["tier"], "level": lv}


def battle(a_sheet: dict, b_sheet: dict, seed: str) -> dict:
    """`{"winner": "a"|"b", "rounds", "log", "champions", "lineups", "units"}`: side a's lineup against side b's, bout
    after bout, from `seed` (hex). Round numbers run on across bouts; a newcomer's entry is an "in" event, and a fighter
    out of time an "out" one."""
    rng = random.Random(int(seed, 16))
    teams: dict[str, list[dict]] = {}
    for side, sheet in (("a", a_sheet), ("b", b_sheet)):
        agents = (sheet or {}).get("agents") or {}
        teams[side] = []
        for i, name in enumerate(lineup_of(agents, (sheet or {}).get("lineup"), (sheet or {}).get("champion"))):
            s = stats(agents.get(name))
            teams[side].append({**s, "id": f"{side}:{name}", "agent": name, "order": i, "max": s["hp"], "turns": 0,
                                "special": False, "focus": False, "out": False, "key": rng.random()})
    log: list[dict] = []

    def emit(r, t, src, dst, n, hp, name=None):
        if len(log) < LOG_MAX - 1:  # room kept for "end"
            log.append({"r": r, "t": t, "src": src, "dst": dst, "n": n, "hp": hp, "name": name})

    def roll(u, foe) -> int:
        return max(1, u["atk"] - foe["def"] // 2)

    def hurt(r, src, dst, dmg, t, name=None):
        dst["hp"] = max(0, dst["hp"] - dmg)
        emit(r, t, src["id"], dst["id"], dmg, dst["hp"], name)
        if dst["hp"] == 0:
            emit(r, "ko", src["id"], dst["id"], 0, 0)
        elif not dst["special"] and dst["hp"] * 2 < dst["max"] and dst["tier"] in SPECIALS:
            special(r, dst, src)

    def special(r, u, foe):  # once per battle, when the fighter first drops below half its HP
        u["special"] = True
        name = SPECIALS[u["tier"]]
        if u["tier"] == "adept":
            gain = min(u["max"] - u["hp"], u["max"] // 4)
            u["hp"] += gain
            emit(r, "special", u["id"], u["id"], gain, u["hp"], name)
        elif u["tier"] == "expert":
            u["focus"] = True
            emit(r, "special", u["id"], None, 0, u["hp"], name)
        else:  # one hit, OVERDRIVE times harder
            hurt(r, u, foe, max(1, int(roll(u, foe) * OVERDRIVE * rng.uniform(0.85, 1.15))), "special", name)

    down = lambda u: u["hp"] <= 0 or u["out"]  # noqa: E731
    ia = ib = rounds = 0
    while ia < len(teams["a"]) and ib < len(teams["b"]):
        pair = (teams["a"][ia], teams["b"][ib])
        if rounds:  # a later bout: who stepped in
            for u in pair:
                if not u["turns"]:
                    emit(rounds, "in", u["id"], None, 0, u["hp"])
        for _ in range(MAX_ROUNDS):
            rounds += 1
            for u in sorted(pair, key=lambda x: (-x["spd"], x["key"])):
                foe = pair[1] if u is pair[0] else pair[0]
                if u["hp"] <= 0 or foe["hp"] <= 0:
                    continue
                u["turns"] += 1
                if u["heal"] and u["turns"] % HEAL_EVERY == 0 and u["hp"] < u["max"]:
                    gain = min(u["max"] - u["hp"], u["heal"])
                    u["hp"] += gain
                    emit(rounds, "heal", u["id"], u["id"], gain, u["hp"])
                crit = rng.random() * 100 < u["crit"]
                dmg = max(1, int(roll(u, foe) * (2 if crit else 1) * (2 if u["focus"] else 1) * rng.uniform(0.85, 1.15)))
                u["focus"] = False
                hurt(rounds, u, foe, dmg, "crit" if crit else "hit")
            if pair[0]["hp"] <= 0 or pair[1]["hp"] <= 0:
                break
        else:  # time: the one with less of its HP left is out; level with it, the seed decides
            left = [u["hp"] / u["max"] for u in pair]
            gone = pair[0] if left[0] < left[1] else pair[1] if left[1] < left[0] else rng.choice(pair)
            gone["out"] = True
            emit(rounds, "out", (pair[1] if gone is pair[0] else pair[0])["id"], gone["id"], 0, gone["hp"])
        ia += down(pair[0])
        ib += down(pair[1])
    winner = "a" if ia < len(teams["a"]) else "b"
    log.append({"r": rounds, "t": "end", "src": None, "dst": None, "n": 0, "hp": 0, "name": winner})
    return {"winner": winner, "rounds": rounds, "log": log,
            "champions": {s: teams[s][0]["agent"] for s in "ab"},
            "lineups": {s: [u["agent"] for u in teams[s]] for s in "ab"},
            # end_hp: where each fighter ended, also when the log was cut short (LOG_MAX); out: beaten on time
            "units": {u["id"]: {"hp": u["max"], "end_hp": u["hp"], "tier": u["tier"], "level": u["level"],
                                "order": u["order"], "out": u["out"]}
                      for s in "ab" for u in teams[s]}}
