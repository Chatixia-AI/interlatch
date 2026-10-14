"""What several commands share: the config and the archive, a console to print to, waiting for the dashboard's
port, the analysis backend's name, and pushing to the hub."""

from __future__ import annotations

import sys


def load_cfg():
    """config.toml as loaded, with Interlatch's folders made."""
    from ...core.config import load_config

    cfg = load_config()
    cfg.ensure_dirs()
    return cfg


def open_archive(cfg):
    """The archive, writable (brought up to the current schema)."""
    from ...storage.sqlite import connect

    return connect(cfg.db_path)


def new_console():
    """A rich console for a command's output."""
    from rich.console import Console

    return Console()


def wait_for_port(port: int, seconds: float = 8.0) -> bool:
    """Whether something on this computer listens on `port` within `seconds` (a dashboard just started)."""
    import socket
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def analyzer_label(cfg) -> str:
    """What analyzes sessions ([analysis] backend), by the name people know it."""
    from ...analysis.runners.factory import make_runner

    return make_runner(cfg).label


def escape_markup(text: str) -> str:
    """`text` printed as it is, not read as rich markup."""
    from rich.markup import escape

    return escape(text or "")


def push_now(cfg, *, quiet: bool = False) -> int:
    """Send the hub this computer's new sessions now, with progress on a terminal; the exit code."""
    from ...sharing.protocol import HubError
    from ...sharing.push import push

    live = not quiet and sys.stdout.isatty()

    def progress(message: str) -> None:
        if live:
            print(f"\r\033[K  {message[:110]}", end="", flush=True)

    try:
        report = push(cfg, progress=progress)
    except HubError as exc:
        if live:
            print("\r\033[K", end="")
        print(f"push: {exc}", file=sys.stderr)
        return 1
    if live:
        print("\r\033[K", end="")
    if not quiet:
        print(f"push: {report.summary()}")
    for err in report.errors[:10]:
        print(f"  ! {err}", file=sys.stderr)
    return 0 if not report.errors else 1
