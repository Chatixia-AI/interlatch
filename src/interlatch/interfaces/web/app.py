"""Local dashboard: JSON API + static single-page app (stdlib only, binds to localhost).

Besides 127.0.0.1 and localhost it answers only to the names in `[server] allowed_hosts` (Tailscale Serve's, set by
`interlatch tailnet on`), and there only to the Tailscale logins in `[server] allowed_users`. On a hub, /api/hub/* takes
other computers' session files, authenticated by the hub's token or a person's computer token instead (hub.py).

Once a hub has people (people.py), its API answers only someone signed in: at the hub computer itself (an admin),
through a company sign-in proxy that names them in `[server] auth_header`, or with a session cookie from an invite or
sign-in link (/signin). Everyone signed in may look; only admins change things. A hub without people answers as before.
"""

from __future__ import annotations

import logging
import threading

from ...core.config import Config
from ...storage.sqlite import connect
from .context import Context, DashboardState
from .jobs import Jobs

log = logging.getLogger("interlatch.server")


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.local = threading.local()
        self.jobs = Jobs()
        self.state = DashboardState()
        self._cfg_sig = self._config_sig()
        from ...sharing.receiver import IngestTrigger

        self.ingest = IngestTrigger(cfg)  # a hub ingests what other computers send, right after they send it
        from ...sharing.people import CodeAttempts

        self.code_attempts = CodeAttempts()  # wrong invite and sign-in codes per address

    def _config_sig(self):
        try:
            return self.cfg.config_path.stat().st_mtime_ns
        except OSError:
            return None

    def refresh_config(self) -> None:
        """The dashboard runs for days: pick up config.toml edits (e.g. `interlatch connect codex`) without a restart."""
        from ...core.config import load_config

        sig = self._config_sig()
        if sig != self._cfg_sig:
            self.cfg = load_config(self.cfg.home)
            self._cfg_sig = sig

    @property
    def conn(self):
        conn = getattr(self.local, "conn", None)
        if conn is None:
            conn = connect(self.cfg.db_path)
            self.local.conn = conn
        return conn

    def context(self) -> Context:
        """What api/'s queries and actions get: the config, this thread's connection, the jobs and the dashboard's
        state, each read from here when used."""
        return Context(lambda: self.cfg, self._take_config, lambda: self.conn, self.jobs, self.state)

    def _take_config(self, cfg: Config) -> None:
        """An action wrote config.toml and gives the config it made: use it, and don't read the file again for it."""
        self.cfg = cfg
        self._cfg_sig = self._config_sig()

    def release(self) -> None:
        """Close this request thread's connection (ThreadingHTTPServer uses a new thread per request)."""
        conn = getattr(self.local, "conn", None)
        if conn is not None:
            try:
                conn.rollback()
                conn.close()
            finally:
                self.local.conn = None
