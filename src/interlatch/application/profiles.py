"""Several hubs on one computer (docs/profiles.md), as `interlatch hubs`, `interlatch group` and the dashboard change
them: adding a hub, connecting your groups of projects to it (their history moves with them), settling waiting
folders, removing a hub. core.profiles has the rules (a hub here is a "profile"), projects.hubgroups works out what
each hub's groups hold, projects.owners records the owner of each session.

Every change runs against Personal's folder, under the registry's lock, in this order: check, look at what Personal's
hub already has of the sessions that move, write the registry, then record the new owners. A session never moves
while a copy of it may sit on another hub, unless you say it may stay there (`leave_on_hub`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import Config, interlatch_home, load_config, set_config_value
from ..core.paths import clean_abs_path
from ..core.profiles import (PERSONAL, WAITING, Profile, Registry, RegistryError, load_registry, registry_lock,
                             registry_path, route, save_registry)
from ..projects import owners
from ..storage.sqlite import connect as open_db


class ProfileError(RuntimeError):
    pass


@dataclass
class Moved:
    """What a change did, or (preview) would do."""
    sessions: dict[str, int] = field(default_factory=dict)  # {old owner: sessions} that move
    to: dict[str, int] = field(default_factory=dict)  # {new owner: sessions}: where they go
    owner: str = ""  # where they go
    labels: dict[str, str] = field(default_factory=dict)  # {owner: name}, as the change leaves them (a new profile's too)
    on_hub: int = 0  # of them, how many Personal's hub has
    hub: str = ""
    checked: bool = True  # False: Personal's hub couldn't be asked


def personal_cfg(cfg: Config) -> Config:
    """The config of Personal's folder, refusing to work from another hub's."""
    if is_profile_home(cfg.home):
        raise ProfileError("This is another hub's folder. Hubs and groups are changed from Personal: run the command "
                           "without --hub.")
    return cfg


def is_profile_home(home: Path) -> bool:
    """Whether `home` is a named profile's folder (<Personal>/profiles/<id>, listed in Personal's registry)."""
    home = Path(home).expanduser()
    parent = home.parent.parent if home.parent.name == "profiles" else None
    if parent is None or not registry_path(parent).exists():
        return False
    try:
        reg = load_registry(parent)
    except RegistryError:
        return True  # can't tell: treat it as one, so nothing changes from here
    return bool(reg) and any(Path(p.home) == home for p in reg.profiles.values())


def find_profile(home: Path, key: str) -> Profile:
    """The profile `key` names (id or label) in the registry next to Personal's `home`."""
    reg = load_registry(home)
    p = reg.find(key) if reg else None
    if p is None:
        names = ", ".join(sorted(x.label for x in reg.profiles.values())) if reg and reg.profiles else "none yet"
        raise ProfileError(f"No hub named {key!r} (hubs: {names}). `interlatch hubs` lists them.")
    return p


def _registry(cfg: Config) -> Registry:
    return load_registry(cfg.home) or Registry(path=registry_path(cfg.home))


def _folder(raw: str) -> str:
    # as typed, made absolute: agents record the folder they ran in as the process saw it, symlinks unresolved
    folder = clean_abs_path(os.path.abspath(os.path.expanduser(raw)))
    if not folder:
        raise ProfileError(f"{raw} is not a folder.")
    return folder


def _check_share_mode(cfg: Config) -> None:
    if cfg.is_spoke and cfg.hub_share == "everything":
        raise ProfileError(f"This computer sends its transcripts to the hub at {cfg.hub_url}, which would send every "
                           "hub's sessions there. With several hubs, each shares knowledge only: run `interlatch config "
                           "set hub.share knowledge` first.")


def _hub_has(cfg: Config, ids: set[str]) -> tuple[int, bool]:
    """How many of `ids` Personal's hub has, and whether it could be asked."""
    if not ids or not cfg.is_spoke:
        return 0, True
    from ..sharing.client import handshake
    from ..sharing.protocol import HubError

    try:
        have = handshake(cfg).get("knowledge") or {}
    except HubError:
        return 0, False
    return len(ids & set(have)), True


