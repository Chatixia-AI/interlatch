"""Setting Interlatch up: the interactive install (which agents to record, past sessions, analysis, the dashboard
at login), uninstalling, the dashboard and the app, the MCP server, config.toml, and moving from Chronicle."""

from __future__ import annotations

import argparse
import json
import sys

from ....core.config import set_config_value
from ..common import analyzer_label, load_cfg, new_console, open_archive, wait_for_port


def _ask(question: str, default: bool) -> bool:
    try:
        answer = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    except EOFError:
        return default
    return default if not answer else answer.startswith("y")


def _setup_choices(cfg, conn, console, *, ask) -> tuple[list[str], list[str]]:
    """Show the agents found on this machine; return (sources to connect, MCP-only clients to register)."""
    from ....runtime.connectors import all_status, mcp_clients_status
    from ....runtime.install import hooks_installed

    statuses = all_status(cfg, conn)
    sources = [s for s in statuses if s["name"] != "codex-cloud"]
    cloud = next(s for s in statuses if s["name"] == "codex-cloud")
    clients = mcp_clients_status()
    claude_hooked = bool(hooks_installed(cfg).get("SessionEnd"))
    connected = lambda s: claude_hooked if s["name"] == "claude" else s["connected"]  # noqa: E731

    console.print("[bold]Coding agents on this machine[/]")
    for s in sources:
        state = ("[green]connected[/]" if connected(s) else "[yellow]found[/]" if s["detected"] else "[dim]not installed[/]")
        count = f" · {s['on_disk']} session{'s' * (s['on_disk'] != 1)} on disk" if s["detected"] else ""
        console.print(f"  {s['label']:<16} {state}{count}", highlight=False)
    for c in clients:
        if c["detected"]:
            state = "[green]MCP server added[/]" if c["registered"] else "[yellow]found[/] [dim](MCP tools only, not recorded)[/]"
            console.print(f"  {c['label']:<16} {state}", highlight=False)
    console.print()

    picked = []
    for s in sources:
        if connected(s):
            picked.append(s["name"])  # re-run connect so paths stay current
        elif s["detected"] and ask(f"Record {s['label']} sessions?", True):
            picked.append(s["name"])
    if "codex" in picked and not cloud["connected"] and cloud["detected"]:
        if ask("Also record Codex Cloud tasks? (lists them online through the codex CLI on every sync)", False):
            picked.append("codex-cloud")
    elif cloud["connected"]:
        picked.append("codex-cloud")
    mcp = [c["name"] for c in clients if c["detected"] and not c["registered"]
           and ask(f"Give {c['label']} Interlatch's MCP tools (search your past sessions)?", True)]
    return picked, mcp


ANALYZE_NEWEST = 20  # what "analyze the newest now" means at install: enough for a first Glossary and Map in minutes
STAGE_SECONDS = {"session": 45, "synthesis": 85, "glossary": 55, "themes": 70}  # typical call times, until measured here


def _about(minutes: float) -> str:
    if minutes < 60:
        return f"about {max(1, round(minutes))} min"
    if minutes < 48 * 60:
        return f"about {round(minutes / 60)} h"
    return f"about {round(minutes / 1440)} days"


def _estimate_minutes(conn, cfg, ids: list[str]) -> float:
    """Rough wall time to analyze `ids` now, then build the knowledge bases, glossaries and Map themes they feed."""
    import math

    secs = dict(STAGE_SECONDS)
    secs.update({r[0]: r[1] for r in conn.execute(
        "SELECT kind, AVG(duration_ms) / 1000.0 FROM analyses WHERE status = 'done' AND duration_ms > 0 GROUP BY kind")
        if r[0] in secs and r[1]})
    project_of = dict(conn.execute("SELECT id, project_path FROM sessions"))
    projects = len({project_of.get(i) for i in ids}) + 1  # + the cross-project playbook
    conc = max(1, cfg.analysis.concurrency)
    return (math.ceil(len(ids) / conc) * secs["session"] + projects * secs["synthesis"]
            + math.ceil(projects / conc) * secs["glossary"] + 2 * secs["themes"]) / 60


