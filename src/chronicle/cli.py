"""Command line interface: `interlatch <command>` (or `chronicle <command>`, its name before the rename)."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__


def _cfg():
    from .config import load_config

    cfg = load_config()
    cfg.ensure_dirs()
    return cfg


def _conn(cfg):
    from .db import connect

    return connect(cfg.db_path)


def _console():
    from rich.console import Console

    return Console()


def _since(value: str | None) -> str | None:
    """'7d', '12h', '2w' or an ISO date -> ISO UTC timestamp."""
    import re
    from datetime import timedelta

    from .util import parse_ts, to_iso, utcnow

    if not value:
        return None
    m = re.fullmatch(r"(\d+)([hdw])", value.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
        return to_iso(utcnow() - delta)
    dt = parse_ts(value)
    return to_iso(dt) if dt else None


# ---------------------------------------------------------------------------- commands
def cmd_hook(args) -> int:
    from .hooks import hook_main

    return hook_main(args.event)


def cmd_ingest_session(args) -> int:
    """Used by the SessionEnd hook (runs detached): ingest one transcript, then process the queue."""
    from pathlib import Path

    from .ingest import sync
    from .util import setup_logging

    cfg = _cfg()
    setup_logging(cfg.logs_dir)
    if cfg.sends_files:
        return _push(cfg, quiet=True)
    conn = _conn(cfg)
    path = Path(args.transcript).expanduser() if args.transcript else None
    if path and path.exists():
        sync(cfg, conn, only=path, ended=args.ended)
    else:
        sync(cfg, conn)
    conn.close()
    if args.work:
        from .worker import run_worker

        run_worker(cfg, wait=False)
    return 0


def cmd_sync(args) -> int:
    from .ingest import sync
    from .migrate import migrate_if_due
    from .notify import release_check_safely
    from .util import setup_logging

    migrate_if_due(quiet=args.quiet)  # the background sync: Chronicle's folder becomes Interlatch's
    cfg = _cfg()
    setup_logging(cfg.logs_dir, verbose=args.verbose)
    release_check_safely(cfg)  # [updates] notify: at most one PyPI request a day, one notification per release
    if cfg.sends_files:  # the hub records and analyzes; this computer only sends it files
        return _push(cfg, quiet=args.quiet)
    conn = _conn(cfg)
    report = sync(cfg, conn, force=args.force)
    if not args.quiet:
        print(f"sync: {report.summary()}")
        for err in report.errors[:10]:
            print(f"  ! {err}", file=sys.stderr)
    conn.close()
    if args.work:
        from .worker import run_worker

        work = run_worker(cfg, progress=None if args.quiet else (lambda m: print(f"  {m}")))
        if not args.quiet:
            print(f"work: {work.summary()}")
    return 0


def cmd_work(args) -> int:
    from .util import setup_logging
    from .worker import run_worker

    cfg = _cfg()
    setup_logging(cfg.logs_dir)
    if cfg.sends_files:
        print(f"This computer sends its sessions to the hub at {cfg.hub_url}, which analyzes them.")
        return 0
    report = run_worker(cfg, max_analyses=args.limit, analyze=not args.no_analyze, synthesize=not args.no_synthesize,
                        export=not args.no_export, force=args.force, model=args.model, wait=True,
                        progress=lambda m: print(f"  {m}"))
    print(report.summary())
    for sid, err in report.failed:
        print(f"  ! {sid[:8]}: {err[:200]}", file=sys.stderr)
    return 0 if not report.failed else 1


def cmd_analyze(args) -> int:
    from .util import human_cost, setup_logging
    from .views import resolve_session_id
    from .worker import pending_sessions, run_worker

    cfg = _cfg()
    setup_logging(cfg.logs_dir)
    conn = _conn(cfg)
    ids: list[str] = []
    for ref in args.sessions:
        sid = resolve_session_id(conn, ref)
        if not sid:
            print(f"no unique session matches {ref!r}", file=sys.stderr)
            return 2
        ids.append(sid)
    if args.all or args.pending:
        if args.all and args.force:
            conn.execute("UPDATE sessions SET analysis_status = 'pending', analysis_attempts = 0 "
                         "WHERE source NOT IN ('history', 'remote') AND analysis_status IN ('done','error','stale')")
            conn.commit()
        ids += pending_sessions(conn, cfg, args.limit or 100_000)
    if not ids:
        print("nothing to analyze (use SESSION ids, --pending or --all)")
        return 0
    from .llm import make_runner

    if args.backend:
        cfg.analysis.backend = args.backend
    runner = make_runner(cfg)
    if args.dry_run:
        from .digest import build_digest

        total = 0
        for sid in ids:
            header, d = build_digest(conn, sid, runner.chunk_chars)
            total += d.chars
            print(f"{sid[:8]}  level {d.level}  {d.chars:>9,} chars  {len(d.chunks)} chunk(s)")
        print(f"{len(ids)} session(s), {total:,} chars (~{total // 4:,} input tokens) with {runner.label} "
              f"({runner.model_label(args.model)})")
        return 0
    conn.close()
    if args.concurrency:
        cfg.analysis.concurrency = args.concurrency
    print(f"analyzing {len(ids)} session(s) with {runner.label} ({runner.model_label(args.model)})…")
    report = run_worker(cfg, session_ids=ids, model=args.model, synthesize=not args.no_synthesize, force=True, wait=True,
                        progress=lambda m: print(f"  {m}"))
    print(f"{report.summary()} · analysis cost {human_cost(report.cost_usd)}")
    for sid, err in report.failed:
        print(f"  ! {sid[:8]}: {err[:300]}", file=sys.stderr)
    return 0 if not report.failed else 1


def cmd_synthesize(args) -> int:
    from .synthesize import GLOBAL, projects_needing_synthesis, synthesize_project
    from .util import setup_logging

    cfg = _cfg()
    setup_logging(cfg.logs_dir)
    conn = _conn(cfg)
    targets = []
    if args.project:
        row = conn.execute("SELECT project_path FROM sessions WHERE project_path = ? OR project_name = ? LIMIT 1",
                           (args.project, args.project)).fetchone()
        targets.append(row[0] if row else args.project)
    if args.all:
        targets += projects_needing_synthesis(conn, cfg, force=True)
    if args.global_ or args.all:
        targets.append(GLOBAL)
    if not targets:
        targets = projects_needing_synthesis(conn, cfg)
    for t in dict.fromkeys(targets):
        print(f"synthesizing {t}…")
        try:
            data = synthesize_project(conn, cfg, t)
            print(f"  {sum(len(s.get('items') or []) for s in data.get('sections') or [])} bullets, "
                  f"{len(data.get('superseded_ids') or [])} items superseded")
        except Exception as exc:
            conn.rollback()  # never carry a half-written transaction (and its lock) into the next target
            print(f"  ! {exc}", file=sys.stderr)
    return 0


def cmd_sessions(args) -> int:
    from rich.table import Table

    from .util import human_cost, human_count, human_duration, local_str

    cfg = _cfg()
    conn = _conn(cfg)
    where, params = ["1=1"], []
    if args.project:
        where.append("(project_path = ? OR project_name = ?)")
        params += [args.project, args.project]
    since = _since(args.since)
    if since:
        where.append("started_at >= ?")
        params.append(since)
    if args.status:
        where.append("analysis_status = ?")
        params.append(args.status)
    rows = conn.execute(
        f"SELECT id, started_at, project_name, title, n_prompts, n_tool_calls, active_s, "
        f"input_tokens+output_tokens+cache_read_tokens+cache_write_tokens tokens, est_cost_usd, outcome, analysis_status, source "
        f"FROM sessions WHERE {' AND '.join(where)} ORDER BY started_at DESC LIMIT ?", [*params, args.limit]).fetchall()
    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1))
        return 0
    t = Table(show_lines=False, header_style="bold", pad_edge=False)
    for col, kw in [("id", {}), ("started", {}), ("project", {}), ("title", {"max_width": 60}), ("prompts", {"justify": "right"}),
                    ("tools", {"justify": "right"}), ("active", {"justify": "right"}), ("tokens", {"justify": "right"}),
                    ("est.$", {"justify": "right"}), ("outcome", {})]:
        t.add_column(col, **kw)
    for r in rows:
        outcome = r["outcome"] or ("history" if r["source"] == "history" else f"[dim]{r['analysis_status']}[/dim]")
        t.add_row(r["id"][:8], local_str(r["started_at"], "%m-%d %H:%M"), r["project_name"] or "-", r["title"] or "",
                  str(r["n_prompts"] or 0), str(r["n_tool_calls"] or 0), human_duration(r["active_s"]),
                  human_count(r["tokens"]), human_cost(r["est_cost_usd"]), outcome)
    _console().print(t)
    return 0


def cmd_show(args) -> int:
    from rich.markdown import Markdown

    from .views import resolve_session_id, session_markdown, session_record

    cfg = _cfg()
    conn = _conn(cfg)
    sid = resolve_session_id(conn, args.session)
    if not sid:
        print(f"no unique session matches {args.session!r}", file=sys.stderr)
        return 2
    s = session_record(conn, sid)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
        return 0
    if args.transcript:
        from .util import local_str

        for r in conn.execute("SELECT ts, kind, tool_name, is_error, text FROM events WHERE session_id = ? AND agent_id = '' "
                              "AND kind IN ('prompt','text','tool_use','command','interrupt','compact') ORDER BY seq", (sid,)):
            label = {"prompt": "[bold cyan]USER[/]", "text": "[bold]CLAUDE[/]", "tool_use": "[dim]  →[/]",
                     "command": "[magenta]CMD[/]", "interrupt": "[red]INTERRUPT[/]", "compact": "[yellow]COMPACT[/]"}[r["kind"]]
            text = r["text"] if r["kind"] in ("prompt", "text") else r["text"][:200]
            _console().print(f"{label} [dim]{local_str(r['ts'], '%H:%M')}[/] ", end="")
            _console().print(text, markup=False, highlight=False)
        return 0
    md = session_markdown(s)
    if args.markdown:
        print(md)
    else:
        _console().print(Markdown(md))
    return 0


def cmd_search(args) -> int:
    from rich.markup import escape

    from .search import search_knowledge, search_sessions
    from .util import local_str

    cfg = _cfg()
    conn = _conn(cfg)
    console = _console()
    query = " ".join(args.query)
    if not args.sessions_only:
        ks = search_knowledge(conn, query, project=args.project, limit=args.limit)
        if ks:
            console.print(f"[bold]Knowledge[/] ({len(ks)})")
            for k in ks:
                console.print(f"  [cyan]{k['kind']:<10}[/] [bold]{k['title']}[/]  [dim]{k.get('project_name') or ''} "
                              f"{(k.get('session_id') or '')[:8]}[/]", highlight=False)
            console.print()
    if not args.knowledge_only:
        ss = search_sessions(conn, query, project=args.project, limit=args.limit)
        console.print(f"[bold]Sessions[/] ({len(ss)})")
        for s in ss:
            console.print(f"  [green]{s['session_id'][:8]}[/] {local_str(s['started_at'], '%Y-%m-%d')} "
                          f"[bold]{s['title']}[/] [dim]{s['project_name']} · {s['hits']} hits[/]", highlight=False)
            for sn in s["snippets"][:2]:
                console.print(f"      [dim]{sn['kind']}:[/] {escape(sn['text'])}", highlight=False)
    return 0


def cmd_knowledge(args) -> int:
    from .ladder import stage_label
    from .search import search_knowledge

    cfg = _cfg()
    conn = _conn(cfg)
    console = _console()
    rows = search_knowledge(conn, args.query, project=args.project, kind=args.kind, limit=args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1, default=str))
        return 0
    for k in rows:
        console.print(f"[cyan]{k['kind']}[/] [bold]{k['title']}[/] [dim]({k.get('project_name') or '-'}, {stage_label(k)}, "
                      f"{k.get('confidence') or '-'}, {(k.get('session_id') or k['source'])[:8]})[/]", highlight=False)
        if not args.brief:
            console.print(f"  {k.get('body') or ''}\n", highlight=False, markup=False)
    if not rows:
        print("no knowledge found")
    return 0


def cmd_projects(args) -> int:
    from rich.table import Table

    from .util import human_cost, human_duration, local_str
    from .views import project_labels

    cfg = _cfg()
    conn = _conn(cfg)
    labels = project_labels(conn)
    t = Table(header_style="bold")
    for col in ("project", "sessions", "active", "est.$", "knowledge", "KB", "last"):
        t.add_column(col, justify="right" if col in ("sessions", "active", "est.$", "knowledge") else "left")
    for r in conn.execute(
        "SELECT project_path, COUNT(*) n, SUM(active_s) a, SUM(est_cost_usd) c, MAX(started_at) last FROM sessions "
        "GROUP BY project_path ORDER BY last DESC"):
        k = conn.execute("SELECT COUNT(*) FROM knowledge WHERE project_path = ? AND status='active'", (r["project_path"],)).fetchone()[0]
        kb = conn.execute("SELECT updated_at FROM project_kb WHERE project_path = ?", (r["project_path"],)).fetchone()
        t.add_row(labels.get(r["project_path"] or "", "?"), str(r["n"]), human_duration(r["a"]), human_cost(r["c"]), str(k),
                  local_str(kb[0], "%m-%d") if kb else "-", local_str(r["last"], "%Y-%m-%d"))
    _console().print(t)
    return 0


def cmd_stats(args) -> int:
    from rich.table import Table

    from .util import human_cost, human_count, human_duration

    cfg = _cfg()
    conn = _conn(cfg)
    since = _since(args.since) or "0000"
    t = conn.execute(
        "SELECT COUNT(*) n, SUM(n_prompts) p, SUM(n_tool_calls) tc, SUM(n_tool_errors) te, SUM(active_s) a, "
        "SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens) tok, SUM(est_cost_usd) c, SUM(lines_added) la, "
        "SUM(lines_removed) lr, SUM(n_subagents) sa, COUNT(DISTINCT project_path) pr FROM sessions WHERE started_at >= ?",
        (since,)).fetchone()
    console = _console()
    console.print(f"[bold]{t['n']}[/] sessions in [bold]{t['pr']}[/] projects · {t['p'] or 0} prompts · "
                  f"{human_count(t['tc'])} tool calls ({t['te'] or 0} failed) · {human_duration(t['a'])} active · "
                  f"{human_count(t['tok'])} tokens · est. {human_cost(t['c'])} · +{t['la'] or 0}/−{t['lr'] or 0} lines · "
                  f"{t['sa'] or 0} subagents", highlight=False)
    tools = Table(title="Top tools", header_style="bold")
    for col in ("tool", "calls", "errors"):
        tools.add_column(col, justify="left" if col == "tool" else "right")
    for r in conn.execute(
        "SELECT name, COUNT(*) n, SUM(is_error) e FROM tool_calls t JOIN sessions s ON s.id = t.session_id "
        "WHERE s.started_at >= ? GROUP BY name ORDER BY n DESC LIMIT 12", (since,)):
        tools.add_row(r["name"], str(r["n"]), str(r["e"] or 0))
    models = Table(title="Models", header_style="bold")
    for col in ("model", "tokens", "est.$"):
        models.add_column(col, justify="left" if col == "model" else "right")
    for r in conn.execute(
        "SELECT a.model, SUM(a.input_tokens+a.output_tokens+a.cache_read_tokens+a.cache_write_tokens) tok, SUM(a.cost_usd) c "
        "FROM api_calls a JOIN sessions s ON s.id = a.session_id WHERE s.started_at >= ? GROUP BY a.model ORDER BY c DESC",
        (since,)):
        models.add_row(r["model"] or "?", human_count(r["tok"]), human_cost(r["c"]))
    console.print(tools)
    console.print(models)
    return 0


def cmd_status(args) -> int:
    from .db import kv_get
    from .install import UI_LABEL, hooks_installed, launchd_status, mcp_registered, statusline_installed
    from .statusline import WINDOW_LABEL, plan_usage
    from .llm import make_runner
    from .util import human_cost, local_str
    from .worker import PAUSE_KEY, QUEUE_REASON_LABEL, count_pending

    cfg = _cfg()
    conn = _conn(cfg)
    console = _console()
    ok = lambda b: "[green]✓[/]" if b else "[red]✗[/]"  # noqa: E731
    hooks = hooks_installed(cfg)
    ld = launchd_status()
    counts = {r[0]: r[1] for r in conn.execute("SELECT analysis_status, COUNT(*) FROM sessions GROUP BY 1")}
    pending = count_pending(conn, cfg)
    spent = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM analyses").fetchone()[0]
    console.print(f"[bold]Interlatch[/] · home {cfg.home}")
    from .hub import last_push, read_token

    if cfg.is_spoke:
        last = last_push(cfg)
        console.print(f"  sends {'what it learns' if cfg.shares_knowledge else 'its sessions'} to the hub at {cfg.hub_url}"
                      f" (last push: {local_str(last['at']) + ' · ' + last['summary'] if last else 'never'})", highlight=False)
    elif read_token(cfg):
        others = conn.execute("SELECT COUNT(*) FROM machines WHERE role = 'spoke'").fetchone()[0]
        console.print(f"  hub for {others} other computer{'s' * (others != 1)} (`interlatch hub status`)")
    console.print(f"  {ok(hooks.get('SessionEnd'))} SessionEnd hook   {ok(hooks.get('SessionStart'))} SessionStart context hook"
                  f"{' (optional)' if not cfg.inject_session_start else ''}")
    plan = plan_usage(conn)
    limits = " · ".join(f"{WINDOW_LABEL[k]} {v['used_pct']:.0f}% (resets {local_str(v['resets_at'], '%m-%d %H:%M')})"
                        for k, v in ((plan or {}).get("limits") or {}).items())
    console.print(f"  {ok(statusline_installed(cfg))} status-line usage collector"
                  + (f": plan limits {limits}, as of {local_str(plan['as_of'])}" if limits
                     else " (optional: interlatch install --statusline)" if not statusline_installed(cfg)
                     else ": no plan limits seen yet (Pro and Max plans only)"), highlight=False)
    ui = launchd_status(UI_LABEL)
    console.print(f"  {ok(ld.get('loaded'))} background sync agent (runs: {ld.get('runs', '-')}, last exit: {ld.get('last_exit', '-')})")
    console.print(f"  {ok(ui.get('loaded'))} dashboard agent: http://127.0.0.1:{cfg.server_port}/")
    runner = make_runner(cfg)
    console.print(f"  {ok(mcp_registered())} MCP server registered   {ok(runner.available())} analysis by {runner.label}: "
                  f"{runner.where() if runner.available() else runner.unavailable_reason()}")
    console.print(f"  sessions: {sum(counts.values())} · " + " · ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    console.print(f"  analysis queue: {pending['ready']} ready now, {pending['queued']} queued in all, {pending['held']} held · "
                  f"model {runner.model_label()} · auto={'on' if cfg.analysis.auto else 'off'} · spent {human_cost(spent)}")
    if pending["reasons"]:
        console.print("    waiting because: " + " · ".join(f"{QUEUE_REASON_LABEL.get(k, k)}: {v}" for k, v in sorted(pending["reasons"].items())))
    if pending["block"]:
        console.print(f"    [yellow]{pending['block']}[/]", highlight=False)
    paused = kv_get(conn, PAUSE_KEY)
    if paused:
        console.print(f"  analysis paused until {local_str(paused)}")
    console.print(f"  last sync: {local_str(kv_get(conn, 'last_sync'))} · db {cfg.db_path.stat().st_size / 1e6:.0f} MB · notes {cfg.notes_dir}")
    for r in conn.execute("SELECT id, analysis_reason FROM sessions WHERE analysis_status='error' ORDER BY ended_at DESC LIMIT 5"):
        console.print(f"  [red]error[/] {r['id'][:8]}: {(r['analysis_reason'] or '')[:160]}", highlight=False)
    return 0


def _ask(question: str, default: bool) -> bool:
    try:
        answer = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    except EOFError:
        return default
    return default if not answer else answer.startswith("y")


def _setup_choices(cfg, conn, console, *, ask) -> tuple[list[str], list[str]]:
    """Show the agents found on this machine; return (sources to connect, MCP-only clients to register)."""
    from .connectors import all_status, mcp_clients_status
    from .install import hooks_installed

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


def _wait_for_port(port: int, seconds: float = 8.0) -> bool:
    import socket
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


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

    from .worker import pending_sessions

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
        from .synthesize import GLOBAL
        from .util import PROGRESS_RE

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

    from .worker import run_worker

    console.print(f"\nAnalyzing {len(ids)} session{'s' * (len(ids) != 1)}. Ctrl-C stops; what is left is analyzed {later}.",
                  highlight=False)
    try:
        with Progress(SpinnerColumn(), TextColumn("{task.description:<16}"), BarColumn(), TextColumn("{task.fields[detail]}"),
                      TimeElapsedColumn(), console=console) as bar:
            stages = _StageProgress(bar)
            report = run_worker(cfg, session_ids=ids, force=True, wait=True, progress=stages)
            stages.finish()
    except KeyboardInterrupt:
        console.print(f"[yellow]Stopped.[/] The rest is analyzed {later}; the Glossary and the Map are built after.",
                      highlight=False)
        return f"Sessions still waiting for analysis are analyzed {later}."
    conn = _conn(cfg)
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
        from .util import local_str

        console.print(f"  [yellow]{_analyzer(cfg)} usage limit reached[/]: analysis resumes after "
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
    from .db import kv_get
    from .update import compares_online

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

    from .db import kv_get

    if args.menu_bar is not None:
        return args.menu_bar
    if (ask is None or platform.system() != "Darwin" or not login_dashboard or cfg.server_menu_bar
            or kv_get(conn, MENU_BAR_ASKED_KEY)):
        return None
    return ask("Show Interlatch's icon in the menu bar? (what it's doing, a search and your recent sessions, a click "
               "away)", False)


def cmd_install(args) -> int:
    from .connectors import connect, disconnect
    from .db import kv_get, kv_set
    from .ingest import sync
    from .install import (LAUNCHD_LABEL, UI_LABEL, agent_path, background_supported, executable, install_hooks,
                          install_launchd, install_mcp, install_statusline, install_ui_agent, launchd_status,
                          statusline_installed, uninstall_launchd)
    from .util import utcnow_iso

    if not args.dry_run:
        from .migrate import migrate_if_due

        migrate_if_due(quiet=False)
    cfg = _cfg()
    conn = _conn(cfg)
    console = _console()
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
            _set_config_value(cfg, "sources", "claude_dirs", '["~/.claude"]')
        if not args.no_hooks:
            actions += install_hooks(cfg, exe, inject=args.inject_context or cfg.inject_session_start, dry_run=args.dry_run)
        if args.inject_context and not args.dry_run:
            _set_config_value(cfg, "inject", "session_start", "true")
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
        _set_config_value(cfg, "updates", "notify", "true" if notify else "false")
        kv_set(conn, NOTIFY_ASKED_KEY, utcnow_iso())
        conn.commit()
        actions.append("a desktop notification when a new version is out (turn off in Status › Updates)" if notify
                       else "no release notifications (turn on in Status › Updates)")
    if menu_bar is not None:  # before the dashboard agent (re)starts below, so it reads the new setting
        _set_config_value(cfg, "server", "menu_bar", "true" if menu_bar else "false")
        kv_set(conn, MENU_BAR_ASKED_KEY, utcnow_iso())
        conn.commit()
        actions.append(_menu_bar_line(menu_bar))
    for a in actions:
        console.print(f"• {a}", highlight=False, soft_wrap=True)

    from .config import load_config

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
        if interactive and first_install and ask("Open it now?", True) and _wait_for_port(cfg.server_port):
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


def _analyzer(cfg) -> str:
    from .llm import make_runner

    return make_runner(cfg).label


def _choose_analyzer(cfg, console, *, ask, dry_run: bool):
    """Settle which agent analyzes sessions; return its runner, or None when it is not installed."""
    from .llm import BACKENDS, make_runner

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
            _set_config_value(cfg, "analysis", "backend", json.dumps(other))
        cfg.analysis.backend = other
        return make_runner(cfg)
    console.print(f"[yellow]{missing}[/] Interlatch analyzes sessions through your own Claude Code or Codex login, or a "
                  "model provider's API (Status › Analysis in the dashboard); until one is set up (and chosen as "
                  "analysis.backend), sessions are recorded but not analyzed, so the Glossary and the Map stay empty.\n",
                  highlight=False)
    return None


def _set_config_value(cfg, section: str, key: str, value: str) -> None:
    from .config import set_config_value

    set_config_value(cfg, section, key, value)


def cmd_uninstall(args) -> int:
    import shutil

    from .connectors import mcp_clients_status, remove_mcp_client
    from .install import uninstall_hooks, uninstall_launchd, uninstall_mcp, uninstall_statusline

    cfg = _cfg()
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
    from .migrate import migrate_if_due
    from .server import serve
    from .util import setup_logging

    migrate_if_due(quiet=False)  # the dashboard at login: Chronicle's folder becomes Interlatch's
    cfg = _cfg()
    setup_logging(cfg.logs_dir)
    serve(cfg, host=args.host, port=args.port, open_browser=args.open, menu_bar=args.menu_bar)
    return 0


def cmd_app(args) -> int:
    from .desktop import main

    return main()


def cmd_migrate(args) -> int:
    """`interlatch migrate`: Chronicle's folder and settings become Interlatch's (migrate.py)."""
    from .migrate import migrate

    lines = migrate(dry_run=args.dry_run)
    for line in lines:
        print(f"• {line}")
    if not lines:
        print("Nothing to move: everything already uses Interlatch's names.")
    return 0


def cmd_export(args) -> int:
    from pathlib import Path

    from .export_md import export_markdown

    cfg = _cfg()
    if args.sessions:
        return _export_sessions(cfg, args)
    if args.out:
        cfg.notes_dir = Path(args.out).expanduser()
    conn = _conn(cfg)
    n = export_markdown(conn, cfg, full=args.full)
    print(f"{n} notes written to {cfg.notes_dir}")
    return 0


def _export_sessions(cfg, args) -> int:
    """`interlatch export ID...`: the sessions as one file (or a .zip of several) instead of the vault."""
    from pathlib import Path

    from .session_export import ExportError, export_sessions
    from .views import resolve_session_id

    conn = _conn(cfg)
    ids = []
    for ref in args.sessions:
        sid = resolve_session_id(conn, ref)
        if not sid:
            print(f"no unique session matches {ref!r}", file=sys.stderr)
            return 2
        ids.append(sid)
    try:
        name, _, data = export_sessions(conn, ids, args.format)
    except ExportError as exc:
        print(exc, file=sys.stderr)
        return 1
    out = Path(args.out or ".").expanduser()
    path = out / name if out.is_dir() else out
    path.write_bytes(data)
    print(f"wrote {path} ({len(data):,} bytes)")
    return 0


def cmd_mcp(args) -> int:
    if args.print_config:
        import json

        from .connectors import mcp_server_entry
        from .install import executable

        from .install import MCP_NAME

        print(json.dumps({"mcpServers": {MCP_NAME: mcp_server_entry(executable())}}, indent=2))
        return 0
    from .mcp_server import serve

    return serve(_cfg())


def cmd_config(args) -> int:
    import os
    import subprocess

    cfg = _cfg()
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

    from .config import LANGUAGES, load_config, set_config_value
    from .llm import BACKENDS

    if not key or value is None or "." not in key:
        print("usage: interlatch config set SECTION.KEY VALUE   (e.g. interlatch config set analysis.backend codex)",
              file=sys.stderr)
        return 2
    section, name = key.rsplit(".", 1) if key.startswith("providers.") else key.split(".", 1)
    if section.startswith("providers."):
        from .providers import PROVIDERS, SETTINGS

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
        from .llm import make_runner

        runner = make_runner(load_config(cfg.home))
        if not runner.available():
            print(f"note: {runner.unavailable_reason()}; analysis waits until it is set up")
    return 0


def _config_key(cfg, provider: str | None, value: str | None, *, forget: bool) -> int:
    """`interlatch config set-key PROVIDER [KEY]`: store a provider's API key (asked for, unechoed, when not given), or
    `forget-key PROVIDER` to remove it."""
    from .llm import BACKENDS
    from .providers import KEY_ENVS, keys_path, set_key

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


def cmd_forget(args) -> int:
    from .ingest import forget_session
    from .views import resolve_session_id

    cfg = _cfg()
    conn = _conn(cfg)
    sid = resolve_session_id(conn, args.session)
    if not sid:
        print(f"no unique session matches {args.session!r}", file=sys.stderr)
        return 2
    title = conn.execute("SELECT title FROM sessions WHERE id = ?", (sid,)).fetchone()[0]
    if not args.yes:
        answer = input(f"Forget {sid[:8]} ({title})? It will never be re-ingested. [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            return 1
    for item in forget_session(conn, cfg, sid, delete_transcript=args.delete_transcript):
        print(f"• removed {item}")
    return 0


def cmd_glossary(args) -> int:
    from .glossary import build_glossaries, glossary_entries, lookup
    from .synthesize import GLOBAL
    from .util import local_str, setup_logging

    cfg = _cfg()
    setup_logging(cfg.logs_dir)
    conn = _conn(cfg)
    console = _console()
    project = None
    if args.project:
        row = conn.execute("SELECT project_path FROM sessions WHERE project_path = ? OR project_name = ? LIMIT 1",
                           (args.project, args.project)).fetchone()
        project = row[0] if row else args.project
    if args.themes:
        from .glossary import build_themes, themes_due

        due = themes_due(conn, force=args.force)
        conn.close()
        if not due:
            print("themes are up to date (use --force to regroup anyway)")
            return 0
        print(f"grouping {len(due)} categor{'y' if len(due) == 1 else 'ies'} into themes with {_analyzer(cfg)}…")
        results = build_themes(cfg, due, concurrency=cfg.analysis.concurrency + 1, progress=lambda m: print(f"  {m}"))
        print(f"done: {sum(1 for v in results.values() if isinstance(v, int))}/{len(due)} categories grouped")
        return 0 if all(isinstance(v, int) for v in results.values()) else 1
    if args.rebuild:
        targets = [project] if project else ([r[0] for r in conn.execute(
            "SELECT DISTINCT project_path FROM knowledge WHERE status = 'active' AND project_path IS NOT NULL")] + [GLOBAL]
            if args.all else [])
        if not targets:
            print("use --rebuild with -p PROJECT or --all")
            return 2
        conn.close()
        print(f"building {len(targets)} glossar{'y' if len(targets) == 1 else 'ies'} with {_analyzer(cfg)}…")
        results = build_glossaries(cfg, targets, concurrency=cfg.analysis.concurrency + 1, progress=lambda m: print(f"  {m}"))
        ok = sum(1 for v in results.values() if isinstance(v, int))
        print(f"done: {ok}/{len(targets)} built, {sum(v for v in results.values() if isinstance(v, int))} terms")
        return 0
    if args.term:
        e = lookup(conn, args.term)
        if not e:
            print(f"{args.term!r} is not in the glossary")
            return 1
        console.print(f"[bold]{e['term']}[/] [dim]{e['category']}" + (f" · also: {', '.join(e['aliases'])}" if e["aliases"] else "") + "[/]",
                      highlight=False)
        console.print(e["definition"] or "", highlight=False, markup=False)
        for u in e["usage"]:
            if u["context"]:
                console.print(f"  [cyan]{u['project_name']}[/]: {u['context']}", highlight=False)
        if e["n_sessions"]:
            console.print(f"  [dim]in {e['n_sessions']} sessions, {local_str(e['first_seen'], '%Y-%m-%d')} → "
                          f"{local_str(e['last_seen'], '%Y-%m-%d')}[/]", highlight=False)
        return 0
    for e in glossary_entries(conn, project=project, category=args.category):
        console.print(f"[bold]{e['term']}[/] [dim]({e['category']})[/] {e['definition'] or ''}", highlight=False)
    return 0


ROLE_TITLES = {"way_in": "Ways in", "code": "Code", "data": "Data", "delivery": "Delivery", "runtime": "Runs on & uses"}


def cmd_systems(args) -> int:
    """The Systems map in the terminal: every system grouped by folder, or one system's parts with their evidence."""
    from rich.markup import escape

    from .systems import build, landscape, system

    cfg = _cfg()
    conn = _conn(cfg)
    data = build(conn, cfg)
    conn.close()
    console = _console()
    if args.name:
        key = args.name.rstrip("/")
        found = system(data, key) or next((system(data, s["id"]) for s in data["systems"]
                                           if s["label"].lower() == key.lower() or s["id"].rsplit("/", 1)[-1].lower() == key.lower()), None)
        if not found:
            print(f"no system named {args.name!r} (see `interlatch systems`)")
            return 1
        if args.json:
            print(json.dumps(found, indent=1, ensure_ascii=False, default=str))
            return 0
        git = found.get("git") or {}
        console.print(f"[bold]{escape(found['label'])}[/]  {escape(found['path'] or '')}  "
                      f"[dim]{found['sessions']} sessions{'  ' + escape(git['url']) if git else ''}[/]")
        names = {p["id"]: p["label"] for p in found["parts"]}
        for role, title in ROLE_TITLES.items():
            parts = sorted((p for p in found["parts"] if p["role"] == role), key=lambda p: -p["weight"])
            if not parts:
                continue
            console.print(f"\n[bold]{title}[/]")
            for p in parts:
                bits = [p["kind"], "/".join(p["stack"][:4]), " ".join(f":{x['port']}" for x in p["ports"][:3]),
                        " · ".join(x for x in (p.get("platform"), p.get("what")) if x)]
                console.print(f"  [cyan]{escape(p['label'])}[/]  [dim]{escape('  '.join(b for b in bits if b))}[/]")
                for ev in p["evidence"][: None if args.evidence else 2]:
                    console.print(f"    [dim]· {escape(ev['text'])}[/]")
                    for ex in ev.get("examples", [])[: 2 if args.evidence else 0]:
                        console.print(f"      [dim]$ {escape(ex['text'][:160])}[/]")
        if found["edges"]:
            console.print("\n[bold]Connections[/]")
            for e in found["edges"]:
                console.print(f"  {escape(names.get(e['from'], e['from']))} [dim]—{escape(e['label'])}→[/] {escape(names.get(e['to'], e['to']))}")
        if found["links"]:
            console.print("\n[bold]Other systems[/]")
            for ln in found["links"]:
                arrow = "→" if ln["direction"] == "out" else "←"
                console.print(f"  {arrow} {escape(ln['other'] or '')}  [dim]{escape(ln['evidence'][0]['text'][:140])}[/]")
        return 0
    if args.json:
        print(json.dumps(landscape(data), indent=1, ensure_ascii=False, default=str))
        return 0
    groups = {g["id"]: g for g in data["groups"]}
    by_id = {s["id"]: s for s in data["systems"]}

    def show(gid: str, depth: int) -> None:
        g = groups[gid]
        if gid:
            console.print(f"{'  ' * (depth - 1)}[bold]{escape(g['label'])}[/]")
        for k in sorted(g["systems"], key=lambda k: -by_id[k]["sessions"]):
            s = by_id[k]
            runs = sorted({p["label"] for p in s["parts"] if p["kind"] in ("deployed", "server")})[:3]
            console.print(f"{'  ' * depth}[cyan]{escape(s['label'])}[/]  [dim]{s['sessions']} sessions"
                          f"{'  ' + escape('/'.join(s['stack'][:3])) if s['stack'] else ''}"
                          f"{'  → ' + escape(', '.join(runs)) if runs else ''}[/]")
        for c in g["groups"]:
            show(c, depth + 1)

    show("", 0)
    if args.links:
        console.print("\n[bold]Links between systems[/]")
        for ln in data["links"]:
            console.print(f"  {escape(by_id[ln['from']]['label'])} → {escape(by_id[ln['to']]['label'])}  "
                          f"[dim]{ln['kind']}: {escape(ln['evidence'][0]['text'][:120])}[/]")
    console.print(f"\n[dim]{len(data['systems'])} systems, {len(data['links'])} links · `interlatch systems NAME` for one system's parts[/]")
    return 0


