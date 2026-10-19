"""Sharing with a hub: pushing now, and `interlatch hub`: enabling a hub, joining one, its people, projects,
folders, leaving and purging."""

from __future__ import annotations

import json
import sys

from ....core.config import set_config_value
from ..common import load_cfg, new_console, open_archive, push_now, wait_for_port


def cmd_push(args) -> int:
    from ....core.util import setup_logging

    cfg = load_cfg()
    setup_logging(cfg.logs_dir)
    if not cfg.is_spoke:
        print("This computer has not joined a hub. On the hub run `interlatch hub enable`, then run the command it "
              "prints here.", file=sys.stderr)
        return 1
    return push_now(cfg, quiet=args.quiet)


def _hub_url_guess(cfg) -> str | None:
    """The address other computers reach this one's dashboard at: its Tailscale name, when Serve is set up."""
    for host in cfg.server_allowed_hosts:
        if host.endswith(".ts.net"):
            return f"https://{host}"
    return None


def cmd_hub(args) -> int:
    import platform

    from ....core.machine import local_machine
    from ....core.util import local_str
    from ....ingestion.persistence import register_local
    from ....runtime.install import background_status, hooks_installed
    from ....sharing.client import dashboard_signin, leave_hub, redeem_invite
    from ....sharing.identity import new_token, read_token, token_path, write_token
    from ....sharing.projects import machines
    from ....sharing.protocol import HubError, join_command
    from ....sharing.pull import last_team
    from ....sharing.push import last_push, problem_text, send_problem

    cfg = load_cfg()
    console = new_console()
    action = args.action

    if action == "enable":
        if cfg.is_spoke:
            console.print(f"This computer sends its sessions to the hub at {cfg.hub_url}. Run `interlatch hub leave` "
                          "first to make it a hub itself.", highlight=False)
            return 1
        token = read_token(cfg)
        if not token or args.rotate:
            token = new_token(cfg)
            if args.rotate:
                console.print("New token: computers that joined with the old one must join again.")
        conn = open_archive(cfg)
        register_local(conn, cfg)
        conn.commit()
        conn.close()
        address = (args.url_opt or args.url or "").rstrip("/")
        if address and address != cfg.hub_address:  # what invites and sign-in links point at from now on
            set_config_value(cfg, "hub", "address", json.dumps(address))
        url = address or cfg.hub_address or (_hub_url_guess(cfg) or "").rstrip("/")
        console.print(f"[bold]{local_machine(cfg)['name']}[/] is a hub: other computers can send it their sessions.",
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
            set_config_value(cfg, "server", "allowed_hosts", json.dumps([*cfg.server_allowed_hosts, name]))
            if cfg.server_host in ("127.0.0.1", "localhost"):
                console.print(f"[yellow]The dashboard listens on {cfg.server_host} only[/]: for other computers to reach "
                              f"{url} without Tailscale, set `interlatch config set server.host 0.0.0.0` and restart it. "
                              "Its dashboard then opens on other devices only for people signed in: add yourself with "
                              "`interlatch hub invite <your name> --email <email> --role admin`.", highlight=False)
        console.print("On each other computer, install Interlatch and run:\n")
        console.print(f"  [bold]{join_command(url, token)}[/]\n", highlight=False, soft_wrap=True)
        console.print("[dim]The token lets a computer send sessions here; keep it private. "
                      "`interlatch hub enable --rotate` replaces it. To give each person a token of their own and a "
                      "dashboard sign-in instead: `interlatch hub invite <name> --email <email>`.[/]", highlight=False)
        if not wait_for_port(cfg.server_port, 1.0):
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
        if read_token(cfg) and not cfg.is_spoke:
            console.print("This computer is a hub itself (`interlatch hub disable` first).")
            return 1
        url = args.url.rstrip("/")
        if "://" not in url:
            url = f"https://{url}"
        token = args.token
        if args.code:
            try:
                got = redeem_invite(cfg, url, args.code)
            except HubError as exc:
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
        write_token(cfg, token)
        set_config_value(cfg, "hub", "url", json.dumps(url))
        if args.share:
            set_config_value(cfg, "hub", "share", json.dumps(args.share))
        set_config_value(cfg, "hub", "all_folders", "true" if args.all_folders else "false")
        from ....core.config import load_config

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
        code = push_now(cfg)
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
        leave_hub(cfg)
        console.print(f"Left the hub at {cfg.hub_url}. This computer records and analyzes its own sessions again; "
                      "the hub keeps what it was sent.", highlight=False)
        return 0

    if action == "disable":
        if cfg.is_spoke or not read_token(cfg):
            console.print("This computer is not a hub.")
            return 0
        token_path(cfg).unlink(missing_ok=True)
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
            link = dashboard_signin(cfg)
        except HubError as exc:
            console.print(f"Can't get a sign-in link: {exc}", highlight=False)
            return 1
        console.print("Open this within a few minutes to see the hub's dashboard as you (it works once):\n")
        console.print(f"  {link}\n", highlight=False, soft_wrap=True)
        return 0

    # status
    if cfg.is_spoke:
        last = last_push(cfg)
        console.print(f"Sends its sessions to the hub at [bold]{cfg.hub_url}[/]"
                      + (" · knowledge only (transcripts stay here)" if cfg.shares_knowledge else "")
                      + ((" · from every folder" if cfg.hub_all_folders else " · from the hub's projects only")
                         if cfg.shares_knowledge else ""), highlight=False)
        console.print(f"  last push: {local_str(last['at']) + ' · ' + last['summary'] if last and last.get('at') else 'never'}",
                      highlight=False)
        if problem := send_problem(last):
            headline, fix = problem_text(problem)
            console.print(f"  [red]![/] {headline}: nothing sent since {local_str(problem['since'])} "
                          f"({problem['attempts']} tries; last: {problem['error']})\n    {fix}", highlight=False)
        elif last and (failure := last.get("failure")):  # not worth a warning yet: a hub restarting, a laptop offline
            console.print(f"  the last try failed ({local_str(failure['at'])}): {failure['error']}; it tries again "
                          "with the next sync", highlight=False)
        team = last_team(cfg)
        if team:
            console.print(f"  team lessons here: {team.get('lessons', 0)} from the hub's team store (as of {team.get('at')})",
                          highlight=False)
        if cfg.hub_folders:
            n = len(cfg.hub_folders)
            console.print(f"  {n} folder{'s' * (n != 1)} added to projects on the hub (`interlatch hub folders`)", highlight=False)
        return 0
    conn = open_archive(cfg)
    rows = machines(conn, cfg)
    conn.close()
    role = "a hub" if read_token(cfg) else "not a hub (`interlatch hub enable` makes it one)"
    console.print(f"[bold]{local_machine(cfg)['name']}[/] is {role}.", highlight=False)
    if read_token(cfg) and cfg.hub_accept == "knowledge":
        console.print("  takes knowledge only: computers that send transcripts are turned away "
                      "(`interlatch config set hub.accept everything` takes them again)", highlight=False)
    from ....core.util import local_str

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
    from ....sharing.identity import read_token
    from ....storage import team as team_store

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
    if not read_token(cfg):
        console.print("[yellow]This computer is not a hub yet[/]: `interlatch hub enable`.", highlight=False)
    return 0


ROLE_WORDS = {"admin": "an admin", "member": "a member", "readonly": "read-only"}


def _projects_arg(conn, args) -> tuple[list[str] | None, str | None]:
    """--project … (hub project names or paths) or --all-projects as people.add takes it: (projects, problem)."""
    from ....sharing.projects import hub_projects

    if args.all_projects and args.project:
        return None, "Pass either --project or --all-projects, not both."
    if args.all_projects:
        return None, None
    known = hub_projects(conn)
    out = []
    for name in args.project or []:
        project, problem = _pick_project(known, name)
        if problem:
            return None, problem + "\n`interlatch hub project add <folder>` sets up a project before anything was sent to it."
        out.append(project["path"])
    return out, None


def _projects_words(conn, projects: list[str] | None) -> str:
    from ....core.paths import project_name_for

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
    from ....sharing import people

    key = (key or "").strip()
    if key.isdigit():
        p = people.get(conn, int(key))
        return p if p and not p["removed_at"] else None
    return people.by_email(conn, key) if key else None


def _hub_purge(cfg, console, args) -> int:
    """`interlatch hub purge <email|id|computer> --project <name>… | --outside-access`: remove for good what a person's
    computers (or one computer) sent to this hub, in some projects or outside the ones they see."""
    from ....core.machine import local_machine
    from ....core.paths import project_name_for
    from ....sharing import people
    from ....sharing.projects import computers_of
    from ....sharing.protocol import HubError
    from ....sharing.receiver import purge, purge_targets

    if cfg.is_spoke:
        console.print(f"Purge runs on the hub; this computer sends to the hub at {cfg.hub_url}.", highlight=False)
        return 1
    if not args.url or bool(args.project) == bool(args.outside_access) or args.all_projects:
        console.print("Usage: interlatch hub purge <email|id|computer> --project <name> … | --outside-access [--yes]")
        return 2
    conn = open_archive(cfg)
    try:
        person = _find_person(conn, args.url)
        if person:
            machines, who = computers_of(conn, person["id"]), f"{person['name']}'s computers"
        else:
            key = args.url.strip().lower()
            machines = [r[0] for r in conn.execute(
                "SELECT id FROM machines WHERE id != ? AND (lower(name) = ? OR (length(?) >= 8 AND id LIKE ? || '%'))",
                (local_machine(cfg)["id"], key, key, key))]
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
            targets = purge_targets(conn, machines, keep=keep)
            where = f"outside {_projects_words(conn, keep)}"
        else:
            projects, problem = _projects_arg(conn, args)
            if problem:
                console.print(problem, highlight=False)
                return 1
            targets = purge_targets(conn, machines, projects=projects)
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
            got = purge(cfg, conn, targets, person_id=person["id"] if person else None)
        except HubError as exc:
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
    from ....core.util import local_str
    from ....sharing import people
    from ....sharing.identity import read_token
    from ....sharing.protocol import invite_command, invite_link

    if cfg.is_spoke:
        console.print(f"People belong to the hub; this computer sends to the hub at {cfg.hub_url}.", highlight=False)
        return 1
    action = args.action
    conn = open_archive(cfg)
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
                console.print(f"  {invite_command(where, code)}\n", highlight=False, soft_wrap=True)
            console.print("Or in their browser, to see the hub's dashboard:")
            console.print(f"  {invite_link(where, code)}\n", highlight=False, soft_wrap=True)
            if not address:
                console.print("[yellow]This hub has no address yet[/]: `interlatch hub enable --url https://<address>` "
                              "sets the one other computers and browsers reach it at.", highlight=False)
            console.print("[dim]Pass them on by chat; the code is not shown again. It joins one computer or opens one "
                          "browser; `interlatch hub invite` again makes another.[/]", highlight=False)
            if not read_token(cfg):
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
        set_config_value(cfg, "hub", "shared_token", json.dumps(on))
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
    from ....projects.routing import declared_projects
    from ....sharing.identity import read_token
    from ....sharing.projects import add_project, hub_projects, remove_project
    from ....sharing.protocol import HubError

    if cfg.is_spoke:
        console.print(f"Projects belong to the hub; this computer sends to the hub at {cfg.hub_url}. To file a folder "
                      "here under one of its projects: `interlatch hub add-folder <folder> --project <name>`.",
                      highlight=False)
        return 1
    verb = (args.url or "list").lower()
    if verb not in ("add", "remove", "list"):
        console.print("Usage: interlatch hub project add <folder> | remove <folder> | list")
        return 2
    conn = open_archive(cfg)
    try:
        if verb == "list":
            set_up = set(declared_projects(conn))
            rows = hub_projects(conn)
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
            got = add_project(cfg, conn, args.value) if verb == "add" else remove_project(cfg, conn, args.value)
        except HubError as exc:
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
        if not read_token(cfg):
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

    from ....core.paths import normalize_project_path
    from ....sharing.client import handshake, leave_project, rejoin_project
    from ....sharing.projects import last_folders
    from ....sharing.protocol import FolderError, HubError

    if not cfg.is_spoke:
        console.print("This computer has not joined a hub.")
        return 1
    if not args.project or len(args.project) > 1:
        console.print(f"Which project on the hub? One `--project <name>`: interlatch hub {args.action} --project <name>")
        return 2
    wanted = args.project[-1]
    try:  # the hub's own list if it can be reached, else the one from the last push; and the projects left
        known = handshake(cfg).get("projects") or []
    except HubError:
        known = (last_folders(cfg) or {}).get("projects") or []
    known += [{"path": x, "name": Path(x).name} for x in cfg.hub_left
              if x not in {normalize_project_path(k.get("path")) for k in known}]
    project, problem = _pick_project(known, wanted)
    if problem:
        console.print(problem, highlight=False)
        return 1
    name = project.get("name") or Path(project["path"]).name
    if args.action == "rejoin":
        if normalize_project_path(project["path"]) not in cfg.hub_left:
            console.print(f"This computer hasn't left {name}.", highlight=False)
            return 0
        cfg = rejoin_project(cfg, project["path"])
        console.print(f"Rejoined [bold]{name}[/]: its sessions are shared again, and its teammates' lessons come back.",
                      highlight=False)
    else:
        try:
            cfg = leave_project(cfg, project["path"])
        except FolderError as exc:
            console.print(str(exc), highlight=False)
            return 1
        console.print(f"Left [bold]{name}[/]: this computer no longer shares its sessions there or gets its teammates' "
                      "lessons. What it already shared stays on the hub. `interlatch hub rejoin --project "
                      f"{wanted}` undoes this.", highlight=False)
    if args.no_push:
        console.print("The hub hears of it at the next push (`interlatch push`, or the background sync).")
        return 0
    return push_now(cfg)


def _hub_folders(cfg, console, args) -> int:
    """`interlatch hub add-folder | remove-folder | folders`: folders here whose sessions belong to a project on the hub."""
    from pathlib import Path

    from ....projects.routing import git_info
    from ....sharing.client import add_folder, handshake, remove_folder
    from ....sharing.projects import last_folders
    from ....sharing.protocol import FolderError, HubError

    if not cfg.is_spoke:
        console.print("This computer doesn't send its sessions to a hub. Folders are added on a computer that does "
                      "(after `interlatch hub join`); on the hub, `interlatch hub status` lists what each one added.")
        return 1
    action = args.action

    if action == "folders":
        if args.list:
            try:
                hello = handshake(cfg)
            except HubError as exc:
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
        report = (last_folders(cfg) or {}).get("folders") or {}
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
            cfg, taken = remove_folder(cfg, key)
        except HubError as exc:
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
            hello = handshake(cfg)
        except HubError as exc:
            console.print(f"Can't reach the hub: {exc}", highlight=False)
            return 1
        project, problem = _pick_project(hello.get("projects") or [], args.project[-1])
        if problem:
            console.print(problem, highlight=False)
            return 1
        name = project.get("name") or Path(project["path"]).name
        try:
            cfg, remote = add_folder(cfg, folder, project, hello)
        except FolderError as exc:
            console.print(str(exc), highlight=False)
            return 1
        if remote:
            console.print(f"Sessions in {folder} already go to {name}: the hub knows its git remote ({remote}). "
                          "Nothing to add.", highlight=False)
            return 0
        info = git_info(folder)
        console.print(f"Sessions in {folder} and the folders below it now go to [bold]{name}[/] on the hub "
                      f"({project['path']}).", highlight=False)
        if info:
            console.print(f"[dim]Its git remote ({info[1]}) is not one the hub knows, so the folder decides.[/]",
                          highlight=False)

    if args.no_push:
        console.print("The hub files them at the next push (`interlatch push`, or the background sync).")
        return 0
    code = push_now(cfg)
    moved = (last_folders(cfg) or {}).get("moved") or 0
    if moved:
        console.print(f"{moved} session{'s' * (moved != 1)} already on the hub moved, with {'their' if moved != 1 else 'its'} "
                      "knowledge.", highlight=False)
    for o in (((last_folders(cfg) or {}).get("folders") or {}).get(folder) or {}).get("overridden") or []:
        console.print(f"[yellow]{o['repo']}[/] inside it goes to {Path(o['project']).name} instead: the hub knows its "
                      "git remote.", highlight=False)
    return code


def add_hub(sub) -> None:
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


def add_push(sub) -> None:
    s = sub.add_parser("push", help="send this computer's new sessions to its hub now (the background job does this)")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_push)
