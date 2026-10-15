"""The usage leaderboard (leaderboard.py): what this computer counts as its own, what a send carries and when it
holds back, signing in through the hosted page (and only with the state this dashboard made), renaming, leaving, the
board itself, and the dashboard's API, which only the computer itself may use. Nothing here reaches Google or Firebase:
leaderboard._call is a fake."""

import base64
import json
import os
import stat
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from interlatch.sharing import leaderboard as lb
from interlatch.storage.sqlite import connect
from interlatch.core.util import to_iso, utcnow
from interlatch.core.machine import local_machine

from test_people_web import TAILNET, hubweb  # noqa: F401  (the fixture)
from test_people_web import _call as _http

OTHER = "99999999-2222-4333-8444-555555555555"
PHOTO = "https://lh3.googleusercontent.com/a/ACg8ocK-photo=s96-c"


def _jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


GOOGLE = {"user_id": "uid1", "name": "Ada Lovelace", "picture": PHOTO, "email": "ada@example.com",
          "firebase": {"sign_in_provider": "google.com", "identities": {"google.com": ["1083"], "email": ["ada@example.com"]}}}


class FakeNet:
    """Answers leaderboard._call by URL prefix; records every call."""

    def __init__(self):
        self.calls, self.routes = [], []

    def on(self, method, prefix, answer):
        self.routes.insert(0, (method, prefix, answer))

    def __call__(self, method, url, *, body=None, form=None, token=None):
        self.calls.append({"method": method, "url": url, "body": body, "form": form, "token": token})
        for m, prefix, answer in self.routes:
            if m == method and url.startswith(prefix):
                return answer(body, form) if callable(answer) else answer
        raise AssertionError(f"unexpected {method} {url}")

    def commits(self):
        return [c for c in self.calls if c["url"].endswith(":commit")]


@pytest.fixture()
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(lb, "_call", fake)
    fake.on("POST", "https://securetoken.googleapis.com/", {"id_token": _jwt(GOOGLE), "refresh_token": "R1", "user_id": "uid1"})
    lb._clear_board()
    lb._photos.clear()
    monkeypatch.setattr(lb, "_api_key", "test-key")  # as if the hosted config had been read
    return fake


@pytest.fixture()
def home(env):
    conn = connect(env["cfg"].db_path)
    env["conn"] = conn
    yield env
    conn.close()


