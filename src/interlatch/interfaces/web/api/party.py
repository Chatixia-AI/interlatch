"""The Party (knowledge/party.py): the coding agents as characters levelled by their lessons on the roadmap, behind a
sign-in: the leaderboard's Google sign-in on a person's own computer (signing in for the Party doesn't join the
board), or being signed in to a hub as a person. Before that, nothing about the agents is answered."""

from __future__ import annotations

import time

from ....core.i18n import tr
from ..context import Context

NOT_SIGNED_IN = "sign in to see your party"


def who(ctx: Context, viewer: dict | None) -> dict | None:
    """Who the Party is for: a hub person, or the Google sign-in kept on this computer; None before signing in."""
    from ....sharing import leaderboard as lb

    if viewer is not None:
        return {"kind": "hub", "name": viewer.get("name"), "uid": None, "photo": False, "board": False}
    state = lb.read_state(ctx.cfg)
    if state is None:
        return None
    return {"kind": "google", "name": state.get("name"), "uid": state.get("uid"), "photo": bool(state.get("photo")),
            "board": lb.on_board(state)}


def _signing_in(ctx: Context) -> bool:
    s = ctx.state.leaderboard_signin
    return bool(s and s["expires"] > time.time())


def _here_id(ctx: Context) -> str:
    from ....core.machine import local_machine_id

    return local_machine_id(ctx.cfg)


def party(ctx: Context, viewer: dict | None, *, here: bool) -> dict:
    """GET /api/party. `here`: the request comes from this computer, the only place a Google sign-in can start."""
    from ....knowledge import party as pt

    # a sign-in runs only at this computer: a hub person isn't told about one in progress there
    gate = {"signing_in": here and _signing_in(ctx), "can_sign_in": here}
    me = who(ctx, viewer)
    if me is None:  # the Party stays locked; at this computer, the week's lessons show (they aren't gated)
        return {"signed_in": False, **gate, **({"preview": pt.preview(ctx.conn, _here_id(ctx))} if here else {})}
    from ....knowledge import hero

    data = pt.compute(ctx.conn, _here_id(ctx), viewer)
    # who's hiring for the classes it shows: read from Firebase only now, signed in (the numbers are public)
    return {"signed_in": True, **gate, "who": me, **data, "market": pt.market_block(pt.payload_tracks(data)),
            "formations": _formations(ctx, viewer, data), "hero": hero.view(ctx.conn, _here_id(ctx), viewer, data)}


def _levels(data: dict) -> dict[str, int]:
    return {a["agent"]: a["level"] for a in data.get("agents") or []}


def _lead(ctx: Context, viewer: dict | None):
    """The champion picked on this computer before decks existed: the first of the deck made for its party."""
    from ....sharing import leaderboard as lb

    return (lb.read_state(ctx.cfg) or {}).get("champion") if viewer is None else None


def _formations(ctx: Context, viewer: dict | None, data: dict) -> dict:
    from ....knowledge import formation

    return formation.formations(ctx.conn, viewer, _levels(data), _lead(ctx, viewer))


def _refused() -> tuple[dict, int]:
    return {"error": tr(NOT_SIGNED_IN), "signin": True}, 403


def skill(ctx: Context, viewer: dict | None, q: dict) -> tuple[dict, int]:
    from ....knowledge import party as pt

    if who(ctx, viewer) is None:
        return _refused()
    got = pt.skill(ctx.conn, _here_id(ctx), viewer, q.get("agent"), q.get("module"))
    return (got, 200) if got else ({"error": tr("not found")}, 404)


def track(ctx: Context, viewer: dict | None, q: dict) -> tuple[dict, int]:
    from ....knowledge import party as pt

    if who(ctx, viewer) is None:
        return _refused()
    got = pt.track(ctx.conn, _here_id(ctx), viewer, q.get("id"))
    return ({**got, "market": pt.market_block([got["id"]])}, 200) if got else ({"error": tr("not found")}, 404)


def tracks(ctx: Context, viewer: dict | None) -> dict:
    """Every track by category, for the goal picker: the catalog's own content, nobody's data. Signed in, with each
    track's hiring numbers too (only then is the market read)."""
    from ....knowledge import party as pt

    data = pt.tracks()
    return data if who(ctx, viewer) is None else pt.market_tracks(data)


