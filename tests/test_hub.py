"""Several computers, one archive: Tailscale access, the hub API, pushing from a spoke, and ingesting what it sent."""

import gzip
import hashlib
import json
import os
import stat
import sys
import textwrap
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from chronicle import hub
from chronicle.config import load_config
from chronicle.db import connect, kv_set

from conftest import CWD, PROJECT_DIR, SID, fake_log, write_fake_claude_tree

SPOKE_SID = "99999999-8888-7777-6666-555555555555"
SPOKE_CWD = "/home/test/code/demo-app"


def _serve(cfg):
    from chronicle.server import App, make_handler

    app = App(cfg)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return app, httpd, f"http://127.0.0.1:{port}"


def _make_spoke(tmp: Path, url: str, token: str, *, cwd: str = SPOKE_CWD, extra: str = "") -> tuple:
    """A second computer: its own Chronicle home and a Claude Code folder with one session of its own."""
    home, claude = tmp / "spoke-home", tmp / "spoke-claude"
    write_fake_claude_tree(claude)
    proj = claude / "projects" / PROJECT_DIR
    new_proj = claude / "projects" / cwd.replace("/", "-")
    proj.rename(new_proj)
    for path in sorted(new_proj.rglob("*"), reverse=True):  # its own session id and folder
        if path.is_file() and path.suffix in (".jsonl", ".json"):
            path.write_text(path.read_text().replace(SID, SPOKE_SID).replace(CWD, cwd))
        if SID in path.name:
            path.rename(path.with_name(path.name.replace(SID, SPOKE_SID)))
    (claude / "history.jsonl").unlink()
    home.mkdir()
    (home / "config.toml").write_text(textwrap.dedent(f"""\
        [sources]
        claude_dirs = ["{claude}"]
        codex_dirs = []
        [hub]
        url = "{url}"
        """) + extra)
    cfg = load_config(home)
    cfg.ensure_dirs()
    hub.write_token(cfg, token)
    return cfg, claude, new_proj / f"{SPOKE_SID}.jsonl"


@pytest.fixture()
def hubenv(synced, monkeypatch):
    """The `synced` computer as a hub, serving on a free port, with a spoke that joined it."""
    requests = []
    monkeypatch.setattr(hub.IngestTrigger, "request", lambda self: requests.append(1))
    token = hub.new_token(synced["cfg"])
    app, httpd, url = _serve(synced["cfg"])
    spoke_cfg, spoke_claude, spoke_main = _make_spoke(synced["tmp"], url, token)
    synced.update(app=app, url=url, token=token, spoke=spoke_cfg, spoke_claude=spoke_claude, spoke_main=spoke_main,
                  ingest_requests=requests)
    yield synced
    httpd.shutdown()


