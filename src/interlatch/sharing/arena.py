"""The Arena's Firestore side: people on the leaderboard who opt in publish a small sheet of their party at
`arena/{uid}` (each agent's level, tier, class and six attribute levels, and which one is its champion; nothing
else), sent with the board's row; the dashboard reads every sheet and has the champions fight this week's duels (knowledge/arena.py), each on its own
computer, all with the same result.

Opting in needs being on the board (leaderboard.on_board); leaving the board leaves the arena. `arena` in
<home>/leaderboard.json says whether this computer sends its sheet (missing: it doesn't).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from urllib.parse import quote

from ..core.config import Config
from ..core.util import utcnow
from ..knowledge import arena as rules
from . import leaderboard as lb

COLLECTION = "arena"
_cache: dict[str, tuple] = {}  # "sheets": (read at, sheets); "standings": (their key, standings)
_lock = threading.Lock()


def in_arena(state: dict | None) -> bool:
    """This computer sends its party's sheet: on the board, and opted in."""
    return lb.on_board(state) and bool((state or {}).get("arena"))


def sheet(cfg: Config, conn, state: dict) -> dict:
    """This computer's sheet, from its own party (party.compute, this computer's scope), without the server time."""
    from ..core.machine import local_machine_id
    from ..knowledge import party

    agents = rules.sheet_agents(party.compute(conn, local_machine_id(cfg)))
    return {"name": state["name"], "photo": state.get("photo") or "", "machine": lb.machine_tag(cfg), "v": rules.SHEET_V,
            "agents": {n: rules.encode_agent(a) for n, a in agents.items()},  # one string each: see knowledge/arena.py
            "champion": rules.champion_of(agents, state.get("champion"))}


def champion(cfg: Config, conn, state: dict | None) -> str:
    """The champion this computer fields: the one picked (leaderboard.json "champion"), else the highest level."""
    from ..core.machine import local_machine_id
    from ..knowledge import party

    agents = rules.sheet_agents(party.compute(conn, local_machine_id(cfg)))
    return rules.champion_of(agents, (state or {}).get("champion"))


def pick(cfg: Config, conn, agent) -> dict:
    """Field another champion (one of the five coding agents); sent now when in the arena."""
    state = lb.read_state(cfg)
    if state is None:
        raise lb.LeaderboardError("sign in first")
    if agent not in rules.CODING_AGENTS:
        raise lb.LeaderboardError("no such agent")
    state = lb._update_state(cfg, champion=agent)
    clear()
    return lb.send(cfg, conn, force=True) if in_arena(state) else state


def write(cfg: Config, conn, state: dict) -> dict:
    """The commit write that puts this computer's sheet at arena/{uid}, stamped with the server's time."""
    fields = {k: lb._value(v) for k, v in sheet(cfg, conn, state).items()}
    return {"update": {"name": f"{lb.DB}/{COLLECTION}/{state['uid']}", "fields": fields},
            "updateTransforms": [{"fieldPath": "at", "setToServerValue": "REQUEST_TIME"}]}


def _delete(cfg: Config, state: dict) -> None:
    try:
        lb._call("DELETE", f"{lb.FIRESTORE}/{COLLECTION}/{quote(state['uid'])}", token=lb._id_token(cfg, state))
    except lb.LeaderboardError as e:
        if not str(e).startswith("404"):
            raise


def join(cfg: Config, conn) -> dict:
    """Enter the arena: needs being on the board; the sheet goes with the row, now."""
    state = lb.read_state(cfg)
    if not lb.on_board(state):
        raise lb.LeaderboardError("join the board first")
    lb._update_state(cfg, arena=True)
    clear()
    return lb.send(cfg, conn, force=True)


def leave(cfg: Config, state: dict | None = None) -> None:
    """Leave the arena: the sheet is deleted, and no more is sent."""
    state = state or lb.read_state(cfg)
    if state is None:
        return
    lb._update_state(cfg, arena=False)  # first: a send under way sees it before its commit (leaderboard.send)
    _delete(cfg, state)
    clear()


def clear() -> None:
    with _lock:
        _cache.clear()


def sheets(*, now=None) -> dict[str, dict]:
    """Every sheet in the arena (at most lb.TOP), cleaned to the rules' bounds: {uid: {name, photo, agents, champion}}. Read
    again at most every lb.BOARD_TTL."""
    with _lock:
        hit = _cache.get("sheets")
        if hit and time.monotonic() - hit[0] < lb.BOARD_TTL:
            return hit[1]
    res = lb._call("POST", f"{lb.FIRESTORE}:runQuery?key={lb.api_key()}", body={"structuredQuery": {
        "from": [{"collectionId": COLLECTION}], "limit": lb.TOP}})
    out = {}
    for item in res if isinstance(res, list) else []:
        doc = item.get("document")
        if not doc:
            continue
        f = lb._plain(doc.get("fields"))
        name = lb.clean_name(f.get("name"))
        agents = f.get("agents") if isinstance(f.get("agents"), dict) else None
        if not name or agents is None or f.get("v") != rules.SHEET_V:  # an older format: not in this arena
            continue
        uid = doc["name"].rsplit("/", 1)[-1]
        photo = f.get("photo") if isinstance(f.get("photo"), str) and lb.PHOTO_RE.match(f["photo"]) else ""
        if photo:
            lb._photos[uid] = photo
        agents = {n: rules.clean_agent(agents.get(n)) for n in rules.CODING_AGENTS}
        out[uid] = {"name": name, "photo": bool(photo), "agents": agents,
                    "champion": rules.champion_of(agents, f.get("champion"))}
    with _lock:
        _cache["sheets"] = (time.monotonic(), out)
    return out


def week(now=None) -> str:
    return rules.week_of((now or utcnow()).date())


def board(me_uid: str | None, *, now=None) -> list[dict]:
    """The arena board this week: every party ranked by wins (then fewer losses, then power, then name)."""
    got = sheets(now=now)
    wk = week(now)
    key = hashlib.sha256(json.dumps([wk, sorted((u, s["agents"], s["champion"]) for u, s in got.items())],
                                    sort_keys=True).encode()).hexdigest()
    with _lock:
        hit = _cache.get("standings")
    if hit and hit[0] == key:
        table = hit[1]
    else:  # one slot: this week's standings for the sheets as they are
        table = rules.standings(got, wk)
        with _lock:
            _cache["standings"] = (key, table)
    rows = [{"uid": u, "name": s["name"], "photo": s["photo"], "wins": table[u]["wins"], "losses": table[u]["losses"],
             "power": rules.power(s["agents"]), "champion": s["champion"], "me": u == me_uid,
             "agents": {s["champion"]: s["agents"][s["champion"]]}}  # the champion's level and tier, for its sprite
            for u, s in got.items()]
    rows.sort(key=lambda r: (-r["wins"], r["losses"], -r["power"], r["name"], r["uid"]))
    return [{"rank": i + 1, **r} for i, r in enumerate(rows)]


def fight(me_uid: str, rival_uid: str, *, now=None) -> dict | None:
    """This week's battle between me and a rival, both in the arena; None when either isn't."""
    got = sheets(now=now)
    if me_uid not in got or rival_uid not in got or me_uid == rival_uid:
        return None
    wk = week(now)
    lite = lambda u: {"uid": u, "name": got[u]["name"], "photo": got[u]["photo"], "agents": got[u]["agents"],  # noqa: E731
                      "champion": got[u]["champion"]}
    return {"me": lite(me_uid), "rival": lite(rival_uid), "week": wk,
            "result": rules.match(me_uid, got[me_uid], rival_uid, got[rival_uid], wk)}
