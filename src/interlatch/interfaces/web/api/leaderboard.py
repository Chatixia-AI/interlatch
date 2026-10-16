"""The usage leaderboard (sharing/leaderboard.py): this computer's person against everyone who signed in, and
signing in through the hosted page, for the board or for the Party (api/party.py), which shares the sign-in."""

from __future__ import annotations

import time

from ..context import Context


def leaderboard(ctx: Context, q: dict) -> dict:
    from ....sharing import leaderboard as lb

    by = q.get("by") if q.get("by") in (*lb.METRICS, "arena") else "hours"
    days = int(q["days"]) if q.get("days") in {str(d) for d in lb.WINDOWS} else 30
    me = lb.status(ctx.cfg)
    mine = lb.totals(ctx.conn, ctx.cfg)
    out = {"by": by, "days": days, "me": me, "rows": [], "error": None, "signing_in": _lb_pending(ctx) is not None,
           "mine": {"hours": mine[f"h{days}"], "sessions": mine[f"s{days}"], "tokens": mine[f"t{days}"]}}
    if by == "arena":  # this week's battles, every arena party against every other, fought here from their sheets
        from ....sharing import arena

        try:
            out["rows"] = arena.board(me.get("uid") if me.get("arena") else None)
            out["week"] = arena.week()
            if me.get("signed_in"):  # the team this computer fields, and the decks it can pick from
                out["me"] = {**me, **_fielding(ctx)}
        except lb.LeaderboardError as e:
            out["error"] = str(e)
        return out
    try:
        rows = sorted(lb.board(by, days), key=lambda r: r[by], reverse=True)
    except lb.LeaderboardError as e:
        out["error"] = str(e)
        return out
    out["rows"] = [{"rank": i + 1, **r, "me": bool(me.get("joined") and r["uid"] == me.get("uid"))}
                   for i, r in enumerate(rows)]
    return out


def _lb_pending(ctx: Context) -> dict | None:
    """The sign-in this dashboard started and waits to hear back from, while it may still come."""
    s = ctx.state.leaderboard_signin
    return s if s and s["expires"] > time.time() else None


def action_leaderboard_signin(ctx: Context, return_to: str, lang: str, purpose: str = "leaderboard") -> dict:
    """Start a sign-in: the hosted page's address, carrying this dashboard's address and a one-time state. `purpose`
    "party" signs in for the Party without joining the board (finish)."""
    from ....sharing import leaderboard as lb

    state = lb.new_state()
    ctx.state.leaderboard_signin = {"state": state, "expires": time.time() + lb.SIGNIN_TTL,
                                    "purpose": "party" if purpose == "party" else "leaderboard"}
    return {"url": lb.signin_url(return_to, state, "ja" if lang == "ja" else "en")}


def action_leaderboard_finish(ctx: Context, body: dict) -> dict:
    """The sign-in page came back: keep its session if it carries the state this dashboard made, then send. Signed in
    for the Party, the session is kept off the board (nothing is sent) unless this account is on it here already, and
    the answer says to go on to the Party (the hosted page always comes back to the Leaderboard page)."""
    from ....sharing import leaderboard as lb

    pending = _lb_pending(ctx)
    if not lb.same_state(pending and pending["state"], body.get("state")):
        raise lb.LeaderboardError("this sign-in wasn't started here, or took too long: start again")
    ctx.state.leaderboard_signin = None  # one use
    party = pending.get("purpose") == "party"
    lb.sign_in(ctx.cfg, body.get("token"), board=None if party else True)
    lb.send(ctx.cfg, ctx.conn, force=True)  # sends nothing off the board
    return {**lb.status(ctx.cfg), "next": "party"} if party else lb.status(ctx.cfg)


def action_leaderboard_cancel(ctx: Context) -> None:
    ctx.state.leaderboard_signin = None


def arena_battle(ctx: Context, q: dict) -> tuple[dict, int]:
    """This week's battle between this computer's person and a rival, both in the arena."""
    from ....core.i18n import tr
    from ....sharing import arena
    from ....sharing import leaderboard as lb

    me = lb.status(ctx.cfg)
    try:
        got = arena.fight(me["uid"], str(q.get("uid") or "")) if me.get("arena") else None
    except lb.LeaderboardError as e:  # Firestore out of reach: as the board says it
        return {"error": str(e)}, 502
    return (got, 200) if got else ({"error": tr("not found")}, 404)


def action_arena(ctx: Context, which: str) -> dict:
    """Enter the arena (on the board only: the sheet is sent now) or leave it (the sheet is deleted)."""
    from ....sharing import arena
    from ....sharing import leaderboard as lb

    if which == "join":
        arena.join(ctx.cfg, ctx.conn)
    else:
        arena.leave(ctx.cfg)
    return lb.status(ctx.cfg)


def _fielding(ctx: Context) -> dict:
    from ....sharing import arena
    from ....sharing import leaderboard as lb

    return arena.fielding(ctx.cfg, ctx.conn, lb.read_state(ctx.cfg))


def action_arena_deck(ctx: Context, body: dict) -> dict:
    """Field another of this computer's decks; sent now when in the arena."""
    from ....sharing import arena
    from ....sharing import leaderboard as lb

    arena.pick(ctx.cfg, ctx.conn, body.get("deck"))
    return {**lb.status(ctx.cfg), **_fielding(ctx)}


def action_leaderboard_join(ctx: Context) -> dict:
    """Signed in for the Party: join the board too, and send."""
    from ....sharing import leaderboard as lb

    lb.join(ctx.cfg, ctx.conn)
    return lb.status(ctx.cfg)


def action_leaderboard_send(ctx: Context) -> dict:
    from ....sharing import leaderboard as lb

    lb.send(ctx.cfg, ctx.conn, force=True)
    return lb.status(ctx.cfg)


def action_leaderboard_name(ctx: Context, body: dict) -> dict:
    from ....sharing import leaderboard as lb

    lb.rename(ctx.cfg, ctx.conn, body.get("name"))
    return lb.status(ctx.cfg)


def action_leaderboard_leave(ctx: Context) -> dict:
    from ....sharing import leaderboard as lb

    lb.leave(ctx.cfg)
    return lb.status(ctx.cfg)