def adventure_map(ctx: Context, viewer: dict | None) -> tuple[dict, int]:
    """GET /api/party/map: the Adventure's world (knowledge/party.py adventure_map)."""
    from ....knowledge import party as pt

    if who(ctx, viewer) is None:
        return _refused()
    return pt.adventure_map(ctx.conn, _here_id(ctx), viewer), 200


def encounter(ctx: Context, viewer: dict | None, q: dict) -> tuple[dict, int]:
    """GET /api/party/encounter?id=<lesson id>: one of the viewer's lessons as an encounter; 404 for any other."""
    from ....knowledge import party as pt

    if who(ctx, viewer) is None:
        return _refused()
    try:
        lesson_id = int(q.get("id") or "")
    except ValueError:
        return {"error": tr("not found")}, 404
    got = pt.encounter(ctx.conn, _here_id(ctx), viewer, lesson_id)
    return (got, 200) if got else ({"error": tr("not found")}, 404)


def action_party_cleared(ctx: Context, viewer: dict | None, body: dict, *, here: bool) -> tuple[dict, int]:
    """Mark a lesson's encounter cleared or not, in the viewer's own log; answers the log, what the win paid (null
    when nothing: not a first clear) and the bag."""
    from ....knowledge import bag
    from ....knowledge import party as pt

    if who(ctx, viewer) is None:
        return _refused()
    try:
        log, reward = pt.clear(ctx.conn, _here_id(ctx), viewer, body.get("id"), bool(body.get("cleared")))
    except pt.PartyError as exc:
        return {"error": tr(str(exc))}, 400
    return {"cleared": log, "reward": reward, "bag": bag.bag(ctx.conn, viewer)}, 200


# ------------------------------------------------------------------ study battles (knowledge/study.py): the person
# picks the answer to a lesson's question among options written for it; only at their own computer
def ask(ctx: Context, viewer: dict | None, q: dict) -> tuple[dict, int]:
    """GET /api/party/ask?id=<lesson id>: the question a study battle asks: its scene, question and the options' texts
    (never which is right), or {available: false, reason} where there is none (on a hub; no model)."""
    from ....knowledge import party as pt
    from ....knowledge import study

    if who(ctx, viewer) is None:
        return _refused()
    try:
        lesson_id = int(q.get("id") or "")
    except ValueError:
        return {"error": tr("not found")}, 404
    if not pt.owns(ctx.conn, _here_id(ctx), viewer, lesson_id):
        return {"error": tr("not found")}, 404
    if viewer is not None:
        return {"available": False, "reason": tr(study.NOT_HERE)}, 200
    try:
        got = study.ask(ctx.conn, ctx.cfg, lesson_id)
    except study.StudyError as exc:
        return {"available": False, "reason": tr(str(exc))}, 200
    except Exception as exc:  # the model's call failed: no study battle this time, the fight still runs
        return {"available": False, "reason": tr(study.NO_MODEL, reason=str(exc)[:200])}, 200
    return {"available": True, "scene": got["scene"], "question": got["question"], "options": [o["text"] for o in got["options"]]}, 200


def action_party_pick(ctx: Context, viewer: dict | None, body: dict, *, here: bool) -> tuple[dict, int]:
    """POST /api/party/pick {"id", "choice"}: the person's first pick among a lesson's options (an index into those
    /api/party/ask sent), kept on this computer. Answers the verdict, the right option, why the pick is right or
    wrong, the principle, when the lesson comes back, and what the person has understood now."""
    from ....knowledge import party as pt
    from ....knowledge import study

    if who(ctx, viewer) is None:
        return _refused()
    from ....knowledge import hero

    lesson_id = body.get("id")
    if not pt.owns(ctx.conn, _here_id(ctx), viewer, lesson_id):
        return {"error": tr("no such lesson")}, 400
    was = hero.total_xp(ctx.conn, study.studied_of(ctx.conn, pt.scope_key(viewer)))
    try:
        got = study.pick(ctx.conn, ctx.cfg, pt.scope_key(viewer), lesson_id, body.get("choice"), here=viewer is None)
    except study.StudyError as exc:
        return {"error": tr(str(exc))}, 400
    except Exception as exc:  # the model's call failed: nothing was kept
        return {"error": tr(study.NO_MODEL, reason=str(exc)[:200])}, 502
    m = pt.adventure_map(ctx.conn, _here_id(ctx), viewer)
    out = {**got, "understood": m["understood"], "readiness": m["readiness"]}
    if viewer is None and hero.load(ctx.conn) is not None:  # what the hero earned by it
        now = hero.total_xp(ctx.conn, study.studied_of(ctx.conn, pt.scope_key(viewer)))
        out["hero"] = {"xp": now, "gained": now - was, "level": hero.level_of(now), "was_level": hero.level_of(was)}
    return out, 200


