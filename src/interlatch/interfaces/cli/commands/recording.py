"""Recording sessions: the hooks coding agents call, sync and the worker, connecting agents, and importing and
screening chat exports."""

from __future__ import annotations

import argparse
import json
import sys

from ..common import escape_markup, failed_label, load_cfg, new_console, open_archive, push_now


def cmd_hook(args) -> int:
    from ....runtime.hooks import hook_main

    return hook_main(args.event)


def cmd_ingest_session(args) -> int:
    """Used by the SessionEnd hook (runs detached): ingest one transcript, then process the queue."""
    from pathlib import Path

    from ....core.util import setup_logging
    from ....ingestion.service import sync

    cfg = load_cfg()
    setup_logging(cfg.logs_dir)
    if cfg.sends_files:
        return push_now(cfg, quiet=True)
    conn = open_archive(cfg)
    path = Path(args.transcript).expanduser() if args.transcript else None
    if path and path.exists():
        sync(cfg, conn, only=path, ended=args.ended)
    else:
        sync(cfg, conn)
    conn.close()
    if args.work:
        from ....application.workflows import run_worker
        from ...exports.vault import export_markdown

        run_worker(cfg, export=export_markdown, wait=False)
    return 0


def cmd_sync(args) -> int:
    from ....core.util import setup_logging
    from ....ingestion.service import sync
    from ....runtime.migrate import migrate_if_due
    from ....runtime.notify import release_check_safely

    migrate_if_due(quiet=args.quiet)  # the background sync: Chronicle's folder becomes Interlatch's
    cfg = load_cfg()
    setup_logging(cfg.logs_dir, verbose=args.verbose)
    release_check_safely(cfg)  # [updates] notify: at most one PyPI request a day, one notification per release
    if cfg.sends_files:  # the hub records and analyzes; this computer only sends it files
        return push_now(cfg, quiet=args.quiet)
    conn = open_archive(cfg)
    report = sync(cfg, conn, force=args.force)
    if not args.quiet:
        print(f"sync: {report.summary()}")
        for err in report.errors[:10]:
            print(f"  ! {err}", file=sys.stderr)
    conn.close()
    if args.work:
        from ....application.workflows import run_worker
        from ...exports.vault import export_markdown

        work = run_worker(cfg, export=export_markdown, progress=None if args.quiet else (lambda m: print(f"  {m}")))
        if not args.quiet:
            print(f"work: {work.summary()}")
    from ....sharing.leaderboard import send_safely

    conn = open_archive(cfg)
    send_safely(cfg, conn)  # once signed in to the leaderboard (`interlatch leaderboard join`)
    conn.close()
    return 0


def cmd_work(args) -> int:
    from ....application.workflows import run_worker
    from ....core.util import setup_logging
    from ...exports.vault import export_markdown

    cfg = load_cfg()
    setup_logging(cfg.logs_dir)
    if cfg.sends_files:
        print(f"This computer sends its sessions to the hub at {cfg.hub_url}, which analyzes them.")
        return 0
    report = run_worker(cfg, max_analyses=args.limit, analyze=not args.no_analyze, synthesize=not args.no_synthesize,
                        export=None if args.no_export else export_markdown, force=args.force, model=args.model, wait=True,
                        progress=lambda m: print(f"  {m}"))
    print(report.summary())
    for sid, err in report.failed:
        print(f"  ! {failed_label(sid)}: {err[:200]}", file=sys.stderr)
    return 0 if not report.failed else 1


def cmd_sources(args) -> int:
    from ....core.util import local_str
    from ....runtime.connectors import all_status, mcp_clients_status

    cfg = load_cfg()
    conn = open_archive(cfg)
    console = new_console()
    for c in all_status(cfg, conn):
        state = "[green]connected[/]" if c["connected"] else ("[yellow]detected, not connected[/]" if c["detected"] else "[dim]not installed[/]")
        console.print(f"[bold]{c['label']}[/] ({c['vendor']}) · {state} · {c['version'] or 'version unknown'}", highlight=False)
        rec = c["recorded"]
        console.print(f"  {c['on_disk']} sessions {c.get('on_disk_label', 'on disk')} · {rec['sessions']} recorded ({rec['analyzed']} analyzed) · "
                      f"last {local_str(rec['last_session'])} · {c['recording']}", highlight=False)
        for chk in c["checks"]:
            mark = "[green]✓[/]" if chk["ok"] else ("[dim]–[/]" if chk["ok"] is None or chk.get("optional") else "[red]✗[/]")
            console.print(f"  {mark} {chk['label']}: [dim]{chk['detail']}[/]", highlight=False)
        console.print()
    console.print("[bold]Other MCP clients[/] [dim](not recorded; `interlatch connect <name>` gives them Interlatch's MCP tools)[/]", highlight=False)
    for c in mcp_clients_status():
        state = "[green]✓ MCP server added[/]" if c["registered"] else ("[yellow]detected[/]" if c["detected"] else "[dim]not installed[/]")
        console.print(f"  {c['label']} [dim]({c['name']})[/] · {state} · [dim]{c['config']}[/]", highlight=False)
    return 0