def cmd_review(args) -> int:
    from rich.markdown import Markdown

    from .reviews import generate_review, week_bounds
    from .util import setup_logging

    cfg = _cfg()
    setup_logging(cfg.logs_dir)
    conn = _conn(cfg)
    key = week_bounds(args.week)[0]
    row = conn.execute("SELECT markdown FROM reviews WHERE period = ?", (key,)).fetchone()
    if row and not args.regenerate:
        _console().print(Markdown(row[0]))
        return 0
    print(f"writing review for {key} with {_analyzer(cfg)}…")
    try:
        generate_review(conn, cfg, key)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    _console().print(Markdown(conn.execute("SELECT markdown FROM reviews WHERE period = ?", (key,)).fetchone()[0]))
    return 0


def _project_arg(conn, value: str | None) -> str | None:
    """-p accepts a project's path or its name."""
    if not value:
        return None
    row = conn.execute("SELECT project_path FROM sessions WHERE project_path = ? OR project_name = ? LIMIT 1",
                       (value, value)).fetchone()
    return row[0] if row else value


def escape_markup(text: str) -> str:
    from rich.markup import escape

    return escape(text or "")


def _home_short(path: str | None) -> str:
    from pathlib import Path

    if not path:
        return "-"
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def _warning_bits(w: dict) -> list[str]:
    bits = []
    if w.get("public_repo"):
        bits.append("public repo")
    if w.get("untracked"):
        bits.append("file not tracked by git")
    if w.get("overlap"):
        bits.append("already covered")
    bits += [f"sensitive: {h}" for h in w.get("sensitive") or []]
    return bits


