"""Several hubs on one computer (docs/profiles.md): `interlatch hubs` (the hubs besides Personal's), `interlatch group`
(your groups of projects, each connected to a hub or to none) and the global `--hub`."""

from __future__ import annotations

import shlex

from ..common import load_cfg, new_console


def _moved_words(moved) -> str:
    n = sum(moved.sessions.values())
    if not n:
        return "No recorded session changes hub."

    def name(o: str) -> str:
        return moved.labels.get(o, o)

    def count(c: int) -> str:
        return f"{c} session{'s' * (c != 1)}"

    went = [f"{count(c)} to {name(o)}" for o, c in sorted(moved.to.items()) if o != "waiting"]
    if moved.to.get("waiting"):
        went.append(f"{count(moved.to['waiting'])} wait for you (`interlatch group waiting`)")
    came = ", ".join(f"{c} from {name(o)}" for o, c in sorted(moved.sessions.items()))
    return f"{n} recorded session{'s' * (n != 1)} change hub: {'; '.join(went)} ({came})."


def _report(console, moved, *, done: bool = True) -> None:
    console.print(_moved_words(moved), highlight=False)
    for o, url in sorted(moved.hubs.items()):
        if done and moved.taken.get(o):
            console.print(f"The hub at {url} took back {moved.taken[o]} of them.", highlight=False)
        elif not done and moved.on_hub.get(o):
            console.print(f"The hub at {url} has {moved.on_hub[o]} of them: it takes them back first.", highlight=False)
        if o in moved.too_old:
            console.print(f"[yellow]The hub at {url} runs an older version that can't take them back: its copies stay "
                          "there.[/]", highlight=False)
        elif o in moved.kept:
            console.print(f"[yellow]The hub at {url} couldn't be asked ({moved.kept[o]})[/]"
                          + (": it takes them back the next time this computer sends to it." if done else "."),
                          highlight=False)


# ------------------------------------------------------------------ interlatch hubs
def cmd_hubs(args) -> int:
    from ....application import profiles as ops
    from ....core.profiles import RegistryError

    cfg = load_cfg()
    console = new_console()
    action = args.action or "list"
    try:
        if action == "list":
            return _list(console, ops.summary(cfg))
        if not args.name:
            console.print(f"Usage: interlatch hubs {action} <name>" + (" [<address> --code <code>]" * (action == "add")))
            return 2
        if action == "add":
            p = ops.add_hub(cfg, args.name, remotes=args.remotes or [])
            console.print(f"Added the hub [bold]{p.label}[/]. Its folder is {p.home}.", highlight=False)
            if args.address:
                from ..main import main

                return main(["--hub", p.label, "hub", "join", args.address, *(["--code", args.code] if args.code else []),
                             *(["--token", args.token] if args.token else []), "--share", "knowledge",
                             *(["--all-folders"] if args.all_folders else [])])
            console.print(f"Join it with the code its admin sent: `interlatch --hub {shlex.quote(p.label)} hub join "
                          "<address> --code <code>`. Then connect a group to it: `interlatch group connect <group> "
                          f"{shlex.quote(p.label)}`.", highlight=False)
            return 0
        if action == "remove":
            p, _ = ops.remove_hub(cfg, args.name, delete_data=args.delete_data)
            console.print(f"Removed the hub {p.label}. What its groups held waits for you to choose "
                          "(`interlatch group waiting`)." + ("" if args.delete_data else
                                                             f" Its folder {p.home} stays: --delete-data removes it."),
                          highlight=False)
            return 0
    except (ops.ProfileError, RegistryError) as exc:
        console.print(str(exc), highlight=False)
        return 1
    return 2


def _list(console, rows: list[dict]) -> int:
    for row in rows:
        if row["id"] == "waiting":
            held = sorted({*row["held"], *(f for f, o in row["choices"].items() if o == "Waiting")})
            if row["sessions"] or held:
                console.print(f"\n[bold]Waiting[/] · {row['sessions']} session{'s' * (row['sessions'] != 1)}"
                              " (`interlatch group waiting`)", highlight=False)
            for f in held:
                console.print(f"  {f}", highlight=False)
            chosen = sorted((f, o) for f, o in row["choices"].items() if o != "Waiting")
            if chosen:
                console.print("\n[bold]Your choices[/]", highlight=False)
            for f, o in chosen:
                console.print(f"  {f} → {o}", highlight=False)
            continue
        n = row["sessions"]
        count = "" if n is None else f" · {n} session{'s' * (n != 1)}"
        hub = f" · sends to {row['hub']}" if row["hub"] else " · not joined"
        console.print(f"[bold]{row['label']}[/]{count}{hub}", highlight=False)
        if row["id"] == "personal":
            console.print("  [dim]everything no other hub's groups hold[/]", highlight=False)
        for g in row["groups"]:
            console.print(f"  group {g}", highlight=False)
        for r in row["remotes"]:
            console.print(f"  repositories matching {r}", highlight=False)
        if row["id"] != "personal" and not row["groups"] and not row["remotes"]:
            console.print("  [dim]no group connected yet: `interlatch group connect <group> "
                          f"{shlex.quote(row['label'])}`[/]", highlight=False)
    if len(rows) == 2:
        console.print("\nOnly Personal's hub. `interlatch hubs add <name>` adds another.")
    return 0


