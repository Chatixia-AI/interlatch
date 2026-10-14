"""Your own groups of projects (groups.py): folder rules (the longest wins), hand picks over them, a group as a filter,
and what the Projects page and a limited person see."""

import pytest

from interlatch.sharing import access
from interlatch.projects import groups
from interlatch.server import App

AKTIO = "/Users/me/Work/AI-BPO/Aktio"
VM = "aktio-vm:/root/Aktio"
PATHS = [f"{AKTIO}/operator_dashboard", f"{AKTIO}/process_monitor", AKTIO, f"{VM}/ai_quotation_processor",
         "/Users/me/Work/AI-BPO/Cosmo/procure", "/Users/me/Work/AI-BPO/Aktio-old", "claude.ai"]


@pytest.fixture()
def projects(synced):
    conn = synced["conn"]
    for i, p in enumerate(PATHS):
        conn.execute("INSERT INTO sessions(id, project_path, project_name, started_at) VALUES (?, ?, ?, ?)",
                     (f"g-{i}", p, p.rsplit("/", 1)[-1], f"2026-10-0{i + 1}T00:00:00Z"))
    conn.commit()
    return synced


def _placed(conn):
    return {p: g for p, (g, _) in groups.assign(conn, PATHS).items()}


def test_folder_rules_take_in_whole_folders_and_the_longest_wins(projects):
    conn = projects["conn"]
    work = groups.save(conn, None, "AI-BPO", ["/Users/me/Work/AI-BPO/"])  # a trailing slash is the same folder
    aktio = groups.save(conn, None, "Aktio", [AKTIO, VM])
    placed = _placed(conn)
    assert [p for p, g in placed.items() if g == aktio] == PATHS[:4]  # the folder itself too, and the VM's copy
    assert placed["/Users/me/Work/AI-BPO/Aktio-old"] == work  # Aktio-old is not inside Aktio
    assert placed["/Users/me/Work/AI-BPO/Cosmo/procure"] == work and placed["claude.ai"] is None
    assert groups.assign(conn, [AKTIO])[AKTIO] == (aktio, "folder")
    assert groups.groups(conn)[0] == {"id": work, "name": "AI-BPO", "folders": ["/Users/me/Work/AI-BPO"], "hub_project": None}


def test_a_hand_pick_wins_and_is_kept_only_where_the_rules_differ(projects):
    conn = projects["conn"]
    aktio = groups.save(conn, None, "Aktio", [AKTIO])
    chats = groups.save(conn, None, "Chats", [])
    groups.move(conn, ["claude.ai", f"{AKTIO}/process_monitor"], chats)
    groups.move(conn, [f"{AKTIO}/operator_dashboard"], None)  # out of every group, though its folder is Aktio's
    assert groups.assign(conn, ["claude.ai"])["claude.ai"] == (chats, "hand")
    assert _placed(conn)[f"{AKTIO}/operator_dashboard"] is None
    groups.move(conn, [f"{AKTIO}/process_monitor", f"{AKTIO}/operator_dashboard"], aktio)  # back where the folder puts it
    assert conn.execute("SELECT project_path FROM project_group_picks").fetchall()[0][0] == "claude.ai"  # no pick left
    groups.delete(conn, chats)  # its picks go with it
    assert _placed(conn)["claude.ai"] is None and not conn.execute("SELECT 1 FROM project_group_picks").fetchone()
    with pytest.raises(groups.GroupError):
        groups.move(conn, ["claude.ai"], chats)


def test_saving_a_ticked_list_puts_in_the_ticked_and_keeps_out_the_unticked(projects):
    conn = projects["conn"]
    gid = groups.save(conn, None, "Aktio", [AKTIO], members=[f"{VM}/ai_quotation_processor", f"{AKTIO}/operator_dashboard"])
    assert [p for p, g in _placed(conn).items() if g == gid] == [f"{AKTIO}/operator_dashboard", f"{VM}/ai_quotation_processor"]
    gid2 = groups.save(conn, gid, "Aktio ", [AKTIO, VM], members=PATHS[:4])  # the rules now cover every ticked one
    assert gid2 == gid and [p for p, g in _placed(conn).items() if g == gid] == PATHS[:4]
    assert not conn.execute("SELECT 1 FROM project_group_picks").fetchone() and groups.groups(conn)[0]["name"] == "Aktio"


@pytest.mark.parametrize(("name", "folders"), [("", []), ("x" * 61, []), ("aktio", []), ("New", ["/"]), ("New", ["  "]),
                                               ("New", ["aktio-vm:"]), ("New", "/a")])
def test_names_and_folder_rules_are_checked(projects, name, folders):
    conn = projects["conn"]
    groups.save(conn, None, "Aktio", [])
    with pytest.raises(groups.GroupError):
        groups.save(conn, None, name, folders)


def test_projects_page_filter_and_breadcrumb(projects):
    app = App(projects["cfg"])
    r, code = app.action_project_groups("save", {"name": "Aktio", "folders": [AKTIO, VM]})
    gid = r["id"]
    assert code == 200 and app.project_groups()["groups"] == [{"id": gid, "name": "Aktio", "folders": [AKTIO, VM], "hub_project": None}]
    rows = {p["project_path"]: p for p in app.projects()}
    assert (rows[AKTIO]["group"], rows[AKTIO]["group_by"]) == (gid, "folder") and rows["claude.ai"]["group"] is None
    assert app.action_project_groups("move", {"paths": ["claude.ai"], "group": gid}) == ({"ok": True}, 200)
    assert {s["id"] for s in app.sessions({"group": str(gid)})["items"]} == {"g-0", "g-1", "g-2", "g-3", "g-6"}
    assert app.sessions({"group": "999"})["total"] == 0
    assert app.project(AKTIO)["group"] == {"id": gid, "name": "Aktio", "by": "folder"}
    assert app.action_project_groups("save", {"name": "aktio"})[1] == 400  # the name is taken
    assert app.action_project_groups("move", {"paths": []})[1] == 400
    assert app.action_project_groups("delete", {"id": gid}) == ({"ok": True}, 200)
    assert app.project_groups()["groups"] == [] and app.project(AKTIO)["group"] is None


def test_a_person_limited_to_projects_sees_no_groups(projects):
    conn = projects["conn"]
    gid = groups.save(conn, None, "Aktio", [AKTIO])
    groups.move(conn, ["claude.ai"], gid)
    access.limit(conn, [AKTIO])  # the names and folders of groups would tell of other projects
    assert groups.groups(conn) == [] and groups.assign(conn, [AKTIO])[AKTIO] == (None, None)


def test_an_existing_archive_gains_the_group_tables(synced):
    from interlatch.storage.sqlite import connect

    conn = synced["conn"]
    conn.execute("DROP TABLE project_groups")
    conn.execute("DROP TABLE project_group_picks")
    conn.execute("PRAGMA user_version = 14")  # an archive from before groups
    conn.commit()
    again = connect(synced["cfg"].db_path)
    assert groups.groups(again) == [] and groups.assign(again, [AKTIO])[AKTIO] == (None, None)
    again.close()