def _analysis_step(cfg, conn, console, analyzer, *, interactive: bool, choice: str | None, background_sync: bool,
                   interval: int) -> tuple[list[str], int, str]:
    """Explain what analysis builds (knowledge, then the Glossary and the Map) and when it happens; ask whether to
    analyze now. Returns (sessions to analyze now, sessions waiting, how the rest gets analyzed)."""
    import math

    from ....analysis.queue import pending_sessions

    ids = pending_sessions(conn, cfg, 100_000)
    n = len(ids)
    if background_sync and cfg.analysis.auto:
        later = "in the background"
    elif not cfg.analysis.auto:
        later = "when you run `interlatch analyze --pending` (analysis.auto is off)"
    else:
        later = "when you run `interlatch analyze --pending` (nothing runs in the background)"
    if not n:
        return [], 0, later
    console.print("\n[bold]Analysis: knowledge, the Glossary and the Map[/]")
    console.print(f"  Interlatch reads each session through your {analyzer.label} login and pulls out what was learned "
                  "(fixes, decisions, gotchas, commands). That knowledge builds each project's knowledge base, then the "
                  "Glossary, then the Map. Until sessions are analyzed, the Glossary and the Map stay empty.",
                  highlight=False)
    console.print(f"  {n} past session{'s' * (n != 1)} waiting. Each is one {analyzer.label} call and counts toward "
                  "your plan's usage.", highlight=False)
    if later == "in the background":
        runs = math.ceil(n / max(1, cfg.analysis.max_per_run))
        console.print(f"  Later: in the background, {cfg.analysis.max_per_run} every {interval} min, so all of them in "
                      f"{_about(runs * interval)}. A project's glossary is built once all its sessions are analyzed.",
                      highlight=False)
    else:
        console.print(f"  Later: {later}.", highlight=False)

    if choice is None and not interactive:
        choice = "later"
    if choice is None:
        newest = ids[:ANALYZE_NEWEST]
        if n <= ANALYZE_NEWEST:
            choice = "all" if _ask(f"Analyze {'them' if n > 1 else 'it'} now and show the progress? "
                                   f"({_about(_estimate_minutes(conn, cfg, ids))})", True) else "later"
        else:
            try:
                answer = input(f"Analyze now and show the progress? [N]ewest {len(newest)} "
                               f"({_about(_estimate_minutes(conn, cfg, newest))}) / [a]ll {n} "
                               f"({_about(_estimate_minutes(conn, cfg, ids))}) / [l]ater: ").strip().lower()
            except EOFError:
                answer = ""
            choice = ("all" if answer.startswith("a") else "later" if answer.startswith(("l", "no", "s"))
                      else answer if answer.isdigit() else str(ANALYZE_NEWEST))
    if choice == "all":
        return ids, n, later
    if choice.isdigit():
        return ids[:int(choice)], n, later
    return [], n, later


class _StageProgress:
    """Draws the worker's progress messages as one bar per stage, so the Glossary and the Map are seen being built."""

    STAGES = {"analyzing": "Sessions", "synthesizing": "Knowledge bases", "glossary": "Glossary", "themes": "Map themes",
              "writing weekly review": "Weekly review"}

    def __init__(self, bar):
        import threading

        self.bar, self.task, self.stage, self.count = bar, None, None, 0
        self.lock = threading.Lock()  # sessions report from several analysis threads at once

    def __call__(self, message: str) -> None:
        with self.lock:
            self._update(message)

    def _update(self, message: str) -> None:
        from ....core.util import PROGRESS_RE
        from ....knowledge.synthesize import GLOBAL

        stage = next((s for s in self.STAGES if message.startswith(s)), None)
        if (stage is None and self.task is None) or message.startswith("stopping"):
            self.bar.console.print(f"  {message}", highlight=False)
            return
        if stage and stage != self.stage:
            self.finish()
            self.stage, self.count = stage, 0
            self.task = self.bar.add_task(self.STAGES[stage], total=None, detail="")
        m = PROGRESS_RE.search(message)
        if m:
            done, total = (int(g.replace(",", "")) for g in m.groups())
            self.bar.update(self.task, completed=done, total=total, detail=f"{done} of {total}")
        elif stage == "synthesizing":
            self.count += 1
            name = message.removeprefix("synthesizing ").rstrip("…")
            self.bar.update(self.task, detail=f"{self.count}: {'global playbook' if name == GLOBAL else name}")

    def finish(self) -> None:
        if self.task is not None:
            total = next(t.total for t in self.bar.tasks if t.id == self.task) or 1
            self.bar.update(self.task, total=total, completed=total,
                            detail=f"{self.count} built" if self.stage == "synthesizing" else "done")


