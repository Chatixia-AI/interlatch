"""Operating Interlatch: its status, the hub in a container, the Postgres mirror, the usage leaderboard, and
reaching the dashboard through a tailnet."""

from __future__ import annotations

import json
import sys

from ....core.config import set_config_value
from ..common import load_cfg, new_console, open_archive, wait_for_port


def cmd_status(args) -> int:
    from ....analysis.queue import PAUSE_KEY, QUEUE_REASON_LABEL, count_pending
    from ....analysis.runners.factory import make_runner
    from ....analysis.runners.settings import env_only_key
    from ....core.util import human_cost, local_str
    from ....runtime.connectors import agents_doctor
    from ....runtime.install import UI_LABEL, executable, hooks_installed, launchd_status, statusline_installed
    from ....runtime.statusline import WINDOW_LABEL, plan_usage
    from ....storage.kv import kv_get

    cfg = load_cfg()
    conn = open_archive(cfg)
    console = new_console()
    ok = lambda b: "[green]✓[/]" if b else "[red]✗[/]"  # noqa: E731
    hooks = hooks_installed(cfg)
    ld = launchd_status()
    counts = {r[0]: r[1] for r in conn.execute("SELECT analysis_status, COUNT(*) FROM sessions GROUP BY 1")}
    pending = count_pending(conn, cfg)
    spent = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM analyses").fetchone()[0]
    console.print(f"[bold]Interlatch[/] · home {cfg.home}")
    from ....sharing.identity import read_token
    from ....sharing.push import last_push, problem_text, send_problem

    if cfg.is_spoke:
        last = last_push(cfg)
        console.print(f"  sends {'what it learns' if cfg.shares_knowledge else 'its sessions'} to the hub at {cfg.hub_url}"
                      f" (last push: {local_str(last['at']) + ' · ' + last['summary'] if last and last.get('at') else 'never'})",
                      highlight=False)
        if problem := send_problem(last):
            headline, fix = problem_text(problem)
            console.print(f"    [red]![/] {headline}: nothing sent since {local_str(problem['since'])} "
                          f"({problem['attempts']} tries). {fix}", highlight=False)
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
    console.print(f"  {ok(runner.available())} analysis by {runner.label}: "
                  f"{runner.where() if runner.available() else runner.unavailable_reason()}")
    if runner.available() and (env := env_only_key(cfg, runner.name)):
        console.print(f"    [yellow]![/] its key is only in ${env}: background analysis and the dashboard don't see it "
                      f"(`interlatch config set-key {runner.name}` saves it)", highlight=False)
    _print_agents(console, agents_doctor(cfg, conn, executable()), hooked=bool(hooks.get("SessionEnd")))
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


def _print_agents(console, rows: list[dict], *, hooked: bool) -> None:
    """`interlatch status`: a line per agent found here, with Interlatch's MCP server (and Claude Code's hooks) in it,
    and whether the command each one runs is still there and is this install."""
    if not rows:
        return
    console.print("  agents:")
    width = max(len(r["label"]) for r in rows)
    for r in rows:
        fix = f"`interlatch connect {r['name']}`"
        what = {"mcp": "MCP server", "hook": "hook"}
        problems = [f"{what[p['what']]} runs {p['command']}, " + ("which is gone" if p["problem"] == "missing" else "not this install")
                    for p in r["problems"]]
        if problems:
            missing = any(p["problem"] == "missing" for p in r["problems"])
            mark, text = "[red]✗[/]" if missing else "[yellow]![/]", "; ".join(problems) + f" ({fix} fixes it)"
        elif r["mcp"]:
            mark, text = "[green]✓[/]", "MCP server" + (" + SessionEnd hook" if r["name"] == "claude" and hooked else "")
        elif r["optional"]:
            mark, text = "[dim]–[/]", f"no MCP server (optional: {fix})"
        else:
            mark, text = "[red]✗[/]", f"no MCP server ({fix} adds it)"
        console.print(f"    {mark} {r['label']:<{width}}  {text} · [dim]{r['config']}[/]", highlight=False)


