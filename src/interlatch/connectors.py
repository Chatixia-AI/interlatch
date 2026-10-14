"""Sources ("connectors"): which coding agents Interlatch records, and how each one is wired up."""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import time
import tomllib
from pathlib import Path

from .config import Config, set_config_value
from .hooks import internal_env
from .i18n import tr
from .install import LEGACY_MCP_NAME, MCP_NAME, exe_argv

MCP_NAMES = (MCP_NAME, LEGACY_MCP_NAME)  # an agent that still has Chronicle's 'chronicle' server runs the same one

_VERSION_CACHE: dict[str, tuple[float, str | None]] = {}


def _version(binary: str | None) -> str | None:
    if not binary:
        return None
    hit = _VERSION_CACHE.get(binary)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10,
                             env=internal_env()).stdout.strip().splitlines()
        version = out[0] if out else None
    except (OSError, subprocess.SubprocessError):
        version = None
    _VERSION_CACHE[binary] = (time.time(), version)
    return version


def _tilde(p: Path | str) -> str:
    s = str(p)
    home = str(Path.home())
    return "~" + s[len(home):] if s.startswith(home) else s


def _recorded(conn: sqlite3.Connection, where: str, params=()) -> dict:
    r = conn.execute(f"SELECT COUNT(*), MAX(started_at), SUM(analysis_status = 'done') FROM sessions WHERE {where}", params).fetchone()
    return {"sessions": r[0] or 0, "last_session": r[1], "analyzed": r[2] or 0}


# ------------------------------------------------------------------ Claude Code
def claude_status(cfg: Config, conn: sqlite3.Connection) -> dict:
    from .install import LAUNCHD_LABEL, hooks_installed, launchd_status, mcp_registered, uses_systemd

    binary = cfg.claude_bin()
    dirs = cfg.claude_dirs
    default_dir = Path("~/.claude").expanduser()
    scan_dirs = dirs or [default_dir]
    on_disk = sum(len(list(d.glob("projects/*/*.jsonl"))) for d in scan_dirs if (d / "projects").is_dir())
    hooks = hooks_installed(cfg)
    rec = _recorded(conn, "agent = 'claude' AND source = 'transcript'")
    recovered = _recorded(conn, "agent = 'claude' AND source IN ('history', 'codex-import')")
    connected = bool(dirs)
    sync = launchd_status(LAUNCHD_LABEL)
    return {
        "name": "claude",
        "label": "Claude Code",
        "vendor": "Anthropic",
        "detected": bool(binary) or default_dir.is_dir(),
        "connected": connected,
        "version": _version(binary),
        "binary": binary,
        "dirs": [_tilde(d) for d in scan_dirs],
        "on_disk": on_disk,
        "recorded": rec,
        "recovered": recovered["sessions"],
        "recording": (tr("SessionEnd hook + background sync every 15 min") if hooks.get("SessionEnd")
                      else tr("background sync every 15 min")) if connected else tr("not recording"),
        "checks": [
            {"key": "files", "label": tr("Transcripts"), "ok": on_disk > 0,
             "detail": tr("{where} · {n} on disk", where=", ".join(_tilde(d) + "/projects" for d in scan_dirs),
                          n=on_disk)},
            {"key": "recording", "label": tr("Recording"), "ok": connected,
             "detail": tr("scanned every sync") if connected else tr("not scanned (disconnected)")},
            {"key": "hook", "label": tr("SessionEnd hook"), "ok": bool(hooks.get("SessionEnd")),
             "detail": tr("archives and analyzes each session as it ends")},
            {"key": "mcp", "label": tr("MCP server in Claude Code"), "ok": mcp_registered(),
             "detail": tr("Claude can search your sessions and knowledge")},
            {"key": "inject", "label": tr("Knowledge injection"), "ok": bool(hooks.get("SessionStart")), "optional": True,
             "detail": tr("SessionStart hook adds the project's knowledge base to new sessions")},
            {"key": "sync", "label": tr("Background sync"), "ok": bool(sync.get("loaded")),
             "detail": tr("{manager}, every 15 minutes", manager="systemd" if uses_systemd() else "launchd")},
        ],
        "notes": [tr("{n} older sessions recovered (prompt history / Codex imports)", n=recovered["sessions"])]
        if recovered["sessions"] else [],
    }


