"""Self-update for the dashboard's Update button: find how Interlatch was installed and upgrade it the same way.

Checking PyPI is the only network call. It runs when the user asks (Status › Check for updates) or, with
`[updates] check_daily` on, once a day while the dashboard is open; the last answer is kept in the database.
An install from a local checkout is checked without the network: it is out of date when the checkout's
files changed after the install.

Interlatch was called Chronicle, and its package agents-chronicle. Each release also comes out as an agents-chronicle
that depends on interlatch and carries the same chronicle/ files (so pip's removal of the old package can't
delete them), so an old install's own upgrade lands here; such an install is offered the move to
the interlatch package instead of an upgrade (move_steps).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from . import __version__
from .i18n import tr

log = logging.getLogger("chronicle.update")

DIST = "interlatch"
LEGACY_DIST = "agents-chronicle"  # the package's name before the rename
PYPI_JSON = f"https://pypi.org/pypi/{DIST}/json"
RELEASES_URL = "https://github.com/Chatixia-AI/interlatch/releases/latest"

# server.serve() sets this: a plain `interlatch ui` (by hand or launchd) can restart itself after an update;
# the desktop app serves the dashboard from its own process and is restarted by the user instead.
RESTARTABLE = False
REMOTE_KEY = "update_check"  # kv: the last PyPI check, so it outlives a restart
DAY = 86400
_remote: dict = {}  # the last PyPI check: {"latest": ..., "checked_at": ...} or {"error": ...}
_method: dict = {}  # install_method() is polled with the status bar; it only changes when an update runs


def _vkey(v: str | None) -> tuple:
    """Release numbers of a version ("0.1.10" -> (0, 1, 10)); enough to order Interlatch's own releases."""
    m = re.match(r"\d+(?:\.\d+)*", v or "")
    return tuple(int(n) for n in m.group(0).split(".")) if m else ()


