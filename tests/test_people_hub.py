"""People on a hub, from the hub API and the command line: who may send, joining with an invite, sign-in links."""

import json
import re
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from interlatch import hub, people
from interlatch.config import load_config
from interlatch.db import connect

MACHINE = "11111111-2222-4333-8444-555555555555"
OTHER = "99999999-2222-4333-8444-555555555555"
CODE_RE = re.compile(r"\b[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}\b")


@pytest.fixture()
def hubcfg(env):
    """The `env` computer as a hub (it has a shared token), with its database open."""
    cfg = env["cfg"]
    token = hub.new_token(cfg)
    conn = connect(cfg.db_path)
    env.update(token=token, conn=conn)
    yield env
    conn.close()


def _bearer(token: str) -> str:
    return f"Bearer {token}"


def test_authorize_shared_token_and_person_tokens(hubcfg):
    cfg, conn, token = hubcfg["cfg"], hubcfg["conn"], hubcfg["token"]
    assert hub.authorize(cfg, conn, _bearer(token), MACHINE) == (True, None)  # no people yet: as before
    assert hub.authorize(cfg, conn, _bearer("nope"), MACHINE) == (False, None)
    assert hub.authorize(cfg, conn, None, MACHINE) == (False, None)
    assert hub.token_ok(cfg, _bearer(token))  # what server.py calls until it moves to authorize

    cfg.hub_shared_token = False
    assert hub.authorize(cfg, conn, _bearer(token), MACHINE) == (True, None)  # still no people: still works
    bob = people.add(conn, "Bob", "bob@example.com", "member")
    assert hub.authorize(cfg, conn, _bearer(token), MACHINE) == (False, None)
    cfg.hub_shared_token = True
    assert hub.authorize(cfg, conn, _bearer(token), MACHINE) == (True, None)

    got = hub.join_with_code(cfg, conn, {"code": people.invite(conn, bob["id"]), "machine": MACHINE, "name": "Bob laptop"})
    ok, who = hub.authorize(cfg, conn, _bearer(got["token"]), MACHINE)
    assert ok and who["id"] == bob["id"]
    assert hub.authorize(cfg, conn, _bearer(got["token"]), OTHER) == (False, None)  # another computer
    people.set_role(conn, bob["id"], "readonly")
    assert hub.authorize(cfg, conn, _bearer(got["token"]), MACHINE)[1]["role"] == "readonly"  # server answers 403
    hub.token_path(cfg).unlink()  # `chronicle hub disable`: nobody gets in
    assert hub.authorize(cfg, conn, _bearer(got["token"]), MACHINE) == (False, None)


def test_join_with_code(hubcfg):
    cfg, conn = hubcfg["cfg"], hubcfg["conn"]
    admin = people.add(conn, "Ada", "ada@example.com", "admin")
    code = people.invite(conn, admin["id"])
    with pytest.raises(hub.HubError, match="bad machine id"):
        hub.join_with_code(cfg, conn, {"code": code, "machine": "x"})
    with pytest.raises(hub.HubError, match="isn't known"):
        hub.join_with_code(cfg, conn, {"code": "AAAA-BBBB-CCCC", "machine": OTHER})
    assert conn.execute("SELECT COUNT(*) FROM machines WHERE id = ?", (OTHER,)).fetchone()[0] == 0  # a bad code records nothing

    got = hub.join_with_code(cfg, conn, {"code": code.lower(), "machine": MACHINE, "name": "Ada laptop",
                                         "platform": "macOS", "version": "9.9"})
    assert got["person"] == people.public(admin) and got["hub"] == hub.local_machine(cfg)["name"] and got["token"]
    row = conn.execute("SELECT name, platform, role, person_id FROM machines WHERE id = ?", (MACHINE,)).fetchone()
    assert tuple(row) == ("Ada laptop", "macOS", "spoke", admin["id"])
    with pytest.raises(hub.HubError, match="already used"):
        hub.join_with_code(cfg, conn, {"code": code, "machine": MACHINE})

    viewer = people.add(conn, "Vic", None, "readonly")
    code = people.invite(conn, viewer["id"])
    with pytest.raises(hub.HubError, match="read-only"):
        hub.join_with_code(cfg, conn, {"code": code, "machine": OTHER})
    assert people.open_browser(conn, code)[0]["id"] == viewer["id"]  # the code still opens a browser

    hub.token_path(cfg).unlink()
    with pytest.raises(hub.HubError, match="not a hub"):
        hub.join_with_code(cfg, conn, {"code": people.invite(conn, admin["id"]), "machine": OTHER})


