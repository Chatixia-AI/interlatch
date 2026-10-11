"""Chronicle -> Interlatch: move ~/.claude-chronicle to ~/.interlatch and point everything that runs us at the new names.

`interlatch migrate` runs it by hand (--dry-run shows what it would do). `interlatch ui`, `interlatch sync`, `install`
and the app run it as they start when it is due (due()): the data folder is the default one, ~/.claude-chronicle is
there and ~/.interlatch is not, or a move did not get to its end. A folder named by INTERLATCH_HOME or CHRONICLE_HOME
is never moved; one of them naming ~/.claude-chronicle itself, as every launchd agent written before the rename does,
counts as the default. Every step is idempotent: running it again changes nothing. What it did goes to
logs/migrate.log.

The move is one rename within the home folder, so a dashboard or background sync that has the database open while it
happens keeps working: open files follow a rename (SQLite's -wal and -shm files move with the database, and its locks
are on the files, not their names). A link left at ~/.claude-chronicle keeps working what still names the old folder:
the background agents until they are replaced, paths the database keeps (rewritten too, _rewrite_paths), an older
Chronicle still running. Copying instead would split a live database, so a move that can't be a rename (another
volume) is not made.

Then, each where it is set up: Claude Code's hooks and status line, the MCP server in every agent that has it
(Claude Code, Codex, VS Code, Copilot CLI, IBM Bob, Antigravity, Claude Desktop, Cursor, Windsurf, Gemini CLI), now
named interlatch with the settings the old entry had, Claude Code permission rules naming mcp__chronicle__ tools, the
markers of the block in managed CLAUDE.md / AGENTS.md files, and the background agents (launchd, or systemd user units
on Linux), last: one of them may be the process doing this. A Chronicle.app left in Applications is reported, never
deleted.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import plistlib
import re
import sqlite3
import sys
import time
import tomllib
from pathlib import Path

from .config import Config, chronicle_home, default_home, home_override, legacy_home, load_config
from .install import LEGACY_BUNDLE_ID, LEGACY_MCP_NAME, MCP_NAME

log = logging.getLogger("interlatch.migrate")

MARKER = "migration.json"  # in the moved folder: {"from", "moved_at", "done"}; due() until done
LOCK_WAIT_S = 60
APP_DIRS = (Path("/Applications"), Path("~/Applications"))  # where a Chronicle.app would be
_TOOL = re.compile(rf"^mcp__{LEGACY_MCP_NAME}(?=__|$)")  # a Claude Code permission rule for one of our tools, or all
_CODEX_TABLE = re.compile(rf'^(\s*\[\s*mcp_servers\.)(?:{LEGACY_MCP_NAME}|"{LEGACY_MCP_NAME}")(\s*\]|\.[^\]]*\])(.*)$')


# ---------------------------------------------------------------- when
def _marker(home: Path) -> dict:
    try:
        data = json.loads((home / MARKER).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def due() -> bool:
    """The commands that start the dashboard or a sync migrate first: the data folder is the default one (no
    INTERLATCH_HOME / CHRONICLE_HOME naming another), and ~/.claude-chronicle is still to move, or a move did not
    finish (the process doing it was stopped)."""
    if home_override() is not None:
        return False
    new = default_home()
    if not os.path.lexists(new):
        return legacy_home().is_dir()
    marker = _marker(new)
    return bool(marker) and not marker.get("done")


def migrate_if_due(*, quiet: bool = True) -> list[str]:
    """migrate() when due(); never raises, so the command it runs before carries on whatever happens."""
    try:
        if not due():
            return []
        lines = migrate(auto=True)
    except Exception as exc:  # the archive stays where it was; the next start tries again
        log.exception("moving Chronicle's folder to Interlatch's failed")
        lines = [f"could not finish moving to Interlatch: {exc} (run `interlatch migrate` to see why)"]
    if lines and not quiet:
        for line in lines:
            print(f"• {line}", file=sys.stderr)
    return lines


@contextlib.contextmanager
def _lock(wait: float = LOCK_WAIT_S):
    """One process migrates at a time. The lock is on the folder both homes are in (the user's home folder): a file
    inside the one being moved would move away from under the next process, and nothing is left behind."""
    fd = os.open(str(legacy_home().parent), os.O_RDONLY)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.2)
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------- the whole of it
def migrate(*, dry_run: bool = False, auto: bool = False) -> list[str]:
    """Move the folder and update every integration; returns what it did (or would do), one line each."""
    with _lock() as got:
        if not got:
            return ["another Interlatch process is moving the data folder; try again in a minute"]
        if auto and not due():  # another process did it while this one waited
            return []
        lines = _move_home(dry_run)
        home = chronicle_home()
        cfg = load_config(home, create=False)
        exe = migration_exe(cfg)
        for step in (_hooks, _statusline, _claude_mcp, _codex_mcp, _json_mcp, _permissions, _instruction_markers):
            try:
                lines += step(cfg, exe, dry_run)
            except Exception as exc:  # one integration's trouble doesn't keep the others from moving
                log.exception("migrate: %s failed", step.__name__)
                lines.append(f"{step.__name__.strip('_').replace('_', ' ')}: failed ({exc})")
        lines += _old_apps()
        agent_lines, retire = _agents(cfg, exe, dry_run)
        lines += agent_lines
        from .install import legacy_agent_is_self, retire_legacy_agent

        own = [label for label in retire if legacy_agent_is_self(label)]
        for label in [label for label in retire if label not in own]:
            lines += retire_legacy_agent(label)
        if own:  # this process is Chronicle's agent: removing it stops this process, so the record is made first
            lines.append(f"removed Chronicle's background agent {legacy_agent_name(own[0])} (the one doing this)")
        if not dry_run:
            _finish(home, lines)
        for label in own:
            retire_legacy_agent(label)
    return lines


def _finish(home: Path, lines: list[str]) -> None:
    """Mark the move done and keep a record of what it did (logs/migrate.log)."""
    marker = _marker(home)
    if marker and not marker.get("done"):
        _write_marker(home, {**marker, "done": True})
    if lines and home.is_dir():
        try:
            (home / "logs").mkdir(exist_ok=True)
            with open(home / "logs" / "migrate.log", "a") as fh:
                fh.writelines(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n" for line in lines)
        except OSError:
            pass


def _write_marker(home: Path, data: dict) -> None:
    tmp = home / f"{MARKER}.tmp{os.getpid()}"
    tmp.write_text(json.dumps(data) + "\n")
    os.replace(tmp, home / MARKER)


def migration_exe(cfg: Config) -> str:
    """The command the integrations run from now on: this install's (install.executable()). Hooks that ran
    Chronicle.app's shim keep running an app's shim, from the new folder: Interlatch.app rewrites it to run itself."""
    from .install import _is_ours, _load_settings, executable, settings_path

    if getattr(sys, "frozen", False):
        return executable()
    try:
        settings = _load_settings(settings_path(cfg))
    except (OSError, ValueError):
        settings = {}
    shims = {str(home / "bin" / name) for home in (legacy_home(), cfg.home) for name in ("chronicle", "interlatch")}
    for groups in (settings.get("hooks") or {}).values():
        for group in groups or []:
            for hook in group.get("hooks") or []:
                first = (hook.get("command") or "").split(" ")[0]
                if _is_ours(hook) and first in shims:
                    name = "interlatch" if (cfg.home / "bin" / "interlatch").exists() else "chronicle"
                    return str(cfg.home / "bin" / name)
    return executable()


# ---------------------------------------------------------------- the folder
def _move_home(dry_run: bool) -> list[str]:
    override = home_override()
    if override is not None:
        return [f"data folder: {override} is set by INTERLATCH_HOME or CHRONICLE_HOME, so it stays where it is"]
    new, old = default_home(), legacy_home()
    if os.path.lexists(new):
        if old.is_dir() and not old.is_symlink():
            return [f"data folder: {old} and {new} both exist; Interlatch uses {new} and left {old} alone"]
        if old.is_symlink() and not dry_run:
            _rewrite_paths(new / "chronicle.db", old, new)  # rows an older Chronicle wrote through the link since
        return []
    if not old.is_dir():
        return []
    if dry_run:
        return [f"would move {old} to {new}, leaving a link at {old}"]
    try:
        os.rename(old, new)  # the same folder: one rename, which open files follow
    except OSError as exc:  # e.g. ~/.claude-chronicle is a mount point
        return [f"could not move {old} to {new} ({exc}); it stays where it is"]
    _write_marker(new, {"from": str(old), "moved_at": time.time(), "done": False})
    lines = [f"moved {old} to {new}"]
    try:
        os.symlink(new.name, old)
        lines[0] += f" (a link at {old} keeps old paths working)"
    except OSError as exc:
        lines.append(f"could not leave a link at {old}: {exc}")
    n = _rewrite_paths(new / "chronicle.db", old, new)
    if n:
        lines.append(f"the database now names {new} in {n} paths")
    return lines


def _rewrite_paths(db: Path, old: Path, new: Path) -> int:
    """Paths the database keeps inside the folder, under its new name: archived transcripts and imported chats, and on
    a hub the files other computers sent (a sync matches those by path, and would take them all for new ones)."""
    if not db.exists():
        return 0
    a, b = f"{old}/", f"{new}/"
    conn = sqlite3.connect(db, timeout=30)
    try:
        n = 0
        for table, columns in (("sessions", ("transcript_path", "archive_path", "claude_dir")),
                               ("files_state", ("path", "archive_path"))):
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col in (c for c in columns if c in have):
                n += conn.execute(f"UPDATE OR IGNORE {table} SET {col} = ? || substr({col}, ?) "
                                  f"WHERE substr({col}, 1, ?) = ?", (b, len(a) + 1, len(a), a)).rowcount
            if "path" in have and table == "files_state":  # the new path's row was there already
                conn.execute("DELETE FROM files_state WHERE substr(path, 1, ?) = ?", (len(a), a))
        conn.commit()
        return n
    finally:
        conn.close()


# ---------------------------------------------------------------- Claude Code
def _our_hooks(cfg: Config) -> dict[str, str]:
    from .install import _is_ours, _load_settings, settings_path

    try:
        settings = _load_settings(settings_path(cfg))
    except (OSError, ValueError):
        return {}
    return {event: h["command"] for event, groups in (settings.get("hooks") or {}).items() for group in groups or []
            for h in group.get("hooks") or [] if _is_ours(h)}


def _hooks(cfg: Config, exe: str, dry_run: bool) -> list[str]:
    from .install import _exe_cmd, install_hooks, settings_path

    current = _our_hooks(cfg)
    if not current:
        return []
    inject = "SessionStart" in current
    wanted = {"SessionEnd": _exe_cmd(exe, "hook", "session-end"),
              **({"SessionStart": _exe_cmd(exe, "hook", "session-start")} if inject else {})}
    if current == wanted:
        return []
    if not dry_run:
        install_hooks(cfg, exe, inject=inject)
    return [f"Claude Code hooks ({settings_path(cfg)}): {'would run' if dry_run else 'now run'} {exe}"]


def _statusline(cfg: Config, exe: str, dry_run: bool) -> list[str]:
    from .install import _exe_cmd, _load_settings, install_statusline, settings_path
    from .statusline import is_ours

    try:
        current = _load_settings(settings_path(cfg)).get("statusLine")
    except (OSError, ValueError):
        return []
    if not is_ours(current) or current.get("command") == _exe_cmd(exe, "statusline"):
        return []
    if not dry_run:
        install_statusline(cfg, exe)
    return [f"Claude Code status line: {'would run' if dry_run else 'now runs'} {_exe_cmd(exe, 'statusline')}"]


def _claude_mcp(cfg: Config, exe: str, dry_run: bool) -> list[str]:
    from .install import install_mcp, mcp_servers

    if LEGACY_MCP_NAME not in mcp_servers():
        return []
    if dry_run:
        return [f"Claude Code: would register the MCP server '{MCP_NAME}' in place of '{LEGACY_MCP_NAME}'"]
    return [f"Claude Code: {line}" for line in install_mcp(cfg, exe)]


def _claude_dirs(cfg: Config) -> list[Path]:
    return list(dict.fromkeys(cfg.claude_dirs or [Path("~/.claude").expanduser()]))


def _projects(cfg: Config) -> list[Path]:
    """Folders Claude Code sessions ran in, from the archive."""
    if not cfg.db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True, timeout=30)
    try:
        rows = conn.execute("SELECT DISTINCT project_path FROM sessions WHERE agent = 'claude' "
                            "AND project_path LIKE '/%'").fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [Path(r[0]) for r in rows if Path(r[0]).is_dir()]


def rename_rules(rules) -> list | None:
    """Permission rules with mcp__chronicle__… renamed mcp__interlatch__… (and mcp__chronicle, the whole server);
    None when there is none. Every other rule stays as it is; a renamed one already there is not added twice."""
    if not isinstance(rules, list) or not any(isinstance(r, str) and _TOOL.match(r) for r in rules):
        return None
    out: list = []
    for r in rules:
        if isinstance(r, str) and _TOOL.match(r):
            r = f"mcp__{MCP_NAME}" + r[len(f"mcp__{LEGACY_MCP_NAME}"):]
            if r in rules or r in out:
                continue
        out.append(r)
    return out


def _permissions(cfg: Config, exe: str, dry_run: bool) -> list[str]:
    """Claude Code permission rules (allow, ask, deny) naming our tools by the old server name: in the user's settings
    and each project's own settings.local.json. A project's settings.json is shared with the people working on it,
    who may still run Chronicle: it is only reported."""
    from .install import _backup

    files = [d / name for d in _claude_dirs(cfg) for name in ("settings.json", "settings.local.json")]
    projects = _projects(cfg)
    files += [p / ".claude" / "settings.local.json" for p in projects]
    lines = []
    for path in dict.fromkeys(Path(os.path.realpath(f)) for f in files):
        try:
            settings = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        perms = settings.get("permissions") if isinstance(settings, dict) else None
        if not isinstance(perms, dict):
            continue
        changed = {k: new for k in ("allow", "ask", "deny") if (new := rename_rules(perms.get(k))) is not None}
        if not changed:
            continue
        if not dry_run:
            settings["permissions"] = {**perms, **changed}
            _backup(cfg, path)
            tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
            tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
            os.replace(tmp, path)
        lines.append(f"Claude Code permissions ({path}): {'would rename' if dry_run else 'renamed'} "
                     f"mcp__{LEGACY_MCP_NAME}__ rules to mcp__{MCP_NAME}__")
    for path in (p / ".claude" / "settings.json" for p in projects):
        try:
            if f'"mcp__{LEGACY_MCP_NAME}' in path.read_text():
                lines.append(f"{path} (shared with the project) still allows mcp__{LEGACY_MCP_NAME}__ tools: rename "
                             f"them mcp__{MCP_NAME}__ there when everyone has moved to Interlatch")
        except OSError:
            continue
    return lines


# ---------------------------------------------------------------- MCP in the other agents
def _codex_mcp(cfg: Config, exe: str, dry_run: bool) -> list[str]:
    """Codex keeps its MCP servers as [mcp_servers.<name>] tables in config.toml: rename ours (and its sub-tables),
    keeping its settings and the file's layout, and point its command at this install when it is written on one line."""
    from .connectors import _split_exe, _tilde, codex_home

    command, args = _split_exe(exe)
    lines = []
    for home in dict.fromkeys([*cfg.codex_dirs, codex_home()]):
        path = home / "config.toml"
        try:
            text = path.read_text()
            servers = tomllib.loads(text).get("mcp_servers") or {}
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if LEGACY_MCP_NAME not in servers:
            continue
        if MCP_NAME in servers:
            lines.append(f"Codex ({_tilde(path)}): has both '{LEGACY_MCP_NAME}' and '{MCP_NAME}' MCP servers; remove "
                         f"'{LEGACY_MCP_NAME}' by hand")
            continue
        out, ours, simple = [], False, {}
        for i, line in enumerate(text.splitlines(keepends=True)):
            end = line[len(line.rstrip("\r\n")):]
            m = _CODEX_TABLE.match(line.rstrip("\r\n"))
            if m:
                line = f"{m.group(1)}{MCP_NAME}{m.group(2)}{m.group(3)}{end}"
                ours = m.group(2).strip() == "]"
            elif line.lstrip().startswith("["):
                ours = False
            elif ours and (k := re.match(r"\s*(command|args)\s*=", line)):
                value = line.split("=", 1)[1].split("#", 1)[0].strip()
                if k.group(1) == "command" or (value.startswith("[") and value.endswith("]")):
                    simple[k.group(1)] = i
            out.append(line)
        if set(simple) == {"command", "args"}:  # both on a line of their own: this install's command
            for key, value in (("command", command), ("args", args)):
                line = out[simple[key]]
                out[simple[key]] = f"{key} = {json.dumps(value)}{line[len(line.rstrip(chr(13) + chr(10))):]}"
        new_text = "".join(out)
        try:
            renamed = tomllib.loads(new_text).get("mcp_servers") or {}
        except tomllib.TOMLDecodeError:
            renamed = {}
        if MCP_NAME not in renamed or LEGACY_MCP_NAME in renamed:
            lines.append(f"Codex ({_tilde(path)}): could not rename the '{LEGACY_MCP_NAME}' MCP server; run "
                         f"`codex mcp remove {LEGACY_MCP_NAME}`, then `interlatch connect codex`")
            continue
        if not dry_run:
            _write_text(cfg, path, new_text, "codex")
        lines.append(f"Codex ({_tilde(path)}): {'would rename' if dry_run else 'renamed'} the MCP server "
                     f"'{LEGACY_MCP_NAME}' to '{MCP_NAME}'")
    return lines


def _write_text(cfg: Config, path: Path, text: str, label: str) -> None:
    import shutil

    backup = cfg.home / "backups" / f"{path.name}.{int(time.time())}.{label}.bak"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, path)