def cmd_feedback(args) -> int:
    """`interlatch feedback`: report a problem or suggest an idea on GitHub, with this install's details filled in."""
    from ....runtime.feedback import details, report

    cfg = load_cfg()
    console = new_console()
    try:
        conn = open_archive(cfg)
    except Exception:  # a broken archive is worth reporting too: the report just doesn't list the agents
        conn = None
    rows = details(cfg, conn)
    if conn is not None:
        conn.close()
    r = report(args.kind, rows, args.title or "")
    if args.print:
        print(r["url"])
        return 0
    where = "a new issue" if r["kind"] == "bug" else "a new discussion in Ideas"
    console.print(f"[bold]{'Report a problem' if r['kind'] == 'bug' else 'Suggest an idea'}[/]: opens {where} on "
                  "GitHub with these details filled in. Nothing is sent from here: you write the rest and post it "
                  "yourself.", highlight=False)
    for label, value in r["details"]:
        console.print(f"  {label + ':':<17} {value}", highlight=False, markup=False)
    if sys.stdin.isatty() and sys.stdout.isatty():
        if input("Open it in your browser? [Y/n] ").strip().lower() in ("", "y", "yes"):
            import webbrowser

            if webbrowser.open(r["url"]):
                return 0
    console.print(f"Open this address to write it:\n{r['url']}", highlight=False, markup=False, soft_wrap=True)
    return 0


def cmd_container(args) -> int:
    from ....runtime.container import main

    return main()


def cmd_leaderboard(args) -> int:
    """`interlatch leaderboard`: the usage leaderboard: where you stand, or join, send now, or leave."""
    from ....sharing import leaderboard as lb

    cfg = load_cfg()
    console = new_console()
    if not cfg.game_enabled and args.action in ("join", "send"):  # leaving still works: the row is deleted
        console.print("Game mode is off, and the leaderboard is part of it. Turn it on in Settings › Appearance, or: "
                      "`interlatch config set game.enabled true`.", highlight=False)
        return 1
    try:
        if args.action == "join" and lb.status(cfg)["signed_in"]:  # signed in already (for the Party): join from here
            conn = open_archive(cfg)
            lb.join(cfg, conn)
            conn.close()
        elif args.action == "join":  # signing in is a browser's job: Google's page, then back to the dashboard
            import webbrowser

            url = f"http://127.0.0.1:{cfg.server_port}/#/innerlatch/leaderboard"
            console.print(f"Sign in with Google from the Leaderboard page in Innerlatch: {url}", highlight=False)
            if not wait_for_port(cfg.server_port, 1.0):
                console.print("[yellow]The dashboard isn't running[/]: start it with `interlatch ui`.", highlight=False)
                return 1
            webbrowser.open(url)
            return 0
        if args.action == "send":
            conn = open_archive(cfg)
            st = lb.send(cfg, conn, force=True)
            conn.close()
            if not lb.on_board(st):
                console.print("Not on the leaderboard: `interlatch leaderboard join`.", highlight=False)
                return 1
        elif args.action == "leave":
            was = lb.status(cfg)
            lb.leave(cfg)
            console.print("Left the leaderboard: your row is deleted, and nothing more is sent." if was["joined"] else
                          "Signed out: this computer forgot the Google sign-in." if was["signed_in"] else
                          "Not signed in.", highlight=False)
            return 0
    except lb.LeaderboardError as exc:
        console.print(f"[red]{exc}[/]", highlight=False)
        return 1
    st = lb.status(cfg)
    if st["signed_in"] and not st["joined"]:
        console.print(f"Signed in as [bold]{st['name']}[/] for the Party, not on the leaderboard: "
                      "`interlatch leaderboard join` joins it.", highlight=False)
        return 0
    if not st["joined"]:
        console.print("Not on the leaderboard. `interlatch leaderboard join` opens the Leaderboard page in Innerlatch, "
                      "where you sign in with Google.", highlight=False)
        return 0
    arena = " · in the arena" if st.get("arena") else ""
    console.print(f"On the leaderboard as [bold]{st['name']}[/]{arena} · last sent {st['last_sent'] or 'not yet'}",
                  highlight=False)
    for problem in (st.get("paused"), st.get("error")):
        if problem:
            console.print(f"  [yellow]{problem}[/]", highlight=False)
    conn = open_archive(cfg)
    mine = lb.totals(conn, cfg)
    conn.close()
    for days in lb.WINDOWS:
        console.print(f"  {days:>2} days: {mine[f'h{days}']:,.1f} h · {mine[f's{days}']:,} sessions · "
                      f"{mine[f't{days}']:,} tokens", highlight=False)
    return 0


