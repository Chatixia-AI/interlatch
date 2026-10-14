"""Settings › Storage: a copy of this archive in Postgres (sharing nothing back; the password goes in, never out)."""

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
