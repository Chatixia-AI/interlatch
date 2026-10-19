"""A member's computer and the hub as a whole: which hub an address names, leaving one (its token first, then what
this computer kept for it), and bringing that back when it joins the same hub again.

What a computer keeps for its hub (the folders it added, the projects it left, the groups it shares, its teammates'
lessons and its notes of the last push and hello) belongs to that hub. Leaving puts the settings aside in
<home>/hub-left/<id of the hub's address>.json and clears all of it, so a different hub joined later never gets the
first one's folders or groups; joining the same hub again offers to bring them back."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from ..core.config import Config
from ..core.util import utcnow_iso
from .identity import read_token, token_path, write_token
from .protocol import HubError

STASH_DIR = "hub-left"  # in Interlatch's folder: what this computer kept for each hub it left
JOINED = "hub-joined.json"  # the hub this computer joined and its token's sha256: that token is no hub's own
EARLIER = "earlier"  # the stash of what a leave from before this one left behind, its hub unknown
_DEFAULT_PORTS = {"http": 80, "https": 443}


def hub_origin(url: str) -> str:
    """The hub an address names, spelled one way: scheme, lowercase IDNA host, and the port unless it is the scheme's
    default. An address without a scheme is https, as `interlatch hub join` takes it. Raises HubError for anything
    that isn't an http(s) address."""
    raw = (url or "").strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").rstrip(".")
    if scheme not in _DEFAULT_PORTS or not host:
        raise HubError(f"not a hub address: {url}")
    try:
        port = parsed.port
    except ValueError:
        raise HubError(f"not a hub address: {url}") from None
    if ":" in host:  # IPv6
        host = f"[{host.lower()}]"
    else:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise HubError(f"not a hub address: {url}") from None
        host = host.lower()
    return f"{scheme}://{host}" + (f":{port}" if port and port != _DEFAULT_PORTS[scheme] else "")


def same_hub(a: str, b: str) -> bool:
    """Whether two addresses name the same hub (hub_origin); an address that names none is no hub's."""
    try:
        return hub_origin(a) == hub_origin(b)
    except HubError:
        return False


def other_hub(cfg: Config, url: str) -> str | None:
    """The hub this computer sends to, when that is not the one at `url`: joining `url` would put it in place of that
    one, so the caller refuses (leave it first). None when it sends to no hub, or to that one."""
    return cfg.hub_url if cfg.is_spoke and not same_hub(cfg.hub_url, url) else None


def hub_of_another_profile(cfg: Config, url: str) -> str | None:
    """The profile (Personal or a named one, core.profiles) other than this one that already sends to the hub at `url`,
    by its label; None when none does. A hub gets one profile's sessions: joining it from a second one is refused
    before anything goes out."""
    from ..core.config import load_config
    from ..core.profiles import load_registry

    home = cfg.home.expanduser()
    personal = home.parent.parent if home.parent.name == "profiles" else home
    try:
        reg = load_registry(personal)
    except Exception:  # an unreadable registry: refuse rather than guess (RegistryError)
        return "another profile (profiles.toml can't be read)"
    if not reg or not reg.profiles:
        return None
    for label, other in [("Personal", personal), *((p.label, p.home) for p in reg.profiles.values())]:
        if other.expanduser() == home or not (other / "config.toml").exists():
            continue
        ocfg = load_config(other, create=False)
        if ocfg.is_spoke and same_hub(ocfg.hub_url, url):
            return label
    return None


def stash_path(cfg: Config, url: str) -> Path:
    """Where leaving the hub at `url` puts what this computer kept for it: no address in the name (no colons, for
    Windows), its sha256 instead."""
    try:
        name = hashlib.sha256(hub_origin(url).encode()).hexdigest()[:16]
    except HubError:
        name = EARLIER
    return cfg.home / STASH_DIR / f"{name}.json"