def _print_diff(console, diff: str) -> None:
    from rich.text import Text

    for line in diff.splitlines():
        style = ("bold" if line.startswith(("+++", "---")) else "green" if line.startswith("+")
                 else "red" if line.startswith("-") else "cyan" if line.startswith("@@") else "")
        console.print(Text(line, style=style), highlight=False, soft_wrap=True)


def _show_suggestion(console, s: dict, pv: dict) -> None:
    console.print(f"[bold]#{s['id']}[/] [cyan]{s['kind']}[/] {escape_markup(s['title'])}", highlight=False, soft_wrap=True)
    if pv.get("command") is not None:
        console.print("  run it yourself, then `interlatch suggest done " + str(s["id"]) + "`:", highlight=False, soft_wrap=True)
        console.print(f"    {pv['command']}", highlight=False, markup=False, soft_wrap=True)
    elif not pv.get("ok"):
        console.print(f"  [red]cannot apply:[/] {escape_markup(str(pv.get('error')))}", highlight=False, soft_wrap=True)
    elif pv["diff"]:
        _print_diff(console, pv["diff"])
    else:
        console.print(f"  {_home_short(pv.get('path'))} already has it (nothing to change)", highlight=False, soft_wrap=True)
    for bit in _warning_bits(pv.get("warnings") or {}):
        console.print(f"  [yellow]! {escape_markup(bit)}[/]", highlight=False, soft_wrap=True)