def _moving(conn, cfg: Config, before: Registry, after: Registry, folders: list[str]) -> tuple[dict[str, str], dict[str, int]]:
    """{session: new owner} for this computer's sessions in `folders` whose owner changes from `before` to `after`
    (a fresh route: the history moves), and {old owner: count}."""
    moves: dict[str, str] = {}
    old: dict[str, int] = {}
    known = owners.known_remotes(conn)
    for folder in folders:
        for s in owners.sessions_in(conn, folder, cfg):
            if s["id"] in moves:
                continue
            _top, remote = owners.repo_of(conn, s["folder"], s.get("remote"))
            now = s["owner"] or route(before, s["folder"], remote, known=known).owner
            new = route(after, s["folder"], remote, known=known).owner
            if new != now:
                moves[s["id"]] = new
                old[now] = old.get(now, 0) + 1
    return moves, old


def _apply(cfg: Config, before: Registry, after: Registry, folders: list[str], *, leave_on_hub: bool, dry_run: bool,
           owner: str) -> Moved:
    conn = open_db(cfg.db_path)
    try:
        owners.ensure_table(conn)
        moves, old = _moving(conn, cfg, before, after, folders)
        leaving = {sid for sid, new in moves.items() if new != PERSONAL}  # what leaves Personal's hub's reach
        on_hub, checked = _hub_has(cfg, leaving) if leaving else (0, True)
        to: dict[str, int] = {}
        for new in moves.values():
            to[new] = to.get(new, 0) + 1
        result = Moved(sessions=old, to=to, owner=owner, on_hub=on_hub, hub=cfg.hub_url if cfg.is_spoke else "",
                       checked=checked, labels={o: after.label(o) for o in {*old, *to, owner} if o})
        if dry_run:
            return result
        if leaving and not leave_on_hub:
            if not checked:
                raise ProfileError(f"Can't ask the hub at {cfg.hub_url} which of the {len(leaving)} sessions that move "
                                   "it already has, so nothing changed. Try again when it can be reached, or pass "
                                   "--leave-on-hub to move them anyway.")
            if on_hub:
                raise ProfileError(f"The hub at {cfg.hub_url} has {on_hub} of the {len(leaving)} sessions that move. "
                                   "Nothing changed. Take them back first (`interlatch hub remove-folder <folder>` "
                                   "for a folder added there, or ask its admin to purge them), or pass --leave-on-hub "
                                   "to move them and leave those copies there.")
        save_registry(after)
        for new in set(moves.values()):
            owners.move(conn, [sid for sid, o in moves.items() if o == new], new, folders[0] if folders else "")
        owners.assign_all(conn, cfg, after if after.profiles or after.choices or after.held else None)
        conn.commit()
        return result
    finally:
        conn.close()


def add_hub(cfg: Config, label: str, *, remotes: list[str] = ()) -> Profile:
    """Another hub for this computer: its own folder (<Personal>/profiles/<id>) with its token, computer identity and
    port, which reads Personal's archive. It owns nothing until a group is connected to it (connect), or by
    `remotes`, git remotes such as github.com/acme-corp/*. Join it with `interlatch --hub <name> hub join`."""
    personal_cfg(cfg)
    _check_share_mode(cfg)
    label = " ".join((label or "").split())
    if not label or label.casefold() in ("personal", "waiting"):
        raise ProfileError("Give the hub a name of its own, such as Acme.")
    with registry_lock(cfg.home):
        before = _registry(cfg)
        if any(p.label.casefold() == label.casefold() for p in before.profiles.values()):
            raise ProfileError(f"There is already a hub named {label}.")
        after = _copy(before)
        pid = after.new_id()
        p = Profile(id=pid, label=label, home=cfg.home / "profiles" / pid, remotes=[_remote(r) for r in remotes],
                    port=after.next_port())
        after.profiles[pid] = p
        _apply(cfg, before, after, _remote_folders(cfg, p), leave_on_hub=False, dry_run=False, owner=pid)
        _make_home(p)
    return p


def _group(conn, key) -> dict:
    """A group by its name (any case) or id."""
    from ..projects.groups import groups

    gs = groups(conn)
    hit = [g for g in gs if str(g["id"]) == str(key).strip() or g["name"].casefold() == str(key).strip().casefold()]
    if len(hit) != 1:
        names = ", ".join(g["name"] for g in gs) or "none yet"
        raise ProfileError(f"No group named {key!r} (groups: {names}). `interlatch group list` shows them.")
    return hit[0]