def _which(name: str) -> str | None:
    """Like shutil.which, but also looks where installers put tools: launchd's PATH is only /usr/bin:/bin."""
    home = Path.home()
    found = shutil.which(name)
    for p in [found, home / ".local/bin" / name, home / ".cargo/bin" / name, f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"]:
        if p and Path(p).is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def install_method() -> dict:
    """How this copy was installed: kind, where from, and the command that upgrades it (None when there is none).
    "steps", when there are several commands (the move from agents-chronicle), runs them in order; "command" is the
    first."""
    if not _method:
        _method.update(_install_method())
    return _method


def _install_method() -> dict:
    from .config import env

    if getattr(sys, "frozen", False):
        return {"kind": "app", "label": "Interlatch.app", "command": None}
    if env("CONTAINER"):  # docker/Dockerfile: an upgrade in place would vanish with the container
        return {"kind": "container", "label": "container image", "command": None}
    receipt = Path(sys.prefix) / "uv-receipt.toml"
    if receipt.is_file():
        reqs = tomllib.loads(receipt.read_text()).get("tool", {}).get("requirements", [])
        # the tool is named after its package: interlatch, or agents-chronicle installed before the rename
        req = next((r for r in reqs if r.get("name") == DIST), None) or next(
            (r for r in reqs if r.get("name") == LEGACY_DIST), {})
        source = req.get("directory") or req.get("path") or req.get("git") or req.get("url")
        uv = _which("uv")
        extras = list(req.get("extras") or [])  # e.g. app, team: a reinstall that adds one keeps the others
        method = {"kind": "uv", "label": f"uv tool from {source}" if source else "uv tool from PyPI", "source": source,
                  "local": bool(req.get("directory") or req.get("path")), "extras": extras,
                  # installed files carry the install time (the receipt is not rewritten by an upgrade)
                  "installed_at": (Path(__file__).parent / "__init__.py").stat().st_mtime}
        if req.get("name") == LEGACY_DIST and not source:
            return {**method, "label": f"uv tool {LEGACY_DIST} from PyPI", "move": True,
                    **_steps(move_steps("uv", uv, extras) if uv else None)}
        # a checkout's version rarely changes, so a plain upgrade would keep the old build: --reinstall rebuilds it
        return {**method, "command": [uv, "tool", "upgrade", *(["--reinstall"] if source else []),
                                      req.get("name") or DIST] if uv else None}
    found = _distribution()
    if found is None:
        return {"kind": "source", "label": "source tree (not installed)", "command": None}
    name, direct = found
    if direct and direct.get("dir_info", {}).get("editable"):
        return {"kind": "source", "label": f"editable checkout {unquote(urlparse(direct['url']).path)}", "command": None}
    if "pipx" in Path(sys.prefix).parts or "pipx" in sys.prefix:
        pipx = _which("pipx")
        if Path(sys.prefix).name == LEGACY_DIST:  # pipx names the environment after the package
            return {"kind": "pipx", "label": f"pipx {LEGACY_DIST}", "move": True,
                    **_steps(move_steps("pipx", pipx, []) if pipx else None)}
        return {"kind": "pipx", "label": "pipx", "command": [pipx, "upgrade", DIST] if pipx else None}
    has_pip = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode == 0
    if name == LEGACY_DIST or _installed(LEGACY_DIST):
        return {"kind": "pip", "label": f"pip in {sys.prefix} ({LEGACY_DIST})", "move": True,
                **_steps(move_steps("pip", sys.executable, []) if has_pip else None)}
    return {"kind": "pip", "label": f"pip in {sys.prefix}",
            "command": [sys.executable, "-m", "pip", "install", "--upgrade", DIST] if has_pip else None}


def _installed(name: str) -> bool:
    try:
        distribution(name)
        return True
    except PackageNotFoundError:
        return False


def _distribution() -> tuple[str, dict | None] | None:
    """(the installed package's name, its direct_url.json): interlatch, else agents-chronicle; None from a source tree."""
    for name in (DIST, LEGACY_DIST):
        try:
            return name, json.loads(distribution(name).read_text("direct_url.json") or "null")
        except PackageNotFoundError:
            continue
    return None


def _steps(steps: list[list[str]] | None) -> dict:
    return {"command": steps[0] if steps else None, "steps": steps}


def move_steps(kind: str, tool: str, extras: list[str]) -> list[list[str]]:
    """The commands that replace an agents-chronicle install with interlatch, keeping its extras.

    agents-chronicle (the one that depends on interlatch and carries its files) installs the same `interlatch` and `chronicle` commands.
    With uv: install interlatch over them first (nothing is lost when that fails), remove agents-chronicle, which takes
    the commands with it, then install interlatch once more, which puts them back from what the first step fetched.
    Both installs force: without it the second one finds interlatch installed and does nothing, and the commands stay
    gone (launchd's agents, the hooks and the MCP server all run them). pipx can't install the second package over the first's commands, so it removes agents-chronicle first. pip keeps
    both in one environment, sharing the module and the commands: reinstalling interlatch after removing the other
    brings back what that removal took."""
    want = f"{DIST}[{','.join(sorted(extras))}]" if extras else DIST
    if kind == "uv":
        install = [tool, "tool", "install", "--force", "--python", f"{sys.version_info[0]}.{sys.version_info[1]}", want]
        return [install, [tool, "tool", "uninstall", LEGACY_DIST], install]
    if kind == "pipx":
        return [[tool, "uninstall", LEGACY_DIST], [tool, "install", want]]
    pip = [tool, "-m", "pip"]
    return [[*pip, "install", "--upgrade", want], [*pip, "uninstall", "-y", LEGACY_DIST],
            [*pip, "install", "--force-reinstall", "--no-deps", DIST]]


def _checkout_files(source: str) -> list[Path]:
    root = Path(source).expanduser()
    return [root / "pyproject.toml", *(p for p in (root / "src").rglob("*") if p.is_file() and "__pycache__" not in p.parts)]


def _checkout_changed(source: str, since: float) -> bool:
    """True when the checkout has files newer than the install, i.e. reinstalling would change something."""
    return any(p.stat().st_mtime > since for p in _checkout_files(source) if p.exists())


def _git(source: str, *args: str) -> str | None:
    git = _which("git")
    if not git:
        return None
    try:
        r = subprocess.run([git, "-C", str(Path(source).expanduser()), *args], capture_output=True, text=True, timeout=5,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def checkout_changes(source: str, since: float) -> dict:
    """What reinstalling would bring in: the files changed since the install and the commits made after it."""
    root = Path(source).expanduser()
    files = sorted((p for p in _checkout_files(source) if p.exists() and p.stat().st_mtime > since),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    log = _git(source, "log", f"--since=@{int(since)}", "--format=%h%x09%ct%x09%s", "-n", "30") or ""
    commits = [dict(zip(("sha", "at", "subject"), line.split("\t", 2), strict=True)) for line in log.splitlines() if line.count("\t") >= 2]
    return {"files": [str(p.relative_to(root)) for p in files], "commits": [{**c, "at": int(c["at"])} for c in commits]}


def _checkout_version(source: str) -> str | None:
    """The version the checkout builds as: pyproject.toml's, or (dynamic, hatch-vcs) the one its git tags give."""
    try:
        return tomllib.loads((Path(source).expanduser() / "pyproject.toml").read_text())["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        pass
    m = re.fullmatch(r"v(\d+(?:\.\d+)*)-(\d+)-(g[0-9a-f]+)",
                     (_git(source, "describe", "--tags", "--long", "--match", "v[0-9]*") or "").strip())
    if not m:
        return None
    tag, distance, sha = m.groups()
    if distance == "0":
        return tag
    *head, last = tag.split(".")  # commits after v0.7.0 build as 0.7.1.devN, like hatch-vcs
    return ".".join([*head, str(int(last) + 1)]) + f".dev{distance}+{sha}"


def check(remote: bool = False, detail: bool = False) -> dict:
    """What the Status page shows. remote=True asks PyPI for the latest release; otherwise the last answer is reused.
    detail=True also lists what a checkout reinstall would bring in (the status bar's poll leaves it out)."""
    m = install_method()
    steps = m.get("steps") or ([m["command"]] if m["command"] else [])
    info = {"current": __version__, "kind": m["kind"], "method": m["label"], "source": m.get("source"),
            "local": m.get("local", False), "latest": None, "available": False, "move": bool(m.get("move")),
            "can_update": bool(steps), "command": " && ".join(shlex.join(s) for s in steps) or None,
            "restartable": RESTARTABLE, "checked_at": None, "error": None, "releases_url": RELEASES_URL}
    if m["kind"] == "source":
        info["note"] = tr("Running from a source checkout: git pull to update.")
        return info
    if m.get("local"):  # checked locally, no network
        info["latest"] = _checkout_version(m["source"])
        info["available"] = _checkout_changed(m["source"], m["installed_at"])
        info["installed_at"] = m["installed_at"]
        info["note"] = None if info["available"] else tr("Matches the checkout.")
        if detail and info["available"]:
            info["changes"] = checkout_changes(m["source"], m["installed_at"])
        return info
    if m.get("source"):  # git or URL: nothing to compare against, reinstalling fetches it again
        info["note"] = tr("Installed from {source}; updating reinstalls from there.", source=m["source"])
        return info
    if remote:
        fetch_latest()
    error = re.fullmatch(r"Could not reach PyPI \((\w+)\)", _remote.get("error") or "")  # kept in English: say it here
    info.update(latest=_remote.get("latest"), checked_at=_remote.get("checked_at"),
                error=tr(PYPI_ERROR, error=error.group(1)) if error else _remote.get("error"))
    # an agents-chronicle install moves to interlatch at any version, the one it runs included
    info["available"] = bool(info["latest"]) and (info["move"] or _vkey(info["latest"]) > _vkey(__version__))
    if info["move"]:
        info["note"] = tr(MOVE_NOTE)
    if info["available"]:
        info["notes_url"] = f"https://github.com/Chatixia-AI/interlatch/releases/tag/v{info['latest']}"
    if m["kind"] == "app":
        info["note"] = tr("Download the new version and drag it into Applications.")
    if m["kind"] == "container":
        info["note"] = tr("Pull the new image and recreate the container: docker compose pull && docker compose up -d")
    return info


PYPI_ERROR = "Could not reach PyPI ({error})"
MOVE_NOTE = ("Chronicle is now Interlatch. Updating moves this install from the agents-chronicle package to "
             "interlatch; the chronicle command keeps working.")


PYPI_TRIES = 3


def fetch_latest() -> None:
    """Ask PyPI for the latest release (the network call).

    PyPI's JSON sits behind a CDN that caches it for 15 minutes, and a release's purge reaches its cache servers one
    by one; each request can land on a different one. So until one answer is newer than this copy, ask again (up to
    PYPI_TRIES) and keep the newest: right after a release, a single answer is often the old version."""
    latest = None
    for _ in range(PYPI_TRIES):
        try:
            req = Request(PYPI_JSON, headers={"User-Agent": f"interlatch/{__version__}", "Accept": "application/json"})
            with urlopen(req, timeout=8) as r:
                version = json.load(r)["info"]["version"]
        except Exception as exc:  # offline, proxy, PyPI down: shown on the page, unless an earlier try answered
            if latest is None:
                _remote.update(checked_at=time.time(), error=PYPI_ERROR.format(error=exc.__class__.__name__))
                return
            break
        latest = max(latest or version, version, key=_vkey)
        if _vkey(latest) > _vkey(__version__):
            break
    _remote.update(latest=latest, checked_at=time.time(), error=None)


def compares_online() -> bool:
    """This install learns about updates from PyPI (not a checkout, a git URL or a source tree)."""
    m = install_method()
    return m["kind"] != "source" and not m.get("source")


def recall(conn) -> None:
    """Load the last PyPI check from the database, when it is newer than the one in memory."""
    from .db import kv_get

    try:
        saved = json.loads(kv_get(conn, REMOTE_KEY) or "null")
    except ValueError:
        return
    if isinstance(saved, dict) and (saved.get("checked_at") or 0) > (_remote.get("checked_at") or 0):
        _remote.clear()
        _remote.update(saved)


def remember(conn) -> None:
    from .db import kv_set

    kv_set(conn, REMOTE_KEY, json.dumps(_remote))
    conn.commit()


def check_due(now: float | None = None) -> bool:
    """A daily check is due: a day after the last answer, or an hour after a failed one."""
    if not compares_online():
        return False
    last = _remote.get("checked_at") or 0
    return (now or time.time()) - last >= (3600 if _remote.get("error") else DAY)


def available() -> dict | None:
    """For the status bar: the update on offer, from checks already made (never touches the network).

    {"to": a version, or "build" for a changed checkout of the same version, "key": what the dashboard's
    notification remembers a dismissal by}. A checkout's key is its commit, so editing files does not notify
    again and again; a new commit does."""
    try:
        info = check()
    except Exception:
        return None
    if not info["available"]:
        return None
    if info["move"]:
        return {"to": info["latest"], "key": f"move:{info['latest']}"}
    if _vkey(info["latest"]) > _vkey(__version__):
        return {"to": info["latest"], "key": info["latest"]}
    head = (_git(info["source"], "rev-parse", "--short", "HEAD") or "").strip() if info["source"] else ""
    return {"to": "build", "key": f"build:{head or int(install_method().get('installed_at') or 0)}"}


def available_version() -> str | None:
    """The version to update to ("build" for a changed checkout of the same version), or None."""
    a = available()
    return a["to"] if a else None


def run_update(progress) -> str:
    """Upgrade with the install's own tool; returns a line for the UI. Restarts the dashboard when it can."""
    m = dict(install_method())  # a copy: each step clears what install_method() remembers
    steps = m.get("steps") or ([m["command"]] if m["command"] else [])
    if not steps:
        raise RuntimeError(f"Interlatch installed as {m['label']} cannot update itself")
    progress("moving to the interlatch package…" if m.get("move") else "updating Interlatch…")
    from . import install  # noqa: F401  restart() needs it, and a move deletes this environment's files

    for step in steps:
        r = subprocess.run(step, capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)
        out = (r.stdout + r.stderr).strip()
        _method.clear()  # a reinstall rewrites the receipt
        if r.returncode:
            raise RuntimeError(out.splitlines()[-1] if out else f"{step[0]} exited with {r.returncode}")
    if m.get("move"):
        if not _which("interlatch"):  # a restart would start nothing: launchd's agent runs that command
            raise RuntimeError(f"the move left no interlatch command; run {shlex.join(steps[-1])} in a terminal")
        new = _new_version() or "?"
    else:
        new = subprocess.run([sys.executable, "-c", f"import importlib.metadata as m; print(m.version({DIST!r}))"],
                             capture_output=True, text=True).stdout.strip() or "?"
    if RESTARTABLE:
        threading.Timer(3.0, restart).start()  # after the next status poll has seen this job finish
        return f"Updated to Interlatch {new}; the dashboard is restarting"
    return f"Updated to Interlatch {new}; quit and reopen Interlatch to use it"


def _new_version() -> str | None:
    """The version the `interlatch` command now runs (after a move this environment is gone)."""
    exe = _which("interlatch")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    return out[-1] if out else None


def restart() -> None:
    """Start the dashboard afresh; open tabs reload themselves when they see it back (or a new build).

    The macOS login item asks launchd for a new process (`launchctl kickstart -k`): a process that re-executes itself
    in place keeps its pid, and macOS then keeps its menu-bar icon hidden. Anything else replaces this process with a
    fresh copy of itself (the pid stays, so a systemd unit is undisturbed; the listening socket is not inherited).
    After the move from agents-chronicle this environment is gone: the fresh copy is the `interlatch` command's."""
    from .install import LEGACY_LABELS, UI_LABEL

    sys.stdout.flush()
    sys.stderr.flush()
    label = os.environ.get("XPC_SERVICE_NAME")
    if sys.platform == "darwin" and label in (UI_LABEL, LEGACY_LABELS[UI_LABEL]):
        try:  # its own session, so launchd stopping this agent's processes doesn't stop launchctl too
            subprocess.Popen(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            log.warning("launchctl kickstart failed; restarting in place")
    if not Path(sys.executable).exists() and (exe := _which("interlatch")):
        os.execv(exe, [exe, *sys.argv[1:]])
    os.execv(sys.executable, sys.orig_argv)
