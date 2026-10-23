"""The dashboard's HTTP side: which host and origin may ask, who is asking (this computer, a person signed in to
a hub, another computer sending sessions), and handing each path to its route (routes.py)."""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ... import __version__
from ...core import i18n
from ...core.i18n import tr
from ...sharing import news
from .app import App
from .assets import PAGE_CSP, WEB_FILES, web_type
from . import api, routes

LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
# A person's dashboard session on a hub (people.py) is kept in a cookie named for that hub: interlatch_session_<id>,
# and __Host-interlatch_session_<id> over https (session_cookie_name). Browsers keep cookies by host name, not port, so
# two hubs on one name with one cookie name took each other's sign-in.
SESSION_COOKIE = "interlatch_session"
COOKIE_ID_KEY = "hub_cookie_id"  # kv: the hub's own random id in its cookie's name, made at its first sign-in
COOKIE_ID_RE = re.compile(r"[0-9a-f]{8}")
# Names every hub used before it had its own: still taken, so nobody is signed out by an upgrade, and replaced by the
# hub's own name on the first answer that finds one holding a session of this hub (_viewer). Drop them once a release
# with this has been out longer than a session lasts (people.BROWSER_DAYS after its last use).
LEGACY_COOKIES = (SESSION_COOKIE, "chronicle_session")  # chronicle_session: from before the rename to Interlatch


def session_cookie_name(conn, *, https: bool, create: bool = False) -> str | None:
    """The name of this hub's session cookie: its own random id (kv hub_cookie_id), so that two hubs reached under one
    host name don't overwrite each other's sign-in. Over https it starts with __Host-, which a browser takes only from a
    secure page, with Path=/ and no Domain, so another site or a plain-http server on the same name can't set it.
    None while the hub has no id yet (it never signed anyone in), unless `create` makes one: only signing in and
    replacing an old cookie do, so a dashboard without people never writes it."""
    from ...storage.kv import kv_get, kv_set, kv_transaction

    hub_id = kv_get(conn, COOKIE_ID_KEY)
    if not (hub_id and COOKIE_ID_RE.fullmatch(hub_id)):
        if not create:
            return None
        with kv_transaction(conn):  # two first sign-ins at once agree on one id
            hub_id = kv_get(conn, COOKIE_ID_KEY)
            if not (hub_id and COOKIE_ID_RE.fullmatch(hub_id)):
                hub_id = secrets.token_hex(4)
                kv_set(conn, COOKIE_ID_KEY, hub_id)
    return f"{'__Host-' if https else ''}{SESSION_COOKIE}_{hub_id}"


def sent_cookies(values: list[str]) -> dict[str, list[str]]:
    """The cookies a request's Cookie headers carry, by name, each value in the order sent. Read leniently, pair by
    pair: a browser also sends the cookies of other servers on the same host name, and one odd value among them must
    not hide this hub's (http.cookies gives up on the whole header)."""
    out: dict[str, list[str]] = {}
    for line in values:
        for part in line.split(";"):
            name, eq, value = part.strip().partition("=")
            if eq and name and value.strip():
                out.setdefault(name, []).append(value.strip())
    return out

# headers a proxy adds: a request with any of them came through one, so it is not from someone at this computer
PROXY_HEADERS = ("X-Forwarded-For", "Tailscale-User-Login", "X-Forwarded-Proto", "X-Real-IP", "Forwarded")
HUB_PUSH = ("/api/hub/file", "/api/hub/sessions", "/api/hub/analyses", "/api/hub/done", "/api/hub/hello",
            "/api/hub/withdraw")
# POSTs any person signed in to a hub may make, not only admins: their own Party quest log, goal, cleared encounters,
# bag and decks (routes.PARTY decides)
PARTY_OWN = ("/api/party/quest", "/api/party/goal", "/api/party/hero", "/api/party/cleared", "/api/party/pick", "/api/party/buy",
             "/api/party/sell", "/api/party/equip", "/api/party/use", "/api/party/deck")

log = logging.getLogger("interlatch.server")
SIGNIN_HELP = ("Ask an admin of this hub for a new invite, or open the hub's dashboard again from your own Interlatch "
               "(Settings › Devices).")
