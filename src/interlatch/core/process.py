"""How Interlatch runs itself and the agents it starts: the command that re-runs it, and the environment that
marks an agent's session as Interlatch's own (its hooks then record nothing)."""

from __future__ import annotations

import os
import sys

INTERNAL_ENV = "INTERLATCH_INTERNAL"  # on the agents Interlatch runs itself (analyses), so their hooks record nothing
INTERNAL_ENVS = (INTERNAL_ENV, "CHRONICLE_INTERNAL")  # and its name before the rename


def internal_env() -> dict[str, str]:
    """The environment for an agent Interlatch runs itself."""
    return {**os.environ, INTERNAL_ENV: "1"}


def self_command() -> list[str]:
    """argv that re-runs this program: the bundled executable inside Interlatch.app, else `python -m interlatch`."""
    return [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "interlatch"]
