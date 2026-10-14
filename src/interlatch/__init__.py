"""Interlatch: shared memory for your coding agents, recorded locally from every session (Claude Code, Codex, Copilot,
Bob, Antigravity), each lesson linked to the session it came from."""

from importlib.metadata import PackageNotFoundError, version


def _version() -> str:
    """Single source of truth: pyproject.toml, via the installed package's metadata."""
    for dist in ("interlatch", "agents-chronicle"):  # the distribution, and its name before the rename
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
    return "0.0.0+unknown"  # running from a source tree that was never installed


__version__ = _version()
