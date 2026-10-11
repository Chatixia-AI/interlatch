"""Install/uninstall Interlatch into Claude Code (hooks, MCP server) and the background agents: launchd on macOS,
systemd user units on Linux (for a hub that runs on a Linux box), Task Scheduler on Windows (windows.py)."""

from __future__ import annotations

import json
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Config
from .i18n import tr
from .util import utcnow_iso

LAUNCHD_LABEL = "com.interlatch.sync"
UI_LABEL = "com.interlatch.ui"
# the agents' labels before the rename to Interlatch: installing one of ours takes its old one away (migrate.py)
LEGACY_LABELS = {LAUNCHD_LABEL: "com.claude-chronicle.sync", UI_LABEL: "com.claude-chronicle.ui"}
HOOK_MARKER = " hook "
COMMANDS = ("interlatch", "chronicle")  # the command, and its name before the rename (still installed as an alias)
MCP_NAME = "interlatch"
LEGACY_MCP_NAME = "chronicle"
APP_BUNDLE_ID = "com.interlatch.app"
LEGACY_BUNDLE_ID = "io.github.kayeungadrian-tam.chronicle"
APP_EXECUTABLES = ("Interlatch", "Chronicle")  # Contents/MacOS/<name> in Interlatch.app, and in Chronicle.app
WINDOWS_TASKS = {LAUNCHD_LABEL: "Sync", UI_LABEL: "Dashboard"}  # in Task Scheduler's \\Interlatch\\ folder


def shim_path() -> Path:
    from .config import chronicle_home

    return chronicle_home() / "bin" / "interlatch"


def write_shim(target: str) -> Path:
    """A stable `interlatch` command that runs the app's bundled executable.

    Hooks and MCP registrations store a command path, and the app's own path changes when it is moved or
    updated, so they point here instead. The app rewrites this on every launch; if the app has moved since,
    the shim looks it up by bundle id with Spotlight (Interlatch.app's, then Chronicle.app's), in the Applications
    folders only: an app anywhere else (a download, a mounted disk image) can claim the same bundle id. Chronicle.app
    wrote bin/chronicle: that gets the same text, so whatever still runs it runs the new app (a copy, not a link: a
    Chronicle.app still opening at login rewrites it, and must not write through a link into this one).
    """
    text = (
        "#!/bin/sh\n"
        "# Written by Interlatch.app on every launch. Claude Code hooks and MCP servers run this.\n"
        f"exe={shlex.quote(target)}\n"
        f'[ -x "$exe" ] || for id in {APP_BUNDLE_ID} {LEGACY_BUNDLE_ID}; do\n'
        '  app="$(mdfind "kMDItemCFBundleIdentifier == \'$id\'" | grep -E "^(/Applications|$HOME/Applications)/[^/]+\\.app$" '
        '| head -n 1)"\n'
        f'  for name in {" ".join(APP_EXECUTABLES)}; do [ -n "$app" ] && [ -x "$app/Contents/MacOS/$name" ] '
        '&& exe="$app/Contents/MacOS/$name" && break 2; done\n'
        "done\n"
        'exec "$exe" "$@"\n'
    )
    path = shim_path()
    old = path.with_name("chronicle")
    for shim in (path, old) if old.is_file() and not old.is_symlink() else (path,):
        if not shim.exists() or shim.read_text() != text:
            shim.parent.mkdir(parents=True, exist_ok=True)
            shim.write_text(text)
            shim.chmod(0o755)
    return path


def executable() -> str:
    """Absolute path of the installed `interlatch` command, or `chronicle` (its alias), else python -m chronicle.

    A PATH entry outside this environment (~/.local/bin/interlatch) wins: it survives `uv tool install --force`, and
    the move from the agents-chronicle package, whose environment goes away, keeps ~/.local/bin/chronicle."""
    if getattr(sys, "frozen", False):  # inside Interlatch.app
        return str(write_shim(sys.executable))
    found = [str(Path(f).absolute() if not on_windows() else Path(f).resolve()) for f in map(shutil.which, COMMANDS) if f]
    outside = [f for f in found if not f.startswith(os.path.join(sys.prefix, ""))]
    if outside or found:
        return (outside or found)[0]
    argv0 = Path(sys.argv[0])
    if argv0.name in COMMANDS and argv0.exists():
        return str(argv0.resolve())
    return f"{shlex.quote(sys.executable)} -m chronicle"


