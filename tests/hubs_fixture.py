"""Tests' shorthand for several hubs (application.profiles): another hub, and a group of folders connected to it."""

from interlatch.application import profiles as ops
from interlatch.projects import groups
from interlatch.storage.sqlite import connect


def group(cfg, name: str, folders: list[str]) -> int:
    conn = connect(cfg.db_path)
    try:
        return groups.save(conn, None, name, list(folders))
    finally:
        conn.close()


def hub_with(cfg, name: str, folders: list[str], *, leave_on_hub: bool = False, dry_run: bool = False):
    """Add the hub `name` (unless it exists) and connect a group `<name> work` of `folders` to it: (hub, moved)."""
    reg = cfg.profiles
    p = reg.find(name) if reg else None
    p = p or ops.add_hub(cfg, name)
    gid = group(cfg, f"{name} work", folders)
    return p, ops.connect(cfg, gid, name, leave_on_hub=leave_on_hub, dry_run=dry_run)