def cmd_suggest(args) -> int:
    from . import suggest
    from .util import setup_logging

    cfg = _cfg()
    setup_logging(cfg.logs_dir)
    conn = _conn(cfg)
    console = _console()
    action, ids = args.action or "list", args.ids
    if action == "refresh":
        got = suggest.refresh(conn, cfg)
        print(f"suggestions: {got['new']} new, {got['updated']} updated, {got['stale']} stale")
        return 0
    if action == "list":
        if ids:
            print("to look at one suggestion: interlatch suggest show ID", file=sys.stderr)
            return 2
        rows = suggest.list_suggestions(conn, status=None if args.all else "new", project=_project_arg(conn, args.project))
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1, default=str))
            return 0
        for s in rows:
            status = "" if s["status"] == "new" else f" [dim]({s['status']})[/]"
            console.print(f"[green]#{s['id']}[/] [cyan]{s['kind']}[/] [bold]{escape_markup(s['title'])}[/]{status} "
                          f"[dim]→ {escape_markup(_home_short(s['target_path']) if s['target_path'] else 'run yourself')}[/]",
                          highlight=False, soft_wrap=True)
            line = (s["evidence"] or {}).get("line")
            if line:
                console.print(f"    [dim]{escape_markup(line)}[/]", highlight=False, soft_wrap=True)
            for bit in _warning_bits(s["warnings"] or {}):
                console.print(f"    [yellow]! {escape_markup(bit)}[/]", highlight=False, soft_wrap=True)
        if not rows:
            print("no suggestions" + ("" if args.all else " waiting (--all for applied and dismissed ones)"))
        else:
            print("\ninterlatch suggest show ID · apply ID · dismiss ID · done ID (setup steps) · move ID --to user|project")
        return 0
    if not ids:
        print(f"usage: interlatch suggest {action} ID", file=sys.stderr)
        return 2
    failed = 0
    for sid in ids:
        s = suggest.get(conn, sid)
        if s is None:
            print(f"no suggestion #{sid}", file=sys.stderr)
            failed += 1
            continue
        if action == "show":
            _show_suggestion(console, s, suggest.preview(conn, cfg, sid))
            continue
        if action == "dismiss":
            suggest.dismiss(conn, sid, args.reason)
            print(f"#{sid} dismissed; it will not be proposed again")
            continue
        if action == "done":
            got = suggest.mark_done(conn, sid)
            print(f"#{sid} marked done" if got["ok"] else f"#{sid}: {got['error']}", file=sys.stdout if got["ok"] else sys.stderr)
            failed += not got["ok"]
            continue
        if action == "move":
            if not args.to:
                print("usage: interlatch suggest move ID --to user|project", file=sys.stderr)
                return 2
            got = suggest.move(conn, cfg, sid, args.to)
            if not got["ok"]:
                print(f"#{sid}: {got['error']}", file=sys.stderr)
                failed += 1
            elif got["moved"]:
                print(f"#{sid} moved to " + ", ".join(_home_short(t) for t in got["targets"]) + " (see interlatch suggest)")
            else:
                print(f"#{sid} moved, but nothing is waiting there: it was dismissed or applied there before")
            continue
        if action == "undo":
            got = suggest.unapply(conn, cfg, sid)
            if got["ok"]:
                print(f"#{sid} taken back out" + (f" of {_home_short(got['path'])}" if got.get("path") else "")
                      + "; it is waiting again")
            else:
                print(f"#{sid}: {got['error']}", file=sys.stderr)
                failed += 1
            continue
        # apply
        pv = suggest.preview(conn, cfg, sid)
        _show_suggestion(console, s, pv)
        if s["kind"] == "environment" or not pv.get("ok"):
            failed += 1
            continue
        if not args.yes:
            try:
                answer = input(f"Apply #{sid} to {_home_short(pv['path'])}? [y/N] ")
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                print(f"#{sid} skipped")
                continue
        got = suggest.apply(conn, cfg, sid)
        if got["ok"]:
            print(f"#{sid} applied to {_home_short(got['path'])} (undo: interlatch suggest undo {sid})")
        else:
            print(f"#{sid}: {got['error']}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


SPARK = "▁▂▃▄▅▆▇█"


def _spark(weekly: list[dict]) -> str:
    vals = [w["sessions"] for w in weekly or []]
    top = max(vals, default=0)
    return "".join(" " if not v else SPARK[min(len(SPARK) - 1, (v * len(SPARK) - 1) // top)] for v in vals) if top else ""


def cmd_friction(args) -> int:
    from rich import box
    from rich.table import Table

    from .friction import report

    cfg = _cfg()
    conn = _conn(cfg)
    got = report(conn, days=args.days, project=_project_arg(conn, args.project), noise=args.noise)
    if args.json:
        print(json.dumps(got, ensure_ascii=False, indent=1, default=str))
        return 0
    console = _console()

    def causes_table(title: str, causes: list[dict], caption: str | None = None):
        t = Table(header_style="bold", title=title, title_justify="left", box=box.SIMPLE_HEAD, caption=caption,
                  caption_justify="left", pad_edge=False)
        for col, width in (("cause", None), ("sessions", 8), ("projects", 8), ("last seen", 10), ("now", 3), ("trend", 12)):
            t.add_column(col, justify="right" if col in ("sessions", "projects") else "left", no_wrap=True,
                         overflow="ellipsis", min_width=width, max_width=max(22, console.width - 58) if width is None else None)
        for c in causes:
            name = c["id"] if c["category"] != "other" else f"other: {c['name']}"
            t.add_row(escape_markup(name), str(c["sessions"]), str(len(c["projects"])), c["last_seen"] or "-",
                      "[red]yes[/]" if c["still_happening"] else "[dim]no[/]", _spark(c["weekly"]))
        console.print(t)

    wasteful = [c for c in got["causes"] if not c["noise"]]
    if wasteful:
        causes_table("What goes wrong", wasteful, "now: still happening (last 14 days) · trend: sessions a week, last 12 weeks")
    else:
        print("no recurring failures found")
    noise = [c for c in got["causes"] if c["noise"]]
    if noise:
        causes_table("Noise (expected failures, not wasted work)", noise)
    if got["tool_errors"]:
        e = Table(header_style="bold", title="Tool error rates", title_justify="left", box=box.SIMPLE_HEAD)
        for col in ("tool", "calls", "errors", "rate"):
            e.add_column(col, justify="left" if col == "tool" else "right")
        for r in got["tool_errors"]:
            e.add_row(escape_markup(r["tool"]), str(r["calls"]), str(r["errors"]), f"{r['rate'] * 100:.1f}%")
        console.print(e)
    ns = got["noise_summary"]
    if not args.noise and ns["occurrences"]:
        print(f"filtered as noise: {ns['occurrences']} failure{'s' if ns['occurrences'] != 1 else ''} in {ns['sessions']} "
              f"session{'s' if ns['sessions'] != 1 else ''} (expected test failures, "
              "read-only checks, provider hiccups); --noise shows them")
    print("fixes for these: interlatch suggest")
    return 0


def cmd_sources(args) -> int:
    from .connectors import all_status, mcp_clients_status
    from .util import local_str

    cfg = _cfg()
    conn = _conn(cfg)
    console = _console()
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

    from .chat_import import ExportError, import_export, summary

    cfg = _cfg()
    conn = _conn(cfg)
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
    from .screen import SOURCES, screen_chats, screen_status
    from .util import human_cost

    report = screen_chats(cfg, conn, source=source, redo=redo, limit=limit, sample=sample,
                          progress=lambda m: print(m, file=sys.stderr))
    print(report.summary() + (f" · screening cost {human_cost(report.cost_usd)}" if report.calls else ""))
    worth = sum(screen_status(conn, s)["to_queue"] for s in SOURCES)
    if worth:
        print(f"next: `interlatch screen --list analyze` shows them, `interlatch screen --queue` queues the {worth:,} worth "
              "analyzing (add --maybe for the maybes too)")
    return 1 if report.left and not report.screened else 0


def cmd_screen(args) -> int:
    from .screen import BATCH, candidates, queue, rule_verdict

    cfg = _cfg()
    conn = _conn(cfg)
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

    from .screen import VERDICT_LABEL, source_filter

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
    console = _console()
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
    from .connectors import MCP_CLIENTS, connect, disconnect
    from .install import executable

    cfg = _cfg()
    if args.disconnect:
        actions = disconnect(cfg, args.name)
    else:
        actions = connect(cfg, args.name, args.exe or executable())
    for a in actions:
        print(f"• {a}")
    if not args.disconnect and not args.no_sync and args.name not in MCP_CLIENTS:
        from .config import load_config
        from .ingest import sync

        cfg = load_config(cfg.home)
        print("syncing…")
        report = sync(cfg, _conn(cfg))
        print(f"sync: {report.summary()}")
    return 0


def _push(cfg, *, quiet: bool = False) -> int:
    from .hub import HubError, push

    live = not quiet and sys.stdout.isatty()

    def progress(message: str) -> None:
        if live:
            print(f"\r\033[K  {message[:110]}", end="", flush=True)

    try:
        report = push(cfg, progress=progress)
    except HubError as exc:
        if live:
            print("\r\033[K", end="")
        print(f"push: {exc}", file=sys.stderr)
        return 1
    if live:
        print("\r\033[K", end="")
    if not quiet:
        print(f"push: {report.summary()}")
    for err in report.errors[:10]:
        print(f"  ! {err}", file=sys.stderr)
    return 0 if not report.errors else 1


def cmd_container(args) -> int:
    from .container import main

    return main()


def cmd_push(args) -> int:
    from .util import setup_logging

    cfg = _cfg()
    setup_logging(cfg.logs_dir)
    if not cfg.is_spoke:
        print("This computer has not joined a hub. On the hub run `interlatch hub enable`, then run the command it "
              "prints here.", file=sys.stderr)
        return 1
    return _push(cfg, quiet=args.quiet)


def _hub_url_guess(cfg) -> str | None:
    """The address other computers reach this one's dashboard at: its Tailscale name, when Serve is set up."""
    for host in cfg.server_allowed_hosts:
        if host.endswith(".ts.net"):
            return f"https://{host}"
    return None


def cmd_hub(args) -> int:
    import platform

    from . import hub
    from .install import background_status, hooks_installed

    cfg = _cfg()
    console = _console()
    action = args.action

    if action == "enable":
        if cfg.is_spoke:
            console.print(f"This computer sends its sessions to the hub at {cfg.hub_url}. Run `interlatch hub leave` "
                          "first to make it a hub itself.", highlight=False)
            return 1
        token = hub.read_token(cfg)
        if not token or args.rotate:
            token = hub.new_token(cfg)
            if args.rotate:
                console.print("New token: computers that joined with the old one must join again.")
        conn = _conn(cfg)
        hub.register_local(conn, cfg)
        conn.commit()
        conn.close()
        address = (args.url_opt or args.url or "").rstrip("/")
        if address and address != cfg.hub_address:  # what invites and sign-in links point at from now on
            _set_config_value(cfg, "hub", "address", json.dumps(address))
        url = address or cfg.hub_address or (_hub_url_guess(cfg) or "").rstrip("/")
        console.print(f"[bold]{hub.local_machine(cfg)['name']}[/] is a hub: other computers can send it their sessions.",
                      highlight=False)
        if not url:
            console.print("Other computers need an address to reach it. Run `interlatch tailnet on` (Tailscale), then "
                          "`interlatch hub enable` again, or pass the address: `interlatch hub enable --url "
                          f"http://<address>:{cfg.server_port}` (only on a network you trust: without Tailscale the "
                          "files travel unencrypted).", highlight=False)
            return 0
        from urllib.parse import urlparse

        given = urlparse(url if "://" in url else f"http://{url}")
        name = (given.hostname or "").lower()
        if name and name not in ("127.0.0.1", "localhost", "::1") and name not in cfg.server_allowed_hosts:
            _set_config_value(cfg, "server", "allowed_hosts", json.dumps([*cfg.server_allowed_hosts, name]))
            if cfg.server_host in ("127.0.0.1", "localhost"):
                console.print(f"[yellow]The dashboard listens on {cfg.server_host} only[/]: for other computers to reach "
                              f"{url} without Tailscale, set `interlatch config set server.host 0.0.0.0` and restart it. "
                              "Its dashboard then opens on other devices only for people signed in: add yourself with "
                              "`interlatch hub invite <your name> --email <email> --role admin`.", highlight=False)
        console.print("On each other computer, install Interlatch and run:\n")
        console.print(f"  [bold]{hub.join_command(url, token)}[/]\n", highlight=False, soft_wrap=True)
        console.print("[dim]The token lets a computer send sessions here; keep it private. "
                      "`interlatch hub enable --rotate` replaces it. To give each person a token of their own and a "
                      "dashboard sign-in instead: `interlatch hub invite <name> --email <email>`.[/]", highlight=False)
        if not _wait_for_port(cfg.server_port, 1.0):
            console.print(f"[yellow]The dashboard is not running on port {cfg.server_port}[/]: it is what receives "
                          "the files. `interlatch install` keeps it running (or run `interlatch ui`).", highlight=False)
        if platform.system() == "Linux":
            console.print("[dim]A hub that should run while nobody is logged in: `loginctl enable-linger $USER`.[/]",
                          highlight=False)
        return 0

    if action == "join":
        if not args.url or bool(args.token) == bool(args.code):
            console.print("Usage: interlatch hub join <hub address> --code <invite code> (from the hub's admin), or "
                          "--token <token> (the hub's `interlatch hub enable` prints the whole command).")
            return 2
        if hub.read_token(cfg) and not cfg.is_spoke:
            console.print("This computer is a hub itself (`interlatch hub disable` first).")
            return 1
        url = args.url.rstrip("/")
        if "://" not in url:
            url = f"https://{url}"
        token = args.token
        if args.code:
            try:
                got = hub.redeem_invite(cfg, url, args.code)
            except hub.HubError as exc:
                console.print(f"Can't join: {exc}", highlight=False)
                return 1
            token, who = got["token"], got.get("person") or {}
            console.print(f"The hub {got.get('hub') or url} knows you as [bold]{who.get('name')}[/] ({who.get('role')}).",
                          highlight=False)
            limited = got.get("projects") is not None
            if got.get("share") == "knowledge":  # summaries and project lessons, never transcripts
                if args.share == "everything":
                    why = (f"{who.get('name')} sees only some projects on this hub" if limited
                           else "This hub takes knowledge only")
                    console.print(f"{why}, so this computer can share knowledge only; joining with --share knowledge "
                                  "instead.", highlight=False)
                args.share = "knowledge"
            if limited:
                names = ", ".join(p.get("name") or p["path"] for p in got.get("projects") or []) or "none yet"
                console.print(f"Projects you share with it: [bold]{names}[/]. Add your folder for each one: "
                              "`interlatch hub add-folder <folder> --project <name>`; sessions elsewhere stay here.",
                              highlight=False)
        hub.write_token(cfg, token)
        _set_config_value(cfg, "hub", "url", json.dumps(url))
        if args.share:
            _set_config_value(cfg, "hub", "share", json.dumps(args.share))
        _set_config_value(cfg, "hub", "all_folders", "true" if args.all_folders else "false")
        from .config import load_config

        cfg = load_config(cfg.home)
        if cfg.shares_knowledge:
            console.print(f"Joined the hub at {url}, sharing knowledge only. This computer keeps recording and "
                          "analyzing its own sessions; the hub gets each analyzed session's summary and project "
                          "lessons. Transcripts and lessons about you stay here.", highlight=False)
            if cfg.hub_all_folders:
                console.print("It shares sessions from every folder on this computer (--all-folders).", highlight=False)
            elif not args.code or got.get("projects") is None:  # a limited person heard which projects above
                console.print("Only sessions in the hub's projects are shared: add your folder for each one with "
                              "`interlatch hub add-folder <folder> --project <name>` (`interlatch hub folders --list` "
                              "lists them). Sessions in other folders stay here.", highlight=False)
        else:
            console.print(f"Joined the hub at {url}. This computer now sends its Claude Code and Codex sessions there; "
                          "the hub records and analyzes them.", highlight=False)
        if args.no_push:
            return 0
        console.print("Sharing what this computer has analyzed so far…" if cfg.shares_knowledge
                      else "Sending the sessions on this computer (the first time can take a while)…")
        code = _push(cfg)
        if code == 0 and cfg.shares_knowledge:
            console.print("New sessions are shared after each analysis. `interlatch hub leave` stops sharing.")
            return code
        if code == 0:
            bg = background_status()
            hooked = bool(hooks_installed(cfg).get("SessionEnd"))
            if bg.get("loaded") or hooked:
                console.print("New sessions go to the hub as each one ends" + (" and every 15 minutes." if bg.get("loaded") else "."))
            else:
                console.print("To send new sessions automatically (as each ends, and every 15 minutes), run "
                              "`interlatch install`. Until then: `interlatch push`.")
            console.print("[dim]This computer's own dashboard and MCP tools keep what they had and are no longer "
                          "updated; open the hub's dashboard instead. `interlatch hub leave` undoes this.[/]",
                          highlight=False)
        return code

    if action == "leave" and args.project or action == "rejoin":
        return _hub_leave_project(cfg, console, args)

    if action == "leave":
        if not cfg.is_spoke:
            console.print("This computer has not joined a hub.")
            return 0
        hub.leave_hub(cfg)
        console.print(f"Left the hub at {cfg.hub_url}. This computer records and analyzes its own sessions again; "
                      "the hub keeps what it was sent.", highlight=False)
        return 0

    if action == "disable":
        if cfg.is_spoke or not hub.read_token(cfg):
            console.print("This computer is not a hub.")
            return 0
        hub.token_path(cfg).unlink(missing_ok=True)
        console.print("Other computers can no longer send sessions here. What they sent is kept.")
        return 0

    if action in ("add-folder", "remove-folder", "folders"):
        return _hub_folders(cfg, console, args)

    if action == "store":
        return _hub_store(cfg, console)

    if action == "purge":
        return _hub_purge(cfg, console, args)

    if action in ("people", "invite", "role", "access", "remove", "shared-token"):
        return _hub_people(cfg, console, args)

    if action == "project":
        return _hub_project(cfg, console, args)

    if action == "signin":
        if not cfg.is_spoke:
            console.print("This computer has not joined a hub; its own dashboard is `interlatch ui`.")
            return 1
        try:
            link = hub.dashboard_signin(cfg)
        except hub.HubError as exc:
            console.print(f"Can't get a sign-in link: {exc}", highlight=False)
            return 1
        console.print("Open this within a few minutes to see the hub's dashboard as you (it works once):\n")
        console.print(f"  {link}\n", highlight=False, soft_wrap=True)
        return 0

    # status
    if cfg.is_spoke:
        last = hub.last_push(cfg)
        console.print(f"Sends its sessions to the hub at [bold]{cfg.hub_url}[/]"
                      + (" · knowledge only (transcripts stay here)" if cfg.shares_knowledge else "")
                      + ((" · from every folder" if cfg.hub_all_folders else " · from the hub's projects only")
                         if cfg.shares_knowledge else ""), highlight=False)
        console.print(f"  last push: {last['at'] + ' · ' + last['summary'] if last else 'never'}", highlight=False)
        team = hub.last_team(cfg)
        if team:
            console.print(f"  team lessons here: {team.get('lessons', 0)} from the hub's team store (as of {team.get('at')})",
                          highlight=False)
        if cfg.hub_folders:
            n = len(cfg.hub_folders)
            console.print(f"  {n} folder{'s' * (n != 1)} added to projects on the hub (`interlatch hub folders`)", highlight=False)
        return 0
    conn = _conn(cfg)
    rows = hub.machines(conn, cfg)
    conn.close()
    role = "a hub" if hub.read_token(cfg) else "not a hub (`interlatch hub enable` makes it one)"
    console.print(f"[bold]{hub.local_machine(cfg)['name']}[/] is {role}.", highlight=False)
    if hub.read_token(cfg) and cfg.hub_accept == "knowledge":
        console.print("  takes knowledge only: computers that send transcripts are turned away "
                      "(`interlatch config set hub.accept everything` takes them again)", highlight=False)
    from .util import local_str

    for m in rows:
        seen = "this computer" if m["this"] else f"last sent {local_str(m['last_push']) if m['last_push'] else 'nothing yet'}"
        if m.get("share") == "knowledge":
            seen += " · knowledge only"
        elif not m["this"] and cfg.hub_accept == "knowledge":
            seen += " · sends transcripts: turned away"
        console.print(f"  {m['name'] or m['id'][:8]:<28} {m['platform'] or '':<8} {m['sessions']:>5} session{'s' * (m['sessions'] != 1)} · {seen}",
                      highlight=False)
        for f in m.get("folders") or []:
            console.print(f"      {f['folder']} → {f['name']}", highlight=False)
    return 0


def _hub_store(cfg, console) -> int:
    """`interlatch hub store`: connect to the hub's team store, run its upgrade steps, and show what it holds."""
    from . import hub, team_store

    if cfg.is_spoke:
        console.print(f"The team store belongs to the hub; this computer sends to the hub at {cfg.hub_url}.",
                      highlight=False)
        return 1
    path = team_store.env_path(cfg)
    if cfg.hub_store != "postgres":
        console.print("This hub keeps what computers share in its own SQLite only. To keep the team's record in "
                      f"Postgres as well: put PGHOST, PGDATABASE, PGUSER and PGPASSWORD in {path} (chmod 600), "
                      "install the driver (uv tool install 'interlatch[team]'), run "
                      "`interlatch config set hub.store postgres`, and restart the dashboard.", highlight=False)
        return 0
    try:
        store = team_store.get(cfg)
        st = store.status()
    except team_store.TeamStoreError as exc:
        console.print(f"[red]{exc}[/]", highlight=False)
        return 1
    c = st["counts"]
    console.print(f"Team store: [bold]{st['where']}[/] (PostgreSQL {st['server']}, schema {st['schema']})", highlight=False)
    console.print(f"  {c['computers']} computers · {c['sessions']} sessions · {c['lessons']} lessons "
                  f"({c['lesson_sources']} statements of them) · {c['audit']} audit entries", highlight=False)
    console.print(f"  upgrade steps: {', '.join(st['steps']) or 'none'} · last activity: {st['last_activity'] or 'none'}",
                  highlight=False)
    if not hub.read_token(cfg):
        console.print("[yellow]This computer is not a hub yet[/]: `interlatch hub enable`.", highlight=False)
    return 0


ROLE_WORDS = {"admin": "an admin", "member": "a member", "readonly": "read-only"}


def _projects_arg(conn, args) -> tuple[list[str] | None, str | None]:
    """--project … (hub project names or paths) or --all-projects as people.add takes it: (projects, problem)."""
    from . import hub

    if args.all_projects and args.project:
        return None, "Pass either --project or --all-projects, not both."
    if args.all_projects:
        return None, None
    known = hub.hub_projects(conn)
    out = []
    for name in args.project or []:
        project, problem = _pick_project(known, name)
        if problem:
            return None, problem + "\n`interlatch hub project add <folder>` sets up a project before anything was sent to it."
        out.append(project["path"])
    return out, None


def _projects_words(conn, projects: list[str] | None) -> str:
    from .ingest import project_name_for

    if projects is None:
        return "every project"
    return ", ".join(project_name_for(x) for x in projects) or "no project yet"


def _find_computer(conn, key: str) -> tuple[str | None, str | None]:
    """(id, problem): a computer this hub knows, by its id or its name (Team › Computers shows both)."""
    key = key.strip()
    rows = conn.execute("SELECT id, name FROM machines WHERE COALESCE(role, '') != 'this' AND (id = ? OR lower(name) = lower(?))",
                        (key, key)).fetchall()
    if len(rows) == 1:
        return rows[0]["id"], None
    if rows:
        return None, f"Several computers are called {key}: give its id instead ({', '.join(r['id'] for r in rows)})."
    return None, f"This hub doesn't know a computer {key}. Team › Computers lists them with their ids."


def _find_person(conn, key: str | None) -> dict | None:
    """A person on this hub by email, or by the id `interlatch hub people` shows."""
    from . import people

    key = (key or "").strip()
    if key.isdigit():
        p = people.get(conn, int(key))
        return p if p and not p["removed_at"] else None
    return people.by_email(conn, key) if key else None


def _hub_purge(cfg, console, args) -> int:
    """`interlatch hub purge <email|id|computer> --project <name>… | --outside-access`: remove for good what a person's
    computers (or one computer) sent to this hub, in some projects or outside the ones they see."""
    from . import hub, people
    from .ingest import project_name_for

    if cfg.is_spoke:
        console.print(f"Purge runs on the hub; this computer sends to the hub at {cfg.hub_url}.", highlight=False)
        return 1
    if not args.url or bool(args.project) == bool(args.outside_access) or args.all_projects:
        console.print("Usage: interlatch hub purge <email|id|computer> --project <name> … | --outside-access [--yes]")
        return 2
    conn = _conn(cfg)
    try:
        person = _find_person(conn, args.url)
        if person:
            machines, who = hub.computers_of(conn, person["id"]), f"{person['name']}'s computers"
        else:
            key = args.url.strip().lower()
            machines = [r[0] for r in conn.execute(
                "SELECT id FROM machines WHERE id != ? AND (lower(name) = ? OR (length(?) >= 8 AND id LIKE ? || '%'))",
                (hub.local_machine(cfg)["id"], key, key, key))]
            who = f"the computer {args.url}"
            if len(machines) > 1:
                console.print(f"{len(machines)} computers are called {args.url}; pass the one you mean by its id "
                              "(`interlatch hub status`).", highlight=False)
                return 1
        if not machines:
            console.print(f"No one on this hub has the email or id {args.url}, and no computer that sent to it is called "
                          "that. `interlatch hub people` and `interlatch hub status` list them.", highlight=False)
            return 1
        if args.outside_access:
            if not person:
                console.print("--outside-access needs a person (their email or id), not a computer.")
                return 2
            keep = people.projects_of(person)
            if keep is None:
                console.print(f"{person['name']} sees every project, so nothing is outside what they see. Limit them "
                              f"first: `interlatch hub access {args.url} --project <name>`.", highlight=False)
                return 1
            targets = hub.purge_targets(conn, machines, keep=keep)
            where = f"outside {_projects_words(conn, keep)}"
        else:
            projects, problem = _projects_arg(conn, args)
            if problem:
                console.print(problem, highlight=False)
                return 1
            targets = hub.purge_targets(conn, machines, projects=projects)
            where = f"in {_projects_words(conn, projects)}"
        if not targets:
            console.print(f"Nothing from {who} {where} on this hub.", highlight=False)
            return 0
        counts: dict[str, int] = {}
        for x in targets:
            label = x["project_name"] or (project_name_for(x["project_path"]) if x["project_path"] else "no project")
            counts[label] = counts.get(label, 0) + 1
        console.print(f"{len(targets)} session{'s' * (len(targets) != 1)} from {who} {where}:", highlight=False)
        for label, n in sorted(counts.items()):
            console.print(f"  {label}: {n}", highlight=False)
        if not args.yes:
            try:
                answer = input("Remove them from this hub for good, with their lessons and the knowledge bases built "
                               "from them? [y/N] ")
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                console.print("Nothing removed.")
                return 1
        try:
            got = hub.purge(cfg, conn, targets, person_id=person["id"] if person else None)
        except hub.HubError as exc:
            console.print(f"Can't purge: {exc}", highlight=False)
            return 1
        console.print(f"Removed {got['sessions']} session{'s' * (got['sessions'] != 1)}"
                      + (" here and in the team store" if got["store"] else "") + ". The computer can't send them "
                      "again.", highlight=False)
        if got["projects"]:
            console.print("Knowledge bases built from them are gone; the next sync builds them again from what is "
                          "left: " + ", ".join(project_name_for(p) for p in got["projects"]), highlight=False)
        console.print("[dim]Weekly reviews already written are not changed. Teammates' computers that already "
                      "fetched lessons from these sessions keep their copy until they next fetch.[/]", highlight=False)
        return 0
    finally:
        conn.close()


def _hub_people(cfg, console, args) -> int:
    """`interlatch hub people | invite | role | remove | shared-token`: who may send to this hub and open its dashboard.

    It runs at the hub itself, so it acts as an admin (people.py: whoever is at the hub computer always is one).
    """
    from . import hub, people
    from .util import local_str

    if cfg.is_spoke:
        console.print(f"People belong to the hub; this computer sends to the hub at {cfg.hub_url}.", highlight=False)
        return 1
    action = args.action
    conn = _conn(cfg)
    try:
        if action == "people":
            rows = people.listing(conn)
            if not rows:
                console.print("No people on this hub yet: computers send with its shared token. To give each person a "
                              "token of their own and a dashboard sign-in: `interlatch hub invite <name> --email <email> "
                              "--role admin|member|readonly`.")
                return 0
            n = len(rows)
            console.print(f"{n} {'person' if n == 1 else 'people'} on this hub · shared token "
                          f"{'on' if cfg.hub_shared_token else 'off'}", highlight=False)
            for p in rows:
                bits = [p["email"] or "no email", p["role"], "sees " + _projects_words(conn, p["projects"])]
                if p["invites"]:
                    bits.append(f"invite open until {local_str(p['invites'][-1]['expires_at'])}")
                console.print(f"  {p['id']:>3}  [bold]{p['name']}[/] · " + " · ".join(bits), highlight=False)
                for c in p["computers"]:
                    used = local_str(c["last_used"]) if c["last_used"] else "never"
                    console.print(f"       computer {c['name'] or (c['machine_id'] or '')[:8]} · last used {used}",
                                  highlight=False)
                if p["browsers"]:
                    n = len(p["browsers"])
                    console.print(f"       {n} browser{'s' * (n != 1)} signed in to the dashboard", highlight=False)
            console.print("[dim]`interlatch hub role <email|id> <role>` changes a role, `interlatch hub access <email|id> "
                          "--project <name>` the projects they see, `interlatch hub remove <email|id>` removes someone, "
                          "`interlatch hub invite <name>` makes a new code.[/]", highlight=False)
            return 0

        if action == "invite":
            name = (args.url or "").strip()
            if not name:
                console.print("Usage: interlatch hub invite <name> [--email <email>] [--role admin|member|readonly] "
                              "--project <name> … | --all-projects")
                return 2
            existing = _find_person(conn, args.email) if args.email else None
            if existing is None and (name.isdigit() or "@" in name):  # `hub invite bob@example.com`: a new code for Bob
                existing = _find_person(conn, name)
            role = args.role or (existing["role"] if existing else "member")
            projects, problem = _projects_arg(conn, args)
            if problem:
                console.print(problem, highlight=False)
                return 1
            chose = bool(args.project or args.all_projects)
            machine = None
            if args.computer:  # an invite for one computer the hub knows: it joins as this person without its key
                machine, problem = _find_computer(conn, args.computer)
                if problem:
                    console.print(problem, highlight=False)
                    return 1
            if not existing and role != "admin" and not chose:  # nothing until granted: the inviter says what they see
                console.print(f"Which projects should {name} see? Add --project <name> (repeat it for more), or "
                              "--all-projects. `interlatch hub project list` shows the hub's projects.", highlight=False)
                return 2
            try:
                if existing and args.role and args.role != existing["role"]:
                    existing = people.set_role(conn, existing["id"], args.role)
                if existing and chose:
                    existing = people.set_projects(conn, existing["id"], projects)
                person = existing or people.add(conn, name, args.email, role, projects=projects)
                code = people.invite(conn, person["id"], machine=machine)
            except people.PeopleError as exc:
                console.print(f"Can't invite {name}: {exc}", highlight=False)
                return 1
            expires = conn.execute("SELECT MAX(expires_at) FROM people_codes WHERE person_id = ? AND kind = 'invite'",
                                   (person["id"],)).fetchone()[0]
            address = cfg.hub_address or (_hub_url_guess(cfg) or "")
            where = address or "<hub address>"
            email = f" ({person['email']})" if person["email"] else ""
            sees = "" if person["role"] == "admin" else f", seeing {_projects_words(conn, people.projects_of(person))}"
            console.print(f"{'New invite for' if existing else 'Added'} [bold]{person['name']}[/]{email}, "
                          f"{ROLE_WORDS[person['role']]}{sees}. The invite code works once, until {local_str(expires)}:\n",
                          highlight=False)
            console.print(f"  [bold]{code}[/]\n", highlight=False)
            if machine:
                known = conn.execute("SELECT name FROM machines WHERE id = ?", (machine,)).fetchone()
                console.print(f"It joins only the computer [bold]{known['name'] or machine}[/] ({machine}), as "
                              f"{person['name']}'s.\n", highlight=False)
            if person["role"] != "readonly":
                console.print("On their computer (it joins as them and sends what it learned, not transcripts):")
                console.print(f"  {hub.invite_command(where, code)}\n", highlight=False, soft_wrap=True)
            console.print("Or in their browser, to see the hub's dashboard:")
            console.print(f"  {hub.invite_link(where, code)}\n", highlight=False, soft_wrap=True)
            if not address:
                console.print("[yellow]This hub has no address yet[/]: `interlatch hub enable --url https://<address>` "
                              "sets the one other computers and browsers reach it at.", highlight=False)
            console.print("[dim]Pass them on by chat; the code is not shown again. It joins one computer or opens one "
                          "browser; `interlatch hub invite` again makes another.[/]", highlight=False)
            if not hub.read_token(cfg):
                console.print("[yellow]This computer is not a hub yet[/]: `interlatch hub enable`.", highlight=False)
            return 0

        if action == "access":
            if not args.url or not (args.project or args.all_projects):
                console.print("Usage: interlatch hub access <email|id> --project <name> … | --all-projects")
                return 2
            person = _find_person(conn, args.url)
            if not person:
                console.print(f"No one on this hub has the email or id {args.url}. `interlatch hub people` lists them.",
                              highlight=False)
                return 1
            projects, problem = _projects_arg(conn, args)
            if problem:
                console.print(problem, highlight=False)
                return 1
            try:
                person = people.set_projects(conn, person["id"], projects)
            except people.PeopleError as exc:
                console.print(f"Can't change what {person['name']} sees: {exc}", highlight=False)
                return 1
            if person["role"] == "admin":
                console.print(f"{person['name']} is an admin and sees every project whatever this says; it applies "
                              "if their role changes.", highlight=False)
                return 0
            console.print(f"{person['name']} now sees {_projects_words(conn, people.projects_of(person))}.", highlight=False)
            if projects is not None and cfg.hub_shared_token:
                console.print("[yellow]The hub's shared token is on[/]: a computer sending with it is nobody in "
                              "particular and is not limited. `interlatch hub shared-token off` once everyone joined "
                              "with an invite.", highlight=False)
            return 0

        if action in ("role", "remove"):
            role = args.value or args.role
            if not args.url or (action == "role" and not role):
                console.print("Usage: interlatch hub role <email|id> admin|member|readonly" if action == "role"
                              else "Usage: interlatch hub remove <email|id>")
                return 2
            person = _find_person(conn, args.url)
            if not person:
                console.print(f"No one on this hub has the email or id {args.url}. `interlatch hub people` lists them.",
                              highlight=False)
                return 1
            try:
                if action == "role":
                    people.set_role(conn, person["id"], role)
                else:
                    people.remove(conn, person["id"])
            except people.PeopleError as exc:
                what = "change the role of" if action == "role" else "remove"
                console.print(f"Can't {what} {person['name']}: {exc}", highlight=False)
                return 1
            if action == "role":
                console.print(f"{person['name']} is now {ROLE_WORDS[role]}.", highlight=False)
            else:
                console.print(f"Removed {person['name']}: their computers and browsers can no longer reach this hub. "
                              "What their computers sent stays.", highlight=False)
            return 0

        # shared-token
        value = (args.url or "").lower()
        if value not in ("on", "off"):
            console.print("Usage: interlatch hub shared-token on|off")
            return 2
        on = value == "on"
        _set_config_value(cfg, "hub", "shared_token", json.dumps(on))
        people.audit(conn, people.LOCAL, "shared-token", on=on)
        conn.commit()
        if on:
            console.print("Computers may send with the hub's shared token again, as well as with their own.")
            return 0
        if not people.has_people(conn):
            console.print("The shared token is refused once this hub has people (`interlatch hub invite`); until then "
                          "computers keep sending with it.")
            return 0
        console.print("Only computers that joined with an invite may send now; the shared token is refused.")
        legacy = [r[0] or r[1][:8] for r in conn.execute(
            "SELECT name, id FROM machines WHERE role = 'spoke' AND person_id IS NULL ORDER BY name")]
        if legacy:
            console.print(f"[yellow]{len(legacy)} computer{'s' * (len(legacy) != 1)} joined with the shared token[/] and "
                          f"need an invite to keep sending: {', '.join(legacy)}", highlight=False)
        return 0
    finally:
        conn.close()


def _hub_project(cfg, console, args) -> int:
    """`interlatch hub project add | remove | list`: projects set up on this hub ahead of time, each a folder here."""
    from . import hub

    if cfg.is_spoke:
        console.print(f"Projects belong to the hub; this computer sends to the hub at {cfg.hub_url}. To file a folder "
                      "here under one of its projects: `interlatch hub add-folder <folder> --project <name>`.",
                      highlight=False)
        return 1
    verb = (args.url or "list").lower()
    if verb not in ("add", "remove", "list"):
        console.print("Usage: interlatch hub project add <folder> | remove <folder> | list")
        return 2
    conn = _conn(cfg)
    try:
        if verb == "list":
            set_up = set(hub.declared_projects(conn))
            rows = hub.hub_projects(conn)
            if not rows:
                console.print("No projects on this hub yet. `interlatch hub project add <folder>` sets one up.")
                return 0
            for p in rows:
                mark = " · set up here" if p["path"] in set_up else ""
                console.print(f"  {p['name']:<32} {p['sessions']:>5}  {p['path']}{mark}", highlight=False)
            return 0
        if not args.value:
            console.print(f"Usage: interlatch hub project {verb} <folder>")
            return 2
        try:
            got = hub.add_project(cfg, conn, args.value) if verb == "add" else hub.remove_project(cfg, conn, args.value)
        except hub.HubError as exc:
            console.print(f"Can't {verb} that project: {exc}", highlight=False)
            return 1
        if verb == "remove":
            console.print(f"{got['path']} is no longer a project set up here; {got['moved']} session"
                          f"{'s' * (got['moved'] != 1)} of this computer went back to their own folders.", highlight=False)
            return 0
        if got.get("existed"):
            console.print(f"[bold]{got['name']}[/] ({got['path']}) is already set up.", highlight=False)
            return 0
        n = got["sessions"]
        console.print(f"Set up [bold]{got['name']}[/] ({got['path']}): this folder and everything below it is one "
                      f"project, with {n} session{'s' * (n != 1)} so far.", highlight=False)
        console.print("Give people access: `interlatch hub invite <name> --email <email> --project "
                      f"{got['name']}`. On their computers: `interlatch hub add-folder <their folder> --project "
                      f"{got['name']}`.", highlight=False)
        if not hub.read_token(cfg):
            console.print("[yellow]This computer is not a hub yet[/]: `interlatch hub enable`.", highlight=False)
        return 0
    finally:
        conn.close()


def _pick_project(projects: list[dict], wanted: str) -> tuple[dict | None, str | None]:
    """The hub project `wanted` names (its path, its name, or its folder's name), or why there isn't exactly one."""
    from pathlib import Path

    w = wanted.strip().rstrip("/") or wanted.strip()
    hits = [p for p in projects if p["path"] == w]
    if not hits:
        low = w.lower()
        hits = [p for p in projects if low in ((p.get("name") or "").lower(), Path(p["path"]).name.lower())]
    if len(hits) == 1:
        return hits[0], None
    if hits:
        return None, (f"{len(hits)} projects on the hub are called {wanted}; pass the one you mean by its path:\n"
                      + "\n".join(f"  {p['path']}" for p in hits))
    near = [p for p in projects if w.lower() in p["path"].lower()][:8]
    return None, (f"The hub has no project called {wanted}."
                  + ("\nDid you mean:\n" + "\n".join(f"  {p['name']}  ({p['path']})" for p in near) if near
                     else " `interlatch hub folders --list` shows the hub's projects."))


def _hub_leave_project(cfg, console, args) -> int:
    """`interlatch hub leave --project <name>` and `interlatch hub rejoin --project <name>`: this computer stops (or
    starts again) sharing to one of the hub's projects and getting its teammates' lessons."""
    from pathlib import Path

    from . import hub

    if not cfg.is_spoke:
        console.print("This computer has not joined a hub.")
        return 1
    if not args.project or len(args.project) > 1:
        console.print(f"Which project on the hub? One `--project <name>`: interlatch hub {args.action} --project <name>")
        return 2
    wanted = args.project[-1]
    try:  # the hub's own list if it can be reached, else the one from the last push; and the projects left
        known = hub.handshake(cfg).get("projects") or []
    except hub.HubError:
        known = (hub.last_folders(cfg) or {}).get("projects") or []
    known += [{"path": x, "name": Path(x).name} for x in cfg.hub_left if x not in {k["path"] for k in known}]
    project, problem = _pick_project(known, wanted)
    if problem:
        console.print(problem, highlight=False)
        return 1
    name = project.get("name") or Path(project["path"]).name
    if args.action == "rejoin":
        if project["path"] not in cfg.hub_left:
            console.print(f"This computer hasn't left {name}.", highlight=False)
            return 0
        cfg = hub.rejoin_project(cfg, project["path"])
        console.print(f"Rejoined [bold]{name}[/]: its sessions are shared again, and its teammates' lessons come back.",
                      highlight=False)
    else:
        try:
            cfg = hub.leave_project(cfg, project["path"])
        except hub.FolderError as exc:
            console.print(str(exc), highlight=False)
            return 1
        console.print(f"Left [bold]{name}[/]: this computer no longer shares its sessions there or gets its teammates' "
                      "lessons. What it already shared stays on the hub. `interlatch hub rejoin --project "
                      f"{wanted}` undoes this.", highlight=False)
    if args.no_push:
        console.print("The hub hears of it at the next push (`interlatch push`, or the background sync).")
        return 0
    return _push(cfg)


def _hub_folders(cfg, console, args) -> int:
    """`interlatch hub add-folder | remove-folder | folders`: folders here whose sessions belong to a project on the hub."""
    from pathlib import Path

    from . import hub

    if not cfg.is_spoke:
        console.print("This computer doesn't send its sessions to a hub. Folders are added on a computer that does "
                      "(after `interlatch hub join`); on the hub, `interlatch hub status` lists what each one added.")
        return 1
    action = args.action

    if action == "folders":
        if args.list:
            try:
                hello = hub.handshake(cfg)
            except hub.HubError as exc:
                console.print(f"Can't reach the hub: {exc}", highlight=False)
                return 1
            for p in hello.get("projects") or []:
                console.print(f"  {p['name']:<32} {p['sessions']:>5}  {p['path']}", highlight=False)
            return 0
        if cfg.shares_knowledge:
            console.print("Shares sessions from every folder (`[hub] all_folders`)." if cfg.hub_all_folders else
                          "Shares only sessions in these folders and in repositories whose git remote the hub knows; "
                          "the rest stay here.", highlight=False)
        if not cfg.hub_folders:
            console.print("No folders added. Sessions go to the hub's project with the same git remote, or keep their "
                          "own folder. To file a folder's sessions under a project on the hub:\n\n"
                          "  interlatch hub add-folder <folder> --project <name>\n")
            return 0
        report = (hub.last_folders(cfg) or {}).get("folders") or {}
        for folder, project in sorted(cfg.hub_folders.items()):
            seen = report.get(folder) or {}
            n = seen.get("sessions")
            count = f" · {n} session{'s' * (n != 1)} from here" if isinstance(n, int) else " · not sent yet"
            console.print(f"  {folder} → [bold]{Path(project).name or project}[/]{count}", highlight=False)
            for o in seen.get("overridden") or []:
                console.print(f"      [yellow]{o['repo']}[/] goes to {Path(o['project']).name} instead: the hub knows "
                              "its git remote", highlight=False)
        return 0

    if not args.url:
        console.print(f"Usage: interlatch hub {action} <folder>" + (" --project <name>" if action == "add-folder" else ""))
        return 2
    folder = str(Path(args.url).expanduser().resolve())

    if action == "remove-folder":
        key = folder if folder in cfg.hub_folders else args.url.rstrip("/") if args.url.rstrip("/") in cfg.hub_folders else None
        if key is None:
            console.print(f"{folder} was not added. `interlatch hub folders` lists the folders that were.", highlight=False)
            return 1
        try:
            cfg, taken = hub.remove_folder(cfg, key)
        except hub.HubError as exc:
            console.print(f"Nothing changed: {exc}", highlight=False)
            return 1
        if taken is None:
            console.print(f"Sessions in {key} are filed by their git remote or their own folder again.", highlight=False)
        else:
            console.print(f"Sessions in {key} no longer go to the hub. It took back the {taken} session"
                          f"{'s' * (taken != 1)} this computer shared from there, with {'their' if taken != 1 else 'its'} "
                          "lessons.", highlight=False)
    else:
        if not args.project or len(args.project) > 1:
            console.print("Which project on the hub? One `--project <name>` (`interlatch hub folders --list` shows them).")
            return 2
        if not Path(folder).is_dir():
            console.print(f"{folder} is not a folder.", highlight=False)
            return 1
        try:
            hello = hub.handshake(cfg)
        except hub.HubError as exc:
            console.print(f"Can't reach the hub: {exc}", highlight=False)
            return 1
        project, problem = _pick_project(hello.get("projects") or [], args.project[-1])
        if problem:
            console.print(problem, highlight=False)
            return 1
        name = project.get("name") or Path(project["path"]).name
        try:
            cfg, remote = hub.add_folder(cfg, folder, project, hello)
        except hub.FolderError as exc:
            console.print(str(exc), highlight=False)
            return 1
        if remote:
            console.print(f"Sessions in {folder} already go to {name}: the hub knows its git remote ({remote}). "
                          "Nothing to add.", highlight=False)
            return 0
        info = hub.git_info(folder)
        console.print(f"Sessions in {folder} and the folders below it now go to [bold]{name}[/] on the hub "
                      f"({project['path']}).", highlight=False)
        if info:
            console.print(f"[dim]Its git remote ({info[1]}) is not one the hub knows, so the folder decides.[/]",
                          highlight=False)

    if args.no_push:
        console.print("The hub files them at the next push (`interlatch push`, or the background sync).")
        return 0
    code = _push(cfg)
    moved = (hub.last_folders(cfg) or {}).get("moved") or 0
    if moved:
        console.print(f"{moved} session{'s' * (moved != 1)} already on the hub moved, with {'their' if moved != 1 else 'its'} "
                      "knowledge.", highlight=False)
    for o in (((hub.last_folders(cfg) or {}).get("folders") or {}).get(folder) or {}).get("overridden") or []:
        console.print(f"[yellow]{o['repo']}[/] inside it goes to {Path(o['project']).name} instead: the hub knows its "
                      "git remote.", highlight=False)
    return code


def cmd_mirror(args) -> int:
    """`interlatch mirror`: the copy of this archive in Postgres ([mirror] to = "postgres"): what it holds, or write it now."""
    from . import mirror
    from .util import setup_logging

    cfg = _cfg()
    console = _console()
    path = mirror.env_path(cfg)
    if not mirror.enabled(cfg):
        console.print("The mirror is off. To keep a copy of this archive in Postgres (on this computer, in Docker or in "
                      f"the cloud): put PGHOST, PGDATABASE, PGUSER and PGPASSWORD in {path} (chmod 600), install the "
                      f"driver ({mirror.INSTALL}), then `interlatch config set mirror.to postgres` and "
                      "`interlatch mirror sync`. Or open Settings › Storage in the dashboard.", highlight=False)
        return 0
    if args.action == "sync":
        setup_logging(cfg.logs_dir)
        res = mirror.sync(cfg, full=args.full, progress=lambda m: print(f"  {m}"))
        if res.get("error"):
            console.print(f"[red]{res['error']}[/]", highlight=False)
            return 1
        console.print(f"mirror: {res.summary()} in {res['seconds']} s", highlight=False)
        return 0
    try:
        st = mirror.status(cfg)
    except mirror.MirrorError as exc:
        console.print(f"[red]{exc}[/]", highlight=False)
        return 1
    console.print(f"Mirror: [bold]{st['where']}[/] (PostgreSQL {st['server']}, schema {st['schema']}, "
                  f"{cfg.mirror_include})", highlight=False)
    c = st["counts"]
    if c:
        console.print("  " + " · ".join(f"{n:,} {t}" for t, n in c.items()), highlight=False)
    last = mirror.last(cfg)
    if last:
        console.print(f"  last sync: {last['at']} · {mirror.Result(last).summary()}", highlight=False)
    else:
        console.print("  not written yet: `interlatch mirror sync`, or wait for the next background run", highlight=False)
    return 0


def cmd_tailnet(args) -> int:
    import platform

    from . import tailnet

    cfg = _cfg()
    console = _console()
    cli = tailnet.find_cli()
    if not cli:
        console.print("Tailscale is not installed. Install it on this computer and your phone "
                      "(https://tailscale.com/download), sign both in to the same account, then run this again.")
        return 1
    try:
        st = tailnet.status(cli)
    except (tailnet.TailnetError, OSError) as exc:
        console.print(f"Could not ask Tailscale for its status: {exc}", highlight=False)
        return 1
    hosts = [h for h in cfg.server_allowed_hosts if h != st.dns_name]

    if args.action == "status":
        serving = tailnet.serves_port(tailnet.serve_status(cli), cfg.server_port)
        console.print(f"Tailscale: {st.state or 'unknown'}" + (f" as {st.login} · this computer is {st.dns_name}" if st.running else ""),
                      highlight=False)
        console.print(f"  dashboard on the tailnet: {st.url + '/' if serving else 'off (`interlatch tailnet on`)'}", highlight=False)
        console.print(f"  allowed_hosts: {cfg.server_allowed_hosts or '[]'} · allowed_users: {cfg.server_allowed_users or 'anyone on the tailnet'}",
                      highlight=False)
        return 0

    if not st.running or not st.dns_name:
        console.print(f"Tailscale is not connected ({st.state or 'unknown'}). Sign in with `tailscale up` or the "
                      "Tailscale app, then run this again.", highlight=False)
        return 1

    if args.action == "off":
        proc = tailnet.serve(cli, cfg.server_port, off=True, interactive=False)
        _set_config_value(cfg, "server", "allowed_hosts", json.dumps(hosts))
        _set_config_value(cfg, "server", "allowed_users", "[]")
        console.print("The dashboard is no longer on your tailnet." if proc.returncode == 0 else
                      f"Tailscale said: {(proc.stderr or proc.stdout).strip()[:300]}", highlight=False)
        return 0

    # on
    _set_config_value(cfg, "server", "allowed_hosts", json.dumps([*hosts, st.dns_name]))
    _set_config_value(cfg, "server", "allowed_users", json.dumps([] if args.anyone or not st.login else [st.login]))
    console.print(f"Serving the dashboard at [bold]{st.url}/[/] with Tailscale Serve…", highlight=False)
    proc = tailnet.serve(cli, cfg.server_port, interactive=sys.stdin.isatty())
    if proc.returncode != 0:
        hint = (" On Linux, let your user configure Tailscale once: `sudo tailscale set --operator=$USER`."
                if platform.system() == "Linux" else "")
        console.print(f"[red]Tailscale Serve did not start[/]{': ' + (proc.stderr or '').strip()[:300] if proc.stderr else ''}.{hint}",
                      highlight=False)
        return 1
    console.print(f"\nOpen [bold]{st.url}/[/] on your phone (with the Tailscale app on) or any computer in your tailnet.",
                  highlight=False)
    console.print("On an iPhone: Share › Add to Home Screen, for an app icon. On Android: ⋮ › Add to Home screen.")
    who = "you" if not args.anyone and st.login else "anyone in your tailnet"
    console.print(f"[dim]Only {who} ({st.login or 'tailnet'}) can open it; nothing is reachable from the internet. "
                  "`interlatch tailnet off` stops it.[/]", highlight=False)
    if not _wait_for_port(cfg.server_port, 1.0):
        console.print(f"[yellow]The dashboard is not running on port {cfg.server_port}.[/] `interlatch install` keeps it "
                      "running in the background (or run `interlatch ui`).", highlight=False)
    return 0


def cmd_context(args) -> int:
    import os

    from .hooks import build_session_context

    cfg = _cfg()
    print(build_session_context(cfg, args.cwd or os.getcwd()) or "(no knowledge recorded for this directory yet)")
    return 0


# ---------------------------------------------------------------------------- parser
def _analyze_choice(value: str) -> str:
    value = value.strip().lower()
    if value in ("all", "later") or (value.isdigit() and int(value) > 0):
        return value
    raise argparse.ArgumentTypeError("use all, later or a number of sessions")


def prog_name() -> str:
    """The command as it was run: `interlatch`, or `chronicle` (the same command under its name before the rename)."""
    from pathlib import Path

    name = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""
    return name if name in ("interlatch", "chronicle") else "interlatch"


def build_parser() -> argparse.ArgumentParser:
    from .llm import BACKENDS

    p = argparse.ArgumentParser(
        prog=prog_name(),
        description="Record, archive and analyze every coding-agent session (Claude Code, Codex, GitHub Copilot, IBM Bob, "
                    "Google Antigravity); extract reusable knowledge.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

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

    s = sub.add_parser("uninstall", help="remove hooks, the background agent and the MCP server")
    s.add_argument("--purge", action="store_true", help="also delete all recorded data")
    s.set_defaults(fn=cmd_uninstall)

    s = sub.add_parser("status", aliases=["doctor"], help="show installation and queue status")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("sync", help="archive and ingest all transcripts")
    s.add_argument("--work", action="store_true", help="then analyze/synthesize/export (the background job does this)")
    s.add_argument("--force", action="store_true", help="re-parse every session")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("work", help="process the analysis queue, synthesize knowledge bases, export notes")
    s.add_argument("--limit", type=int, help="max sessions to analyze this run")
    s.add_argument("--model")
    s.add_argument("--force", action="store_true", help="synthesize even below the threshold")
    s.add_argument("--no-analyze", action="store_true")
    s.add_argument("--no-synthesize", action="store_true")
    s.add_argument("--no-export", action="store_true")
    s.set_defaults(fn=cmd_work)

    s = sub.add_parser("analyze", help="analyze specific sessions now (ids or prefixes)")
    s.add_argument("sessions", nargs="*")
    s.add_argument("--pending", action="store_true", help="all sessions waiting for analysis")
    s.add_argument("--all", action="store_true", help="every eligible session (with --force: re-analyze done ones)")
    s.add_argument("--limit", type=int)
    s.add_argument("--model")
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-synthesize", action="store_true")
    s.add_argument("--backend", choices=list(BACKENDS), help="analyze with this agent or provider for this run only")
    s.add_argument("--dry-run", action="store_true", help="show digest sizes instead of calling the model")
    s.add_argument("--concurrency", type=int, help="parallel analysis processes for this run")
    s.set_defaults(fn=cmd_analyze)

    s = sub.add_parser("synthesize", help="rebuild project knowledge bases")
    s.add_argument("--project", help="project path or name")
    s.add_argument("--global", dest="global_", action="store_true", help="the cross-project playbook")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_synthesize)

    s = sub.add_parser("sessions", aliases=["ls"], help="list sessions")
    s.add_argument("-p", "--project")
    s.add_argument("--since", help="e.g. 7d, 12h, 2w or a date")
    s.add_argument("--status", help="analysis status filter")
    s.add_argument("-n", "--limit", type=int, default=30)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_sessions)

    s = sub.add_parser("show", help="show a session overview")
    s.add_argument("session")
    s.add_argument("--transcript", action="store_true", help="print the conversation")
    s.add_argument("--markdown", action="store_true", help="raw Markdown")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("search", help="full-text search across transcripts and knowledge")
    s.add_argument("query", nargs="+")
    s.add_argument("-p", "--project")
    s.add_argument("-n", "--limit", type=int, default=10)
    s.add_argument("--knowledge-only", action="store_true")
    s.add_argument("--sessions-only", action="store_true")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("knowledge", aliases=["kb"], help="browse extracted knowledge")
    s.add_argument("query", nargs="?")
    s.add_argument("-k", "--kind")
    s.add_argument("-p", "--project")
    s.add_argument("-n", "--limit", type=int, default=30)
    s.add_argument("--brief", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_knowledge)

    s = sub.add_parser("projects", help="per-project summary")
    s.set_defaults(fn=cmd_projects)

    s = sub.add_parser("stats", help="usage statistics")
    s.add_argument("--since", help="e.g. 30d")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("ui", aliases=["serve"], help="open the local dashboard")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--open", action="store_true", help="open a browser tab")
    s.add_argument("--menu-bar", action=argparse.BooleanOptionalAction, default=None,
                   help="macOS: show Interlatch's menu-bar icon (default: only as the login item, with server.menu_bar on)")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("app", help="open the desktop app (macOS; needs the `app` extra)")
    s.set_defaults(fn=cmd_app)

    s = sub.add_parser("export", help="write the Markdown (Obsidian) vault, or export sessions as files")
    s.add_argument("sessions", nargs="*", help="session ids or prefixes: export these (one file, or a .zip of several)")
    s.add_argument("--format", choices=["md", "json", "raw"], default="md",
                   help="with sessions: md (overview + conversation), json (every event) or raw (the original transcript)")
    s.add_argument("--out", help="the vault folder; with sessions, a file or folder to write the export to")
    s.add_argument("--full", action="store_true", help="rewrite every note")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("glossary", help="the vocabulary of your projects: terms, aliases, definitions")
    s.add_argument("term", nargs="?")
    s.add_argument("-p", "--project")
    s.add_argument("-c", "--category")
    s.add_argument("--rebuild", action="store_true", help="rebuild with the analysis model (-p PROJECT or --all)")
    s.add_argument("--all", action="store_true")
    s.add_argument("--themes", action="store_true", help="group big categories into themes with the analysis model (the Map's Theme level)")
    s.add_argument("--force", action="store_true", help="with --themes: regroup categories that have not changed")
    s.set_defaults(fn=cmd_glossary)

    s = sub.add_parser("systems", help="the Systems map: every project as a system with its parts, from manifests and sessions")
    s.add_argument("name", nargs="?", help="one system (its name or folder): its parts, connections and evidence")
    s.add_argument("--links", action="store_true", help="also list the links between systems")
    s.add_argument("--evidence", action="store_true", help="with NAME: every piece of evidence, with example commands")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_systems)

    s = sub.add_parser("review", help="weekly engineering review written by the analysis model (default: last completed week)")
    s.add_argument("week", nargs="?", help="ISO week like 2026-W39, 'current' or 'last'")
    s.add_argument("--regenerate", action="store_true")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("suggest", aliases=["suggestions"],
                       help="proposed fixes (instruction lines, config changes, setup steps); nothing is written until you apply one")
    s.add_argument("action", nargs="?", choices=["list", "show", "apply", "dismiss", "done", "undo", "move", "refresh"],
                   help="list (default), show ID (the diff), apply ID..., dismiss ID, done ID (a setup step you ran), "
                        "undo ID (take an applied one back out), move ID --to user|project, refresh")
    s.add_argument("ids", nargs="*", type=int, metavar="ID")
    s.add_argument("-p", "--project", help="only this project's suggestions (path or name)")
    s.add_argument("--all", action="store_true", help="also applied, dismissed, stale and done ones")
    s.add_argument("--json", action="store_true")
    s.add_argument("-y", "--yes", action="store_true", help="with apply: don't ask before writing")
    s.add_argument("--reason", help="with dismiss: why (kept for you)")
    s.add_argument("--to", choices=["user", "project"],
                   help="with move: the user-level file (every project) or the files of the projects it came from")
    s.set_defaults(fn=cmd_suggest)

    s = sub.add_parser("friction", help="what goes wrong: recurring failure causes across your sessions, and tool error rates")
    s.add_argument("-p", "--project", help="only this project (path or name)")
    s.add_argument("--days", type=int, help="only the last N days")
    s.add_argument("--json", action="store_true")
    s.add_argument("--noise", action="store_true", help="also show failures that are expected (test runs, read-only checks, provider hiccups)")
    s.set_defaults(fn=cmd_friction)

    s = sub.add_parser("forget", help="remove a session from the vault for good (e.g. it contained secrets)")
    s.add_argument("session")
    s.add_argument("--delete-transcript", action="store_true", help="also delete Claude Code's original transcript")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(fn=cmd_forget)

    s = sub.add_parser("sources", aliases=["connectors"], help="which coding agents are connected and how")
    s.set_defaults(fn=cmd_sources)

    agents = ["claude", "codex", "codex-cloud", "copilot", "bob", "antigravity"]
    clients = ["claude-desktop", "cursor", "windsurf", "gemini"]
    s = sub.add_parser("connect", help="start recording an agent (claude, codex, codex-cloud, copilot, bob, antigravity), or give an MCP client "
                                       "(claude-desktop, cursor, windsurf, gemini) Interlatch's MCP server")
    s.add_argument("name", choices=agents + clients)
    s.add_argument("--exe", help="command the agent should run for Interlatch's MCP server")
    s.add_argument("--no-sync", action="store_true")
    s.set_defaults(fn=cmd_connect, disconnect=False)

    s = sub.add_parser("disconnect", help="stop recording an agent, or remove the MCP server from a client (recorded sessions are kept)")
    s.add_argument("name", choices=agents + clients)
    s.set_defaults(fn=cmd_connect, disconnect=True, exe=None, no_sync=True)

    s = sub.add_parser("import", help="import chats from a claude.ai or ChatGPT data export (the .zip, its folder, or conversations.json)")
    s.add_argument("path")
    s.add_argument("--analyze", action="store_true", help="queue the imported chats for analysis (uses your Claude plan)")
    s.add_argument("--screen", action="store_true", help="then screen them: which are worth analyzing (`interlatch screen`)")
    s.set_defaults(fn=cmd_import)

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

    s = sub.add_parser("context", help="print the knowledge digest a new session in this directory would get")
    s.add_argument("--cwd")
    s.set_defaults(fn=cmd_context)

    s = sub.add_parser("config", help="show or edit config.toml")
    s.add_argument("action", nargs="?", choices=["show", "path", "edit", "set", "set-key", "forget-key"], default="show")
    s.add_argument("key", nargs="?", help="with set: SECTION.KEY, e.g. analysis.backend or providers.ollama.model; "
                                          "with set-key / forget-key: the provider, e.g. openai, or bob for IBM Bob")
    s.add_argument("value", nargs="?", help="with set: the value (TOML, or a bare word); with set-key: the API key "
                                            "(asked for when left out, so it stays out of your shell history)")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("migrate", help="move Chronicle's data folder (~/.claude-chronicle) to ~/.interlatch and point "
                                       "hooks, MCP servers and background agents at the new names (runs by itself "
                                       "when the dashboard or the background sync starts)")
    s.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    s.set_defaults(fn=cmd_migrate)

    s = sub.add_parser("mcp", help="run the MCP server (stdio); registered by `install` and `connect`")
    s.add_argument("--print-config", action="store_true", help="print a JSON entry to add Interlatch to any MCP client by hand")
    s.set_defaults(fn=cmd_mcp)

    s = sub.add_parser("mirror", help="a copy of this archive in a Postgres database you choose: what it holds, or write it now")
    s.add_argument("action", nargs="?", choices=["status", "sync"], default="status")
    s.add_argument("--full", action="store_true", help="with sync: write every row again, whatever the mirror holds")
    s.set_defaults(fn=cmd_mirror)

    s = sub.add_parser("tailnet", help="reach the dashboard from your phone and other computers through Tailscale")
    s.add_argument("action", nargs="?", choices=["on", "off", "status"], default="status")
    s.add_argument("--anyone", action="store_true", help="with on: let anyone in your tailnet in, not just your login")
    s.set_defaults(fn=cmd_tailnet)

    s = sub.add_parser("hub", help="one archive for several computers: this one records them all (enable), "
                                   "or sends its sessions to one that does (join)")
    s.add_argument("action", nargs="?", choices=["status", "enable", "join", "leave", "disable", "folders", "add-folder",
                                                 "remove-folder", "store", "people", "invite", "role", "access",
                                                 "remove", "shared-token", "signin", "project", "purge", "rejoin"],
                   default="status")
    s.add_argument("url", nargs="?", metavar="address|folder|name|email",
                   help="with join: the hub's address; with add-folder and remove-folder: a folder on this computer; "
                        "with invite: the person's name; with role, access and remove: their email or id; with "
                        "shared-token: on or off; with project: add, remove or list")
    s.add_argument("value", nargs="?", metavar="role|folder",
                   help="with role: admin, member or readonly; with project add and remove: a folder on this computer")
    s.add_argument("--token", help="with join: the token the hub's `interlatch hub enable` printed")
    s.add_argument("--code", help="with join: the invite code the hub's admin gave you (instead of --token)")
    s.add_argument("--url", dest="url_opt",
                   help="with enable: the address other computers and browsers reach this hub at (kept as [hub] address)")
    s.add_argument("--email", help="with invite: the person's email (how a company sign-in finds them)")
    s.add_argument("--role", choices=["admin", "member", "readonly"], help="with invite: their role (member by default)")
    s.add_argument("--rotate", action="store_true", help="with enable: make a new token (computers must join again)")
    s.add_argument("--project", action="append",
                   help="with add-folder: the project on the hub its sessions belong to (name or path); with leave and "
                        "rejoin: the project this computer leaves or rejoins; with invite and access: a project the "
                        "person sees (repeat it for more)")
    s.add_argument("--computer", metavar="id|name",
                   help="with invite: a code for that one computer the hub already knows (Team › Computers), when it "
                        "can't show the key it sent the hub before")
    s.add_argument("--all-projects", action="store_true",
                   help="with invite and access: the person sees every project on the hub")
    s.add_argument("--share", choices=["everything", "knowledge"],
                   help="with join: send transcripts for the hub to analyze (everything, the default), or analyze here "
                        "and send only summaries and project lessons (knowledge)")
    s.add_argument("--all-folders", action="store_true",
                   help="with join: share knowledge from every folder on this computer, not only the folders added to "
                        "the hub's projects and repositories it knows")
    s.add_argument("--outside-access", action="store_true",
                   help="with purge: remove what the person's computers sent outside the projects they see now")
    s.add_argument("--yes", action="store_true", help="with purge: don't ask before removing")
    s.add_argument("--list", action="store_true", help="with folders: list the hub's projects")
    s.add_argument("--no-push", action="store_true",
                   help="with join, add-folder, remove-folder, leave --project, rejoin: don't send anything to the hub yet")
    s.set_defaults(fn=cmd_hub)

    s = sub.add_parser("push", help="send this computer's new sessions to its hub now (the background job does this)")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_push)

    s = sub.add_parser("container", help="run a hub in a container (docker/): set it up from INTERLATCH_* (or CHRONICLE_*) variables, "
                                         "then serve the dashboard and sync every 15 minutes")
    s.set_defaults(fn=cmd_container)

    s = sub.add_parser("hook", help=argparse.SUPPRESS)
    s.add_argument("event", choices=["session-end", "session-start", "stop", "pre-compact"])
    s.set_defaults(fn=cmd_hook)

    s = sub.add_parser("ingest-session", help=argparse.SUPPRESS)
    s.add_argument("transcript", nargs="?")
    s.add_argument("--ended", action="store_true")
    s.add_argument("--work", action="store_true")
    s.set_defaults(fn=cmd_ingest_session)
    return p


def _rerun_in_utf8_mode(argv: list[str]) -> int:
    """Windows: Claude Code and the other agents write UTF-8, while Python there reads and writes text in the ANSI code
    page (cp1252, cp932) unless in UTF-8 mode, which is set as Python starts. So this command runs again in it, as do
    the Pythons it starts (PYTHONUTF8). Windows has no exec: this process waits, its stdin and stdout passed on."""
    import os
    import subprocess

    proc = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "chronicle", *argv], env={**os.environ, "PYTHONUTF8": "1"})
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:  # Ctrl+C reaches both: the command decides how it ends
            continue


def main(argv: list[str] | None = None) -> int:
    if argv is None and sys.platform == "win32" and not sys.flags.utf8_mode:
        return _rerun_in_utf8_mode(sys.argv[1:])
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["hook"] and len(argv) >= 2:  # fast path: no argparse/rich import cost
        from .hooks import hook_main

        return hook_main(argv[1])
    if argv[:1] == ["statusline"]:  # Claude Code runs this after every turn: same fast path
        from .statusline import main as statusline_main

        return statusline_main()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        from .config import chronicle_home

        parser.print_help()
        if not (chronicle_home() / "config.toml").exists():
            print("\nNew here? Run `interlatch install` to pick which coding agents to record and start the dashboard.")
        return 0
    try:
        return args.fn(args) or 0
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
