"""Several hubs on one computer (docs/profiles.md), on the dashboard: Settings › Devices › Hubs lists them and what
waits for you; a group's page connects the group to one. application.profiles does the work."""

from __future__ import annotations

from ..context import Context


def hubs(ctx: Context) -> dict:
    """Personal's hub and each other one (name, where it sends, its groups, sessions), the groups and the hub each goes
    to, and the folders that wait for you to choose."""
    from ....application.profiles import summary
    from ....core.profiles import RegistryError
    from ....projects.groups import groups
    from ....projects.hubgroups import hub_of_group
    from ....projects.owners import waiting

    try:
        rows = summary(ctx.cfg)
        reg = ctx.cfg.profiles
    except RegistryError as exc:
        return {"error": str(exc), "hubs": [], "groups": [], "waiting": []}
    hubs = [r for r in rows if r["id"] != "waiting"]
    for r in hubs:
        r.pop("home", None)  # this computer's folders: not needed to show them
    gs = []
    for g in groups(ctx.conn):
        hub = hub_of_group(reg, g["id"])
        gs.append({"id": g["id"], "name": g["name"], "hub": hub.label if hub else None})
    return {"hubs": hubs, "groups": gs, "waiting": _waiting(waiting(ctx.conn))}


def _waiting(rows: list[dict]) -> list[dict]:
    """What waits, one entry per choice to make: the projects a folder holds waiting (a group connected to no hub
    inside a connected one, a project picked out of one) together under that folder, with how many projects and
    sessions; any other waiting project on its own, with why."""
    from ....core.paths import is_abs_path

    out: dict[str, dict] = {}
    for r in rows:
        held = r.get("rule") == "held" and is_abs_path(r.get("why"))  # held by that folder (route(): its detail)
        key = r["why"] if held else r["folder"] or ""
        e = out.setdefault(key, {"folder": key, "sessions": 0, "projects": 0, "held": held,
                                 "why": None if held else r.get("why")})
        e["sessions"] += r["sessions"]
        e["projects"] += 1
    return sorted(out.values(), key=lambda e: (-e["sessions"], e["folder"]))


def action_hubs(ctx: Context, verb: str, body: dict) -> tuple[dict, int]:
    """add (name, and optionally address and code to join it), remove, connect (group, hub; personal disconnects),
    choose (folder, hub or personal). A change the hubs can't make says why, 409, and changes nothing."""
    from ....application import profiles as ops
    from ....core.profiles import RegistryError
    from ....projects.groups import GroupError

    leave = body.get("leave_on_hub") is True
    try:
        if verb == "add":
            p = ops.add_hub(ctx.cfg, str(body.get("name") or ""))
            out: dict = {"ok": True, "hub": p.label}
            if body.get("address"):
                try:
                    out["joined"] = ops.join(ctx.cfg, p.label, str(body.get("address") or ""), str(body.get("code") or ""),
                                             all_folders=body.get("all_folders") is True)
                except ops.ProfileError as exc:  # added, not joined: say so, keep it
                    out["join_error"] = str(exc)
            return out, 200
        if verb == "join":
            return {"ok": True, **ops.join(ctx.cfg, str(body.get("hub") or ""), str(body.get("address") or ""),
                                           str(body.get("code") or ""), all_folders=body.get("all_folders") is True)}, 200
        if verb == "remove":
            p, _ = ops.remove_hub(ctx.cfg, str(body.get("hub") or ""))
            return {"ok": True, "hub": p.label}, 200
        if verb == "connect":
            hub = str(body.get("hub") or "")
            if hub.strip().casefold() == "personal":
                from ....projects.hubgroups import hub_of_group

                gid = body.get("group")
                if not hub_of_group(ctx.cfg.profiles, int(gid) if str(gid).isdigit() else -1):
                    return {"ok": True, "moved": 0}, 200  # it already goes to Personal's
                _, moved = ops.disconnect(ctx.cfg, gid)
            else:
                moved = ops.connect(ctx.cfg, body.get("group"), hub, leave_on_hub=leave)
            return {"ok": True, "moved": sum(moved.sessions.values()), "to": {moved.labels.get(k, k): v
                                                                             for k, v in moved.to.items()}}, 200
        if verb == "choose":
            moved = ops.choose(ctx.cfg, str(body.get("folder") or ""), str(body.get("hub") or ""), leave_on_hub=leave)
            return {"ok": True, "moved": sum(moved.sessions.values())}, 200
    except (ops.ProfileError, RegistryError, GroupError) as exc:
        return {"error": str(exc), "on_hub": "has " in str(exc) and "sessions that move" in str(exc)}, 409
    return {"error": "unknown action"}, 404
