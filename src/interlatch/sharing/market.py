"""Who's hiring: job-market numbers for the Party's classes, the roadmap's tracks.

careers/ (outside the package) reads the public job boards of a few dozen companies through their official APIs, maps
each technical posting to the roadmap's tracks and modules, and keeps only derived numbers: per track, the open
postings and their share, worldwide and in Japan, and the modules employers ask for most. No posting's title, link or
text is kept anywhere. It publishes them as the `market/current` document in the `interlatch` Firebase project, and as
market.json beside the Party's catalog (knowledge/market.json), which ships with each release.

Reading them: one GET of that document with the project's web key alone (leaderboard.api_key()). The rules let anyone
read it, so no sign-in goes with the request and nothing about the person does. It runs only when a signed-in Party
is computed, at most every TTL in this process. Until it answers, or when it can't (offline, no document yet, the
rules not deployed), the Party shows the bundled copy; a failed read is tried again after RETRY. Nothing here raises
into the Party: no numbers at all is None.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from . import leaderboard as lb

DOC_URL = f"{lb.FIRESTORE}/market/current"
BUNDLED_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "market.json"
TTL = 6 * 3600  # the live numbers are read again at most this often
RETRY = 15 * 60  # after a failed read: what there is (the last live numbers, else the bundled copy) for this long
WAIT = 3.0  # the first read holds a Party back at most this long (seconds); later ones never do
COUNTS = ("global", "japan", "new_global", "new_japan")
SHARES = ("share_global", "share_japan")
TRENDS = ("trend_global", "trend_japan")
SAMPLES = ("boards", "global", "japan")
TOP_MODULES = 6
_ID = re.compile(r"^[A-Za-z0-9][\w-]{0,79}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ the document, as plain values
def _value(v):
    """One Firestore value as plain Python: maps and arrays too (leaderboard._plain reads the flat ones)."""
    if not isinstance(v, dict):
        return None
    if "mapValue" in v:
        return _fields((v.get("mapValue") or {}).get("fields"))
    if "arrayValue" in v:
        return [_value(x) for x in (v.get("arrayValue") or {}).get("values") or []]
    return lb._plain({"v": v}).get("v")


def _fields(fields) -> dict:
    return {k: _value(v) for k, v in fields.items()} if isinstance(fields, dict) else {}


def _number(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf") else None


def _count(v) -> int:
    n = _number(v)
    return int(n) if n is not None and n > 0 else 0


def _share(v) -> float | None:
    n = _number(v)
    return round(min(max(n, 0.0), 1.0), 4) if n is not None else None


def _trend(v) -> float | None:
    n = _number(v)
    return round(n, 3) if n is not None and n >= 0 else None


def clean(raw) -> dict | None:
    """The numbers the dashboard uses, from a market document (Firestore's or the bundled one): the known fields with
    the types they should have, and nothing else (it is drawn in the page). None when it isn't a market at all."""
    if not isinstance(raw, dict) or not isinstance(raw.get("classes"), dict):
        return None
    classes = {}
    for cid, c in raw["classes"].items():
        if not isinstance(cid, str) or not _ID.match(cid) or not isinstance(c, dict):
            continue
        tops = c.get("top_modules") if isinstance(c.get("top_modules"), list) else []
        classes[cid] = {**{k: _count(c.get(k)) for k in COUNTS}, **{k: _share(c.get(k)) for k in SHARES},
                        **{k: _trend(c.get(k)) for k in TRENDS},
                        "top_modules": list(dict.fromkeys(m for m in tops if isinstance(m, str) and _ID.match(m)))[:TOP_MODULES]}
    sample = raw.get("sample") if isinstance(raw.get("sample"), dict) else {}
    status, as_of = raw.get("status"), raw.get("as_of")
    return {"status": status if isinstance(status, str) and _ID.match(status) else None,
            "as_of": as_of if isinstance(as_of, str) and _DAY.match(as_of) else None,
            "window_days": _count(raw.get("window_days")) or None,
            "sample": {k: _count(sample.get(k)) for k in SAMPLES},
            "classes": classes}


def bundled() -> dict | None:
    """The copy that shipped with this version (knowledge/market.json); None without one."""
    try:
        got = clean(json.loads(BUNDLED_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    return {**got, "source": "bundled"} if got else None


def read_live() -> dict:
    """market/current from Firestore, with the web key and nothing else. Raises LeaderboardError when it can't."""
    doc = lb._call("GET", f"{DOC_URL}?key={lb.api_key()}")
    got = clean(_fields(doc.get("fields") if isinstance(doc, dict) else None))
    if got is None:
        raise lb.LeaderboardError("the market document has no numbers")
    return {**got, "source": "live"}


# ------------------------------------------------------------------ the cache: one read at a time, in the background
_lock = threading.Lock()
_cache: tuple[float, float, dict | None] | None = None  # (when, how long it holds, the market)
_reader: threading.Thread | None = None
_now = time.monotonic


def clear_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def _refresh() -> None:
    """One read, in its own thread: the live numbers, else the last live ones, else the bundled copy."""
    global _cache
    try:
        with _lock:
            before = _cache[2] if _cache else None
        try:
            got, ttl = read_live(), TTL
        except Exception as e:  # noqa: BLE001 - offline, no document yet, rules not deployed, a bad answer
            log.info("market numbers not read: %s", e)
            got, ttl = (before if before and before.get("source") == "live" else bundled()), RETRY
        with _lock:
            _cache = (_now(), ttl, got)
    except Exception:  # noqa: BLE001 - the cache stays as it was, and the next Party tries again
        log.exception("market numbers failed")


def current() -> dict | None:
    """The market numbers: {status, as_of, window_days, sample, classes, source: "live" | "bundled"}, or None when
    there are none. A read older than its time answers at once and starts a new read beside it; the first read of
    all waits up to WAIT for Firestore, then answers with the bundled copy. Never raises."""
    global _reader
    try:
        with _lock:
            hit = _cache
            if hit is not None and _now() - hit[0] < hit[1]:
                return hit[2]
            started = _reader is None or not _reader.is_alive()
            if started:
                _reader = threading.Thread(target=_refresh, name="interlatch-market", daemon=True)
                _reader.start()
            reader = _reader
        if hit is not None:
            return hit[2]
        if started:
            reader.join(WAIT)
            with _lock:
                if _cache is not None:
                    return _cache[2]
        return bundled()
    except Exception:  # noqa: BLE001 - a game's numbers must not stop the Party
        log.exception("market numbers failed")
        try:
            return bundled()
        except Exception:  # noqa: BLE001
            return None
