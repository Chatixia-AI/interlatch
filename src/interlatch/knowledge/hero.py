"""The hero (Innerlatch): the person as a character of their own, made once on their own computer, on a career path
they choose, levelled only by what they show (docs/design/innerlatch.md, section 2). The coding agents are the hero's
companions: their levels stay lesson coverage (party.py), and nothing moves one meter from another.

The sheet keeps only the name and the look. The path is the Party's goal (party.GOAL_KEY): a roadmap track, or the
Generalist path (GENERALIST), built from the catalog, not picked by hand. The XP is worked out from the study record
each time (study.py), so past study counts from day one and nothing extra is stored that could drift.

Only someone at this computer (viewer None) has a hero: per-person learning data stays on the person's own computer
(docs/design/learning.md). A person signed in to a hub has none there.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date

from ..core.i18n import tr
from ..core.util import loads, utcnow, utcnow_iso
from ..storage.kv import kv_get, kv_set

HERO_KEY = "party.hero:"  # + scope key ("here" only): {"name", "look": {part: index}, "at"}
GENERALIST = "generalist"  # the path id of the Generalist path (party.GOAL_KEY holds it like a track id)
GENERALIST_TARGET = 2  # the rank the Generalist path asks of each of its modules
NAME_MAX = 24
# each part of the look: how many choices it has (sprites.js HERO_LOOK has the same counts, in the same order)
LOOK = {"body": 3, "skin": 6, "hair": 6, "hair_color": 8, "outfit": 8, "accessory": 5}
XP_FIRST = 10  # a lesson answered right for the first time
XP_AGAIN = 5  # a right answer on a later day, when it came back
XP_OWNED = 20  # the lesson became owned (study.OWNED_AT right on separate days in a row)
LEVEL_STEP = 10  # level L from LEVEL_STEP·(L-1)² XP: slower than the companions' (party.char_level)
SUGGEST = 3
NOT_HERE = "your hero lives on your own computer"
NO_NAME = "give your hero a name"
BAD_LOOK = "that look isn't one of the choices"


class HeroError(ValueError):
    """Something asked of the hero that can't be done: the message is an English template (tr())."""