def _derived(cfg: Config, reg: Registry) -> Registry:
    from ..projects.hubgroups import derive

    conn = open_db(cfg.db_path)
    try:
        return derive(conn, reg)
    finally:
        conn.close()


def _check_overlap(after: Registry) -> None:
    """Two hubs can't own the same folder: a group connected to each with the same folder rule, or project."""
    seen: dict[str, str] = {}
    for p in after.profiles.values():
        for f in p.owns:
            if f in seen and seen[f] != p.id:
                raise ProfileError(f"{f} would belong to both {after.label(seen[f])} and {p.label}. Take it out of one "
                                   "of their groups first.")
            seen[f] = p.id


def connect(cfg: Config, group_key, hub_key: str, *, leave_on_hub: bool = False, dry_run: bool = False) -> Moved:
    """Connect a group to a hub: its folders and the projects picked into it go to that hub only, the sessions
    recorded there so far included. A group is connected to one hub at a time."""
    personal_cfg(cfg)
    _check_share_mode(cfg)
    conn = connect_db(cfg)
    try:
        g = _group(conn, group_key)
    finally:
        conn.close()
    with registry_lock(cfg.home):
        before = _registry(cfg)
        p = before.find(hub_key)
        if p is None:
            raise ProfileError(f"No hub named {hub_key!r}. `interlatch hubs` lists them; `interlatch hubs add <name>` "
                               "adds one.")
        after = _copy(before)
        for q in after.profiles.values():
            if g["id"] in q.groups:
                q.groups.remove(g["id"])
        after.profiles[p.id].groups.append(g["id"])
        after = _derived(cfg, after)
        for f in after.profiles[p.id].owns:
            after.choices.pop(f, None)  # connecting it settles a choice made there
        _check_overlap(after)
        return _apply(cfg, before, after, changed(before, after), leave_on_hub=leave_on_hub, dry_run=dry_run,
                      owner=p.id)


def disconnect(cfg: Config, group_key, *, dry_run: bool = False) -> tuple[str, Moved]:
    """Disconnect a group from its hub. What it held waits for you to choose (never straight to Personal, so a
    client's folder isn't shared with your Personal hub by mistake)."""
    from ..projects.hubgroups import hub_of_group

    personal_cfg(cfg)
    conn = connect_db(cfg)
    try:
        g = _group(conn, group_key)
    finally:
        conn.close()
    with registry_lock(cfg.home):
        before = _registry(cfg)
        p = hub_of_group(before, g["id"])
        if p is None:
            raise ProfileError(f"The group {g['name']} isn't connected to a hub.")
        mine = _derived(cfg, _only(before, p.id, g["id"])).profiles[p.id].owns  # what this group gave the hub
        after = _copy(before)
        after.profiles[p.id].groups.remove(g["id"])
        after = _derived(cfg, after)
        for f in mine:
            if not any(f in q.owns for q in after.profiles.values()):
                after.choices[f] = WAITING
        return p.label, _apply(cfg, before, after, changed(before, after), leave_on_hub=True, dry_run=dry_run,
                               owner=WAITING)


def _only(reg: Registry, pid: str, gid: int) -> Registry:
    out = _copy(reg)
    out.profiles = {pid: out.profiles[pid]}
    out.profiles[pid].groups = [gid]
    out.profiles[pid].manual = []
    return out


def sync(cfg: Config, *, leave_on_hub: bool = False, dry_run: bool = False) -> Moved:
    """After a group changed (its folders, the projects in it, or it was removed): what each hub owns follows, the
    sessions that change hands included. Raises ProfileError, changing nothing, when Personal's hub has some of the
    sessions that would leave it."""
    if cfg.owner != PERSONAL:
        return Moved()
    try:
        before = load_registry(cfg.home)
    except RegistryError as exc:
        raise ProfileError(str(exc)) from None
    if before is None or not before.profiles:
        return Moved()
    with registry_lock(cfg.home):
        before = _registry(cfg)
        after = _derived(cfg, before)
        _check_overlap(after)
        folders = changed(before, after)
        if not folders and after.held == before.held:
            return Moved()
        return _apply(cfg, before, after, folders, leave_on_hub=leave_on_hub, dry_run=dry_run, owner="")


def connect_db(cfg: Config):
    return open_db(cfg.db_path)


def changed(before: Registry, after: Registry) -> list[str]:
    from ..projects.hubgroups import changed as diff

    return diff(before, after)


