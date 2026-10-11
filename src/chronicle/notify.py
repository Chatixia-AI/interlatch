"""Desktop notification of a new Interlatch release (opt-in: `[updates] notify`).

The background sync calls release_check() on every run. It asks pypi.org at most once a day (update.check_due) and
notifies once per release, so a notification that was ignored or dismissed does not come back. macOS posts through
osascript, Linux through notify-send; neither needs anything installed.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess

from .config import Config

log = logging.getLogger("interlatch.notify")

NOTIFIED_KEY = "update_notified"  # kv: the release the last notification was about
UPGRADE = {"uv": "uv tool upgrade interlatch", "pipx": "pipx upgrade interlatch",
           "pip": "pip install -U interlatch"}


def _applescript(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def post(title: str, message: str) -> bool:
    """Show a desktop notification; False when this system has no way to (or it failed)."""
    if platform.system() == "Darwin":
        cmd = ["osascript", "-e", f"display notification {_applescript(message)} with title {_applescript(title)}"]
    elif shutil.which("notify-send"):
        cmd = ["notify-send", "--app-name=Interlatch", title, message]
    else:
        return False
    try:
        return subprocess.run(cmd, capture_output=True, timeout=10, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def how_to_update(cfg: Config, kind: str) -> str:
    if kind == "app":
        return "Open Interlatch › Status › Updates to download it."
    where = f"the dashboard's Status › Updates (http://127.0.0.1:{cfg.server_port}/#/status?focus=updates)"
    return f"Update from {where}, or run: {_upgrade(kind)}" if kind in UPGRADE else f"Update from {where}."


def _upgrade(kind: str) -> str:
    """The command that updates this install; for one of the agents-chronicle package, the move to interlatch."""
    from .update import check, install_method

    return (check()["command"] if install_method().get("move") else None) or UPGRADE[kind]


def release_check(cfg: Config, conn) -> str | None:
    """Notify about a release newer than this copy, once per release. Returns the version notified about."""
    from . import update
    from .db import kv_get, kv_set

    if not cfg.update_notify or not update.compares_online():
        return None
    update.recall(conn)  # the dashboard's checks count too
    if update.check_due():
        update.fetch_latest()
        update.remember(conn)
    offer = update.available()
    if not offer or kv_get(conn, NOTIFIED_KEY) == offer["key"]:
        return None
    if not post(f"Interlatch {offer['to']} is available", how_to_update(cfg, update.install_method()["kind"])):
        log.info("no way to show a notification for Interlatch %s here", offer["to"])
        return None
    kv_set(conn, NOTIFIED_KEY, offer["key"])
    conn.commit()
    log.info("notified about Interlatch %s", offer["to"])
    return offer["to"]


def release_check_safely(cfg: Config) -> None:
    """For the background sync: never let a notification problem fail the sync."""
    from .db import connect

    if not cfg.update_notify:
        return
    try:
        conn = connect(cfg.db_path)
        try:
            release_check(cfg, conn)
        finally:
            conn.close()
    except Exception:
        log.exception("release notification check failed")