def cmd_import(args) -> int:
    from pathlib import Path

    from ....ingestion.chat_import import ExportError, import_export, summary

    cfg = load_cfg()
    conn = open_archive(cfg)
    try:
        counts = import_export(cfg, conn, Path(args.path), analyze=args.analyze, progress=lambda m: print(m, file=sys.stderr))
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(summary(counts))
    if args.screen and not args.analyze:
        return _screen_run(cfg, conn, source=None, redo=False, limit=None, sample=False)
    if counts["new"] + counts["updated"]:
        print("queued for analysis (`interlatch work` runs it now)" if args.analyze
              else "not analyzed: `interlatch screen` sorts out which chats are worth analyzing (reading only their "
                   "openings); or open one and choose Analyze now, or re-run with --analyze to queue them all")
    return 0


def _screen_run(cfg, conn, *, source, redo, limit, sample) -> int:
    from ....analysis.screen import SOURCES, screen_chats, screen_status
    from ....core.util import human_cost

    report = screen_chats(cfg, conn, source=source, redo=redo, limit=limit, sample=sample,
                          progress=lambda m: print(m, file=sys.stderr))
    print(report.summary() + (f" · screening cost {human_cost(report.cost_usd)}" if report.calls else ""))
    worth = sum(screen_status(conn, s)["to_queue"] for s in SOURCES)
    if worth:
        print(f"next: `interlatch screen --list analyze` shows them, `interlatch screen --queue` queues the {worth:,} worth "
              "analyzing (add --maybe for the maybes too)")
    return 1 if report.left and not report.screened else 0


def cmd_screen(args) -> int:
    from ....analysis.screen import BATCH, candidates, queue, rule_verdict

    cfg = load_cfg()
    conn = open_archive(cfg)
    if args.queue:
        n = queue(conn, source=args.source, maybe=args.maybe)
        print(f"queued {n:,} chat{'' if n == 1 else 's'} for analysis: the background agent analyzes "
              f"{cfg.analysis.max_per_run} every 15 minutes, newest first (`interlatch analyze --pending` runs them now)"
              if n else "nothing to queue: screen the chats first (`interlatch screen`), or they are queued already")
        return 0
    if args.list:
        return _screen_list(conn, args)
    if args.dry_run:
        rows = candidates(conn, source=args.source, redo=args.redo, limit=args.sample or args.limit, sample=bool(args.sample))
        ruled = sum(rule_verdict(r) is not None for r in rows)
        rest = len(rows) - ruled
        print(f"{len(rows):,} chats to screen: {ruled:,} settled by rules, {rest:,} for {cfg.analysis.screen_model} in "
              f"{-(-rest // BATCH)} call{'' if -(-rest // BATCH) == 1 else 's'}; nothing sent (dry run)")
        return 0
    return _screen_run(cfg, conn, source=args.source, redo=args.redo, limit=args.sample or args.limit, sample=bool(args.sample))


def _screen_list(conn, args) -> int:
    from rich import box
    from rich.table import Table

    from ....analysis.screen import VERDICT_LABEL, source_filter

    where, params = source_filter(args.source)
    rows = conn.execute(
        f"SELECT id, started_at, n_prompts, title, screen_topic, screen_reason, analysis_status FROM sessions "
        f"WHERE {where} AND screen_verdict = ? AND screen_sig = files_sig ORDER BY started_at DESC LIMIT ?",
        [*params, args.list, args.limit or 100_000]).fetchall()
    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print(f"no chats screened as {args.list}")
        return 0
    console = new_console()
    t = Table(header_style="bold", box=box.SIMPLE_HEAD, pad_edge=False)
    for col in ("id", "date", "prompts", "chat", "why"):
        t.add_column(col, justify="right" if col == "prompts" else "left", no_wrap=col in ("id", "date"),
                     overflow="fold" if col in ("chat", "why") else "ellipsis")
    for r in rows:
        title = escape_markup(r["title"] or "(untitled)") + (f" [dim]· {escape_markup(r['screen_topic'])}[/]" if r["screen_topic"] else "")
        t.add_row(r["id"][:8], (r["started_at"] or "")[:10], str(r["n_prompts"] or 0), title,
                  escape_markup(r["screen_reason"] or "") + (" [green](analyzed)[/]" if r["analysis_status"] == "done" else ""))
    console.print(t)
    print(f"{len(rows):,} chat{'' if len(rows) == 1 else 's'} screened as {args.list} ({VERDICT_LABEL[args.list]})")
    return 0


