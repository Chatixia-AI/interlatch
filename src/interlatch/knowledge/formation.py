"""Formations: decks a person builds from their coding agents on the Party page, each an ordered team of one to
TEAM_MAX agents. The Arena fields one of them (sharing/arena.py): its first agent fights first, and whoever wins a bout
stays on for the next (knowledge/arena.py). A deck changes only who fights and in what order, never a level.

Kept per scope (party.scope_key), in the kv table: DECKS_KEY + key → {"decks": [{"id", "name", "members": [agent, ...]}],
"arena": deck id}. Until a deck is saved, the party has one made for it (default_members): the champion picked before
decks existed (leaderboard.json "champion"), then the highest levels. A name may be empty: the page then numbers it.
Every change is one read-modify-write under kv_transaction.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata

from ..core.agents import CODING_AGENTS
from ..core.util import loads
from ..storage.kv import kv_get, kv_set, kv_transaction
from .party import PartyError, scope_key

DECKS_KEY = "party.decks:"  # + scope key
DECKS_MAX = 5
TEAM_MAX = 3
NAME_MAX = 24
ID_RE = re.compile(r"^d[1-9][0-9]?$")

NO_DECK = "no such deck"
TOO_MANY_DECKS = "a party holds at most 5 decks"
LAST_DECK = "a party keeps at least one deck"
BAD_MEMBERS = "a deck holds one to three of your agents, each once"


def default_members(levels: dict[str, int], lead=None) -> list[str]:
    """`lead` (one of the agents) first, then the highest levels (ties: CODING_AGENTS order), TEAM_MAX of them."""
    head = [lead] if lead in CODING_AGENTS else []
    rest = sorted((a for a in CODING_AGENTS if a not in head), key=lambda a: (-(levels.get(a) or 1), CODING_AGENTS.index(a)))
    return head + rest[:TEAM_MAX - len(head)]


def clean_members(members) -> list[str] | None:
    """The members as a deck keeps them, or None when they aren't one to TEAM_MAX distinct coding agents."""
    if not isinstance(members, list) or not 1 <= len(members) <= TEAM_MAX:
        return None
    if any(not isinstance(a, str) or a not in CODING_AGENTS for a in members) or len(set(members)) != len(members):
        return None
    return list(members)


def clean_name(name) -> str:
    """A deck's name: one line, no control characters, at most NAME_MAX characters ("" lets the page number it)."""
    if not isinstance(name, str):
        return ""
    name = " ".join(name.split())  # tabs and line breaks are spaces; then no other control character stays
    return "".join(c for c in name if unicodedata.category(c)[0] != "C")[:NAME_MAX].strip()


def load(conn: sqlite3.Connection, key: str) -> dict:
    """The scope's decks as stored, each checked (a malformed one is left out); "arena" only when it names one."""
    raw = loads(kv_get(conn, DECKS_KEY + key), {})
    raw = raw if isinstance(raw, dict) else {}
    decks, seen = [], set()
    for d in raw.get("decks") if isinstance(raw.get("decks"), list) else []:
        d = d if isinstance(d, dict) else {}
        members = clean_members(d.get("members"))
        if not isinstance(d.get("id"), str) or not ID_RE.match(d["id"]) or d["id"] in seen or members is None:
            continue
        seen.add(d["id"])
        decks.append({"id": d["id"], "name": clean_name(d.get("name")), "members": members})
        if len(decks) == DECKS_MAX:
            break
    arena = raw.get("arena") if raw.get("arena") in seen else None
    return {"decks": decks, "arena": arena}


def _save(conn: sqlite3.Connection, key: str, rec: dict) -> None:
    kv_set(conn, DECKS_KEY + key, json.dumps(rec, sort_keys=True))


def _or_default(rec: dict, levels: dict[str, int], lead) -> dict:
    """The stored decks, or (none saved yet) the one made for the party."""
    if rec["decks"]:
        return {"decks": rec["decks"], "arena": rec["arena"] or rec["decks"][0]["id"]}
    return {"decks": [{"id": "d1", "name": "", "members": default_members(levels, lead)}], "arena": "d1"}


def out(rec: dict, levels: dict[str, int], lead=None) -> dict:
    """What the Party page gets: the decks, which one the Arena fields, and the limits."""
    got = _or_default(rec, levels, lead)
    return {**got, "saved": bool(rec["decks"]), "max": DECKS_MAX, "team": TEAM_MAX}


def formations(conn: sqlite3.Connection, viewer: dict | None, levels: dict[str, int], lead=None) -> dict:
    return out(load(conn, scope_key(viewer)), levels, lead)


def arena_members(conn: sqlite3.Connection, levels: dict[str, int], lead=None) -> list[str]:
    """The team this computer fields in the Arena (its own scope): the arena deck's members, in order."""
    got = _or_default(load(conn, scope_key(None)), levels, lead)
    return next(d["members"] for d in got["decks"] if d["id"] == got["arena"])


def _change(conn: sqlite3.Connection, viewer: dict | None, levels: dict[str, int], lead, change) -> dict:
    """Read the decks (the one made for the party, if none is saved yet), change them (`change(rec)` raises
    PartyError to refuse), write them back: one transaction."""
    key = scope_key(viewer)
    with kv_transaction(conn):
        rec = _or_default(load(conn, key), levels, lead)
        change(rec)
        _save(conn, key, rec)
    return out(rec, levels, lead)


def _deck(rec: dict, deck_id) -> dict:
    d = next((d for d in rec["decks"] if d["id"] == deck_id), None)
    if d is None:
        raise PartyError(NO_DECK)
    return d


def save(conn: sqlite3.Connection, viewer: dict | None, levels: dict[str, int], deck_id=None, *, name=None,
         members=None, lead=None) -> dict:
    """Change a deck's name or members (None keeps them), or with no `deck_id` add a deck (its members: the ones
    given, else the party's highest levels)."""
    got = None if members is None else clean_members(members)
    if members is not None and got is None:
        raise PartyError(BAD_MEMBERS)

    def change(rec: dict) -> None:
        if deck_id is None:
            if len(rec["decks"]) >= DECKS_MAX:
                raise PartyError(TOO_MANY_DECKS)
            used = {d["id"] for d in rec["decks"]}
            new = next(f"d{n}" for n in range(1, 100) if f"d{n}" not in used)
            rec["decks"].append({"id": new, "name": clean_name(name), "members": got or default_members(levels)})
            return
        d = _deck(rec, deck_id)
        if name is not None:
            d["name"] = clean_name(name)
        if got is not None:
            d["members"] = got

    return _change(conn, viewer, levels, lead, change)


def delete(conn: sqlite3.Connection, viewer: dict | None, levels: dict[str, int], deck_id, *, lead=None) -> dict:
    """Delete a deck, never the last one; the Arena then fields the first deck left, if it fielded this one."""
    def change(rec: dict) -> None:
        d = _deck(rec, deck_id)
        if len(rec["decks"]) == 1:
            raise PartyError(LAST_DECK)
        rec["decks"].remove(d)
        if rec["arena"] == deck_id:
            rec["arena"] = rec["decks"][0]["id"]

    return _change(conn, viewer, levels, lead, change)


def field(conn: sqlite3.Connection, viewer: dict | None, levels: dict[str, int], deck_id, *, lead=None) -> dict:
    """The deck the Arena fields from now on."""
    def change(rec: dict) -> None:
        rec["arena"] = _deck(rec, deck_id)["id"]

    return _change(conn, viewer, levels, lead, change)
