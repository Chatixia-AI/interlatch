"""A member's computer talking to its hub: the HTTP client, the hello that tells the hub what this computer has,
joining with an invite, signing in to the hub's dashboard, and adding or removing folders and leaving projects (one
change at a time, never during a push)."""

from __future__ import annotations

import functools
import http.client
import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlencode, urlparse

from .. import __version__
from ..core.config import Config
from ..core.machine import local_machine, platform_label
from ..core.paths import normalize_project_path
from ..projects import routing  # git_info through routing: one patch there reaches every caller
from .identity import machine_key, read_token
from .projects import REPOS_CACHE_S, GroupRoutes, _record_folders, group_routes, sent_folders
from .protocol import PROTOCOL, FolderError, HubError, HubUnauthorized, HubUnreachable, _check_protocol

SHARING_LOCK = "hub-sharing.lock"  # on a spoke: one push, hello or folder change at a time (sharing())
PUSH_WAIT_S = 600  # a push waits this long for another to finish
CHANGE_WAIT_S = 60  # a folder change, which someone is waiting on, this long


@dataclass
class Root:
    name: str   # claude, claude-2, codex, …: the folder it lands in on the hub
    kind: str   # claude | codex
    path: Path


def spoke_roots(cfg: Config) -> list[Root]:
    roots = []
    for kind, dirs in (("claude", cfg.claude_dirs), ("codex", cfg.codex_dirs)):
        for i, d in enumerate(dirs):
            if d.is_dir():
                roots.append(Root(kind if i == 0 else f"{kind}-{i + 1}", kind, d))
    return roots


def _walk(base: Path, rel_base: PurePosixPath):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel = PurePosixPath(rel_base, Path(dirpath).relative_to(base).as_posix())
        for fn in filenames:
            if not fn.startswith("."):
                yield str(rel / fn).removeprefix("./"), Path(dirpath) / fn


def root_files(root: Root):
    """(relative path, file) for every file of an agent's folder the hub's ingest reads."""
    if root.kind == "claude":
        projects = root.path / "projects"
        if projects.is_dir():
            yield from _walk(projects, PurePosixPath("projects"))
        if (root.path / "history.jsonl").is_file():
            yield "history.jsonl", root.path / "history.jsonl"
    else:
        for sub in ("sessions", "memories"):
            if (root.path / sub).is_dir():
                yield from _walk(root.path / sub, PurePosixPath(sub))
        for name in ("session_index.jsonl", "external_agent_session_imports.json"):
            if (root.path / name).is_file():
                yield name, root.path / name


