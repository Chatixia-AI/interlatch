"""The dashboard's HTTP side: which host and origin may ask, who is asking (this computer, a person signed in to
a hub, another computer sending sessions), and which App method answers each path."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ... import __version__
from ...core import i18n
from ...core.i18n import tr
from ...knowledge.synthesize import GLOBAL
from ...sharing import news
from .app import App
from .assets import PAGE_CSP, WEB_FILES
from . import api

LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
SESSION_COOKIE = "interlatch_session"  # a person's dashboard session on a hub (people.py)
LEGACY_SESSION_COOKIE = "chronicle_session"  # the same, signed in before the rename: still honoured until it expires

# headers a proxy adds: a request with any of them came through one, so it is not from someone at this computer
PROXY_HEADERS = ("X-Forwarded-For", "Tailscale-User-Login", "X-Forwarded-Proto", "X-Real-IP", "Forwarded")
HUB_PUSH = ("/api/hub/file", "/api/hub/sessions", "/api/hub/analyses", "/api/hub/done", "/api/hub/hello",
            "/api/hub/withdraw")

log = logging.getLogger("interlatch.server")
SIGNIN_HELP = ("Ask an admin of this hub for a new invite, or open the hub's dashboard again from your own Interlatch "
               "(Settings › Devices).")
TOO_MANY_CODES = "too many wrong codes from this address; try again in {seconds} s"  # people.CodeAttempts


def make_handler(app: App, port: int):
    local_hosts = {f"{h}:{port}" for h in LOOPBACK}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"interlatch/{__version__}"
        viewer: dict | None = None  # who is viewing (_viewer), None meaning an admin
        limited = False  # the viewer sees only some projects (access.py): set per request in _get

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

        def _session(self) -> str:
            from http.cookies import CookieError, SimpleCookie

            try:
                jar = SimpleCookie(self.headers.get("Cookie") or "")
            except CookieError:
                return ""
            morsel = jar.get(SESSION_COOKIE) or jar.get(LEGACY_SESSION_COOKIE)
            return morsel.value if morsel else ""

        def _session_cookie(self, value: str, max_age: int) -> str:
            secure = "; Secure" if self._proxied_https() else ""
            return f"{SESSION_COOKIE}={value}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}{secure}"

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
            person = people.browser_person(app.conn, self._session())
            if person:
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
                    "people_mode": people.has_people(app.conn), "can_admin": self._can_admin(), "hub": api.team.hub_info(ctx)}

        def _address(self) -> str:
            """The hub's address as others reach it: [hub] address, or a guess from how this page was opened."""
            if app.cfg.hub_address:
                return app.cfg.hub_address
            return f"{'https' if self._proxied_https() else 'http'}://{self._host() or '127.0.0.1'}"

        def _signin(self, code: str, page: str | None = None):
            """An invite link, or a sign-in link a person's own Interlatch asked for: opens a browser session, on the page
            the link's next names (people.landing) or Home. After a few unknown codes from one address, its next tries
            wait their turn (people.CodeAttempts)."""
            import html

            from ...sharing import people

            accept = (self.headers.get("Accept-Language") or "").strip().lower()
            i18n.lang.set("ja" if accept.startswith("ja") else "en")  # a link opened by hand sends no X-Interlatch-Lang
            who = self._client()
            wait = app.code_attempts.wait(who)
            try:
                if wait:
                    raise people.PeopleError(TOO_MANY_CODES, seconds=wait)  # the code isn't looked at
                _person, session = people.open_browser(app.conn, code, label=self.headers.get("User-Agent"))
            except people.PeopleError as exc:
                if isinstance(exc, people.UnknownCode):
                    app.code_attempts.wrong(who)
                title = html.escape(tr("Could not sign in"))
                body = (f'<!doctype html><html lang="{i18n.lang.get()}"><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width, initial-scale=1">'
                        f'<meta name="color-scheme" content="light dark"><title>{title} · Interlatch</title>'
                        '<body style="font: 16px/1.5 system-ui, sans-serif; max-width: 36rem; margin: 12vh auto; '
                        f'padding: 0 16px"><h1 style="font-size: 1.3rem">{title}</h1><p>{html.escape(exc.shown())}</p>'
                        f"<p>{html.escape(tr(SIGNIN_HELP))}</p>"
                        f'<p><a href="/">{html.escape(tr("Open the dashboard"))}</a></p></body></html>').encode()
                self.send_response(429 if wait else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if wait:
                    self.send_header("Retry-After", str(wait))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(302)
            self.send_header("Location", people.landing(page))
            self.send_header("Set-Cookie", self._session_cookie(session, people.BROWSER_DAYS * 86400))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")  # the code is in this page's address
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
            """Another computer sending its sessions (hub.py). Authenticated by the hub's shared token or a person's
            computer token (hub.authorize), not a browser header; joining with an invite needs neither."""
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

        def _json(self, data, status=200, cookie: str | None = None, headers: dict | None = None):
            body = json.dumps(data, ensure_ascii=False, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            if cookie:
                self.send_header("Set-Cookie", cookie)
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
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
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
                return self._get()
            finally:
                i18n.lang.reset(token)
                app.release()

        def do_POST(self):
            token = i18n.lang.set(i18n.pick(self._asked_lang()))
            try:
                app.refresh_config()
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
                    return self._signin(q.get("code", ""), q.get("next"))
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
                if p == "/api/me":
                    return self._json(self._me())
                if p == "/api/people":
                    if not self._can_admin():
                        return self._json({"error": tr("only an admin of this hub can do this")}, 403)
                    return self._json(api.hub_admin.people_info(ctx))
                if p == "/api/overview":
                    return self._json(api.status.overview(ctx, q, limited=self.limited))
                if p == "/api/leaderboard" or p.startswith("/api/leaderboard/"):  # a person's own computer only
                    if not self._from_here():
                        return self._json({"error": tr("the leaderboard is on the dashboard of your own computer")}, 403)
                    if p == "/api/leaderboard":
                        return self._json(api.leaderboard.leaderboard(ctx, q))
                    m = re.fullmatch(r"/api/leaderboard/avatar/([\w-]+)", p)
                    if m:
                        from ...sharing.leaderboard import avatar

                        got = avatar(m.group(1), app.cfg)
                        return self._image(*got) if got else self._json({"error": "no avatar"}, 404)
                if p == "/api/team/who":
                    return self._json(api.team.who_options(ctx))
                if p == "/api/team":
                    return self._json(api.team.team(ctx, q, scope=people.projects_of(self.viewer), admin=self._can_admin(),
                                                    viewer=self.viewer))
                if p == "/api/team/news":
                    after = q.get("after") if news.ISO_RE.fullmatch(q.get("after") or "") else None
                    return self._json(api.team.team_news(ctx, self.viewer, scope=people.projects_of(self.viewer),
                                                         admin=self._can_admin(), after=after))
                if p == "/api/sessions":
                    return self._json(api.sessions.sessions(ctx, q, limited=self.limited))
                if p == "/api/export":  # a download: one session's file, or a .zip of several
                    from ..exports.sessions import ExportError

                    try:
                        if q.get("check"):  # asked first, so a refusal shows as a message, not a downloaded error
                            return self._json(api.sessions.export_check(ctx, q.get("ids", ""), q.get("format", "md")))
                        return self._download(*api.sessions.export(ctx, q.get("ids", ""), q.get("format", "md")))
                    except ExportError as exc:
                        return self._json({"error": str(exc)}, 400)
                m = re.fullmatch(r"/api/sessions/([\w-]+)", p)
                if m:
                    if self.limited:
                        s = api.sessions.session_summary(ctx, m.group(1))
                        return self._json(s) if s else self._json({"error": tr("not found")}, 404)
                    s = api.sessions.session(ctx, m.group(1))
                    return self._json({**s, "local": self._local()}) if s else self._json({"error": tr("not found")}, 404)
                m = re.fullmatch(r"/api/sessions/([\w-]+)/events", p)
                if m:
                    return self._json(api.sessions.events(ctx, m.group(1), q))
                m = re.fullmatch(r"/api/sessions/([\w-]+)/matches", p)
                if m:
                    return self._json(api.sessions.matches(ctx, m.group(1), q))
                if p == "/api/projects":
                    return self._json(api.projects.projects(ctx, scope=people.projects_of(self.viewer)))
                if p == "/api/project-groups":  # can_share: what leaves this computer changes only from here
                    return self._json({**api.projects.project_groups(ctx), "can_share": app.cfg.is_spoke and self._from_here()})
                if p == "/api/artifacts":
                    return self._json({**api.artifacts.artifacts(ctx, q), "local": self._local()})
                m = re.fullmatch(r"/api/artifacts/(\d+)/open", p)
                if m:  # the file itself, in a new tab: from disk, or as the agent wrote it once it is gone
                    f = api.artifacts.artifact_open(ctx, int(m.group(1)))
                    return self._open_file(f) if f else self._json({"error": tr("this file can no longer be opened")}, 404)
                m = re.fullmatch(r"/api/artifacts/(\d+)/file", p)
                if m:  # an image or diagram an agent made, still on disk: shown as a thumbnail
                    f = api.artifacts.artifact_file(ctx, int(m.group(1)))
                    return self._image(*f) if f else self._json({"error": tr("not available")}, 404)
                if p == "/api/project":
                    proj = api.projects.project(ctx, unquote(q.get("path", "")))
                    return self._json({**proj, "local": self._local()}) if proj else self._json({"error": tr("not found")}, 404)
                if p == "/api/diagram":  # a download: the project's architecture sketch as an .excalidraw file
                    f = api.knowledge.diagram_file(ctx, q.get("path", ""))
                    return self._download(*f) if f else self._json({"error": tr("no architecture sketch for this project")}, 404)
                if p == "/api/file":  # parse_qs has decoded the path already: a second unquote would mangle a literal %
                    return self._json(api.projects.file_sessions(ctx, q.get("path", ""), min(int(q.get("limit") or 50), 500)))
                if p == "/api/files":
                    return self._json(api.projects.folder_files(ctx, q.get("root", ""), min(int(q.get("limit") or 200), 2000),
                                                                existing=q.get("existing") == "1",
                                                                ignored=q.get("ignored") != "0"))
                if p == "/api/knowledge":
                    return self._json(api.knowledge.knowledge(ctx, q))
                if p == "/api/learning/interests":
                    return self._json(api.knowledge.learning_interests(ctx, self.viewer, q))
                if p == "/api/learning/glance":
                    return self._json(api.knowledge.learning_glance(ctx, self.viewer, q))
                if p == "/api/knowledge/hub":
                    return self._json(api.knowledge.knowledge_hub(ctx))
                if p == "/api/search":
                    return self._json(api.sessions.search(ctx, q))
                if p == "/api/status":
                    return self._json(api.status.status(ctx))
                if p == "/api/reviews":
                    return self._json(api.knowledge.reviews(ctx))
                if p == "/api/glossary":
                    return self._json(api.knowledge.glossary(ctx, q))
                if p == "/api/connectors":
                    return self._json(api.sources.connectors(ctx))
                if p == "/api/mcp":
                    return self._json(api.sources.mcp_info())
                if p == "/api/mcp-clients":
                    return self._json(api.sources.mcp_clients())
                if p == "/api/glossary/terms":
                    return self._json(api.knowledge.glossary_terms(ctx))
                if p == "/api/map":
                    return self._json(api.knowledge.map(ctx))
                if p == "/api/systems":
                    return self._json(api.knowledge.systems(ctx))
                if p == "/api/system":
                    found = api.knowledge.system(ctx, q.get("id", ""))
                    return self._json(found) if found else self._json({"error": tr("not found")}, 404)
                if p == "/api/jobs":
                    return self._json(api.status.status_small(ctx, limited=self.limited))
                if p == "/api/update":
                    return self._json(api.settings.update_info(ctx, remote=False))
                if p == "/api/imports":
                    return self._json(api.sources.imports(ctx))
                if p == "/api/devices":
                    dv = api.devices.devices(ctx)
                    if self.viewer is not None and not self._can_admin():  # computers, keys and the team store: admins'
                        dv = {**dv, "this": {"name": dv["this"]["name"]}, "machines": [], "store": None,
                              "allowed_hosts": [], "allowed_users": [], "path_map": {}, "folders": [], "projects": [],
                              "team": None, "last_push": None}
                    return self._json({**dv, "here": self._from_here(), **self._me()})
                if p == "/api/team-store":
                    if self.viewer is not None and not self._can_admin():  # its database, host and counts: admins'
                        return self._json({"error": tr("only an admin of this hub can do this")}, 403)
                    return self._json(api.hub_admin.team_store_status(ctx))
                if p in ("/api/mirror", "/api/mirror/status"):
                    if not self._can_admin():  # its address and user: at the computer itself, or an admin of a hub
                        return self._json({"error": tr("change this on the computer itself, not from another device")}, 403)
                    return self._json(api.storage.mirror_info(ctx) if p == "/api/mirror" else api.storage.mirror_status(ctx))
                if p == "/api/team/settings":
                    if not self._can_admin():
                        return self._json({"error": tr("only an admin of this hub can do this")}, 403)
                    return self._json(api.hub_admin.hub_settings(ctx))
                if p == "/api/projects/shared":
                    if not self._can_admin():
                        return self._json({"error": tr("only an admin of this hub can do this")}, 403)
                    return self._json({**api.devices.shared_projects(ctx), "here": self._from_here()})
                if p == "/api/suggestions":
                    return self._json(api.knowledge.suggestions(ctx, q))
                if p == "/api/suggestions/unseen":
                    from ...knowledge.suggest import count_unseen

                    return self._json({"unseen": count_unseen(app.conn)})
                m = re.fullmatch(r"/api/suggestions/(\d+)/preview", p)
                if m:
                    return self._json(*api.knowledge.suggestion_preview(ctx, int(m.group(1)), q))
                if p == "/api/friction":
                    return self._json(api.knowledge.friction(ctx, q))
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

        def _post(self):
            ctx = app.context()
            if not self._host_ok():
                return self._json({"error": tr("forbidden")}, 403)
            if urlparse(self.path).path.startswith("/api/hub/"):
                return self._hub_api(urlparse(self.path).path)
            if "1" not in (self.headers.get("X-Interlatch"), self.headers.get("X-Chronicle")) or not (self._user_ok() or self._people_mode()):
                return self._json({"error": tr("forbidden")}, 403)
            if urlparse(self.path).path == "/api/signout":
                from ...sharing import people

                if self._session():
                    people.sign_out(app.conn, self._session())
                return self._json({"ok": True}, cookie=self._session_cookie("", 0))
            self.viewer, refused = self._viewer()
            if refused:
                return self._json(*refused)
            if urlparse(self.path).path == "/api/team/news/seen":  # anyone who sees the Team page: their own news only
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    if length > 4096:
                        raise ValueError
                    body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                except ValueError:
                    return self._json({"error": tr("bad json")}, 400)
                news.mark_seen(app.conn, self.viewer, body.get("at") if isinstance(body, dict) else None)
                return self._json({"ok": True})
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
                if p == "/api/sync":
                    return self._json({"started": api.status.action_sync(ctx)})
                m = re.fullmatch(r"/api/leaderboard/(signin|finish|cancel|send|name|leave)", p)
                if m:  # signing in keeps a credential on this computer: only from here
                    from ...sharing.leaderboard import LeaderboardError

                    if not self._from_here():
                        return self._json({"error": tr("the leaderboard is on the dashboard of your own computer")}, 403)
                    body = body if isinstance(body, dict) else {}
                    try:
                        if m.group(1) == "signin":  # the hosted page comes back to this dashboard, as it was opened
                            return self._json(api.leaderboard.action_leaderboard_signin(ctx, f"http://{self._host()}/", i18n.lang.get()))
                        if m.group(1) == "finish":
                            return self._json(api.leaderboard.action_leaderboard_finish(ctx, body))
                        if m.group(1) == "cancel":
                            api.leaderboard.action_leaderboard_cancel(ctx)
                            return self._json({"ok": True})
                        if m.group(1) == "name":
                            return self._json(api.leaderboard.action_leaderboard_name(ctx, body))
                        return self._json(api.leaderboard.action_leaderboard_send(ctx) if m.group(1) == "send" else api.leaderboard.action_leaderboard_leave(ctx))
                    except LeaderboardError as e:
                        return self._json({"error": str(e)}, 502)
                m = re.fullmatch(r"/api/artifacts/(\d+)/reveal", p)
                if m:  # open the file in its own app, or show it in Finder: only from this computer
                    if not self._local():
                        return self._json({"error": tr("files open only on the computer Interlatch runs on")}, 403)
                    err = api.artifacts.artifact_reveal(ctx, int(m.group(1)), "reveal" if body.get("how") == "reveal" else "open")
                    return self._json({"error": err}, 400) if err else self._json({"ok": True})
                if p in ("/api/team-store/test", "/api/team-store/save"):
                    if not self._can_admin():  # a password: at the hub itself, or an admin of a hub with people
                        return self._json({"error": tr("change this on the computer itself, not from another device")}, 403)
                    if p == "/api/team-store/test":
                        return self._json(api.hub_admin.action_team_store_test(ctx, body))
                    return self._json(api.hub_admin.action_team_store_save(ctx, body))
                if p in ("/api/mirror/test", "/api/mirror/save", "/api/mirror/sync"):
                    if not self._can_admin():  # a password, and where this archive goes: as for the team store
                        return self._json({"error": tr("change this on the computer itself, not from another device")}, 403)
                    if p == "/api/mirror/test":
                        return self._json(api.storage.action_mirror_test(ctx, body))
                    if p == "/api/mirror/sync":
                        return self._json({"started": api.storage.action_mirror_sync(ctx)})
                    return self._json(api.storage.action_mirror_save(ctx, body))
                if p in ("/api/devices/share", "/api/devices/hub-signin"):
                    if not self._from_here():  # what leaves this computer, or its own sign-in: only from here
                        return self._json({"error": tr("change this on the computer itself, not from another device")}, 403)
                    if p == "/api/devices/hub-signin":
                        from ...sharing.client import dashboard_signin
                        from ...sharing.protocol import HubError

                        try:
                            return self._json({"url": dashboard_signin(app.cfg, page=body.get("next"))})
                        except HubError as exc:
                            return self._json({"error": tr(str(exc))}, 400)
                    if "all_folders" in body:
                        return self._json(api.devices.action_share_folders(ctx, body.get("all_folders")))
                    return self._json(api.devices.action_share_mode(ctx, str(body.get("share") or "")))
                m = re.fullmatch(r"/api/devices/projects/(choices|add|remove|leave|rejoin)", p)
                if m or p == "/api/devices/leave-hub":  # what leaves this computer, and for which projects: only from here
                    if not self._from_here():
                        return self._json({"error": tr("change this on the computer itself, not from another device")}, 403)
                    if p == "/api/devices/leave-hub":
                        return self._json(api.devices.action_leave_hub(ctx))
                    if m.group(1) == "choices":
                        return self._json(api.devices.hub_project_choices(ctx))
                    return self._json(*api.devices.action_hub_project(ctx, m.group(1), body if isinstance(body, dict) else {}))
                if p == "/api/team/settings":
                    if not self._can_admin():
                        return self._json({"error": tr("only an admin of this hub can do this")}, 403)
                    return self._json(*api.hub_admin.action_hub_settings(ctx, body if isinstance(body, dict) else {}, self.viewer))
                m = re.fullmatch(r"/api/projects/shared/(add|remove)", p)
                if m:  # what leaves this computer (its sessions go to the team store): only from here. A dedicated hub
                    # has no sessions of its own, so there its admins may make (by name) and remove projects too.
                    here = self._from_here()
                    if not here and not (app.cfg.hub_dedicated and self._can_admin()):
                        return self._json({"error": tr("change this on the computer itself, not from another device")}, 403)
                    return self._json(*api.devices.action_shared_project(ctx, m.group(1), body if isinstance(body, dict) else {},
                                                                         by=self.viewer, here=here))
                m = re.fullmatch(r"/api/project-groups/(save|delete|move|share)", p)
                if m:
                    if m.group(1) == "share" and not self._from_here():  # what leaves this computer: only from here
                        return self._json({"error": tr("change this on the computer itself, not from another device")}, 403)
                    return self._json(*api.projects.action_project_groups(ctx, m.group(1), body if isinstance(body, dict) else {}))
                m = re.fullmatch(r"/api/people/(add|invite|role|access|remove|revoke|shared-token)", p)
                if m:
                    if not self._can_admin():
                        return self._json({"error": tr("only an admin of this hub can do this")}, 403)
                    return self._json(*api.hub_admin.action_people(ctx, m.group(1), body if isinstance(body, dict) else {},
                                                                   self.viewer, self._address()))
                if p == "/api/team/accept":  # what this hub takes from the computers that send to it
                    if not self._can_admin():
                        return self._json({"error": tr("only an admin of this hub can do this")}, 403)
                    return self._json(*api.devices.action_accept(ctx, str(body.get("accept") or ""), self.viewer))
                if p == "/api/devices/push":
                    return self._json({"started": api.devices.action_push(ctx)})
                if p == "/api/update/check":  # a POST: with the daily check off, the dashboard's only call to PyPI
                    return self._json(api.settings.update_info(ctx, remote=True))
                if p == "/api/analysis/backend":
                    return self._json(api.settings.action_backend(ctx, str(body.get("backend") or "")))
                if p == "/api/analysis/agent":
                    return self._json(api.settings.action_agent(ctx, body if isinstance(body, dict) else {}))
                if p in ("/api/analysis/provider", "/api/analysis/provider/test", "/api/analysis/provider/models"):
                    if not self._can_admin():  # an API key, and where transcripts are sent
                        return self._json({"error": tr("change this on the computer itself, not from another device")}, 403)
                    if p == "/api/analysis/provider/test":
                        return self._json(api.settings.action_provider_test(ctx, str(body.get("provider") or "")))
                    if p == "/api/analysis/provider/models":
                        return self._json(api.settings.provider_models(ctx, str(body.get("provider") or "")))
                    return self._json(api.settings.action_provider(ctx, body if isinstance(body, dict) else {}))
                if p == "/api/analysis/language":
                    return self._json(api.settings.action_language(ctx, str(body.get("language") or "")))
                if p == "/api/update/daily":
                    return self._json(api.settings.action_update_setting(ctx, "check_daily", bool(body.get("on"))))
                if p == "/api/update/notify":
                    return self._json(api.settings.action_update_setting(ctx, "notify", bool(body.get("on"))))
                if p == "/api/update":
                    return self._json(api.settings.action_update(ctx))
                if p == "/api/menu-bar":  # installs software and restarts the dashboard: only from this computer
                    if not self._from_here():
                        return self._json({"error": tr("change this on the computer itself, not from another device")}, 403)
                    return self._json(api.settings.action_menu_bar(ctx, bool(body.get("on"))))
                if p == "/api/analysis/run":  # the Queue's Analyze now
                    return self._json(api.status.action_analyze_queue(ctx, str(body.get("which") or "ready")))
                if p == "/api/sessions/analyze":
                    return self._json(api.status.action_analyze_many(ctx, list(body.get("ids") or [])))
                if p == "/api/screen":
                    return self._json({"started": api.sources.action_screen(ctx, body.get("source") or None, bool(body.get("redo")))})
                if p == "/api/screen/queue":
                    return self._json(api.sources.action_screen_queue(ctx, body.get("source") or None, bool(body.get("maybe"))))
                m = re.fullmatch(r"/api/sessions/([\w-]+)/analyze", p)
                if m:
                    return self._json({"started": api.status.action_analyze(ctx, m.group(1))})
                m = re.fullmatch(r"/api/connectors/([\w-]+)/(connect|disconnect)", p)
                if m:
                    try:
                        return self._json(api.sources.action_connector(ctx, m.group(1), m.group(2)))
                    except KeyError:
                        return self._json({"error": tr("unknown connector")}, 404)
                if p == "/api/map/themes":
                    return self._json({"started": api.knowledge.action_themes(ctx, bool(body.get("force")))})
                if p == "/api/glossary/rebuild":
                    return self._json({"started": api.knowledge.action_glossary(ctx, body.get("path") or None)})
                if p == "/api/review":
                    return self._json({"started": api.knowledge.action_review(ctx, body.get("week"))})
                if p == "/api/synthesize":
                    return self._json({"started": api.knowledge.action_synthesize(ctx, body.get("path") or GLOBAL)})
                m = re.fullmatch(r"/api/knowledge/(\d+)", p)
                if m:
                    return self._json({"ok": api.knowledge.action_knowledge(ctx, int(m.group(1)), body)})
                if p == "/api/suggestions/seen":
                    from ...knowledge.suggest import mark_seen

                    mark_seen(app.conn)
                    return self._json({"ok": True})
                if p == "/api/suggestions/refresh":
                    from ...knowledge.suggest import refresh

                    return self._json(refresh(app.conn, app.cfg))
                m = re.fullmatch(r"/api/suggestions/(\d+)/(apply|unapply|dismiss|done|edit|move)", p)
                if m:
                    return self._json(*api.knowledge.action_suggestion(ctx, int(m.group(1)), m.group(2), body if isinstance(body, dict) else {}))
                return self._json({"error": tr("not found")}, 404)
            except Exception as exc:
                log.exception("POST %s failed", p)
                return self._json({"error": str(exc)}, 500)

    return Handler
