"""A hub's sign-in cookie is named for that hub (handler.session_cookie_name): browsers keep cookies by host name, not
port, so two hubs reached under one name with one cookie name took each other's sign-in. Over https the name starts
with __Host-. Cookies under the names every hub used before are still taken, and replaced; signing out clears them."""

import re

from interlatch.interfaces.web.handler import COOKIE_ID_KEY, sent_cookies, session_cookie_name
from interlatch.sharing import people
from interlatch.storage.kv import kv_get
from interlatch.storage.sqlite import connect

from test_people_web import REMOTE, _call, _raw, _signin, hubweb, team  # noqa: F401  (the fixtures)

HTTPS = {**REMOTE, "X-Forwarded-Proto": "https"}  # through the proxy in front, which 127.0.0.1 is trusted as


def _cookies(headers) -> dict[str, str]:
    """Each Set-Cookie of an answer, by name: the whole header."""
    return {c.split("=", 1)[0]: c for c in headers.get_all("Set-Cookie") or []}


def _session(team, who="Bob") -> str:  # noqa: F811
    return people.open_browser(team["conn"], people.signin_code(team["conn"], team[who.lower()]), "test")[1]


def test_the_name_is_the_hubs_own_and_stays(tmp_path):
    conn = connect(tmp_path / "a" / "chronicle.db")
    assert session_cookie_name(conn, https=False) is None  # no sign-in yet: nothing made, nothing written
    assert kv_get(conn, COOKIE_ID_KEY) is None
    name = session_cookie_name(conn, https=False, create=True)
    assert re.fullmatch(r"interlatch_session_[0-9a-f]{8}", name)
    assert session_cookie_name(conn, https=True) == f"__Host-{name}"
    conn.close()
    again = connect(tmp_path / "a" / "chronicle.db")  # a restart, or an upgrade: the same database, the same name
    assert session_cookie_name(again, https=False, create=True) == name
    other = connect(tmp_path / "b" / "chronicle.db")  # another hub on the same host name: its own
    assert session_cookie_name(other, https=False, create=True) != name
    other.execute("UPDATE kv SET value = 'not-an-id' WHERE key = ?", (COOKIE_ID_KEY,))
    assert session_cookie_name(other, https=False) is None  # never a name from an edited value
    assert re.fullmatch(r"interlatch_session_[0-9a-f]{8}", session_cookie_name(other, https=False, create=True))


def test_cookies_are_read_pair_by_pair():
    """A browser sends other servers' cookies on the same host name too: an odd one doesn't hide the hub's."""
    got = sent_cookies(['other="a b"; broken; =x; empty=; interlatch_session_0a1b2c3d=s1', "interlatch_session=s2; x=1=2"])
    assert got == {"other": ['"a b"'], "interlatch_session_0a1b2c3d": ["s1"], "interlatch_session": ["s2"], "x": ["1=2"]}


def test_signing_in_sets_the_hubs_own_cookie_over_http_and_https(team):  # noqa: F811
    url, conn = team["url"], team["conn"]
    plain = session_cookie_name(conn, https=False)
    assert plain is None  # the people were added in the archive: the hub has signed nobody in yet
    status, headers, _ = _signin(url, people.signin_code(conn, team["bob"]), headers=REMOTE)
    plain = session_cookie_name(conn, https=False)
    cookie = headers["Set-Cookie"]
    assert status == 302 and cookie.startswith(f"{plain}=") and "Secure" not in cookie
    assert all(part in cookie for part in ("; HttpOnly", "; SameSite=Lax", "; Path=/", "Max-Age=")) and "Domain" not in cookie
    session = cookie.split(";")[0].split("=", 1)[1]
    assert _call(url, "/api/me", headers={**REMOTE, "Cookie": f"{plain}={session}"})[1]["viewer"]["name"] == "Bob"

    status, headers, _ = _signin(url, people.signin_code(conn, team["bob"]), headers=HTTPS)
    cookie = headers["Set-Cookie"]
    assert status == 302 and cookie.startswith(f"__Host-{plain}=")  # the browser takes it only from a secure page,
    assert all(part in cookie for part in ("; Secure", "; Path=/", "; HttpOnly", "; SameSite=Lax"))  # with Path=/,
    assert "Domain" not in cookie  # and no Domain
    secure = cookie.split(";")[0].split("=", 1)[1]
    assert _call(url, "/api/me", headers={**HTTPS, "Cookie": f"__Host-{plain}={secure}"})[1]["viewer"]["name"] == "Bob"
    # over https only the __Host- name counts, which no plain-http server on the same host name can set; and a
    # __Host- cookie means nothing over http, where a browser never sends one
    assert _call(url, "/api/me", headers={**HTTPS, "Cookie": f"{plain}={secure}"})[0] == 401
    assert _call(url, "/api/me", headers={**REMOTE, "Cookie": f"__Host-{plain}={secure}"})[0] == 401