class _Exclusions:
    """cfg.exclude_projects, applied on the spoke so excluded projects never leave it."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache: dict[str, bool] = {}

    def excluded(self, root: Root, rel: str, path: Path) -> bool:
        if not self.cfg.exclude_projects:
            return False
        from ..sources.claude.files import decode_project_dir, peek_cwd
        from ..sources.codex.parser import read_meta

        parts = rel.split("/")
        if root.kind == "claude" and parts[0] == "projects" and len(parts) > 2:
            key = f"{root.name}/{parts[1]}"
            if key not in self.cache:
                proj = root.path / "projects" / parts[1]
                self.cache[key] = self.cfg.is_excluded(peek_cwd(proj) or decode_project_dir(parts[1]))
            return self.cache[key]
        if root.kind == "codex" and parts[0] == "sessions" and path.suffix == ".jsonl":
            return self.cfg.is_excluded(read_meta(path).get("cwd"))
        return False


def spoke_repos(cfg: Config, roots: list[Root]) -> dict[str, list[str]]:
    """{project folder: [git top level, normalized remote]} for the Claude Code projects here, cached for a day."""
    from ..sources.claude.files import decode_project_dir, peek_cwd

    cache_path = cfg.home / "repos-cache.json"
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    now = time.time()
    out: dict[str, list[str]] = {}
    for root in roots:
        projects = root.path / "projects"
        if root.kind != "claude" or not projects.is_dir():
            continue
        for proj in projects.iterdir():
            if not proj.is_dir():
                continue
            cwd = peek_cwd(proj) or decode_project_dir(proj.name)
            hit = cache.get(cwd)
            if not hit or now - hit.get("at", 0) > REPOS_CACHE_S:
                info = routing.git_info(cwd)
                hit = cache[cwd] = {"at": now, "info": list(info) if info else None}
            if hit.get("info"):
                out[cwd] = hit["info"]
    try:
        cache_path.write_text(json.dumps(cache))
    except OSError:
        pass
    return out


class HubClient:
    """Keep-alive HTTP(S) client for the hub's /api/hub endpoints."""

    def __init__(self, url: str, token: str, *, timeout: float = 120):
        parsed = urlparse(url if "://" in url else f"https://{url}")
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise HubError(f"not a hub address: {url}")
        self.url = f"{parsed.scheme}://{parsed.netloc}"
        self.https = parsed.scheme == "https"
        self.host = parsed.hostname
        self.port = parsed.port
        self.token = token
        self.timeout = timeout
        self.conn: http.client.HTTPConnection | None = None

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def _connection(self) -> http.client.HTTPConnection:
        if self.conn is None:
            cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
            self.conn = cls(self.host, self.port, timeout=self.timeout)
        return self.conn

    def request(self, method: str, path: str, *, params: dict | None = None, body=None, length: int | None = None,
                headers: dict | None = None) -> dict:
        target = path + (f"?{urlencode(params)}" if params else "")
        hdrs = {"User-Agent": f"interlatch/{__version__}", **(headers or {})}
        if self.token:  # none when redeeming an invite: the code is the credential
            hdrs["Authorization"] = f"Bearer {self.token}"
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        if body is not None:
            hdrs["Content-Length"] = str(length if length is not None else len(body))
        for attempt in (1, 2):  # a kept-alive connection the hub closed meanwhile gets one fresh retry
            if hasattr(body, "seek"):
                body.seek(0)
            try:
                conn = self._connection()
                conn.request(method, target, body=body, headers=hdrs)
                resp = conn.getresponse()
                data = resp.read()
                break
            except (OSError, http.client.HTTPException) as exc:
                self.close()
                if attempt == 2:
                    raise HubUnreachable(f"can't reach the hub at {self.url}: {exc}") from exc
        try:
            payload = json.loads(data or b"{}")
        except ValueError:
            payload = {"error": data[:200].decode(errors="replace")}
        if resp.status == 401:
            raise HubUnauthorized("the hub did not accept this computer's token; join it again with a new invite "
                                  "(`interlatch hub invite` on the hub) or the command `interlatch hub enable` prints there")
        if resp.status >= 400:
            raise HubError(f"hub error {resp.status}: {payload.get('error') or payload}")
        return payload


def hello_info(cfg: Config, roots: list[Root], routes: GroupRoutes | None = None) -> dict:
    me = local_machine(cfg)
    routes = group_routes(cfg) if routes is None else routes
    return {"protocol": PROTOCOL, "machine": me["id"], "name": me["name"], "key": machine_key(cfg),
            "platform": platform_label(), "version": __version__, "roots": [r.name for r in roots], "repos": spoke_repos(cfg, roots),
            "folders": sent_folders(cfg, routes), "left": cfg.hub_left, "share": cfg.hub_share}


_sharing = threading.local()


@contextmanager
def sharing(cfg: Config, wait: float):
    """Yields the config as config.toml says now, while no other push, hello or folder change of this computer runs.

    Every hello tells the hub the whole list of this computer's folders and the projects it left, and the hub files
    by the last list it heard. A push that started before a folder was added (an analysis run takes minutes) would
    send the list it started with, and the hub would take the folder back out; one that started before a folder was
    removed or a project left would share it again. So a push reads the config only once it holds this, and a folder
    change waits for a push between its hello and its last batch. Held already by this thread: just the config."""
    from ..core.config import current
    from ..core.util import file_lock

    if getattr(_sharing, "held", False):
        yield current(cfg)
        return
    with file_lock(cfg.locks_dir / SHARING_LOCK, timeout=wait) as got:
        if not got:
            raise HubError("this computer is still sharing with the hub; try again in a minute")
        _sharing.held = True
        try:
            yield current(cfg)
        finally:
            _sharing.held = False


def _one_change(fn):
    """A hello or a change to this computer's folders on the hub, which someone waits on: under sharing(), by the
    config as it is now."""
    @functools.wraps(fn)
    def wrapped(cfg: Config, *args, **kwargs):
        with sharing(cfg, CHANGE_WAIT_S) as now:
            return fn(now, *args, **kwargs)
    return wrapped