def cmd_connect(args) -> int:
    from ....runtime.connectors import MCP_CLIENTS, connect, disconnect
    from ....runtime.install import executable

    cfg = load_cfg()
    if args.disconnect:
        actions = disconnect(cfg, args.name)
    else:
        actions = connect(cfg, args.name, args.exe or executable())
    for a in actions:
        print(f"• {a}")
    if not args.disconnect and not args.no_sync and args.name not in MCP_CLIENTS:
        from ....core.config import load_config
        from ....ingestion.service import sync

        cfg = load_config(cfg.home)
        print("syncing…")
        report = sync(cfg, open_archive(cfg))
        print(f"sync: {report.summary()}")
    return 0


CONNECT_NAMES = ["claude", "codex", "codex-cloud", "copilot", "bob", "antigravity", "opencode",  # coding agents to record
                 "claude-desktop", "cursor", "windsurf", "gemini"]  # MCP clients: connect and disconnect take these


def add_sync(sub) -> None:
    s = sub.add_parser("sync", help="archive and ingest all transcripts")
    s.add_argument("--work", action="store_true", help="then analyze/synthesize/export (the background job does this)")
    s.add_argument("--force", action="store_true", help="re-parse every session")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_sync)


def add_work(sub) -> None:
    s = sub.add_parser("work", help="process the analysis queue, synthesize knowledge bases, export notes")
    s.add_argument("--limit", type=int, help="max sessions to analyze this run")
    s.add_argument("--model")
    s.add_argument("--force", action="store_true", help="synthesize even below the threshold")
    s.add_argument("--no-analyze", action="store_true")
    s.add_argument("--no-synthesize", action="store_true")
    s.add_argument("--no-export", action="store_true")
    s.set_defaults(fn=cmd_work)


def add_sources(sub) -> None:
    s = sub.add_parser("sources", aliases=["connectors"], help="which coding agents are connected and how")
    s.set_defaults(fn=cmd_sources)


def add_connect(sub) -> None:
    s = sub.add_parser("connect", help="start recording an agent (claude, codex, codex-cloud, copilot, bob, antigravity, opencode), or give an MCP client "
                                       "(claude-desktop, cursor, windsurf, gemini) Interlatch's MCP server")
    s.add_argument("name", choices=CONNECT_NAMES)
    s.add_argument("--exe", help="command the agent should run for Interlatch's MCP server")
    s.add_argument("--no-sync", action="store_true")
    s.set_defaults(fn=cmd_connect, disconnect=False)


def add_disconnect(sub) -> None:
    s = sub.add_parser("disconnect", help="stop recording an agent, or remove the MCP server from a client (recorded sessions are kept)")
    s.add_argument("name", choices=CONNECT_NAMES)
    s.set_defaults(fn=cmd_connect, disconnect=True, exe=None, no_sync=True)


def add_import(sub) -> None:
    s = sub.add_parser("import", help="import chats from a claude.ai or ChatGPT data export (the .zip, its folder, or conversations.json)")
    s.add_argument("path")
    s.add_argument("--analyze", action="store_true", help="queue the imported chats for analysis (uses your Claude plan)")
    s.add_argument("--screen", action="store_true", help="then screen them: which are worth analyzing (`interlatch screen`)")
    s.set_defaults(fn=cmd_import)


def add_screen(sub) -> None:
    s = sub.add_parser("screen", help="sort imported chats into worth analyzing, maybe and not worth it, reading only "
                                      "their openings; nothing is analyzed until you queue them")
    s.add_argument("--source", choices=["chatgpt", "claude-ai"], help="only this export (default: both)")
    s.add_argument("--limit", type=int, help="screen (or list) at most N chats, newest first")
    s.add_argument("--sample", type=int, metavar="N", help="screen N chats picked at random, to try it first")
    s.add_argument("--redo", action="store_true", help="screen chats again that were screened already")
    s.add_argument("--dry-run", action="store_true", help="count what would be screened; nothing is sent")
    s.add_argument("--list", choices=["analyze", "maybe", "skip"], help="show the chats screened as this, with why")
    s.add_argument("--json", action="store_true", help="with --list: as JSON")
    s.add_argument("--queue", action="store_true", help="queue the chats worth analyzing for the background analysis")
    s.add_argument("--maybe", action="store_true", help="with --queue: the maybes too")
    s.set_defaults(fn=cmd_screen)


def add_hook(sub) -> None:
    s = sub.add_parser("hook", help=argparse.SUPPRESS)
    s.add_argument("event", choices=["session-end", "session-start", "stop", "pre-compact"])
    s.set_defaults(fn=cmd_hook)


def add_ingest_session(sub) -> None:
    s = sub.add_parser("ingest-session", help=argparse.SUPPRESS)
    s.add_argument("transcript", nargs="?")
    s.add_argument("--ended", action="store_true")
    s.add_argument("--work", action="store_true")
    s.set_defaults(fn=cmd_ingest_session)
