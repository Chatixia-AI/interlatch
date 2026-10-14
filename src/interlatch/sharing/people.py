"""People on a hub: who may send to it and open its dashboard, and with which role. No Tailscale needed.

Roles:
    admin     sees everything, invites people and changes roles, changes the hub's settings (team store, people)
    member    their computers send to the hub and get teammates' lessons back; sees the hub's dashboard
    readonly  sees the hub's dashboard; sends nothing, changes nothing

Projects: a member or read-only person sees the projects they were given (`interlatch hub invite … --project`), or
every project (`--all-projects`). Someone limited to projects sees only those projects' session summaries and project
lessons on the dashboard (access.py), and their computers share knowledge only, only for those projects (receiver.py).
Admins always see everything. People added before projects existed see every project, as they did.

Everything starts with an invite. An admin adds a person with a role and gets a one-time code (expires after
INVITE_DAYS) to pass on by chat; the hub sends no email. The code is redeemed once, either by a computer
(`interlatch hub join <hub> --code …`: it becomes a push token for that person and that computer) or by a browser
(the invite link: it becomes a dashboard session). A person's own computer can later ask for a short sign-in link
(SIGNIN_MINUTES) to open the hub's dashboard without a password.

Only hashes of codes, tokens and sessions are stored; the secret itself is shown once (codes) or lives only on the
computer (tokens) or in the browser's cookie (sessions). Removing a person, or revoking a token or session, takes
effect at the next request. Whoever is at the hub computer itself is always an admin: that is how the first admin
is made, and how a hub whose admins are all gone is recovered.

While a hub has no people, it works as before: computers send with the hub's shared token. Once it has people the
shared token keeps working until `[hub] shared_token = false` (Devices › People, or the config), so computers that
joined before are not cut off.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from ..core.util import utcnow_iso
from ..core.paths import is_abs_path, normalize_project_path

ROLES = ("admin", "member", "readonly")
ROLE_RANK = {"readonly": 0, "member": 1, "admin": 2}
INVITE_DAYS = 7
SIGNIN_MINUTES = 5
BROWSER_DAYS = 30  # a dashboard session lasts this long after it was last used
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: read aloud or retyped from chat
LOCAL = "this computer"  # the actor for things done at the hub itself
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")


class PeopleError(Exception):
    """A message template and its values, so the dashboard can show it in the viewer's language (`shown`)."""

    def __init__(self, template: str, **values):
        super().__init__(template.format(**values) if values else template)
        self.template, self.values = template, values

    def shown(self) -> str:
        from ..core.i18n import tr

        return tr(self.template, **self.values)


class UnknownCode(PeopleError):
    """A code this hub never issued (or not of the kind asked for): what a guess gets, so CodeAttempts counts it."""


class CodeAttempts:
    """Wrong codes per address, for the two places a code is the credential (/signin?code= and /api/hub/join).

    The first FREE wrong ones in WINDOW cost nothing. After them, a try from that address waits 1 s after the last wrong
    one, then 2 s, 4 s and so on up to MAX_WAIT, and is answered without its code being looked at. Never a lockout: a
    wait runs out, a quiet WINDOW forgets the address, and a code the hub issued (used or expired) doesn't count.
    Codes are long and single-use, so this is belt and braces."""

    FREE, WINDOW, MAX_WAIT, KEEP = 5, 15 * 60, 60, 10_000

    def __init__(self, clock=time.monotonic):
        self._clock, self._lock = clock, threading.Lock()
        self._seen: dict[str, tuple[int, float]] = {}  # address -> (wrong codes, when the last one came)

    def _count(self, who: str, now: float) -> tuple[int, float]:
        n, last = self._seen.get(who, (0, 0.0))
        return (0, 0.0) if n and now - last > self.WINDOW else (n, last)

    def wait(self, who: str) -> int:
        """Seconds `who` still waits before a code of theirs is looked at; 0 to go ahead."""
        with self._lock:
            now = self._clock()
            n, last = self._count(who, now)
            if n < self.FREE:
                return 0
            return max(0, math.ceil(min(self.MAX_WAIT, 2 ** (n - self.FREE)) - (now - last)))

    def wrong(self, who: str) -> None:
        with self._lock:
            now = self._clock()
            self._seen[who] = (self._count(who, now)[0] + 1, now)
            if len(self._seen) > self.KEEP:  # many addresses at once: forget the half that went quiet first
                cut = sorted(last for _, last in self._seen.values())[len(self._seen) // 2]
                self._seen = {k: v for k, v in self._seen.items() if v[1] > cut}


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.strip().encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _expired(at: str | None) -> bool:
    return bool(at) and at < _iso(_now())


def new_code() -> str:
    """XXXX-XXXX-XXXX: 60 bits, easy to read out and retype."""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalize_code(code: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9]", "", code or "").upper()
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}" if len(raw) == 12 else raw