def _analyze_now(cfg, console, ids: list[str], waiting: int, later: str) -> str:
    """Analyze `ids` with live progress, then build the knowledge bases, glossaries and Map themes they feed.
    Returns the line for the closing summary about what is left."""
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    from ....application.workflows import run_worker
    from ...exports.vault import export_markdown

    console.print(f"\nAnalyzing {len(ids)} session{'s' * (len(ids) != 1)}. Ctrl-C stops; what is left is analyzed {later}.",
                  highlight=False)
    try:
        with Progress(SpinnerColumn(), TextColumn("{task.description:<16}"), BarColumn(), TextColumn("{task.fields[detail]}"),
                      TimeElapsedColumn(), console=console) as bar:
            stages = _StageProgress(bar)
            report = run_worker(cfg, export=export_markdown, session_ids=ids, force=True, wait=True, progress=stages)
            stages.finish()
    except KeyboardInterrupt:
        console.print(f"[yellow]Stopped.[/] The rest is analyzed {later}; the Glossary and the Map are built after.",
                      highlight=False)
        return f"Sessions still waiting for analysis are analyzed {later}."
    conn = open_archive(cfg)
    terms = conn.execute("SELECT COUNT(*) FROM glossary").fetchone()[0]
    themed = conn.execute("SELECT COUNT(DISTINCT category) FROM glossary_themes").fetchone()[0]
    conn.close()
    console.print(f"• analyzed {len(report.analyzed)} session{'s' * (len(report.analyzed) != 1)}"
                  + (f" ({len(report.skipped)} too short to analyze)" if report.skipped else "")
                  + f", built {len(report.synthesized)} knowledge base{'s' * (len(report.synthesized) != 1)}, "
                  f"{terms} glossary term{'s' * (terms != 1)}"
                  + (f", Map themes for {themed} categor{'ies' if themed != 1 else 'y'}" if themed else ""), highlight=False)
    for target, err in report.failed[:3]:
        console.print(f"  [red]![/] {target[:40]}: {err[:200]}", highlight=False)
    if report.paused_until:
        from ....core.util import local_str

        console.print(f"  [yellow]{analyzer_label(cfg)} usage limit reached[/]: analysis resumes after "
                      f"{local_str(report.paused_until)}.", highlight=False)
    rest = waiting - len(report.analyzed) - len(report.skipped)
    if rest > 0:
        return (f"The other {rest} session{'s are' if rest != 1 else ' is'} analyzed {later}; the Glossary and the Map "
                "grow as they are.")
    return "The Glossary and the Map are ready on the dashboard." if terms else ""


NOTIFY_ASKED_KEY = "update_notify_asked"  # kv: install asked about release notifications, so it asks only once
MENU_BAR_ASKED_KEY = "menu_bar_asked"  # kv: install asked about the menu-bar icon, so it asks only once


def _notify_choice(cfg, conn, args, *, ask, background_sync: bool) -> bool | None:
    """Turn release notifications on or off (True/False), or leave the setting alone (None). Asked once, and only
    where they can work: an install that updates from PyPI, with the background sync running to check."""
    from ....runtime.update import compares_online
    from ....storage.kv import kv_get

    if args.notify_updates is not None:
        return args.notify_updates
    if (ask is None or cfg.update_notify or not background_sync or kv_get(conn, NOTIFY_ASKED_KEY)
            or not compares_online()):
        return None
    return ask("Notify you when a new version of Interlatch is out? (a desktop notification; asks pypi.org once a "
               "day and sends nothing about you)", True)


