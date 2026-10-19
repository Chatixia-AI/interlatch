"""Several hubs on one computer (docs/profiles.md): which hub each session belongs to.

Personal (~/.interlatch) records and analyzes every session from every agent, always, and may send to a hub of its
own. Each other hub is a "profile" here: a folder of its own (<Personal>/profiles/<id>: its [hub] link, token,
computer identity, sharing state) that reads Personal's archive (core.config._as_profile). You connect your groups of
projects (projects.groups) to it, and the folders they hold become what it owns (projects.hubgroups.derive). The
registry, `profiles.toml` in Personal's folder, lists them; route() decides who owns a session, and each session
reaches at most one hub.

    held = ["/Users/me/work/acme/notes"]      # held waiting by the groups: a project picked out of a connected group
    [choices]                                 # your answers: they beat every other rule but a narrower owned folder
    "/Users/me/work/acme/oss" = "personal"
    "/Users/me/clients/old" = "waiting"       # held until you choose (a disconnected group's, a removed hub's)

    [profiles.p7k2m9qa]
    label = "Acme"
    home = "/Users/me/.interlatch/profiles/p7k2m9qa"
    owns = ["/Users/me/work/acme"]            # its groups' folders and picked projects
    remotes = ["github.com/acme-corp/*"]
    port = 11525
    groups = [3]

Owners are "personal", a profile's id, or "waiting": a session whose owner is unclear (two hubs' rules match it, or a
group let it go) is recorded by Personal but shared with no hub and shown to no agent until you choose.
"""

from __future__ import annotations

import fnmatch
import json
import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .paths import clean_abs_path, normalize_project_path, path_kind

PERSONAL = "personal"
WAITING = "waiting"
FILE = "profiles.toml"
FIRST_PORT = 11525  # Personal's dashboard is on 11524
_ID_CHARS = "abcdefghijkmnpqrstuvwxyz23456789"  # no 0/o, 1/l


@dataclass
class Profile:
    id: str
    label: str
    home: Path
    owns: list[str] = field(default_factory=list)
    remotes: list[str] = field(default_factory=list)
    port: int = FIRST_PORT
    groups: list[int] = field(default_factory=list)  # the groups connected to this hub (projects.groups); owns follows
    manual: list[str] = field(default_factory=list)  # folders given by hand (`interlatch profile create --owns`, 0.28.1)


@dataclass
class Registry:
    path: Path  # Personal's profiles.toml
    profiles: dict[str, Profile] = field(default_factory=dict)
    choices: dict[str, str] = field(default_factory=dict)  # {folder: owner}, owner WAITING holds it
    held: list[str] = field(default_factory=list)  # folders the groups hold waiting (projects.hubgroups.derive)

    @property
    def personal_home(self) -> Path:
        return self.path.parent

    def find(self, key: str) -> Profile | None:
        """A profile by id or label (any case), or None."""
        key = (key or "").strip()
        if key in self.profiles:
            return self.profiles[key]
        hits = [p for p in self.profiles.values() if p.label.casefold() == key.casefold()]
        return hits[0] if len(hits) == 1 else None

    def label(self, owner: str) -> str:
        if owner == PERSONAL:
            return "Personal"
        if owner == WAITING:
            return "Waiting"
        p = self.profiles.get(owner)
        return p.label if p else owner

    def folder_rules(self) -> list[tuple[str, str, bool]]:
        """(folder, owner, chosen) for every owned folder and every choice."""
        out = [(f, o, True) for f, o in self.choices.items()]
        out += [(f, WAITING, True) for f in self.held if f not in self.choices]  # your own choice there wins
        for p in self.profiles.values():
            out += [(f, p.id, False) for f in p.owns]
        return out

    def owner_of_folder(self, folder: str) -> str | None:
        """Which profile has `folder` itself as an owned folder (not just one around it), or None."""
        folder = clean_abs_path(folder)
        return next((o for f, o, chosen in self.folder_rules() if f == folder and not chosen), None)

    def next_port(self) -> int:
        used = {p.port for p in self.profiles.values()}
        port = FIRST_PORT
        while port in used:
            port += 1
        return port

    def new_id(self) -> str:
        while True:
            pid = "p" + "".join(secrets.choice(_ID_CHARS) for _ in range(7))
            if pid not in self.profiles:
                return pid


@dataclass(frozen=True)
class Route:
    owner: str  # PERSONAL, a profile id, or WAITING
    rule: str  # "recorded", "folder", "remote", "conflict", "default"
    detail: str = ""  # the folder or pattern that decided, or why it waits


def under(path: str | None, folder: str) -> bool:
    """`path` is `folder` or below it (projects.routing.under, which core can't import)."""
    if not path or not folder:
        return False
    path, folder = normalize_project_path(path), normalize_project_path(folder)
    if path_kind(path) != path_kind(folder):
        return False
    return path == folder or path.startswith(folder if folder.endswith("/") else folder + "/")


def remote_matches(remote: str | None, pattern: str) -> bool:
    """A normalized git remote (github.com/acme-corp/app) against a pattern (github.com/acme-corp/*)."""
    return bool(remote) and fnmatch.fnmatchcase(remote.lower(), pattern.lower())


