"""Connecting to a Postgres database: the settings file both the hub's team store (team_store.py) and the personal
mirror (mirror.py) keep their connection in, and checking what the dashboard sends for it.

A settings file holds PG* lines (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD, PGSSLMODE), readable by this user
only. The password goes in from the dashboard and never comes back out. The driver (psycopg) is an optional extra.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("interlatch.pg")

PG_KEYS = {"PGHOST": "host", "PGPORT": "port", "PGDATABASE": "dbname", "PGUSER": "user", "PGPASSWORD": "password",
           "PGSSLMODE": "sslmode"}
SSLMODES = ("require", "verify-ca", "verify-full", "prefer", "disable")
SETTINGS = ("host", "port", "dbname", "user", "sslmode")  # what the dashboard shows and edits; the password only goes in


class PgError(Exception):
    pass


def read_env(path: Path) -> dict[str, str]:
    """PG* lines of an env file: KEY=value, a value optionally in one pair of quotes; # starts a comment line."""
    raw: dict[str, str] = {}
    if path.stat().st_mode & 0o077:
        log.warning("%s can be read by other users of this computer; chmod 600 it", path)
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            raw[k.strip()] = v
    return raw


def params_from(path: Path, *, environ: bool = True) -> dict:
    """psycopg connect() arguments from the env file at `path`, else (`environ`) the PG* environment; {} when neither
    names a server and a database. Never logged: it holds a password."""
    raw = read_env(path) if path.exists() else {k: v for k, v in os.environ.items() if k in PG_KEYS} if environ else {}
    params = {PG_KEYS[k]: v for k, v in raw.items() if k in PG_KEYS and v}
    if not params.get("host") or not params.get("dbname"):
        return {}
    params.setdefault("sslmode", "require")
    return params


def shown(params: dict | None) -> dict | None:
    """The connection as the dashboard shows it: everything but the password, which is only said to be there."""
    if not params:
        return None
    return {**{k: params.get(k) or "" for k in SETTINGS}, "password_set": bool(params.get("password"))}


def check_settings(values: dict, saved: dict | None, error: type[Exception] = PgError) -> dict:
    """psycopg connect() arguments from what the dashboard sent: a password left empty keeps the saved one."""
    params = {}
    for k in (*SETTINGS, "password"):
        v = values.get(k)
        v = "" if v is None else str(v)
        if "\n" in v or "\r" in v or "\0" in v:
            raise error(f"{k} can't contain a line break")
        if k != "password":
            v = v.strip()
        if v:
            params[k] = v
    for k in ("host", "dbname", "user"):
        if not params.get(k):
            raise error({"host": "the database server's address is missing", "dbname": "the database name is missing",
                         "user": "the user name is missing"}[k])
    port = params.get("port", "5432")
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise error("the port must be a number from 1 to 65535")
    params["port"] = port
    params.setdefault("sslmode", "require")
    if params["sslmode"] not in SSLMODES:
        raise error(f"SSL mode must be one of: {', '.join(SSLMODES)}")
    if "password" not in params:
        if not saved or not saved.get("password"):
            raise error("the password is missing")
        params["password"] = saved["password"]
    return params


def write_settings(path: Path, params: dict, header: str) -> None:
    """Write an env file (readable by this user only), replacing it in one step."""
    def quoted(v: str) -> str:  # a value that reading would change keeps its exact text inside one pair of quotes
        return f"'{v}'" if v != v.strip() or (len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'") else v

    keys = {v: k for k, v in PG_KEYS.items()}
    lines = [f"# {header}"]
    lines += [f"{keys[k]}={quoted(str(params[k]))}" for k in (*SETTINGS[:4], "password", "sslmode") if params.get(k)]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def driver_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("psycopg") is not None


def where(params: dict) -> str:
    return f"{params.get('dbname')} on {params.get('host')}"


def first_line(exc: BaseException) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0]


def iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z") if hasattr(value, "isoformat") else str(value)