def json_mcp_configs(cfg: Config) -> list[tuple[Path, str, str]]:
    """(file, key of its server map, label) for every JSON MCP config connectors.py writes ours into."""
    from . import connectors as c

    found = [(u / "mcp.json", "servers", "VS Code") for u in c.vscode_user_dirs()]
    for d in dict.fromkeys([*cfg.copilot_dirs, c.copilot_home()]):
        found += [(d / "mcp-config.json", "mcpServers", "Copilot CLI"), (d / "mcp.json", "servers", "VS Code")]
    for home in dict.fromkeys([*cfg.bob_dirs, c.bob_home()]):
        found += [(f, "mcpServers", "IBM Bob") for f in c.bob_mcp_files(home)]
    for home in dict.fromkeys([*cfg.antigravity_dirs, c.antigravity_home()]):
        found.append((c.antigravity_mcp_config(home), "mcpServers", "Antigravity"))
    found += [(c.mcp_client_config(name), "mcpServers", client["label"]) for name, client in c.MCP_CLIENTS.items()]
    seen, out = set(), []
    for path, key, label in found:
        if path not in seen:
            seen.add(path)
            out.append((path, key, label))
    return out


def _json_mcp(cfg: Config, exe: str, dry_run: bool) -> list[str]:
    from .connectors import _tilde, _write_json, mcp_server_entry, rename_server

    lines = []
    for path, key, label in json_mcp_configs(cfg):
        try:
            text = path.read_text()
        except OSError:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            if f'"{LEGACY_MCP_NAME}"' in text:
                lines.append(f"{label} ({_tilde(path)}): not plain JSON; rename the '{LEGACY_MCP_NAME}' MCP server "
                             f"'{MCP_NAME}' by hand")
            continue
        servers = data.get(key) if isinstance(data, dict) else None
        if not isinstance(servers, dict) or LEGACY_MCP_NAME not in servers:
            continue
        if not dry_run:
            data[key] = rename_server(servers, mcp_server_entry(exe))
            _write_json(cfg, path, data, label)
        lines.append(f"{label} ({_tilde(path)}): {'would rename' if dry_run else 'renamed'} the MCP server "
                     f"'{LEGACY_MCP_NAME}' to '{MCP_NAME}'")
    return lines


