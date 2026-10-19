"""Who may talk to a hub: the hub's token, this computer's key (sent with every hello), and checking a request's
token or a person's session."""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from ..core.config import Config

TOKEN_FILE = "hub-token"


def machine_key(cfg: Config) -> str:
    """This computer's key, made once and kept in <home>/machine-key (readable by you only). It goes to the hub with
    every hello; the hub keeps the first one it sees, and an invite that claims this computer's id later must show it
    (people.may_claim). A file of its own, not machine.json: that one is shown and edited (its name)."""
    path = cfg.home / "machine-key"
    try:
        key = path.read_text().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(secrets.token_urlsafe(32) + "\n")
    os.chmod(tmp, 0o600)
    try:
        os.link(tmp, path)  # only if there is none yet: two processes starting at once end up with one key
    except FileExistsError:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    return path.read_text().strip()


def token_path(cfg: Config) -> Path:
    return cfg.home / TOKEN_FILE


def read_token(cfg: Config) -> str | None:
    try:
        token = token_path(cfg).read_text().strip()
    except OSError:
        return None
    return token or None


def write_token(cfg: Config, token: str) -> None:
    path = token_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name(path.name + ".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token + "\n")
    os.replace(path.with_name(path.name + ".tmp"), path)


def new_token(cfg: Config) -> str:
    token = secrets.token_urlsafe(32)
    write_token(cfg, token)
    return token


def token_ok(cfg: Config, authorization: str | None) -> bool:
    from .membership import left_token

    expected = read_token(cfg)
    if not expected or cfg.is_spoke or not authorization or not authorization.startswith("Bearer "):
        return False
    if left_token(cfg):  # the token of a hub this computer sent to, still on file: never this computer's own
        return False
    return hmac.compare_digest(authorization[7:].strip().encode(), expected.encode())


def authorize(cfg: Config, conn, authorization: str | None, machine_id: str | None = None) -> tuple[bool, dict | None]:
    """Who may call /api/hub/*: (ok, person). The hub's shared token is let in (person None) while the hub has no
    people, or while `[hub] shared_token` stays on; a person's own computer token (from `hub join --code`) is let in
    as that person, from the computer it was issued to. Nothing is let in once this computer stopped being a hub."""
    from . import people

    if not read_token(cfg) or cfg.is_spoke or not authorization or not authorization.startswith("Bearer "):
        return False, None
    if token_ok(cfg, authorization):
        if not (cfg.hub_shared_token or not people.has_people(conn)):
            return False, None
        if machine_id and people.computer_bound(conn, machine_id):
            # the shared token names no computer: it may not act as one that joined as a person (send, refile or take
            # back that computer's sessions); that computer sends with its own token
            return False, None
        return True, None
    person = people.computer_person(conn, authorization[7:].strip(), machine_id or None)
    return (True, person) if person else (False, None)