def audit(conn: sqlite3.Connection, actor: str | None, action: str, person_id: int | None = None, **detail) -> None:
    conn.execute("INSERT INTO people_audit(at, actor, action, person_id, detail) VALUES (?, ?, ?, ?, ?)",
                 (utcnow_iso(), actor, action, person_id, json.dumps(detail, ensure_ascii=False) if detail else None))


def actor_of(person: dict | None) -> str:
    return f"person:{person['id']}" if person else LOCAL


def clean_projects(projects) -> list[str] | None:
    """None (every project) or a sorted list of distinct project paths; anything else is refused."""
    if projects is None:
        return None
    if isinstance(projects, str) or not isinstance(projects, (list, tuple, set)):
        raise PeopleError("projects must be a list of project paths")
    out = set()
    for x in projects:
        x = str(x or "").strip()
        x = normalize_project_path(x)
        if not is_abs_path(x) or len(x) > 1024 or "\0" in x:
            raise PeopleError("{project} is not a project on this hub", project=x or "''")
        out.add(x)
    return sorted(out)


def projects_of(person: dict | None) -> list[str] | None:
    """The projects `person` may see: None for every project (an admin, someone at the hub itself, or someone given
    every project), else their list, possibly empty."""
    if person is None or person.get("role") == "admin":
        return None
    raw = person.get("projects_json")
    if raw is None:
        return None
    try:
        got = json.loads(raw)
    except ValueError:
        return []  # unreadable: nothing, never everything
    return [x for x in got if isinstance(x, str)] if isinstance(got, list) else []


def sees(person: dict | None, project_path: str | None) -> bool:
    scope = projects_of(person)
    return scope is None or (project_path is not None and project_path in scope)