# ------------------------------------------------------------------ gate bosses (knowledge/gates.py): a module's rank
# rises only there, with the person's own work judged by the model; only at their own computer
def gate(ctx: Context, viewer: dict | None, q: dict) -> tuple[dict, int]:
    """GET /api/party/gate?module=<id>: what the module's gate asks, whether it's open, the last attempt, the work
    that can be linked."""
    from ....knowledge import gates
    from ....knowledge import party as pt

    if who(ctx, viewer) is None:
        return _refused()
    if viewer is not None:
        return {"error": tr(gates.NOT_HERE)}, 403
    got = gates.view(ctx.conn, pt.load_catalog(), _here_id(ctx), q.get("module") or "")
    return (got, 200) if got else ({"error": tr("not found")}, 404)


def action_party_gate(ctx: Context, viewer: dict | None, body: dict, *, here: bool) -> tuple[dict, int]:
    """POST /api/party/gate {"module", "artifacts": [artifact id], "text"}: try the module's open gate. Answers the
    verdict, what was met and what is missing, the rank shown now and the XP the hero gained, and the gate as it is."""
    from ....knowledge import gates
    from ....knowledge import party as pt

    if who(ctx, viewer) is None:
        return _refused()
    if viewer is not None:
        return {"error": tr(gates.NOT_HERE)}, 403
    cat, here_id, module = pt.load_catalog(), _here_id(ctx), body.get("module")
    ready = gates.readiness(ctx.conn, cat, here_id).get(module, 0) if isinstance(module, str) else 0
    try:
        got = gates.attempt(ctx.conn, ctx.cfg, cat, here_id, module, body.get("artifacts"), body.get("text"),
                            ready=ready, here=True)
    except gates.GateError as exc:
        return {"error": tr(exc.template, **exc.kw)}, 400
    except Exception as exc:  # the model's call failed: nothing was kept
        return {"error": tr(gates.NO_MODEL, reason=str(exc)[:200])}, 502
    return {**got, "gate": gates.view(ctx.conn, cat, here_id, module)}, 200


# ------------------------------------------------------------------ the Town & gear (knowledge/bag.py)
def bag(ctx: Context, viewer: dict | None) -> tuple[dict, int]:
    """GET /api/party/bag: the viewer's gold, items, what each agent wears and the battle stats it adds."""
    from ....knowledge import bag as bg

    if who(ctx, viewer) is None:
        return _refused()
    return bg.bag(ctx.conn, viewer), 200


def shop(ctx: Context, viewer: dict | None, q: dict) -> tuple[dict, int]:
    """GET /api/party/shop?region=<attr>: a region's town; 404 for a region the roadmap doesn't have."""
    from ....knowledge import bag as bg

    if who(ctx, viewer) is None:
        return _refused()
    got = bg.shop(ctx.conn, _here_id(ctx), viewer, q.get("region"))
    return (got, 200) if got else ({"error": tr("not found")}, 404)


def action_party_bag(ctx: Context, viewer: dict | None, what: str, body: dict, *, here: bool) -> tuple[dict, int]:
    """POST /api/party/buy, sell, equip, use: one change to the viewer's own bag; answers the bag."""
    from ....knowledge import bag as bg
    from ....knowledge import party as pt

    if who(ctx, viewer) is None:
        return _refused()
    try:
        if what == "buy":
            got = bg.buy(ctx.conn, _here_id(ctx), viewer, body.get("id"), body.get("n"), body.get("region"))
        elif what == "sell":
            got = bg.sell(ctx.conn, viewer, body.get("id"), body.get("n"))
        elif what == "equip":
            got = bg.equip(ctx.conn, viewer, body.get("agent"), body.get("slot"), body.get("id"))
        else:
            got = bg.use(ctx.conn, viewer, body.get("id"))
    except pt.PartyError as exc:
        return {"error": tr(str(exc))}, 400
    return {"bag": got}, 200


