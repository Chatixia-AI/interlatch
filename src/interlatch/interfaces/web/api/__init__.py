"""The dashboard's API by feature: what each page reads and the actions it takes, as functions of a Context
(context.py). The handler reaches each feature as api.<feature>."""

from . import (  # noqa: F401
    artifacts,
    common,
    devices,
    hub_admin,
    knowledge,
    leaderboard,
    projects,
    sessions,
    settings,
    sources,
    status,
    storage,
    team,
)