# ------------------------------------------------------------------ people
def has_people(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM people WHERE removed_at IS NULL LIMIT 1").fetchone() is not None


def get(conn: sqlite3.Connection, person_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    return dict(r) if r else None


def by_email(conn: sqlite3.Connection, email: str) -> dict | None:
    r = conn.execute("SELECT * FROM people WHERE email = ? AND removed_at IS NULL", ((email or "").strip().lower(),)).fetchone()
    return dict(r) if r else None


def add(conn: sqlite3.Connection, name: str, email: str | None, role: str, *, by: dict | None = None,
        projects: list[str] | None = None) -> dict:
    """Add a person. Email is optional but needed for a company sign-in (auth header) to find them. `projects`: the
    project paths they see, None for every project (the command line and the dashboard make the inviter choose)."""
    name, email = (name or "").strip(), (email or "").strip().lower() or None
    projects = clean_projects(projects)
    if not name:
        raise PeopleError("a name is needed")
    if role not in ROLES:
        raise PeopleError("role must be one of: {roles}", roles=", ".join(ROLES))
    if email and not EMAIL_RE.match(email):
        raise PeopleError("{email} is not an email address", email=email)
    if email and by_email(conn, email):
        raise PeopleError("{email} is already on this hub", email=email)
    if email:  # someone removed earlier comes back as a new person: the old row keeps its history
        conn.execute("UPDATE people SET email = NULL WHERE email = ? AND removed_at IS NOT NULL", (email,))
    cur = conn.execute("INSERT INTO people(name, email, role, created_at, created_by, projects_json) VALUES (?, ?, ?, ?, ?, ?)",
                       (name[:120], email, role, utcnow_iso(), by["id"] if by else None,
                        None if projects is None else json.dumps(projects, ensure_ascii=False)))
    audit(conn, actor_of(by), "add", cur.lastrowid, role=role, email=email, projects=projects)
    conn.commit()
    return get(conn, cur.lastrowid)


def set_role(conn: sqlite3.Connection, person_id: int, role: str, *, by: dict | None = None) -> dict:
    p = get(conn, person_id)
    if not p or p["removed_at"]:
        raise PeopleError("no such person")
    if role not in ROLES:
        raise PeopleError("role must be one of: {roles}", roles=", ".join(ROLES))
    if p["role"] == "admin" and role != "admin" and by is not None and _admins(conn) == [person_id]:
        raise PeopleError("this is the hub's last admin: make someone else an admin first")
    conn.execute("UPDATE people SET role = ? WHERE id = ?", (role, person_id))
    audit(conn, actor_of(by), "role", person_id, before=p["role"], after=role)
    conn.commit()
    return get(conn, person_id)


def set_projects(conn: sqlite3.Connection, person_id: int, projects: list[str] | None, *, by: dict | None = None) -> dict:
    """Which projects a person sees: a list of project paths, or None for every project. Takes effect at their
    next request; what their computers already sent stays."""
    p = get(conn, person_id)
    if not p or p["removed_at"]:
        raise PeopleError("no such person")
    projects = clean_projects(projects)
    before = None if p.get("projects_json") is None else projects_of({**p, "role": "member"})
    conn.execute("UPDATE people SET projects_json = ? WHERE id = ?",
                 (None if projects is None else json.dumps(projects, ensure_ascii=False), person_id))
    audit(conn, actor_of(by), "projects", person_id, before=before, after=projects)
    conn.commit()
    return get(conn, person_id)


def remove(conn: sqlite3.Connection, person_id: int, *, by: dict | None = None) -> None:
    """Remove a person: their codes, computer tokens and browser sessions stop working at once. What their computers
    sent stays on the hub."""
    p = get(conn, person_id)
    if not p or p["removed_at"]:
        raise PeopleError("no such person")
    if p["role"] == "admin" and by is not None and _admins(conn) == [person_id]:
        raise PeopleError("this is the hub's last admin: make someone else an admin first")
    now = utcnow_iso()
    conn.execute("UPDATE people SET removed_at = ? WHERE id = ?", (now, person_id))
    conn.execute("UPDATE people_tokens SET revoked_at = ? WHERE person_id = ? AND revoked_at IS NULL", (now, person_id))
    conn.execute("UPDATE people_codes SET used_at = ? WHERE person_id = ? AND used_at IS NULL", (now, person_id))
    audit(conn, actor_of(by), "remove", person_id)
    conn.commit()


def _admins(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM people WHERE role = 'admin' AND removed_at IS NULL")]


def listing(conn: sqlite3.Connection) -> list[dict]:
    """Everyone on the hub, with their computers, open browser sessions and unused invites (no secrets)."""
    now = _iso(_now())
    out = []
    for p in conn.execute("SELECT * FROM people WHERE removed_at IS NULL ORDER BY role = 'admin' DESC, name"):
        p = dict(p)
        p["projects"] = projects_of(p)
        toks = [dict(t) for t in conn.execute(
            "SELECT t.token_hash, t.kind, t.machine_id, t.label, t.created_at, t.last_used, t.expires_at, m.name AS machine_name "
            "FROM people_tokens t LEFT JOIN machines m ON m.id = t.machine_id "
            "WHERE t.person_id = ? AND t.revoked_at IS NULL ORDER BY t.created_at", (p["id"],))]
        p["computers"] = [{"id": t["token_hash"][:16], "machine_id": t["machine_id"], "name": t["machine_name"] or t["label"],
                           "since": t["created_at"], "last_used": t["last_used"]} for t in toks if t["kind"] == "computer"]
        p["browsers"] = [{"id": t["token_hash"][:16], "label": t["label"], "since": t["created_at"], "last_used": t["last_used"]}
                         for t in toks if t["kind"] == "browser" and not (t["expires_at"] and t["expires_at"] < now)]
        p["invites"] = [{"expires_at": r[0], "created_at": r[1]} for r in conn.execute(
            "SELECT expires_at, created_at FROM people_codes WHERE person_id = ? AND kind = 'invite' AND used_at IS NULL "
            "AND expires_at > ? ORDER BY created_at", (p["id"], now))]
        out.append(p)
    return out


# ------------------------------------------------------------------ one-time codes
def invite(conn: sqlite3.Connection, person_id: int, *, by: dict | None = None, days: int = INVITE_DAYS,
           machine: str | None = None) -> str:
    """A one-time code for this person: redeemed by `interlatch hub join … --code` (a computer) or the invite link
    (a browser). Returns the code; only its hash is kept. With `machine`, the invite is for that one computer the hub
    already knows: an admin's word that it is this person's, for a computer that can't show its key (may_claim)."""
    p = get(conn, person_id)
    if not p or p["removed_at"]:
        raise PeopleError("no such person")
    if machine and not conn.execute("SELECT 1 FROM machines WHERE id = ?", (machine,)).fetchone():
        raise PeopleError("this hub doesn't know a computer {machine}", machine=machine)
    code = new_code()
    now = _now()
    conn.execute("INSERT INTO people_codes(code_hash, person_id, kind, created_at, expires_at, machine_id) "
                 "VALUES (?, ?, 'invite', ?, ?, ?)", (_hash(code), person_id, _iso(now), _iso(now + timedelta(days=days)), machine))
    audit(conn, actor_of(by), "invite", person_id, days=days, **({"machine": machine} if machine else {}))
    conn.commit()
    return code


def signin_code(conn: sqlite3.Connection, person: dict) -> str:
    """A short-lived code that opens the dashboard as this person, asked for by one of their computers."""
    code = secrets.token_urlsafe(24)
    now = _now()
    conn.execute("INSERT INTO people_codes(code_hash, person_id, kind, created_at, expires_at) VALUES (?, ?, 'signin', ?, ?)",
                 (_hash(code), person["id"], _iso(now), _iso(now + timedelta(minutes=SIGNIN_MINUTES))))
    conn.commit()
    return code


def peek_invite(conn: sqlite3.Connection, code: str) -> dict | None:
    """The person an unused invite code is for, without using it up (e.g. to refuse a read-only person's computer
    while the code still opens a browser); None when the code isn't a live invite."""
    r = conn.execute("SELECT person_id, expires_at FROM people_codes WHERE kind = 'invite' AND used_at IS NULL "
                     "AND code_hash IN (?, ?)", (_hash(normalize_code(code)), _hash(code or ""))).fetchone()
    if not r or _expired(r["expires_at"]):
        return None
    p = get(conn, r["person_id"])
    return p if p and not p["removed_at"] else None


def _take_code(conn: sqlite3.Connection, code: str, kinds: tuple[str, ...]) -> dict:
    # an invite as typed (any case, with or without dashes), or a sign-in code exactly as issued
    r = conn.execute("SELECT * FROM people_codes WHERE code_hash IN (?, ?)",
                     (_hash(normalize_code(code)), _hash(code or ""))).fetchone()
    h = r["code_hash"] if r else None
    if not r or r["kind"] not in kinds:
        raise UnknownCode("that code isn't known on this hub")
    if r["used_at"]:
        raise PeopleError("that code was already used; ask an admin for a new one")
    if _expired(r["expires_at"]):
        raise PeopleError("that code has expired; ask an admin for a new one")
    p = get(conn, r["person_id"])
    if not p or p["removed_at"]:
        raise PeopleError("that person is no longer on this hub")
    cur = conn.execute("UPDATE people_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL", (utcnow_iso(), h))
    if cur.rowcount != 1:  # two redemptions at once: only one wins
        raise PeopleError("that code was already used; ask an admin for a new one")
    return p


def key_hash(key) -> str | None:
    """What a computer's key (its machine-key file) is kept as; None for anything that isn't one."""
    return _hash(key) if isinstance(key, str) and 32 <= len(key) <= 200 else None


def record_key(conn: sqlite3.Connection, machine_id: str, key) -> None:
    """Keep a computer's key, as its hash, the first time it shows one (it says hello, or joins). One on record never
    changes: a computer that later shows another isn't that computer."""
    if h := key_hash(key):
        conn.execute("UPDATE machines SET key_hash = ? WHERE id = ? AND key_hash IS NULL", (h, machine_id))


def invite_machine(conn: sqlite3.Connection, code: str) -> str | None:
    """The one computer an invite was made for (`interlatch hub invite --computer`), if it was."""
    r = conn.execute("SELECT machine_id FROM people_codes WHERE kind = 'invite' AND code_hash IN (?, ?)",
                     (_hash(normalize_code(code)), _hash(code or ""))).fetchone()
    return r["machine_id"] if r else None


def may_claim(conn: sqlite3.Connection, code: str, machine_id: str, key) -> bool:
    """Whether a (live) invite may make `machine_id` its person's computer. One the hub has never heard of may join. One
    it knows (it said hello or sent sessions) only by showing the key the hub recorded from it, or with an invite an
    admin made for that computer: anyone with an invite could otherwise claim another computer's id, and its sessions
    with it, and lock it out (a computer that joined as a person sends with its own token only)."""
    known = conn.execute("SELECT key_hash FROM machines WHERE id = ?", (machine_id,)).fetchone()
    if known is None and not conn.execute("SELECT 1 FROM sessions WHERE machine_id = ? LIMIT 1", (machine_id,)).fetchone():
        return True
    if invite_machine(conn, code) == machine_id:
        return True
    stored, shown = known["key_hash"] if known else None, key_hash(key)
    return bool(stored and shown and hmac.compare_digest(stored, shown))


NOT_THAT_COMPUTER = ("this hub already knows a computer with this id ({machine}) and can't tell this is it: an admin "
                     "makes an invite for that computer (`interlatch hub invite <name> --computer {machine}`), and you "
                     "join with that code")


def join_computer(conn: sqlite3.Connection, code: str, machine_id: str, machine_name: str | None = None,
                  key: str | None = None) -> tuple[dict, str]:
    """Redeem an invite for a computer: (person, push token). The token replaces any earlier one of that computer of
    the same person. A computer someone else's live token is bound to is refused: the invite would take it over, and
    send (or take back) sessions as that computer. An admin removes that computer first. A computer the hub already
    knows must show its key (may_claim). Both are checked only for a live invite, before it is used up: a wrong code
    learns nothing about the computer."""
    owner = peek_invite(conn, code)
    if owner and conn.execute(
            "SELECT 1 FROM people_tokens t JOIN people x ON x.id = t.person_id WHERE t.machine_id = ? AND t.kind = 'computer' "
            "AND t.revoked_at IS NULL AND x.removed_at IS NULL AND t.person_id != ?", (machine_id, owner["id"])).fetchone():
        raise PeopleError("this computer already joined this hub as someone else; an admin removes it from that "
                          "person first (Team › People)")
    if owner and (meant := invite_machine(conn, code)) and meant != machine_id:
        raise PeopleError("this invite is for another computer ({machine}); join from that one", machine=meant)
    if owner and not may_claim(conn, code, machine_id, key):
        raise PeopleError(NOT_THAT_COMPUTER, machine=machine_id)
    p = _take_code(conn, code, ("invite",))
    token = secrets.token_urlsafe(32)
    now = utcnow_iso()
    conn.execute("UPDATE people_tokens SET revoked_at = ? WHERE machine_id = ? AND kind = 'computer' AND revoked_at IS NULL",
                 (now, machine_id))
    conn.execute("INSERT INTO people_tokens(token_hash, person_id, kind, machine_id, label, created_at) "
                 "VALUES (?, ?, 'computer', ?, ?, ?)", (_hash(token), p["id"], machine_id, (machine_name or "")[:120] or None, now))
    conn.execute("UPDATE machines SET person_id = ? WHERE id = ?", (p["id"], machine_id))
    audit(conn, actor_of(p), "join", p["id"], machine=machine_id, name=machine_name)
    conn.commit()
    return p, token


def open_browser(conn: sqlite3.Connection, code: str, label: str | None = None) -> tuple[dict, str]:
    """Redeem an invite or a sign-in code in a browser: (person, session secret for the cookie)."""
    p = _take_code(conn, code, ("invite", "signin"))
    session = secrets.token_urlsafe(32)
    now = _now()
    conn.execute("INSERT INTO people_tokens(token_hash, person_id, kind, label, created_at, expires_at, last_used) "
                 "VALUES (?, ?, 'browser', ?, ?, ?, ?)",
                 (_hash(session), p["id"], (label or "")[:200] or None, _iso(now), _iso(now + timedelta(days=BROWSER_DAYS)), _iso(now)))
    audit(conn, actor_of(p), "signin", p["id"])
    conn.commit()
    return p, session


LANDING_RE = re.compile(r"/[A-Za-z0-9/_.~%=&?+-]{0,500}")  # a dashboard page: a hash route, such as /project?path=%2F…


def landing(page) -> str:
    """Where a sign-in link opens the dashboard: the page its next asks for (a hash route), or Home. Only a route's own
    characters pass, so the link can't lead off the dashboard or into the response's headers."""
    return f"/#{page}" if isinstance(page, str) and LANDING_RE.fullmatch(page) else "/#/"


# ------------------------------------------------------------------ who is asking
def _by_token(conn: sqlite3.Connection, secret: str, kind: str) -> tuple[dict, dict] | None:
    if not secret:
        return None
    t = conn.execute("SELECT * FROM people_tokens WHERE token_hash = ? AND kind = ?", (_hash(secret), kind)).fetchone()
    if not t or t["revoked_at"] or _expired(t["expires_at"]):
        return None
    p = get(conn, t["person_id"])
    if not p or p["removed_at"]:
        return None
    return p, dict(t)


def computer_bound(conn: sqlite3.Connection, machine_id: str) -> bool:
    """Whether a computer joined this hub as a person who is still on it (a live computer token is bound to it).
    Such a computer sends with its own token: the hub's shared token may not act as it (identity.authorize)."""
    return conn.execute(
        "SELECT 1 FROM people_tokens t JOIN people x ON x.id = t.person_id WHERE t.machine_id = ? AND t.kind = 'computer' "
        "AND t.revoked_at IS NULL AND x.removed_at IS NULL", (machine_id,)).fetchone() is not None


def computer_person(conn: sqlite3.Connection, token: str, machine_id: str | None = None) -> dict | None:
    """The person a computer's push token belongs to; None when it is no (longer a) valid token. A token presented by
    another computer than the one it was issued to is refused."""
    hit = _by_token(conn, token, "computer")
    if not hit:
        return None
    p, t = hit
    if machine_id and t["machine_id"] and t["machine_id"] != machine_id:
        return None
    if not t["last_used"] or t["last_used"][:16] != utcnow_iso()[:16]:  # at most one write a minute
        conn.execute("UPDATE people_tokens SET last_used = ? WHERE token_hash = ?", (utcnow_iso(), t["token_hash"]))
        conn.commit()
    return p


def browser_person(conn: sqlite3.Connection, session: str) -> dict | None:
    """The person a dashboard session cookie belongs to, sliding its expiry forward; None when it isn't valid."""
    hit = _by_token(conn, session, "browser")
    if not hit:
        return None
    p, t = hit
    now = _now()
    if not t["last_used"] or t["last_used"][:13] != _iso(now)[:13]:  # at most one write an hour
        conn.execute("UPDATE people_tokens SET last_used = ?, expires_at = ? WHERE token_hash = ?",
                     (_iso(now), _iso(now + timedelta(days=BROWSER_DAYS)), t["token_hash"]))
        conn.commit()
    return p


def sign_out(conn: sqlite3.Connection, session: str) -> None:
    conn.execute("UPDATE people_tokens SET revoked_at = ? WHERE token_hash = ? AND kind = 'browser' AND revoked_at IS NULL",
                 (utcnow_iso(), _hash(session)))
    conn.commit()


def revoke(conn: sqlite3.Connection, person_id: int, token_id: str, *, by: dict | None = None) -> bool:
    """Revoke one of a person's computer tokens or browser sessions by the short id listing() shows."""
    if not re.fullmatch(r"[0-9a-f]{16}", token_id or ""):
        return False
    cur = conn.execute("UPDATE people_tokens SET revoked_at = ? WHERE person_id = ? AND substr(token_hash, 1, 16) = ? "
                       "AND revoked_at IS NULL", (utcnow_iso(), person_id, token_id))
    if cur.rowcount:
        audit(conn, actor_of(by), "revoke", person_id, token=token_id)
    conn.commit()
    return bool(cur.rowcount)


def allows(person: dict | None, need: str) -> bool:
    """`person` (None: someone at the hub computer itself, always an admin) has at least role `need`."""
    if person is None:
        return True
    return ROLE_RANK.get(person.get("role"), -1) >= ROLE_RANK[need]


def public(person: dict | None) -> dict:
    """What the dashboard may know about who is viewing it."""
    if person is None:
        return {"id": None, "name": LOCAL, "email": None, "role": "admin", "here": True, "projects": None}
    return {"id": person["id"], "name": person["name"], "email": person.get("email"), "role": person["role"], "here": False,
            "projects": projects_of(person)}


def audit_log(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    rows = conn.execute("SELECT a.*, p.name AS person_name FROM people_audit a LEFT JOIN people p ON p.id = a.person_id "
                        "ORDER BY a.id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else {}
        if d["actor"] and d["actor"].startswith("person:"):
            who = get(conn, int(d["actor"][7:]))
            d["actor_name"] = who["name"] if who else d["actor"]
        else:
            d["actor_name"] = d["actor"]
        out.append(d)
    return out
