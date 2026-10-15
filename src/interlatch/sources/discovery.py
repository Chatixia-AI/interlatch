"""Where each coding agent keeps its files on this computer (Codex, Copilot and VS Code, Bob, Antigravity,
OpenCode), and
where this system keeps apps' settings. Finding them only; connecting an agent is runtime/connectors.py."""

from __future__ import annotations

import os
from pathlib import Path


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def app_support_dir() -> Path:
    """Where this system keeps apps' settings: ~/Library/Application Support, %APPDATA%, or ~/.config."""
    import sys

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def copilot_home() -> Path:
    return Path(os.environ.get("COPILOT_HOME", "~/.copilot")).expanduser()


def vscode_user_dirs() -> list[Path]:
    base = app_support_dir()
    return [d for d in (base / "Code" / "User", base / "Code - Insiders" / "User") if d.is_dir()]


def bob_home() -> Path:
    return Path(os.environ.get("BOB_HOME", "~/.bob")).expanduser()


def antigravity_home() -> Path:
    return Path(os.environ.get("ANTIGRAVITY_HOME", "~/.gemini/antigravity")).expanduser()


def opencode_home() -> Path:
    """OpenCode's data folder: $XDG_DATA_HOME/opencode, else ~/.local/share/opencode (on Windows too)."""
    if os.environ.get("OPENCODE_HOME"):
        return Path(os.environ["OPENCODE_HOME"]).expanduser()
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "opencode"


def opencode_config_dir() -> Path:
    """Where OpenCode reads its global config: $OPENCODE_CONFIG_DIR, else $XDG_CONFIG_HOME/opencode or ~/.config/opencode."""
    if os.environ.get("OPENCODE_CONFIG_DIR"):
        return Path(os.environ["OPENCODE_CONFIG_DIR"]).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode"