def stashed(cfg: Config, url: str) -> dict | None:
    """What this computer kept for the hub at `url` when it left it, or None."""
    try:
        data = json.loads(stash_path(cfg, url).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("origin") == hub_origin(url) else None


def _token_fingerprint(token: str | None) -> str | None:
    return hashlib.sha256(token.encode()).hexdigest() if token else None


def keep_token(cfg: Config, url: str, token: str) -> None:
    """Keep the token this computer sends to the hub at `url` with, noting first that it is that hub's (left_token)."""
    _write_private(cfg.home / JOINED, {"hub": url, "origin": hub_origin(url), "token_sha256": _token_fingerprint(token)})
    write_token(cfg, token)


def left_token(cfg: Config) -> bool:
    """Whether this computer's token file holds a token it was given to send to a hub with, while it sends to none
    ([hub] url cleared by hand, or by a leave that stopped halfway): never a hub's own token. `interlatch hub enable`
    makes a new one instead of handing that one out, and the hub API doesn't take it (identity.token_ok)."""
    fp = None if cfg.is_spoke else _token_fingerprint(read_token(cfg))
    if not fp:
        return False
    folder = cfg.home / STASH_DIR
    for path in [cfg.home / JOINED, *(sorted(folder.glob("*.json")) if folder.is_dir() else [])]:
        try:
            if json.loads(path.read_text()).get("token_sha256") == fp:
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def drop_left_token(cfg: Config) -> None:
    """Remove a token left_token() found: the token first, then the note that it was a hub's."""
    token_path(cfg).unlink(missing_ok=True)
    (cfg.home / JOINED).unlink(missing_ok=True)


def _groups(cfg: Config) -> tuple[list[dict], list[dict]]:
    """(the groups shared as a hub project, every folder a group shared) in this computer's database. None for a named
    profile: groups are shared with Personal's hub only."""
    if not cfg.db_path.exists() or cfg.owner != "personal":
        return [], []
    from ..storage.sqlite import connect

    conn = connect(cfg.db_path, readonly=True)
    try:
        linked = [{"id": r[0], "name": r[1], "hub_project": r[2]} for r in conn.execute(
            "SELECT id, name, hub_project FROM project_groups WHERE hub_project IS NOT NULL ORDER BY id")]
        shares = [{"path": r[0], "hub_project": r[1], "group_id": r[2], "shared_at": r[3]} for r in conn.execute(
            "SELECT path, hub_project, group_id, shared_at FROM group_shares ORDER BY path")]
    finally:
        conn.close()
    return linked, shares


def _kept(cfg: Config) -> dict:
    """What this computer keeps for the hub it sends to (or a hub it left before this kept nothing), never a token."""
    linked, shares = _groups(cfg)
    return {"folders": dict(cfg.hub_folders), "left": list(cfg.hub_left), "share": cfg.hub_share,
            "all_folders": cfg.hub_all_folders, "groups": linked, "group_shares": shares}


def _anything(kept: dict) -> bool:
    return bool(kept["folders"] or kept["left"] or kept["groups"] or kept["group_shares"] or kept["all_folders"])


def _write_private(path: Path, data: dict) -> None:
    """Write the file whole, readable by this user only, in a folder that is too."""
    if path.parent.name == STASH_DIR:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def _clear(cfg: Config) -> None:
    """Clear what this computer keeps for a hub: [hub] folders, left and all_folders (which widens what it shares:
    off is safer), its groups' links to hub projects and the folders they shared, its teammates' lessons, and its
    notes of the last push, pull and hello. Not [hub] share: whether this computer records and analyzes its own
    sessions is its own choice, not the hub's, and a hub joined later must not turn that off. Not [hub] url, which the
    caller clears last."""
    from ..core.config import set_config_value
    from .projects import FOLDERS_FILE
    from .protocol import TEAM_SOURCE
    from .pull import TEAM_FILE
    from .push import LAST_PUSH

    for key, value in (("folders", "{}"), ("left", "[]"), ("all_folders", "false")):
        set_config_value(cfg, "hub", key, value)
    if cfg.db_path.exists():
        from ..storage.sqlite import connect

        from .pull import is_from

        conn = connect(cfg.db_path)
        try:
            if cfg.owner == "personal":  # groups are shared with Personal's hub only (a profile reads its archive)
                conn.execute("UPDATE project_groups SET hub_project = NULL WHERE hub_project IS NOT NULL")
                conn.execute("DELETE FROM group_shares")
            mine = [r[0] for r in conn.execute("SELECT id, fingerprint FROM knowledge WHERE source = ?", (TEAM_SOURCE,))
                    if is_from(cfg, r[1])]  # this hub's teammates' lessons, not another profile's hub's
            if mine:
                conn.execute(f"DELETE FROM knowledge WHERE id IN ({','.join('?' * len(mine))})", mine)
            conn.commit()
        finally:
            conn.close()
    for name in (LAST_PUSH, TEAM_FILE, FOLDERS_FILE, JOINED):
        (cfg.home / name).unlink(missing_ok=True)


def leave(cfg: Config) -> Path | None:
    """Leave the hub this computer sends to: put aside what it kept for that hub (stash_path), remove the token, clear
    the rest, and the hub's address last. Returns where it was put aside (None: nothing to keep). The token goes
    first: a token file without an address makes this computer look like a hub whose own token is that one.

    Run again after a leave that stopped halfway (the token already gone), it keeps what the first one put aside."""
    from ..core.config import set_config_value

    path = stash_path(cfg, cfg.hub_url)
    token = read_token(cfg)
    kept = _kept(cfg)
    if token or not path.exists():
        try:
            origin = hub_origin(cfg.hub_url)
        except HubError:
            origin = None
        _write_private(path, {"hub": cfg.hub_url, "origin": origin, "left_at": utcnow_iso(),
                              "token_sha256": _token_fingerprint(token), **kept})
    token_path(cfg).unlink(missing_ok=True)
    _clear(cfg)
    set_config_value(cfg, "hub", "url", '""')
    return path


def clear_leftovers(cfg: Config) -> Path | None:
    """Before joining a hub: what an earlier leave (before Interlatch cleared it) left behind would otherwise go to
    the new hub. Put its settings aside as hub-left/earlier.json and clear it all. Returns that file when there were
    any."""
    kept = _kept(cfg)
    path = None
    if _anything(kept):
        path = cfg.home / STASH_DIR / f"{EARLIER}.json"
        _write_private(path, {"hub": None, "origin": None, "left_at": utcnow_iso(), **kept})
    _clear(cfg)
    return path


def restore(cfg: Config, kept: dict, *, share: str | None = None, all_folders: bool = False) -> None:
    """Bring back what this computer kept for a hub it left (stashed()), before it joins that hub again: its folders,
    the projects it left, how it shared, and its groups' links. `share` and `all_folders`: what the join itself asks
    for, which wins."""
    from ..core.config import set_config_value, toml_table
    from ..core.paths import is_abs_path

    folders = {str(k): str(v) for k, v in (kept.get("folders") or {}).items() if is_abs_path(str(k))}
    left = sorted({str(x) for x in kept.get("left") or [] if is_abs_path(str(x))})
    set_config_value(cfg, "hub", "folders", toml_table(folders))
    set_config_value(cfg, "hub", "left", json.dumps(left))
    share = share or (kept.get("share") if kept.get("share") in ("everything", "knowledge") else None)
    if share:
        set_config_value(cfg, "hub", "share", json.dumps(share))
    set_config_value(cfg, "hub", "all_folders", "true" if all_folders or kept.get("all_folders") is True else "false")
    if cfg.db_path.exists() and cfg.owner == "personal" and (kept.get("groups") or kept.get("group_shares")):
        from ..storage.sqlite import connect

        conn = connect(cfg.db_path)
        try:
            for g in kept.get("groups") or []:
                if isinstance(g, dict) and isinstance(g.get("id"), int) and isinstance(g.get("hub_project"), str):
                    conn.execute("UPDATE project_groups SET hub_project = ? WHERE id = ?", (g["hub_project"], g["id"]))
            for s in kept.get("group_shares") or []:
                if isinstance(s, dict) and isinstance(s.get("path"), str) and isinstance(s.get("hub_project"), str):
                    conn.execute("INSERT INTO group_shares(path, hub_project, group_id, shared_at) VALUES (?, ?, ?, ?) "
                                 "ON CONFLICT(path) DO NOTHING",
                                 (s["path"], s["hub_project"], s.get("group_id"), s.get("shared_at")))
            conn.commit()
        finally:
            conn.close()


def forget(cfg: Config, url: str) -> None:
    stash_path(cfg, url).unlink(missing_ok=True)