def _menu_bar_choice(cfg, conn, args, *, ask, login_dashboard: bool) -> bool | None:
    """Turn the macOS menu-bar icon on or off (True/False), or leave the setting alone (None). It is opt-in: asked
    once, and only where it can show, on a Mac whose dashboard runs at login."""
    import platform

    from ....storage.kv import kv_get

    if args.menu_bar is not None:
        return args.menu_bar
    if (ask is None or platform.system() != "Darwin" or not login_dashboard or cfg.server_menu_bar
            or kv_get(conn, MENU_BAR_ASKED_KEY)):
        return None
    return ask("Show Interlatch's icon in the menu bar? (what it's doing, a search and your recent sessions, a click "
               "away)", False)


def cmd_install(args) -> int:
    from ....core.util import utcnow_iso
    from ....ingestion.service import sync
    from ....runtime.connectors import connect, disconnect
    from ....runtime.install import (LAUNCHD_LABEL, UI_LABEL, agent_path, background_supported, executable,
                                     install_hooks, install_launchd, install_mcp, install_statusline, install_ui_agent,
                                     launchd_status, statusline_installed, uninstall_launchd)
    from ....storage.kv import kv_get, kv_set

    if not args.dry_run:
        from ....runtime.migrate import migrate_if_due

        migrate_if_due(quiet=False)
    cfg = load_cfg()
    conn = open_archive(cfg)
    console = new_console()
    exe = args.exe or executable()
    interactive = not args.yes and not args.dry_run and sys.stdin.isatty()
    ask = _ask if interactive else (lambda _q, default: default)

    console.print("[bold]Interlatch setup[/]\n")
    analyzer = _choose_analyzer(cfg, console, ask=ask, dry_run=args.dry_run)
    if " -m " in exe:
        console.print(f"[yellow]`interlatch` is not on PATH[/]; hooks will run `{exe}`. "
                      "`uv tool install interlatch` gives it a stable path.\n", highlight=False)

    picked, mcp_clients = _setup_choices(cfg, conn, console, ask=ask)
    background_on = False  # nothing keeps Interlatch running here: say so instead of promising background analysis
    turned_off = [label for label, off in ((LAUNCHD_LABEL, args.no_launchd), (UI_LABEL, args.no_ui)) if off]
    if background_supported() and not (args.no_launchd and args.no_ui):
        background_on = (any(launchd_status(label).get("loaded") for label in (LAUNCHD_LABEL, UI_LABEL))
                         or ask("Run Interlatch in the background, starting at login? (syncs every 15 minutes and "
                                "keeps the dashboard up)", True))
    notify = _notify_choice(cfg, conn, args, ask=_ask if interactive else None,
                            background_sync=background_on and not args.no_launchd)
    menu_bar = _menu_bar_choice(cfg, conn, args, ask=_ask if interactive else None,
                                login_dashboard=background_on and not args.no_ui)
    if args.dry_run:
        console.print("Would record: " + (", ".join(picked) or "nothing") +
                      (f"; would add the MCP server to: {', '.join(mcp_clients)}" if mcp_clients else ""))

    actions = []
    if "claude" in picked:
        if not args.dry_run and not cfg.claude_dirs:
            set_config_value(cfg, "sources", "claude_dirs", '["~/.claude"]')
        if not args.no_hooks:
            actions += install_hooks(cfg, exe, inject=args.inject_context or cfg.inject_session_start, dry_run=args.dry_run)
        if args.inject_context and not args.dry_run:
            set_config_value(cfg, "inject", "session_start", "true")
        if args.statusline or statusline_installed(cfg):  # opt-in; once on, re-installs keep its path current
            actions += install_statusline(cfg, exe, dry_run=args.dry_run)
        if not args.no_mcp:
            actions += install_mcp(cfg, exe, dry_run=args.dry_run)
    elif cfg.claude_dirs and not args.dry_run:
        actions += disconnect(cfg, "claude")  # declined (or not installed): don't scan ~/.claude either
    if args.dry_run:
        if not background_on:
            actions.append("background agents: not installed")
        if background_on and not args.no_launchd:
            actions += install_launchd(cfg, exe, interval=args.interval * 60, dry_run=True)
        if background_on and not args.no_ui:
            actions += install_ui_agent(cfg, exe, dry_run=True)
        actions += [f"would remove background agent {agent_path(label)}" for label in turned_off
                    if launchd_status(label).get("installed")]
        if notify is not None:
            actions.append(f"would turn release notifications {'on' if notify else 'off'}")
        if menu_bar is not None:
            actions.append(f"would turn the menu-bar icon {'on' if menu_bar else 'off'}")
        for a in actions:
            console.print(f"• {a}", highlight=False, soft_wrap=True)
        return 0
    for name in [n for n in picked if n != "claude"] + mcp_clients:
        actions += connect(cfg, name, exe)
    if notify is not None:
        set_config_value(cfg, "updates", "notify", "true" if notify else "false")
        kv_set(conn, NOTIFY_ASKED_KEY, utcnow_iso())
        conn.commit()
        actions.append("a desktop notification when a new version is out (turn off in Status › Updates)" if notify
                       else "no release notifications (turn on in Status › Updates)")
    if menu_bar is not None:  # before the dashboard agent (re)starts below, so it reads the new setting
        set_config_value(cfg, "server", "menu_bar", "true" if menu_bar else "false")
        kv_set(conn, MENU_BAR_ASKED_KEY, utcnow_iso())
        conn.commit()
        actions.append(_menu_bar_line(menu_bar))
    for a in actions:
        console.print(f"• {a}", highlight=False, soft_wrap=True)

    from ....core.config import load_config

    cfg = load_config(cfg.home)  # pick up the sources just connected
    if picked and not args.no_sync:
        with console.status("Importing past sessions…"):
            report = sync(cfg, conn)
        console.print(f"• imported: {report.summary()}", highlight=False, soft_wrap=True)
    first_install = not kv_get(conn, "installed_at")
    if first_install:
        kv_set(conn, "installed_at", utcnow_iso())
        conn.commit()
    now, waiting, later = [], 0, ""
    if picked and analyzer and not cfg.sends_files:
        now, waiting, later = _analysis_step(cfg, conn, console, analyzer, interactive=interactive, choice=args.analyze,
                                             background_sync=background_on and not args.no_launchd, interval=args.interval)
    conn.close()
    if now:  # before the background agents start, so they don't take the same sessions
        after = _analyze_now(cfg, console, now, waiting, later)
    else:
        after = (f"The {waiting} waiting session{'s are' if waiting != 1 else ' is'} analyzed {later}; the Glossary "
                 "and the Map are built from them." if waiting else "")

    background = []
    if background_on and not args.no_launchd:
        background += install_launchd(cfg, exe, interval=args.interval * 60)
    if background_on and not args.no_ui:
        background += install_ui_agent(cfg, exe)
    background += uninstall_launchd(turned_off)
    for a in background:
        console.print(f"• {a}", highlight=False, soft_wrap=True)

    url = f"http://127.0.0.1:{cfg.server_port}/"
    ui_running = any(a.startswith("dashboard always available") for a in background)
    console.print("\n[bold green]Done.[/]" + (" Nothing is recorded yet: connect an agent any time with "
                                              "`interlatch connect <name>`." if not picked else ""))
    if after:
        console.print(after, highlight=False)
    if not background_on:
        hooked = "claude" in picked and not args.no_hooks
        console.print("Not running in the background"
                      + (": Claude Code sessions are still recorded and analyzed as they end." if hooked else ".")
                      + " Run `interlatch sync --work` now and then to import new sessions and analyze the queue; "
                      "re-run `interlatch install` to turn background running on.", highlight=False)
    if ui_running:
        console.print(f"Dashboard: [bold]{url}[/] (kept running in the background; `interlatch ui --open` opens it)",
                      highlight=False)
        if interactive and first_install and ask("Open it now?", True) and wait_for_port(cfg.server_port):
            import webbrowser

            webbrowser.open(url)
    else:
        console.print(f"Dashboard: run [bold]interlatch ui --open[/] (serves {url} while it runs)", highlight=False)
    console.print(f"[dim]More: interlatch sources · interlatch status · config {cfg.config_path}[/]", highlight=False, soft_wrap=True)
    return 0


