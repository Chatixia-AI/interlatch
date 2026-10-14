"""Safety net: every CLI command, alias, option, default and handler, snapshotted to tests/goldens/cli.json.

Moving or splitting the command line (interfaces/cli/) must leave this snapshot unchanged: a dropped flag, a changed
default, a renamed dest or a command routed to another function fails here. For a deliberate change, rerun with
UPDATE_GOLDENS=1 and review the diff of tests/goldens/cli.json.
"""

from __future__ import annotations

import argparse
from typing import Any

from interlatch.interfaces.cli.parser import build_parser

from golden import assert_golden

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


def _name(value) -> str:
    """A function or type by name (its address changes every run)."""
    return getattr(value, "__qualname__", None) or getattr(value, "__name__", None) or type(value).__name__


def _value(value) -> Any:
    if value is argparse.SUPPRESS:
        return "==SUPPRESS=="
    if isinstance(value, type):
        return f"<type {value.__name__}>"
    if callable(value):
        return f"<fn {_name(value)}>"
    if isinstance(value, (list, tuple)):
        return [_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _value(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def _action(action: argparse.Action, groups: dict[int, int]) -> dict[str, Any]:
    return {
        "action": type(action).__name__,
        "option_strings": list(action.option_strings),
        "dest": action.dest,
        "default": _value(action.default),
        "const": _value(action.const),
        "choices": None if action.choices is None else [_value(c) for c in action.choices],
        "nargs": action.nargs,
        "type": None if action.type is None else _name(action.type),
        "required": action.required,
        "metavar": _value(action.metavar),
        "help_suppressed": action.help is argparse.SUPPRESS,
        "exclusive_group": groups.get(id(action)),
    }


def _snapshot(parser: argparse.ArgumentParser) -> dict[str, Any]:
    """One parser: its options in order, its defaults (set_defaults: the handler), and its subcommands, nested."""
    groups = {id(a): i for i, g in enumerate(parser._mutually_exclusive_groups) for a in g._group_actions}
    snap: dict[str, Any] = {
        "options": [_action(a, groups) for a in parser._actions
                    if not isinstance(a, (argparse._HelpAction, argparse._SubParsersAction))],
        "defaults": _value(dict(parser._defaults)),
    }
    sub = _subparsers(parser)
    if sub is not None:
        hidden = {c.dest for c in sub._choices_actions if c.help is argparse.SUPPRESS}
        listed = {c.dest for c in sub._choices_actions}
        commands: dict[str, Any] = {}
        for name, child in sub.choices.items():
            canonical = next(n for n, c in sub.choices.items() if c is child)  # an alias maps to the first name
            if canonical != name:
                commands[canonical]["aliases"].append(name)
                continue
            commands[name] = {"aliases": [], "hidden": name in hidden or name not in listed, **_snapshot(child)}
        snap["subcommands"] = {"dest": sub.dest, "required": sub.required, "commands": commands}
    return snap


def test_every_command_option_and_default_matches_the_golden():
    snap = _snapshot(build_parser())
    for opt in snap["options"]:
        if opt["dest"] == "version":
            opt["default"] = opt["const"] = None  # the version string changes every release
    assert_golden("cli.json", snap)


def test_cli_commands_and_aliases_pinned():
    parser = build_parser()
    commands = _snapshot(parser)["subcommands"]["commands"]
    assert {name: c["aliases"] for name, c in commands.items()} == EXPECTED_COMMANDS
    assert set(_subparsers(parser).choices) == set(EXPECTED_COMMANDS) | {a for v in EXPECTED_COMMANDS.values() for a in v}
    assert {name for name, c in commands.items() if c["hidden"]} == {"hook", "ingest-session"}


def test_every_command_has_a_handler():
    commands = _snapshot(build_parser())["subcommands"]["commands"]
    for name, c in commands.items():
        assert str(c["defaults"].get("fn", "")).startswith("<fn cmd_"), f"{name} routes to {c['defaults']}"


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
