"""Process-level tests: MCP server, HTTP API, hooks, installer."""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from chronicle.db import connect
from chronicle.install import hooks_installed, install_hooks, uninstall_hooks

from conftest import CWD, SECRET, SID


def _run_mcp(env, messages):
    proc = subprocess.run(
        [sys.executable, "-m", "chronicle", "mcp"],
        input="\n".join(json.dumps(m) for m in messages) + "\n",
        capture_output=True, text=True, timeout=60, env={**os.environ}, cwd=CWD if os.path.isdir(CWD) else None,
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_mcp_server_protocol(synced):
    from chronicle.analyze import analyze_session

    analyze_session(synced["conn"], synced["cfg"], SID)
    out = _run_mcp(synced, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_knowledge", "arguments": {"query": "TTL"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "get_session", "arguments": {"session_id": SID[:8]}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "search_sessions", "arguments": {"query": "logout", "project": "demo-app"}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "get_transcript", "arguments": {"session_id": SID, "limit": 3}}},
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "recent_sessions", "arguments": {"days": 100000}}},
        {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "project_knowledge", "arguments": {"project": "demo-app"}}},
        {"jsonrpc": "2.0", "id": 9, "method": "nope"},
        {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "get_session", "arguments": {"bogus": 1}}},
    ])
    by_id = {m["id"]: m for m in out}
    assert by_id[1]["result"]["protocolVersion"] == "2025-06-18"
    assert by_id[1]["result"]["serverInfo"]["name"] == "interlatch"
    assert {t["name"] for t in by_id[2]["result"]["tools"]} >= {"search_knowledge", "search_sessions", "get_session", "project_knowledge"}
    assert all(t["annotations"]["readOnlyHint"] and not t["annotations"]["openWorldHint"] for t in by_id[2]["result"]["tools"])
    text = lambda i: by_id[i]["result"]["content"][0]["text"]  # noqa: E731
    assert "Token TTL compared in seconds vs ms" in text(3)
    assert "Fixed login token expiry bug" in text(4)
    assert "logout" in text(5).lower()
    assert "USER" in text(6) and "more: call again with offset=3" in text(6)
    assert SECRET not in text(6) and "[REDACTED:anthropic-key]" in text(6)
    assert SID[:8] in text(7)
    assert "Token TTL" in text(8)
    assert by_id[9]["error"]["code"] == -32601
    assert by_id[10]["result"]["isError"] is True
    assert len(out) == 10  # the notification got no reply