# ------------------------------------------------------------------ the formulas
def level_of(xp: int) -> int:
    return math.isqrt(max(int(xp), 0) // LEVEL_STEP) + 1


def threshold(level: int) -> int:
    return LEVEL_STEP * (level - 1) ** 2


def total_xp(conn: sqlite3.Connection, log_: list[dict]) -> int:
    """The hero's XP: from the study record (xp_of) and from the gates passed (gates.xp_of)."""
    from . import gates

    return xp_of(log_) + gates.xp_of(gates.attempts_of(conn))


def xp_of(log_: list[dict]) -> int:
    """The XP a study record earns (study.studied_of, oldest first): each lesson's first right answer, each right
    answer on a later day, and the day it became owned. One answer a day counts per lesson (the day's last, as
    study.schedule reads it); a wrong answer or time away takes nothing away."""
    from .study import OWNED_AT

    days: dict[int, list] = {}
    for x in log_:
        seq = days.setdefault(x["id"], [])
        if seq and seq[-1][0] == x["on"]:
            seq[-1] = (x["on"], x["verdict"])
        else:
            seq.append((x["on"], x["verdict"]))
    xp = 0
    for seq in days.values():
        right = streak = 0
        owned = False
        for _, verdict in seq:
            if verdict != "got_it":
                streak = 0
                continue
            xp += XP_FIRST if not right else XP_AGAIN
            right += 1
            streak += 1
            if streak >= OWNED_AT and not owned:
                xp += XP_OWNED
                owned = True
    return xp


# ------------------------------------------------------------------ the paths
def generalist(cat) -> list[str]:
    """The Generalist path's modules, in the catalog's order: those at least half of all tracks include, and those at
    least five tracks mark as critical."""
    n = len(cat.tracks)
    inn: dict[str, int] = {}
    crit: dict[str, int] = {}
    for t in cat.tracks:
        for mid in set(t.get("modules") or []):
            inn[mid] = inn.get(mid, 0) + 1
        for mid in set(t.get("critical") or []):
            crit[mid] = crit.get(mid, 0) + 1
    return [m["id"] for m in cat.modules if 2 * inn.get(m["id"], 0) >= n or crit.get(m["id"], 0) >= 5]


def path_modules(cat, path: str | None) -> list[dict]:
    """The modules a path measures you against: [{id, target, critical}], the track's order (None: no path)."""
    if path == GENERALIST:
        return [{"id": mid, "target": GENERALIST_TARGET, "critical": False} for mid in generalist(cat)]
    t = cat.track.get(path) if path else None
    if not t:
        return []
    targets, critical = t.get("targets") or {}, set(t.get("critical") or [])
    return [{"id": mid, "target": targets.get(mid) if isinstance(targets.get(mid), int) else None,
             "critical": mid in critical} for mid in t.get("modules") or [] if mid in cat.module]


# ------------------------------------------------------------------ the sheet
def load(conn: sqlite3.Connection) -> dict | None:
    """This computer's hero sheet, or None before the hero is made."""
    got = loads(kv_get(conn, HERO_KEY + "here"), None)
    if not isinstance(got, dict) or not isinstance(got.get("name"), str) or not got["name"].strip():
        return None
    look = got.get("look") if isinstance(got.get("look"), dict) else {}
    return {"name": got["name"], "look": {k: _part(look.get(k), n) for k, n in LOOK.items()}, "at": got.get("at")}


def _part(v, n: int) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < n else 0


def save(conn: sqlite3.Connection, viewer: dict | None, name, look) -> dict:
    """Make the hero, or change its name and look. Only at this computer."""
    if viewer is not None:
        raise HeroError(NOT_HERE)
    name = " ".join(name.split())[:NAME_MAX] if isinstance(name, str) else ""
    if not name:
        raise HeroError(NO_NAME)
    if not isinstance(look, dict) or set(look) - set(LOOK):
        raise HeroError(BAD_LOOK)
    for k, n in LOOK.items():
        v = look.get(k, 0)
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < n:
            raise HeroError(BAD_LOOK)
    was = load(conn)
    sheet = {"name": name, "look": {k: look.get(k, 0) for k in LOOK}, "at": (was or {}).get("at") or utcnow_iso()}
    kv_set(conn, HERO_KEY + "here", json.dumps(sheet, sort_keys=True))
    conn.commit()
    return sheet


# ------------------------------------------------------------------ the hero as the Party shows it
def view(conn: sqlite3.Connection, here_id: str, viewer: dict | None, data: dict, *, catalog=None,
         today: date | None = None) -> dict | None:
    """The `hero` block of GET /api/party: None for a person on a hub; {"made": False, looks, suggest} before the hero
    is made; else the sheet, the level, the path with each module's readiness, rank and gate (gates.py) against its
    target, and every gate open now (`gates`, the path's first). `data`: the party's compute() payload (its goal is the
    path, its agents' classes the suggestions)."""
    from . import gates, party, study

    if viewer is not None:
        return None
    cat = catalog or party.load_catalog()
    suggest = []
    for a in sorted(data.get("agents") or [], key=lambda x: (-x["level"], -x["xp"])):
        c = a.get("class")
        if c and c["id"] not in (s["id"] for s in suggest) and len(suggest) < SUGGEST:
            suggest.append({"id": c["id"], "name": c["name"], "agent": a["agent"]})
    sheet = load(conn)
    if sheet is None:
        return {"made": False, "looks": LOOK, "suggest": suggest, "name_max": NAME_MAX,
                "generalist": {"id": GENERALIST, "name": tr("Generalist"), "modules": len(generalist(cat))}}
    sc = party._scope_of(conn, here_id, None)
    counted = [k for k in party.lessons_in(conn, cat, sc) if k.modules]
    log_ = study.studied_of(conn, sc.key)
    sched = study.schedule(log_, today or utcnow().date())
    ready = study.readiness(counted, cat, log_)
    tried = gates.attempts_of(conn)
    xp = xp_of(log_) + gates.xp_of(tried)
    lv = level_of(xp)
    goal = data.get("goal")
    path_id = goal["id"] if goal and goal.get("chosen") else None
    day = today or utcnow().date()
    gate = {m["id"]: gates.state(m["id"], ready.get(m["id"], 0), tried, day) for m in cat.modules}
    gate_of = lambda mid: {k: gate[mid][k] for k in ("next", "open", "resting")}  # noqa: E731
    modules = [{**m, "name": party._t(cat.module[m["id"]], "name"), "attr": cat.module[m["id"]].get("attr"),
                "ready": ready.get(m["id"], 0), "rank": gate[m["id"]]["rank"], "gate": gate_of(m["id"])}
               for m in path_modules(cat, path_id)]
    on_path = {m["id"] for m in modules}
    open_now = sorted((mid for mid, g in gate.items() if g["open"]), key=lambda mid: (mid not in on_path, cat.module_index[mid]))
    return {"made": True, **sheet, "looks": LOOK, "name_max": NAME_MAX, "suggest": suggest,
            "level": lv, "xp": xp, "level_xp": threshold(lv), "next_xp": threshold(lv + 1),
            "understood": len(study.understood(log_)), "owned": sum(1 for x in sched.values() if x["owned"]),
            "ready_at": study.READY_AT, "ranks": sum(g["rank"] for g in gate.values()),
            "gates": [{"module": mid, "name": party._t(cat.module[mid], "name"), "attr": cat.module[mid].get("attr"),
                       "rank": gate[mid]["next"]} for mid in open_now],
            "path": {"id": path_id, "name": goal["name"], "generalist": path_id == GENERALIST, "modules": modules}
            if path_id else None,
            "generalist": {"id": GENERALIST, "name": tr("Generalist"), "modules": len(generalist(cat))}}
