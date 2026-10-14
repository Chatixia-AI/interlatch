"""Browsing the archive: sessions, one session, search, projects, statistics; forgetting a session, and exporting."""

from __future__ import annotations

import json
import sys

from ..common import load_cfg, new_console, open_archive


def _since(value: str | None) -> str | None:
    """'7d', '12h', '2w' or an ISO date -> ISO UTC timestamp."""
    import re
    from datetime import timedelta

    from ....core.util import parse_ts, to_iso, utcnow

    if not value:
        return None
    m = re.fullmatch(r"(\d+)([hdw])", value.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
        return to_iso(utcnow() - delta)
    dt = parse_ts(value)
    return to_iso(dt) if dt else None


def cmd_sessions(args) -> int:
    from rich.table import Table

    from ....core.util import human_cost, human_count, human_duration, local_str

    cfg = load_cfg()
    conn = open_archive(cfg)
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
    new_console().print(t)
    return 0


def cmd_show(args) -> int:
    from rich.markdown import Markdown

    from ....application.records import resolve_session_id, session_record
    from ...exports.markdown import session_markdown

    cfg = load_cfg()
    conn = open_archive(cfg)
    sid = resolve_session_id(conn, args.session)
    if not sid:
        print(f"no unique session matches {args.session!r}", file=sys.stderr)
        return 2
    s = session_record(conn, sid)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
        return 0
    if args.transcript:
        from ....core.util import local_str

        for r in conn.execute("SELECT ts, kind, tool_name, is_error, text FROM events WHERE session_id = ? AND agent_id = '' "
                              "AND kind IN ('prompt','text','tool_use','command','interrupt','compact') ORDER BY seq", (sid,)):
            label = {"prompt": "[bold cyan]USER[/]", "text": "[bold]CLAUDE[/]", "tool_use": "[dim]  →[/]",
                     "command": "[magenta]CMD[/]", "interrupt": "[red]INTERRUPT[/]", "compact": "[yellow]COMPACT[/]"}[r["kind"]]
            text = r["text"] if r["kind"] in ("prompt", "text") else r["text"][:200]
            new_console().print(f"{label} [dim]{local_str(r['ts'], '%H:%M')}[/] ", end="")
            new_console().print(text, markup=False, highlight=False)
        return 0
    md = session_markdown(s)
    if args.markdown:
        print(md)
    else:
        new_console().print(Markdown(md))
    return 0


def cmd_search(args) -> int:
    from rich.markup import escape

    from ....application.search import search_knowledge, search_sessions
    from ....core.util import local_str

    cfg = load_cfg()
    conn = open_archive(cfg)
    console = new_console()
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


def cmd_projects(args) -> int:
    from rich.table import Table

    from ....core.util import human_cost, human_duration, local_str
    from ....projects.labels import project_labels

    cfg = load_cfg()
    conn = open_archive(cfg)
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
    new_console().print(t)
    return 0


def cmd_stats(args) -> int:
    from rich.table import Table

    from ....core.util import human_cost, human_count, human_duration

    cfg = load_cfg()
    conn = open_archive(cfg)
    since = _since(args.since) or "0000"
    t = conn.execute(
        "SELECT COUNT(*) n, SUM(n_prompts) p, SUM(n_tool_calls) tc, SUM(n_tool_errors) te, SUM(active_s) a, "
        "SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens) tok, SUM(est_cost_usd) c, SUM(lines_added) la, "
        "SUM(lines_removed) lr, SUM(n_subagents) sa, COUNT(DISTINCT project_path) pr FROM sessions WHERE started_at >= ?",
        (since,)).fetchone()
    console = new_console()
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


def cmd_export(args) -> int:
    from pathlib import Path

    from ...exports.vault import export_markdown

    cfg = load_cfg()
    if args.sessions:
        return _export_sessions(cfg, args)
    if args.out:
        cfg.notes_dir = Path(args.out).expanduser()
    conn = open_archive(cfg)
    n = export_markdown(conn, cfg, full=args.full)
    print(f"{n} notes written to {cfg.notes_dir}")
    return 0


def _export_sessions(cfg, args) -> int:
    """`interlatch export ID...`: the sessions as one file (or a .zip of several) instead of the vault."""
    from pathlib import Path

    from ....application.records import resolve_session_id
    from ...exports.sessions import ExportError, export_sessions

    conn = open_archive(cfg)
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


def cmd_forget(args) -> int:
    from ....application.records import resolve_session_id
    from ....ingestion.deletion import forget_session

    cfg = load_cfg()
    conn = open_archive(cfg)
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


def add_sessions(sub) -> None:
    s = sub.add_parser("sessions", aliases=["ls"], help="list sessions")
    s.add_argument("-p", "--project")
    s.add_argument("--since", help="e.g. 7d, 12h, 2w or a date")
    s.add_argument("--status", help="analysis status filter")
    s.add_argument("-n", "--limit", type=int, default=30)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_sessions)


def add_show(sub) -> None:
    s = sub.add_parser("show", help="show a session overview")
    s.add_argument("session")
    s.add_argument("--transcript", action="store_true", help="print the conversation")
    s.add_argument("--markdown", action="store_true", help="raw Markdown")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)


def add_search(sub) -> None:
    s = sub.add_parser("search", help="full-text search across transcripts and knowledge")
    s.add_argument("query", nargs="+")
    s.add_argument("-p", "--project")
    s.add_argument("-n", "--limit", type=int, default=10)
    s.add_argument("--knowledge-only", action="store_true")
    s.add_argument("--sessions-only", action="store_true")
    s.set_defaults(fn=cmd_search)


def add_projects(sub) -> None:
    s = sub.add_parser("projects", help="per-project summary")
    s.set_defaults(fn=cmd_projects)


def add_stats(sub) -> None:
    s = sub.add_parser("stats", help="usage statistics")
    s.add_argument("--since", help="e.g. 30d")
    s.set_defaults(fn=cmd_stats)


def add_export(sub) -> None:
    s = sub.add_parser("export", help="write the Markdown (Obsidian) vault, or export sessions as files")
    s.add_argument("sessions", nargs="*", help="session ids or prefixes: export these (one file, or a .zip of several)")
    s.add_argument("--format", choices=["md", "json", "raw"], default="md",
                   help="with sessions: md (overview + conversation), json (every event) or raw (the original transcript)")
    s.add_argument("--out", help="the vault folder; with sessions, a file or folder to write the export to")
    s.add_argument("--full", action="store_true", help="rewrite every note")
    s.set_defaults(fn=cmd_export)


def add_forget(sub) -> None:
    s = sub.add_parser("forget", help="remove a session from the vault for good (e.g. it contained secrets)")
    s.add_argument("session")
    s.add_argument("--delete-transcript", action="store_true", help="also delete Claude Code's original transcript")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(fn=cmd_forget)