def _menu_bar_line(on: bool) -> str:
    import importlib.util

    if not on:
        return "no menu-bar icon (`interlatch install --menu-bar` adds it)"
    if importlib.util.find_spec("AppKit") is None:
        return ("the menu-bar icon, once the `app` extra is installed: "
                "uv tool install --force --python 3.13 'interlatch\\[app]', then `interlatch install` again")  # \\[ keeps Rich from reading [app] as markup
    return "Interlatch's icon in the menu bar (`interlatch install --no-menu-bar` removes it)"


def _choose_analyzer(cfg, console, *, ask, dry_run: bool):
    """Settle which agent analyzes sessions; return its runner, or None when it is not installed."""
    from ....analysis.runners.factory import BACKENDS, make_runner

    runner = make_runner(cfg)
    other = "codex" if runner.name == "claude" else "claude"
    other_found = bool(cfg.codex_bin() if other == "codex" else cfg.claude_bin())
    if runner.available():
        if other_found:
            console.print(f"Sessions are analyzed with {runner.label}. To use {BACKENDS[other]} instead: Status › "
                          f"Analysis in the dashboard, or `interlatch config set analysis.backend {other}`.\n",
                          highlight=False)
        return runner
    missing = f"{runner.unavailable_reason()}."
    if other_found and ask(f"{missing} Analyze sessions with {BACKENDS[other]} instead, through your "
                           f"{BACKENDS[other]} login?", True):
        if not dry_run:
            set_config_value(cfg, "analysis", "backend", json.dumps(other))
        cfg.analysis.backend = other
        return make_runner(cfg)
    console.print(f"[yellow]{missing}[/] Interlatch analyzes sessions through your own Claude Code or Codex login, or a "
                  "model provider's API (Status › Analysis in the dashboard); until one is set up (and chosen as "
                  "analysis.backend), sessions are recorded but not analyzed, so the Glossary and the Map stay empty.\n",
                  highlight=False)
    return None