def _remote(pattern: str) -> str:
    from ..projects.routing import normalize_remote

    pattern = pattern.strip()
    star = pattern.endswith("*")
    norm = normalize_remote(pattern.rstrip("*").rstrip("/")) if "/" in pattern or ":" in pattern else None
    return (norm + ("/*" if star else "")) if norm else pattern.lower()


def _remote_folders(cfg: Config, p: Profile) -> list[str]:
    """The folders of this computer's repositories whose remote matches one of `p`'s patterns."""
    if not p.remotes or not cfg.db_path.exists():
        return []
    from ..core.profiles import remote_matches

    conn = open_db(cfg.db_path, readonly=True)
    try:
        import json

        out = []
        for value, in conn.execute("SELECT value FROM kv WHERE key LIKE 'git:%'"):
            if not value:
                continue
            top, remote = json.loads(value)
            if any(remote_matches(remote, pat) for pat in p.remotes) and top not in out:
                out.append(top)
        return out
    finally:
        conn.close()


def _make_home(p: Profile) -> None:
    """The hub's own folder: config, port and a fresh computer identity (made on first load). It reads Personal's
    archive (core.config._as_profile); an older Interlatch run there records and sends nothing (exclude_projects =
    ["*"], no agent folders)."""
    p.home.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        p.home.chmod(0o700)
    cfg = load_config(p.home)  # writes the default config.toml
    for section, key, value in (("sources", "claude_dirs", "[]"), ("sources", "import_history", "false"),
                                ("sources", "import_memory", "false"), ("sources", "exclude_projects", '["*"]'),
                                ("hub", "share", '"knowledge"'), ("server", "port", str(p.port))):
        set_config_value(cfg, section, key, value)
    load_config(p.home).ensure_dirs()


def choose(cfg: Config, folder: str, key: str, *, leave_on_hub: bool = False, dry_run: bool = False) -> Moved:
    """Settle `folder` (a waiting one, or any other): its sessions belong to `key`, a hub's name or "personal"."""
    personal_cfg(cfg)
    folder = _folder(folder)
    with registry_lock(cfg.home):
        before = _registry(cfg)
        if key.strip().casefold() == PERSONAL:
            owner = PERSONAL
        else:
            p = before.find(key)
            if p is None:
                raise ProfileError(f"No hub named {key!r}: choose one from `interlatch hubs`, or personal.")
            owner = p.id
            _check_share_mode(cfg)
        after = _copy(before)
        after.choices[folder] = owner
        return _apply(cfg, before, after, [folder], leave_on_hub=leave_on_hub, dry_run=dry_run, owner=owner)


def remove_hub(cfg: Config, key: str, *, delete_data: bool = False) -> tuple[Profile, Moved]:
    """Remove a hub from this computer. Its groups are no longer connected, and what they held waits for you; its own
    folder (token, identity) stays unless `delete_data`. Leave the hub first (`interlatch --hub <name> hub leave`)."""
    import shutil

    personal_cfg(cfg)
    with registry_lock(cfg.home):
        before = _registry(cfg)
        p = before.find(key)
        if p is None:
            raise ProfileError(f"No hub named {key!r}. `interlatch hubs` lists them.")
        after = _copy(before)
        del after.profiles[p.id]
        held = list(p.owns)
        conn = open_db(cfg.db_path)
        try:  # the repositories it got by their remote, outside its folders: held as well
            owners.ensure_table(conn)
            held += [r[0] for r in conn.execute("SELECT DISTINCT folder FROM session_owner WHERE owner = ? "
                                                "AND folder IS NOT NULL", (p.id,)) if r[0] not in held]
        finally:
            conn.close()
        for f in held:
            after.choices[f] = WAITING
        for f, o in list(after.choices.items()):
            if o == p.id:
                after.choices[f] = WAITING
        moved = _apply(cfg, before, after, held, leave_on_hub=True, dry_run=False, owner=WAITING)
        if delete_data and p.home.is_dir() and p.home.parent == cfg.home / "profiles":
            shutil.rmtree(p.home)
    return p, moved


def _copy(reg: Registry) -> Registry:
    return Registry(path=reg.path, choices=dict(reg.choices), held=list(reg.held),
                    profiles={k: Profile(id=v.id, label=v.label, home=v.home, owns=list(v.owns), remotes=list(v.remotes),
                                         port=v.port, groups=list(v.groups), manual=list(v.manual))
                              for k, v in reg.profiles.items()})