def action_party_deck(ctx: Context, viewer: dict | None, body: dict, *, here: bool) -> tuple[dict, int]:
    """POST /api/party/deck: add a deck (no "id"), rename it or change its members in order ("name", "members"),
    delete it ("delete": true), or field it in the Arena ("arena": true, this computer's own party only). Answers the
    decks; when the team this computer fields changed and it is in the arena, the sheet is sent now."""
    from ....knowledge import formation
    from ....knowledge import party as pt

    if who(ctx, viewer) is None:
        return _refused()
    deck = body.get("id")
    levels = _levels(pt.compute(ctx.conn, _here_id(ctx), viewer))
    lead = _lead(ctx, viewer)
    before = formation.formations(ctx.conn, viewer, levels, lead)
    try:
        if deck is not None and not isinstance(deck, str):
            raise pt.PartyError(formation.NO_DECK)
        if body.get("delete"):
            got = formation.delete(ctx.conn, viewer, levels, deck, lead=lead)
        elif body.get("arena"):
            if viewer is not None:  # a hub person's party never enters the arena
                raise pt.PartyError(formation.NO_DECK)
            got = formation.field(ctx.conn, viewer, levels, deck, lead=lead)
        else:
            got = formation.save(ctx.conn, viewer, levels, deck, name=body.get("name"), members=body.get("members"),
                                 lead=lead)
    except pt.PartyError as exc:
        return {"error": tr(str(exc))}, 400
    if viewer is None and _fielded(before) != _fielded(got):
        from ....sharing import arena

        arena.resend(ctx.cfg, ctx.conn)
    return {"formations": got}, 200


def _fielded(f: dict) -> list[str]:
    return next((d["members"] for d in f["decks"] if d["id"] == f["arena"]), [])


def action_party_quest(ctx: Context, viewer: dict | None, body: dict, *, here: bool) -> tuple[dict, int]:
    """Mark a quest done or not, in the viewer's own log; answers the party as it is now."""
    from ....knowledge import party as pt

    if who(ctx, viewer) is None:
        return _refused()
    try:
        pt.set_quest(ctx.conn, viewer, body.get("id"), bool(body.get("done")))
    except pt.PartyError as exc:
        return {"error": tr(str(exc))}, 400
    return party(ctx, viewer, here=here), 200


def action_party_hero(ctx: Context, viewer: dict | None, body: dict, *, here: bool) -> tuple[dict, int]:
    """POST /api/party/hero {"name", "look", "path"?}: make the hero, or change its name and look; `path` (a track id
    or "generalist") also sets the path, the Party's goal. Only at this computer. Answers the party as it is now."""
    from ....knowledge import hero
    from ....knowledge import party as pt

    if who(ctx, viewer) is None:
        return _refused()
    if viewer is not None:
        return {"error": tr(hero.NOT_HERE)}, 403
    try:
        if "path" in body:  # checked first: a bad path keeps the hero as it was
            pt.set_goal(ctx.conn, viewer, body.get("path"))
        hero.save(ctx.conn, viewer, body.get("name"), body.get("look"))
    except (hero.HeroError, pt.PartyError) as exc:
        return {"error": tr(str(exc))}, 400
    return party(ctx, viewer, here=here), 200


def action_party_goal(ctx: Context, viewer: dict | None, body: dict, *, here: bool) -> tuple[dict, int]:
    """Follow another track as the main quest (null: back to the strongest agent's class)."""
    from ....knowledge import party as pt

    if who(ctx, viewer) is None:
        return _refused()
    try:
        pt.set_goal(ctx.conn, viewer, body.get("track"))
    except pt.PartyError as exc:
        return {"error": tr(str(exc))}, 400
    return party(ctx, viewer, here=here), 200


def action_party_signin(ctx: Context, return_to: str, lang: str) -> dict:
    """Start the Google sign-in for the Party: the leaderboard's, kept off the board when it comes back."""
    from . import leaderboard

    return leaderboard.action_leaderboard_signin(ctx, return_to, lang, purpose="party")


def action_party_signout(ctx: Context) -> dict:
    """Forget the Google sign-in; on the board, that is leaving it (the row is deleted)."""
    from ....sharing import leaderboard as lb

    lb.leave(ctx.cfg)
    return party(ctx, None, here=True)
