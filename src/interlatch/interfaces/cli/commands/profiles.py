"""`interlatch profile`: named profiles, each with its own hub (docs/profiles.md), and the global `--profile`."""

from __future__ import annotations

from ..common import load_cfg, new_console


def _moved_words(reg_label, moved) -> str:
    n = sum(moved.sessions.values())
    if not n:
        return "No recorded session moves."
    parts = ", ".join(f"{c} from {reg_label(o)}" for o, c in sorted(moved.sessions.items()))
    return f"{n} recorded session{'s' * (n != 1)} move{'s' * (n == 1)} to {reg_label(moved.owner)} ({parts})."


def cmd_profile(args) -> int:
    from ....application import profiles as ops
    from ....core.profiles import PERSONAL, RegistryError, load_registry

    cfg = load_cfg()
    console = new_console()
    action = args.action

    def report(moved, *, done: bool = True) -> None:
        def label(owner: str) -> str:  # as the change leaves them: a profile a dry run would make has its name too
            if owner in moved.labels:
                return moved.labels[owner]
            reg = load_registry(cfg.home)
            return reg.label(owner) if reg else ("Personal" if owner == PERSONAL else owner)

        console.print(_moved_words(label, moved), highlight=False)
        if moved.on_hub:
            console.print(f"[yellow]The hub at {moved.hub} has {moved.on_hub} of them[/]"
                          + (": those copies stay there." if done else "."), highlight=False)
        elif not moved.checked:
            console.print(f"[yellow]The hub at {moved.hub} couldn't be asked which of them it has.[/]", highlight=False)

    try:
        if action == "list":
            return _list(console, ops.summary(cfg))
        if action == "where":
            if not args.target:
                console.print("Usage: interlatch profile where <folder>")
                return 2
            r, reg, remote = ops.where(cfg, args.target)
            console.print(f"[bold]{reg.label(r.owner)}[/] — {_why(r, reg, remote)}", highlight=False)
            return 0
        if action == "waiting":
            return _waiting(cfg, console)
        if action == "create":
            if not args.target:
                console.print("Usage: interlatch profile create <name> [--owns <folder>]… [--remotes <pattern>]…")
                return 2
            p, moved = ops.create(cfg, args.target, owns=args.owns or [], remotes=args.remotes or [],
                                  leave_on_hub=args.leave_on_hub, dry_run=args.dry_run)
            if args.dry_run:
                report(moved, done=False)
                return 0
            console.print(f"Made the profile [bold]{p.label}[/] ({p.id}): its folder is {p.home}, its dashboard's port "
                          f"{p.port}.", highlight=False)
            for f in p.owns:
                console.print(f"  owns {f}", highlight=False)
            for r in p.remotes:
                console.print(f"  owns repositories matching {r}", highlight=False)
            report(moved)
            console.print("Personal still records and analyzes every session, and no longer shares these with its "
                          f"hub. Sending them to {p.label}'s own hub comes in a later version (docs/profiles.md).",
                          highlight=False)
            return 0
        if action in ("own", "choose"):
            if not args.target or not args.name:
                console.print(f"Usage: interlatch profile {action} <folder> <profile{'|personal' * (action == 'choose')}>")
                return 2
            fn = ops.own if action == "own" else ops.choose
            moved = fn(cfg, args.target, args.name, leave_on_hub=args.leave_on_hub, dry_run=args.dry_run)
            report(moved, done=not args.dry_run)
            return 0
        if action == "disown":
            if not args.target:
                console.print("Usage: interlatch profile disown <folder>")
                return 2
            was, moved = ops.disown(cfg, args.target, dry_run=args.dry_run)
            console.print(f"{args.target} no longer belongs to {was}. Its sessions wait for you to choose: "
                          f"`interlatch profile choose {_quoted(args.target)} <profile|personal>`.", highlight=False)
            report(moved, done=not args.dry_run)
            return 0
        if action == "remove":
            if not args.target:
                console.print("Usage: interlatch profile remove <profile> [--delete-data]")
                return 2
            p, moved = ops.remove(cfg, args.target, delete_data=args.delete_data)
            console.print(f"Removed {p.label}. Its folders and sessions wait for you to choose "
                          "(`interlatch profile waiting`)." + ("" if args.delete_data else
                                                               f" Its folder {p.home} stays: --delete-data removes it."),
                          highlight=False)
            return 0
    except (ops.ProfileError, RegistryError) as exc:
        console.print(str(exc), highlight=False)
        return 1
    return 2


def _quoted(s: str) -> str:
    import shlex

    return shlex.quote(s)


def _why(r, reg, remote) -> str:
    if r.rule == "folder":
        return f"inside {r.detail}, which it owns"
    if r.rule == "chosen":
        return f"you chose it for {r.detail}"
    if r.rule == "held":
        return f"{r.detail} waits for you to choose (`interlatch profile choose`)"
    if r.rule == "remote":
        return f"its repository ({remote}) matches {r.detail}"
    if r.rule == "conflict":
        return f"waits for you: {r.detail}"
    if not reg.profiles:
        return "there are no named profiles"
    return "no profile's folder or repository pattern matches it" + (f" (its remote: {remote})" if remote else "")


def _list(console, rows: list[dict]) -> int:
    for row in rows:
        if row["id"] == "waiting":
            held = sorted(f for f, o in row["choices"].items() if o == "Waiting")
            if row["sessions"] or held:
                console.print(f"\n[bold]Waiting[/] · {row['sessions']} session{'s' * (row['sessions'] != 1)}"
                              " (`interlatch profile waiting`)", highlight=False)
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
        hub = f" · hub {row['hub']}" if row["hub"] else " · no hub"
        ident = "" if row["id"] == "personal" else f" ({row['id']})"
        console.print(f"[bold]{row['label']}[/]{ident}{count}{hub} · port {row['port']}", highlight=False)
        console.print(f"  [dim]{row['home']}[/]", highlight=False)
        for f in row["owns"]:
            console.print(f"  owns {f}", highlight=False)
        for r in row["remotes"]:
            console.print(f"  owns repositories matching {r}", highlight=False)
    if len(rows) == 2:
        console.print("\nNo named profiles. `interlatch profile create <name> --owns <folder>` makes one.")
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
                  "`interlatch profile choose <folder> <profile|personal>`.")
    return 0


def add_profile(sub) -> None:
    s = sub.add_parser("profile", help="named profiles, each with its own hub: create, list, where, own, disown, "
                                       "waiting, choose, remove")
    s.add_argument("action", choices=["create", "list", "where", "own", "disown", "waiting", "choose", "remove"])
    s.add_argument("target", nargs="?", help="the new profile's name (create), a folder, or a profile (remove)")
    s.add_argument("name", nargs="?", help="own: the profile; choose: the profile, or personal")
    s.add_argument("--owns", action="append", metavar="FOLDER", help="create: a folder it owns (repeatable)")
    s.add_argument("--remotes", action="append", metavar="PATTERN",
                   help="create: git remotes it owns, such as github.com/acme-corp/* (repeatable)")
    s.add_argument("--leave-on-hub", action="store_true",
                   help="move sessions even if Personal's hub has them (those copies stay there)")
    s.add_argument("--dry-run", action="store_true", help="say what would move, change nothing")
    s.add_argument("--delete-data", action="store_true", help="remove: delete the profile's folder too")
    s.set_defaults(fn=cmd_profile)