@_one_change
def handshake(cfg: Config, client: HubClient | None = None) -> dict:
    """Say hello without sending files: the hub's projects and what it made of this computer's folders."""
    token = read_token(cfg)
    if not cfg.hub_url or not token:
        raise HubError("this computer has not joined a hub (`interlatch hub join`)")
    client = client or HubClient(cfg.hub_url, token)
    routes = group_routes(cfg)
    info = hello_info(cfg, spoke_roots(cfg), routes)
    try:
        hello = client.request("POST", "/api/hub/hello", body=info)
    finally:
        client.close()
    _check_protocol(hello)
    routes.remember(cfg)
    _record_folders(cfg, hello, info["repos"])
    return hello


def redeem_invite(cfg: Config, url: str, code: str, client: HubClient | None = None) -> dict:
    """Trade an invite code for this computer's own token at the hub (`interlatch hub join --code`).

    Returns the hub's answer: {"token", "person", "hub"}. The caller keeps the token (write_token). A computer that
    sends to another hub is refused before anything goes out: the join sends this computer's key, which is how a hub
    recognises it (people.may_claim), and another hub's operator has no business with it.
    """
    from .membership import other_hub, same_hub

    if joined := other_hub(cfg, url):
        raise HubError(f"this computer sends to the hub at {joined}; leave it first (`interlatch hub leave`)")
    me = local_machine(cfg)
    client = client or HubClient(url, "")
    if read_token(cfg) and cfg.hub_url and same_hub(cfg.hub_url, client.url):
        # it sends to this hub already (with the shared token): a hello first puts its key on record there, so the
        # invite can claim this computer's id with it (people.may_claim)
        try:
            handshake(cfg)
        except HubError:
            pass
    try:
        got = client.request("POST", "/api/hub/join", body={"code": code, "machine": me["id"], "name": me["name"],
                                                             "key": machine_key(cfg), "platform": platform_label(),
                                                             "version": __version__})
    except HubUnauthorized:  # a hub that predates invites asks every caller for its shared token
        raise HubError(f"the hub at {client.url} doesn't take invite codes yet: update Interlatch there, or join with "
                       "the command its `interlatch hub enable` prints") from None
    finally:
        client.close()
    if not isinstance(got.get("token"), str) or not got["token"]:
        raise HubError(f"the hub at {client.url} sent no token")
    return got


def dashboard_signin(cfg: Config, client: HubClient | None = None, page: str | None = None) -> str:
    """A short-lived link that opens the hub's dashboard as the person this computer joined as (no password); on page
    (a hash route such as /project?path=…) when given. An older hub ignores it and opens on Home."""
    from .people import LANDING_RE

    token = read_token(cfg)
    if not cfg.hub_url or not token:
        raise HubError("this computer has not joined a hub (`interlatch hub join`)")
    client = client or HubClient(cfg.hub_url, token)
    try:
        got = client.request("POST", "/api/hub/signin", body={"machine": local_machine(cfg)["id"]})
    finally:
        client.close()
    code = got.get("code")
    if not isinstance(code, str) or not code:
        raise HubError("the hub sent no sign-in code")
    link = f"{cfg.hub_url}/signin?code={quote(code, safe='-_')}"
    return f"{link}&next={quote(page, safe='')}" if isinstance(page, str) and LANDING_RE.fullmatch(page) else link


def _save_hub(cfg: Config, *, folders: dict[str, str] | None = None, left: list[str] | None = None) -> Config:
    from ..core.config import load_config, set_config_value, toml_table

    if folders is not None:
        set_config_value(cfg, "hub", "folders", toml_table(folders))
    if left is not None:
        set_config_value(cfg, "hub", "left", json.dumps(sorted(set(left))))
    return load_config(cfg.home)


