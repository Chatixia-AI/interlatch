"""Several hubs on one computer (docs/profiles.md), on the dashboard: Settings › Hubs lists them, how each one's sending
goes and what waits for you; a group's page connects the group to one. application.profiles does the work."""

from __future__ import annotations

from ....core.i18n import tr
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
        r.update(_sending(_hub_cfg(ctx, r["id"], reg)))
        r.pop("home", None)  # this computer's folders: not needed to show them
    gs = []
    for g in groups(ctx.conn):
        hub = hub_of_group(reg, g["id"])
        gs.append({"id": g["id"], "name": g["name"], "hub": hub.label if hub else None})
    return {"hubs": hubs, "groups": gs, "waiting": _waiting(waiting(ctx.conn), reg)}


class HubView:
    """The dashboard as one of this computer's hubs sees it: that hub's config (Personal's, or another's own folder,
    which reads Personal's archive), the same archive, jobs and state. What a device action writes back to its config
    stays with that hub; only Personal's replaces the dashboard's."""

    def __init__(self, ctx: Context, cfg):
        self._ctx = ctx
        self._cfg = cfg

    @property
    def cfg(self):
        return self._cfg

    @cfg.setter
    def cfg(self, cfg) -> None:
        self._cfg = cfg
        if cfg.owner == "personal":
            self._ctx.cfg = cfg

    def __getattr__(self, name):
        return getattr(self._ctx, name)


def for_hub(ctx: Context, key) -> tuple[object, str | None]:
    """(the dashboard as hub `key` sees it, None), or (None, why not). No key, or "personal": Personal's, as before."""
    from ....application.profiles import ProfileError, find_profile
    from ....core.config import load_config

    key = str(key or "").strip()
    if not key or key.casefold() == "personal":
        return ctx, None
    try:
        p = find_profile(ctx.cfg.home, key)
    except ProfileError as exc:
        return None, str(exc)
    if not (p.home / "config.toml").exists():
        return None, tr("{hub} hasn't joined a hub yet.", hub=p.label)
    return HubView(ctx, load_config(p.home, create=False)), None


def _hub_cfg(ctx: Context, hub_id: str, reg):
    """The config that sends to hub `hub_id`: Personal's, or that hub's own folder's (None: no such hub)."""
    from ....core.config import load_config

    if hub_id == "personal":
        return ctx.cfg
    p = reg.profiles.get(hub_id) if reg else None
    return load_config(p.home, create=False) if p and (p.home / "config.toml").exists() else None


def _sending(cfg) -> dict:
    """How sending to the hub goes, as Settings › Devices shows it for Personal's: the hub's name as it last said, the
    last share, a problem worth a warning, and the teammates' lessons it sent back."""
    from ....sharing.projects import last_folders
    from ....sharing.pull import last_team
    from ....sharing.push import last_push, send_problem

    if cfg is None or not cfg.is_spoke:
        return {"joined": False}
    pushed = last_push(cfg)
    return {"joined": True, "hub_name": (last_folders(cfg) or {}).get("hub"), "share": cfg.hub_share,
            "all_folders": cfg.hub_all_folders, "last_push": pushed, "push_problem": send_problem(pushed),
            "team_lessons": (last_team(cfg) or {}).get("lessons")}


def _waiting(rows: list[dict], reg=None) -> list[dict]:
    """What waits, one entry per choice to make: the projects a folder holds waiting (a group connected to no hub
    inside a connected one, a project picked out of one) together under that folder, with how many projects and
    sessions; any other waiting project on its own, with why. `let_go`: held because a group was disconnected or its
    hub removed (the registry's choices), not by the groups as they are."""
    from ....core.paths import is_abs_path

    out: dict[str, dict] = {}
    for r in rows:
        held = r.get("rule") == "held" and is_abs_path(r.get("why"))  # held by that folder (route(): its detail)
        key = r["why"] if held else r["folder"] or ""
        e = out.setdefault(key, {"folder": key, "sessions": 0, "projects": 0, "held": held,
                                 "let_go": held and reg is not None and key not in reg.held,
                                 "why": None if held else r.get("why")})
        e["sessions"] += r["sessions"]
        e["projects"] += 1
    return sorted(out.values(), key=lambda e: (-e["sessions"], e["folder"]))


def action_hubs(ctx: Context, verb: str, body: dict) -> tuple[dict, int]:
    """add (name, and optionally address and code to join it), remove, connect (group, hub; personal disconnects),
    choose (folder, hub or personal), push (hub: share with it now), leave (hub: stop sending to it). A change the hubs
    can't make says why, 409, and changes nothing."""
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
            return {"ok": True, "moved": sum(moved.sessions.values()), "taken": sum(moved.taken.values()),
                    "to": {moved.labels.get(k, k): v for k, v in moved.to.items()}}, 200
        if verb in ("push", "leave"):
            key = str(body.get("hub") or "")
            reg = ctx.cfg.profiles
            hub_id = "personal" if key.strip().casefold() == "personal" else ops.find_profile(ctx.cfg.home, key).id
            hcfg = _hub_cfg(ctx, hub_id, reg)
            if hcfg is None or not hcfg.is_spoke:
                return {"error": tr("{hub} hasn't joined a hub yet.", hub=key)}, 409
            if verb == "push":
                from ....sharing.push import push

                return {"ok": True, "started": ctx.jobs.start("push", lambda progress: push(hcfg, progress=progress)
                                                                 .summary())}, 200
            from ....sharing.client import leave_hub

            left = leave_hub(hcfg)
            if hub_id == "personal":
                ctx.cfg = left
            return {"ok": True, "hub": hcfg.hub_url}, 200
        if verb == "choose":
            moved = ops.choose(ctx.cfg, str(body.get("folder") or ""), str(body.get("hub") or ""), leave_on_hub=leave)
            return {"ok": True, "moved": sum(moved.sessions.values()), "taken": sum(moved.taken.values())}, 200
    except (ops.ProfileError, RegistryError, GroupError) as exc:
        return {"error": str(exc), "on_hub": getattr(exc, "can_leave", False)}, 409
    return {"error": "unknown action"}, 404