def cmd_mirror(args) -> int:
    """`interlatch mirror`: the copy of this archive in Postgres ([mirror] to = "postgres"): what it holds, or write it now."""
    from ....core.util import setup_logging
    from ....storage import mirror

    cfg = load_cfg()
    console = new_console()
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

    from ....runtime import tailnet

    cfg = load_cfg()
    console = new_console()
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
        set_config_value(cfg, "server", "allowed_hosts", json.dumps(hosts))
        set_config_value(cfg, "server", "allowed_users", "[]")
        console.print("The dashboard is no longer on your tailnet." if proc.returncode == 0 else
                      f"Tailscale said: {(proc.stderr or proc.stdout).strip()[:300]}", highlight=False)
        return 0

    # on
    set_config_value(cfg, "server", "allowed_hosts", json.dumps([*hosts, st.dns_name]))
    set_config_value(cfg, "server", "allowed_users", json.dumps([] if args.anyone or not st.login else [st.login]))
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
    if not wait_for_port(cfg.server_port, 1.0):
        console.print(f"[yellow]The dashboard is not running on port {cfg.server_port}.[/] `interlatch install` keeps it "
                      "running in the background (or run `interlatch ui`).", highlight=False)
    return 0


def add_status(sub) -> None:
    s = sub.add_parser("status", aliases=["doctor"], help="show installation and queue status")
    s.set_defaults(fn=cmd_status)


def add_feedback(sub) -> None:
    s = sub.add_parser("feedback", aliases=["bug"],
                       help="report a problem or suggest an idea on GitHub, with this install's details filled in")
    s.add_argument("kind", nargs="?", choices=["bug", "idea"], default="bug",
                   help="bug: a new issue (the default); idea: a new discussion in Ideas")
    s.add_argument("--title", help="the title to start with")
    s.add_argument("--print", action="store_true", help="only print the address, don't ask or open a browser")
    s.set_defaults(fn=cmd_feedback)


def add_leaderboard(sub) -> None:
    s = sub.add_parser("leaderboard", help="the usage leaderboard: sign in with Google and compare hours, sessions and tokens")
    s.add_argument("action", nargs="?", choices=["status", "join", "send", "leave"], default="status")
    s.set_defaults(fn=cmd_leaderboard)


def add_mirror(sub) -> None:
    s = sub.add_parser("mirror", help="a copy of this archive in a Postgres database you choose: what it holds, or write it now")
    s.add_argument("action", nargs="?", choices=["status", "sync"], default="status")
    s.add_argument("--full", action="store_true", help="with sync: write every row again, whatever the mirror holds")
    s.set_defaults(fn=cmd_mirror)


def add_tailnet(sub) -> None:
    s = sub.add_parser("tailnet", help="reach the dashboard from your phone and other computers through Tailscale")
    s.add_argument("action", nargs="?", choices=["on", "off", "status"], default="status")
    s.add_argument("--anyone", action="store_true", help="with on: let anyone in your tailnet in, not just your login")
    s.set_defaults(fn=cmd_tailnet)


def add_container(sub) -> None:
    s = sub.add_parser("container", help="run a hub in a container (docker/): set it up from INTERLATCH_* (or CHRONICLE_*) variables, "
                                         "then serve the dashboard and sync every 15 minutes")
    s.set_defaults(fn=cmd_container)
