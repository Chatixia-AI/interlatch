"""Projects on the hub as the Projects page and sidebar list them: on a computer that sends to a hub, the hub project
each of its projects is in (sharing.projects.hub_project_of); on the hub, the projects set up there that have no
sessions yet."""

import json
from dataclasses import replace

from interlatch.sharing.projects import FOLDERS_FILE, add_project, hub_project_of
from interlatch.interfaces.web.app import App

from conftest import CWD
from interlatch.interfaces.web import api

RESONA = "/Users/me/Work/AI-BPO/Resona"
REPO = f"{RESONA}/quote-agent"  # a repository inside an added folder: its remote decides


def _spoke(cfg, **kw):
    """`cfg` sending to a hub, with what the hub made of its folders at the last push."""
    (cfg.home / FOLDERS_FILE).write_text(json.dumps({
        "at": "2026-10-07T00:00:00Z", "hub": "Team hub",
        "projects": [{"path": "/data/projects/AI-BPO-Resona", "name": "AI-BPO-Resona"},
                     {"path": "/data/projects/Cosmo-Quote-Agent", "name": "Cosmo Quote Agent"}],
        "remotes": {"/data/projects/Cosmo-Quote-Agent": [{"remote": "github.com/me/quote-agent", "folder": REPO}]}}))
    return replace(cfg, hub_url="http://127.0.0.1:9", hub_folders={RESONA: "/data/projects/AI-BPO-Resona"}, **kw)


def test_the_hub_project_each_project_here_is_in(env):
    cfg = env["cfg"]
    assert hub_project_of(cfg, [f"{RESONA}/excel"]) == {}  # not sending to a hub
    spoke = _spoke(cfg, hub_left=["/data/projects/Gone"], exclude_projects=[f"{RESONA}/secret*"])
    spoke = replace(spoke, hub_folders={**spoke.hub_folders, "/Users/me/old": "/data/projects/Gone"})
    got = hub_project_of(spoke, [f"{RESONA}/excel", RESONA, f"{REPO}/web", f"{RESONA}/secret-client",
                                     "/Users/me/old/x", "/Users/me/elsewhere", "claude.ai", None])
    assert got == {
        f"{RESONA}/excel": {"path": "/data/projects/AI-BPO-Resona", "name": "AI-BPO-Resona"},  # the added folder
        RESONA: {"path": "/data/projects/AI-BPO-Resona", "name": "AI-BPO-Resona"},
        f"{REPO}/web": {"path": "/data/projects/Cosmo-Quote-Agent", "name": "Cosmo Quote Agent"},  # its remote first
    }  # excluded, left, in no hub project, a chat: none


def test_a_project_page_and_card_say_which_hub_project(synced):
    cfg = _spoke(synced["cfg"])
    cfg = replace(cfg, hub_folders={CWD: "/data/projects/AI-BPO-Resona"})
    app = App(cfg)
    row = next(p for p in api.projects.projects(app.context()) if p["project_path"] == CWD)
    assert row["hub"] == {"path": "/data/projects/AI-BPO-Resona", "name": "AI-BPO-Resona"}
    assert api.projects.project(app.context(), CWD)["hub"] == row["hub"]
    assert all(p["hub"] is None for p in api.projects.projects(App(synced["cfg"]).context()))  # not sending to a hub: none


def test_a_project_set_up_on_the_hub_is_listed_before_anything_is_filed(synced):
    cfg, conn = synced["cfg"], synced["conn"]
    empty = synced["tmp"] / "new-client"
    empty.mkdir()
    path = add_project(cfg, conn, str(empty))["path"]
    app = App(cfg)
    rows = {p["project_path"]: p for p in api.projects.projects(app.context())}
    assert rows[path] | {"weekly": None} == {
        "project_path": path, "project_name": "new-client", "label": "new-client", "sessions": 0, "prompts": 0,
        "active_s": 0, "cost": 0, "tokens": 0, "first": None, "last": None, "analyzed": 0, "knowledge": 0,
        "kb_updated": None, "exists": True, "weekly": None, "outcomes": {}, "agents": {}, "shared": True, "hub": None,
        "group": None, "group_by": None}
    assert list(rows)[-1] == path  # after every project with sessions
    assert path not in {p["project_path"] for p in api.projects.projects(app.context(), scope=[CWD])}  # not someone limited to another one
    assert path in {p["project_path"] for p in api.projects.projects(app.context(), scope=[path])}
    add_project(cfg, conn, CWD)
    assert [p["sessions"] > 0 for p in api.projects.projects(app.context()) if p["project_path"] == CWD] == [True]  # once, with its sessions
