"""One computer, two hubs (core.profiles): Personal sends to its hub, a named profile to its own, both from Personal's
one archive. Each hub gets only its owner's sessions, knows the computer by a different identity, and its teammates'
lessons reach only the agents in its owner's folders."""

import gzip
import json
import textwrap

import pytest

from interlatch.application import profiles as ops
from interlatch.core.config import load_config, set_config_value
from interlatch.core.machine import local_machine_id
from interlatch.sharing import client
from interlatch.sharing.identity import new_token, write_token
from interlatch.sharing.pull import apply_team_lessons
from interlatch.sharing.push import push
from interlatch.storage.sqlite import connect

from test_hub import SPOKE_CWD, SPOKE_SID, _analyzed_on_spoke, _serve
from hubs_fixture import hub_with
from test_team import teamenv  # noqa: F401  (the fixture)

MINE, MINE_SID = "/home/test/code/mine", "mine-0000-1111-2222-333333333333"


@pytest.fixture()
def twohubs(teamenv, tmp_path):  # noqa: F811
    """teamenv's spoke is Personal, joined to hub A; Acme owns SPOKE_CWD and joins hub B."""
    home = tmp_path / "hub-b"
    home.mkdir()
    (home / "config.toml").write_text(textwrap.dedent("""\
        [sources]
        claude_dirs = []
        [hub]
        name = "Hub B"
        """))
    hub_b = load_config(home)
    token = new_token(hub_b)
    _app, httpd, url = _serve(load_config(home))
    spoke = teamenv["spoke"]
    _analyzed_on_spoke(spoke, title="ACME_WORK billing", at="2026-10-02T00:00:00Z",
                       lessons=[("acme-gotcha", "project", "gotcha")])
    conn = connect(spoke.db_path)  # a second session, in a folder of Personal's
    row = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone())
    row.update(id=MINE_SID, project_path=MINE, project_name="mine", llm_title="PERSONAL_WORK notes", machine_path=None)
    conn.execute(f"INSERT INTO sessions({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))
    conn.commit()
    conn.close()
    p, _ = hub_with(spoke, "Acme", [SPOKE_CWD])
    acme = load_config(p.home)
    set_config_value(acme, "hub", "url", json.dumps(url))
    set_config_value(acme, "hub", "all_folders", "true")  # hub B has no projects yet: share every folder Acme owns
    write_token(acme, token)
    acme = load_config(p.home)
    teamenv.update(acme=acme, profile=p, hub_b=hub_b, url_b=url)
    yield teamenv
    httpd.shutdown()


def _hub_has(cfg, sid=SPOKE_SID) -> bool:
    conn = connect(cfg.db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (sid,)).fetchone()[0] == 1
    finally:
        conn.close()


def test_each_hub_gets_only_its_owners_sessions(twohubs, monkeypatch):
    spoke, acme, hub_a, hub_b = twohubs["spoke"], twohubs["acme"], twohubs["cfg"], twohubs["hub_b"]
    sent: dict[str, list[str]] = {}
    request = client.HubClient.request

    def spy(self, method, path, **kw):
        body = kw.get("body")
        if isinstance(body, (bytes, bytearray)):
            try:
                body = gzip.decompress(body)
            except OSError:
                pass
            text = bytes(body).decode("utf-8", "replace")
        else:
            text = json.dumps(body)
        sent.setdefault(self.url, []).append(text)
        return request(self, method, path, **kw)

    monkeypatch.setattr(client.HubClient, "request", spy)
    assert push(spoke).sent == 1  # Personal's hub: Personal's session, nothing of Acme's
    assert push(acme).sent == 1
    assert _hub_has(hub_b) and not _hub_has(hub_a)
    assert _hub_has(hub_a, MINE_SID) and not _hub_has(hub_b, MINE_SID)
    to_a = "\n".join(sent[twohubs["url"].rstrip("/")])
    assert "ACME_WORK" not in to_a and SPOKE_CWD not in to_a and "acme-gotcha" not in to_a
    to_b = "\n".join(sent[twohubs["url_b"]])
    assert "ACME_WORK" in to_b and "PERSONAL_WORK" not in to_b and MINE not in to_b
    # the two hubs know this computer by two identities
    ids = {r[0] for r in connect(hub_b.db_path).execute("SELECT id FROM machines WHERE role != 'this'")}
    assert local_machine_id(acme) in ids and local_machine_id(spoke) not in ids
    assert local_machine_id(spoke) != local_machine_id(acme)


def test_the_cli_and_the_worker_send_for_every_profile(twohubs, monkeypatch):
    from interlatch.application.workflows import run_worker
    from interlatch.interfaces.cli.main import main

    spoke, hub_a, hub_b = twohubs["spoke"], twohubs["cfg"], twohubs["hub_b"]
    assert [c.owner for c in ops.joined(spoke)] == [twohubs["profile"].id]
    monkeypatch.setenv("INTERLATCH_HOME", str(spoke.home))
    assert main(["push", "--quiet"]) == 0
    assert _hub_has(hub_b) and not _hub_has(hub_a)
    report = run_worker(load_config(spoke.home), analyze=False, synthesize=False)
    assert "Acme:" in (report.shared or "")


def test_teammates_lessons_from_two_hubs_live_side_by_side(twohubs, monkeypatch):
    """Each hub's lessons are kept under its owner; one hub's pull never removes the other's, and agents in a folder
    see only its owner's hub's teammates."""
    import os

    from interlatch.interfaces.mcp import Tools

    spoke, acme = twohubs["spoke"], twohubs["acme"]
    mine = {"lessons": [{"id": 1, "title": "PERSONAL_TEAM lesson", "kind": "gotcha", "place": "x"}],
            "places": {"x": {"mine": SPOKE_SID}}, "hub": "A"}
    theirs = {"lessons": [{"id": 1, "title": "ACME_TEAM lesson", "kind": "gotcha", "place": "x"}],
              "places": {"x": {"mine": SPOKE_SID}}, "hub": "B"}
    assert apply_team_lessons(spoke, mine, {}) == 1
    assert apply_team_lessons(acme, theirs, {}) == 1
    assert apply_team_lessons(spoke, {"lessons": [], "hub": "A"}, {}) == 0  # Personal's hub sent none this time
    conn = connect(spoke.db_path)
    titles = {r[0]: r[1] for r in conn.execute("SELECT title, fingerprint FROM knowledge WHERE source = 'team'")}
    conn.close()
    assert titles == {"ACME_TEAM lesson": f"team:{acme.owner}:1"}
    apply_team_lessons(spoke, mine, {})
    monkeypatch.setattr(os, "getcwd", lambda: SPOKE_CWD)
    text = Tools(spoke).search_knowledge("lesson")
    assert "ACME_TEAM" in text and "PERSONAL_TEAM" not in text  # Acme's folder: Acme's hub's teammates only


def test_leaving_one_hub_keeps_the_other_and_personals_groups(twohubs):
    from interlatch.sharing.membership import leave

    spoke, acme = twohubs["spoke"], twohubs["acme"]
    conn = connect(spoke.db_path)
    conn.execute("INSERT INTO project_groups(name, folders_json, hub_project, created_at) VALUES "
                 "('Mine', '[]', '/hub/mine', '2026-10-01')")
    conn.commit()
    conn.close()
    apply_team_lessons(spoke, {"lessons": [{"id": 1, "title": "PERSONAL_TEAM", "kind": "gotcha", "place": "x"}],
                               "places": {"x": {"mine": SPOKE_SID}}, "hub": "A"}, {})
    apply_team_lessons(acme, {"lessons": [{"id": 2, "title": "ACME_TEAM", "kind": "gotcha", "place": "x"}],
                              "places": {"x": {"mine": SPOKE_SID}}, "hub": "B"}, {})
    leave(acme)
    conn = connect(spoke.db_path)
    assert [r[0] for r in conn.execute("SELECT title FROM knowledge WHERE source = 'team'")] == ["PERSONAL_TEAM"]
    assert conn.execute("SELECT hub_project FROM project_groups WHERE name = 'Mine'").fetchone()[0] == "/hub/mine"
    conn.close()
    assert load_config(spoke.home).is_spoke and not load_config(acme.home).is_spoke


def test_a_profile_shares_none_of_personals_groups(twohubs):
    from interlatch.sharing.projects import group_routes

    acme = twohubs["acme"]
    conn = connect(acme.db_path)
    conn.execute("INSERT INTO project_groups(name, folders_json, hub_project, created_at) VALUES "
                 f"('Mine', '[\"{SPOKE_CWD}\"]', '/hub/mine', '2026-10-01')")
    conn.commit()
    conn.close()
    assert not group_routes(acme) and not group_routes(acme).projects()


def test_a_session_goes_by_its_recorded_owner_not_only_its_folder(twohubs):
    """A session keeps the owner it was given, even when its folder now says another profile: its hub gets it only
    if both agree (here Personal's, recorded before; Acme's hub must not get it)."""
    spoke, acme, hub_b = twohubs["spoke"], twohubs["acme"], twohubs["hub_b"]
    conn = connect(spoke.db_path)
    conn.execute("UPDATE session_owner SET owner = 'personal' WHERE session_id = ?", (SPOKE_SID,))
    conn.commit()
    conn.close()
    assert push(acme).sent == 0 and not _hub_has(hub_b)
