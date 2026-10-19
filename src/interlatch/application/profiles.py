"""Named profiles, as `interlatch profile` and (later) the dashboard change them: making one, moving folders in and
out with their history, settling waiting folders, removing one (core.profiles has the rules, projects.owners the
owner of each session).

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
from ..storage.sqlite import connect


class ProfileError(RuntimeError):
    pass


@dataclass
class Moved:
    """What a change did, or (preview) would do."""
    sessions: dict[str, int] = field(default_factory=dict)  # {old owner: sessions} that move
    owner: str = ""  # where they go
    on_hub: int = 0  # of them, how many Personal's hub has
    hub: str = ""
    checked: bool = True  # False: Personal's hub couldn't be asked


def personal_cfg(cfg: Config) -> Config:
    """The config of Personal's folder, refusing to work from a named profile's."""
    if is_profile_home(cfg.home):
        raise ProfileError("This is a named profile's folder. Profiles are changed from Personal: run the command "
                           "without --profile.")
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
        raise ProfileError(f"No profile named {key!r} (profiles: {names}). `interlatch profile list` shows them.")
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
                           "profile's sessions there. Named profiles share knowledge only: run `interlatch config set "
                           "hub.share knowledge` first.")


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
    conn = connect(cfg.db_path)
    try:
        owners.ensure_table(conn)
        moves, old = _moving(conn, cfg, before, after, folders)
        leaving = {sid for sid, new in moves.items() if new != PERSONAL}  # what leaves Personal's hub's reach
        on_hub, checked = _hub_has(cfg, leaving) if leaving else (0, True)
        result = Moved(sessions=old, owner=owner, on_hub=on_hub, hub=cfg.hub_url if cfg.is_spoke else "", checked=checked)
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
        owners.assign_all(conn, cfg, after if after.profiles or after.choices else None)
        conn.commit()
        return result
    finally:
        conn.close()


def create(cfg: Config, label: str, *, owns: list[str] = (), remotes: list[str] = (), leave_on_hub: bool = False,
           dry_run: bool = False) -> tuple[Profile, Moved]:
    """A new named profile with its own folder (<Personal>/profiles/<id>) and port, owning `owns` and the
    repositories matching `remotes`. The sessions already recorded there move to it."""
    personal_cfg(cfg)
    _check_share_mode(cfg)
    label = " ".join((label or "").split())
    if not label or label.casefold() in ("personal", "waiting"):
        raise ProfileError("Give the profile a name of its own, such as Acme.")
    folders = [_folder(f) for f in owns]
    with registry_lock(cfg.home):
        before = _registry(cfg)
        if any(p.label.casefold() == label.casefold() for p in before.profiles.values()):
            raise ProfileError(f"There is already a profile named {label}.")
        for f in folders:
            if (o := before.owner_of_folder(f)) is not None:
                raise ProfileError(f"{f} already belongs to {before.label(o)}.")
        after = Registry(path=before.path, profiles=dict(before.profiles), choices=dict(before.choices))
        pid = after.new_id()
        p = Profile(id=pid, label=label, home=cfg.home / "profiles" / pid, owns=folders,
                    remotes=[_remote(r) for r in remotes], port=after.next_port())
        after.profiles[pid] = p
        for f in folders:
            after.choices.pop(f, None)  # owning it settles a choice made there
        moved = _apply(cfg, before, after, folders + _remote_folders(cfg, p), leave_on_hub=leave_on_hub,
                       dry_run=dry_run, owner=pid)
        if not dry_run:
            _make_home(p)
    return p, moved


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

    conn = connect(cfg.db_path, readonly=True)
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
    """The profile's own folder: config, port and a fresh computer identity (made on first load). It reads no
    agent's folder itself and, until it copies its sessions from Personal (docs/profiles.md), records nothing: an
    older Interlatch run there records and sends nothing either (exclude_projects = ["*"])."""
    p.home.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        p.home.chmod(0o700)
    cfg = load_config(p.home)  # writes the default config.toml
    for section, key, value in (("sources", "claude_dirs", "[]"), ("sources", "import_history", "false"),
                                ("sources", "import_memory", "false"), ("sources", "exclude_projects", '["*"]'),
                                ("hub", "share", '"knowledge"'), ("server", "port", str(p.port))):
        set_config_value(cfg, section, key, value)
    load_config(p.home).ensure_dirs()


def own(cfg: Config, folder: str, key: str, *, leave_on_hub: bool = False, dry_run: bool = False) -> Moved:
    """Move `folder` into profile `key`, its sessions recorded so far included."""
    personal_cfg(cfg)
    _check_share_mode(cfg)
    folder = _folder(folder)
    with registry_lock(cfg.home):
        before = _registry(cfg)
        p = before.find(key)
        if p is None:
            raise ProfileError(f"No profile named {key!r}. `interlatch profile list` shows them.")
        if (o := before.owner_of_folder(folder)) is not None:
            if o == p.id:
                raise ProfileError(f"{folder} already belongs to {p.label}.")
            raise ProfileError(f"{folder} belongs to {before.label(o)}. `interlatch profile disown {folder}` first.")
        after = _copy(before)
        after.profiles[p.id].owns.append(folder)
        after.choices.pop(folder, None)
        return _apply(cfg, before, after, [folder], leave_on_hub=leave_on_hub, dry_run=dry_run, owner=p.id)


def disown(cfg: Config, folder: str, *, dry_run: bool = False) -> tuple[str, Moved]:
    """Take `folder` from the profile that owns it. Its sessions wait for you to choose (never straight to Personal,
    so a company folder isn't shared with your Personal hub by mistake)."""
    personal_cfg(cfg)
    folder = _folder(folder)
    with registry_lock(cfg.home):
        before = _registry(cfg)
        o = before.owner_of_folder(folder)
        if o is None:
            raise ProfileError(f"No profile owns {folder} itself. `interlatch profile where {folder}` says what "
                               "decides it.")
        after = _copy(before)
        after.profiles[o].owns.remove(folder)
        after.choices[folder] = WAITING
        return before.label(o), _apply(cfg, before, after, [folder], leave_on_hub=True, dry_run=dry_run, owner=WAITING)


def choose(cfg: Config, folder: str, key: str, *, leave_on_hub: bool = False, dry_run: bool = False) -> Moved:
    """Settle `folder` (a waiting one, or any other): its sessions belong to `key`, a profile or "personal"."""
    personal_cfg(cfg)
    folder = _folder(folder)
    with registry_lock(cfg.home):
        before = _registry(cfg)
        if key.strip().casefold() == PERSONAL:
            owner = PERSONAL
        else:
            p = before.find(key)
            if p is None:
                raise ProfileError(f"No profile named {key!r}: choose one from `interlatch profile list`, or personal.")
            owner = p.id
            _check_share_mode(cfg)
        after = _copy(before)
        after.choices[folder] = owner
        return _apply(cfg, before, after, [folder], leave_on_hub=leave_on_hub, dry_run=dry_run, owner=owner)


def remove(cfg: Config, key: str, *, delete_data: bool = False) -> tuple[Profile, Moved]:
    """Remove a profile from the registry. Its folders and sessions wait for you; its own folder stays unless
    `delete_data`."""
    import shutil

    personal_cfg(cfg)
    with registry_lock(cfg.home):
        before = _registry(cfg)
        p = before.find(key)
        if p is None:
            raise ProfileError(f"No profile named {key!r}. `interlatch profile list` shows them.")
        after = _copy(before)
        del after.profiles[p.id]
        held = list(p.owns)
        conn = connect(cfg.db_path)
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
    return Registry(path=reg.path, choices=dict(reg.choices),
                    profiles={k: Profile(id=v.id, label=v.label, home=v.home, owns=list(v.owns), remotes=list(v.remotes),
                                         port=v.port) for k, v in reg.profiles.items()})


def where(cfg: Config, folder: str):
    """(Route, registry) for a folder as a new session there would be routed."""
    folder = _folder(folder)
    reg = _registry(cfg)
    conn = connect(cfg.db_path)
    try:
        _top, remote = owners.repo_of(conn, folder)
        known = owners.known_remotes(conn)
        conn.commit()
    finally:
        conn.close()
    return route(reg, folder, remote, known=known), reg, remote


def summary(cfg: Config) -> list[dict]:
    """Personal and each named profile: label, id, folder, port, owned folders and patterns, sessions it owns."""
    reg = _registry(cfg)
    counts: dict[str, int] = {}
    if cfg.db_path.exists():
        conn = connect(cfg.db_path, readonly=True)
        try:
            counts = owners.counts(conn)
        finally:
            conn.close()
    out = [{"id": PERSONAL, "label": "Personal", "home": str(cfg.home), "port": cfg.server_port, "owns": [],
            "remotes": [], "sessions": counts.get(PERSONAL), "hub": cfg.hub_url if cfg.is_spoke else ""}]
    for p in reg.profiles.values():
        pcfg = load_config(p.home, create=False) if (p.home / "config.toml").exists() else None
        out.append({"id": p.id, "label": p.label, "home": str(p.home), "port": p.port, "owns": p.owns,
                    "remotes": p.remotes, "sessions": counts.get(p.id, 0),
                    "hub": pcfg.hub_url if pcfg and pcfg.is_spoke else ""})
    out.append({"id": WAITING, "label": "Waiting", "sessions": counts.get(WAITING, 0),
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