def test_an_old_cookie_name_still_signs_in_and_is_replaced(team):  # noqa: F811
    url, conn = team["url"], team["conn"]
    for old in ("interlatch_session", "chronicle_session"):
        for over in (REMOTE, HTTPS):
            conn.execute("DELETE FROM kv WHERE key = ?", (COOKIE_ID_KEY,))  # a hub upgraded with people signed in
            conn.commit()
            session = _session(team)
            status, headers, body = _raw(url, "/api/me", headers={**over, "Cookie": f"{old}={session}"})
            name = session_cookie_name(conn, https=over is HTTPS)
            got = _cookies(headers)
            assert status == 200 and b'"Bob"' in body and name and set(got) == {name, old}, (old, over)
            assert got[name].startswith(f"{name}={session};") and "Max-Age=2592000" in got[name]
            assert ("; Secure" in got[name]) == (over is HTTPS)
            assert got[old].startswith(f"{old}=;") and "Max-Age=0" in got[old] and "Path=/" in got[old]
            assert _call(url, "/api/me", headers={**over, "Cookie": f"{name}={session}"})[1]["viewer"]["name"] == "Bob"
            # with the hub's own cookie sent, nothing more is set
            assert "Set-Cookie" not in _raw(url, "/api/me", headers={**over, "Cookie": f"{name}={session}"})[1]


def test_an_old_cookie_name_of_another_hub_is_left_alone(team):  # noqa: F811
    """The old names were every hub's: one on another port of this host name may hold that hub's session. This hub
    neither replaces nor clears it, and a request with nothing else isn't signed in."""
    url, conn = team["url"], team["conn"]
    mine = _session(team)
    name = session_cookie_name(conn, https=False, create=True)
    status, headers, _ = _raw(url, "/api/me", headers={**REMOTE, "Cookie": f"interlatch_session=other-hubs; {name}={mine}"})
    assert status == 200 and "Set-Cookie" not in headers
    status, headers, _ = _raw(url, "/api/me", headers={**REMOTE, "Cookie": "interlatch_session=other-hubs"})
    assert status == 401 and "Set-Cookie" not in headers
    status, headers, _ = _signin(url, people.signin_code(conn, team["bob"]),
                                 headers={**REMOTE, "Cookie": "interlatch_session=other-hubs"})
    assert status == 302 and set(_cookies(headers)) == {name}  # signing in sets this hub's own, clears nothing else


def test_signing_in_replaces_an_old_names_session_and_clears_it(team):  # noqa: F811
    url, conn = team["url"], team["conn"]
    old, older = _session(team, "Ada"), _session(team, "Ada")
    status, headers, _ = _signin(url, people.signin_code(conn, team["ada"]),
                                 headers={**REMOTE, "Cookie": f"interlatch_session={old}; chronicle_session={older}"})
    name = session_cookie_name(conn, https=False)
    got = _cookies(headers)
    assert status == 302 and set(got) == {name, "interlatch_session", "chronicle_session"}
    assert all("Max-Age=0" in got[n] for n in ("interlatch_session", "chronicle_session"))
    assert people.browser_person(conn, old) is None and people.browser_person(conn, older) is None


def test_signing_out_ends_and_clears_both_names(team):  # noqa: F811
    url, conn = team["url"], team["conn"]
    name = session_cookie_name(conn, https=True, create=True)
    now, before = _session(team, "Vic"), _session(team, "Vic")
    status, headers, _ = _raw(url, "/api/signout", {}, {**HTTPS, "Cookie": f"{name}={now}; interlatch_session={before}; "
                                                                          "chronicle_session=other-hubs"})
    got = _cookies(headers)
    assert status == 200 and set(got) == {name, "interlatch_session"}  # another hub's old-name cookie stays
    assert all("Max-Age=0" in c and "; Secure" in c and "Path=/" in c for c in got.values())
    assert people.browser_person(conn, now) is None and people.browser_person(conn, before) is None
    assert _call(url, "/api/me", headers={**HTTPS, "Cookie": f"{name}={now}"})[0] == 401
    assert _call(url, "/api/me", headers={**REMOTE, "Cookie": f"interlatch_session={before}"})[0] == 401


def test_a_dashboard_without_people_sets_and_keeps_no_cookie(hubweb):  # noqa: F811
    """This computer's own dashboard (no people, opened from here): no sign-in, so no cookie set and no id made, even
    when its browser carries a session cookie of a hub on another port."""
    url, conn = hubweb["url"], hubweb["conn"]
    for path, body in (("/api/me", None), ("/api/overview", None), ("/api/signout", {})):
        for cookie in ({}, {"Cookie": "interlatch_session=other-hubs"}):
            status, headers, _ = _raw(url, path, body, cookie)
            assert status == 200 and "Set-Cookie" not in headers, (path, cookie)
    assert kv_get(conn, COOKIE_ID_KEY) is None