def exe_argv(exe: str) -> list[str]:
    """`exe` (executable(), or install's --exe) as argv: a program's path, which may have spaces in it, or a command
    line (python -m chronicle). On Windows a backslash is a path's, not an escape."""
    if Path(exe).is_file():
        return [exe]
    return shlex.split(exe.replace("\\", "/") if on_windows() else exe)


def _exe_cmd(exe: str, *args: str) -> str:
    """The command line Claude Code runs (a hook, the status line)."""
    if on_windows():
        from .windows import shell_command

        return shell_command([*exe_argv(exe), *args])
    base = exe if " -m " in exe else shlex.quote(exe)
    return " ".join([base, *args])


def _is_ours(hook: dict) -> bool:
    """A hook Interlatch added: its command runs `interlatch hook …`, or the `chronicle hook …` of before the rename
    (through either command, the app's shim in ~/.interlatch or ~/.claude-chronicle, or python -m chronicle)."""
    cmd = hook.get("command") or ""
    return any(name in cmd for name in COMMANDS) and HOOK_MARKER in f" {cmd} "


def settings_path(cfg: Config) -> Path:
    return (cfg.claude_dirs[0] if cfg.claude_dirs else Path("~/.claude").expanduser()) / "settings.json"


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text()
    return json.loads(text) if text.strip() else {}


def _backup(cfg: Config, path: Path) -> Path | None:
    if not path.exists():
        return None
    dest = cfg.home / "backups" / f"{path.name}.{utcnow_iso().replace(':', '')}.bak"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return dest


def hooks_installed(cfg: Config) -> dict[str, bool]:
    try:
        settings = _load_settings(settings_path(cfg))
    except (OSError, ValueError):
        return {}
    found = {}
    for event, groups in (settings.get("hooks") or {}).items():
        for group in groups or []:
            if any(_is_ours(h) for h in group.get("hooks") or []):
                found[event] = True
    return found