# ------------------------------------------------------------------ interlatch group
def cmd_group(args) -> int:
    from ....application import profiles as ops
    from ....core.profiles import RegistryError
    from ....projects import groups as gr
    from ....storage.sqlite import connect

    cfg = load_cfg()
    console = new_console()
    action, rest = args.action or "list", list(args.args or [])

    def need(n: int, usage: str) -> bool:
        if len(rest) < n:
            console.print(f"Usage: interlatch group {usage}")
            return False
        return True

    try:
        if action == "list":
            return _groups(cfg, console)
        if action == "waiting":
            return _waiting(cfg, console)
        if action == "where":
            if not need(1, "where <folder>"):
                return 2
            r, reg, remote = ops.where(cfg, rest[0])
            console.print(f"[bold]{reg.label(r.owner)}[/] — {_why(r, reg, remote)}", highlight=False)
            return 0
        if action == "create":
            if not need(1, "create <name> [--folder <folder>]…"):
                return 2
            gid, moved = ops.change_groups(cfg, lambda c: gr.save(c, None, rest[0], args.folder or []),
                                           leave_on_hub=args.leave_on_hub)
            console.print(f"Made the group [bold]{rest[0]}[/]. Connect it to a hub with `interlatch group connect "
                          f"{shlex.quote(rest[0])} <hub>`.", highlight=False)
            return 0
        if action in ("add-folder", "remove-folder"):
            if not need(2, f"{action} <group> <folder>"):
                return 2
            conn = connect(cfg.db_path)
            try:
                g = ops._group(conn, rest[0])
            finally:
                conn.close()
            folder = gr.clean_folder(rest[1])
            folders = [f for f in g["folders"] if f != folder] + ([folder] if action == "add-folder" else [])
            if folder is None or (action == "remove-folder" and folder not in g["folders"]):
                console.print(f"{rest[1]} is not one of {g['name']}'s folders." if folder else "That isn't a folder.",
                              highlight=False)
                return 1
            _, moved = ops.change_groups(cfg, lambda c: gr.save(c, g["id"], g["name"], folders),
                                         leave_on_hub=args.leave_on_hub)
            console.print(f"{g['name']}'s folders: {', '.join(folders) or 'none'}.", highlight=False)
            if moved.sessions:
                _report(console, moved)
            return 0
        if action == "remove":
            if not need(1, "remove <group>"):
                return 2
            conn = connect(cfg.db_path)
            try:
                g = ops._group(conn, rest[0])
            finally:
                conn.close()
            from ....projects.hubgroups import hub_of_group

            if hub_of_group(cfg.profiles, g["id"]):
                console.print(f"{g['name']} is connected to a hub: `interlatch group disconnect "
                              f"{shlex.quote(g['name'])}` first.", highlight=False)
                return 1
            ops.change_groups(cfg, lambda c: gr.delete(c, g["id"]))
            console.print(f"Removed the group {g['name']}; its projects stay as they are.", highlight=False)
            return 0
        if action == "connect":
            if not need(2, "connect <group> <hub>"):
                return 2
            moved = ops.connect(cfg, rest[0], rest[1], leave_on_hub=args.leave_on_hub, dry_run=args.dry_run)
            if not args.dry_run:
                console.print(f"The group {rest[0]} goes to {moved.labels.get(moved.owner, rest[1])} only. Personal "
                              "still records and analyzes it.", highlight=False)
            _report(console, moved, done=not args.dry_run)
            return 0
        if action == "disconnect":
            if not need(1, "disconnect <group>"):
                return 2
            was, moved = ops.disconnect(cfg, rest[0], dry_run=args.dry_run)
            console.print(f"The group {rest[0]} is no longer connected to {was}. What it held waits for you to choose: "
                          "`interlatch group waiting`.", highlight=False)
            _report(console, moved, done=not args.dry_run)
            return 0
        if action == "choose":
            if not need(2, "choose <folder> <hub|personal>"):
                return 2
            moved = ops.choose(cfg, rest[0], rest[1], leave_on_hub=args.leave_on_hub, dry_run=args.dry_run)
            _report(console, moved, done=not args.dry_run)
            return 0
    except (ops.ProfileError, RegistryError, gr.GroupError) as exc:
        console.print(str(exc) + (" Pass --leave-on-hub to move them anyway." * getattr(exc, "can_leave", False)),
                      highlight=False)
        return 1
    return 2


def _why(r, reg, remote) -> str:
    if r.rule == "folder":
        return f"inside {r.detail}, which a group connected to it holds"
    if r.rule == "chosen":
        return f"you chose it for {r.detail}"
    if r.rule == "held":
        return f"{r.detail} waits for you to choose (`interlatch group choose`)"
    if r.rule == "remote":
        return f"its repository ({remote}) matches {r.detail}"
    if r.rule == "conflict":
        return f"waits for you: {r.detail}"
    if not reg.profiles:
        return "there is no hub but Personal's"
    return "no group connected to another hub holds it" + (f" (its remote: {remote})" if remote else "")


