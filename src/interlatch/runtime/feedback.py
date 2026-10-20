"""Feedback from the people who use Interlatch: a GitHub page to report a problem (a new issue) or suggest an idea (a
new discussion in Ideas), filled in with what this install is, for `interlatch feedback`, the dashboard's
Settings › Feedback and the VS Code extension's Send Feedback.

Nothing is sent from here: the person reads the details, opens the page and posts it themselves on GitHub. The
details say what this install is (versions, system, how it was installed, which agents it records) and never what is
in it: no paths, addresses, names, projects or session content. Only title and body go in the address: GitHub answers
404 to a `labels` parameter from anyone who may not label issues, which is everyone outside the project.
"""

from __future__ import annotations

import platform
from urllib.parse import quote, urlencode

from .. import __version__

REPO = "https://github.com/Chatixia-AI/interlatch"
KINDS = ("bug", "idea")

_INTRO = {
    "bug": ("**What happened**\n\n<!-- What did you do, what did you expect, and what happened instead? -->\n\n\n"
            "**Steps to reproduce**\n\n1. \n"),
    "idea": "**What would help**\n\n<!-- What are you trying to do, and what gets in the way today? -->\n\n",
}
_INSTALL = {"app": "Interlatch.app", "container": "container image", "pipx": "pipx", "pip": "pip",
            "source": "source checkout"}


def system_name() -> str:
    """The operating system as people name it: macOS 26.1 (arm64), Windows 11 (10.0.26100) (AMD64), Linux 6.8 (x86_64)."""
    name = platform.system()
    if name == "Darwin" and (mac := platform.mac_ver()[0]):
        name = f"macOS {mac}"
    elif name == "Windows":
        name = f"Windows {platform.release()} ({platform.version()})"
    else:
        name = f"{name} {platform.release()}"
    return f"{name} ({platform.machine()})" if platform.machine() else name


def install_kind() -> str:
    """How this copy was installed, without where from (a path or a URL would say who and where the person is)."""
    from .update import install_method

    method = install_method()
    if method.get("kind") == "uv":
        return "uv tool (local checkout)" if method.get("local") else "uv tool" if method.get("source") else "uv tool (PyPI)"
    return _INSTALL.get(method.get("kind"), method.get("kind") or "unknown")


def hub_role(cfg) -> str:
    """This computer's part in a hub, without the hub's address."""
    from ..sharing.identity import read_token

    if cfg.hub_dedicated:
        return "dedicated hub"
    if read_token(cfg):
        return "hub for other computers"
    if cfg.is_spoke:
        return "sends what it learns to a hub" if cfg.shares_knowledge else "sends its sessions to a hub"
    return "none"


def details(cfg, conn=None) -> list[tuple[str, str]]:
    """What a report says about this install, as (label, value) rows, in the order the report lists them."""
    rows = [("Interlatch", __version__), ("Installed with", install_kind()), ("System", system_name()),
            ("Python", platform.python_version()), ("Analysis", cfg.analysis.backend), ("Hub", hub_role(cfg))]
    if conn is not None:
        agents = [r[0] for r in conn.execute("SELECT DISTINCT agent FROM sessions ORDER BY agent")]
        rows.append(("Agents recorded", ", ".join(agents) or "none yet"))
    return rows


def body(kind: str, rows: list[tuple[str, str]], via: str) -> str:
    """The text the GitHub page starts with: what to write, then the details as a table. `via` names where it was
    made (`interlatch feedback`, the dashboard, the VS Code extension)."""
    table = "\n".join(f"| {label} | {str(value).replace('|', '/')} |" for label, value in rows)
    return f"{_INTRO[kind]}\n**Details** (filled in by {via})\n\n| | |\n|---|---|\n{table}\n"


def page_url(kind: str, title: str, text: str) -> str:
    """The GitHub page that opens with `title` and `text` filled in: a new issue, or a new discussion in Ideas."""
    # %20 for a space: a + is a space only to form decoders
    query = urlencode({"title": title, "body": text}, quote_via=quote)
    return f"{REPO}/issues/new?{query}" if kind == "bug" else f"{REPO}/discussions/new?category=ideas&{query}"


def report(kind: str, rows: list[tuple[str, str]], title: str = "", via: str = "`interlatch feedback`") -> dict:
    """{kind, title, details: [[label, value]], body, url} for one report with these detail rows (details(), plus
    whatever the caller adds: the dashboard's language, the editor's version)."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    text = body(kind, rows, via)
    return {"kind": kind, "title": title, "details": [list(r) for r in rows], "body": text,
            "url": page_url(kind, title, text)}