# ---------------------------------------------------------------- CLAUDE.md / AGENTS.md
def managed_instruction_files(cfg: Config) -> list[Path]:
    """The user-level CLAUDE.md and AGENTS.md, and every file a suggestion was written to or proposed for."""
    from .instructions import claude_home, codex_home

    paths = [claude_home(cfg) / "CLAUDE.md", codex_home(cfg) / "AGENTS.md"]
    if cfg.db_path.exists():
        conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True, timeout=30)
        try:
            paths += [Path(r[0]) for r in conn.execute(
                "SELECT DISTINCT target_path FROM suggestions WHERE kind = 'instruction' AND target_path IS NOT NULL")]
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return list(dict.fromkeys(paths))


def _instruction_markers(cfg: Config, exe: str, dry_run: bool) -> list[str]:
    from .instructions import LEGACY_BEGIN, MalformedBlock, relabel, write_atomic

    lines = []
    for path in managed_instruction_files(cfg):
        try:
            text = path.read_text()
        except OSError:
            continue
        if LEGACY_BEGIN not in text:
            continue
        try:
            new = relabel(text)
        except MalformedBlock as exc:
            lines.append(f"{path}: {exc}; its markers were left as they are")
            continue
        if new == text:
            continue
        if not dry_run:
            write_atomic(path, new, cfg=cfg, mkdir=False)
        lines.append(f"{path}: {'would mark' if dry_run else 'marked'} its block interlatch (was chronicle)")
    return lines


