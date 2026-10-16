"""Knowledge: browsing it, analyzing sessions and synthesizing knowledge bases now, the glossary, the systems map,
the weekly review, suggestions, friction, and the digest a new session gets."""

from __future__ import annotations

import json
import sys

from ..common import analyzer_label, escape_markup, failed_label, load_cfg, new_console, open_archive


def cmd_analyze(args) -> int:
    from ....analysis.queue import pending_sessions
    from ....application.records import resolve_session_id
    from ....application.workflows import run_worker
    from ....core.util import human_cost, setup_logging
    from ...exports.vault import export_markdown

    cfg = load_cfg()
    setup_logging(cfg.logs_dir)
    conn = open_archive(cfg)
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
    from ....analysis.runners.factory import make_runner

    if args.backend:
        cfg.analysis.backend = args.backend
    runner = make_runner(cfg)
    if args.dry_run:
        from ....analysis.digest import build_digest

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
                        export=export_markdown, progress=lambda m: print(f"  {m}"))
    print(f"{report.summary()} · analysis cost {human_cost(report.cost_usd)}")
    for sid, err in report.failed:
        print(f"  ! {failed_label(sid)}: {err[:300]}", file=sys.stderr)
    return 0 if not report.failed else 1


def cmd_synthesize(args) -> int:
    from ....core.util import setup_logging
    from ....knowledge.synthesize import GLOBAL, projects_needing_synthesis, synthesize_project

    cfg = load_cfg()
    setup_logging(cfg.logs_dir)
    conn = open_archive(cfg)
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


def cmd_knowledge(args) -> int:
    from ....application.search import search_knowledge
    from ....knowledge.ladder import stage_label

    cfg = load_cfg()
    conn = open_archive(cfg)
    console = new_console()
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


def cmd_glossary(args) -> int:
    from ....core.util import local_str, setup_logging
    from ....knowledge.glossary import build_glossaries, glossary_entries, lookup
    from ....knowledge.synthesize import GLOBAL

    cfg = load_cfg()
    setup_logging(cfg.logs_dir)
    conn = open_archive(cfg)
    console = new_console()
    project = None
    if args.project:
        row = conn.execute("SELECT project_path FROM sessions WHERE project_path = ? OR project_name = ? LIMIT 1",
                           (args.project, args.project)).fetchone()
        project = row[0] if row else args.project
    if args.themes:
        from ....knowledge.glossary import build_themes, themes_due

        due = themes_due(conn, force=args.force)
        conn.close()
        if not due:
            print("themes are up to date (use --force to regroup anyway)")
            return 0
        print(f"grouping {len(due)} categor{'y' if len(due) == 1 else 'ies'} into themes with {analyzer_label(cfg)}…")
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
        print(f"building {len(targets)} glossar{'y' if len(targets) == 1 else 'ies'} with {analyzer_label(cfg)}…")
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

    from ....projects.systems import build, landscape, system

    cfg = load_cfg()
    conn = open_archive(cfg)
    data = build(conn, cfg)
    conn.close()
    console = new_console()
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

    from ....core.util import setup_logging
    from ....knowledge.reviews import generate_review, week_bounds

    cfg = load_cfg()
    setup_logging(cfg.logs_dir)
    conn = open_archive(cfg)
    key = week_bounds(args.week)[0]
    row = conn.execute("SELECT markdown FROM reviews WHERE period = ?", (key,)).fetchone()
    if row and not args.regenerate:
        new_console().print(Markdown(row[0]))
        return 0
    print(f"writing review for {key} with {analyzer_label(cfg)}…")
    try:
        generate_review(conn, cfg, key)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    new_console().print(Markdown(conn.execute("SELECT markdown FROM reviews WHERE period = ?", (key,)).fetchone()[0]))
    return 0


