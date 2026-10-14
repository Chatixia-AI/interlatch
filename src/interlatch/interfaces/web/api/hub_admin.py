"""Running a hub: its settings, the team store (the password goes in, never out), and its people and invites."""

from __future__ import annotations

import json
from urllib.parse import urlparse

from ....core.config import env
from ....core.i18n import tr
from ..context import Context


def hub_settings(ctx: Context) -> dict:
    """This hub's name and address, what it takes, and which of them a container's variables set (each start
    writes them again, so the dashboard can't change them)."""
    from ....core.machine import local_machine
    from ....sharing.identity import read_token

    if ctx.cfg.is_spoke or not read_token(ctx.cfg):
        return {"error": tr("this computer is not a hub")}
    container = bool(env("CONTAINER"))
    return {"name": ctx.cfg.hub_name, "default_name": local_machine(ctx.cfg)["name"],
            "address": ctx.cfg.hub_address, "accept": ctx.cfg.hub_accept, "store": ctx.cfg.hub_store,
            "container": container, "dedicated": ctx.cfg.hub_dedicated,
            "managed": {"address": container, "name": container and bool((env("HUB_NAME") or "").strip())},
            "home": str(ctx.cfg.home), "db": str(ctx.cfg.db_path)}


def action_hub_settings(ctx: Context, body: dict, by: dict | None) -> tuple[dict, int]:
    """Rename this hub, or change the address computers and browsers reach it at (invites and sign-in links
    use it). A new address's host name is added to [server] allowed_hosts, as `interlatch hub enable --url` does."""
    from ....core.config import load_config, set_config_value
    from ....sharing import people
    from ....sharing.identity import read_token

    if ctx.cfg.is_spoke or not read_token(ctx.cfg):
        return {"error": tr("this computer is not a hub")}, 400
    managed = hub_settings(ctx)["managed"]
    changed: dict = {}
    if "name" in body:
        if managed["name"]:
            return {"error": tr("INTERLATCH_HUB_NAME in the hub's .env sets its name: change it there.")}, 400
        name = " ".join("".join(c for c in str(body.get("name") or "") if c.isprintable() or c.isspace()).split())
        if len(name) > 80:
            return {"error": tr("A hub's name is up to 80 characters.")}, 400
        if not name and env("CONTAINER"):  # else it would be the container's random host name
            return {"error": tr("Give the hub a name.")}, 400
        changed["name"] = name
    if "address" in body:
        if managed["address"]:
            return {"error": tr("INTERLATCH_HUB_URL in the hub's .env sets its address: change it there.")}, 400
        address = str(body.get("address") or "").strip().rstrip("/")
        parts = urlparse(address)
        if address and (parts.scheme not in ("http", "https") or not parts.hostname or parts.path or parts.query
                        or parts.fragment or parts.username or parts.password):
            return {"error": tr("An address is like https://interlatch.example.com: http or https and a host name, "
                                "with a port if needed.")}, 400
        changed["address"] = address
    if not changed:
        return {"error": tr("Nothing to change.")}, 400
    if "name" in changed:
        set_config_value(ctx.cfg, "hub", "name", json.dumps(changed["name"]))
    if "address" in changed:
        set_config_value(ctx.cfg, "hub", "address", json.dumps(changed["address"]))
        host = (urlparse(changed["address"]).hostname or "").lower()
        if host and host not in ("127.0.0.1", "localhost", "::1") and host not in ctx.cfg.server_allowed_hosts:
            set_config_value(ctx.cfg, "server", "allowed_hosts", json.dumps([*ctx.cfg.server_allowed_hosts, host]))
    ctx.cfg = load_config(ctx.cfg.home)
    people.audit(ctx.conn, people.actor_of(by), "settings", None, **changed)
    ctx.conn.commit()
    return {"ok": True, **hub_settings(ctx)}, 200


def team_store_info(ctx: Context) -> dict:
    from ....storage import team as team_store

    return {"enabled": ctx.cfg.hub_store == "postgres", "driver": team_store.driver_available(),
            "settings": team_store.read_settings(ctx.cfg), "file": str(team_store.env_path(ctx.cfg)),
            "sslmodes": list(team_store.SSLMODES)}


def team_store_status(ctx: Context) -> dict:
    """What the store holds; asked for after the page shows, since the database may be far away."""
    from ....storage import team as team_store

    if ctx.cfg.hub_store != "postgres" or ctx.cfg.is_spoke:
        return {"enabled": False}
    try:
        return {"enabled": True, **team_store.get(ctx.cfg).status()}
    except team_store.TeamStoreError as exc:
        return {"enabled": True, "error": str(exc)}


def _store_params(ctx: Context, body: dict) -> dict:
    from ....storage import team as team_store

    try:
        saved = team_store.connection_params(ctx.cfg)
    except (team_store.TeamStoreError, OSError):
        saved = None
    return team_store.check_settings(body, saved)


def action_team_store_test(ctx: Context, body: dict) -> dict:
    from ....storage import team as team_store

    try:
        return {"ok": True, **team_store.probe(_store_params(ctx, body))}
    except team_store.TeamStoreError as exc:
        return {"ok": False, "error": tr(str(exc))}


