"""What a dashboard query or action works with (api/): the config, this request's connection to the archive, the
jobs that run long actions, and the little the dashboard keeps between requests. Not the App itself: App.context()
makes one, and each value is read from the App when it is used, so a long job sees the config as it is then."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from ...core.config import Config
from .jobs import Jobs


@dataclass
class DashboardState:
    """What the dashboard keeps from one request to the next, besides its config and jobs."""

    leaderboard_signin: dict | None = None  # a leaderboard sign-in waiting to come back from the hosted page
    update_check: threading.Lock = field(default_factory=threading.Lock)  # one daily update check at a time


class Context:
    def __init__(self, config: Callable[[], Config], take_config: Callable[[Config], None],
                 connection: Callable[[], sqlite3.Connection], jobs: Jobs, state: DashboardState):
        self._config = config
        self._take_config = take_config
        self._connection = connection
        self.jobs = jobs
        self.state = state

    @property
    def cfg(self) -> Config:
        return self._config()

    @cfg.setter
    def cfg(self, cfg: Config) -> None:
        """After an action wrote config.toml: the dashboard uses this config from now on."""
        self._take_config(cfg)

    @property
    def conn(self) -> sqlite3.Connection:
        """This request thread's connection to the archive, opened when first used (App.release closes it)."""
        return self._connection()
