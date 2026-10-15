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
    data = pt.compute(ctx.conn, _here_id(ctx), viewer)
    # who's hiring for the classes it shows: read from Firebase only now, signed in (the numbers are public)
    return {"signed_in": True, **gate, "who": me, **data, "market": pt.market_block(pt.payload_tracks(data))}


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