def test_http_api(synced):
    from chronicle.server import App, make_handler

    app = App(synced["cfg"])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def get(path, headers=None):
        req = urllib.request.Request(base + path, headers=headers or {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()

    try:
        for path in ("/api/overview?days=all", "/api/sessions", f"/api/sessions/{SID}", f"/api/sessions/{SID[:8]}/events",
                     "/api/projects", "/api/knowledge", "/api/search?q=login", "/api/status", "/api/glossary",
                     "/api/glossary/terms", "/api/reviews", "/api/knowledge/hub", "/"):
            status, body = get(path)
            assert status == 200, path
        data = json.loads(get(f"/api/sessions/{SID}")[1])
        assert data["n_prompts"] == 2 and data["agents"][1]["agent_id"]
        sessions = json.loads(get("/api/sessions?sort=est_cost_usd&order=asc")[1])
        assert sessions["total"] == 2
        projects = json.loads(get("/api/projects")[1])  # project cards: 12 weekly buckets, outcomes, agents
        assert all(len(p["weekly"]) == 12 and isinstance(p["outcomes"], dict) and p["agents"] for p in projects)
        daily = json.loads(get("/api/overview?days=all")[1])["daily"]  # stat-tile sparklines
        assert daily and {"tool_calls", "lines_added"} <= set(daily[0])
        found = json.loads(get("/api/search?q=logout&sort=newest")[1])  # search all sessions
        assert found["sessions"][0]["session_id"] == SID and found["sessions"][0]["snippets"] and "knowledge" in found
        matches = json.loads(get(f"/api/sessions/{SID[:8]}/matches?q=logout")[1])  # find in session, in transcript order
        assert matches and all("«logout»" in m["snippet"].lower() for m in matches)
        assert [m["seq"] for m in matches if not m["agent_id"]] == sorted(m["seq"] for m in matches if not m["agent_id"])
        assert json.loads(get(f"/api/sessions/{SID}/matches?q=")[1]) == []
        try:
            get("/api/status", {"Host": "attacker.example:80"})
            raise AssertionError("foreign Host header accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        req = urllib.request.Request(base + "/api/sync", data=b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            raise AssertionError("POST without X-Interlatch header accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        kid = synced["conn"].execute("SELECT id FROM knowledge LIMIT 1").fetchone()[0]
        req = urllib.request.Request(base + f"/api/knowledge/{kid}", data=b'{"pinned": true}', method="POST",
                                     headers={"X-Interlatch": "1", "Content-Type": "application/json"})
        assert json.loads(urllib.request.urlopen(req, timeout=10).read())["ok"] is True
        req = urllib.request.Request(base + f"/api/knowledge/{kid}", data=b'{"pinned": true}', method="POST",
                                     headers={"X-Chronicle": "1", "Content-Type": "application/json"})  # a tab from before the rename
        assert json.loads(urllib.request.urlopen(req, timeout=10).read())["ok"] is True
        touched = json.loads(get("/api/file?path=" + urllib.parse.quote(f"{CWD}/auth.py"))[1])  # the VS Code extension's view
        assert [s["id"] for s in touched["sessions"]] == [SID]
        assert touched["sessions"][0]["file"] == {"reads": 0, "changes": 1, "added": 2, "removed": 1}
        folder = json.loads(get("/api/files?root=" + urllib.parse.quote(CWD))[1])  # and its file list
        assert [(f["rel"], f["sessions"]) for f in folder["files"]] == [("auth.py", 1)]
    finally:
        httpd.shutdown()


def test_file_sessions_matches_relative_paths(synced):
    from chronicle.server import App

    conn = synced["conn"]
    conn.execute("INSERT INTO sessions(id, agent, project_path, started_at) VALUES ('codex-1', 'codex', ?, '2026-09-21T09:00:00Z')",
                 (CWD,))
    conn.execute("INSERT INTO sessions(id, agent, project_path, started_at) VALUES ('other-1', 'codex', '/elsewhere', "
                 "'2026-09-22T09:00:00Z')")
    conn.executemany("INSERT INTO session_files(session_id, path, reads, edits) VALUES (?,?,?,?)",
                     [("codex-1", "auth.py", 2, 0), ("other-1", "auth.py", 0, 1)])  # Codex: relative to its folder
    conn.commit()
    app = App(synced["cfg"])
    found = app.file_sessions(f"{CWD}/./auth.py")
    assert found["path"] == f"{CWD}/auth.py" and found["total"] == 2
    assert [s["id"] for s in found["sessions"]] == ["codex-1", SID]  # newest first; /elsewhere's auth.py is another file
    assert found["sessions"][0]["file"] == {"reads": 2, "changes": 0, "added": 0, "removed": 0}
    assert app.file_sessions("auth.py") == {"path": "", "total": 0, "sessions": []}  # only absolute paths
    assert app.file_sessions(f"{CWD}/nothing.py")["sessions"] == []


def test_folder_files_lists_touched_files(synced, tmp_path):
    from chronicle.server import App

    conn = synced["conn"]
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "app.py").write_text("x")
    conn.execute("INSERT INTO sessions(id, agent, project_path, started_at) VALUES ('c-old', 'claude', ?, '2026-09-01T09:00:00Z')",
                 (str(proj),))
    conn.execute("INSERT INTO sessions(id, agent, project_path, started_at) VALUES ('x-new', 'codex', ?, '2026-09-05T09:00:00Z')",
                 (str(proj / "src"),))
    conn.executemany("INSERT INTO session_files(session_id, path, reads, edits) VALUES (?,?,?,?)", [
        ("c-old", f"{proj}/src/app.py", 0, 2),
        ("c-old", f"{proj}/gone.png", 1, 0),
        ("c-old", f"{proj}-sibling/app.py", 0, 1),  # a sibling folder sharing the prefix is not inside it
        ("x-new", "app.py", 3, 0),  # Codex, relative to proj/src
    ])
    conn.commit()
    app = App(synced["cfg"])
    listed = app.folder_files(str(proj))
    assert [(f["rel"], f["sessions"], f["changed"]) for f in listed["files"]] == [("src/app.py", 2, 1), ("gone.png", 1, 0)]
    assert listed["files"][0]["last"] == "2026-09-05T09:00:00Z"
    assert listed["files"][0]["session_ids"] == ["c-old", "x-new"] and listed["files"][0]["changed_ids"] == ["c-old"]
    assert [f["rel"] for f in app.folder_files(str(proj), existing=True)["files"]] == ["src/app.py"]
    assert [f["rel"] for f in app.folder_files(str(proj / "src"))["files"]] == ["app.py"]
    assert [f["rel"] for f in app.folder_files(CWD)["files"]] == ["auth.py"]
    assert app.folder_files("/")["files"] == [] and app.folder_files("proj")["files"] == []
    assert app.folder_files(str(proj), ignored=False)["total"] == 2  # not a git repository: nothing to leave out

    # in a git repository, ignored=False leaves out what .gitignore covers
    (proj / "debug.log").write_text("x")
    (proj / ".gitignore").write_text("*.log\n")
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    conn.execute("INSERT INTO session_files(session_id, path, reads, edits) VALUES ('c-old', ?, 0, 1)", (f"{proj}/debug.log",))
    conn.commit()
    assert [f["rel"] for f in app.folder_files(str(proj), existing=True)["files"]] == ["src/app.py", "debug.log"]
    assert [f["rel"] for f in app.folder_files(str(proj), existing=True, ignored=False)["files"]] == ["src/app.py"]


def test_install_hooks_preserves_existing_settings(env, tmp_path):
    cfg = env["cfg"]
    settings = env["claude_dir"] / "settings.json"
    settings.write_text(json.dumps({
        "permissions": {"defaultMode": "auto"},
        "hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "other-tool --flag"}]}],
                  "Stop": [{"hooks": [{"type": "command", "command": "notify-me"}]}]},
    }))
    install_hooks(cfg, "/opt/bin/chronicle", inject=True)
    install_hooks(cfg, "/opt/bin/chronicle", inject=True)  # idempotent
    data = json.loads(settings.read_text())
    assert data["permissions"] == {"defaultMode": "auto"}
    cmds = [h["command"] for g in data["hooks"]["SessionEnd"] for h in g["hooks"]]
    assert cmds.count("/opt/bin/chronicle hook session-end") == 1 and "other-tool --flag" in cmds
    assert hooks_installed(cfg) == {"SessionEnd": True, "SessionStart": True}
    assert list((cfg.home / "backups").glob("settings.json.*.bak"))
    uninstall_hooks(cfg)
    data = json.loads(settings.read_text())
    assert [h["command"] for g in data["hooks"]["SessionEnd"] for h in g["hooks"]] == ["other-tool --flag"]
    assert "SessionStart" not in data["hooks"] and data["hooks"]["Stop"]


def test_session_end_hook_detaches_and_ingests(env):
    payload = json.dumps({"session_id": SID, "transcript_path": str(env["main"]), "cwd": CWD, "hook_event_name": "SessionEnd"})
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "chronicle", "hook", "session-end"], input=payload, capture_output=True,
                          text=True, timeout=30, env={**os.environ, "FAKE_CLAUDE_MODE": "ok"})
    elapsed = time.monotonic() - t0
    assert proc.returncode == 0 and elapsed < 5, (proc.stderr, elapsed)
    deadline = time.monotonic() + 60
    status = None
    while time.monotonic() < deadline:
        if env["cfg"].db_path.exists():
            conn = connect(env["cfg"].db_path)
            row = conn.execute("SELECT ended_flag, analysis_status FROM sessions WHERE id=?", (SID,)).fetchone()
            conn.close()
            if row and row["analysis_status"] == "done":
                status = dict(row)
                break
        time.sleep(0.5)
    assert status == {"ended_flag": 1, "analysis_status": "done"}, (env["home"] / "logs" / "hooks.log").read_text()