def cmd_uninstall(args) -> int:
    import shutil

    from ....runtime.connectors import mcp_clients_status, remove_mcp_client
    from ....runtime.install import uninstall_hooks, uninstall_launchd, uninstall_mcp, uninstall_statusline

    cfg = load_cfg()
    actions = uninstall_hooks(cfg) + uninstall_statusline(cfg) + uninstall_launchd() + uninstall_mcp(cfg)
    for c in mcp_clients_status():
        if c["registered"]:
            actions += remove_mcp_client(cfg, c["name"])
    for a in actions or ["nothing to remove"]:
        print(f"• {a}")
    if args.purge:
        shutil.rmtree(cfg.home, ignore_errors=True)
        print(f"• deleted {cfg.home}")
    else:
        print(f"Data kept in {cfg.home} (use --purge to delete it).")
    return 0


def cmd_serve(args) -> int:
    from ....core.util import setup_logging
    from ....runtime.migrate import migrate_if_due
    from ....server import serve

    migrate_if_due(quiet=False)  # the dashboard at login: Chronicle's folder becomes Interlatch's
    cfg = load_cfg()
    setup_logging(cfg.logs_dir)
    serve(cfg, host=args.host, port=args.port, open_browser=args.open, menu_bar=args.menu_bar)
    return 0


def cmd_app(args) -> int:
    from ....runtime.platforms.desktop import main

    return main()


def cmd_migrate(args) -> int:
    """`interlatch migrate`: Chronicle's folder and settings become Interlatch's (migrate.py)."""
    from ....runtime.migrate import migrate

    lines = migrate(dry_run=args.dry_run)
    for line in lines:
        print(f"• {line}")
    if not lines:
        print("Nothing to move: everything already uses Interlatch's names.")
    return 0


def cmd_mcp(args) -> int:
    if args.print_config:
        import json

        from ....runtime.connectors import mcp_server_entry
        from ....runtime.install import MCP_NAME, executable

        print(json.dumps({"mcpServers": {MCP_NAME: mcp_server_entry(executable())}}, indent=2))
        return 0
    from ...mcp import serve

    return serve(load_cfg())