def test_signin_code_for(hubcfg):
    cfg, conn = hubcfg["cfg"], hubcfg["conn"]
    with pytest.raises(hub.HubError, match="join with an invite to sign in"):
        hub.signin_code_for(cfg, conn, None)  # the shared token is nobody in particular
    bob = people.add(conn, "Bob", None, "member")
    code = hub.signin_code_for(cfg, conn, bob)["code"]
    assert people.open_browser(conn, code)[0]["id"] == bob["id"]


def _stub_hub(cfg):
    """The hub's /api/hub/join and /api/hub/signin, routed the way the contract has server.py route them."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            conn = connect(cfg.db_path)
            try:
                if self.path == "/api/hub/join":
                    out, status = hub.join_with_code(cfg, conn, body), 200
                else:
                    ok, person = hub.authorize(cfg, conn, self.headers.get("Authorization"), body.get("machine"))
                    out, status = (hub.signin_code_for(cfg, conn, person), 200) if ok else ({"error": "unauthorized"}, 401)
            except hub.HubError as exc:
                out, status = {"error": str(exc)}, 400
            finally:
                conn.close()
            data = json.dumps(out).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _spoke_home(tmp):
    home = tmp / "spoke-home"
    home.mkdir()
    (home / "config.toml").write_text(textwrap.dedent("""\
        [sources]
        claude_dirs = []
        codex_dirs = []
        """))
    return home


def _out(capsys) -> str:
    return " ".join(capsys.readouterr().out.split())  # rich wraps long lines


def test_join_with_an_invite_then_sign_in(hubcfg, monkeypatch, capsys):
    from interlatch.cli import main

    cfg, conn = hubcfg["cfg"], hubcfg["conn"]
    bob = people.add(conn, "Bob", "bob@example.com", "member")
    code = people.invite(conn, bob["id"])
    httpd, url = _stub_hub(cfg)
    try:
        home = _spoke_home(hubcfg["tmp"])
        monkeypatch.setenv("INTERLATCH_HOME", str(home))
        assert main(["hub", "join", url, "--no-push"]) == 2  # a code or a token
        assert main(["hub", "join", url, "--code", code, "--token", "t", "--no-push"]) == 2
        capsys.readouterr()
        assert main(["hub", "join", url, "--code", "AAAA-BBBB-CCCC", "--no-push"]) == 1
        assert "isn't known" in _out(capsys)
        assert not load_config(home).is_spoke

        assert main(["hub", "join", url, "--code", code, "--share", "knowledge", "--no-push"]) == 0
        assert "knows you as Bob (member)" in _out(capsys)
        spoke = load_config(home)
        assert spoke.hub_url == url and spoke.shares_knowledge
        ok, who = hub.authorize(cfg, conn, _bearer(hub.read_token(spoke)), hub.local_machine(spoke)["id"])
        assert ok and who["id"] == bob["id"]

        assert main(["hub", "signin"]) == 0
        link = re.search(r"(\S+/signin\?code=\S+)", capsys.readouterr().out).group(1)
        assert link.startswith(f"{url}/signin?code=") and link == link.strip()
        assert people.open_browser(conn, link.split("code=", 1)[1])[0]["id"] == bob["id"]

        hub.write_token(spoke, hubcfg["token"])  # Bob's computer with the shared token: the hub refuses it
        assert main(["hub", "signin"]) == 1
        assert "did not accept this computer's token" in _out(capsys)
    finally:
        httpd.shutdown()


def test_join_with_a_token_still_works(env, monkeypatch, capsys):
    from interlatch.cli import main

    home = _spoke_home(env["tmp"])
    monkeypatch.setenv("INTERLATCH_HOME", str(home))
    assert main(["hub", "join", "hub.example.ts.net", "--token", "shared", "--no-push"]) == 0
    spoke = load_config(home)
    assert spoke.hub_url == "https://hub.example.ts.net" and hub.read_token(spoke) == "shared"

    # a token can start with "-" (token_urlsafe): the printed command still parses
    command = hub.join_command("https://hub.example.ts.net", "-Ab_9")
    assert command.endswith("--token=-Ab_9")
    assert main([*command.split()[1:], "--no-push"]) == 0 and hub.read_token(load_config(home)) == "-Ab_9"


def test_redeem_at_a_hub_without_invites(env, monkeypatch):
    def old_hub(self, method, path, **kw):
        raise hub.HubUnauthorized("the hub did not accept this computer's token")

    monkeypatch.setattr(hub.HubClient, "request", old_hub)
    with pytest.raises(hub.HubError, match="doesn't take invite codes yet"):
        hub.redeem_invite(env["cfg"], "https://old-hub.example", "AAAA-BBBB-CCCC")


def test_people_from_the_command_line(hubcfg, monkeypatch, capsys):
    import interlatch.cli as cli

    conn, home = hubcfg["conn"], hubcfg["home"]
    monkeypatch.setattr(cli, "_wait_for_port", lambda *a, **kw: True)
    main = cli.main
    assert main(["hub", "people"]) == 0
    assert "No people on this hub yet" in _out(capsys)

    assert main(["hub", "enable", "--url", "https://chronicle.example.internal"]) == 0
    assert load_config(home).hub_address == "https://chronicle.example.internal"
    capsys.readouterr()

    assert main(["hub", "invite", "Ada", "--email", "ada@example.com", "--role", "admin"]) == 0
    out = _out(capsys)
    code = CODE_RE.search(out).group(0)
    assert "Added Ada (ada@example.com), an admin" in out
    assert f"interlatch hub join https://chronicle.example.internal --code {code} --share knowledge" in out
    assert f"https://chronicle.example.internal/signin?code={code}" in out
    assert main(["hub", "invite", "Ada", "--email", "ADA@example.com"]) == 0  # a new code for the same person
    out = _out(capsys)
    assert "New invite for Ada" in out and CODE_RE.search(out).group(0) != code
    assert [p["name"] for p in people.listing(conn)] == ["Ada"]

    assert main(["hub", "invite", "Vic", "--email", "vic@example.com", "--role", "readonly", "--all-projects"]) == 0
    out = _out(capsys)
    assert "read-only" in out and "hub join" not in out and "/signin?code=" in out  # a browser only
    assert main(["hub", "invite", "Max", "--email", "not-an-email", "--all-projects"]) == 1
    assert "not an email address" in _out(capsys)

    assert main(["hub", "people"]) == 0
    out = _out(capsys)
    assert "2 people on this hub · shared token on" in out and "vic@example.com" in out and "invite open until" in out
    vic = people.by_email(conn, "vic@example.com")
    assert main(["hub", "role", str(vic["id"]), "member"]) == 0
    assert "Vic is now a member" in _out(capsys)
    assert people.get(conn, vic["id"])["role"] == "member"
    assert main(["hub", "role", "vic@example.com", "owner"]) == 1
    assert "role must be one of" in _out(capsys)
    assert main(["hub", "role", "999", "admin"]) == 1
    assert "No one on this hub" in _out(capsys)
    assert main(["hub", "role", "vic@example.com"]) == 2
    assert main(["hub", "remove", "vic@example.com"]) == 0
    assert "Removed Vic" in _out(capsys)
    assert [p["name"] for p in people.listing(conn)] == ["Ada"]

    conn.execute("INSERT INTO machines(id, name, role) VALUES (?, 'Old laptop', 'spoke')", (OTHER,))
    conn.commit()
    assert main(["hub", "shared-token", "off"]) == 0
    out = _out(capsys)
    assert "shared token is refused" in out and "Old laptop" in out
    assert load_config(home).hub_shared_token is False
    assert hub.authorize(load_config(home), conn, _bearer(hubcfg["token"]), OTHER) == (False, None)
    assert main(["hub", "shared-token", "on"]) == 0
    assert load_config(home).hub_shared_token is True
    assert main(["hub", "shared-token", "maybe"]) == 2
    assert [a["detail"] for a in people.audit_log(conn) if a["action"] == "shared-token"] == [{"on": True}, {"on": False}]


def test_people_commands_belong_to_the_hub(env, monkeypatch, capsys):
    from interlatch.cli import main
    from interlatch.config import set_config_value

    set_config_value(env["cfg"], "hub", "url", '"https://hub.example"')
    assert main(["hub", "invite", "Ada"]) == 1
    assert "People belong to the hub" in _out(capsys)