def _groups(cfg, console) -> int:
    from ....core.profiles import load_registry
    from ....projects.groups import groups
    from ....projects.hubgroups import hub_of_group
    from ....storage.sqlite import connect

    if not cfg.db_path.exists():
        console.print("No groups yet.")
        return 0
    conn = connect(cfg.db_path, readonly=True)
    try:
        gs = groups(conn)
    finally:
        conn.close()
    reg = load_registry(cfg.home)
    if not gs:
        console.print("No groups yet. `interlatch group create <name> --folder <folder>` makes one (or the dashboard's "
                      "Projects page).")
        return 0
    for g in gs:
        hub = hub_of_group(reg, g["id"])
        to = f"→ {hub.label}" if hub else "→ Personal" + (f" (shared as {g['hub_project']})" if g["hub_project"] else "")
        console.print(f"[bold]{g['name']}[/] {to}", highlight=False)
        for f in g["folders"]:
            console.print(f"  {f}", highlight=False)
    return 0


def _waiting(cfg, console) -> int:
    from ....projects.owners import waiting
    from ....storage.sqlite import connect

    if not cfg.db_path.exists():
        console.print("Nothing waits.")
        return 0
    conn = connect(cfg.db_path, readonly=True)
    try:
        rows = waiting(conn)
    finally:
        conn.close()
    if not rows:
        console.print("Nothing waits.")
        return 0
    for r in rows:
        console.print(f"[bold]{r['folder'] or '(no folder)'}[/] · {r['sessions']} session{'s' * (r['sessions'] != 1)}"
                      + (f" · {r['remote']}" if r["remote"] else ""), highlight=False)
        if r["why"]:
            console.print(f"  [dim]{r['why']}[/]", highlight=False)
    console.print("\nThey are recorded here, but no hub gets them and no agent sees them. Settle one with "
                  "`interlatch group choose <folder> <hub|personal>`, or put it in a connected group.")
    return 0


def add_hubs(sub) -> None:
    s = sub.add_parser("hubs", help="the hubs this computer sends to besides Personal's: list, add, remove")
    s.add_argument("action", nargs="?", choices=["list", "add", "remove"])
    s.add_argument("name", nargs="?", help="the hub's name here, such as Acme")
    s.add_argument("address", nargs="?", help="add: the hub's address, to join it right away")
    s.add_argument("--code", help="add: the invite code its admin sent")
    s.add_argument("--token", help=argparse_hidden())
    s.add_argument("--all-folders", action="store_true",
                   help="add: share every folder its groups hold, not only those added to a project on the hub")
    s.add_argument("--remotes", action="append", metavar="PATTERN",
                   help="add: git remotes it owns wherever they are cloned, such as github.com/acme-corp/* (repeatable)")
    s.add_argument("--delete-data", action="store_true", help="remove: delete its folder (token, identity) too")
    s.set_defaults(fn=cmd_hubs)


def add_group(sub) -> None:
    s = sub.add_parser("group", help="your groups of projects, and the hub each one goes to: list, create, add-folder, "
                                     "remove-folder, remove, connect, disconnect, where, waiting, choose")
    s.add_argument("action", nargs="?", choices=["list", "create", "add-folder", "remove-folder", "remove", "connect",
                                                 "disconnect", "where", "waiting", "choose"])
    s.add_argument("args", nargs="*", help="create <name>; add-folder|remove-folder <group> <folder>; connect <group> "
                                           "<hub>; disconnect|remove <group>; where <folder>; choose <folder> <hub>")
    s.add_argument("--folder", action="append", metavar="FOLDER", help="create: a folder rule (repeatable)")
    s.add_argument("--leave-on-hub", action="store_true",
                   help="move sessions even if the hub they were sent to can't take them back now (it does at "
                        "the next send; a hub too old to take them back keeps them)")
    s.add_argument("--dry-run", action="store_true", help="connect, disconnect, choose: say what would move")
    s.set_defaults(fn=cmd_group)


def argparse_hidden():
    import argparse

    return argparse.SUPPRESS


def cmd_profile(args) -> int:
    """`interlatch profile` (0.28.1): its jobs moved to `interlatch hubs` and `interlatch group`."""
    import sys

    print("`interlatch profile` became `interlatch hubs` (the hubs this computer sends to) and `interlatch group` (your "
          "groups of projects, each connected to a hub): `interlatch hubs` lists them, `interlatch group connect "
          "<group> <hub>` gives a hub folders. Folders a profile owned stay its own (docs/profiles.md).", file=sys.stderr)
    return 2


def add_profile(sub) -> None:
    import argparse

    s = sub.add_parser("profile", help=argparse.SUPPRESS)  # 0.28.1's name, kept so its scripts say where it went
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_profile)
