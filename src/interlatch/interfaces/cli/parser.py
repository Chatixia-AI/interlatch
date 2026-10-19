"""The `interlatch` parser: every command group's commands, assembled in the order `interlatch --help` lists them."""

from __future__ import annotations

import argparse
import sys

from ... import __version__
from .commands.archive import add_export, add_forget, add_projects, add_search, add_sessions, add_show, add_stats
from .commands.knowledge import (add_analyze, add_context, add_friction, add_glossary, add_knowledge, add_review,
                                 add_suggest, add_synthesize, add_systems)
from .commands.operations import add_container, add_leaderboard, add_mirror, add_status, add_tailnet
from .commands.hubs import add_group, add_hubs, add_profile
from .commands.recording import (add_connect, add_disconnect, add_hook, add_import, add_ingest_session, add_screen,
                                 add_sources, add_sync, add_work)
from .commands.setup import add_app, add_config, add_install, add_mcp, add_migrate, add_ui, add_uninstall
from .commands.sharing import add_hub, add_push


def prog_name() -> str:
    """The command as it was run: `interlatch`, or `chronicle` (the same command under its name before the rename)."""
    from pathlib import Path

    name = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""
    return name if name in ("interlatch", "chronicle") else "interlatch"


# every command, in the order `interlatch --help` lists them
COMMANDS = (add_install, add_uninstall, add_status, add_sync, add_work, add_analyze, add_synthesize, add_sessions,
            add_show, add_search, add_knowledge, add_projects, add_stats, add_ui, add_app, add_export, add_glossary,
            add_systems, add_review, add_suggest, add_friction, add_forget, add_sources, add_connect, add_disconnect,
            add_import, add_screen, add_context, add_config, add_migrate, add_mcp, add_leaderboard, add_mirror,
            add_tailnet, add_hub, add_push, add_hubs, add_group, add_profile, add_container, add_hook, add_ingest_session)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=prog_name(),
        description="Record, archive and analyze every coding-agent session (Claude Code, Codex, GitHub Copilot, IBM Bob, "
                    "Google Antigravity, OpenCode); extract reusable knowledge.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--hub", "--profile", metavar="NAME", dest="hub_name",  # --profile: its name in 0.28.1
                   help="run hub, push or config for another of this computer's hubs (`interlatch hubs`); without it, "
                        "Personal's")
    sub = p.add_subparsers(dest="command", metavar="<command>")
    for add in COMMANDS:
        add(sub)
    return p
