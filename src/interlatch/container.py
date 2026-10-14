"""A hub in a container (docker/): `interlatch container` sets it up from INTERLATCH_* variables (CHRONICLE_*, their
names before the rename, still count), then serves the dashboard and runs what `interlatch install` would schedule
with launchd or systemd (sync and work every 15 minutes).

Every start writes the variables that are set (not empty) into config.toml, so they win over it; one left unset
leaves config.toml as it is. A hub's first start also makes it take knowledge only (summaries and project lessons, not transcripts), turns the shared token off
(computers join with invites) and adds its first admin. Until a hub has people, anyone who reaches its dashboard is an
admin (server.py `_viewer`), so a container never serves one without them.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
from collections.abc import Mapping
from urllib.parse import urlparse

from .config import STORES, Config, load_config, set_config_value
from .config import env as _env

log = logging.getLogger("interlatch.container")

DEFAULT_PORT = 11524
WORK_MINUTES = 15


class SetupError(Exception):
    """A variable is missing or wrong: the container stops with this message instead of serving."""


def _list(raw: str | None) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def var(env: Mapping[str, str], name: str) -> str:
    """INTERLATCH_<name> from `env`, else CHRONICLE_<name>; "" when neither is set."""
    return _env(name, "", environ=env) or ""


def _set(cfg: Config, section: str, key: str, value) -> None:
    set_config_value(cfg, section, key, json.dumps(value))  # JSON strings, numbers, booleans and lists are TOML too


def hub_url(env: Mapping[str, str]) -> tuple[str, str]:
    """INTERLATCH_HUB_URL as (address, host name): where computers and browsers reach this hub."""
    url = var(env, "HUB_URL").strip().rstrip("/")
    if not url:
        raise SetupError("INTERLATCH_HUB_URL is not set: the address computers and browsers reach this hub at, "
                         "e.g. https://interlatch.example.com")
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.path or parts.query or parts.fragment:
        raise SetupError(f"INTERLATCH_HUB_URL must be an address such as https://interlatch.example.com, not {url!r}")
    return url, parts.hostname.lower()


def configure(cfg: Config, env: Mapping[str, str]) -> tuple[Config, bool]:
    """Write the INTERLATCH_* variables into config.toml and make this computer a hub. Returns the reloaded config,
    and whether this was the hub's first start."""
    from . import hub

    url, host = hub_url(env)
    port = (var(env, "PORT") or str(DEFAULT_PORT)).strip()
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise SetupError(f"INTERLATCH_PORT must be a port number, not {port!r}")
    store = var(env, "TEAM_STORE").strip().lower()
    if store not in STORES:
        raise SetupError(f"INTERLATCH_TEAM_STORE must be \"postgres\" or empty, not {store!r}")
    if cfg.is_spoke:
        raise SetupError(f"this Interlatch sends to the hub at {cfg.hub_url}; a container is a hub itself")

    first = not hub.read_token(cfg)
    _set(cfg, "server", "host", (var(env, "HOST") or "0.0.0.0").strip())
    _set(cfg, "server", "port", int(port))
    # nothing reaches a container's dashboard from "this computer": every request came through a proxy or the network,
    # so none may count as made at the hub (always an admin). Admins sign in, or run `interlatch hub` in the container.
    _set(cfg, "server", "behind_proxy", True)
    more = [h.lower() for h in _list(var(env, "ALLOWED_HOSTS"))]
    _set(cfg, "server", "allowed_hosts", list(dict.fromkeys([host, *more])))
    if proxies := _list(var(env, "TRUSTED_PROXIES")):  # a proxy elsewhere whose X-Forwarded-Proto counts
        _set(cfg, "server", "trusted_proxies", proxies)
    _set(cfg, "hub", "address", url)
    _set(cfg, "hub", "dedicated", True)  # a server for the team: no sessions of its own, projects set up by name
    if name := var(env, "HUB_NAME").strip():
        _set(cfg, "hub", "name", name)
    if store:  # the connection comes from the PG* variables (team_store.connection_params)
        _set(cfg, "hub", "store", store)
    if first:  # defaults an admin may change later, in the dashboard or with `interlatch config set`
        _set(cfg, "hub", "accept", "knowledge")  # every computer analyzes its own sessions; the hub needs no model
        _set(cfg, "hub", "shared_token", False)  # computers join with an invite of their own
        if not name and not cfg.hub_name:  # not the container's random host name
            _set(cfg, "hub", "name", "Interlatch hub")
    cfg = load_config(cfg.home)
    cfg.ensure_dirs()
    if first:
        hub.new_token(cfg)
    return cfg, first


