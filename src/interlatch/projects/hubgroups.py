"""Groups connected to a hub (docs/profiles.md): what each hub owns, worked out from your groups of projects.

A group (projects.groups) is a name, folder rules and projects picked by hand. Connected to a hub (core.profiles:
Profile.groups), its folders and the projects picked into it are what that hub owns. Two kinds of folders inside a
connected group are held waiting instead (Registry.held), so they never fall back to Personal's hub on their own:

- a project you picked out of the connected group (into no group, or into a group connected to no hub);
- the folder rule of a group connected to no hub, inside a connected group's folder.

A group connected to another hub inside one is that hub's: the longest rule wins, as route() says.
"""

from __future__ import annotations

import sqlite3

from ..core.paths import clean_abs_path
from ..core.profiles import Profile, Registry, under


def derive(conn: sqlite3.Connection, reg: Registry) -> Registry:
    """A copy of `reg` whose hubs own what their connected groups hold now, with the folders those groups hold
    waiting. Groups that no longer exist are dropped from their hub."""
    from .groups import _picks, groups

    gs = {g["id"]: g for g in groups(conn)}
    hub_of = {gid: p.id for p in reg.profiles.values() for gid in p.groups if gid in gs}
    picks = _picks(conn)
    out = Registry(path=reg.path, choices=dict(reg.choices))
    for p in reg.profiles.values():
        mine = [gid for gid in p.groups if gid in gs]
        owns: list[str] = list(p.manual)  # given by hand before groups (0.28.1)
        for gid in mine:
            owns += [f for f in gs[gid]["folders"]]
            owns += [path for path, pick in picks.items() if pick == gid]
        out.profiles[p.id] = Profile(id=p.id, label=p.label, home=p.home, owns=_clean(owns), remotes=list(p.remotes),
                                     port=p.port, groups=mine, manual=list(p.manual))
    connected = [f for p in out.profiles.values() for f in p.owns]
    held: list[str] = []
    for path, pick in picks.items():  # picked out of a connected group's folders: into none, or an unconnected one
        if (pick is None or pick not in hub_of) and any(under(path, f) for f in connected):
            held.append(path)
    for gid, g in gs.items():  # an unconnected group's folder inside a connected one
        if gid not in hub_of:
            held += [f for f in g["folders"] if any(under(f, c) and f != c for c in connected)]
    out.held = _clean(held)
    return out


def _clean(folders: list[str]) -> list[str]:
    out: list[str] = []
    for f in folders:
        c = clean_abs_path(f)
        if c and c not in out:
            out.append(c)
    return sorted(out)


def changed(before: Registry, after: Registry) -> list[str]:
    """The folders whose owner may differ between two registries: what to look at for sessions that move."""
    def rules(reg: Registry) -> set[tuple[str, str]]:
        return {(f, o) for f, o, _ in reg.folder_rules()}

    return sorted({f for f, _ in rules(before) ^ rules(after)})


def hub_of_group(reg: Registry | None, group_id: int) -> Profile | None:
    """The hub a group is connected to, or None."""
    return next((p for p in reg.profiles.values() if group_id in p.groups), None) if reg else None