def test_hooks_are_inert_for_internal_runs(env):
    proc = subprocess.run([sys.executable, "-m", "chronicle", "hook", "session-end"], input="{}", capture_output=True,
                          text=True, timeout=30, env={**os.environ, "CHRONICLE_INTERNAL": "1"})
    assert proc.returncode == 0
    time.sleep(1)
    assert not env["cfg"].db_path.exists() or connect(env["cfg"].db_path).execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_session_start_context(synced):
    from chronicle.analyze import analyze_session
    from chronicle.hooks import build_session_context
    from chronicle.synthesize import synthesize_project

    analyze_session(synced["conn"], synced["cfg"], SID)
    synthesize_project(synced["conn"], synced["cfg"], CWD)
    ctx = build_session_context(synced["cfg"], CWD + "/src")
    assert ctx and "demo-app" in ctx and "Token TTL unit mismatch." in ctx and len(ctx) <= synced["cfg"].inject_max_chars
    assert build_session_context(synced["cfg"], "/somewhere/else") is None


def test_dashboard_picks_up_config_changes(synced):
    from chronicle.config import set_config_value
    from chronicle.server import App

    app = App(synced["cfg"])
    assert app.cfg.codex_dirs == []
    import time as _t

    _t.sleep(0.01)
    set_config_value(app.cfg, "sources", "codex_dirs", '["~/.codex"]')
    app.refresh_config()
    assert app.cfg.codex_dirs and app.cfg.codex_dirs[0].name == ".codex"