def first_admin(cfg: Config, conn, env: Mapping[str, str]) -> list[str]:
    """Add INTERLATCH_ADMIN_EMAIL as the hub's first admin when it has no people yet. Returns what to print: their
    invite, which is shown only now."""
    from . import hub, people

    if people.has_people(conn):
        return []
    email = var(env, "ADMIN_EMAIL").strip()
    if not email:
        raise SetupError("INTERLATCH_ADMIN_EMAIL is not set. Until a hub has people, anyone who reaches its dashboard "
                         "is an admin, so this container adds its first admin before it serves anything: set "
                         "INTERLATCH_ADMIN_EMAIL (and INTERLATCH_ADMIN_NAME), then start it again.")
    name = var(env, "ADMIN_NAME").strip() or email.split("@", 1)[0]
    try:
        person = people.add(conn, name, email, "admin")
        code = people.invite(conn, person["id"])
    except people.PeopleError as exc:
        raise SetupError(f"can't add the first admin {email}: {exc.shown()}") from None
    address = cfg.hub_address
    return [
        f"Added {person['name']} ({person['email']}) as this hub's admin. The invite works once, for "
        f"{people.INVITE_DAYS} days:",
        "",
        f"  In a browser, to open the hub's dashboard:  {hub.invite_link(address, code)}",
        f"  Or on their computer, to join it:           {hub.invite_command(address, code)}",
        "",
        f"It is not shown again. A new one: docker compose exec hub interlatch hub invite {person['email']}",
    ]


def _work(home, minutes: float, stop: threading.Event) -> None:
    """What the launchd agent or systemd timer runs elsewhere: read what computers sent, then analyze, synthesize and
    export. On a hub that takes knowledge only, with no model set up, this is mostly the Markdown export."""
    from .db import connect
    from .ingest import sync
    from .worker import run_worker

    while not stop.wait(minutes * 60):
        try:
            cfg = load_config(home)
            conn = connect(cfg.db_path)
            try:
                report = sync(cfg, conn)
            finally:
                conn.rollback()
                conn.close()
            work = run_worker(cfg)
            log.info("container: sync %s | work %s", report.summary(), work.summary())
        except Exception:  # the next round tries again; the dashboard keeps serving
            log.exception("background sync and work failed")


def main(env: Mapping[str, str] | None = None) -> int:
    from . import hub
    from .db import connect
    from .server import serve
    from .util import setup_logging

    env = os.environ if env is None else env
    cfg = load_config()
    setup_logging(cfg.logs_dir, console=True)
    try:
        cfg, first = configure(cfg, env)
        conn = connect(cfg.db_path)
        try:
            hub.register_local(conn, cfg)
            conn.commit()
            lines = first_admin(cfg, conn, env)
        finally:
            conn.close()
    except SetupError as exc:
        print(f"interlatch: {exc}", file=sys.stderr, flush=True)
        return 2
    for line in lines:
        print(line, flush=True)
    if first:
        print("This hub takes knowledge only: each computer analyzes its own sessions and sends summaries and "
              "project lessons, never transcripts.", flush=True)
    if not cfg.hub_address.startswith("https://"):
        print(f"Warning: {cfg.hub_address} is not https. Invite codes, tokens and sign-in cookies cross the network "
              "in clear text; put HTTPS in front (docker/compose.yaml runs Caddy for it).", file=sys.stderr, flush=True)

    raw = (var(env, "WORK_MINUTES") or str(WORK_MINUTES)).strip()
    try:
        minutes = float(raw)
    except ValueError:
        minutes = WORK_MINUTES
    stop = threading.Event()
    if minutes > 0:
        threading.Thread(target=_work, args=(cfg.home, minutes, stop), name="sync-and-work", daemon=True).start()

    def on_term(signum, frame):  # PID 1 in a container has no default SIGTERM action: `docker stop` sends one
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    try:  # the hub's address, never 127.0.0.1: VS Code on the server would forward that to the viewer's own port
        serve(cfg, banner=f"Interlatch hub is up: {cfg.hub_address}")
    finally:
        stop.set()
    return 0
