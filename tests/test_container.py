"""A hub in a container (container.py, docker/): set up from INTERLATCH_* (or CHRONICLE_*) variables, never served without people, and
no request through the network counts as made at the hub."""

import signal
import urllib.error
import urllib.request

import pytest

from chronicle import container, hub, people, update
from chronicle.config import load_config, set_config_value
from chronicle.db import connect

from test_hub import _serve

ENV = {"INTERLATCH_HUB_URL": "https://chronicle.example.com/", "INTERLATCH_ADMIN_EMAIL": "ada@example.com",
       "INTERLATCH_ADMIN_NAME": "Ada", "INTERLATCH_WORK_MINUTES": "0"}


@pytest.fixture()
def served(env, monkeypatch):
    """container.main() up to serving: what it would serve, without binding a port or touching SIGTERM for good."""
    got = {}

    def serve(cfg, **kw):  # the first start's config and arguments are kept
        got.setdefault("cfg", cfg)
        got.setdefault("kw", kw)

    monkeypatch.setattr("chronicle.server.serve", serve)
    before = signal.getsignal(signal.SIGTERM)
    yield env, got
    signal.signal(signal.SIGTERM, before)


def test_first_start_makes_a_knowledge_only_hub_reached_through_its_address(env):
    cfg, first = container.configure(env["cfg"], ENV)
    assert first and hub.read_token(cfg)
    assert cfg.server_host == "0.0.0.0" and cfg.server_port == 11524
    assert cfg.server_behind_proxy
    assert cfg.server_allowed_hosts == ["chronicle.example.com"]
    assert cfg.hub_address == "https://chronicle.example.com"
    assert cfg.hub_shared_token is False
    assert cfg.hub_name == "Interlatch hub"  # not the container's random host name
    assert cfg.hub_dedicated is True  # a server for the team: its dashboard leaves out a person's own computer
    text = cfg.config_path.read_text()
    assert 'accept = "knowledge"' in text
    assert "claude_dirs" in text  # the rest of config.toml is kept


def test_later_starts_keep_what_an_admin_changed(env):
    cfg, _ = container.configure(env["cfg"], {**ENV, "INTERLATCH_HUB_NAME": "Platform team"})
    token = hub.read_token(cfg)
    set_config_value(cfg, "hub", "shared_token", "true")
    set_config_value(cfg, "hub", "accept", '"everything"')
    cfg, first = container.configure(load_config(cfg.home), {**ENV, "INTERLATCH_HUB_NAME": "", "INTERLATCH_PORT": "9000",
                                                             "INTERLATCH_ALLOWED_HOSTS": "hub.internal, Other.Example"})
    assert not first and hub.read_token(cfg) == token  # computers that joined keep working
    assert cfg.hub_shared_token is True and 'accept = "everything"' in cfg.config_path.read_text()
    assert cfg.hub_name == "Platform team"  # an empty variable leaves config.toml alone
    assert cfg.server_port == 9000
    assert cfg.server_allowed_hosts == ["chronicle.example.com", "hub.internal", "other.example"]


@pytest.mark.parametrize("change, problem", [
    ({"INTERLATCH_HUB_URL": ""}, "INTERLATCH_HUB_URL is not set"),
    ({"INTERLATCH_HUB_URL": "chronicle.example.com"}, "must be an address"),
    ({"INTERLATCH_HUB_URL": "https://chronicle.example.com/hub"}, "must be an address"),
    ({"INTERLATCH_PORT": "web"}, "INTERLATCH_PORT"),
    ({"INTERLATCH_TEAM_STORE": "mysql"}, "INTERLATCH_TEAM_STORE"),
])
def test_wrong_variables_stop_the_container(env, change, problem):
    with pytest.raises(container.SetupError, match=problem):
        container.configure(env["cfg"], {**ENV, **change})
    assert not hub.read_token(env["cfg"])


def test_variables_from_before_the_rename_still_count(env):
    """A hub's .env written for Chronicle names CHRONICLE_* variables; an INTERLATCH_* one wins, an empty one is unset."""
    old = {k.replace("INTERLATCH_", "CHRONICLE_"): v for k, v in ENV.items()}
    cfg, _ = container.configure(env["cfg"], {**old, "CHRONICLE_HUB_NAME": "Old name", "INTERLATCH_HUB_NAME": "",
                                              "CHRONICLE_PORT": "9000", "INTERLATCH_PORT": "9001"})
    assert cfg.hub_address == "https://chronicle.example.com" and cfg.hub_name == "Old name" and cfg.server_port == 9001