def _project_arg(conn, value: str | None) -> str | None:
    """-p accepts a project's path or its name."""
    if not value:
        return None
    row = conn.execute("SELECT project_path FROM sessions WHERE project_path = ? OR project_name = ? LIMIT 1",
                       (value, value)).fetchone()
    return row[0] if row else value


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
    from ....core.util import setup_logging
    from ....knowledge import suggest

    cfg = load_cfg()
    setup_logging(cfg.logs_dir)
    conn = open_archive(cfg)
    console = new_console()
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

    from ....knowledge.friction import report

    cfg = load_cfg()
    conn = open_archive(cfg)
    got = report(conn, days=args.days, project=_project_arg(conn, args.project), noise=args.noise)
    if args.json:
        print(json.dumps(got, ensure_ascii=False, indent=1, default=str))
        return 0
    console = new_console()

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


def cmd_context(args) -> int:
    import os

    from ....runtime.hooks import build_session_context

    cfg = load_cfg()
    print(build_session_context(cfg, args.cwd or os.getcwd()) or "(no knowledge recorded for this directory yet)")
    return 0


def add_analyze(sub) -> None:
    from ....analysis.runners.factory import BACKENDS

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


def add_synthesize(sub) -> None:
    s = sub.add_parser("synthesize", help="rebuild project knowledge bases")
    s.add_argument("--project", help="project path or name")
    s.add_argument("--global", dest="global_", action="store_true", help="the cross-project playbook")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_synthesize)


def add_knowledge(sub) -> None:
    s = sub.add_parser("knowledge", aliases=["kb"], help="browse extracted knowledge")
    s.add_argument("query", nargs="?")
    s.add_argument("-k", "--kind")
    s.add_argument("-p", "--project")
    s.add_argument("-n", "--limit", type=int, default=30)
    s.add_argument("--brief", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_knowledge)


def add_glossary(sub) -> None:
    s = sub.add_parser("glossary", help="the vocabulary of your projects: terms, aliases, definitions")
    s.add_argument("term", nargs="?")
    s.add_argument("-p", "--project")
    s.add_argument("-c", "--category")
    s.add_argument("--rebuild", action="store_true", help="rebuild with the analysis model (-p PROJECT or --all)")
    s.add_argument("--all", action="store_true")
    s.add_argument("--themes", action="store_true", help="group big categories into themes with the analysis model (the Map's Theme level)")
    s.add_argument("--force", action="store_true", help="with --themes: regroup categories that have not changed")
    s.set_defaults(fn=cmd_glossary)


def add_systems(sub) -> None:
    s = sub.add_parser("systems", help="the Systems map: every project as a system with its parts, from manifests and sessions")
    s.add_argument("name", nargs="?", help="one system (its name or folder): its parts, connections and evidence")
    s.add_argument("--links", action="store_true", help="also list the links between systems")
    s.add_argument("--evidence", action="store_true", help="with NAME: every piece of evidence, with example commands")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_systems)


def add_review(sub) -> None:
    s = sub.add_parser("review", help="weekly engineering review written by the analysis model (default: last completed week)")
    s.add_argument("week", nargs="?", help="ISO week like 2026-W39, 'current' or 'last'")
    s.add_argument("--regenerate", action="store_true")
    s.set_defaults(fn=cmd_review)


def add_suggest(sub) -> None:
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


def add_friction(sub) -> None:
    s = sub.add_parser("friction", help="what goes wrong: recurring failure causes across your sessions, and tool error rates")
    s.add_argument("-p", "--project", help="only this project (path or name)")
    s.add_argument("--days", type=int, help="only the last N days")
    s.add_argument("--json", action="store_true")
    s.add_argument("--noise", action="store_true", help="also show failures that are expected (test runs, read-only checks, provider hiccups)")
    s.set_defaults(fn=cmd_friction)


def add_context(sub) -> None:
    s = sub.add_parser("context", help="print the knowledge digest a new session in this directory would get")
    s.add_argument("--cwd")
    s.set_defaults(fn=cmd_context)