# ---------------------------------------------------------------- the app and the background agents
def _old_apps() -> list[str]:
    lines = []
    for folder in APP_DIRS:
        app = folder.expanduser() / "Chronicle.app"
        try:
            with open(app / "Contents" / "Info.plist", "rb") as fh:
                bundle = plistlib.load(fh).get("CFBundleIdentifier")
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        if bundle == LEGACY_BUNDLE_ID:
            lines.append(f"{app} can be deleted: Interlatch replaces it (its Open at Login item goes with it)")
    return lines


def _legacy_interval() -> int:
    """How often Chronicle's background sync ran, in seconds."""
    from .install import LAUNCHD_LABEL, LEGACY_LABELS, LEGACY_SYSTEMD_UNITS, plist_path, systemd_dir, uses_systemd

    try:
        if uses_systemd():
            m = re.search(r"OnUnitActiveSec=(\d+)s", (systemd_dir() / LEGACY_SYSTEMD_UNITS[LAUNCHD_LABEL][0]).read_text())
            return int(m.group(1)) if m else 900
        with open(plist_path(LEGACY_LABELS[LAUNCHD_LABEL]), "rb") as fh:
            return int(plistlib.load(fh).get("StartInterval") or 900)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return 900


def legacy_agent_name(label: str) -> str:
    from .install import LEGACY_LABELS, LEGACY_SYSTEMD_UNITS, uses_systemd

    return LEGACY_SYSTEMD_UNITS[label][0] if uses_systemd() else LEGACY_LABELS[label]


def _agents(cfg: Config, exe: str, dry_run: bool) -> tuple[list[str], list[str]]:
    """Interlatch's background agents in place of Chronicle's (launchd on macOS, systemd user units on Linux): (lines,
    the labels whose old agent is to go once ours runs)."""
    from .install import LAUNCHD_LABEL, LEGACY_LABELS, install_launchd, install_ui_agent, legacy_agent_installed

    lines: list[str] = []
    retire: list[str] = []
    for label in LEGACY_LABELS:
        if not legacy_agent_installed(label):
            continue
        if dry_run:
            lines.append(f"would replace Chronicle's background agent {legacy_agent_name(label)} with Interlatch's")
            continue
        done = (install_launchd(cfg, exe, interval=_legacy_interval(), retire=False) if label == LAUNCHD_LABEL
                else install_ui_agent(cfg, exe, retire=False))
        lines += done
        if not any("failed" in line for line in done):  # Chronicle's stays when ours could not start
            retire.append(label)
    return lines, retire
