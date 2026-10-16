"""Small shared helpers: time, formatting, locking, logging, JSONL IO."""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import logging
import logging.handlers
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

try:
    import fcntl
except ImportError:  # Windows: msvcrt locks a byte range instead
    fcntl = None
    import msvcrt

log = logging.getLogger("interlatch")

PROGRESS_RE = re.compile(r"(\d[\d,]*)\s*(?:of|/)\s*(\d[\d,]*)")  # "12 of 40", "120/2,000": a job's progress


# ---------------------------------------------------------------- time
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return to_iso(utcnow())


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_ts(value) -> datetime | None:
    """Parse an ISO timestamp (with Z) or epoch milliseconds into an aware UTC datetime."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def local_str(iso: str | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    dt = parse_ts(iso)
    return dt.astimezone().strftime(fmt) if dt else "-"


# ---------------------------------------------------------------- formatting
def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, _ = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def human_count(n: float | None) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(n) >= div:
            value = n / div
            return f"{value:.1f}{unit}" if value < 100 else f"{value:.0f}{unit}"
    return f"{n:.0f}"


def human_cost(usd: float | None) -> str:
    if usd is None:
        return "-"
    if usd >= 100:
        return f"${usd:,.0f}"
    return f"${usd:,.2f}"


def truncate(text, limit: int, marker: str = " …[truncated {n} chars]") -> str:
    if not text:
        return ""
    text = safe_text(text)
    if len(text) <= limit:
        return text
    return text[:limit] + marker.format(n=len(text) - limit)


def one_line(text, limit: int = 160) -> str:
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", str(text)).strip()
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def fingerprint(*parts: str) -> str:
    norm = "\x1f".join(re.sub(r"\W+", " ", (p or "").lower()).strip() for p in parts)
    return hashlib.sha1(norm.encode()).hexdigest()


_SURROGATE = re.compile("[\ud800-\udfff]")


def safe_text(value) -> str:
    """str() that SQLite and UTF-8 accept: lone surrogates (e.g. a truncated emoji escape) become U+FFFD."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return _SURROGATE.sub("\ufffd", value) if _SURROGATE.search(value) else value


def dumps(obj) -> str:
    return safe_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def loads(text: str | None, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- IO
def iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield JSON objects from a .jsonl or .jsonl.gz file, skipping torn/corrupt lines."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


@contextlib.contextmanager
def file_lock(path: Path, *, blocking: bool = True, timeout: float | None = None):
    """Advisory inter-process lock. Yields True when acquired, False otherwise (non-blocking)."""
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    acquired = False
    try:
        if not blocking:
            acquired = _try_lock(fh)
        elif timeout is None and fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
            acquired = True
        else:
            deadline = None if timeout is None else time.monotonic() + timeout
            while not (acquired := _try_lock(fh)):
                if deadline is not None and time.monotonic() >= deadline:
                    break
                time.sleep(0.25)
        if acquired:
            fh.seek(0)
            fh.truncate()
            fh.write(str(os.getpid()))
            fh.flush()
        yield acquired
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                _unlock(fh)
        fh.close()


# Windows locks a range of bytes, and a locked range may lie past the end of the file: this one is far from the pid
# that file_lock writes at the start, so writing it never meets the lock
_WIN_LOCK_AT = 1 << 30


def _try_lock(fh) -> bool:
    """Take the lock on `fh` without waiting; False when another process holds it."""
    if fcntl:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    os.lseek(fh.fileno(), _WIN_LOCK_AT, os.SEEK_SET)
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    return True


def _unlock(fh) -> None:
    if fcntl:
        fcntl.flock(fh, fcntl.LOCK_UN)
    else:
        os.lseek(fh.fileno(), _WIN_LOCK_AT, os.SEEK_SET)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


class SharedRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A log that several processes write (the dashboard, the background sync, the hooks). Windows can't rename a file
    another process has open, so a rollover there fails until the others let go; it used to print "--- Logging error
    ---" with WinError 32 under every message. A failed rollover now keeps writing to the same file and is tried again
    with the next message."""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            if self.stream is None:
                self.stream = self._open()


def setup_logging(logs_dir: Path, *, verbose: bool = False, console: bool = False) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("interlatch")
    if getattr(root, "_interlatch_configured", False):
        return
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = SharedRotatingFileHandler(logs_dir / "chronicle.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        ch.setLevel(logging.DEBUG if verbose else logging.WARNING)
        root.addHandler(ch)
    root._interlatch_configured = True  # type: ignore[attr-defined]
