"""What a computer that shares knowledge sends ([hub] all_folders), and taking back what it sent (`chronicle hub
purge`): someone given every project shared sessions from folders they never meant to share."""

import pytest

from interlatch.projects import routing
from interlatch.sharing import people
from interlatch.sharing.identity import machine_key, read_token, write_token
from interlatch.sharing.protocol import HubError
from interlatch.sharing.push import _shared_records, push
from interlatch.sharing.receiver import purge
from interlatch.interfaces.cli.main import main
from interlatch.core.config import load_config
from interlatch.storage.sqlite import connect
from interlatch.core.machine import local_machine

from conftest import CWD, SID
from test_hub import SPOKE_CWD, SPOKE_SID, _analyzed_on_spoke
from test_team import teamenv  # noqa: F401  (a fixture)


def test_only_the_hubs_projects_by_default(tmp_path, monkeypatch):
    home = tmp_path / "member"
    home.mkdir()
    (home / "config.toml").write_text('[hub]\nurl = "http://hub"\nshare = "knowledge"\n'
                                      '[hub.folders]\n"/home/yuma/resona" = "/srv/Resona"\n')
    monkeypatch.setenv("INTERLATCH_HOME", str(home))
    cfg = load_config(home)
    assert cfg.hub_all_folders is False
    conn = connect(cfg.db_path)
    for sid, path in (("added", "/home/yuma/resona/app"), ("repo", "/home/yuma/misc/lib"),
                      ("private", "/home/yuma/eneos/app"), ("nowhere", None)):
        conn.execute("INSERT INTO sessions(id, source, agent, project_path, analysis_status, analyzed_at) VALUES "
                     "(?, 'transcript', 'claude', ?, 'done', '2026-10-01')", (sid, path))
    conn.commit()
    conn.close()
    monkeypatch.setattr(routing, "git_info", lambda p: ("/home/yuma/misc/lib", "github.com/acme/lib") if p.endswith("lib") else None)
    remotes = {"github.com/acme/lib": "/srv/Lib"}
    recs, _, kept = _shared_records(cfg, {}, remotes=remotes, projects_only=True)
    assert sorted(r["id"] for r in recs) == ["added", "repo"] and kept == 2
    have = {r["id"]: r["analyzed_at"] for r in recs}
    recs, unchanged, kept = _shared_records(cfg, have, remotes=remotes, projects_only=True)
    assert recs == [] and unchanged == 2 and kept == 2
    assert len(_shared_records(cfg, {}, remotes=remotes)[0]) == 4  # all_folders = true

    for raw, want in (("true", True), ('"true"', False), ("1", False), ("false", False)):  # only an explicit true
        (home / "config.toml").write_text(f'[hub]\nurl = "http://hub"\nshare = "knowledge"\nall_folders = {raw}\n')
        assert load_config(home).hub_all_folders is want, raw


def test_join_keeps_to_the_hubs_projects_unless_asked(teamenv, monkeypatch, capsys):  # noqa: F811
    spoke = teamenv["spoke"]
    monkeypatch.setenv("INTERLATCH_HOME", str(spoke.home))
    token = read_token(spoke)
    assert main(["hub", "join", teamenv["url"], f"--token={token}", "--share", "knowledge", "--no-push"]) == 0
    assert "Only sessions in the hub's projects are shared" in capsys.readouterr().out
    assert load_config(spoke.home).hub_all_folders is False
    assert main(["hub", "join", teamenv["url"], f"--token={token}", "--share", "knowledge", "--all-folders",
                 "--no-push"]) == 0
    assert "from every folder" in capsys.readouterr().out
    assert load_config(spoke.home).hub_all_folders is True


def _yuma(conn, spoke):
    """Yuma, given every project, and the spoke as her computer, sending with her token from now on (the hub's
    shared token no longer acts as a computer that joined as a person)."""
    p = people.add(conn, "Yuma", "yuma@example.com", "member", projects=None)
    _, token = people.join_computer(conn, people.invite(conn, p["id"]), local_machine(spoke)["id"], "Yuma's Mac",
                                    machine_key(spoke))  # it pushed before: the hub has its key
    write_token(spoke, token)
    return p


def test_purge_what_someone_shared_outside_their_projects(teamenv, monkeypatch, capsys):  # noqa: F811
    fake, spoke, conn, cfg = teamenv["fake"], teamenv["spoke"], teamenv["conn"], teamenv["cfg"]
    _analyzed_on_spoke(spoke, title="Private work", at="2026-10-02T00:00:00Z", lessons=[("p1", "project", "fact")])
    assert push(spoke).sent == 1
    assert SPOKE_SID in fake.sessions and fake.lessons
    conn.execute("INSERT INTO project_kb(project_path, project_name, updated_at, markdown) VALUES (?, 'demo-app', "
                 "'2026-10-02', 'lesson p1')", (SPOKE_CWD,))
    conn.commit()
    yuma = _yuma(conn, spoke)

    assert main(["hub", "purge", "yuma@example.com", "--outside-access"]) == 1
    assert "sees every project" in capsys.readouterr().out
    people.set_projects(conn, yuma["id"], [CWD])
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert main(["hub", "purge", "yuma@example.com", "--outside-access"]) == 1
    out = capsys.readouterr().out
    assert "1 session from Yuma's computers outside demo-app" in out and "Nothing removed" in out
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == 1

    assert main(["hub", "purge", "yuma@example.com", "--outside-access", "--yes"]) == 0
    assert "Removed 1 session here and in the team store" in capsys.readouterr().out
    gone = connect(cfg.db_path)
    assert gone.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == 0
    assert gone.execute("SELECT COUNT(*) FROM knowledge WHERE session_id = ?", (SPOKE_SID,)).fetchone()[0] == 0
    assert gone.execute("SELECT COUNT(*) FROM project_kb WHERE project_path = ?", (SPOKE_CWD,)).fetchone()[0] == 0
    assert gone.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (SID,)).fetchone()[0] == 1  # the hub's own
    assert people.audit_log(gone)[0]["action"] == "purge"
    gone.close()
    assert SPOKE_SID not in fake.sessions and fake.lessons == {}

    # the computer still has it and offers it again (Yuma seeing every project once more): the hub doesn't take it back
    people.set_projects(conn, yuma["id"], None)
    report = push(spoke)
    assert report.sent == 1 and not report.errors
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == 0
    assert SPOKE_SID not in fake.sessions


def test_purge_a_computers_project_and_never_the_hubs_own(teamenv, capsys):  # noqa: F811
    spoke, conn, cfg = teamenv["spoke"], teamenv["conn"], teamenv["cfg"]
    _analyzed_on_spoke(spoke, title="Private work", at="2026-10-02T00:00:00Z", lessons=[])
    assert push(spoke).sent == 1
    name = local_machine(spoke)["name"]
    assert main(["hub", "purge", name, "--project", SPOKE_CWD, "--yes"]) == 0
    assert "Removed 1 session" in capsys.readouterr().out
    assert main(["hub", "purge", name, "--project", SPOKE_CWD, "--yes"]) == 1  # it was its only session there
    assert "no project called" in capsys.readouterr().out
    assert main(["hub", "purge", name, "--project", CWD, "--yes"]) == 0
    assert "Nothing from the computer" in capsys.readouterr().out
    assert main(["hub", "purge", "nobody", "--project", SPOKE_CWD]) == 1
    assert main(["hub", "purge", name]) == 2  # which projects?

    own = [dict(id=SID, project_path=CWD, transcript_path=None)]
    with pytest.raises(HubError, match="not this hub's own"):
        purge(cfg, conn, own)
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (SID,)).fetchone()[0] == 1
