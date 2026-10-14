"""Which project a folder belongs to here, when it is another computer's: by its git remote, by a folder that
computer added to a project, by a project set up on this computer, or by [hub] path_map."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from ..core.config import Config
from ..core.machine import local_machine
from ..core.paths import is_abs_path, normalize_project_path, path_kind

FOLDERS_KV = "hub-folders:"  # + machine id: the folders that computer added to projects here (spoke's [hub] folders)

MAX_FOLDERS = 200


def normalize_remote(url: str | None) -> str | None:
    """git@github.com:Org/Repo.git, https://user@github.com/Org/Repo, ssh://git@github.com/Org/Repo -> github.com/org/repo."""
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    m = re.match(r"^[\w.-]+@([\w.-]+):(.+)$", u)  # scp-like ssh
    if m:
        host, path = m.group(1), m.group(2)
    else:
        parsed = urlparse(u)
        if not parsed.hostname:
            return None
        host, path = parsed.hostname, parsed.path
    path = re.sub(r"\.git/?$", "", path.strip("/"))
    return f"{host.lower()}/{path.lower()}" if path else None


def git_info(path: str) -> tuple[str, str] | None:
    """(top-level folder, normalized remote) of the git repository at path, or None."""
    if not path or not Path(path).is_dir() or not shutil.which("git"):
        return None
    try:
        top = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if top.returncode != 0:
            return None
        remotes = subprocess.run(["git", "-C", path, "remote"], capture_output=True, text=True, timeout=5).stdout.split()
        if not remotes:
            return None
        name = "origin" if "origin" in remotes else remotes[0]
        url = subprocess.run(["git", "-C", path, "remote", "get-url", name], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    remote = normalize_remote(url)
    return (top.stdout.strip(), remote) if remote else None


def under(path: str | None, folder: str) -> bool:
    """`path` is `folder` or somewhere below it, compared as normalize_project_path spells them (C:\\x is under c:/).
    A POSIX folder never holds a Windows path (a drive or a UNC share), nor the other way round; no folder (""), nothing."""
    if not path or not folder:
        return False
    path, folder = normalize_project_path(path), normalize_project_path(folder)
    if path_kind(path) != path_kind(folder):
        return False
    return path == folder or path.startswith(folder if folder.endswith("/") else folder + "/")


def clean_folders(raw) -> dict[str, str]:
    """A spoke's {folder: project on the hub} as the hub accepts it: absolute paths, a bounded number of them."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in list(raw.items())[:MAX_FOLDERS]:
        k, v = str(k).strip(), str(v).strip()
        if is_abs_path(k) and is_abs_path(v) and len(k) <= 1024 and len(v) <= 1024 and "\0" not in k + v:
            out[normalize_project_path(k)] = normalize_project_path(v)
    return out


def folders_of(conn, machine_id: str) -> dict[str, str]:
    """The folders another computer added to projects here, as it last reported them."""
    from ..storage.kv import kv_get

    try:
        data = json.loads(kv_get(conn, FOLDERS_KV + machine_id) or "{}")
    except ValueError:
        return {}
    return clean_folders(data)


def declared_projects(conn) -> list[str]:
    """The projects set up on this hub ahead of time (`interlatch hub project add`): folders on this computer."""
    return [r[0] for r in conn.execute("SELECT path FROM hub_projects ORDER BY path")]


class ProjectResolver:
    """Maps a project folder on another computer to the same project's folder here.

    By git remote (the other computer reported its repositories' remotes, or the transcript names one), or by a
    folder that computer added to a project here (`interlatch hub add-folder`), whichever is more specific: a
    repository with a known remote inside an added folder follows its remote. Then by `[hub] path_map` prefixes;
    otherwise the folder is kept as that computer recorded it.
    """

    def __init__(self, cfg: Config, conn):
        self.cfg = cfg
        self.conn = conn
        self.me = local_machine(cfg)["id"]
        self._repos: dict[str, dict] = {}
        self._folders: dict[str, dict[str, str]] = {}
        self._local: dict[str, str] | None = None
        self._declared: list[str] | None = None

    def repos(self, machine_id: str) -> dict:
        if machine_id not in self._repos:
            row = self.conn.execute("SELECT repos_json FROM machines WHERE id = ?", (machine_id,)).fetchone()
            try:
                self._repos[machine_id] = json.loads(row[0]) if row and row[0] else {}
            except ValueError:
                self._repos[machine_id] = {}
        return self._repos[machine_id]

    def folders(self, machine_id: str) -> dict[str, str]:
        if machine_id not in self._folders:
            self._folders[machine_id] = folders_of(self.conn, machine_id)
        return self._folders[machine_id]

    def declared(self) -> list[str]:
        if self._declared is None:
            self._declared = declared_projects(self.conn)
        return self._declared

    def project_of(self, path: str | None) -> str | None:
        """A folder set up as a project here (add_project) holds everything below it."""
        if not path:
            return path
        return max((d for d in self.declared() if under(path, d)), key=len, default=path)

    def project_remotes(self) -> dict[str, str]:
        """{normalized remote: the project here its repository's sessions are filed under}."""
        return {rem: self.project_of(top) for rem, top in self.local_remotes().items()}

    def local_remotes(self) -> dict[str, str]:
        """{normalized remote: top-level folder} of the repositories this computer's own sessions ran in."""
        if self._local is None:
            from ..storage.kv import kv_get, kv_set

            self._local = {}
            paths = [r[0] for r in self.conn.execute(  # where they ran, also when a set-up project holds them
                "SELECT COALESCE(machine_path, project_path) p, COUNT(*) n FROM sessions WHERE project_path IS NOT NULL "
                "AND (machine_id IS NULL OR machine_id = ?) GROUP BY p ORDER BY n", (self.me,))]
            for p in paths:
                key = f"git:{p}"
                cached = kv_get(self.conn, key)
                if cached is None:
                    info = git_info(p)
                    cached = json.dumps(list(info)) if info else ""
                    kv_set(self.conn, key, cached)
                if cached:
                    top, remote = json.loads(cached)
                    self._local[remote] = top  # ordered by use, so the most-used folder wins
        return self._local

    def resolve(self, machine_id: str | None, path: str | None, remote: str | None = None) -> str | None:
        if not path or not machine_id or machine_id == self.me:
            return self.project_of(path)
        if path_kind(path) in ("drive", "unc"):  # another computer's Windows folder: spelled one way, as its [hub]
            path = normalize_project_path(path)  # folders and left are, so C:\app and c:/app are one project here
        return self.project_of(self._resolve(machine_id, path, remote))

    def _resolve(self, machine_id: str, path: str, remote: str | None) -> str:
        norm, top = normalize_remote(remote), None
        for repo_top, repo_remote in sorted(self.repos(machine_id).values(), key=lambda v: -len(v[0])):
            if under(path, repo_top):
                top, norm = normalize_project_path(repo_top), norm or repo_remote
                break
        local_top = self.local_remotes().get(norm) if norm else None
        added = self.folders(machine_id)
        folder = max((f for f in added if under(path, f)), key=len, default=None)
        # a remote named by the transcript alone (no reported top level) is the session's own repository
        if local_top and (folder is None or top is None or len(top) >= len(folder)):
            rest = path[len(top):].strip("/") if top else ""
            return f"{local_top}/{rest}" if rest else local_top
        if folder is not None:
            return added[folder]  # the folder and everything below it is that project
        for src in sorted(self.cfg.hub_path_map, key=len, reverse=True):
            if path == src or path.startswith(src + "/"):
                return self.cfg.hub_path_map[src] + path[len(src):]
        return path


_resolvers: dict[int, ProjectResolver] = {}


def resolver(cfg: Config, conn, *, fresh: bool = False) -> ProjectResolver:
    key = id(conn)
    if fresh or key not in _resolvers or _resolvers[key].conn is not conn:
        _resolvers.clear()  # one live sync at a time; don't keep resolvers of closed connections around
        _resolvers[key] = ProjectResolver(cfg, conn)
    return _resolvers[key]
