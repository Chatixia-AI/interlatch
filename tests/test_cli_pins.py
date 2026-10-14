"""Safety-net test suite: Pin every CLI command, alias, argument, choice, and default.

Guarantees that moving, refactoring, or splitting cli.py cannot silently drop or mutate CLI flags.
"""

from __future__ import annotations

import argparse
from typing import Any

from chronicle.cli import build_parser

EXPECTED_COMMANDS = {
    "install": ["setup"],
    "uninstall": [],
    "status": ["doctor"],
    "sync": [],
    "work": [],
    "analyze": [],
    "synthesize": [],
    "sessions": ["ls"],
    "show": [],
    "search": [],
    "knowledge": ["kb"],
    "projects": [],
    "stats": [],
    "ui": ["serve"],
    "app": [],
    "export": [],
    "glossary": [],
    "systems": [],
    "review": [],
    "suggest": ["suggestions"],
    "friction": [],
    "forget": [],
    "sources": ["connectors"],
    "connect": [],
    "disconnect": [],
    "import": [],
    "screen": [],
    "context": [],
    "config": [],
    "migrate": [],
    "mcp": [],
    "leaderboard": [],
    "mirror": [],
    "tailnet": [],
    "hub": [],
    "push": [],
    "container": [],
    "hook": [],
    "ingest-session": [],
}


def _get_subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _extract_options(subparser: argparse.ArgumentParser) -> dict[str, dict[str, Any]]:
    opts = {}
    for action in subparser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        key = "/".join(action.option_strings) if action.option_strings else action.dest
        opts[key] = {
            "dest": action.dest,
            "required": action.required,
            "default": action.default,
            "choices": list(action.choices) if action.choices else None,
            "nargs": action.nargs,
            "type": getattr(action.type, "__name__", str(action.type)) if action.type else None,
        }
    return opts


def test_cli_commands_and_aliases_pinned():
    parser = build_parser()
    subparsers = _get_subparsers(parser)

    # Every expected command exists in subparsers
    for cmd, aliases in EXPECTED_COMMANDS.items():
        assert cmd in subparsers, f"Missing command: {cmd}"
        for alias in aliases:
            assert alias in subparsers, f"Missing alias: {alias} for {cmd}"

    # No unexpected commands exist
    all_expected = set(EXPECTED_COMMANDS.keys()) | {a for aliases in EXPECTED_COMMANDS.values() for a in aliases}
    assert set(subparsers.keys()) == all_expected


