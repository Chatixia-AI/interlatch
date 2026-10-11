"""People on a hub, from the dashboard's side: who is viewing (at the hub, a company sign-in, a session cookie), what
each role may do, signing in with an invite or sign-in link, the People API, and the hub API's person tokens."""

import json
import urllib.error
import urllib.request
from urllib.parse import quote

import pytest

from chronicle import hub, people
from chronicle.config import load_config, set_config_value

from test_hub import _serve

MACHINE = "11111111-2222-4333-8444-555555555555"
OTHER = "99999999-2222-4333-8444-555555555555"
REMOTE = {"X-Forwarded-For": "203.0.113.5"}  # not this computer: test requests all come from 127.0.0.1
TAILNET = {**REMOTE, "Tailscale-User-Login": "me@github"}  # a phone through Tailscale Serve, which names its login


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def _raw(url, path, body=None, headers=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json", "X-Interlatch": "1", **(headers or {})})
    try:
        with OPENER.open(req, timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def _call(url, path, body=None, headers=None):
    status, _, data = _raw(url, path, body, headers)
    return status, json.loads(data)


def _as(session, **extra):
    return {**REMOTE, "Cookie": f"interlatch_session={session}", **extra}


@pytest.fixture()
def hubweb(synced, monkeypatch):
    """The `synced` computer as a hub serving its dashboard on a free port; no people yet."""
    monkeypatch.setattr(hub.IngestTrigger, "request", lambda self: None)
    cfg = synced["cfg"]
    token = hub.new_token(cfg)
    app, httpd, url = _serve(cfg)
    synced.update(app=app, url=url, token=token)
    yield synced
    httpd.shutdown()


@pytest.fixture()
def team(hubweb):
    """Ada (admin), Bob (member) and Vic (read-only), each with a browser session."""
    conn = hubweb["conn"]
    ada = people.add(conn, "Ada", "ada@example.com", "admin")
    bob = people.add(conn, "Bob", "bob@example.com", "member", by=ada)
    vic = people.add(conn, "Vic", "vic@example.com", "readonly", by=ada)
    sessions = {p["name"]: people.open_browser(conn, people.invite(conn, p["id"]), "test")[1] for p in (ada, bob, vic)}
    hubweb.update(ada=ada, bob=bob, vic=vic, sessions=sessions)
    return hubweb


def test_a_hub_without_people_answers_as_before(hubweb):
    url = hubweb["url"]
    code, dv = _call(url, "/api/devices")
    assert code == 200 and dv["here"] and dv["can_admin"] and not dv["people_mode"] and dv["viewer"]["role"] == "admin"
    code, dv = _call(url, "/api/devices", headers=TAILNET)  # a Tailscale login: lets in, but hub settings stay here
    assert code == 200 and not dv["here"] and not dv["can_admin"] and not dv["viewer"]["here"]
    assert _call(url, "/api/suggestions/seen", {}, TAILNET) == (200, {"ok": True})
    assert _call(url, "/api/people", headers=TAILNET)[0] == 403
    assert _call(url, "/api/team-store/save", {"enabled": False}, TAILNET)[0] == 403
    code, me = _call(url, "/api/me")
    assert code == 200 and me["viewer"]["here"] and not me["people_mode"]
    assert _call(url, "/api/learning/interests") == (200, {"interests": []})
    status, headers, page = _raw(url, "/signin?code=AAAA-BBBB-CCCC")
    assert status == 400 and b"known on this hub" in page and headers["Content-Type"].startswith("text/html")


def test_who_may_look_and_who_may_change(team):
    url, s = team["url"], team["sessions"]
    code, r = _call(url, "/api/overview", headers=REMOTE)
    assert code == 401 and r["signin"] is True
    assert _call(url, "/api/overview")[0] == 200  # the hub computer itself is always an admin
    assert _raw(url, "/", headers=REMOTE)[0] == 200  # the page stays public, so it can show how to sign in
    assert _call(url, "/api/overview", headers=_as("not-a-session"))[0] == 401

    for name, role in (("Vic", "readonly"), ("Bob", "member")):
        code, me = _call(url, "/api/me", headers=_as(s[name]))
        assert code == 200 and me["viewer"]["name"] == name and me["viewer"]["role"] == role
        assert me["people_mode"] and not me["can_admin"] and not me["viewer"]["here"]
        assert _call(url, "/api/overview", headers=_as(s[name]))[0] == 200
        code, r = _call(url, "/api/suggestions/seen", {}, _as(s[name]))
        assert code == 403 and "admin" in r["error"]
        assert _call(url, "/api/people", headers=_as(s[name]))[0] == 403
        assert _call(url, "/api/team-store/save", {"enabled": False}, _as(s[name]))[0] == 403
        assert _call(url, "/api/mirror/save", {"enabled": False}, _as(s[name]))[0] == 403
        assert _call(url, "/api/mirror", headers=_as(s[name]))[0] == 403
    code, r = _call(url, "/api/suggestions/seen", {}, _as(s["Bob"], **{"X-Interlatch-Lang": "ja"}))
    assert r["error"] == "これができるのはこのハブの管理者だけです"
    # a browser signed in before the rename, in a tab opened then: its cookie and language header keep counting
    before = {**REMOTE, "Cookie": f"chronicle_session={s['Bob']}", "X-Chronicle-Lang": "ja"}
    assert _call(url, "/api/me", headers=before)[1]["viewer"]["name"] == "Bob"
    assert _call(url, "/api/suggestions/seen", {}, before)[1]["error"] == "これができるのはこのハブの管理者だけです"

    code, dv = _call(url, "/api/devices", headers=_as(s["Ada"]))
    assert code == 200 and dv["can_admin"] and dv["viewer"]["name"] == "Ada" and not dv["here"]
    assert _call(url, "/api/suggestions/seen", {}, _as(s["Ada"])) == (200, {"ok": True})
    assert _call(url, "/api/people", headers=_as(s["Ada"]))[0] == 200
    assert _call(url, "/api/mirror", headers=_as(s["Ada"]))[0] == 200  # an admin of the hub may set up its mirror
    assert _call(url, "/api/devices/share", {"share": "knowledge"}, _as(s["Ada"]))[0] == 403  # this computer's own
    assert _call(url, "/api/devices/hub-signin", {}, _as(s["Ada"]))[0] == 403

    status, headers, _ = _raw(url, "/api/signout", {}, _as(s["Vic"]))
    assert status == 200 and "Max-Age=0" in headers["Set-Cookie"]
    assert _call(url, "/api/overview", headers=_as(s["Vic"]))[0] == 401


def test_team_store_settings_for_an_admin_person(team):
    url, s, home = team["url"], team["sessions"], team["cfg"].home
    set_config_value(team["cfg"], "hub", "store", '"postgres"')
    assert _call(url, "/api/team-store/save", {"enabled": False}, _as(s["Bob"]))[0] == 403
    assert load_config(home).hub_store == "postgres"
    code, r = _call(url, "/api/team-store/save", {"enabled": False}, _as(s["Ada"]))
    assert code == 200 and r["ok"] and load_config(home).hub_store == ""


def test_signing_in_with_a_link(team):
    url, conn, app = team["url"], team["conn"], team["app"]
    code = people.invite(conn, team["vic"]["id"])
    status, headers, _ = _raw(url, f"/signin?code={code.lower()}", headers=REMOTE)
    cookie = headers["Set-Cookie"]
    assert status == 302 and headers["Location"] == "/#/" and "Secure" not in cookie and cookie.startswith("interlatch_session=")
    assert all(part in cookie for part in ("HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=2592000"))
    session = cookie.split(";")[0].split("=", 1)[1]
    assert _call(url, "/api/me", headers=_as(session))[1]["viewer"]["name"] == "Vic"

    status, headers, page = _raw(url, f"/signin?code={code}", headers=REMOTE)  # a code works once
    assert status == 400 and b"already used" in page and "Set-Cookie" not in headers
    status, _, page = _raw(url, f"/signin?code={code}", headers={**REMOTE, "Accept-Language": "ja,en;q=0.8"})
    assert "使用済み".encode() in page and "サインインできませんでした".encode() in page
    status, _, page = _raw(url, "/signin?code=%3Cscript%3E", headers=REMOTE)
    assert status == 400 and b"<script>" not in page

    signin = people.signin_code(conn, team["bob"])  # https through a trusted proxy: the cookie is Secure
    status, headers, _ = _raw(url, f"/signin?code={signin}", headers={**REMOTE, "X-Forwarded-Proto": "https"})
    assert status == 302 and "Secure" in headers["Set-Cookie"]
    app.cfg.server_trusted_proxies = ["10.0.0.1"]  # from anyone else, X-Forwarded-Proto is not believed
    signin = people.signin_code(conn, team["bob"])
    status, headers, _ = _raw(url, f"/signin?code={signin}", headers={**REMOTE, "X-Forwarded-Proto": "https"})
    assert status == 302 and "Secure" not in headers["Set-Cookie"]


def test_a_signin_link_opens_on_the_page_it_names(team):
    url, conn, bob = team["url"], team["conn"], team["bob"]
    page = "/project?path=%2Fdata%2Fprojects%2FCosmo-Quote-Agent"
    status, headers, _ = _raw(url, f"/signin?code={people.signin_code(conn, bob)}&next={quote(page, safe='')}", headers=REMOTE)
    assert status == 302 and headers["Location"] == f"/#{page}" and headers["Set-Cookie"]
    for bad in ("https://evil.example/", "/x\r\nSet-Cookie: a=b", "javascript:alert(1)", "/a b", "/#/x", "/<p>", "/" + "a" * 600, ""):
        status, headers, _ = _raw(url, f"/signin?code={people.signin_code(conn, bob)}&next={quote(bad, safe='')}", headers=REMOTE)
        assert status == 302 and headers["Location"] == "/#/", bad  # Home: only a dashboard route passes


def test_this_computer_asks_for_a_signin_link_to_a_page(tmp_path):
    home = tmp_path / "spoke"
    home.mkdir()
    (home / "config.toml").write_text('[hub]\nurl = "https://hub.example.com"\n')
    cfg = load_config(home)
    hub.write_token(cfg, "t")

    class Client:
        def request(self, method, path, body=None, **kw):
            return {"code": "ABCD-EFGH-JKLM"}

        def close(self):
            pass

    link = "https://hub.example.com/signin?code=ABCD-EFGH-JKLM"
    assert hub.dashboard_signin(cfg, Client()) == link
    assert hub.dashboard_signin(cfg, Client(), page="/project?path=%2Fsrv%2FAktio") == f"{link}&next=%2Fproject%3Fpath%3D%252Fsrv%252FAktio"
    assert hub.dashboard_signin(cfg, Client(), page="https://evil.example/") == link


def test_company_sign_in_header(team):
    url, app = team["url"], team["app"]
    app.cfg.server_auth_header = "X-Auth-Email"
    code, me = _call(url, "/api/me", headers={**REMOTE, "X-Auth-Email": "BOB@example.com"})
    assert code == 200 and me["viewer"]["name"] == "Bob"
    code, r = _call(url, "/api/me", headers={**REMOTE, "X-Auth-Email": "eve@example.com"})
    assert code == 403 and "not on this hub" in r["error"]
    app.cfg.server_trusted_proxies = ["10.0.0.1"]  # the header is believed only from a trusted proxy
    assert _call(url, "/api/me", headers={**REMOTE, "X-Auth-Email": "bob@example.com"})[0] == 401


def test_people_api_and_its_audit(team):
    url, s, app = team["url"], team["sessions"], team["app"]
    ada = _as(s["Ada"])
    assert _call(url, "/api/people/add", {"name": "Cy", "email": "cy@example.com"}, _as(s["Bob"]))[0] == 403
    code, r = _call(url, "/api/people/add", {"name": "Cy", "email": "cy@example.com", "role": "member", "projects": "all"}, ada)
    assert code == 200 and r["person"]["role"] == "member" and r["expires_at"] and r["note"]
    assert r["link"].endswith(f"/signin?code={r['code']}") and f"--code {r['code']} --share knowledge" in r["join"]
    cy = r["person"]["id"]
    code, r = _call(url, "/api/people/add", {"name": "Cy", "email": "cy@example.com", "projects": "all"}, ada)
    assert code == 400 and "already on this hub" in r["error"]

    app.cfg.hub_address = "https://hub.example.com"
    code, r = _call(url, "/api/people/invite", {"id": cy}, ada)
    assert r["join"] == f"interlatch hub join https://hub.example.com --code {r['code']} --share knowledge"
    assert r["link"] == f"https://hub.example.com/signin?code={r['code']}" and "note" not in r
    code, r = _call(url, "/api/people/role", {"id": cy, "role": "readonly"}, ada)
    assert code == 200 and {p["name"]: p["role"] for p in r["people"]}["Cy"] == "readonly"
    code, r = _call(url, "/api/people/role", {"id": team["ada"]["id"], "role": "member"},
                    {**ada, "X-Interlatch-Lang": "ja"})
    assert code == 400 and r["error"] == "このハブの最後の管理者です。先にほかの人を管理者にしてください"
    assert _call(url, "/api/people/revoke", {"id": cy, "token": "f" * 16}, ada)[0] == 400
    code, r = _call(url, "/api/people/remove", {"id": cy}, ada)
    assert code == 200 and "Cy" not in [p["name"] for p in r["people"]]
    assert _call(url, "/api/people/remove", {"id": "x"}, ada)[0] == 400

    code, info = _call(url, "/api/people", headers=ada)
    assert code == 200 and info["roles"] == ["admin", "member", "readonly"] and info["shared_token"] is True
    by_ada = [(a["action"], a["actor_name"]) for a in info["audit"] if a["actor_name"] == "Ada"]
    assert ("add", "Ada") in by_ada and ("role", "Ada") in by_ada and ("remove", "Ada") in by_ada
    code, r = _call(url, "/api/people/shared-token", {"on": False}, ada)
    assert code == 200 and r["shared_token"] is False and load_config(team["cfg"].home).hub_shared_token is False
    assert r["audit"][0]["action"] == "shared-token"


def test_person_tokens_on_the_hub_api(team):
    url, conn, token = team["url"], team["conn"], team["token"]

    def api(path, body, bearer):
        return _call(url, path, body, {"Authorization": f"Bearer {bearer}"})

    code = people.invite(conn, team["bob"]["id"])
    status, r = _call(url, "/api/hub/join", {"code": code, "machine": MACHINE, "name": "Bob laptop"})
    assert status == 200 and r["person"]["name"] == "Bob"
    bob_token = r["token"]
    assert _call(url, "/api/hub/join", {"code": code, "machine": MACHINE})[0] == 400  # used
    assert api("/api/hub/done", {"machine": MACHINE}, bob_token) == (200, {"ok": True})
    assert api("/api/hub/done", {"machine": OTHER}, bob_token)[0] == 401  # issued to another computer
    assert api("/api/hub/done", {"machine": MACHINE}, "nope")[0] == 401

    status, r = api("/api/hub/signin", {"machine": MACHINE}, bob_token)
    assert status == 200 and _raw(url, f"/signin?code={r['code']}", headers=REMOTE)[0] == 302
    status, r = api("/api/hub/signin", {"machine": MACHINE}, token)
    assert status == 401  # the shared token can't act as Bob's computer
    status, r = api("/api/hub/signin", {"machine": OTHER}, token)
    assert status == 400 and "join with an invite" in r["error"]  # a computer that is nobody's: no sign-in

    people.set_role(conn, team["bob"]["id"], "readonly")
    status, r = api("/api/hub/done", {"machine": MACHINE}, bob_token)
    assert status == 403 and r["error"] == "read-only people can't send to the hub"
    assert api("/api/hub/signin", {"machine": MACHINE}, bob_token)[0] == 200  # still opens the dashboard

    assert api("/api/hub/done", {"machine": OTHER}, token)[0] == 200  # the shared token, while it's on, as nobody's computer
    assert api("/api/hub/done", {"machine": MACHINE}, token)[0] == 401  # but never as Bob's
    set_config_value(team["cfg"], "hub", "shared_token", "false")
    assert api("/api/hub/done", {"machine": OTHER}, token)[0] == 401


def test_wrong_codes_make_an_address_wait(team):
    url, app, conn = team["url"], team["app"], team["conn"]
    t = [1000.0]
    app.code_attempts = people.CodeAttempts(clock=lambda: t[0])
    there = {"X-Forwarded-For": "203.0.113.7"}  # through the proxy on the hub itself (trusted): that visitor's address
    for _ in range(people.CodeAttempts.FREE):
        assert _raw(url, "/signin?code=AAAA-BBBB-CCCC", headers=there)[0] == 400
    good = people.invite(conn, team["bob"]["id"])
    status, headers, page = _raw(url, f"/signin?code={good}", headers=there)
    assert status == 429 and headers["Retry-After"] == "1" and b"too many wrong codes" in page
    assert people.peek_invite(conn, good)  # not looked at, so not used up
    assert _raw(url, f"/signin?code={good}", headers={"X-Forwarded-For": "198.51.100.9"})[0] == 302  # nobody else waits

    # joining with a code counts the same way, for the same address
    wrong = {"code": "AAAA-BBBB-CCCC", "machine": OTHER}
    status, r = _call(url, "/api/hub/join", wrong, there)
    assert status == 429 and "try again in 1 s" in r["error"]
    t[0] += 1
    status, r = _call(url, "/api/hub/join", wrong, there)
    assert status == 400 and "isn't known" in r["error"]
    status, headers, _ = _raw(url, "/api/hub/join", wrong, there)
    assert status == 429 and headers["Retry-After"] == "2"

    # a code the hub issued, used or expired, is no guess: it never makes anyone wait
    app.code_attempts = people.CodeAttempts(clock=lambda: t[0])
    for _ in range(people.CodeAttempts.FREE + 3):
        assert _raw(url, f"/signin?code={good}", headers=there)[0] == 400  # already used
    assert app.code_attempts.wait("203.0.113.7") == 0

    # X-Forwarded-For from a proxy the hub doesn't trust is not believed: every visitor is that proxy
    app.cfg.server_trusted_proxies = []
    for i in range(people.CodeAttempts.FREE):
        assert _raw(url, "/signin?code=AAAA-BBBB-CCCC", headers={"X-Forwarded-For": f"192.0.2.{i}"})[0] == 400
    assert _raw(url, "/signin?code=AAAA-BBBB-CCCC", headers={"X-Forwarded-For": "192.0.2.99"})[0] == 429


def test_a_proxy_on_the_hub_itself_is_not_someone_here(hubweb):
    url, app = hubweb["url"], hubweb["app"]
    for header in ("X-Forwarded-Proto", "X-Real-IP", "Forwarded"):  # added by a proxy: not someone at this computer
        code, dv = _call(url, "/api/devices", headers={header: "https" if header == "X-Forwarded-Proto" else "for=1.2.3.4",
                                                       "Tailscale-User-Login": "me@github"})
        assert code == 200 and not dv["here"] and not dv["can_admin"], header
        code, dv = _call(url, "/api/devices", headers={header: "https" if header == "X-Forwarded-Proto" else "for=1.2.3.4"})
        assert code == 403 and dv["nobody"] is True, header  # nginx in front, say: no people, no way in
    app.cfg.server_behind_proxy = True  # nginx's default: the hub's own Host and no forwarding headers at all
    code, dv = _call(url, "/api/devices")
    assert code == 403 and dv["nobody"] is True  # no people, so no sign-in: nobody through the proxy gets in
    assert _call(url, "/api/team-store/save", {"enabled": False})[0] == 403
    assert _call(url, "/api/update", {})[0] == 403
    people.add(hubweb["conn"], "Ada", "ada@example.com", "admin")
    code, r = _call(url, "/api/overview")
    assert code == 401 and r["signin"] is True  # with people, everyone through the proxy signs in


def test_tailscale_logins_and_people(team):
    """[server] allowed_users keeps others out of a hub without people; with people, people.py says who is who."""
    url, s, app = team["url"], team["sessions"], team["app"]
    app.cfg.server_allowed_users = ["me@github"]
    assert _call(url, "/api/me", headers=_as(s["Bob"]))[1]["viewer"]["name"] == "Bob"
    code, r = _call(url, "/api/overview", headers=REMOTE)
    assert code == 401 and r["signin"] is True
    signin = people.signin_code(team["conn"], team["vic"])
    assert _raw(url, f"/signin?code={signin}", headers=REMOTE)[0] == 302
    assert _call(url, "/api/suggestions/seen", {}, _as(s["Ada"])) == (200, {"ok": True})


def test_learning_interests_use_the_authenticated_person_not_query_parameters(team):
    from chronicle.util import utcnow_iso

    conn, url, sessions = team["conn"], team["url"], team["sessions"]
    for mid, person, sid, question in (
        (MACHINE, team["bob"]["id"], "curious-bob", "Explain API versioning."),
        (OTHER, team["vic"]["id"], "curious-vic", "Explain frontend React hooks."),
    ):
        conn.execute("INSERT INTO machines(id, person_id) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET person_id=excluded.person_id",
                     (mid, person))
        conn.execute("INSERT INTO sessions(id, machine_id, started_at) VALUES(?,?,?)", (sid, mid, utcnow_iso()))
        conn.execute("INSERT INTO events(session_id, seq, kind, text) VALUES(?,1,'prompt',?)", (sid, question))
    conn.commit()
    assert _call(url, "/api/learning/interests", headers=REMOTE)[0] == 401
    code, result = _call(url, f"/api/learning/interests?viewer_id={team['vic']['id']}", headers=_as(sessions["Bob"]))
    assert code == 200 and [i["topic"] for i in result["interests"]] == ["api"]
    code, result = _call(url, "/api/learning/interests", headers=_as(sessions["Vic"]))
    assert code == 200 and [i["topic"] for i in result["interests"]] == ["frontend"]
