"""Settings › Storage: the space Interlatch uses and the folder older sessions go to, and a copy of this archive in
Postgres (sharing nothing back; the password goes in, never out)."""

from __future__ import annotations

import json

from ....core.i18n import tr
from ..context import Context


def mirror_info(ctx: Context) -> dict:
    from ....core.config import MIRROR_INCLUDES
    from ....storage import mirror
    from ....storage import postgres as pg

    return {"enabled": mirror.enabled(ctx.cfg), "include": ctx.cfg.mirror_include, "schema": ctx.cfg.mirror_schema,
            "includes": list(MIRROR_INCLUDES), "driver": pg.driver_available(), "install": mirror.INSTALL,
            "settings": mirror.read_settings(ctx.cfg), "file": str(mirror.env_path(ctx.cfg)),
            "sslmodes": list(pg.SSLMODES), "last": mirror.last(ctx.cfg),
            "running": ctx.jobs.snapshot().get("mirror", {}).get("state") == "running"}


def mirror_status(ctx: Context) -> dict:
    """What the mirror holds; asked for after the page shows, since the database may be far away."""
    from ....storage import mirror

    if not mirror.enabled(ctx.cfg):
        return {"enabled": False}
    try:
        return {"enabled": True, **mirror.status(ctx.cfg)}
    except mirror.MirrorError as exc:
        return {"enabled": True, "error": tr(str(exc))}


def _mirror_params(ctx: Context, body: dict) -> dict:
    from ....storage import mirror

    try:
        saved = mirror.connection_params(ctx.cfg)
    except (mirror.MirrorError, OSError):
        saved = None
    return mirror.check_settings(body, saved)


def _mirror_schema(ctx: Context, body: dict) -> str:
    from ....storage import mirror

    schema = str(body.get("schema") or ctx.cfg.mirror_schema).strip()
    if not (schema.isidentifier() and schema.isascii()):
        raise mirror.MirrorError("the schema name can have only letters, digits and _, and can't start with a digit")
    return schema


def _mirror_check(ctx: Context, params: dict, schema: str) -> dict:
    """probe(), plus whether this computer may write there: a schema no other computer's mirror is in, and a user
    that can create its tables."""
    from ....core.machine import local_machine
    from ....storage import mirror

    found = mirror.probe(params, schema)
    owner = found["owner"]
    found["mine"] = bool(owner and owner["id"] == local_machine(ctx.cfg)["id"])
    if owner and not found["mine"]:
        raise mirror.MirrorError(f"schema {schema} holds the mirror of another computer ({owner['name'] or owner['id']}); "
                                 "pick a schema of this computer's own")
    if not owner and not found["can_create"]:
        raise mirror.MirrorError(f"user {params['user']} can't create the mirror's tables in {found['where']}")
    return found


def action_mirror_test(ctx: Context, body: dict) -> dict:
    from ....storage import mirror

    try:
        return {"ok": True, **_mirror_check(ctx, _mirror_params(ctx, body), _mirror_schema(ctx, body))}
    except mirror.MirrorError as exc:
        return {"ok": False, "error": tr(str(exc))}


def action_mirror_save(ctx: Context, body: dict) -> dict:
    """Turn the mirror on with these settings (tested first: nothing is saved if they don't work) and write it
    now, or off. Off keeps the settings, so turning it on again needs no password, and leaves the copy as it is."""
    from ....core.config import MIRROR_INCLUDES, load_config, set_config_value
    from ....storage import mirror

    if not body.get("enabled"):
        set_config_value(ctx.cfg, "mirror", "to", '""')
        ctx.cfg = load_config(ctx.cfg.home)
        return {"ok": True, "mirror": mirror_info(ctx)}
    include = str(body.get("include") or ctx.cfg.mirror_include)
    if include not in MIRROR_INCLUDES:
        return {"error": tr("what the mirror holds must be \"knowledge\" or \"everything\"")}
    try:
        params, schema = _mirror_params(ctx, body), _mirror_schema(ctx, body)
        _mirror_check(ctx, params, schema)
    except mirror.MirrorError as exc:
        return {"error": tr(str(exc))}
    mirror.write_settings(ctx.cfg, params)
    for key, value in (("include", include), ("schema", schema), ("to", "postgres")):
        set_config_value(ctx.cfg, "mirror", key, json.dumps(value))
    ctx.cfg = load_config(ctx.cfg.home)
    return {"ok": True, "mirror": mirror_info(ctx), "started": action_mirror_sync(ctx)}


def action_mirror_sync(ctx: Context) -> bool:
    from ....storage import mirror

    cfg = ctx.cfg

    def job(progress):
        res = mirror.sync(cfg, progress=progress)
        if res.get("error"):
            raise RuntimeError(res["error"])
        return f"mirror: {res.summary()}"

    return mirror.enabled(cfg) and ctx.jobs.start("mirror", job)


def space_info(ctx: Context) -> dict:
    """What Interlatch's folder holds, part by part, and the [storage] settings."""
    from ....ingestion import space

    return {**space.usage(ctx.cfg, ctx.conn), "profile": bool(ctx.cfg.archive_home),
            "running": ctx.jobs.snapshot().get("storage", {}).get("state") == "running"}


def action_space_save(ctx: Context, body: dict) -> dict:
    """Save [storage]: the folder for older sessions (checked first), after how many days, and the size to warn at."""
    from ....core.config import load_config, set_config_value
    from ....ingestion import space

    cfg = ctx.cfg
    folder = str(body.get("older_dir") or "").strip()
    try:
        days, warn = int(body.get("older_after_days") or cfg.storage_older_days), float(body.get("warn_gb") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": tr("not saved: {keys}", keys="older_after_days, warn_gb")}
    if days < 1 or warn < 0:
        return {"ok": False, "error": tr("not saved: {keys}", keys="older_after_days, warn_gb")}
    try:
        path = space.check_older_dir(cfg, folder) if folder else None
    except space.StorageError as exc:
        return {"ok": False, "error": tr(str(exc))}
    old = cfg.storage_older_dir
    if old and path != old and space.usage(cfg, ctx.conn)["older"]["sessions"]:
        return {"ok": False, "error": tr("sessions are kept in {folder}; bring them back before changing the folder",
                                         folder=str(old))}
    set_config_value(cfg, "storage", "older_dir", json.dumps(str(path) if path else ""))
    set_config_value(cfg, "storage", "older_after_days", str(days))
    set_config_value(cfg, "storage", "warn_gb", f"{warn:g}")
    ctx.cfg = load_config(cfg.home)
    return {"ok": True, "space": space_info(ctx)}


def action_space_move(ctx: Context, back: bool) -> bool:
    """Move older sessions to their folder now (no size limit, unlike a background run), or bring them all back."""
    from ....ingestion import space

    cfg = ctx.cfg

    def job(progress):
        res = (space.bring_back if back else space.move_older)(cfg, progress=progress, wait=60)
        if res["errors"]:
            raise RuntimeError(f"{space.summary(res)}: {res['errors'][0]}")
        return f"older sessions: {space.summary(res)}"

    return bool(cfg.storage_older_dir) and ctx.jobs.start("storage", job)