def where(cfg: Config, folder: str):
    """(Route, registry) for a folder as a new session there would be routed."""
    folder = _folder(folder)
    reg = _registry(cfg)
    conn = open_db(cfg.db_path)
    try:
        _top, remote = owners.repo_of(conn, folder)
        known = owners.known_remotes(conn)
        conn.commit()
    finally:
        conn.close()
    return route(reg, folder, remote, known=known), reg, remote


def summary(cfg: Config) -> list[dict]:
    """Personal and each other hub: name, id, folder, port, its groups, the folders and patterns it owns, sessions it
    owns, the address it sends to; then what waits."""
    from ..projects.groups import groups

    reg = _registry(cfg)
    counts: dict[str, int] = {}
    names: dict[int, str] = {}
    if cfg.db_path.exists():
        conn = open_db(cfg.db_path, readonly=True)
        try:
            counts = owners.counts(conn)
            names = {g["id"]: g["name"] for g in groups(conn)}
        finally:
            conn.close()
    out = [{"id": PERSONAL, "label": "Personal", "home": str(cfg.home), "port": cfg.server_port, "owns": [],
            "remotes": [], "groups": [], "sessions": counts.get(PERSONAL), "hub": cfg.hub_url if cfg.is_spoke else ""}]
    for p in reg.profiles.values():
        pcfg = load_config(p.home, create=False) if (p.home / "config.toml").exists() else None
        out.append({"id": p.id, "label": p.label, "home": str(p.home), "port": p.port, "owns": p.owns,
                    "remotes": p.remotes, "groups": [names.get(g, str(g)) for g in p.groups],
                    "sessions": counts.get(p.id, 0), "hub": pcfg.hub_url if pcfg and pcfg.is_spoke else ""})
    out.append({"id": WAITING, "label": "Waiting", "sessions": counts.get(WAITING, 0), "held": list(reg.held),
                "choices": {f: reg.label(o) for f, o in reg.choices.items()}})
    return out


def profile_home(key: str, personal_home: Path | None = None) -> Path:
    """The folder of profile `key` (id or label), for the global --profile option. "personal" is Personal's."""
    home = Path(personal_home or interlatch_home())
    if is_profile_home(home):  # run from inside a profile (INTERLATCH_HOME): its Personal's registry
        home = home.parent.parent
    if key.strip().casefold() == PERSONAL:
        return home
    return find_profile(home, key).home


def joined(cfg: Config) -> list[Config]:
    """The named profiles that send to a hub of their own, each as its own config (core.config._as_profile): what the
    background job and `interlatch push` send for after Personal. None from a profile's own folder."""
    if cfg.owner != PERSONAL:
        return []
    try:
        reg = load_registry(cfg.home)
    except RegistryError:
        return []
    out = []
    for p in (reg.profiles.values() if reg else []):
        if (p.home / "config.toml").exists():
            pcfg = load_config(p.home, create=False)
            if pcfg.is_spoke:
                out.append(pcfg)
    return out


def change_groups(cfg: Config, change, *, leave_on_hub: bool = False) -> tuple[object, Moved]:
    """Run `change(conn)` (a projects.groups edit), then let each hub's folders follow (sync). If the hubs can't
    follow (Personal's hub has sessions that would leave it, two hubs would own one folder), the groups are put back
    as they were and ProfileError says why. Returns (what `change` returned, what moved)."""
    conn = open_db(cfg.db_path)
    try:
        kept = ([tuple(r) for r in conn.execute("SELECT id, name, folders_json, created_at, hub_project FROM project_groups")],
                [tuple(r) for r in conn.execute("SELECT project_path, group_id FROM project_group_picks")])
        result = change(conn)
        conn.commit()
        try:
            moved = sync(cfg, leave_on_hub=leave_on_hub)
        except ProfileError:
            conn.execute("DELETE FROM project_groups")
            conn.execute("DELETE FROM project_group_picks")
            conn.executemany("INSERT INTO project_groups(id, name, folders_json, created_at, hub_project) "
                             "VALUES (?, ?, ?, ?, ?)", kept[0])
            conn.executemany("INSERT INTO project_group_picks(project_path, group_id) VALUES (?, ?)", kept[1])
            conn.commit()
            raise
        return result, moved
    finally:
        conn.close()