def cmd_config(args) -> int:
    import os
    import subprocess

    cfg = load_cfg()
    if args.action == "set":
        return _config_set(cfg, args.key, args.value)
    if args.action in ("set-key", "forget-key"):
        return _config_key(cfg, args.key, args.value, forget=args.action == "forget-key")
    if args.action == "path":
        print(cfg.config_path)
    elif args.action == "edit":
        editor = os.environ.get("EDITOR", {"darwin": "open", "win32": "notepad"}.get(sys.platform, "vi"))
        subprocess.call([*editor.split(), str(cfg.config_path)])
    else:
        print(cfg.config_path.read_text())
    return 0


def _config_set(cfg, key: str | None, value: str | None) -> int:
    """`interlatch config set section.key value`: the value is TOML, or a bare word taken as a string."""
    import tomllib

    from ....analysis.runners.factory import BACKENDS
    from ....core.config import LANGUAGES, load_config, set_config_value

    if not key or value is None or "." not in key:
        print("usage: interlatch config set SECTION.KEY VALUE   (e.g. interlatch config set analysis.backend codex)",
              file=sys.stderr)
        return 2
    section, name = key.rsplit(".", 1) if key.startswith("providers.") else key.split(".", 1)
    if section.startswith("providers."):
        from ....analysis.runners.settings import PROVIDERS, SETTINGS

        if section.split(".", 1)[1] not in PROVIDERS or name not in SETTINGS:
            print(f"usage: interlatch config set providers.PROVIDER.KEY VALUE; PROVIDER is one of: {', '.join(PROVIDERS)}; "
                  f"KEY one of: {', '.join(SETTINGS)} (API keys: interlatch config set-key PROVIDER)", file=sys.stderr)
            return 2
    try:
        tomllib.loads(f"v = {value}")
        literal = value
    except tomllib.TOMLDecodeError:
        literal = json.dumps(value)  # a bare word: a TOML basic string
    if key == "analysis.backend" and tomllib.loads(f"v = {literal}")["v"] not in BACKENDS:
        print(f"analysis.backend must be one of: {', '.join(BACKENDS)}", file=sys.stderr)
        return 2
    if key == "analysis.language" and tomllib.loads(f"v = {literal}")["v"] not in LANGUAGES:
        print(f"analysis.language must be one of: {', '.join(LANGUAGES)}", file=sys.stderr)
        return 2
    try:
        set_config_value(cfg, section, name, literal)
    except tomllib.TOMLDecodeError as exc:
        print(f"not changed: {exc}", file=sys.stderr)
        return 2
    print(f"[{section}] {name} = {literal}")
    if key == "analysis.backend":
        from ....analysis.runners.factory import make_runner

        runner = make_runner(load_config(cfg.home))
        if not runner.available():
            print(f"note: {runner.unavailable_reason()}; analysis waits until it is set up")
    return 0


def _config_key(cfg, provider: str | None, value: str | None, *, forget: bool) -> int:
    """`interlatch config set-key PROVIDER [KEY]`: store a provider's API key (asked for, unechoed, when not given), or
    `forget-key PROVIDER` to remove it."""
    from ....analysis.runners.factory import BACKENDS
    from ....analysis.runners.settings import KEY_ENVS, keys_path, set_key

    if provider not in KEY_ENVS:
        print(f"usage: interlatch config {'forget-key' if forget else 'set-key'} PROVIDER; PROVIDER is one of: "
              f"{', '.join(KEY_ENVS)}", file=sys.stderr)
        return 2
    label = BACKENDS[provider]
    if forget:
        set_key(cfg, provider, None)
        print(f"forgot the {label} API key")
        return 0
    if value is None:
        import getpass

        value = getpass.getpass(f"{label} API key: ")
    if not value.strip():
        print("no key given; nothing changed", file=sys.stderr)
        return 2
    set_key(cfg, provider, value)
    print(f"stored the {label} API key in {keys_path(cfg)} (readable by you only)")
    return 0


def _analyze_choice(value: str) -> str:
    value = value.strip().lower()
    if value in ("all", "later") or (value.isdigit() and int(value) > 0):
        return value
    raise argparse.ArgumentTypeError("use all, later or a number of sessions")


