"""The Arena's Firestore side: people on the leaderboard who opt in publish a small sheet of their party at
`arena/{uid}` (each agent's level, tier, class and six attribute levels, and its lineup: the deck it fields, in order,
whose first is its champion; nothing else), sent with the board's row; the dashboard reads every sheet and has the
lineups fight this week's battles (knowledge/arena.py), each on its own computer, all with the same result. The deck
is picked on the Party page or here (knowledge/formation.py).

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


def _agents(cfg: Config, conn) -> dict:
    from ..core.machine import local_machine_id
    from ..knowledge import party

    return rules.sheet_agents(party.compute(conn, local_machine_id(cfg)))


def _levels(agents: dict) -> dict[str, int]:
    return {n: a["level"] for n, a in agents.items()}


def lineup(cfg: Config, conn, state: dict | None, agents: dict | None = None) -> list[str]:
    """The team this computer fields, in order: its arena deck (formation.py), else the one made for the party (the
    champion picked before decks, leaderboard.json "champion", first)."""
    from ..knowledge import formation

    agents = agents if agents is not None else _agents(cfg, conn)
    return rules.lineup_of(agents, formation.arena_members(conn, _levels(agents), (state or {}).get("champion")))


def sheet(cfg: Config, conn, state: dict) -> dict:
    """This computer's sheet, from its own party (party.compute, this computer's scope), without the server time."""
    agents = _agents(cfg, conn)
    team = lineup(cfg, conn, state, agents)
    return {"name": state["name"], "photo": state.get("photo") or "", "machine": lb.machine_tag(cfg), "v": rules.SHEET_V,
            "agents": agents, "lineup": team, "champion": team[0]}


def fielding(cfg: Config, conn, state: dict | None) -> dict:
    """For the Arena page: the team this computer fields (its arena deck, in order; the first is its champion), its
    decks to pick from (formation.formations), and each agent's level and tier for their sprites."""
    from ..knowledge import formation

    agents = _agents(cfg, conn)
    team = lineup(cfg, conn, state, agents)
    return {"lineup": team, "champion": team[0],
            "decks": formation.formations(conn, None, _levels(agents), (state or {}).get("champion")),
            "agents": {n: {"level": a["level"], "tier": a["tier"]} for n, a in agents.items()}}


def pick(cfg: Config, conn, deck_id) -> dict:
    """Field another deck; sent now when in the arena."""
    from ..knowledge import formation
    from ..knowledge.party import PartyError

    state = lb.read_state(cfg)
    if state is None:
        raise lb.LeaderboardError("sign in first")
    agents = _agents(cfg, conn)
    try:
        formation.field(conn, None, _levels(agents), deck_id, lead=state.get("champion"))
    except PartyError as e:
        raise lb.LeaderboardError(str(e)) from None
    return resend(cfg, conn)


def resend(cfg: Config, conn) -> dict | None:
    """The team changed (another deck fielded, or the fielded one edited): sent now when in the arena."""
    state = lb.read_state(cfg)
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
    """Every sheet in the arena (at most lb.TOP), cleaned to the rules' bounds: {uid: {name, photo, agents, lineup,
    champion}}. Read again at most every lb.BOARD_TTL."""
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
        team = rules.lineup_of(agents, f.get("lineup"), f.get("champion"))
        out[uid] = {"name": name, "photo": bool(photo), "agents": agents, "lineup": team, "champion": team[0]}
    with _lock:
        _cache["sheets"] = (time.monotonic(), out)
    return out


def week(now=None) -> str:
    return rules.week_of((now or utcnow()).date())


def board(me_uid: str | None, *, now=None) -> list[dict]:
    """The arena board this week: every party ranked by wins (then fewer losses, then power, then name)."""
    got = sheets(now=now)
    wk = week(now)
    key = hashlib.sha256(json.dumps([wk, sorted((u, s["agents"], s["lineup"]) for u, s in got.items())],
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
             "power": rules.power(s["agents"]), "champion": s["champion"], "lineup": s["lineup"], "me": u == me_uid,
             "agents": {n: s["agents"][n] for n in s["lineup"]}}  # the lineup's levels and tiers, for their sprites
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
                      "lineup": got[u]["lineup"], "champion": got[u]["champion"]}
    return {"me": lite(me_uid), "rival": lite(rival_uid), "week": wk,
            "result": rules.match(me_uid, got[me_uid], rival_uid, got[rival_uid], wk)}
