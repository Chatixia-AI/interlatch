"""A push that started before the person changed what this computer shares (an analysis run takes minutes) tells the
hub what the config says when it shares, not what it said when the run began: the hub files by the last list of
folders it heard. One push or folder change at a time. And each folder of a project counts its own sessions."""

import pytest

from interlatch.sharing import client
from interlatch.projects.routing import folders_of
from interlatch.sharing.client import SHARING_LOCK, leave_project, sharing
from interlatch.sharing.projects import folders_report, left_of
from interlatch.sharing.protocol import HubError
from interlatch.sharing.push import push, push_knowledge
from interlatch.core.config import load_config, set_config_value, toml_table
from interlatch.core.util import file_lock
from interlatch.core.machine import local_machine

from conftest import CWD
from test_hub import SPOKE_CWD, SPOKE_SID, _analyzed_on_spoke
from test_leave_project import _in_project, _shared_at
from test_team import teamenv  # noqa: F401  (the fixture)

ADDED = "/home/test/code/coc-ai"  # a second folder of the same project, joined while a run was going


def test_a_folder_added_during_a_run_stays_on_the_hub(teamenv):  # noqa: F811
    conn = teamenv["conn"]
    started_with = _in_project(teamenv)  # the config a long run read before the folder was added
    me = local_machine(started_with)["id"]
    set_config_value(started_with, "hub", "folders", toml_table({SPOKE_CWD: CWD, ADDED: CWD}))  # Join a project
    push(started_with)  # the run ends and shares
    assert folders_of(conn, me) == {SPOKE_CWD: CWD, ADDED: CWD}  # not taken back out, its sessions not moved


def test_a_project_left_during_a_run_is_not_shared_again(teamenv):  # noqa: F811
    conn = teamenv["conn"]
    started_with = _in_project(teamenv)
    me = local_machine(started_with)["id"]
    leave_project(started_with, CWD)
    _analyzed_on_spoke(started_with, title="Mine, again", at="2026-10-03T00:00:00Z", lessons=[("p2", "project", "fact")])
    report = push_knowledge(started_with)  # what the analysis worker calls, with the config it began with
    assert report.sent == 0 and report.skipped == 1
    assert left_of(conn, me) == [CWD] and tuple(_shared_at(conn)) == (CWD, "2026-10-02T00:00:00Z")


def test_one_share_or_folder_change_at_a_time(teamenv, monkeypatch):  # noqa: F811
    spoke = _in_project(teamenv)
    monkeypatch.setattr(client, "CHANGE_WAIT_S", 0.3)
    with file_lock(spoke.locks_dir / SHARING_LOCK):  # another process is pushing
        with pytest.raises(HubError, match="still sharing"):
            leave_project(spoke, CWD)
    assert load_config(spoke.home).hub_left == []
    with sharing(spoke, 1):  # the thread that holds it may change folders: no waiting on itself
        assert leave_project(spoke, CWD).hub_left == [CWD]


def test_a_callers_own_changes_hold_until_the_file_changes(teamenv):  # noqa: F811
    from interlatch.core.config import current

    spoke = load_config(teamenv["spoke"].home)
    assert current(spoke) is spoke  # unchanged since read
    spoke.hub_left = ["/in/memory"]  # a test's, or a caller's, own change
    assert current(spoke).hub_left == ["/in/memory"]
    set_config_value(spoke, "hub", "all_folders", "false")
    assert current(spoke).hub_left == [] and current(spoke).hub_all_folders is False  # the file changed: it wins


def test_each_folder_counts_its_own_sessions(teamenv):  # noqa: F811
    conn = teamenv["conn"]
    spoke = _in_project(teamenv)  # one session, run in SPOKE_CWD, filed under CWD
    me = local_machine(spoke)["id"]
    assert conn.execute("SELECT machine_path FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == SPOKE_CWD
    report = folders_report(conn, me, {SPOKE_CWD: CWD, ADDED: CWD}, {}, {})
    assert (report[SPOKE_CWD]["sessions"], report[ADDED]["sessions"]) == (1, 0)