def test_push_then_ingest_tags_the_computer(hubenv):
    from chronicle.ingest import sync

    cfg, spoke = hubenv["cfg"], hubenv["spoke"]
    spoke_id = hub.local_machine(spoke)["id"]
    report = hub.push(spoke)
    assert report.sent >= 3 and not report.errors  # transcript, subagent files, memory notes
    assert hubenv["ingest_requests"], "the hub was not asked to ingest what arrived"
    received = cfg.machines_dir / spoke_id / "claude" / "projects" / SPOKE_CWD.replace("/", "-") / f"{SPOKE_SID}.jsonl"
    assert received.read_bytes() == hubenv["spoke_main"].read_bytes()
    assert abs(received.stat().st_mtime - hubenv["spoke_main"].stat().st_mtime) < 0.01

    sync(cfg, hubenv["conn"])
    conn = hubenv["conn"]
    row = conn.execute("SELECT machine_id, project_path, archive_path, n_prompts FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
    assert row["machine_id"] == spoke_id and row["project_path"] == SPOKE_CWD and row["n_prompts"] == 2
    assert f"/archive/machines/{spoke_id}/claude/" in row["archive_path"]
    own = conn.execute("SELECT machine_id FROM sessions WHERE id = ?", (SID,)).fetchone()[0]
    assert own == hub.local_machine(cfg)["id"] != spoke_id
    seen = {m["id"]: m for m in hub.machines(conn, cfg)}
    assert set(seen) == {own, spoke_id} and seen[own]["this"] and seen[spoke_id]["sessions"] == 1
    assert seen[spoke_id]["last_push"] and seen[spoke_id]["files"] >= 3

    again = hub.push(spoke)  # the hub already has everything
    assert again.sent == 0 and again.unchanged == report.sent
    with open(hubenv["spoke_main"], "a") as fh:  # the session goes on
        fh.write(json.dumps({"type": "user", "sessionId": SPOKE_SID, "cwd": SPOKE_CWD, "timestamp": "2026-09-20T11:00:00.000Z",
                             "message": {"role": "user", "content": "one more thing"}}) + "\n")
    assert hub.push(spoke).sent == 1


def test_hub_api_refuses_bad_tokens_and_paths(hubenv):
    spoke = hubenv["spoke"]
    hub.write_token(spoke, "not-the-token")
    with pytest.raises(hub.HubError, match="token"):
        hub.push(spoke)
    body = gzip.compress(b"x")
    params = f"machine={hub.local_machine(spoke)['id']}&root=claude&path=projects/../../evil&size=1&mtime=1&sha256={hashlib.sha256(b'x').hexdigest()}"
    req = urllib.request.Request(f"{hubenv['url']}/api/hub/file?{params}", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {hubenv['token']}"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 400
    assert not any(hubenv["cfg"].home.parent.glob("evil*"))
    bad = urllib.request.Request(f"{hubenv['url']}/api/hub/hello", data=b"{}", method="POST")  # no token at all
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(bad, timeout=10)
    assert exc.value.code == 401
    hub.write_token(spoke, hubenv["token"])
    (spoke.home / "machine.json").write_text(json.dumps(hub.local_machine(hubenv["cfg"])))  # a copied Chronicle folder
    with pytest.raises(hub.HubError, match="own id"):
        hub.push(spoke)


def test_damaged_upload_is_rejected(hubenv):
    data = b'{"a": 1}\n'
    params = (f"machine={hub.local_machine(hubenv['spoke'])['id']}&root=claude&path=history.jsonl&size={len(data)}"
              f"&mtime=1&sha256={hashlib.sha256(b'other').hexdigest()}")
    req = urllib.request.Request(f"{hubenv['url']}/api/hub/file?{params}", data=gzip.compress(data), method="POST",
                                 headers={"Authorization": f"Bearer {hubenv['token']}"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 400 and b"damaged" in exc.value.read()
    assert not list(hubenv["cfg"].machines_dir.rglob("history.jsonl*"))


def test_forgotten_session_is_not_taken_back(hubenv):
    from chronicle.ingest import forget_session, sync

    hub.push(hubenv["spoke"])
    sync(hubenv["cfg"], hubenv["conn"])
    forget_session(hubenv["conn"], hubenv["cfg"], SPOKE_SID, delete_transcript=True)
    hub.push(hubenv["spoke"])  # the spoke still has it and offers it again
    sync(hubenv["cfg"], hubenv["conn"])
    assert hubenv["conn"].execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == 0
    assert not list(hubenv["cfg"].machines_dir.rglob(f"{SPOKE_SID}.jsonl"))


def test_excluded_projects_never_leave_the_spoke(hubenv):
    spoke = hubenv["spoke"]
    spoke.exclude_projects = ["/home/test/code/*"]
    report = hub.push(spoke)
    assert report.skipped >= 3 and report.sent == 0
    assert not list(hubenv["cfg"].machines_dir.rglob("*.jsonl"))


def test_projects_matched_across_computers(hubenv, monkeypatch):
    from chronicle.ingest import sync

    cfg, conn = hubenv["cfg"], hubenv["conn"]
    cfg.hub_path_map = {"/home/test/code": "/Users/test/Projects"}
    hub.push(hubenv["spoke"])
    sync(cfg, conn)
    row = conn.execute("SELECT project_path, machine_path FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
    assert row["project_path"] == CWD and row["machine_path"] == SPOKE_CWD  # same project as the hub's own session

    # by git remote: the spoke reported its repository, the hub knows the same remote for a folder of its own
    spoke_id = hub.local_machine(hubenv["spoke"])["id"]
    conn.execute("UPDATE machines SET repos_json = ? WHERE id = ?",
                 (json.dumps({"/srv/x/app": ["/srv/x/app", "github.com/org/demo-app"]}), spoke_id))
    kv_set(conn, f"git:{CWD}", json.dumps([CWD, "github.com/org/demo-app"]))
    conn.commit()
    cfg.hub_path_map = {}
    r = hub.resolver(cfg, conn, fresh=True)
    assert r.resolve(spoke_id, "/srv/x/app/api") == f"{CWD}/api"
    assert r.resolve(spoke_id, "/srv/x/other") == "/srv/x/other"  # nothing known: kept as recorded
    assert r.resolve(spoke_id, "/work/demo", remote="git@github.com:Org/demo-app.git") == CWD  # Codex's own remote
    assert r.resolve(hub.local_machine(cfg)["id"], "/srv/x/app") == "/srv/x/app"  # the hub's own sessions: untouched


def test_added_folders_and_what_wins(hubenv):
    """A folder a computer added files everything below it; a repository with a remote the hub knows, when it is the
    more specific match, follows its remote."""
    cfg, conn = hubenv["cfg"], hubenv["conn"]
    spoke_id = hub.local_machine(hubenv["spoke"])["id"]
    hub.push(hubenv["spoke"])  # registers the computer
    conn.execute("UPDATE machines SET repos_json = ? WHERE id = ?", (json.dumps({
        "/w/client/app": ["/w/client/app", "github.com/org/demo-app"],      # known here: CWD
        "/w/client/fork": ["/w/client/fork", "github.com/someone/fork"],    # unknown here
        "/w/mono": ["/w/mono", "github.com/org/demo-app"],                  # known, with an added folder inside
    }), spoke_id))
    kv_set(conn, f"git:{CWD}", json.dumps([CWD, "github.com/org/demo-app"]))
    kv_set(conn, hub.FOLDERS_KV + spoke_id, json.dumps({
        "/w/client": "/hub/client-a", "/w/client/notes/private": "/hub/other", "/w/mono/tools": "/hub/tools"}))
    conn.commit()
    r = hub.resolver(cfg, conn, fresh=True)
    assert r.resolve(spoke_id, "/w/client") == "/hub/client-a"
    assert r.resolve(spoke_id, "/w/client/notes/deep") == "/hub/client-a"  # the folder and everything below it
    assert r.resolve(spoke_id, "/w/client/notes/private/x") == "/hub/other"  # the most specific folder wins
    assert r.resolve(spoke_id, "/w/client/app/api") == f"{CWD}/api"  # a known remote inside the folder wins
    assert r.resolve(spoke_id, "/w/client/fork") == "/hub/client-a"  # an unknown remote: the folder decides
    assert r.resolve(spoke_id, "/w/mono/tools/cli") == "/hub/tools"  # a folder inside a known repository wins
    assert r.resolve(spoke_id, "/w/mono/src") == f"{CWD}/src"
    assert r.resolve(spoke_id, "/w/client/x", remote="git@github.com:org/demo-app.git") == CWD  # the transcript's own
    assert r.resolve(spoke_id, "/elsewhere") == "/elsewhere"


def test_hub_add_folder_files_and_moves_sessions(hubenv, monkeypatch, capsys):
    """`chronicle hub add-folder` on the spoke: its sessions there move to the hub's project, knowledge and all, and
    `remove-folder` moves them back."""
    from chronicle.cli import main
    from chronicle.ingest import sync

    cfg, conn, spoke = hubenv["cfg"], hubenv["conn"], hubenv["spoke"]
    hub.push(spoke)
    sync(cfg, conn)
    conn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, confidence, source, "
                 "fingerprint, created_at, updated_at) VALUES (?, ?, 'demo-app', 'gotcha', 'A lesson', 'body', 'high', "
                 "'analysis', 'fp-spoke', '2026-09-20', '2026-09-20')", (SPOKE_SID, SPOKE_CWD))
    conn.execute("INSERT INTO project_kb(project_path, project_name, knowledge_max_id, n_items) VALUES (?, 'demo-app', 99, 1)",
                 (CWD,))
    conn.commit()
    assert conn.execute("SELECT project_path FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == SPOKE_CWD

    folder = hubenv["tmp"] / "spoke-work"  # the spoke's own folder: it has to exist there
    folder.mkdir()
    monkeypatch.setenv("INTERLATCH_HOME", str(spoke.home))
    assert main(["hub", "add-folder", str(folder), "--project", "demo-app"]) == 1  # two projects are called that
    assert "/home/test/code/demo-app" in capsys.readouterr().out
    assert main(["hub", "add-folder", str(folder), "--project", CWD]) == 0
    out = " ".join(capsys.readouterr().out.split())  # rich wraps long lines
    assert "now go to demo-app" in out
    spoke = load_config(spoke.home)
    assert spoke.hub_folders == {str(folder.resolve()): CWD}

    # the session ran in SPOKE_CWD, which exists only on the simulated other computer: add it in its config directly
    from chronicle.config import toml_table
    from chronicle.config import set_config_value

    set_config_value(spoke, "hub", "folders", toml_table({**spoke.hub_folders, SPOKE_CWD: CWD}))
    spoke = load_config(spoke.home)
    report = hub.push(spoke)
    assert not report.errors
    row = conn.execute("SELECT project_path, project_name, machine_path FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
    assert (row["project_path"], row["project_name"], row["machine_path"]) == (CWD, "demo-app", SPOKE_CWD)
    assert conn.execute("SELECT project_path FROM knowledge WHERE fingerprint = 'fp-spoke'").fetchone()[0] == CWD
    assert conn.execute("SELECT knowledge_max_id FROM project_kb WHERE project_path = ?", (CWD,)).fetchone()[0] == 0
    seen = hub.last_folders(spoke)
    assert seen["moved"] == 1 and seen["folders"][SPOKE_CWD]["sessions"] == 1
    listed = {m["id"]: m for m in hub.machines(conn, cfg)}[hub.local_machine(spoke)["id"]]["folders"]
    assert {f["folder"] for f in listed} == {SPOKE_CWD, str(folder.resolve())}

    with open(hubenv["spoke_main"], "a") as fh:  # the session goes on: ingesting it again keeps the project
        fh.write(json.dumps({"type": "user", "sessionId": SPOKE_SID, "cwd": SPOKE_CWD, "timestamp": "2026-09-20T11:00:00.000Z",
                             "message": {"role": "user", "content": "one more thing"}}) + "\n")
    hub.push(spoke)
    sync(cfg, conn)
    assert conn.execute("SELECT project_path FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == CWD

    assert main(["hub", "folders"]) == 0
    assert SPOKE_CWD in capsys.readouterr().out
    assert main(["hub", "remove-folder", SPOKE_CWD]) == 0
    assert "1 session already on the hub moved" in " ".join(capsys.readouterr().out.split())
    row = conn.execute("SELECT project_path, machine_path FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
    assert (row["project_path"], row["machine_path"]) == (SPOKE_CWD, None)
    assert conn.execute("SELECT project_path FROM knowledge WHERE fingerprint = 'fp-spoke'").fetchone()[0] == SPOKE_CWD


def test_hub_add_folder_refuses_another_projects_repository(env, monkeypatch, capsys):
    from chronicle.cli import main
    from chronicle.config import set_config_value

    cfg = load_config(env["home"])
    set_config_value(cfg, "hub", "url", '"https://hub.example"')
    hub.write_token(cfg, "t")
    folder = env["tmp"] / "client-b-app"
    folder.mkdir()
    projects = [{"path": "/hub/client-a", "name": "client-a", "sessions": 3},
                {"path": "/hub/client-b", "name": "client-b", "sessions": 2},
                {"path": "/other/client-b", "name": "client-b", "sessions": 1}]
    monkeypatch.setattr(hub, "handshake", lambda cfg: {"projects": projects,
                                                       "remotes": {"github.com/org/b": "/hub/client-b"}})
    monkeypatch.setattr(hub, "git_info", lambda path: (path, "github.com/org/b"))

    assert main(["hub", "add-folder", str(folder), "--project", "client-a", "--no-push"]) == 1
    assert "belongs to one project" in " ".join(capsys.readouterr().out.split())
    assert main(["hub", "add-folder", str(folder), "--project", "client-b", "--no-push"]) == 1  # two of them
    assert "/other/client-b" in capsys.readouterr().out
    assert main(["hub", "add-folder", str(folder), "--project", "/hub/client-b", "--no-push"]) == 0
    assert "Nothing to add" in " ".join(capsys.readouterr().out.split())  # its remote already files it there
    assert main(["hub", "add-folder", str(folder), "--project", "nope", "--no-push"]) == 1
    assert load_config(env["home"]).hub_folders == {}

    monkeypatch.setattr(hub, "git_info", lambda path: None)  # not a repository: the member decides
    assert main(["hub", "add-folder", str(folder), "--project", "client-a", "--no-push"]) == 0
    assert load_config(env["home"]).hub_folders == {str(folder.resolve()): "/hub/client-a"}
    assert main(["hub", "remove-folder", str(folder), "--no-push"]) == 0
    assert load_config(env["home"]).hub_folders == {}


def _analyzed_on_spoke(spoke, *, title: str, at: str, lessons: list[tuple[str, str, str]]):
    """Record the spoke's own session there and give it an analysis, as its own Claude Code would."""
    from chronicle.ingest import sync

    sconn = connect(spoke.db_path)
    sync(spoke, sconn)
    sconn.execute("UPDATE sessions SET analysis_status = 'done', analyzed_at = ?, llm_title = ?, summary = 'What happened', "
                  "analyzed_prompts = n_prompts WHERE id = ?", (at, title, SPOKE_SID))
    sconn.execute("DELETE FROM knowledge WHERE session_id = ?", (SPOKE_SID,))
    for fp, scope, kind in lessons:
        case = json.dumps({"scene": f"scene {fp}", "question": "why?", "answer": "because", "ruled_out": []}) \
            if kind in ("fix", "gotcha") else None
        sconn.execute("INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, scope, confidence, "
                      "source, fingerprint, created_at, updated_at, case_json) VALUES (?, ?, 'demo-app', ?, ?, 'body', ?, "
                      "'high', 'analysis', ?, ?, ?, ?)", (SPOKE_SID, SPOKE_CWD, kind, f"lesson {fp}", scope, fp, at, at, case))
    sconn.commit()
    sconn.close()


def test_knowledge_only_sharing(hubenv):
    """[hub] share = "knowledge": the spoke analyzes its own sessions and sends the hub only details, the analysis
    and project lessons; no transcript arrives, the hub never re-analyzes it, and folders still decide the project."""
    from chronicle.analyze import AnalysisSkipped, analyze_session
    from chronicle.ingest import mark_missing_sources, sync
    from chronicle.worker import count_pending, pending_sessions

    cfg, conn, spoke = hubenv["cfg"], hubenv["conn"], hubenv["spoke"]
    spoke.hub_share = "knowledge"
    assert spoke.shares_knowledge and not spoke.sends_files
    spoke_id = hub.local_machine(spoke)["id"]
    _analyzed_on_spoke(spoke, title="Shared title", at="2026-09-20T12:00:00Z",
                       lessons=[("p-gotcha", "project", "gotcha"), ("p-pref", "project", "preference"),
                                ("g-gotcha", "global", "gotcha")])

    report = hub.push(spoke)  # its folder is no project of the hub's, and nobody added it: it stays here
    assert report.sent == 0 and report.skipped == 1 and "1 kept here" in report.summary()
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE source = 'remote'").fetchone()[0] == 0
    spoke.hub_all_folders = True  # `chronicle hub join --all-folders`
    report = hub.push(spoke)
    assert report.kind == "knowledge" and report.sent == 1 and not report.errors
    assert "1 session shared" in report.summary()
    assert hubenv["ingest_requests"], "the hub was not asked to build its knowledge"
    assert not list(cfg.machines_dir.rglob("*.jsonl")), "a transcript left the spoke"
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
    assert (row["source"], row["machine_id"], row["project_path"], row["analysis_status"]) == \
        ("remote", spoke_id, SPOKE_CWD, "done")
    assert row["title"] == "Shared title" and row["summary"] == "What happened" and row["n_prompts"] == 2
    assert row["first_prompt"] is None and row["transcript_path"] is None and row["last_prompt"] is None
    lessons = [r[0] for r in conn.execute("SELECT fingerprint FROM knowledge WHERE session_id = ?", (SPOKE_SID,))]
    assert lessons == ["p-gotcha"]  # lessons about the person stay on the spoke
    case = conn.execute("SELECT case_json FROM knowledge WHERE fingerprint = 'p-gotcha'").fetchone()[0]
    assert json.loads(case)["scene"] == "scene p-gotcha"  # its case file came along

    assert SPOKE_SID not in pending_sessions(conn, cfg, 100)
    ready = count_pending(conn, cfg)["ready"]
    conn.execute("UPDATE sessions SET analysis_status = 'pending' WHERE id = ?", (SPOKE_SID,))  # e.g. a "re-analyze all"
    assert SPOKE_SID not in pending_sessions(conn, cfg, 100) and count_pending(conn, cfg)["ready"] == ready
    with pytest.raises(AnalysisSkipped, match="keeps its transcript"):
        analyze_session(conn, cfg, SPOKE_SID)
    mark_missing_sources(conn)
    assert conn.execute("SELECT source_present FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == 1
    conn.execute("UPDATE sessions SET analysis_status = 'done' WHERE id = ?", (SPOKE_SID,))
    conn.commit()
    sync(cfg, conn)  # the hub's own sync leaves it alone
    assert conn.execute("SELECT source FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == "remote"

    again = hub.push(spoke)
    assert again.sent == 0 and again.unchanged == 1

    _analyzed_on_spoke(spoke, title="Better title", at="2026-09-21T09:00:00Z", lessons=[("p-fix", "project", "fix")])
    assert hub.push(spoke).sent == 1  # analyzed again there: the hub's copy is replaced, lessons and all
    assert conn.execute("SELECT title FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == "Better title"
    assert [r[0] for r in conn.execute("SELECT fingerprint FROM knowledge WHERE session_id = ?", (SPOKE_SID,))] == ["p-fix"]

    spoke.hub_folders = {SPOKE_CWD: CWD}  # an added folder files shared sessions too
    hub.push(spoke)
    row = conn.execute("SELECT project_path, machine_path FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
    assert (row["project_path"], row["machine_path"]) == (CWD, SPOKE_CWD)
    assert conn.execute("SELECT project_path FROM knowledge WHERE fingerprint = 'p-fix'").fetchone()[0] == CWD
    seen = {m["id"]: m for m in hub.machines(conn, cfg)}[spoke_id]
    assert seen["share"] == "knowledge" and seen["last_push"] and seen["sessions"] == 1


def test_hub_keeps_its_own_record_of_a_session_it_has(hubenv):
    """A session whose transcript the hub already has (sent before the spoke switched to knowledge only) keeps the
    hub's record; bad records are ignored."""
    from chronicle.ingest import sync

    cfg, conn, spoke = hubenv["cfg"], hubenv["conn"], hubenv["spoke"]
    hub.push(spoke)  # everything: the transcript arrives
    sync(cfg, conn)
    spoke.hub_share = "knowledge"
    _analyzed_on_spoke(spoke, title="From the spoke", at="2026-09-20T12:00:00Z", lessons=[("p1", "project", "fact")])
    hub.push(spoke)
    row = conn.execute("SELECT source, title FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
    assert row["source"] == "transcript" and row["title"] != "From the spoke"

    body = gzip.compress(json.dumps({"sessions": [{"id": "../x", "agent": "claude"}, {"id": "ok-1", "agent": "chatgpt"},
                                                  {"id": "ok-2", "agent": "claude", "tools_json": {"Bash": 1},
                                                   "knowledge": [{"kind": "fact", "title": "t", "scope": "global"}]}]}).encode())
    params = f"machine={hub.local_machine(spoke)['id']}"
    req = urllib.request.Request(f"{hubenv['url']}/api/hub/sessions?{params}", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {hubenv['token']}"})
    assert json.loads(urllib.request.urlopen(req, timeout=10).read())["stored"] == 1
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id IN ('../x', 'ok-1')").fetchone()[0] == 0
    assert conn.execute("SELECT tools_json FROM sessions WHERE id = 'ok-2'").fetchone()[0] == '{"Bash": 1}'
    assert conn.execute("SELECT COUNT(*) FROM knowledge WHERE session_id = 'ok-2'").fetchone()[0] == 0


def test_knowledge_mode_records_and_analyzes_here(env, monkeypatch):
    """A knowledge-only computer keeps its own Chronicle running: the hook ingests here instead of pushing files,
    and the worker runs, then tries to share (an unreachable hub is just noted)."""
    from chronicle import hooks
    from chronicle.config import set_config_value
    from chronicle.worker import run_worker

    cfg = load_config(env["home"])
    set_config_value(cfg, "hub", "url", '"http://127.0.0.1:9"')
    set_config_value(cfg, "hub", "share", '"knowledge"')
    hub.write_token(cfg, "t")
    cfg = load_config(env["home"])
    assert cfg.shares_knowledge and not cfg.sends_files

    spawned = []
    monkeypatch.setattr(hooks, "spawn_detached", lambda args, log: spawned.append(args))
    hooks._on_session_end({"transcript_path": "/x.jsonl"}, ended=True)
    assert "ingest-session" in spawned[0] and "push" not in spawned[0]

    report = run_worker(cfg, analyze=False, synthesize=False, export=False)
    assert report.note is None or "hub" not in report.note
    assert report.shared and report.shared.startswith("not sent")


def test_hub_ignores_bad_folders():
    assert hub.clean_folders({"relative/x": "/a", "/ok/": "/p/", "/b": "relative", 3: "/c"}) == {"/ok": "/p"}
    assert hub.clean_folders(["/a"]) == {}
    assert hub.under("/a/b", "/a") and hub.under("/a", "/a/") and not hub.under("/ab", "/a") and hub.under("/x", "/")


def test_normalize_remote():
    for url in ("git@github.com:Org/Repo.git", "https://github.com/org/repo", "https://tok@github.com/Org/Repo/",
                "ssh://git@github.com/org/repo.git"):
        assert hub.normalize_remote(url) == "github.com/org/repo"
    assert hub.normalize_remote("not a url") is None and hub.normalize_remote(None) is None


def test_spoke_hands_over_its_earlier_analyses(synced, tmp_path, monkeypatch):
    """A spoke that already analyzed its sessions sends the results once; the hub doesn't pay for them again."""
    from chronicle.ingest import sync
    from chronicle.worker import run_worker

    monkeypatch.setattr(hub.IngestTrigger, "request", lambda self: None)
    cfg = synced["cfg"]
    token = hub.new_token(cfg)
    _app, httpd, url = _serve(cfg)
    try:
        spoke, _claude, _main = _make_spoke(tmp_path, "", token, extra=textwrap.dedent(f"""\
            [analysis]
            claude_bin = "{synced['fake']}"
            idle_minutes = 0
            concurrency = 1
            """))
        conn = connect(spoke.db_path)
        sync(spoke, conn)  # the spoke's own Chronicle, before it joins
        conn.close()
        run_worker(spoke, synthesize=False, export=False)
        spoke_conn = connect(spoke.db_path)
        assert spoke_conn.execute("SELECT analysis_status FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()[0] == "done"
        spoke_conn.close()
        calls = len(fake_log(synced))

        spoke.hub_url = url  # joins
        report = hub.push(spoke)
        assert report.analyses >= 1
        sync(cfg, synced["conn"])
        row = synced["conn"].execute("SELECT analysis_status, summary, title FROM sessions WHERE id = ?", (SPOKE_SID,)).fetchone()
        assert row["analysis_status"] == "done" and row["summary"] and row["title"] == "Fixed login token expiry bug"
        assert synced["conn"].execute("SELECT COUNT(*) FROM knowledge WHERE session_id = ? AND source = 'analysis'",
                                      (SPOKE_SID,)).fetchone()[0] >= 1
        assert not list(cfg.machines_dir.glob(f"*/{hub.ANALYSES_FILE}"))  # applied, so gone
        run_worker(cfg, synthesize=False, export=False)
        analyzed = [c for c in fake_log(synced)[calls:] if SPOKE_SID in json.dumps(c)]
        assert not analyzed, "the hub analyzed a session the spoke had already analyzed"
        assert hub.push(spoke).analyses == 0  # only once
    finally:
        httpd.shutdown()


def test_spoke_sends_instead_of_recording(env, monkeypatch):
    from chronicle import hooks
    from chronicle.worker import run_worker

    cfg = env["cfg"]
    with open(cfg.config_path, "a") as fh:
        fh.write('\n[hub]\nurl = "https://pc.tail1234.ts.net"\n')
    spawned = []
    monkeypatch.setattr(hooks, "spawn_detached", lambda args, log: spawned.append(args))
    hooks._on_session_end({"transcript_path": str(env["main"])}, ended=True)
    assert spawned and spawned[0][-2:] == ["push", "--quiet"]
    report = run_worker(load_config(cfg.home))
    assert "hub" in (report.note or "") and not report.analyzed
    assert not fake_log(env)


def test_tailnet_names_and_logins(synced):
    cfg = synced["cfg"]
    cfg.server_allowed_hosts = ["pc.tail1234.ts.net"]
    cfg.server_allowed_users = ["me@github"]
    _app, httpd, base = _serve(cfg)

    def status(headers):
        req = urllib.request.Request(base + "/api/jobs", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        ts = {"Host": "pc.tail1234.ts.net", "X-Forwarded-For": "100.64.0.2"}  # as Tailscale Serve forwards it
        assert status({**ts, "Tailscale-User-Login": "me@github"}) == 200
        assert status({**ts, "Tailscale-User-Login": "someone@else"}) == 403
        assert status(ts) == 403  # no identity: a tagged device
        # Serve passes the client's Host through as sent: claiming to be this computer must not skip the login check
        assert status({"Host": base.removeprefix("http://"), "X-Forwarded-For": "100.64.0.2"}) == 403
        assert status({"Host": base.removeprefix("http://"), "X-Forwarded-For": "100.64.0.2", "Tailscale-User-Login": "me@github"}) == 200
        assert status({"Host": "evil.example"}) == 403
        assert status({}) == 200  # this computer itself, by 127.0.0.1
        cfg.server_allowed_users = []
        assert status({**ts, "Tailscale-User-Login": "anyone@else"}) == 200  # anyone on the tailnet
        assert status(ts) == 403  # but with no people, never someone Serve names no login for (Funnel, a tagged device)
        req = urllib.request.Request(base + "/api/sync", data=b"{}", method="POST",
                                     headers={**ts, "X-Interlatch": "1", "Tailscale-User-Login": "x"})
        cfg.server_allowed_users = ["me@github"]
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=10)
        assert exc.value.code == 403
    finally:
        httpd.shutdown()


FAKE_TAILSCALE = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_TS_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
if args[:2] == ["status", "--json"]:
    print(json.dumps({"BackendState": "Running", "Self": {"DNSName": "pc.tail1234.ts.net.", "UserID": 7,
                      "TailscaleIPs": ["100.64.0.1"]}, "User": {"7": {"LoginName": "me@github"}},
                      "CurrentTailnet": {"Name": "me@github"}}))
elif args[:2] == ["serve", "status"]:
    print(json.dumps({"Web": {"pc.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}}}))
'''


def test_tailnet_on_and_hub_enable(env, monkeypatch, capsys):
    from chronicle.cli import main

    bin_dir = env["tmp"] / "tsbin"
    bin_dir.mkdir()
    fake = bin_dir / "tailscale"
    fake.write_text(FAKE_TAILSCALE.replace("{python}", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_TS_LOG", str(env["tmp"] / "ts.log"))
    monkeypatch.setattr(sys, "stdin", open(os.devnull))

    assert main(["tailnet", "on"]) == 0
    cfg = load_config(env["home"])
    assert cfg.server_allowed_hosts == ["pc.tail1234.ts.net"] and cfg.server_allowed_users == ["me@github"]
    calls = [json.loads(line) for line in (env["tmp"] / "ts.log").read_text().splitlines()]
    assert ["serve", "--bg", "--https=443", f"http://127.0.0.1:{cfg.server_port}"] in calls
    assert "https://pc.tail1234.ts.net/" in capsys.readouterr().out

    assert main(["hub", "enable"]) == 0
    out = capsys.readouterr().out
    token = hub.read_token(cfg)
    assert token and f"interlatch hub join https://pc.tail1234.ts.net --token={token}" in out.replace("\n", "")
    assert oct(hub.token_path(cfg).stat().st_mode & 0o777) == "0o600"

    assert main(["tailnet", "off"]) == 0
    cfg = load_config(env["home"])
    assert cfg.server_allowed_hosts == [] and cfg.server_allowed_users == []


def test_systemd_units_quote_commands(env):
    from chronicle.install import systemd_units

    units = systemd_units(env["cfg"], "/usr/bin/python3 -m chronicle", interval=600)
    assert "ExecStart=/usr/bin/python3 -m chronicle sync --work --quiet" in units["interlatch-sync.service"]
    assert "OnUnitActiveSec=600s" in units["interlatch-sync.timer"]
    assert "ExecStart=/usr/bin/python3 -m chronicle ui" in units["interlatch-ui.service"]
    assert "Restart=always" in units["interlatch-ui.service"]
    spaced = systemd_units(env["cfg"], "'/opt/my apps/chronicle'")["interlatch-ui.service"]
    assert 'ExecStart="/opt/my apps/chronicle" ui' in spaced