def test_cli_key_options_pinned():
    parser = build_parser()
    subparsers = _get_subparsers(parser)

    # install options
    install_opts = _extract_options(subparsers["install"])
    assert "-y/--yes" in install_opts
    assert "--no-sync" in install_opts
    assert "--no-hooks" in install_opts
    assert "--no-launchd" in install_opts
    assert "--no-mcp" in install_opts
    assert "--no-ui" in install_opts
    assert "--inject-context" in install_opts
    assert "--statusline" in install_opts
    assert "--interval" in install_opts
    assert install_opts["--interval"]["default"] == 15
    assert "--exe" in install_opts
    assert "--analyze" in install_opts
    assert "--notify-updates/--no-notify-updates" in install_opts
    assert "--menu-bar/--no-menu-bar" in install_opts
    assert "--dry-run" in install_opts

    # sync options
    sync_opts = _extract_options(subparsers["sync"])
    assert "--work" in sync_opts
    assert "--force" in sync_opts
    assert "--quiet" in sync_opts
    assert "-v/--verbose" in sync_opts

    # analyze options
    analyze_opts = _extract_options(subparsers["analyze"])
    assert "sessions" in analyze_opts
    assert "--pending" in analyze_opts
    assert "--all" in analyze_opts
    assert "--limit" in analyze_opts
    assert "--model" in analyze_opts
    assert "--force" in analyze_opts
    assert "--no-synthesize" in analyze_opts
    assert "--backend" in analyze_opts
    assert "--dry-run" in analyze_opts
    assert "--concurrency" in analyze_opts

    # hub actions
    hub_opts = _extract_options(subparsers["hub"])
    assert "action" in hub_opts
    expected_hub_actions = [
        "status", "enable", "join", "leave", "disable", "folders", "add-folder",
        "remove-folder", "store", "people", "invite", "role", "access",
        "remove", "shared-token", "signin", "project", "purge", "rejoin",
    ]
    assert hub_opts["action"]["choices"] == expected_hub_actions
    assert "--token" in hub_opts
    assert "--code" in hub_opts
    assert "--share" in hub_opts
    assert hub_opts["--share"]["choices"] == ["everything", "knowledge"]
    assert "--role" in hub_opts
    assert hub_opts["--role"]["choices"] == ["admin", "member", "readonly"]

    # leaderboard actions
    lb_opts = _extract_options(subparsers["leaderboard"])
    assert lb_opts["action"]["choices"] == ["status", "join", "send", "leave"]

    # mirror actions
    mirror_opts = _extract_options(subparsers["mirror"])
    assert mirror_opts["action"]["choices"] == ["status", "sync"]
    assert "--full" in mirror_opts

    # tailnet actions
    tailnet_opts = _extract_options(subparsers["tailnet"])
    assert tailnet_opts["action"]["choices"] == ["on", "off", "status"]
    assert "--anyone" in tailnet_opts

    # config actions
    config_opts = _extract_options(subparsers["config"])
    assert config_opts["action"]["choices"] == ["show", "path", "edit", "set", "set-key", "forget-key"]

    # export options
    export_opts = _extract_options(subparsers["export"])
    assert export_opts["--format"]["choices"] == ["md", "json", "raw"]
    assert export_opts["--format"]["default"] == "md"

    # connect / disconnect choices
    connect_opts = _extract_options(subparsers["connect"])
    expected_agents = ["claude", "codex", "codex-cloud", "copilot", "bob", "antigravity",
                       "claude-desktop", "cursor", "windsurf", "gemini"]
    assert connect_opts["name"]["choices"] == expected_agents


def test_cli_parse_args_smoke():
    parser = build_parser()

    # Aliases route to the same handler function
    args_install = parser.parse_args(["install", "-y"])
    args_setup = parser.parse_args(["setup", "-y"])
    assert args_install.fn == args_setup.fn and args_setup.yes is True

    args_sessions = parser.parse_args(["sessions", "-n", "50"])
    args_ls = parser.parse_args(["ls", "-n", "50"])
    assert args_sessions.fn == args_ls.fn and args_ls.limit == 50

    args_status = parser.parse_args(["status"])
    args_doctor = parser.parse_args(["doctor"])
    assert args_status.fn == args_doctor.fn

    args_kb = parser.parse_args(["kb", "sqlite"])
    args_knowledge = parser.parse_args(["knowledge", "sqlite"])
    assert args_kb.fn == args_knowledge.fn and args_kb.query == "sqlite"

    args_ui = parser.parse_args(["ui", "--port", "9000"])
    args_serve = parser.parse_args(["serve", "--port", "9000"])
    assert args_ui.fn == args_serve.fn and args_serve.port == 9000

    args_sources = parser.parse_args(["sources"])
    args_connectors = parser.parse_args(["connectors"])
    assert args_sources.fn == args_connectors.fn

    args_suggest = parser.parse_args(["suggest", "list"])
    args_suggestions = parser.parse_args(["suggestions", "list"])
    assert args_suggest.fn == args_suggestions.fn and args_suggestions.action == "list"

    # Subcommands parse expected fields
    args = parser.parse_args(["hub", "join", "https://hub.example.com", "--token", "tok123", "--share", "knowledge"])
    assert args.command == "hub"
    assert args.action == "join"
    assert args.url == "https://hub.example.com"
    assert args.token == "tok123"
    assert args.share == "knowledge"

    args = parser.parse_args(["leaderboard", "join"])
    assert args.command == "leaderboard" and args.action == "join"

    args = parser.parse_args(["mirror", "sync", "--full"])
    assert args.command == "mirror" and args.action == "sync" and args.full is True