def _session(conn, sid, *, days_ago=1, agent="claude", source="transcript", machine=None, active=3600, tokens=(10, 20, 30, 40)):
    at = to_iso(utcnow() - timedelta(days=days_ago))
    conn.execute("INSERT INTO sessions(id, source, agent, started_at, ended_at, active_s, input_tokens, output_tokens, "
                 "cache_read_tokens, cache_write_tokens, machine_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (sid, source, agent, at, at, active, *tokens, machine))
    conn.commit()


def _joined(cfg, **extra):
    lb.write_state(cfg, {"uid": "uid1", "refresh_token": "R0", "provider": "google.com", "pid": "1083", "name": "Ada",
                         "photo": PHOTO, "joined_at": "x", "last_sent": None, "error": None, "paused": None, **extra})


def test_totals_count_this_computers_coding_agent_sessions_only(home):
    conn, cfg = home["conn"], home["cfg"]
    here = local_machine(cfg)["id"]
    _session(conn, "a", days_ago=1)  # this computer, no machine id recorded
    _session(conn, "b", days_ago=20, machine=here, active=7200)
    _session(conn, "c", days_ago=60, agent="codex")
    _session(conn, "old", days_ago=120)
    _session(conn, "teammate", days_ago=1, machine=OTHER)  # a hub holds it: someone else's
    _session(conn, "shared", days_ago=1, source="remote")
    _session(conn, "chat", days_ago=1, agent="claude-ai")  # an imported chat
    _session(conn, "hist", days_ago=1, source="history")
    t = lb.totals(conn, cfg)
    assert (t["s7"], t["s30"], t["s90"]) == (1, 2, 3)
    assert (t["h7"], t["h30"], t["h90"]) == (1.0, 3.0, 4.0)
    assert (t["t7"], t["t30"], t["t90"]) == (100, 200, 300)
    assert isinstance(t["s7"], int) and isinstance(t["h7"], float)


def test_the_firebase_key_comes_from_the_hosted_config_once(net, monkeypatch):
    monkeypatch.setattr(lb, "_api_key", None)
    net.on("GET", lb.CONFIG_URL, {"apiKey": "from-hosting", "projectId": "interlatch"})
    assert lb.api_key() == "from-hosting" and lb.api_key() == "from-hosting"
    assert [c["url"] for c in net.calls] == [lb.CONFIG_URL]
    monkeypatch.setattr(lb, "_api_key", None)
    net.on("GET", lb.CONFIG_URL, {})
    with pytest.raises(lb.LeaderboardError, match="settings"):
        lb.api_key()


def test_nothing_is_sent_before_signing_in(home, net):
    assert lb.send(home["cfg"], home["conn"]) is None
    assert net.calls == []


def test_signing_in_keeps_the_session_a_first_name_and_no_email(home, net):
    cfg = home["cfg"]
    state = lb.sign_in(cfg, "R0-from-the-hosted-page")
    assert net.calls[0]["form"] == {"grant_type": "refresh_token", "refresh_token": "R0-from-the-hosted-page"}
    assert state["uid"] == "uid1" and state["provider"] == "google.com" and state["pid"] == "1083"
    assert state["name"] == "Ada" and state["photo"] == PHOTO and state["refresh_token"] == "R1"
    saved = lb.state_path(cfg).read_text()
    assert "ada@example.com" not in saved and "Lovelace" not in saved
    assert stat.S_IMODE(os.stat(lb.state_path(cfg)).st_mode) == 0o600


def test_only_google_or_github_accounts_sign_in(home, net):
    anon = {"user_id": "u", "firebase": {"sign_in_provider": "anonymous", "identities": {}}}
    net.on("POST", "https://securetoken.googleapis.com/", {"id_token": _jwt(anon), "refresh_token": "R1"})
    with pytest.raises(lb.LeaderboardError, match="Google"):
        lb.sign_in(home["cfg"], "R0-from-the-hosted-page")
    assert lb.read_state(home["cfg"]) is None


def test_a_picture_from_elsewhere_is_dropped(home, net):
    net.on("POST", "https://securetoken.googleapis.com/", {"id_token": _jwt({**GOOGLE, "picture": "https://evil.example/x.png"})})
    assert lb.sign_in(home["cfg"], "R0-from-the-hosted-page")["photo"] == ""


def test_a_send_carries_totals_name_and_a_hashed_machine_only(home, net):
    conn, cfg = home["conn"], home["cfg"]
    _session(conn, "a")
    _joined(cfg)
    net.on("POST", lb.FIRESTORE + ":commit", {})
    st = lb.send(cfg, conn)
    (commit,) = net.commits()
    assert commit["token"] == _jwt(GOOGLE)
    write = commit["body"]["writes"][0]
    assert write["update"]["name"].endswith("/documents/players/uid1")
    fields = write["update"]["fields"]
    assert set(fields) == {"provider", "pid", "name", "photo", "machine", "h7", "h30", "h90", "s7", "s30", "s90",
                           "t7", "t30", "t90"}
    assert fields["provider"] == {"stringValue": "google.com"} and fields["pid"] == {"stringValue": "1083"}
    assert fields["name"] == {"stringValue": "Ada"} and fields["photo"] == {"stringValue": PHOTO}
    assert fields["s7"] == {"integerValue": "1"} and fields["h7"] == {"doubleValue": 1.0}
    machine = fields["machine"]["stringValue"]
    assert machine != local_machine(cfg)["id"] and len(machine) == 16  # which computer, without saying which
    assert write["updateTransforms"] == [{"fieldPath": "at", "setToServerValue": "REQUEST_TIME"}]  # the rules check it
    assert st["last_sent"] and st["refresh_token"] == "R1"  # a rotated refresh token is kept


def test_sends_are_spaced_out_unless_forced(home, net):
    conn, cfg = home["conn"], home["cfg"]
    _joined(cfg, last_sent=to_iso(utcnow() - timedelta(minutes=2)))
    lb.send(cfg, conn)
    assert net.calls == []
    net.on("GET", lb.FIRESTORE + "/players/uid1", {"fields": {"machine": {"stringValue": lb.machine_tag(cfg)}}})
    net.on("POST", lb.FIRESTORE + ":commit", {})
    lb.send(cfg, conn, force=True)
    assert len(net.commits()) == 1


def test_another_computer_that_signed_in_takes_over(home, net):
    conn, cfg = home["conn"], home["cfg"]
    _joined(cfg, last_sent=to_iso(utcnow() - timedelta(hours=1)))
    net.on("GET", lb.FIRESTORE + "/players/uid1", {"fields": {"machine": {"stringValue": "someothercomputer"}}})
    st = lb.send(cfg, conn)
    assert st["paused"] and net.commits() == []
    lb.send(cfg, conn, force=True)  # paused stays paused until it signs in again
    assert net.commits() == []


def test_a_failed_send_is_kept_as_the_error_and_never_raises(home, net):
    conn, cfg = home["conn"], home["cfg"]
    _joined(cfg)

    def down(body, form):
        raise lb.LeaderboardError("no connection: offline")

    net.on("POST", lb.FIRESTORE + ":commit", down)
    st = lb.send(cfg, conn)
    assert st["error"] == "no connection: offline" and st["last_sent"] is None
    lb.send_safely(cfg, conn)


def test_renaming_cleans_the_name_and_sends_it(home, net):
    conn, cfg = home["conn"], home["cfg"]
    _joined(cfg)
    net.on("POST", lb.FIRESTORE + ":commit", {})
    lb.rename(cfg, conn, "  Countess\n of   Lovelace\x07 " + "x" * 40)
    name = net.commits()[0]["body"]["writes"][0]["update"]["fields"]["name"]["stringValue"]
    assert name.startswith("Countess of Lovelace") and len(name) == lb.NAME_MAX and "\n" not in name
    with pytest.raises(lb.LeaderboardError):
        lb.rename(cfg, conn, " \n ")


def test_leaving_deletes_the_row_and_the_sign_in(home, net):
    cfg = home["cfg"]
    _joined(cfg)
    net.on("DELETE", lb.FIRESTORE + "/players/uid1", {})
    net.on("DELETE", lb.FIRESTORE + "/arena/uid1", {})
    lb.leave(cfg)
    assert [c["url"] for c in net.calls if c["method"] == "DELETE"] == [lb.FIRESTORE + "/players/uid1",
                                                                       lb.FIRESTORE + "/arena/uid1"]  # the arena too
    assert not lb.state_path(cfg).exists() and lb.status(cfg)["joined"] is False


def test_the_board_ranks_and_drops_rows_older_than_their_window(net):
    now = utcnow()

    def doc(uid, name, h30, at, photo=PHOTO):
        return {"document": {"name": f"{lb.DB}/players/{uid}", "fields": {
            "name": {"stringValue": name}, "photo": {"stringValue": photo}, "h30": {"doubleValue": h30},
            "s30": {"integerValue": "3"}, "t30": {"integerValue": "900"}, "at": {"timestampValue": to_iso(at)}}}}

    net.on("POST", lb.FIRESTORE + ":runQuery", lambda body, form: [
        doc("a", "Ann", 40.5, now - timedelta(hours=1)), doc("b", "Ben", 30.0, now - timedelta(days=45)),
        doc("c", "Cy", 3.0, now, photo="https://evil.example/x.png"), {"readTime": "x"}])
    rows = lb.board("hours", 30)
    query = net.calls[0]["body"]["structuredQuery"]
    assert query["orderBy"][0]["field"]["fieldPath"] == "h30" and query["limit"] == lb.TOP
    assert [r["name"] for r in rows] == ["Ann", "Cy"]  # Ben's totals are from before the window began
    assert rows[0] == {"uid": "a", "name": "Ann", "photo": True, "at": rows[0]["at"], "hours": 40.5, "sessions": 3, "tokens": 900}
    assert rows[1]["photo"] is False and "c" not in lb._photos  # a picture from elsewhere is never fetched
    assert lb.avatar("c") is None
    lb.board("hours", 30)
    assert len(net.calls) == 1  # read again only after a while
    with pytest.raises(lb.LeaderboardError):
        lb.board("cost", 30)


def test_signing_in_from_the_dashboard_needs_the_state_it_made(hubweb, net):  # noqa: F811
    url, cfg = hubweb["url"], hubweb["cfg"]
    net.on("POST", lb.FIRESTORE + ":commit", {})
    net.on("POST", lb.FIRESTORE + ":runQuery", [])
    status, r = _http(url, "/api/leaderboard/finish", {"state": "x" * 32, "token": "R0-from-the-hosted-page"})
    assert status == 502 and lb.read_state(cfg) is None  # no sign-in was started here
    status, r = _http(url, "/api/leaderboard/signin", {})
    page = urlparse(r["url"])
    assert f"{page.scheme}://{page.netloc}{page.path}" == lb.SIGNIN_PAGE
    q = {k: v[0] for k, v in parse_qs(page.query).items()}
    assert q["return"] == url + "/" and q["lang"] == "en" and len(q["state"]) >= 20  # back to this very dashboard
    assert _http(url, "/api/leaderboard")[1]["signing_in"] is True
    forged = q["state"][:-1] + ("B" if q["state"].endswith("A") else "A")  # never the state itself
    status, r = _http(url, "/api/leaderboard/finish", {"state": forged, "token": "R0-from-the-hosted-page"})
    assert status == 502 and lb.read_state(cfg) is None  # someone else's sign-in can't be slipped in
    status, r = _http(url, "/api/leaderboard/signin", {})
    state = parse_qs(urlparse(r["url"]).query)["state"][0]
    status, r = _http(url, "/api/leaderboard/finish", {"state": state, "token": "R0-from-the-hosted-page"})
    assert status == 200 and r["joined"] and r["name"] == "Ada" and "refresh_token" not in r
    assert len(net.commits()) == 1  # sent at once
    assert _http(url, "/api/leaderboard/finish", {"state": state, "token": "R0-other"})[0] == 502  # one use


def test_the_dashboard_api_is_for_this_computer_only(hubweb, net):  # noqa: F811
    url, cfg = hubweb["url"], hubweb["cfg"]
    _joined(cfg)
    net.on("POST", lb.FIRESTORE + ":runQuery", [{"document": {"name": f"{lb.DB}/players/uid1", "fields": {
        "name": {"stringValue": "Ada"}, "photo": {"stringValue": PHOTO}, "s7": {"integerValue": "4"},
        "at": {"timestampValue": to_iso(utcnow())}}}}])
    status, data = _http(url, "/api/leaderboard?by=sessions&days=7")
    assert status == 200 and data["by"] == "sessions" and data["days"] == 7
    assert set(data["mine"]) == {"hours", "sessions", "tokens"} and data["me"]["name"] == "Ada"
    assert data["rows"] == [{"rank": 1, "uid": "uid1", "name": "Ada", "photo": True, "at": data["rows"][0]["at"],
                             "hours": 0, "sessions": 4, "tokens": 0, "me": True}]
    assert "R0" not in json.dumps(data) and PHOTO not in json.dumps(data)  # no token, and pictures come through here
    # a phone through Tailscale may open this hub's dashboard (no people), but not its person's leaderboard
    assert _http(url, "/api/devices", headers=TAILNET)[0] == 200
    for path, body in (("/api/leaderboard", None), ("/api/leaderboard/avatar/uid1", None), ("/api/leaderboard/send", {}),
                       ("/api/leaderboard/signin", {}), ("/api/leaderboard/finish", {}), ("/api/leaderboard/name", {"name": "x"}),
                       ("/api/leaderboard/leave", {})):
        assert _http(url, path, body, headers=TAILNET)[0] == 403, path
    assert lb.read_state(cfg)["name"] == "Ada"  # another device's leave or rename did nothing
    assert not [c for c in net.calls if c["method"] != "POST" or ":runQuery" not in c["url"]]


def test_an_error_firestore_answers_as_a_list_is_still_a_leaderboard_error(monkeypatch):
    """runQuery reports a refusal as `[{"error": {...}}]`, not an object: it must read as the refusal it is."""
    import io
    from urllib.error import HTTPError

    body = json.dumps([{"error": {"code": 403, "message": "Missing or insufficient permissions."}}]).encode()

    def refuse(req, timeout=None):
        raise HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(body))

    monkeypatch.setattr(lb, "urlopen", refuse)
    with pytest.raises(lb.LeaderboardError, match="403 Missing or insufficient permissions"):
        lb._call("POST", lb.FIRESTORE + ":runQuery", body={})
    for odd in (b"[]", b"7", b'"x"'):
        monkeypatch.setattr(lb, "urlopen", lambda req, timeout=None, b=odd: (_ for _ in ()).throw(
            HTTPError(req.full_url, 500, "Server Error", {}, io.BytesIO(b))))
        with pytest.raises(lb.LeaderboardError, match="500"):
            lb._call("GET", lb.FIRESTORE + "/players/x")