def connect_claude(cfg: Config, exe: str) -> list[str]:
    from .install import install_hooks, install_mcp

    actions = []
    if not cfg.claude_dirs:
        set_config_value(cfg, "sources", "claude_dirs", '["~/.claude"]')
        actions.append(tr("recording {where}", where="~/.claude"))
    actions += install_hooks(cfg, exe, inject=cfg.inject_session_start)
    actions += install_mcp(cfg, exe)
    return actions


def disconnect_claude(cfg: Config) -> list[str]:
    from .install import uninstall_hooks, uninstall_mcp

    set_config_value(cfg, "sources", "claude_dirs", "[]")
    return ([tr("stopped recording {label} (recorded sessions are kept)", label="Claude Code")] + uninstall_hooks(cfg)
            + uninstall_mcp(cfg))


# ------------------------------------------------------------------ Codex
def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def codex_mcp_registered(home: Path | None = None) -> bool:
    try:
        data = tomllib.loads(((home or codex_home()) / "config.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return any(name in (data.get("mcp_servers") or {}) for name in MCP_NAMES)


def _codex_notify(home: Path) -> list | None:
    try:
        return tomllib.loads((home / "config.toml").read_text()).get("notify")
    except (OSError, tomllib.TOMLDecodeError):
        return None


def codex_status(cfg: Config, conn: sqlite3.Connection) -> dict:
    from .codex_parser import load_imports, rollout_files

    binary = cfg.codex_bin()
    home = cfg.codex_dirs[0] if cfg.codex_dirs else codex_home()
    files = rollout_files(home)
    imports = load_imports(home)
    connected = bool(cfg.codex_dirs)
    rec = _recorded(conn, "agent = 'codex' AND source != 'codex-cloud'")
    recovered = _recorded(conn, "source = 'codex-import'")
    notify = _codex_notify(home)
    notify_owner = Path(notify[0]).name if isinstance(notify, list) and notify else None
    mcp = codex_mcp_registered(home)
    return {
        "name": "codex",
        "label": "Codex",
        "vendor": "OpenAI",
        "detected": bool(binary) or (home / "sessions").is_dir(),
        "connected": connected,
        "version": _version(binary),
        "binary": binary,
        "dirs": [_tilde(home)],
        "on_disk": len(files),
        "recorded": rec,
        "recovered": recovered["sessions"],
        "imports": len(imports),
        "recording": tr("background sync every 15 min") if connected else tr("not recording"),
        "checks": [
            {"key": "files", "label": tr("Rollouts"), "ok": bool(files),
             "detail": tr("{where} · {n} on disk", where=f"{_tilde(home)}/sessions", n=len(files))},
            {"key": "recording", "label": tr("Recording"), "ok": connected,
             "detail": tr("scanned every sync") if connected else tr("not scanned (connect to start)")},
            {"key": "hook", "label": tr("Session-end hook"), "ok": None, "optional": True,
             "detail": tr("not available: Codex allows one notify program and it is used by {owner}; sessions are picked "
                          "up by the 15-minute sync", owner=notify_owner) if notify_owner
             else tr("Codex has no session-end hook; sessions are picked up by the 15-minute sync")},
            {"key": "mcp", "label": tr("MCP server in Codex"), "ok": mcp,
             "detail": tr("Codex can search your sessions and knowledge (Claude's too)")},
            {"key": "imports", "label": tr("Claude sessions imported by Codex"), "ok": None, "optional": True,
             "detail": tr("{n} in Codex's import registry; {recovered} recovered into the vault", n=len(imports),
                          recovered=recovered["sessions"])
                       + (tr(" (the rest are duplicates of transcripts already recorded)") if imports else "")},
        ],
        "notes": [],
    }


def connect_codex(cfg: Config, exe: str) -> list[str]:
    home = codex_home()
    actions = []
    if not cfg.codex_dirs:
        set_config_value(cfg, "sources", "codex_dirs", f'["{_tilde(home)}"]')
        actions.append(tr("recording {where}", where=_tilde(home)))
    binary = cfg.codex_bin()
    if not binary:
        return actions + [tr("MCP registration skipped: codex CLI not found")]
    if not codex_mcp_registered(home):
        cmd = [binary, "mcp", "add", MCP_NAME, "--", *exe_argv(exe), "mcp"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        actions.append(tr("registered MCP server 'interlatch' in Codex") if proc.returncode == 0
                       else tr("Codex MCP registration failed: {error}", error=(proc.stderr or proc.stdout).strip()[:300]))
    return actions


def disconnect_codex(cfg: Config) -> list[str]:
    set_config_value(cfg, "sources", "codex_dirs", "[]")
    actions = [tr("stopped recording {label} (recorded sessions are kept)", label="Codex")]
    binary = cfg.codex_bin()
    try:
        servers = tomllib.loads((codex_home() / "config.toml").read_text()).get("mcp_servers") or {}
    except (OSError, tomllib.TOMLDecodeError):
        servers = {}
    for name in [n for n in MCP_NAMES if n in servers] if binary else []:
        proc = subprocess.run([binary, "mcp", "remove", name], capture_output=True, text=True, timeout=60)
        actions.append((tr("removed MCP server 'interlatch' from Codex") if name == MCP_NAME
                       else tr("removed MCP server '{name}'", name=name)) if proc.returncode == 0
                       else tr("Codex MCP removal failed: {error}", error=(proc.stderr or proc.stdout).strip()[:200]))
    return actions


# ------------------------------------------------------------------ Codex Cloud
def codex_cloud_status(cfg: Config, conn: sqlite3.Connection) -> dict:
    from .codex_cloud import load_status

    binary = cfg.codex_bin()
    connected = cfg.codex_cloud
    last = load_status(conn)
    rec = _recorded(conn, "source = 'codex-cloud'")
    checked = time.strftime("%b %d %H:%M", time.localtime(last["checked_at"])) if last.get("checked_at") else None
    listing = (tr("{n} tasks at the last sync ({when})", n=last.get("tasks", 0), when=checked) if last.get("ok")
               else tr("failed at the last sync ({when}): {error}", when=checked, error=last.get("error")) if checked
               else tr("not listed yet"))
    return {
        "name": "codex-cloud",
        "agent": "codex",
        "label": "Codex Cloud",
        "vendor": "OpenAI",
        "detected": bool(binary),
        "connected": connected,
        "version": _version(binary),
        "binary": binary,
        "dirs": [],
        "on_disk": last.get("tasks", 0) if last.get("ok") else 0,
        "on_disk_label": tr("in the cloud"),
        "recorded": rec,
        "recovered": 0,
        "recording": tr("task list and diffs every sync, through the codex CLI") if connected else tr("not recording"),
        "checks": [
            {"key": "cli", "label": "Codex CLI", "ok": bool(binary),
             "detail": tr("lists tasks with your Codex login (`codex login`)") if binary
             else tr("not found: install Codex and run `codex login`")},
            {"key": "recording", "label": tr("Recording"), "ok": connected,
             "detail": tr("checked every sync") if connected else tr("not checked (connect to start)")},
            {"key": "files", "label": tr("Task list"), "ok": bool(last.get("ok")) if checked else None,
             "optional": not connected, "detail": listing},
            {"key": "conversation", "label": tr("Conversation"), "ok": None, "optional": True,
             "detail": tr("not available from the CLI: each task is recorded with its title, repository, changed files "
                          "and diff")},
        ],
        "notes": [],
    }


def connect_codex_cloud(cfg: Config, exe: str) -> list[str]:
    set_config_value(cfg, "sources", "codex_cloud", "true")
    actions = [tr("recording Codex Cloud tasks")]
    if not cfg.codex_bin():
        actions.append(tr("codex CLI not found: install Codex and run `codex login`"))
    return actions


def disconnect_codex_cloud(cfg: Config) -> list[str]:
    set_config_value(cfg, "sources", "codex_cloud", "false")
    return [tr("stopped recording Codex Cloud (recorded tasks are kept)")]


# ------------------------------------------------------------------ MCP entries in other tools' JSON configs
def _mcp_json_has(path: Path, key: str) -> bool:
    try:
        servers = json.loads(path.read_text()).get(key) or {}
        return any(name in servers for name in MCP_NAMES)
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def _mcp_json_set(cfg: Config, path: Path, key: str, entry: dict | None, label: str) -> str:
    return _mcp_json_write(cfg, path, key, entry, label)[0]


def _mcp_json_write(cfg: Config, path: Path, key: str, entry: dict | None, label: str) -> tuple[str, bool]:
    """Add (entry) or remove (None) our server in a JSON MCP config, keeping everything else: (what happened, whether
    the file changed). Adding replaces Chronicle's 'chronicle' server in its place; removing takes either. The file is
    backed up first; a file that is not plain JSON (comments) is left alone."""
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        return tr("{label}: {path} is not plain JSON; add the 'interlatch' MCP server by hand", label=label,
                  path=_tilde(path)), False
    if not isinstance(data, dict) or not isinstance(data.setdefault(key, {}), dict):
        return tr("{label}: unexpected {path} format; left unchanged", label=label, path=_tilde(path)), False
    servers = data[key]
    if entry is None:
        if not any(name in servers for name in MCP_NAMES):
            return tr("{label}: no interlatch MCP server to remove", label=label), False
        for name in MCP_NAMES:
            servers.pop(name, None)
    else:
        if servers.get(MCP_NAME) == entry and LEGACY_MCP_NAME not in servers:
            return tr("{label}: interlatch MCP server already registered", label=label), False
        data[key] = rename_server(servers, entry)
    _write_json(cfg, path, data, label)
    return (tr("{label}: removed MCP server 'interlatch' ({path})", label=label, path=_tilde(path))
            if entry is None else
            tr("{label}: registered MCP server 'interlatch' ({path})", label=label, path=_tilde(path))), True


def rename_server(servers: dict, entry: dict) -> dict:
    """The server map with ours set to `entry` and Chronicle's 'chronicle' gone; ours takes the old one's place in the
    order, and keeps the settings the old one had besides its command (Bob's alwaysAllow, an env, ...)."""
    old = servers.get(LEGACY_MCP_NAME)
    merged = {**entry, **old, **{k: entry[k] for k in ("command", "args") if k in entry}} if isinstance(old, dict) else entry
    if LEGACY_MCP_NAME not in servers:
        return {**servers, MCP_NAME: merged}
    return {(MCP_NAME if k == LEGACY_MCP_NAME else k): (merged if k == LEGACY_MCP_NAME else v)
            for k, v in servers.items() if k != MCP_NAME}


def _write_json(cfg: Config, path: Path, data: dict, label: str) -> None:
    """Back the file up into the backups folder, then replace it."""
    if path.exists():
        backup = cfg.home / "backups" / f"{path.name}.{int(time.time())}.{re.sub(r'[^a-z]+', '-', label.lower())}.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _split_exe(exe: str) -> tuple[str, list[str]]:
    parts = exe_argv(exe)
    return parts[0], [*parts[1:], "mcp"]


def app_support_dir() -> Path:
    """Where this system keeps apps' settings: ~/Library/Application Support, %APPDATA%, or ~/.config."""
    import sys

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


# ------------------------------------------------------------------ GitHub Copilot
def copilot_home() -> Path:
    return Path(os.environ.get("COPILOT_HOME", "~/.copilot")).expanduser()


def vscode_user_dirs() -> list[Path]:
    base = app_support_dir()
    return [d for d in (base / "Code" / "User", base / "Code - Insiders" / "User") if d.is_dir()]


def _copilot_extension_version() -> str | None:
    exts = sorted(Path("~/.vscode/extensions").expanduser().glob("github.copilot-chat-*"))
    return exts[-1].name.removeprefix("github.copilot-chat-") if exts else None


def copilot_status(cfg: Config, conn: sqlite3.Connection) -> dict:
    from .copilot_parser import agent_session_dirs, chat_files

    home = next((d for d in cfg.copilot_dirs if (d / "session-state").is_dir()), copilot_home())
    users = [d for d in cfg.copilot_dirs if (d / "workspaceStorage").is_dir()] or vscode_user_dirs()
    agent_sessions = agent_session_dirs(home)
    chats = [f for u in users for f in chat_files(u)]
    connected = bool(cfg.copilot_dirs)
    binary = shutil.which("copilot")
    ext = _copilot_extension_version()
    rec = _recorded(conn, "agent = 'copilot'")
    mcp_vscode = any(_mcp_json_has(u / "mcp.json", "servers") for u in users)
    mcp_cli = _mcp_json_has(home / "mcp-config.json", "mcpServers")
    return {
        "name": "copilot",
        "label": "GitHub Copilot",
        "vendor": "GitHub",
        "detected": home.is_dir() or bool(ext) or bool(binary),
        "connected": connected,
        "version": ext and f"Copilot Chat {ext}",
        "binary": binary,
        "dirs": [_tilde(home), *(_tilde(u) for u in users)],
        "on_disk": len(agent_sessions) + len(chats),
        "recorded": rec,
        "recovered": 0,
        "recording": tr("background sync every 15 min") if connected else tr("not recording"),
        "checks": [
            {"key": "files", "label": tr("Copilot agent sessions"), "ok": bool(agent_sessions),
             "detail": tr("{where} · {n} (Copilot CLI and VS Code agent host)", where=f"{_tilde(home)}/session-state",
                          n=len(agent_sessions))},
            {"key": "chats", "label": tr("Copilot Chat in VS Code"), "ok": bool(chats),
             "detail": tr("{n} chat logs in VS Code workspace storage (empty chat panels are skipped)", n=len(chats))},
            {"key": "recording", "label": tr("Recording"), "ok": connected,
             "detail": tr("scanned every sync") if connected else tr("not scanned (connect to start)")},
            {"key": "mcp", "label": tr("MCP server in VS Code"), "ok": mcp_vscode,
             "detail": tr("Copilot Chat can search your sessions and knowledge")},
            {"key": "mcp", "label": tr("MCP server in Copilot CLI"), "ok": mcp_cli, "optional": True,
             "detail": f"{_tilde(home)}/mcp-config.json"},
            {"key": "hook", "label": tr("Session-end hook"), "ok": None, "optional": True,
             "detail": tr("not used: sessions are picked up by the 15-minute sync")},
        ],
        "notes": [tr("Copilot is billed per seat; costs shown are API list-price estimates where token splits are known")],
    }


def connect_copilot(cfg: Config, exe: str) -> list[str]:
    actions = []
    home = copilot_home()
    dirs = [d for d in (home, *vscode_user_dirs()) if d.is_dir()]
    if not cfg.copilot_dirs and dirs:
        set_config_value(cfg, "sources", "copilot_dirs", json.dumps([_tilde(d) for d in dirs], ensure_ascii=False))
        actions.append(tr("recording {where}", where=", ".join(_tilde(d) for d in dirs)))
    command, args = _split_exe(exe)
    for u in vscode_user_dirs():
        actions.append(_mcp_json_set(cfg, u / "mcp.json", "servers", {"type": "stdio", "command": command, "args": args}, "VS Code"))
    if home.is_dir():
        actions.append(_mcp_json_set(cfg, home / "mcp-config.json", "mcpServers",
                                     {"type": "local", "command": command, "args": args, "tools": ["*"]}, "Copilot CLI"))
    return actions


def disconnect_copilot(cfg: Config) -> list[str]:
    set_config_value(cfg, "sources", "copilot_dirs", "[]")
    actions = [tr("stopped recording {label} (recorded sessions are kept)", label="GitHub Copilot")]
    for u in vscode_user_dirs():
        if _mcp_json_has(u / "mcp.json", "servers"):
            actions.append(_mcp_json_set(cfg, u / "mcp.json", "servers", None, "VS Code"))
    if _mcp_json_has(copilot_home() / "mcp-config.json", "mcpServers"):
        actions.append(_mcp_json_set(cfg, copilot_home() / "mcp-config.json", "mcpServers", None, "Copilot CLI"))
    return actions


# ------------------------------------------------------------------ IBM Bob
def bob_home() -> Path:
    return Path(os.environ.get("BOB_HOME", "~/.bob")).expanduser()


def bob_mcp_files(home: Path) -> tuple[Path, Path]:
    """Bob's global MCP configs: mcp.json (Bob 2.x, which Bob Shell reads) and mcp_settings.json (older Bob IDE).
    Bob 2.x copies mcp_settings.json to mcp.json once, only while mcp.json does not exist yet."""
    settings = home / "settings"
    return settings / "mcp.json", settings / "mcp_settings.json"


def _bob_mcp_registered(home: Path) -> bool:
    current, legacy = bob_mcp_files(home)
    if current.exists():
        return _mcp_json_has(current, "mcpServers")
    return _mcp_json_has(legacy, "mcpServers")  # Bob copies it to mcp.json on its next start


def bob_status(cfg: Config, conn: sqlite3.Connection) -> dict:
    from .bob_parser import bob_db, load_tasks, task_folder

    home = cfg.bob_dirs[0] if cfg.bob_dirs else bob_home()
    tasks = [t for t in load_tasks(home) if not cfg.is_internal_path(task_folder(t))]  # not Interlatch's own analyses
    with_prompt = sum(1 for t in tasks if t["messages"])
    connected = bool(cfg.bob_dirs)
    app = next((a for a in ("/Applications/IBM Bob.app", "/Applications/IBM Bob - Insiders.app") if Path(a).exists()), None)
    mcp = _bob_mcp_registered(home)
    return {
        "name": "bob",
        "label": "IBM Bob",
        "vendor": "IBM",
        "detected": home.is_dir() or bool(app),
        "connected": connected,
        "version": None,
        "binary": app,
        "dirs": [_tilde(home)],
        "on_disk": with_prompt,
        "recorded": _recorded(conn, "agent = 'bob'"),
        "recovered": 0,
        "recording": tr("background sync every 15 min") if connected else tr("not recording"),
        "checks": [
            {"key": "files", "label": tr("Task database"), "ok": bob_db(home).exists(),
             "detail": tr("{where} · {n} tasks, {messages} with messages", where=_tilde(bob_db(home)), n=len(tasks),
                          messages=with_prompt)},
            {"key": "recording", "label": tr("Recording"), "ok": connected,
             "detail": tr("scanned every sync") if connected else tr("not scanned (connect to start)")},
            {"key": "mcp", "label": tr("MCP server in Bob"), "ok": mcp,
             "detail": _tilde(bob_mcp_files(home)[0])},
            {"key": "chats", "label": tr("IDE chat history"), "ok": None, "optional": True,
             "detail": tr("Bob IDE keeps no conversation files on this Mac; only tasks in its task database are recorded")},
        ],
        "notes": [],
    }


def connect_bob(cfg: Config, exe: str) -> list[str]:
    actions = []
    home = bob_home()
    if not cfg.bob_dirs:
        set_config_value(cfg, "sources", "bob_dirs", f'["{_tilde(home)}"]')
        actions.append(tr("recording {where}", where=_tilde(home)))
    if home.is_dir():
        command, args = _split_exe(exe)
        entry = {"command": command, "args": args, "disabled": False, "alwaysAllow": []}
        current, legacy = bob_mcp_files(home)
        if not current.exists() and legacy.exists():
            # Do Bob's own migration first, so writing mcp.json doesn't keep the user's other servers from moving over.
            shutil.copy2(legacy, current)
        actions += [_mcp_json_set(cfg, path, "mcpServers", entry, "IBM Bob") for path in (current, legacy)]
    return actions


def disconnect_bob(cfg: Config) -> list[str]:
    set_config_value(cfg, "sources", "bob_dirs", "[]")
    actions = [tr("stopped recording {label} (recorded sessions are kept)", label="IBM Bob")]
    for path in bob_mcp_files(bob_home()):
        if _mcp_json_has(path, "mcpServers"):
            actions.append(_mcp_json_set(cfg, path, "mcpServers", None, "IBM Bob"))
    return actions


# ------------------------------------------------------------------ Google Antigravity
def antigravity_home() -> Path:
    return Path(os.environ.get("ANTIGRAVITY_HOME", "~/.gemini/antigravity")).expanduser()


def antigravity_mcp_config(home: Path) -> Path:
    """Antigravity's global MCP servers live beside its home: ~/.gemini/config/mcp_config.json."""
    return home.parent / "config" / "mcp_config.json"


def _app_version(app: Path) -> str | None:
    try:
        with open(app / "Contents" / "Info.plist", "rb") as fh:
            return plistlib.load(fh).get("CFBundleShortVersionString")
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def antigravity_status(cfg: Config, conn: sqlite3.Connection) -> dict:
    from .antigravity_parser import conversation_dirs, stored_conversations

    home = cfg.antigravity_dirs[0] if cfg.antigravity_dirs else antigravity_home()
    readable = {d.name for d in conversation_dirs(home)}
    unreadable = [c for c in stored_conversations(home) if c not in readable]
    connected = bool(cfg.antigravity_dirs)
    app = APPLICATIONS / "Antigravity.app"
    mcp_config = antigravity_mcp_config(home)
    return {
        "name": "antigravity",
        "label": "Google Antigravity",
        "vendor": "Google",
        "detected": home.is_dir() or app.exists(),
        "connected": connected,
        "version": _app_version(app),
        "binary": str(app) if app.exists() else None,
        "dirs": [_tilde(home)],
        "on_disk": len(readable),
        "recorded": _recorded(conn, "agent = 'antigravity'"),
        "recovered": 0,
        "recording": tr("background sync every 15 min") if connected else tr("not recording"),
        "checks": [
            {"key": "files", "label": tr("Conversation logs"), "ok": bool(readable),
             "detail": tr("{where} · {n} conversations with a step log", where=f"{_tilde(home)}/brain", n=len(readable))},
            {"key": "recording", "label": tr("Recording"), "ok": connected,
             "detail": tr("scanned every sync") if connected else tr("not scanned (connect to start)")},
            {"key": "mcp", "label": tr("MCP server in Antigravity"), "ok": _mcp_json_has(mcp_config, "mcpServers"),
             "detail": _tilde(mcp_config)},
            {"key": "chats", "label": tr("Conversations without a log"), "ok": None, "optional": True,
             "detail": tr("{n} kept only in Antigravity's own encrypted store; these cannot be read", n=len(unreadable))
                       if unreadable else tr("none: every conversation has a readable step log")},
        ],
        "notes": [tr("Antigravity records tokens but no prices; sessions on Gemini models show no cost")],
    }


def connect_antigravity(cfg: Config, exe: str) -> list[str]:
    actions = []
    home = antigravity_home()
    if not cfg.antigravity_dirs:
        set_config_value(cfg, "sources", "antigravity_dirs", json.dumps([_tilde(home)], ensure_ascii=False))
        actions.append(tr("recording {where}", where=_tilde(home)))
    if home.is_dir():
        actions.append(_mcp_json_set(cfg, antigravity_mcp_config(home), "mcpServers", mcp_server_entry(exe), "Antigravity"))
    return actions


def disconnect_antigravity(cfg: Config) -> list[str]:
    set_config_value(cfg, "sources", "antigravity_dirs", "[]")
    actions = [tr("stopped recording {label} (recorded sessions are kept)", label="Google Antigravity")]
    path = antigravity_mcp_config(antigravity_home())
    if _mcp_json_has(path, "mcpServers"):
        actions.append(_mcp_json_set(cfg, path, "mcpServers", None, "Antigravity"))
    return actions


# ------------------------------------------------------------------ MCP-only clients
# Tools Interlatch does not record but can give its MCP server to. Each keeps a JSON config with a server map.
APPLICATIONS = Path("/Applications")

MCP_CLIENTS = {
    # "in_app_support": its paths are in app_support_dir(), not the home folder
    "claude-desktop": {"label": "Claude Desktop", "vendor": "Anthropic", "app": "Claude.app", "in_app_support": True,
                       "config": "Claude/claude_desktop_config.json", "home": "Claude", "restart": True},
    "cursor": {"label": "Cursor", "vendor": "Anysphere", "app": "Cursor.app", "config": ".cursor/mcp.json", "home": ".cursor"},
    "windsurf": {"label": "Windsurf", "vendor": "Windsurf", "app": "Windsurf.app",
                 "config": ".codeium/windsurf/mcp_config.json", "home": ".codeium/windsurf"},
    "gemini": {"label": "Gemini CLI", "vendor": "Google", "binary": "gemini", "config": ".gemini/settings.json", "home": ".gemini"},
}


def _client_base(c: dict) -> Path:
    return app_support_dir() if c.get("in_app_support") else Path.home()


def mcp_client_config(name: str) -> Path:
    return _client_base(MCP_CLIENTS[name]) / MCP_CLIENTS[name]["config"]


def _mcp_client_detected(name: str) -> bool:
    c = MCP_CLIENTS[name]
    return ((_client_base(c) / c["home"]).is_dir() or ("app" in c and (APPLICATIONS / c["app"]).exists())
            or ("binary" in c and bool(shutil.which(c["binary"]))))


def mcp_clients_status() -> list[dict]:
    return [{"name": name, "label": c["label"], "vendor": c["vendor"], "detected": _mcp_client_detected(name),
             "registered": _mcp_json_has(mcp_client_config(name), "mcpServers"), "config": _tilde(mcp_client_config(name))}
            for name, c in MCP_CLIENTS.items()]


def mcp_server_entry(exe: str) -> dict:
    command, args = _split_exe(exe)
    return {"command": command, "args": args}


def add_mcp_client(cfg: Config, name: str, exe: str) -> list[str]:
    c = MCP_CLIENTS[name]
    if not _mcp_client_detected(name):
        return [tr("{label} not found on this computer; nothing changed", label=c["label"])]
    action, changed = _mcp_json_write(cfg, mcp_client_config(name), "mcpServers", mcp_server_entry(exe), c["label"])
    return [action + (tr("; restart {label} to load it", label=c["label"]) if c.get("restart") and changed else "")]


def remove_mcp_client(cfg: Config, name: str) -> list[str]:
    return [_mcp_json_set(cfg, mcp_client_config(name), "mcpServers", None, MCP_CLIENTS[name]["label"])]


# ------------------------------------------------------------------ registry
CONNECTORS = {
    "claude": (claude_status, connect_claude, disconnect_claude),
    "codex": (codex_status, connect_codex, disconnect_codex),
    "codex-cloud": (codex_cloud_status, connect_codex_cloud, disconnect_codex_cloud),
    "copilot": (copilot_status, connect_copilot, disconnect_copilot),
    "bob": (bob_status, connect_bob, disconnect_bob),
    "antigravity": (antigravity_status, connect_antigravity, disconnect_antigravity),
}


def all_status(cfg: Config, conn: sqlite3.Connection) -> list[dict]:
    return [status(cfg, conn) for status, _, _ in CONNECTORS.values()]


def connect(cfg: Config, name: str, exe: str) -> list[str]:
    if name in MCP_CLIENTS:
        return add_mcp_client(cfg, name, exe)
    if name not in CONNECTORS:
        raise KeyError(name)
    return CONNECTORS[name][1](cfg, exe)


def disconnect(cfg: Config, name: str) -> list[str]:
    if name in MCP_CLIENTS:
        return remove_mcp_client(cfg, name)
    if name not in CONNECTORS:
        raise KeyError(name)
    return CONNECTORS[name][2](cfg)
