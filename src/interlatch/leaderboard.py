"""The usage leaderboard: people who sign in with Google compare their hours, sessions and tokens with everyone else's.

Nothing leaves this computer until its person signs in and so joins. Then each sync sends one row about this
computer's own coding-agent sessions (never a teammate's that a hub holds, never an imported chat): the hours of
active time, the sessions started and the tokens used in the last 7, 30 and 90 days, with the name they show (their
Google first name until they change it) and their Google picture. No email, project, prompt or title. Leaving deletes
the row.

Where it lives: Firestore in the `interlatch` Firebase project (leaderboard/ holds its rules; the sign-in page ships
with the website, Chatixia-AI/chronicle-site, which owns the project's Hosting site),
spoken to over REST so no SDK is needed here. Signing in: the dashboard opens SIGNIN_PAGE on Firebase Hosting with
its own loopback address and a one-time state; that page signs in with Google through Firebase and comes back to
the dashboard with the Firebase refresh token in the URL fragment, which the dashboard keeps only when the state is
the one it made. Only that refresh token is kept, in <home>/leaderboard.json (0600). One computer reports for an
account at a time: the one that signed in last.

The totals are self-reported. The rules bound them, nothing proves them: it's a game, not a record.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .core.config import Config
from .core.util import parse_ts, to_iso, utcnow, utcnow_iso

PROJECT = "interlatch"
# The project's web config (its Firebase web key among it), as Firebase Hosting serves it: read once per process,
# so the key lives with the project rather than in this code (api_key())
CONFIG_URL = "https://interlatch.firebaseapp.com/__/firebase/init.json"
SIGNIN_PAGE = "https://interlatch.firebaseapp.com/signin.html"  # Chatixia-AI/chronicle-site: public/signin.html
PROVIDERS = ("google.com", "github.com")  # what the rules take; the page offers Google
PHOTO_RE = re.compile(r"^https://(lh3\.googleusercontent\.com|avatars\.githubusercontent\.com)/\S{1,450}$")
NAME_MAX = 32
WINDOWS = (7, 30, 90)
METRICS = {"hours": "h", "sessions": "s", "tokens": "t"}
TOP = 100  # the rules refuse a longer list
SEND_EVERY = 10 * 60  # a sync more often than this sends nothing new
BOARD_TTL = 5 * 60  # the board is read again at most this often, per tab
SIGNIN_TTL = 15 * 60  # how long a sign-in may take
TIMEOUT = 15
AGENTS = ("claude", "codex", "copilot", "bob", "antigravity")  # coding agents (hub.SHARED_AGENTS); chats stay out

DB = f"projects/{PROJECT}/databases/(default)/documents"
FIRESTORE = f"https://firestore.googleapis.com/v1/{DB}"


class LeaderboardError(Exception):
    pass


# ------------------------------------------------------------------ what this computer did
def totals(conn, cfg: Config, now=None) -> dict:
    """This computer's coding-agent sessions in each window: h<days> hours of active time, s<days> sessions started,
    t<days> tokens (input, output, cache read and write, as the dashboard counts them)."""
    from .hub import local_machine

    now = now or utcnow()
    here = local_machine(cfg)["id"]
    out = {}
    for days in WINDOWS:
        since = to_iso(now - timedelta(days=days))
        s, active, tokens = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(active_s), 0), COALESCE(SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) "
            "+ COALESCE(cache_read_tokens, 0) + COALESCE(cache_write_tokens, 0)), 0) FROM sessions "
            f"WHERE started_at >= ? AND source NOT IN ('history', 'remote') AND agent IN ({', '.join('?' * len(AGENTS))}) "
            "AND COALESCE(machine_id, ?) = ?", (since, *AGENTS, here, here)).fetchone()
        out[f"h{days}"] = round(active / 3600, 2)
        out[f"s{days}"] = int(s)
        out[f"t{days}"] = int(tokens)
    return out


def machine_tag(cfg: Config) -> str:
    """Which computer reports, without saying which: a hash of its machine id."""
    from .hub import local_machine

    return hashlib.sha256(local_machine(cfg)["id"].encode()).hexdigest()[:16]


# ------------------------------------------------------------------ HTTP
def _call(method: str, url: str, *, body=None, form: dict | None = None, token: str | None = None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "interlatch"}
    data = None
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=TIMEOUT) as res:
            raw = res.read()
    except HTTPError as e:
        try:
            err = json.loads(e.read() or b"{}")
        except ValueError:
            err = {}
        msg = err.get("error")
        msg = msg.get("message") if isinstance(msg, dict) else msg or err.get("error_description")
        raise LeaderboardError(f"{e.code} {msg or e.reason}") from None
    except (URLError, OSError) as e:
        raise LeaderboardError(f"no connection: {getattr(e, 'reason', e)}") from None
    return json.loads(raw) if raw.strip() else {}


_api_key: str | None = None


def api_key() -> str:
    """The Firebase web key, from the project's hosted config. It's public by design (every browser that opens the
    sign-in page gets it) and only names the project: the Firestore rules protect the data."""
    global _api_key
    if _api_key is None:
        key = _call("GET", CONFIG_URL).get("apiKey")
        if not isinstance(key, str) or not key:
            raise LeaderboardError("the leaderboard's settings didn't load")
        _api_key = key
    return _api_key


# ------------------------------------------------------------------ sign-in state (<home>/leaderboard.json)
def state_path(cfg: Config) -> Path:
    return cfg.home / "leaderboard.json"


def read_state(cfg: Config) -> dict | None:
    try:
        data = json.loads(state_path(cfg).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("uid") and data.get("refresh_token") else None


def write_state(cfg: Config, state: dict) -> None:
    path = state_path(cfg)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def _update_state(cfg: Config, **changes) -> dict | None:
    state = read_state(cfg)
    if state is not None:
        state.update(changes)
        write_state(cfg, state)
    return state


# ------------------------------------------------------------------ signing in: Google through the hosted page
def signin_url(return_to: str, state: str, lang: str = "en") -> str:
    """The hosted page that signs in and comes back to `return_to` (this dashboard) with `state`."""
    return f"{SIGNIN_PAGE}?{urlencode({'return': return_to, 'state': state, 'lang': lang})}"


def new_state() -> str:
    return secrets.token_urlsafe(24)


def same_state(expected: str | None, got) -> bool:
    return bool(expected) and isinstance(got, str) and hmac.compare_digest(expected, got)


def clean_name(name) -> str | None:
    """A name to show: one line, no control characters, at most NAME_MAX characters; None if nothing is left."""
    if not isinstance(name, str):
        return None
    name = " ".join("".join(ch for ch in name if ch.isprintable()).split())[:NAME_MAX].strip()
    return name or None


def _claims(id_token: str) -> dict:
    """The payload of an ID token Firebase just gave us over TLS (not verified here: Firestore's rules verify it)."""
    try:
        part = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        raise LeaderboardError("Firebase gave an unreadable sign-in") from None


def _refresh(refresh_token: str) -> dict:
    r = _call("POST", f"https://securetoken.googleapis.com/v1/token?key={api_key()}",
              form={"grant_type": "refresh_token", "refresh_token": refresh_token})
    if not r.get("id_token"):
        raise LeaderboardError("the leaderboard sign-in has expired: sign in again")
    return r


def sign_in(cfg: Config, refresh_token: str) -> dict:
    """Keep the session the sign-in page came back with: who it is, and the name and picture they show."""
    if not isinstance(refresh_token, str) or not 20 <= len(refresh_token) <= 4096:
        raise LeaderboardError("no sign-in came back")
    r = _refresh(refresh_token)
    claims = _claims(r["id_token"])
    fb = claims.get("firebase") or {}
    provider = fb.get("sign_in_provider")
    ids = (fb.get("identities") or {}).get(provider) or []
    if provider not in PROVIDERS or not ids:
        raise LeaderboardError("sign in with Google")
    photo = claims.get("picture") if isinstance(claims.get("picture"), str) and PHOTO_RE.match(claims["picture"]) else ""
    name = clean_name((claims.get("name") or "").split(" ")[0]) or "Player"
    state = {"uid": claims.get("user_id") or r.get("user_id"), "refresh_token": r.get("refresh_token") or refresh_token,
             "provider": provider, "pid": str(ids[0]), "name": name, "photo": photo, "joined_at": utcnow_iso(),
             "last_sent": None, "error": None, "paused": None}
    if not state["uid"]:
        raise LeaderboardError("Firebase didn't say who you are")
    write_state(cfg, state)
    return state


def _id_token(cfg: Config, state: dict) -> str:
    r = _refresh(state["refresh_token"])
    if r.get("refresh_token") and r["refresh_token"] != state["refresh_token"]:
        _update_state(cfg, refresh_token=r["refresh_token"])
    return r["id_token"]


# ------------------------------------------------------------------ Firestore values
def _value(v) -> dict:
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def _plain(fields: dict) -> dict:
    out = {}
    for k, v in (fields or {}).items():
        if "integerValue" in v:
            out[k] = int(v["integerValue"])
        elif "doubleValue" in v:
            out[k] = float(v["doubleValue"])
        elif "timestampValue" in v:
            out[k] = v["timestampValue"]
        else:
            out[k] = next(iter(v.values()), None)
    return out


# ------------------------------------------------------------------ sending, renaming and leaving
def send(cfg: Config, conn, *, force: bool = False) -> dict | None:
    """Send this computer's totals, once signed in; None when not. Another computer that signed in to the same account
    since takes over: this one stops sending (state "paused") until it signs in again."""
    state = read_state(cfg)
    if state is None or state.get("paused"):
        return state
    if not force and state.get("last_sent"):
        last = parse_ts(state["last_sent"])
        if last and (utcnow() - last).total_seconds() < SEND_EVERY:
            return state
    try:
        token = _id_token(cfg, state)
        tag = machine_tag(cfg)
        doc = f"{FIRESTORE}/players/{quote(state['uid'])}"
        if state.get("last_sent"):  # another computer may have taken over since
            try:
                current = _plain(_call("GET", doc, token=token).get("fields"))
            except LeaderboardError as e:
                if not str(e).startswith("404"):
                    raise
                current = {}
            if current.get("machine") and current["machine"] != tag:
                return _update_state(cfg, paused="another computer reports for this account now", error=None)
        fields = {"provider": state["provider"], "pid": state["pid"], "name": state["name"], "photo": state.get("photo") or "",
                  "machine": tag, **totals(conn, cfg)}
        _call("POST", f"{FIRESTORE}:commit", token=token, body={"writes": [{
            "update": {"name": f"{DB}/players/{state['uid']}", "fields": {k: _value(v) for k, v in fields.items()}},
            "updateTransforms": [{"fieldPath": "at", "setToServerValue": "REQUEST_TIME"}]}]})
    except LeaderboardError as e:
        return _update_state(cfg, error=str(e)[:300])
    _clear_board()
    return _update_state(cfg, last_sent=utcnow_iso(), error=None)


def send_safely(cfg: Config, conn) -> None:
    """The sync's send: never fails the sync."""
    import logging

    try:
        send(cfg, conn)
    except Exception:  # noqa: BLE001 - a game must not stop the archive
        logging.getLogger(__name__).exception("leaderboard send failed")


def rename(cfg: Config, conn, name) -> dict:
    """Show another name on the board, from the next send (sent now)."""
    clean = clean_name(name)
    if clean is None:
        raise LeaderboardError("type a name to show")
    if _update_state(cfg, name=clean) is None:
        raise LeaderboardError("sign in first")
    return send(cfg, conn, force=True)


def leave(cfg: Config) -> None:
    """Delete this account's row from the board, then forget the sign-in. The row goes even if this computer had
    stopped sending."""
    state = read_state(cfg)
    if state is None:
        return
    try:
        token = _id_token(cfg, state)
        _call("DELETE", f"{FIRESTORE}/players/{quote(state['uid'])}", token=token)
    except LeaderboardError as e:
        if not str(e).startswith("404"):
            raise
    state_path(cfg).unlink(missing_ok=True)
    _clear_board()


def status(cfg: Config) -> dict:
    state = read_state(cfg)
    if state is None:
        return {"joined": False}
    return {"joined": True, "uid": state.get("uid"), "name": state.get("name"), "photo": bool(state.get("photo")),
            "provider": state.get("provider"), "joined_at": state.get("joined_at"), "last_sent": state.get("last_sent"),
            "error": state.get("error"), "paused": state.get("paused")}


# ------------------------------------------------------------------ the board
_board_cache: dict[tuple[str, int], tuple[float, list]] = {}
_board_lock = threading.Lock()
_photos: dict[str, str] = {}  # uid: picture URL, from the boards read lately (avatar())


def _clear_board() -> None:
    with _board_lock:
        _board_cache.clear()


def board(by: str, days: int, *, now=None) -> list[dict]:
    """The top players for one metric and window, highest first. A row not sent within the window is left out: its
    totals are older than the period they claim."""
    if by not in METRICS or days not in WINDOWS:
        raise LeaderboardError("no such board")
    key = (by, days)
    with _board_lock:
        hit = _board_cache.get(key)
        if hit and time.monotonic() - hit[0] < BOARD_TTL:
            return hit[1]
    field = f"{METRICS[by]}{days}"
    res = _call("POST", f"{FIRESTORE}:runQuery?key={api_key()}", body={"structuredQuery": {
        "from": [{"collectionId": "players"}], "orderBy": [{"field": {"fieldPath": field}, "direction": "DESCENDING"}],
        "limit": TOP}})
    since = to_iso((now or utcnow()) - timedelta(days=days))
    rows = []
    for item in res if isinstance(res, list) else []:
        doc = item.get("document")
        if not doc:
            continue
        f = _plain(doc.get("fields"))
        if not f.get("name") or (f.get("at") or "") < (since or ""):
            continue
        uid = doc["name"].rsplit("/", 1)[-1]
        photo = f.get("photo") if isinstance(f.get("photo"), str) and PHOTO_RE.match(f["photo"]) else ""
        if photo:
            _photos[uid] = photo
        rows.append({"uid": uid, "name": str(f["name"])[:64], "photo": bool(photo), "at": f.get("at"),
                     "hours": f.get(f"h{days}", 0), "sessions": f.get(f"s{days}", 0), "tokens": f.get(f"t{days}", 0)})
    with _board_lock:
        _board_cache[key] = (time.monotonic(), rows)
    return rows


# ------------------------------------------------------------------ pictures, through this computer (the page loads
# nothing from elsewhere: its Content-Security-Policy)
_avatars: dict[str, tuple[bytes, str]] = {}
AVATAR_MAX = 300


def avatar(uid: str, cfg: Config | None = None) -> tuple[bytes, str] | None:
    """A player's picture, from a board read lately or (for this computer's person) the sign-in."""
    url = _photos.get(uid)
    if url is None and cfg is not None:
        state = read_state(cfg) or {}
        url = state.get("photo") if state.get("uid") == uid else None
    if not url or not PHOTO_RE.match(url):
        return None
    if url in _avatars:
        return _avatars[url]
    try:
        with urlopen(Request(url, headers={"User-Agent": "interlatch"}), timeout=TIMEOUT) as res:
            kind = res.headers.get_content_type()
            data = res.read(300_000)
    except (URLError, OSError):
        return None
    if kind not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        return None
    if len(_avatars) >= AVATAR_MAX:
        _avatars.clear()
    _avatars[url] = (data, kind)
    return data, kind