def route(reg: Registry | None, folder: str | None, remote: str | None = None, *, recorded: str | None = None,
          known: dict[str, set[str]] | None = None) -> Route:
    """Who owns a session that ran in `folder` (its git remote `remote`). The first rule that matches wins:

    1. recorded: a session keeps the owner it was given (not "waiting"); only moving a folder changes it.
    2. folder: the longest owned folder or choice that holds it (a choice wins a tie). A choice is your answer and
       settles it, "waiting" holding it until you choose; an owned folder waits too when another profile's repo rule
       claims the repository.
    3. remote: a profile's remote pattern, or a repository already seen in a profile's folders (`known`:
       {remote: owners}); two profiles claiming it wait for you.
    4. default: Personal, chats with no folder included.
    """
    if not reg or not (reg.profiles or reg.choices or reg.held):  # held folders outlive the last profile
        return Route(PERSONAL, "default")
    if recorded and recorded != WAITING and (recorded == PERSONAL or recorded in reg.profiles):
        return Route(recorded, "recorded")
    claims = _remote_claims(reg, remote, known)
    path = clean_abs_path(folder) if folder else ""
    best = max(((f, o, c) for f, o, c in reg.folder_rules() if under(path, f)), key=lambda r: (len(r[0]), r[2]),
               default=None)
    if best:
        f, owner, chosen = best
        if chosen:
            if owner not in (PERSONAL, WAITING) and owner not in reg.profiles:
                owner = WAITING  # chosen for a profile since removed: hold it, never fall back to Personal
            return Route(owner, "held" if owner == WAITING else "chosen", f)
        others = claims - {owner}
        if others:
            return Route(WAITING, "conflict", f"{reg.label(owner)}'s folder {f}, and {_names(reg, others)}'s repositories")
        return Route(owner, "folder", f)
    if len(claims) > 1:
        return Route(WAITING, "conflict", f"{_names(reg, claims)} each claim its repository ({remote})")
    if claims:
        owner = next(iter(claims))
        pattern = next((p for p in reg.profiles[owner].remotes if remote_matches(remote, p)), "")
        return Route(owner, "remote", pattern or remote or "")
    return Route(PERSONAL, "default")


def _remote_claims(reg: Registry, remote: str | None, known: dict[str, set[str]] | None) -> set[str]:
    if not remote:
        return set()
    out = {p.id for p in reg.profiles.values() if any(remote_matches(remote, pat) for pat in p.remotes)}
    out |= {o for o in (known or {}).get(remote.lower(), set()) if o in reg.profiles}
    return out


def _names(reg: Registry, owners: set[str]) -> str:
    return " and ".join(sorted(reg.label(o) for o in owners))


# ------------------------------------------------------------------ the registry file
_cache: dict[str, tuple[tuple[int, int] | None, Registry | None]] = {}


def registry_path(personal_home: Path) -> Path:
    return Path(personal_home).expanduser() / FILE


def load_registry(personal_home: Path) -> Registry | None:
    """The registry in `personal_home`, or None when there is none (no named profile was ever made). Read again only
    when the file changed."""
    path = registry_path(personal_home)
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        _cache.pop(str(path), None)
        return None
    hit = _cache.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1]
    reg = _parse(path)
    _cache[str(path)] = (stamp, reg)
    return reg


def _parse(path: Path) -> Registry:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        # a registry that can't be read must never share as if there were no profiles: every folder waits
        raise RegistryError(f"{path} can't be read ({exc}). Fix it or remove it.") from exc
    reg = Registry(path=path, held=_folders(data.get("held")))
    for f, owner in (data.get("choices") or {}).items():
        folder = _folders([f])
        if folder:
            reg.choices[folder[0]] = str(owner)
    for pid, raw in (data.get("profiles") or {}).items():
        if not isinstance(raw, dict):
            continue
        reg.profiles[pid] = Profile(id=pid, label=str(raw.get("label") or pid),
                                    home=Path(str(raw.get("home") or path.parent / "profiles" / pid)).expanduser(),
                                    owns=_folders(raw.get("owns")),
                                    remotes=[str(r).strip().lower() for r in raw.get("remotes") or [] if str(r).strip()],
                                    port=int(raw.get("port") or FIRST_PORT),
                                    groups=[int(g) for g in raw.get("groups") or [] if isinstance(g, int)],
                                    # 0.28.1 wrote no groups: the folders it owned were given by hand, and stay
                                    manual=_folders(raw.get("manual") if "groups" in raw else raw.get("owns")))
    return reg


def _folders(raw) -> list[str]:
    out = []
    for f in raw or []:
        p = clean_abs_path(os.path.expanduser(str(f)))
        if p and p not in out:
            out.append(p)
    return out


class RegistryError(RuntimeError):
    pass


def save_registry(reg: Registry) -> None:
    """Write the registry whole, readable by this user only. Callers hold registry_lock()."""
    lines = ["# Interlatch's hubs besides Personal's, and which folders each one's groups hold (docs/profiles.md).",
             "# Written by `interlatch group ...` and `interlatch hubs ...`; owns and held follow the groups.", "",
             f"held = {_list(reg.held)}", "",
             "[choices]", *(f"{json.dumps(f)} = {json.dumps(o)}" for f, o in sorted(reg.choices.items()))]
    for p in reg.profiles.values():
        lines += ["", f"[profiles.{p.id}]", f"label = {json.dumps(p.label)}", f"home = {json.dumps(str(p.home))}",
                  f"owns = {_list(p.owns)}", f"remotes = {_list(p.remotes)}", f"port = {int(p.port)}",
                  f"groups = [{', '.join(str(int(g)) for g in p.groups)}]", f"manual = {_list(p.manual)}"]
    reg.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = reg.path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    if os.name == "posix":
        tmp.chmod(0o600)
    tmp.replace(reg.path)
    _cache.pop(str(reg.path), None)


def _list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


def registry_lock(personal_home: Path):
    from .util import file_lock

    return file_lock(Path(personal_home).expanduser() / "locks" / "profiles.lock", timeout=30)