def add_install(sub) -> None:
    s = sub.add_parser("install", aliases=["setup"],
                       help="set up: pick which coding agents to record, import past sessions, start the dashboard")
    s.add_argument("-y", "--yes", action="store_true", help="don't ask: record every agent found (the default without a terminal)")
    s.add_argument("--no-sync", action="store_true", help="don't import past sessions now (the background sync will)")
    s.add_argument("--no-hooks", action="store_true")
    s.add_argument("--no-launchd", action="store_true", help="no 15-minute background sync (removes it if installed)")
    s.add_argument("--no-mcp", action="store_true")
    s.add_argument("--no-ui", action="store_true", help="don't keep the dashboard running in the background (removes it if installed)")
    s.add_argument("--inject-context", action="store_true", help="also add a SessionStart hook that injects project knowledge")
    s.add_argument("--statusline", action="store_true",
                   help="also record context and plan-limit usage from Claude Code's status line (keeps your own status line)")
    s.add_argument("--interval", type=int, default=15, help="background sync interval in minutes (default 15)")
    s.add_argument("--exe", help="command used by hooks/launchd (default: the installed `interlatch`)")
    s.add_argument("--analyze", type=_analyze_choice, metavar="all|N|later",
                   help="analyze past sessions now with progress (all, or the newest N), or later; asked when omitted "
                        "(later without a terminal). The Glossary and the Map are built from the analyzed sessions")
    s.add_argument("--notify-updates", action=argparse.BooleanOptionalAction, default=None,
                   help="show a desktop notification when a new version is out (asks pypi.org once a day); "
                        "asked once when omitted")
    s.add_argument("--menu-bar", action=argparse.BooleanOptionalAction, default=None,
                   help="macOS: show Interlatch's icon in the menu bar while the dashboard runs at login (needs the "
                        "`app` extra); asked once when omitted, off by default")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_install)


def add_uninstall(sub) -> None:
    s = sub.add_parser("uninstall", help="remove hooks, the background agent and the MCP server")
    s.add_argument("--purge", action="store_true", help="also delete all recorded data")
    s.set_defaults(fn=cmd_uninstall)


def add_ui(sub) -> None:
    s = sub.add_parser("ui", aliases=["serve"], help="open the local dashboard")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--open", action="store_true", help="open a browser tab")
    s.add_argument("--menu-bar", action=argparse.BooleanOptionalAction, default=None,
                   help="macOS: show Interlatch's menu-bar icon (default: only as the login item, with server.menu_bar on)")
    s.set_defaults(fn=cmd_serve)


def add_app(sub) -> None:
    s = sub.add_parser("app", help="open the desktop app (macOS; needs the `app` extra)")
    s.set_defaults(fn=cmd_app)


def add_config(sub) -> None:
    s = sub.add_parser("config", help="show or edit config.toml")
    s.add_argument("action", nargs="?", choices=["show", "path", "edit", "set", "set-key", "forget-key"], default="show")
    s.add_argument("key", nargs="?", help="with set: SECTION.KEY, e.g. analysis.backend or providers.ollama.model; "
                                          "with set-key / forget-key: the provider, e.g. openai, or bob for IBM Bob")
    s.add_argument("value", nargs="?", help="with set: the value (TOML, or a bare word); with set-key: the API key "
                                            "(asked for when left out, so it stays out of your shell history)")
    s.set_defaults(fn=cmd_config)


def add_migrate(sub) -> None:
    s = sub.add_parser("migrate", help="move Chronicle's data folder (~/.claude-chronicle) to ~/.interlatch and point "
                                       "hooks, MCP servers and background agents at the new names (runs by itself "
                                       "when the dashboard or the background sync starts)")
    s.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    s.set_defaults(fn=cmd_migrate)


def add_mcp(sub) -> None:
    s = sub.add_parser("mcp", help="run the MCP server (stdio); registered by `install` and `connect`")
    s.add_argument("--print-config", action="store_true", help="print a JSON entry to add Interlatch to any MCP client by hand")
    s.set_defaults(fn=cmd_mcp)