def install_hooks(cfg: Config, exe: str, *, inject: bool, dry_run: bool = False) -> list[str]:
    path = settings_path(cfg)
    settings = _load_settings(path)
    hooks = settings.setdefault("hooks", {})
    wanted = {"SessionEnd": _exe_cmd(exe, "hook", "session-end")}
    if inject:
        wanted["SessionStart"] = _exe_cmd(exe, "hook", "session-start")
    actions = []
    # drop any previous hooks of ours first (Chronicle's too) so paths stay current
    for event in list(hooks):
        groups = []
        for group in hooks[event] or []:
            kept = [h for h in group.get("hooks") or [] if not _is_ours(h)]
            if kept:
                groups.append({**group, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    for event, command in wanted.items():
        hooks.setdefault(event, []).append({"hooks": [{"type": "command", "command": command, "timeout": 15}]})
        actions.append(f"hook {event}: {command}")
    if not hooks:
        settings.pop("hooks", None)
    new_text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    if not dry_run and (not path.exists() or path.read_text() != new_text):
        backup = _backup(cfg, path)
        if backup:
            actions.append(tr("backed up {path} -> {backup}", path=path, backup=backup))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text)
    return actions


def uninstall_hooks(cfg: Config) -> list[str]:
    path = settings_path(cfg)
    if not path.exists():
        return []
    settings = _load_settings(path)
    hooks = settings.get("hooks") or {}
    removed = 0
    for event in list(hooks):
        groups = []
        for group in hooks[event] or []:
            kept = [h for h in group.get("hooks") or [] if not _is_ours(h)]
            removed += len(group.get("hooks") or []) - len(kept)
            if kept:
                groups.append({**group, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    if removed:
        _backup(cfg, path)
        path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    return [tr("removed {n} hook(s) from {path}", n=removed, path=path)] if removed else []


# ------------------------------------------------------------------ status line (optional)
def statusline_installed(cfg: Config) -> bool:
    from .statusline import is_ours

    try:
        return is_ours(_load_settings(settings_path(cfg)).get("statusLine"))
    except (OSError, ValueError):
        return False


def install_statusline(cfg: Config, exe: str, *, dry_run: bool = False) -> list[str]:
    """Point Claude Code's status line at `interlatch statusline`, which records usage and then runs the status
    line the user already had (saved to statusline/wrapped.json) so it looks exactly as before."""
    from .statusline import is_ours, wrapped_path

    path = settings_path(cfg)
    settings = _load_settings(path)
    current = settings.get("statusLine")
    actions = []
    if isinstance(current, dict) and not is_ours(current) and current.get("command"):
        actions.append(f"keeps your status line: runs `{current['command']}` after recording")
        if not dry_run:
            wrapped_path().parent.mkdir(parents=True, exist_ok=True)
            wrapped_path().write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")
    elif not is_ours(current):
        actions.append("no status line before: shows model, context and plan limits "
                       "(Claude Code then hides most footer key hints)")
        if not dry_run:
            wrapped_path().unlink(missing_ok=True)
    base = current if isinstance(current, dict) else {}
    keep = {k: v for k, v in base.items() if k in ("padding", "refreshInterval")}
    settings["statusLine"] = {"type": "command", "command": _exe_cmd(exe, "statusline"), **keep}
    actions.append(f"statusLine: {settings['statusLine']['command']}")
    new_text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    if not dry_run and (not path.exists() or path.read_text() != new_text):
        backup = _backup(cfg, path)
        if backup:
            actions.append(tr("backed up {path} -> {backup}", path=path, backup=backup))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text)
    return actions


def uninstall_statusline(cfg: Config) -> list[str]:
    """Give the status line back: the user's own command if Interlatch wrapped one, else none."""
    from .statusline import is_ours, wrapped_path

    path = settings_path(cfg)
    if not path.exists():
        return []
    settings = _load_settings(path)
    if not is_ours(settings.get("statusLine")):
        return []
    try:
        original = json.loads(wrapped_path().read_text())
    except (OSError, ValueError):
        original = None
    if isinstance(original, dict) and original.get("command"):
        settings["statusLine"] = original
        done = f"restored your status line `{original['command']}` in {path}"
    else:
        settings.pop("statusLine", None)
        done = f"removed the Interlatch status line from {path}"
    _backup(cfg, path)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    wrapped_path().unlink(missing_ok=True)
    return [done]


# ------------------------------------------------------------------ systemd (Linux)
SYSTEMD_UNITS = {LAUNCHD_LABEL: ("interlatch-sync.timer", "interlatch-sync.service"), UI_LABEL: ("interlatch-ui.service",)}
LEGACY_SYSTEMD_UNITS = {LAUNCHD_LABEL: ("chronicle-sync.timer", "chronicle-sync.service"),  # Chronicle's
                        UI_LABEL: ("chronicle-ui.service",)}


def uses_systemd() -> bool:
    return platform.system() == "Linux" and shutil.which("systemctl") is not None


def on_windows() -> bool:
    return platform.system() == "Windows"


def background_supported() -> bool:
    """Whether `interlatch install` can keep the sync and dashboard running from login here."""
    return platform.system() == "Darwin" or uses_systemd() or on_windows()


def systemd_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "systemd" / "user"


def _sd_quote(arg: str) -> str:
    arg = arg.replace("%", "%%")
    if not arg or any(c in arg for c in ' \t"\'\\;$'):
        return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return arg


def systemd_units(cfg: Config, exe: str, *, interval: int = 900) -> dict[str, str]:
    """The unit files for the background sync (a timer) and the always-on dashboard."""
    cmd = exe_argv(exe)
    env = "\n".join(f"Environment={_sd_quote(f'{k}={v}')}" for k, v in _agent_env(cfg).items())
    run = lambda *args: " ".join(_sd_quote(a) for a in [*cmd, *args])  # noqa: E731
    return {
        "interlatch-sync.service": (
            "[Unit]\nDescription=Interlatch: archive, ingest and analyze coding-agent sessions\n\n"
            f"[Service]\nType=oneshot\nExecStart={run('sync', '--work', '--quiet')}\n{env}\nNice=10\n"
            "IOSchedulingClass=idle\n"),
        "interlatch-sync.timer": (
            "[Unit]\nDescription=Interlatch: sync every few minutes\n\n"
            f"[Timer]\nOnBootSec=2min\nOnUnitActiveSec={interval}s\n\n[Install]\nWantedBy=timers.target\n"),
        "interlatch-ui.service": (
            "[Unit]\nDescription=Interlatch dashboard\n\n"
            f"[Service]\nExecStart={run('ui')}\n{env}\nRestart=always\nRestartSec=30\n\n"
            "[Install]\nWantedBy=default.target\n"),
    }


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def _systemd_install(cfg: Config, exe: str, names: tuple[str, ...], enable: str, interval: int) -> str | None:
    units = systemd_units(cfg, exe, interval=interval)
    d = systemd_dir()
    d.mkdir(parents=True, exist_ok=True)
    for name in names:
        (d / name).write_text(units[name])
    _systemctl("daemon-reload")
    proc = _systemctl("enable", "--now", enable)
    if proc.returncode != 0:
        return proc.stderr.strip() or f"exit {proc.returncode}"
    if enable.endswith(".service"):
        _systemctl("restart", enable)  # pick up a changed ExecStart
    return None


def _systemd_status(label: str) -> dict:
    """The units' state; while only Chronicle's are installed (until `interlatch migrate`), theirs."""
    units = SYSTEMD_UNITS[label]
    if not (systemd_dir() / units[0]).exists() and (systemd_dir() / LEGACY_SYSTEMD_UNITS[label][0]).exists():
        units = LEGACY_SYSTEMD_UNITS[label]
    info = {"installed": (systemd_dir() / units[0]).exists(),
            "loaded": _systemctl("is-active", units[0]).stdout.strip() == "active"}
    show = _systemctl("show", units[-1], "-p", "ExecMainStatus", "-p", "NRestarts").stdout
    for line in show.splitlines():
        key, _, value = line.partition("=")
        if key == "ExecMainStatus" and info["installed"]:
            info["last_exit"] = value
    return info


# ------------------------------------------------------------------ Task Scheduler (Windows)
def _task_python(exe: str) -> Path:
    """The python that a task runs `-m chronicle` with: the one in `exe` when that is `python -m chronicle`, else this
    process's (the uv tool's environment, which `interlatch install` runs in)."""
    argv = exe_argv(exe)
    return Path(argv[0]) if argv[1:3] == ["-m", "chronicle"] else Path(sys.executable)


def _windows_install(cfg: Config, exe: str, label: str, args: list[str], *, every_minutes: int, keep_alive: bool,
                     description: str, dry_run: bool) -> str | None:
    """Register the task for `label` that runs `interlatch <args>` without a window; an error message, or None."""
    from . import windows

    python = _task_python(exe)
    pythonw = next((p for p in (python.with_name("pythonw.exe"), python) if p.is_file()), python)
    claude = cfg.claude_bin()
    log = cfg.logs_dir / ("ui" if label == UI_LABEL else "sync")
    arguments = windows.runner_arguments(args, log=log, home=cfg.home, path_dirs=[str(Path(claude).parent)] if claude else [])
    if dry_run:
        return None
    xml = windows.task_xml(description=description, command=str(pythonw), arguments=arguments,
                           every_minutes=every_minutes, keep_alive=keep_alive)
    return windows.install_task(WINDOWS_TASKS[label], xml)


# ------------------------------------------------------------------ launchd
def plist_path(label: str = LAUNCHD_LABEL) -> Path:
    return Path("~/Library/LaunchAgents").expanduser() / f"{label}.plist"


def _agent_env(cfg: Config) -> dict:
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    claude = cfg.claude_bin()
    for extra in ([str(Path(claude).parent)] if claude else []) + [str(Path(sys.executable).parent), "/usr/bin", "/bin"]:
        if extra not in path_dirs:
            path_dirs.append(extra)
    return {"PATH": os.pathsep.join(p for p in path_dirs if p), "HOME": str(Path.home()), "INTERLATCH_HOME": str(cfg.home)}


def _bootstrap(label: str, plist: dict) -> str | None:
    path = plist_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
    with open(path, "wb") as fh:
        plistlib.dump(plist, fh)
    proc = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
    return None if proc.returncode == 0 else proc.stderr.strip() or f"exit {proc.returncode}"


def _own_units() -> set[str]:
    """The systemd units this process runs in (from its cgroup), on Linux."""
    try:
        return {part for line in Path("/proc/self/cgroup").read_text().splitlines() for part in line.split("/")}
    except OSError:
        return set()


def legacy_agent_is_self(label: str) -> bool:
    """This process is the agent Chronicle installed for `label`'s job: removing that agent stops this process."""
    if uses_systemd():
        return bool(_own_units() & set(LEGACY_SYSTEMD_UNITS.get(label, ())))
    return os.environ.get("XPC_SERVICE_NAME") == LEGACY_LABELS.get(label)


def legacy_agent_installed(label: str) -> bool:
    if uses_systemd():
        return any((systemd_dir() / u).exists() for u in LEGACY_SYSTEMD_UNITS.get(label, ()))
    return platform.system() == "Darwin" and label in LEGACY_LABELS and plist_path(LEGACY_LABELS[label]).exists()


def retire_legacy_agent(label: str) -> list[str]:
    """Take away the agent Chronicle installed for what `label` does now (launchd LEGACY_LABELS, systemd
    LEGACY_SYSTEMD_UNITS), once ours is in place. When this process is that agent, stopping it stops this process:
    the command that stops it runs in a session of its own, so the agent goes all the same."""
    if not legacy_agent_installed(label):
        return []
    if uses_systemd():
        units = LEGACY_SYSTEMD_UNITS[label]
        _systemctl("disable", *units)
        for u in units:
            (systemd_dir() / u).unlink(missing_ok=True)
        _systemctl("daemon-reload")
        cmd, done = ["systemctl", "--user", "stop", *units], f"removed Chronicle's systemd unit {units[0]}"
    else:
        old = LEGACY_LABELS[label]
        plist_path(old).unlink(missing_ok=True)
        cmd, done = ["launchctl", "bootout", f"gui/{os.getuid()}/{old}"], f"removed Chronicle's launchd agent {old}"
    if legacy_agent_is_self(label):
        subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    else:
        subprocess.run(cmd, capture_output=True)
    return [done]


def install_ui_agent(cfg: Config, exe: str, *, dry_run: bool = False, retire: bool = True) -> list[str]:
    """Keep the dashboard running at http://127.0.0.1:<port> (restarted by launchd or systemd if it exits). With
    `retire`, the agent Chronicle installed for it before the rename goes (retire_legacy_agent)."""
    url = f"http://127.0.0.1:{cfg.server_port}/"
    if uses_systemd():
        if dry_run:
            return [f"systemd user unit {systemd_dir() / 'interlatch-ui.service'} serving {url}"]
        err = _systemd_install(cfg, exe, ("interlatch-ui.service",), "interlatch-ui.service", 900)
        if err:
            return [f"dashboard agent failed: {err}"]
        return [f"dashboard always available at {url}"] + (retire_legacy_agent(UI_LABEL) if retire else [])
    if on_windows():
        task = f"Task Scheduler task \\Interlatch\\{WINDOWS_TASKS[UI_LABEL]}"
        err = _windows_install(cfg, exe, UI_LABEL, ["ui"], every_minutes=5, keep_alive=True, dry_run=dry_run,
                               description=f"Interlatch dashboard at {url}")
        if dry_run:
            return [f"{task} serving {url}"]
        return [f"dashboard agent failed: {err}"] if err else [f"dashboard always available at {url} ({task})"]
    if platform.system() != "Darwin":
        return ["dashboard agent skipped (no launchd or systemd); run `interlatch ui` yourself"]
    program = exe_argv(exe) + ["ui"]
    if dry_run:
        return [f"dashboard agent {plist_path(UI_LABEL)} serving {url}"]
    err = _bootstrap(UI_LABEL, {
        "Label": UI_LABEL,
        "ProgramArguments": program,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "EnvironmentVariables": _agent_env(cfg),
        "StandardOutPath": str(cfg.logs_dir / "ui.out.log"),
        "StandardErrorPath": str(cfg.logs_dir / "ui.err.log"),
    })
    if err:
        return [f"dashboard agent failed: {err}"]
    return [f"dashboard always available at {url}"] + (retire_legacy_agent(UI_LABEL) if retire else [])


def install_launchd(cfg: Config, exe: str, *, interval: int = 900, dry_run: bool = False, retire: bool = True) -> list[str]:
    """The background sync every `interval` seconds: a launchd agent on macOS, a systemd user timer on Linux. With
    `retire`, Chronicle's agent for it goes (retire_legacy_agent)."""
    if uses_systemd():
        if dry_run:
            return [f"systemd user timer {systemd_dir() / 'interlatch-sync.timer'} running sync every {interval}s"]
        err = _systemd_install(cfg, exe, ("interlatch-sync.service", "interlatch-sync.timer"), "interlatch-sync.timer",
                               interval)
        if err:
            return [f"systemd timer failed: {err}"]
        return ([f"systemd timer interlatch-sync.timer runs `interlatch sync --work` every {interval // 60} min"]
                + (retire_legacy_agent(LAUNCHD_LABEL) if retire else []))
    if on_windows():
        task = f"Task Scheduler task \\Interlatch\\{WINDOWS_TASKS[LAUNCHD_LABEL]}"
        err = _windows_install(cfg, exe, LAUNCHD_LABEL, ["sync", "--work", "--quiet"], every_minutes=max(1, interval // 60),
                               keep_alive=False, dry_run=dry_run,
                               description="Interlatch: archive, ingest and analyze coding-agent sessions")
        if dry_run:
            return [f"{task} running `interlatch sync --work --quiet` every {max(1, interval // 60)} min"]
        return ([f"Task Scheduler failed: {err}"] if err
                else [f"{task} runs `interlatch sync --work` every {max(1, interval // 60)} min"])
    if platform.system() != "Darwin":
        return ["background sync skipped (no launchd or systemd); schedule `interlatch sync --work` with cron instead"]
    program = exe_argv(exe) + ["sync", "--work", "--quiet"]
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": program,
        "StartInterval": interval,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "EnvironmentVariables": _agent_env(cfg),
        "StandardOutPath": str(cfg.logs_dir / "launchd.out.log"),
        "StandardErrorPath": str(cfg.logs_dir / "launchd.err.log"),
    }
    if dry_run:
        return [f"launchd agent {plist_path()} running {' '.join(program)} every {interval}s"]
    err = _bootstrap(LAUNCHD_LABEL, plist)
    if err:
        return [f"launchd bootstrap failed: {err}"]
    return ([f"launchd agent {LAUNCHD_LABEL} runs `{' '.join(program)}` every {interval // 60} min"]
            + (retire_legacy_agent(LAUNCHD_LABEL) if retire else []))


def uninstall_launchd(labels=(LAUNCHD_LABEL, UI_LABEL)) -> list[str]:
    """Remove these agents, and the ones Chronicle installed for the same jobs before the rename."""
    out = []
    if uses_systemd():
        for units in [u for label in labels for u in (SYSTEMD_UNITS.get(label), LEGACY_SYSTEMD_UNITS.get(label)) if u]:
            if not any((systemd_dir() / u).exists() for u in units):
                continue
            _systemctl("disable", "--now", units[0])
            for u in units:
                (systemd_dir() / u).unlink(missing_ok=True)
            out.append(f"removed systemd unit {units[0]}")
        if out:
            _systemctl("daemon-reload")
        return out
    if on_windows():
        from .windows import delete_task

        return [f"removed Task Scheduler task \\Interlatch\\{WINDOWS_TASKS[label]}" for label in labels
                if label in WINDOWS_TASKS and delete_task(WINDOWS_TASKS[label])]
    if platform.system() != "Darwin":
        return out
    for label in [x for wanted in labels for x in (wanted, LEGACY_LABELS.get(wanted)) if x]:
        path = plist_path(label)
        if not path.exists():
            continue
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
        path.unlink(missing_ok=True)
        out.append(f"removed launchd agent {label}")
    return out


def active_label(label: str) -> str:
    """`label`, or Chronicle's label for the same agent while only that one is installed (until `interlatch migrate`)."""
    old = LEGACY_LABELS.get(label)
    return old if old and not plist_path(label).exists() and plist_path(old).exists() else label


def launchd_status(label: str = LAUNCHD_LABEL) -> dict:
    """The agent's state; until `interlatch migrate` replaces it, that of the agent Chronicle installed for the job."""
    if uses_systemd() and label in SYSTEMD_UNITS:
        return _systemd_status(label)
    if on_windows() and label in WINDOWS_TASKS:
        from .windows import task_status

        return task_status(WINDOWS_TASKS[label])
    if platform.system() != "Darwin":
        return {"installed": False}
    label = active_label(label)
    proc = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, text=True)
    info = {"installed": plist_path(label).exists(), "loaded": proc.returncode == 0}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("last exit code"):
            info["last_exit"] = line.split("=", 1)[-1].strip()
        elif line.startswith("runs ="):
            info["runs"] = line.split("=", 1)[-1].strip()
    return info


def background_status() -> dict:
    """The background sync agent's state, whichever service manager runs it."""
    return launchd_status(LAUNCHD_LABEL)


def agent_path(label: str) -> Path | str:
    """Where the agent for `label` is defined: its unit or plist file, or on Windows its task's name."""
    if on_windows():
        from .windows import task_name

        return task_name(WINDOWS_TASKS[label])
    return systemd_dir() / SYSTEMD_UNITS[label][0] if uses_systemd() else plist_path(label)


# ------------------------------------------------------------------ MCP
def _claude_json() -> Path:
    return Path("~/.claude.json").expanduser()


def mcp_servers() -> dict:
    """Claude Code's user-level MCP servers (~/.claude.json)."""
    try:
        return json.loads(_claude_json().read_text()).get("mcpServers") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def mcp_registered() -> bool:
    """Our MCP server is registered in Claude Code, under its name or as Chronicle's 'chronicle' (it runs the same)."""
    servers = mcp_servers()
    return MCP_NAME in servers or LEGACY_MCP_NAME in servers


def install_mcp(cfg: Config, exe: str, *, dry_run: bool = False) -> list[str]:
    """Register the MCP server in Claude Code (user scope), in place of Chronicle's 'chronicle' when that is there."""
    claude = cfg.claude_bin()
    if not claude:
        return [tr("MCP registration skipped: claude CLI not found")]
    args = exe_argv(exe) + ["mcp"]
    cmd = [claude, "mcp", "add", "--scope", "user", "--transport", "stdio", MCP_NAME, "--", *args]
    if dry_run:
        return ["would run: " + " ".join(shlex.quote(c) for c in cmd)]
    from .hooks import internal_env

    legacy = LEGACY_MCP_NAME in mcp_servers()
    for name in (MCP_NAME, LEGACY_MCP_NAME) if legacy else (MCP_NAME,):
        subprocess.run([claude, "mcp", "remove", "--scope", "user", name], capture_output=True, text=True)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=internal_env())
    if proc.returncode != 0:
        return [tr("MCP registration failed: {error}", error=(proc.stderr or proc.stdout).strip()[:300])]
    return [tr("registered MCP server '{name}' (user scope)", name=MCP_NAME)] + (
        [tr("removed MCP server '{name}'", name=LEGACY_MCP_NAME)] if legacy else [])


def uninstall_mcp(cfg: Config) -> list[str]:
    claude = cfg.claude_bin()
    servers = mcp_servers()
    out = []
    for name in (MCP_NAME, LEGACY_MCP_NAME):
        if not claude or name not in servers:
            continue
        proc = subprocess.run([claude, "mcp", "remove", "--scope", "user", name], capture_output=True, text=True)
        out.append(tr("removed MCP server '{name}'", name=name) if proc.returncode == 0
                   else tr("MCP removal failed: {error}", error=proc.stderr.strip()[:200]))
    return out