def action_team_store_save(ctx: Context, body: dict) -> dict:
    """Turn the team store on with these settings (tested first: nothing is saved if they don't work), or off.
    Off keeps the settings, so turning it on again needs no password."""
    from ....core.config import load_config, set_config_value
    from ....storage import team as team_store

    if ctx.cfg.is_spoke:
        return {"error": tr("this computer sends to a hub: the team store is set up on the hub")}
    if not body.get("enabled"):
        set_config_value(ctx.cfg, "hub", "store", '""')
        ctx.cfg = load_config(ctx.cfg.home)
        return {"ok": True, "store": team_store_info(ctx)}
    try:
        params = _store_params(ctx, body)
        found = team_store.probe(params)
        if not found["steps"] and not found["can_create"]:
            raise team_store.TeamStoreError(f"user {params['user']} can't create the team's tables in {found['where']}")
    except team_store.TeamStoreError as exc:
        return {"error": tr(str(exc))}
    team_store.write_settings(ctx.cfg, params)
    set_config_value(ctx.cfg, "hub", "store", '"postgres"')
    ctx.cfg = load_config(ctx.cfg.home)
    return {"ok": True, "store": team_store_info(ctx), "status": team_store_status(ctx)}


def people_info(ctx: Context) -> dict:
    from ....sharing import people
    from ....sharing.projects import hub_projects

    return {"people": people.listing(ctx.conn), "shared_token": ctx.cfg.hub_shared_token,
            "address": ctx.cfg.hub_address or None, "audit": people.audit_log(ctx.conn, 50),
            "roles": list(people.ROLES), "projects": hub_projects(ctx.conn)}


def _invite(ctx: Context, person: dict, by: dict | None, address: str) -> dict:
    """A new invite for `person`, with the command and the link to pass on. The code is shown this once."""
    from ....sharing import people
    from ....sharing.protocol import invite_command, invite_link

    code = people.invite(ctx.conn, person["id"], by=by)
    expires = ctx.conn.execute("SELECT expires_at FROM people_codes WHERE person_id = ? AND kind = 'invite' "
                                "ORDER BY rowid DESC LIMIT 1", (person["id"],)).fetchone()[0]
    out = {"person": person, "code": code, "expires_at": expires,
           "join": invite_command(address, code), "link": invite_link(address, code)}
    if not ctx.cfg.hub_address:  # guessed from this page's address, which others may not reach
        out["note"] = tr("this hub has no address set ([hub] address), so these use the address this page was "
                         "opened at; set it with `interlatch hub enable --url`")
    return out


def _projects_body(ctx: Context, body: dict) -> list[str] | None:
    """`projects` from the dashboard: "all" for every project, else a list of this hub's project paths."""
    from ....sharing import people
    from ....sharing.projects import hub_projects

    raw = body.get("projects")
    if raw == "all":
        return None
    if not isinstance(raw, list):
        raise people.PeopleError("say which projects they see, or every project")
    known = {p["path"] for p in hub_projects(ctx.conn)}
    unknown = [x for x in raw if x not in known]
    if unknown:
        raise people.PeopleError("{project} is not a project on this hub", project=str(unknown[0]))
    return people.clean_projects(raw)


def action_people(ctx: Context, verb: str, body: dict, by: dict | None, address: str) -> tuple[dict, int]:
    """add, invite, role, access, remove, revoke or shared-token, done by `by` (None: at the hub itself) for the
    audit."""
    from ....core.config import load_config, set_config_value
    from ....sharing import people

    try:
        if verb == "add":
            role = str(body.get("role") or "member")
            projects = [] if role == "admin" else _projects_body(ctx, body)  # an admin sees all; if made a member, nothing yet
            person = people.add(ctx.conn, str(body.get("name") or ""), str(body.get("email") or ""), role, by=by,
                                projects=projects)
            return _invite(ctx, person, by, address), 200
        if verb == "shared-token":
            on = bool(body.get("on"))
            set_config_value(ctx.cfg, "hub", "shared_token", "true" if on else "false")
            ctx.cfg = load_config(ctx.cfg.home)
            people.audit(ctx.conn, people.actor_of(by), "shared-token", None, on=on)
            ctx.conn.commit()
            return {"ok": True, **people_info(ctx)}, 200
        try:
            pid = int(body.get("id"))
        except (TypeError, ValueError):
            return {"error": tr("no such person")}, 400
        if verb == "invite":
            person = people.get(ctx.conn, pid)
            if not person or person["removed_at"]:
                return {"error": tr("no such person")}, 400
            return _invite(ctx, person, by, address), 200
        if verb == "role":
            people.set_role(ctx.conn, pid, str(body.get("role") or ""), by=by)
        elif verb == "access":
            people.set_projects(ctx.conn, pid, _projects_body(ctx, body), by=by)
        elif verb == "remove":
            people.remove(ctx.conn, pid, by=by)
        elif verb == "revoke":
            if not people.revoke(ctx.conn, pid, str(body.get("token") or ""), by=by):
                return {"error": tr("no such computer or browser session")}, 400
        else:
            return {"error": tr("not found")}, 404
    except people.PeopleError as exc:
        return {"error": exc.shown()}, 400
    return {"ok": True, **people_info(ctx)}, 200
