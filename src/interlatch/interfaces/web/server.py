"""Starting the dashboard: binding its port (or finding a dashboard already running there), and serving until it
is stopped."""

from __future__ import annotations

import errno
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ... import __version__
from ...core.config import Config
from .app import App
from .handler import make_handler


def make_server(cfg: Config, host: str | None = None, port: int | None = None, *,
                any_port: bool = False) -> ThreadingHTTPServer:
    """Bind the dashboard. With any_port, fall back to a free port when the configured one is taken."""
    host = host or cfg.server_host
    port = cfg.server_port if port is None else port
    app = App(cfg)
    try:
        httpd = ThreadingHTTPServer((host, port), BaseHTTPRequestHandler)
    except OSError:
        if not any_port:
            raise
        httpd = ThreadingHTTPServer((host, 0), BaseHTTPRequestHandler)
    # the handler checks Host against the bound port, so it is built once the port is known
    httpd.RequestHandlerClass = make_handler(app, httpd.server_address[1])
    httpd.app = app  # the menu-bar item reads the same status (menubar.py)
    return httpd


def _already_running(cfg: Config, port: int, open_browser: bool) -> None:
    """The port is taken: say by what (usually Interlatch's own launchd agent) and how to get the new code served."""
    import os
    import sys
    from urllib.request import urlopen

    from ...runtime.install import UI_LABEL, active_label, launchd_status

    url = f"http://127.0.0.1:{port}/"
    try:
        with urlopen(url + "api/jobs", timeout=3) as r:
            running = json.load(r).get("version")
    except Exception:
        running = None
    if not running:
        print(f"Port {port} is in use by another program. Use --port to pick another one.", file=sys.stderr)
        raise SystemExit(1)
    if open_browser:
        webbrowser.open(url)
    agent = launchd_status(UI_LABEL).get("loaded")
    print(f"Interlatch {running} is already running at {url}" + (" (the background agent from `interlatch install`)." if agent else "."),
          file=sys.stderr)
    if running != __version__ or agent:
        if agent and sys.platform == "win32":
            restart = r"schtasks /End /TN \Interlatch\Dashboard, then schtasks /Run /TN \Interlatch\Dashboard"
        elif agent:
            restart = f"launchctl kickstart -k gui/{os.getuid()}/{active_label(UI_LABEL)}"
        else:
            restart = "stop that one (Ctrl+C where it runs)"
        print(f"If it still runs older code (after an update), restart it to load this install ({__version__}): {restart}",
              file=sys.stderr)
    print("Or run a second dashboard with --port <n>.", file=sys.stderr)
    raise SystemExit(0 if open_browser else 1)


def serve(cfg: Config, host: str | None = None, port: int | None = None, open_browser: bool = False, *,
          banner: str | None = None, menu_bar: bool | None = None) -> None:
    """`banner`: the line printed once the dashboard listens, instead of its 127.0.0.1 address (container.py prints
    the hub's own: an editor connected to the server forwards any 127.0.0.1 address it sees in a terminal).
    `menu_bar`: the macOS menu-bar item too; None shows it when this is the login item (menubar.wants_menu_bar)."""
    from ...runtime import update

    if sys.platform == "win32" and os.environ.get("INTERLATCH_WINDOWS_TASK"):
        from ...runtime.platforms.windows import QUIT_TEXT, logon_id, quit_stands

        if quit_stands(cfg.home, logon_id()):  # quit from its notification-area icon: not back before the next sign-in
            print(f"Quit from the notification-area icon. {QUIT_TEXT}", flush=True)
            return
    update.RESTARTABLE = True  # this process is only the dashboard, so an update can re-exec it
    try:
        httpd = make_server(cfg, host, port)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        _already_running(cfg, port or cfg.server_port, open_browser)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(banner or f"Interlatch dashboard: {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    from ...runtime.platforms.menubar import run_with_server, wants_menu_bar

    if (wants_menu_bar(cfg) if menu_bar is None else menu_bar) and run_with_server(cfg, httpd, url):
        return
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