def test_the_team_store_comes_from_the_variables(env):
    cfg, _ = container.configure(env["cfg"], {**ENV, "INTERLATCH_TEAM_STORE": "Postgres"})
    assert cfg.hub_store == "postgres"


def test_the_first_admin_is_added_once_with_an_invite(env):
    cfg, _ = container.configure(env["cfg"], ENV)
    conn = connect(cfg.db_path)
    try:
        lines = container.first_admin(cfg, conn, ENV)
        ada = people.by_email(conn, "ada@example.com")
        assert ada["role"] == "admin" and ada["name"] == "Ada"
        text = "\n".join(lines)
        assert "https://chronicle.example.com/signin?code=" in text
        assert "interlatch hub join https://chronicle.example.com --code" in text and "--share knowledge" in text
        assert container.first_admin(cfg, conn, {**ENV, "CHRONICLE_ADMIN_EMAIL": "eve@example.com"}) == []
        assert people.by_email(conn, "eve@example.com") is None
    finally:
        conn.close()


def test_never_serves_a_hub_without_people(served, capsys):
    env, got = served
    assert container.main({**ENV, "INTERLATCH_ADMIN_EMAIL": ""}) == 2
    assert "cfg" not in got
    assert "INTERLATCH_ADMIN_EMAIL is not set" in capsys.readouterr().err
    assert container.main({**ENV, "INTERLATCH_ADMIN_EMAIL": "not an email"}) == 2
    assert "cfg" not in got


def test_serves_once_set_up_and_prints_the_invite(served, capsys):
    env, got = served
    assert container.main(ENV) == 0
    cfg = got["cfg"]
    conn = connect(cfg.db_path)
    try:
        assert people.has_people(conn)
    finally:
        conn.close()
    out = capsys.readouterr()
    assert "/signin?code=" in out.out and "knowledge only" in out.out
    assert got["kw"]["banner"] == "Interlatch hub is up: https://chronicle.example.com"
    assert "not https" not in out.err
    assert container.main(ENV) == 0
    assert "/signin?code=" not in capsys.readouterr().out  # shown once


def test_plain_http_is_warned_about(served, capsys):
    _, got = served
    assert container.main({**ENV, "INTERLATCH_HUB_URL": "http://hub.lan:8080"}) == 0
    assert got["cfg"].server_allowed_hosts == ["hub.lan"]
    assert "not https" in capsys.readouterr().err


def test_a_request_from_loopback_is_not_an_admin(env, monkeypatch):
    """In a container, Caddy (or anything sharing its network) reaches the dashboard from 127.0.0.1 with Host
    127.0.0.1: without behind_proxy that would count as someone at the hub, who is always an admin."""
    monkeypatch.setattr(hub.IngestTrigger, "request", lambda self: None)
    cfg, _ = container.configure(env["cfg"], ENV)
    conn = connect(cfg.db_path)
    container.first_admin(cfg, conn, ENV)
    conn.close()
    _app, httpd, url = _serve(cfg)
    try:
        req = urllib.request.Request(url + "/api/people", headers={"X-Interlatch": "1"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=10)
        assert exc.value.code == 401
    finally:
        httpd.shutdown()


def test_a_container_install_does_not_update_itself(monkeypatch):
    monkeypatch.setattr(update, "_method", {})
    monkeypatch.setenv("CHRONICLE_CONTAINER", "1")  # its name before the rename still counts
    info = update.check()
    assert info["kind"] == "container" and not info["can_update"]
    assert "docker compose pull" in info["note"]


def test_the_banner_replaces_the_local_address(env, monkeypatch, capsys):
    """An editor connected to the hub's server (VS Code Remote-SSH) forwards any 127.0.0.1 address printed in its
    terminal to the same port on the viewer's own computer, hiding their own dashboard there."""
    from chronicle import server

    class Httpd:
        server_address = ("127.0.0.1", 11524)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(server, "make_server", lambda cfg, host, port: Httpd())
    server.serve(env["cfg"], banner="Chronicle hub is up: https://hub.example.com")
    out = capsys.readouterr().out
    assert out.strip() == "Chronicle hub is up: https://hub.example.com" and "127.0.0.1" not in out
    server.serve(env["cfg"])
    assert "http://127.0.0.1:11524/" in capsys.readouterr().out  # `chronicle ui` still says where it is