@_one_change
def add_folder(cfg: Config, folder: str, project: dict, hello: dict) -> tuple[Config, str | None]:
    """`interlatch hub add-folder`: file the sessions in `folder` (absolute), and the folders below it, under the hub's
    `project`; one this computer left is joined again. `hello`: the hub's answer to handshake(), for the git remotes
    it knows. Returns the reloaded config and, when nothing changed because the folder's repository already goes
    there by its git remote, that remote. Raises FolderError when the folder can't go there."""
    folder = normalize_project_path(folder)
    project_path = normalize_project_path(project["path"])
    name = project.get("name") or Path(project_path).name
    if not Path(folder).is_dir():
        raise FolderError("{folder} is not a folder.", folder=folder)
    info = routing.git_info(folder)
    owner = (hello.get("remotes") or {}).get(info[1]) if info else None
    if owner and normalize_project_path(owner) != project_path:
        raise FolderError("{folder} is in the git repository {remote}, which the hub files under {owner}. A repository "
                          "belongs to one project, so its sessions can't go to {name}.",
                          folder=folder, remote=info[1], owner=Path(owner).name, name=name)
    left = [x for x in cfg.hub_left if x != project_path]
    if owner:
        return _save_hub(cfg, left=left) if left != cfg.hub_left else cfg, info[1]
    return _save_hub(cfg, folders={**cfg.hub_folders, folder: project_path},
                     left=left if left != cfg.hub_left else None), None


@_one_change
def remove_folder(cfg: Config, folder: str, client: HubClient | None = None) -> tuple[Config, int | None]:
    """`interlatch hub remove-folder`, for the wrong folder: its sessions no longer go to the project. A computer that
    shares knowledge first has the hub take back what it shared from there (withdraw); one that sends transcripts
    leaves them to the hub, which files them by their git remote or their own folder again. Returns the reloaded
    config and how many sessions the hub took back (None: it wasn't asked)."""
    folder = normalize_project_path(folder)
    if folder not in cfg.hub_folders:
        raise FolderError("{folder} was not added.", folder=folder)
    taken = None
    if cfg.shares_knowledge:  # the hub first: if it can't be reached, nothing here changes
        token = read_token(cfg)
        if not token:
            raise HubError("this computer has not joined a hub (`interlatch hub join`)")
        client = client or HubClient(cfg.hub_url, token)
        try:
            r = client.request("POST", "/api/hub/withdraw", body={"machine": local_machine(cfg)["id"], "folder": folder})
        except HubError as exc:
            if str(exc).startswith("hub error 404"):
                raise FolderError("The hub runs an older version that can't take back what this computer shared. "
                                  "Update the hub first.") from None
            raise
        finally:
            client.close()
        taken = int(r.get("sessions") or 0)
    project = cfg.hub_folders[folder]
    rest = {k: v for k, v in cfg.hub_folders.items() if k != folder}
    left = cfg.hub_left
    if project in left and project not in rest.values():  # no folder of it left here: nothing to leave any more
        left = [x for x in left if x != project]
    return _save_hub(cfg, folders=rest, left=left if left != cfg.hub_left else None), taken


@_one_change
def leave_project(cfg: Config, project: str) -> Config:
    """This computer leaves a project on the hub ([hub] left): it no longer shares sessions filed there or gets their
    teammates' lessons. What it already shared stays on the hub. Only for a computer that shares knowledge: one that
    sends transcripts sends them all."""
    if not cfg.shares_knowledge:
        raise FolderError("Only a computer that shares knowledge can leave a project: one that sends its transcripts "
                          "sends them all.")
    project = normalize_project_path(project)
    return cfg if project in cfg.hub_left else _save_hub(cfg, left=[*cfg.hub_left, project])


@_one_change
def rejoin_project(cfg: Config, project: str) -> Config:
    """Undo leave_project: sessions filed under `project` are shared again, and its teammates' lessons come back."""
    project = normalize_project_path(project)
    return _save_hub(cfg, left=[x for x in cfg.hub_left if x != project]) if project in cfg.hub_left else cfg


@_one_change
def leave_hub(cfg: Config) -> Config:
    """`interlatch hub leave`, and Settings › Devices › Leave the hub: stop sending to the hub. This computer records
    and analyzes its own sessions again; the hub keeps what it was sent. What this computer kept for that hub is put
    aside and cleared (membership.leave), the token first, so a hub joined later never gets any of it."""
    from ..core.config import load_config
    from .membership import leave

    leave(cfg)
    return load_config(cfg.home)


@_one_change
def move_hub(cfg: Config, url: str) -> Config:
    """`interlatch hub set-url`: the hub this computer sends to now answers at `url`. Only the address changes: the
    token, the folders and everything else stay, and nothing is sent until the next push."""
    from ..core.config import load_config, set_config_value

    if not cfg.is_spoke:
        raise HubError("this computer has not joined a hub (`interlatch hub join`)")
    HubClient(url, "")  # an http(s) address, or HubError
    set_config_value(cfg, "hub", "url", json.dumps(url.rstrip("/")))
    return load_config(cfg.home)