def test_analyze_selected_sessions(synced):
    """The Sessions list's "Analyze N": picked sessions, skipped ones included, analyzed in one background job."""
    from chronicle.server import App

    conn = synced["conn"]
    conn.execute("UPDATE sessions SET analysis_status = 'skipped', analysis_reason = 'imported'")
    conn.execute("INSERT INTO sessions(id, source, project_path, analysis_status) VALUES ('hist-1', 'history', '/p', 'skipped')")
    conn.commit()
    app = App(synced["cfg"])
    matching = app.sessions({"status": "skipped", "ids_only": "1"})
    assert matching["total"] == 3 and "hist-1" in matching["ids"] and len(matching["ids"]) == 3

    r = app.action_analyze_many([SID[:8], "hist-1", "no-such-session"])
    assert r["started"] and r["count"] == 1 and r["dropped"] == 2
    for _ in range(200):
        if app.jobs.snapshot()["analyze:selection"]["state"] != "running":
            break
        time.sleep(0.05)
    job = app.jobs.snapshot()["analyze:selection"]
    assert job["state"] == "done", job
    status = dict(conn.execute("SELECT id, analysis_status FROM sessions").fetchall())
    assert status[SID] == "done" and status["hist-1"] == "skipped"
    assert all(v == "skipped" for k, v in status.items() if k != SID)  # the ones not picked stay as they were

    assert not app.action_analyze_many(["hist-1"])["started"]


def test_job_progress_counts():
    """The Activity panel draws a bar from "N of M" / "N/M" in a job's message."""
    from chronicle.server import Jobs

    jobs = Jobs()
    gate = threading.Event()

    def job(progress):
        for msg in ("analyzing sessions: 3 of 40 done", "importing ChatGPT chats… 1,200/2,000", "synthesizing…"):
            progress(msg)
            seen.append({k: jobs.snapshot()["j"][k] for k in ("done", "total")})
        gate.wait(5)
        return "ok"

    seen = []
    assert jobs.start("j", job)
    for _ in range(100):
        if len(seen) == 3:
            break
        time.sleep(0.01)
    gate.set()
    assert seen == [{"done": 3, "total": 40}, {"done": 1200, "total": 2000}, {"done": None, "total": None}]