SIGNIN_CHECK = "Go on only if you opened this link from your own Interlatch or an admin of this hub sent it to you."
TOO_MANY_CODES = "too many wrong codes from this address; try again in {seconds} s"  # people.CodeAttempts
# The /signin page's policy: the dashboard's own stylesheet, fonts, logo and signin.js, nothing inline, and its form
# posts only here. It may not be framed: a page that shows it in a frame can't trick a click on Sign in.
SIGNIN_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
              "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
# line icons, drawn as app.js's icon() draws them
_ICON = '<svg class="icon{cls}" viewBox="0 0 24 24" aria-hidden="true">{paths}</svg>'
SUN = _ICON.format(cls=" ic-sun", paths='<circle cx="12" cy="12" r="4"/>' + "".join(
    f'<path d="{d}"/>' for d in ("M12 2v2", "M12 20v2", "m4.93 4.93 1.41 1.41", "m17.66 17.66 1.41 1.41", "M2 12h2",
                                 "M20 12h2", "m6.34 17.66-1.41 1.41", "m19.07 4.93-1.41 1.41")))
MOON = _ICON.format(cls=" ic-moon", paths='<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"/>')
SHIELD = _ICON.format(cls="", paths='<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="m9 12 2 2 4-4"/>')


def make_handler(app: App, port: int):
    local_hosts = {f"{h}:{port}" for h in LOOPBACK}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"interlatch/{__version__}"
        viewer: dict | None = None  # who is viewing (_viewer), None meaning an admin
        limited = False  # the viewer sees only some projects (access.py): set per request in _get
        signed_in = ""  # the session cookie _viewer signed the viewer in with
        cookies_out: list[str] | tuple = ()  # Set-Cookie headers for this answer: a new list per request (do_GET, do_POST)

        def log_message(self, fmt, *args):  # quiet
            log.debug("%s - %s", self.address_string(), fmt % args)

        def send_header(self, keyword, value):
            """http.server writes a header as given: one with a line break in it would start headers (or a body) of
            its own. Header values come from transcripts too (a file's name), so none may hold one: a break becomes
            a space rather than an error, which, raised after send_response, would leave two status lines queued."""
            super().send_header(re.sub(r"[\r\n\0]", " ", str(keyword)), re.sub(r"[\r\n\0]", " ", str(value)))

        def _host(self) -> str:
            return (self.headers.get("Host") or "").strip().lower()

        def _host_ok(self) -> bool:
            """The Host header names this dashboard (a DNS-rebinding guard): localhost, or an allowed name."""
            host = self._host()
            if host in local_hosts:
                return True
            name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
            return any(host == a or name == a for a in app.cfg.server_allowed_hosts)

        def _user_ok(self) -> bool:
            """With [server] allowed_users set, a request that came through Tailscale Serve needs one of those logins.

            Serve connects from localhost and always sets X-Forwarded-For and Tailscale-User-Login itself (a client's
            own values are replaced), but it passes the client's Host header through as sent: whether a request came
            through Serve is told by X-Forwarded-For, never by the Host name, or a tailnet device could claim
            `Host: 127.0.0.1` and skip the check."""
            users = app.cfg.server_allowed_users
            if not users:
                return True
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return False  # only Serve on this computer may vouch for a login
            proxied = self.headers.get("X-Forwarded-For") is not None or self.headers.get("Tailscale-User-Login") is not None
            if not proxied and self._host() in local_hosts:
                return True  # this computer itself
            return (self.headers.get("Tailscale-User-Login") or "") in users

        def _from_here(self) -> bool:
            """The request comes from this computer itself: not through Tailscale Serve, not from another device.
            Gates the settings that hold a password or change what leaves the computer (Devices).

            With `[server] behind_proxy`, never: a reverse proxy on this computer may send neither X-Forwarded-For nor
            its visitors' Host (nginx's default), so every visitor would look like someone at the hub itself."""
            if app.cfg.server_behind_proxy:
                return False
            proxied = any(self.headers.get(h) is not None for h in PROXY_HEADERS)
            return not proxied and self.client_address[0] in ("127.0.0.1", "::1") and self._host() in local_hosts

        def _people_mode(self) -> bool:
            from ...sharing import people

            return people.has_people(app.conn)

        def _local(self) -> str | None:
            """"mac", "linux" or "windows" when the request comes from this computer itself and Interlatch can open
            files here; None otherwise. Gates "Open on this Mac"."""
            import sys

            if not self._from_here():
                return None
            return ("mac" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux")
                    else "windows" if sys.platform == "win32" else None)

        # ---- who is viewing (people.py): set per request in _get/_post, None meaning an admin
        def _client(self) -> str:
            """The address a request came from. Through a proxy in [server] trusted_proxies, the visitor that proxy
            names: the last X-Forwarded-For entry, the one it added itself. Anyone else's X-Forwarded-For is not
            believed."""
            addr = self.client_address[0]
            if addr in app.cfg.server_trusted_proxies:
                forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
                if forwarded:
                    return forwarded
            return addr

        def _proxied_https(self) -> bool:
            """The request reached a proxy in [server] trusted_proxies over https. X-Forwarded-Proto from anyone else
            is not believed."""
            return (self.client_address[0] in app.cfg.server_trusted_proxies
                    and (self.headers.get("X-Forwarded-Proto") or "").strip().lower() == "https")

        def _cookie_name(self, create: bool = False) -> str | None:
            return session_cookie_name(app.conn, https=self._proxied_https(), create=create)

        def _session(self) -> str:
            """The session this browser is signed in with (_viewer found it), else the first one it sent."""
            return self.signed_in or next((v for _, v in self._session_cookies()), "")

        def _sessions(self) -> tuple[str, ...]:
            return tuple(v for _, v in self._session_cookies())

        def _session_cookies(self) -> list[tuple[str, str]]:
            """(name, value) of the session cookies this browser sent: this hub's own name first, then the names from
            before it had one. Over https only the __Host- name counts, which no plain-http server can set."""
            current = self._cookie_name()
            sent = sent_cookies(self.headers.get_all("Cookie") or [])
            return [(n, v) for n in ((current,) if current else ()) + LEGACY_COOKIES for v in sent.get(n, ())]

        def _session_cookie(self, value: str, max_age: int) -> str:
            secure = "; Secure" if self._proxied_https() else ""  # __Host- (over https) requires it
            return f"{self._cookie_name(create=True)}={value}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}{secure}"

        def _clear_cookie(self, name: str) -> str:
            secure = "; Secure" if self._proxied_https() else ""
            return f"{name}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0{secure}"

        def _ours(self) -> list[tuple[str, str]]:
            """The session cookies this browser sent that hold a live session of this hub. A cookie under an old name
            may be another hub's on the same host name: that one is left alone, never cleared from here."""
            from ...sharing import people

            return [(n, v) for n, v in self._session_cookies() if people.browser_person(app.conn, v)]

        def _replace_cookie(self, value: str):
            """This browser's session `value` came in a cookie under an old name: the answer sets it under this hub's
            own name and clears the old names that held it. The session itself goes on (no new secret), so requests
            the page sends meanwhile, still with the old cookie, keep working."""
            from ...sharing import people

            self.cookies_out.append(self._session_cookie(value, people.BROWSER_DAYS * 86400))
            self.cookies_out += [self._clear_cookie(n) for n, v in self._session_cookies() if v == value and n in LEGACY_COOKIES]

        def end_headers(self):
            for cookie in self.cookies_out:  # set while answering (_viewer, sign-in, sign-out), whatever the answer
                self.send_header("Set-Cookie", cookie)
            self.cookies_out = []
            super().end_headers()

        def _viewer(self) -> tuple[dict | None, tuple[dict, int] | None]:
            """(person, None) for who is viewing, person None being an admin: someone at the hub computer itself, or,
            while the hub has no people, a Tailscale login that Serve on this computer vouched for ([server]
            allowed_users already checked it). (None, (error, status)) when no one is signed in.

            A hub without people has no sign-in, so anything else is refused: a request from another device
            (server.host 0.0.0.0), through a reverse proxy, or through Tailscale Funnel would be an admin of
            everything."""
            from ...sharing import people

            if self._from_here():
                return None, None
            if not self._people_mode():
                if (not app.cfg.server_behind_proxy and self.client_address[0] in ("127.0.0.1", "::1")
                        and self.headers.get("Tailscale-User-Login")):
                    return None, None
                return None, ({"error": tr("this dashboard has no people yet, so only this computer may open it. At "
                                           "the hub, run `interlatch hub invite <your name> --email <email> --role admin` "
                                           "and open the invite link it prints"), "nobody": True}, 403)
            header = app.cfg.server_auth_header
            if header and self.client_address[0] in app.cfg.server_trusted_proxies and self.headers.get(header):
                person = people.by_email(app.conn, self.headers.get(header))  # a company sign-in in front of the hub
                if not person:
                    return None, ({"error": tr("you're not on this hub; ask an admin to add you")}, 403)
                return person, None
            for name, value in self._session_cookies():
                person = people.browser_person(app.conn, value)
                if person:
                    self.signed_in = value
                    if name in LEGACY_COOKIES:  # signed in before the hub had its own cookie name: move it there
                        self._replace_cookie(value)
                    return person, None
            return None, ({"error": tr("sign in to this hub"), "signin": True}, 401)

        def _can_admin(self) -> bool:
            """May change the hub's own settings (people, team store): at the hub itself, or an admin person. A hub
            without people keeps these to the computer itself, as before."""
            return self._from_here() or (self.viewer is not None and self.viewer["role"] == "admin")

        def _me(self) -> dict:
            ctx = app.context()
            from ...sharing import people

            return {"viewer": {**people.public(self.viewer), "here": self._from_here()},
                    "people_mode": people.has_people(app.conn), "can_admin": self._can_admin(), "hub": api.team.hub_info(ctx),
                    "game": ctx.cfg.game_enabled}

        def _address(self) -> str:
            """The hub's address as others reach it: [hub] address, or a guess from how this page was opened."""
            if app.cfg.hub_address:
                return app.cfg.hub_address
            return f"{'https' if self._proxied_https() else 'http'}://{self._host() or '127.0.0.1'}"

        def _same_origin(self) -> bool:
            """A page of this dashboard sent the request, not another site's (a login CSRF): the browser's
            Sec-Fetch-Site says so, or its Origin names this Host. A page can set neither."""
            if (self.headers.get("Sec-Fetch-Site") or "").strip().lower() == "same-origin":
                return True
            origin = (self.headers.get("Origin") or "").strip().lower()
            return origin not in ("", "null") and urlparse(origin).netloc == self._host()

        def _signin_page(self, status: int, title: str, body: str, retry: int = 0, switch_lang: bool = False):
            """The small page /signin answers with: asking before a sign-in, or why one didn't happen. The dashboard's
            own sign-in card (app.css), with its theme and language buttons (signin.js), under a policy that lets in
            the dashboard's own files and nothing inline."""
            import html
            from urllib.parse import urlencode

            title = html.escape(title)
            hub = app.cfg.hub_name or self._host()
            switch = ""
            if switch_lang:  # the same link in the other language; not on a refusal, where it would try the code again
                other = "en" if i18n.lang.get() == "ja" else "ja"
                q = {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items()} | {"lang": other}
                switch = (f'<a class="btn small" id="signin-lang" lang="{other}" href="/signin?{html.escape(urlencode(q))}">'
                          f'{"日本語" if other == "ja" else "English"}</a>')
            theme = html.escape(tr("Toggle theme"))
            page = (f'<!doctype html><html lang="{i18n.lang.get()}"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width, initial-scale=1">'
                    f'<meta name="color-scheme" content="light dark"><title>{title} · Interlatch</title>'
                    '<link rel="icon" href="/favicon.png" type="image/png"><link rel="stylesheet" href="/app.css">'
                    '<script src="/signin.js"></script></head><body><main class="signin-screen">'
                    '<div class="card signin-card"><div class="signin-brand">'
                    '<img src="/icon.png" width="26" height="26" alt="">Interlatch'
                    f'{f"<span class=signin-hub>{html.escape(hub)}</span>" if hub else ""}</div>'
                    f'<h1>{title}</h1>{body}<div class="signin-foot">{switch}'
                    f'<button class="btn small" id="signin-theme" type="button" aria-label="{theme}" title="{theme}">'
                    f'{SUN}{MOON}</button></div></div></main></body></html>').encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            if retry:
                self.send_header("Retry-After", str(retry))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")  # the code is in this page's address
            self.send_header("Content-Security-Policy", SIGNIN_CSP)
            self.end_headers()
            self.wfile.write(page)

        def _signin_refused(self, exc, wait: int = 0, status: int = 400):
            import html

            self._signin_page(429 if wait else status, tr("Could not sign in"),
                              f'<p class="signin-note">{html.escape(exc.shown())}</p><p>{html.escape(tr(SIGNIN_HELP))}</p>'
                              f'<a class="btn primary signin-go" href="/">{html.escape(tr("Open the dashboard"))}</a>', wait)

        def _signin_code(self, code: str, take):
            """take(code) for a sign-in, after the wrong-code wait (people.CodeAttempts): its answer, or None once the
            refusal page was sent."""
            from ...sharing import people

            accept = (self.headers.get("Accept-Language") or "").strip().lower()
            asked = parse_qs(urlparse(self.path).query).get("lang", [""])[-1]  # the page's language button
            # a link opened by hand sends no X-Interlatch-Lang
            i18n.lang.set(asked if asked in i18n.LANGS else "ja" if accept.startswith("ja") else "en")
            who = self._client()
            wait = app.code_attempts.wait(who)
            try:
                if wait:
                    raise people.PeopleError(TOO_MANY_CODES, seconds=wait)  # the code isn't looked at
                return take(code)
            except people.PeopleError as exc:
                if isinstance(exc, people.UnknownCode):
                    app.code_attempts.wrong(who)
                self._signin_refused(exc, wait)
                return None

        def _signin_ask(self, code: str, page: str | None = None):
            """GET /signin?code=: an invite link, or a sign-in link a person's own Interlatch asked for. Says who the
            code signs in as and asks first: a link alone never signs a browser in, or another site could sign yours in
            as someone else (a login CSRF). The code isn't used up here."""
            import html

            from ...sharing import people

            person = self._signin_code(code, lambda c: people.code_person(app.conn, c))
            if person is None:
                return
            name, email = person["name"], person.get("email") or ""
            hidden = "".join(f'<input type="hidden" name="{k}" value="{html.escape(v)}">'
                             for k, v in (("code", code), ("next", page)) if v)
            self._signin_page(200, tr("Sign in to this hub"),
                              f'<div class="signin-who"><span class="signin-avatar" aria-hidden="true">'
                              f"{html.escape(name[:1].upper())}</span><div><b>{html.escape(name)}</b>"
                              f'{f"<span>{html.escape(email)}</span>" if email else ""}</div></div>'
                              f'<p class="signin-check">{SHIELD}<span>{html.escape(tr(SIGNIN_CHECK))}</span></p>'
                              f'<form method="post" action="/signin">{hidden}<button class="btn primary signin-go" '
                              f'type="submit">{html.escape(tr("Sign in as {name}", name=name))}</button></form>', switch_lang=True)

        def _signin(self, code: str, page: str | None = None):
            """POST /signin, from the page above or the dashboard's own sign-in form: opens a browser session, on the
            page the link's next names (people.landing) or Home. Only a page of this dashboard may ask."""
            from ...sharing import people

            if not self._same_origin():
                return self._signin_refused(people.PeopleError("open the sign-in link again"), status=403)
            old_names = [n for n, _ in self._ours() if n in LEGACY_COOKIES]  # before they end, below
            opened = self._signin_code(code, lambda c: people.open_browser(
                app.conn, c, label=self.headers.get("User-Agent"), replaces=self._sessions()))  # this browser's old session ends
            if opened is None:
                return
            self.cookies_out = [self._session_cookie(opened[1], people.BROWSER_DAYS * 86400),
                                *(self._clear_cookie(n) for n in dict.fromkeys(old_names))]
            self.send_response(302)
            self.send_header("Location", people.landing(page))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _open_file(self, f: dict):
            from urllib.parse import quote

            # the name came from a transcript: no control characters (a line break would end the header)
            ascii_name = re.sub(r"[\x00-\x1f\x7f]", "_", f["name"].encode("ascii", "replace").decode())
            ascii_name = ascii_name.replace("?", "_").replace('"', "'").replace("\\", "_")
            self.send_response(200)
            self.send_header("Content-Type", f["ctype"])
            self.send_header("Content-Disposition", f"{'inline' if f['inline'] else 'attachment'}; filename=\"{ascii_name}\"; "
                                                    f"filename*=UTF-8''{quote(f['name'])}")
            self.send_header("Content-Length", str(len(f["body"])))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if f["policy"]:
                self.send_header("Content-Security-Policy", f["policy"])
            self.send_header("X-Interlatch-Source", f["source"])  # "disk", or "archive": as the agent wrote it, now gone
            self.end_headers()
            self.wfile.write(f["body"])

        def _hub_api(self, p: str):
            """Another computer sending its sessions (sharing/receiver.py). Authenticated by the hub's shared token or a
            person's computer token (sharing.identity.authorize), not a browser header; joining with an invite needs
            neither."""
            from ...sharing.identity import authorize
            from ...sharing.protocol import HubError, UnknownInvite
            from ...sharing.receiver import (_drain, check_machine, hello, join_with_code, receive_analyses,
                                             receive_file, receive_sessions, signin_code_for, team_lessons,
                                             transcripts_refused, withdraw)

            q = {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items()}
            length = int(self.headers.get("Content-Length") or 0)
            if p == "/api/hub/join":  # the invite code is the credential
                if length > 65536:
                    return self._json({"error": "too large"}, 413)
                who = self._client()
                try:
                    body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                    if not isinstance(body, dict):
                        return self._json({"error": "bad json"}, 400)
                    if wait := app.code_attempts.wait(who):  # after a few unknown codes from here: not looked at
                        return self._json({"error": tr(TOO_MANY_CODES, seconds=wait)}, 429, headers={"Retry-After": str(wait)})
                    return self._json(join_with_code(app.cfg, app.conn, body))
                except UnknownInvite as exc:
                    app.code_attempts.wrong(who)
                    return self._json({"error": str(exc)}, 400)
                except (HubError, ValueError) as exc:
                    return self._json({"error": str(exc)}, 400)
            auth = self.headers.get("Authorization")
            ok, person = authorize(app.cfg, app.conn, auth, q.get("machine"))  # before reading what was sent
            if not ok:
                return self._json({"error": "unauthorized"}, 401)
            if person and person["role"] == "readonly" and p in HUB_PUSH:
                return self._json({"error": "read-only people can't send to the hub"}, 403)
            if p in ("/api/hub/file", "/api/hub/analyses") and (refused := transcripts_refused(app.cfg, person)):
                _drain(self.rfile, length)  # transcripts this hub doesn't take: never kept
                return self._json({"error": str(refused)}, 403)
            try:
                if p == "/api/hub/file":
                    return self._json(receive_file(app.cfg, app.conn, q, self.rfile, length))
                if p == "/api/hub/analyses":
                    return self._json(receive_analyses(app.cfg, app.conn, q, self.rfile, length))
                if p == "/api/hub/sessions":
                    return self._json(receive_sessions(app.cfg, app.conn, q, self.rfile, length, person))
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                if not isinstance(body, dict):
                    return self._json({"error": "bad json"}, 400)
                if body.get("machine"):  # a person's token works only from the computer it was issued to, and the
                    # shared token not as a computer that joined as a person: the body names the computer acting
                    ok, person = authorize(app.cfg, app.conn, auth, str(body["machine"]))
                    if not ok:
                        return self._json({"error": "unauthorized"}, 401)
                if p == "/api/hub/signin":
                    return self._json(signin_code_for(app.cfg, app.conn, person))
                if p == "/api/hub/hello":
                    return self._json(hello(app.cfg, app.conn, body, person))
                if p == "/api/hub/lessons":
                    return self._json(team_lessons(app.cfg, body, person, app.conn))
                if p == "/api/hub/withdraw":  # the computer named in the body, which a person's token is bound to
                    if not body.get("machine"):
                        return self._json({"error": "bad machine id"}, 400)
                    return self._json(withdraw(app.cfg, app.conn, body, person))
                if p == "/api/hub/done":
                    check_machine(app.cfg, str(body.get("machine") or ""))
                    app.ingest.request()
                    return self._json({"ok": True})
            except (HubError, ValueError) as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"error": "not found"}, 404)

        def _json(self, data, status=200, headers: dict | None = None):
            body = json.dumps(data, ensure_ascii=False, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _download(self, name: str, ctype: str, body: bytes):
            from urllib.parse import quote

            ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "'")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition", f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _image(self, body: bytes, ctype: str):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=300")
            self.send_header("X-Content-Type-Options", "nosniff")
            # an SVG is a document that can carry script: opened on its own, it renders in a sandbox that allows nothing
            self.send_header("Content-Security-Policy", "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox")
            self.end_headers()
            self.wfile.write(body)

        def _static(self, rel: str):
            name = rel.lstrip("/") or "index.html"
            if name not in WEB_FILES:
                name = "index.html"
            body = WEB_FILES[name]
            self.send_response(200)
            self.send_header("Content-Type", web_type(name))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            if name == "index.html":
                app_window = parse_qs(urlparse(self.path).query).get("app") == ["mac"] and self._from_here()
                self.send_header("Content-Security-Policy", PAGE_CSP.format(eval=" 'unsafe-eval'" if app_window else ""))
            elif name == "panel.html":  # the menu-bar panel (menubar.py): the same rules, no eval
                self.send_header("Content-Security-Policy", PAGE_CSP.format(eval=""))
            self.end_headers()
            self.wfile.write(body)

        def _asked_lang(self) -> str | None:
            """The X-Interlatch-Lang header, or X-Chronicle-Lang from a tab opened before the rename."""
            return self.headers.get("X-Interlatch-Lang") or self.headers.get("X-Chronicle-Lang")

        def do_GET(self):
            token = i18n.lang.set(i18n.pick(self._asked_lang()))  # the dashboard's language, per browser
            try:
                app.refresh_config()
                self.signed_in, self.cookies_out = "", []
                return self._get()
            finally:
                i18n.lang.reset(token)
                app.release()

        def do_POST(self):
            token = i18n.lang.set(i18n.pick(self._asked_lang()))
            try:
                app.refresh_config()
                self.signed_in, self.cookies_out = "", []
                return self._post()
            finally:
                i18n.lang.reset(token)
                app.release()

        def _get(self):
            ctx = app.context()
            if not self._host_ok():
                return self._json({"error": tr("forbidden host")}, 403)
            if not self._user_ok() and not self._people_mode():  # with people, _viewer says who may see what
                return self._json({"error": tr("this Tailscale login is not allowed ([server] allowed_users)")}, 403)
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            p = url.path
            try:
                if p == "/signin":
                    return self._signin_ask(q.get("code", ""), q.get("next"))
                if p.startswith("/api/"):  # the page itself stays public, so it can show how to sign in
                    self.viewer, refused = self._viewer()
                    if refused:
                        return self._json(*refused)
                    from ...sharing import access, people

                    scope = people.projects_of(self.viewer)
                    if scope is not None:  # someone limited to projects: a few pages, of their projects only
                        if not access.allowed(p):
                            return self._json({"error": tr("you see only some projects on this hub"), "limited": True}, 403)
                        access.limit(app.conn, scope)
                        self.limited = True
                route, m = routes.find(routes.GET, p)
                if route:
                    if why := route.access.refused(self, ctx):
                        return self._json({"error": tr(why)}, 403)
                    return route.answer(self, ctx, m, q)
                if p.startswith("/api/"):
                    return self._json({"error": tr("not found")}, 404)
                return self._static(p)
            except BrokenPipeError:
                pass
            except Exception as exc:
                log.exception("GET %s failed", p)
                return self._json({"error": str(exc)}, 500)

        def _import_upload(self):
            """A claude.ai or ChatGPT export .zip, streamed to disk (it can be hundreds of MB), then imported in the background."""
            ctx = app.context()
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return self._json({"error": tr("empty upload")}, 400)
            dest = app.cfg.home / "imports" / f"chat-export-{int(time.time())}.zip"
            dest.parent.mkdir(parents=True, exist_ok=True)
            left = length
            with open(dest, "wb") as f:
                while left:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            if left:
                dest.unlink(missing_ok=True)
                return self._json({"error": tr("upload cut off")}, 400)
            name = Path(unquote(self.headers.get("X-Filename") or "")).name or None  # the file's own name, for messages
            started = api.sources.action_import(ctx, dest, name)
            if not started:
                dest.unlink(missing_ok=True)
            return self._json({"started": started})

        def _small_body(self, limit: int = 4096):
            """The JSON body of a POST any signed-in person may make: at most `limit` bytes, else None. A missing,
            negative or unreadable Content-Length is refused before anything is read (read(-1) would read until the
            caller hangs up)."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 <= length <= limit:
                    return None
                return json.loads(self.rfile.read(length) or b"{}") if length else {}
            except ValueError:
                return None

        def _form(self, limit: int = 4096) -> dict | None:
            """A small urlencoded form body (the /signin form), else None."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 <= length <= limit:
                    return None
                return {k: v[-1] for k, v in parse_qs(self.rfile.read(length).decode() if length else "").items()}
            except (ValueError, UnicodeDecodeError):
                return None

        def _party_own(self, ctx, p: str):
            """A Party quest marked done, or the goal: the viewer's own log, so members and read-only people may too.
            The route's access still decides, and someone limited to projects is answered from those projects only,
            as on GET."""
            from ...sharing import access, people

            body = self._small_body()
            if body is None:
                return self._json({"error": tr("bad json")}, 400)
            try:
                route, m = routes.find(routes.POST, p)
                if why := route.access.refused(self, ctx):
                    return self._json({"error": tr(why)}, 403)
                scope = people.projects_of(self.viewer)
                if scope is not None:
                    access.limit(app.conn, scope)
                    self.limited = True
                return route.answer(self, ctx, m, body)
            except Exception as exc:
                log.exception("POST %s failed", p)
                return self._json({"error": str(exc)}, 500)

        def _post(self):
            ctx = app.context()
            if not self._host_ok():
                return self._json({"error": tr("forbidden")}, 403)
            if urlparse(self.path).path.startswith("/api/hub/"):
                return self._hub_api(urlparse(self.path).path)
            if urlparse(self.path).path == "/signin":  # a form, so no X-Interlatch header: _signin checks the origin
                if not self._user_ok() and not self._people_mode():
                    return self._json({"error": tr("forbidden")}, 403)
                form = self._form()
                if form is None:
                    return self._json({"error": tr("bad form")}, 400)
                return self._signin(form.get("code", ""), form.get("next"))
            if "1" not in (self.headers.get("X-Interlatch"), self.headers.get("X-Chronicle")) or not (self._user_ok() or self._people_mode()):
                return self._json({"error": tr("forbidden")}, 403)
            if urlparse(self.path).path == "/api/signout":
                from ...sharing import people

                # every session cookie of this hub's that the browser sent ends and is cleared: its own name's, and
                # an old name's not replaced yet; an old name holding another hub's session is left to that hub
                old_names = [n for n, v in self._ours() if n in LEGACY_COOKIES]
                for _, value in self._session_cookies():
                    people.sign_out(app.conn, value)  # no-op for a value that isn't a browser session here
                current = self._cookie_name()
                self.cookies_out = [self._clear_cookie(n) for n in dict.fromkeys(([current] if current else []) + old_names)]
                return self._json({"ok": True})
            self.viewer, refused = self._viewer()
            if refused:
                return self._json(*refused)
            if urlparse(self.path).path == "/api/team/news/seen":  # anyone who sees the Team page: their own news only
                body = self._small_body()
                if body is None:
                    return self._json({"error": tr("bad json")}, 400)
                news.mark_seen(app.conn, self.viewer, body.get("at") if isinstance(body, dict) else None)
                return self._json({"ok": True})
            if urlparse(self.path).path in PARTY_OWN:  # anyone who sees the Party: their own quests and goal only
                return self._party_own(ctx, urlparse(self.path).path)
            if self.viewer is not None and self.viewer["role"] != "admin":  # members and read-only people look
                return self._json({"error": tr("only an admin of this hub can do this")}, 403)
            if urlparse(self.path).path == "/api/import":
                return self._import_upload()
            length = int(self.headers.get("Content-Length") or 0)
            body = {}
            if length:
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._json({"error": tr("bad json")}, 400)
            p = urlparse(self.path).path
            try:
                route, m = routes.find(routes.POST, p)
                if not route:
                    return self._json({"error": tr("not found")}, 404)
                if why := route.access.refused(self, ctx):
                    return self._json({"error": tr(why)}, 403)
                return route.answer(self, ctx, m, body)
            except Exception as exc:
                log.exception("POST %s failed", p)
                return self._json({"error": str(exc)}, 500)

    return Handler
