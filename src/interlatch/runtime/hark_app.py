"""The Hark Mac app (Hark.app, hark.com's desktop window): is it installed and running, and what its log says.

Hark keeps its chats on its servers (the browser extension saves them, sources/hark.py). On this Mac the app keeps
only a log, ~/Library/Logs/com.harklabs.hark.mac/shell.log, in lines like

    [2026-11-27][00:52:05][hark_desktop_lib::device_tools::link][INFO] device link: connected as 908d…

with times in UTC. Read here: launches, sign-ins, and the device link, through which Hark's assistant uses tools on
this Mac (files, shell, location): when it connected, the tools it offers, and what went wrong. The log doesn't
record each tool Hark ran; those are in the chats. Nothing here writes anything or talks to Hark.
"""

from __future__ import annotations

import plistlib
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..core.process import run_program

BUNDLE_ID = "com.harklabs.hark.mac"
EXECUTABLE = "hark-desktop"
TAIL_BYTES = 512 * 1024  # the log's end: enough for weeks of launches
MAX_PROBLEMS = 5
_LINE = re.compile(r"^\[(\d{4}-\d\d-\d\d)\]\[(\d\d:\d\d:\d\d)\]\[([^\]]*)\]\[([A-Z]+)\] (.*)$")
_LAUNCH = re.compile(r"^diagnostics: Hark (\S+) launched")
_TOOLS = re.compile(r"^device link: the tool list changed to (.+)$")


def app_paths() -> list[Path]:
    return [Path("/Applications/Hark.app"), Path("~/Applications/Hark.app").expanduser()]


def log_path() -> Path:
    return Path(f"~/Library/Logs/{BUNDLE_ID}/shell.log").expanduser()


def _version(app: Path) -> str | None:
    try:
        with open(app / "Contents" / "Info.plist", "rb") as f:
            return plistlib.load(f).get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None


def _running() -> bool:
    try:
        return run_program(["pgrep", "-x", EXECUTABLE], timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _tail(path: Path) -> list[str]:
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - TAIL_BYTES))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if size > TAIL_BYTES else lines  # the first may be cut


def _iso(day: str, clock: str) -> str:
    return f"{day}T{clock}Z"


def read_log(lines: list[str], now: datetime | None = None) -> dict:
    """What the log's lines say: the latest launch and sign-in, launches in the last 7 days, the device link's state,
    and the latest warnings and errors."""
    now = now or datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {"last_launch": None, "launched_version": None, "launches_7d": 0, "signed_in": None,
           "device_link": None, "tools": None, "problems": []}
    for line in lines:
        m = _LINE.match(line)
        if not m:
            continue
        at, level, text = _iso(m[1], m[2]), m[4], m[5]
        if launch := _LAUNCH.match(text):
            out["last_launch"], out["launched_version"] = at, launch[1]
            out["launches_7d"] += at >= week_ago
            out["device_link"] = None  # a new launch starts without a link
        elif text.startswith("sign-in sheet:") and "handed its code" in text:
            out["signed_in"] = at
        elif text.startswith("device link: connected as"):
            out["device_link"] = {"state": "connected", "at": at}
        elif tools := _TOOLS.match(text):
            out["tools"] = tools[1].strip()
        elif text.startswith("device link:") and level in ("WARN", "ERROR"):
            out["device_link"] = {"state": "trouble", "at": at, "detail": text.removeprefix("device link: ")}
        if level in ("WARN", "ERROR"):
            out["problems"].append({"at": at, "level": level.lower(), "text": text[:300]})
    out["problems"] = out["problems"][-MAX_PROBLEMS:][::-1]  # newest first
    return out


def status() -> dict:
    """The app on this Mac: {installed: False} when it isn't (and off macOS, where there is no Hark app)."""
    app = next((p for p in app_paths() if p.is_dir()), None) if sys.platform == "darwin" else None
    log = log_path()
    if app is None:
        return {"installed": False}
    return {"installed": True, "app": str(app), "version": _version(app), "running": _running(),
            "log": str(log) if log.is_file() else None, **read_log(_tail(log))}