def test_export_sessions(synced):
    """One session downloads as its own file, several as a .zip with an index; secrets stay redacted."""
    import io
    import zipfile

    from chronicle.server import App
    from chronicle.session_export import ExportError

    conn = synced["conn"]
    app = App(synced["cfg"])
    other = conn.execute("SELECT id FROM sessions WHERE id != ?", (SID,)).fetchone()[0]

    name, ctype, body = app.export(SID[:8], "md")
    text = body.decode()
    assert name.endswith(f"{SID[:8]}.md") and ctype.startswith("text/markdown")
    assert text.startswith("---\nsession_id: " + SID) and "## Conversation" in text and "### You" in text
    assert SECRET not in text

    data = json.loads(app.export(SID, "json")[2])
    assert data["session"]["id"] == SID and data["events"] and SECRET not in json.dumps(data)

    name, ctype, body = app.export(f"{SID},{other}", "md")
    z = zipfile.ZipFile(io.BytesIO(body))
    assert name.endswith(".zip") and ctype == "application/zip"
    assert sum(n.endswith(".md") for n in z.namelist()) == 3  # two sessions and the index
    assert SID[:8] in z.read(next(n for n in z.namelist() if n.endswith("index.md"))).decode()

    raw_name, _, raw = app.export(SID, "raw")  # the archived transcript, decompressed
    assert raw_name.endswith(".jsonl") and raw.startswith(b"{")
    check = app.export_check(f"{SID},{other}", "raw")
    assert check["count"] == 2
    history = conn.execute("SELECT id FROM sessions WHERE source = 'history'").fetchone()
    if history:
        try:
            app.export_check(history[0], "raw")
            raise AssertionError("a history-only session has no original transcript")
        except ExportError:
            pass


def test_export_from_the_command_line(synced, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    r = subprocess.run([sys.executable, "-m", "chronicle", "export", SID[:8], "--format", "json", "--out", str(out)],
                       capture_output=True, text=True, env={**os.environ})
    assert r.returncode == 0, r.stderr
    [written] = out.iterdir()
    assert written.suffix == ".json" and json.loads(written.read_text())["session"]["id"] == SID


def test_mcp_agent_filter_and_speaker(synced):
    """recent_sessions and search_sessions take an agent; transcripts name the agent that replied."""
    synced["conn"].execute("UPDATE sessions SET agent = 'claude-ai' WHERE id = ?", (SID,))
    synced["conn"].commit()
    out = _run_mcp(synced, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "recent_sessions", "arguments": {"days": 100000, "agent": "claude-ai"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "recent_sessions", "arguments": {"days": 100000, "agent": "codex"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "search_sessions", "arguments": {"query": "logout", "agent": "claude-ai"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "search_sessions", "arguments": {"query": "logout", "agent": "bob"}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "get_transcript", "arguments": {"session_id": SID, "limit": 5}}},
    ])
    by_id = {m["id"]: m for m in out}
    text = lambda i: by_id[i]["result"]["content"][0]["text"]  # noqa: E731
    schema = {t["name"]: t["inputSchema"]["properties"] for t in by_id[1]["result"]["tools"]}
    assert "chatgpt" in schema["recent_sessions"]["agent"]["enum"] and "agent" in schema["search_sessions"]
    assert SID[:8] in text(2) and "Claude.ai" in text(2)
    assert text(3) == "No sessions in that window."
    assert SID[:8] in text(4) and "Claude.ai" in text(4)
    assert text(5).startswith("No Bob sessions matched")
    assert "CLAUDE:" in text(6) and "USER" in text(6)


def test_mcp_page_info(env):
    """Settings › MCP: the command that starts the server, its tools, and config to paste for each kind of client."""
    import tomllib

    from chronicle.mcp_server import TOOLS
    from chronicle.server import App

    info = App(env["cfg"]).mcp_info()
    assert info["args"][-1] == "mcp" and info["command"]
    assert [t["name"] for t in info["tools"]] == [t["name"] for t in TOOLS]
    search = next(t for t in info["tools"] if t["name"] == "search_sessions")
    assert search["params"][0] == "query" and "agent" in search["params"]  # required first
    snip = info["snippets"]
    assert json.loads(snip["json"])["mcpServers"]["interlatch"]["command"] == info["command"]
    assert json.loads(snip["vscode"])["servers"]["interlatch"]["type"] == "stdio"
    assert tomllib.loads(snip["codex"])["mcp_servers"]["interlatch"]["args"] == info["args"]
    assert snip["claude"].startswith("claude mcp add --scope user interlatch -- ") and snip["claude"].endswith(" mcp")
