import json

import pytest

from interlatch.sources.antigravity import (conversation_dirs, load_db_meta, load_steps, load_title,
                                            parse_antigravity_conversation, transcript_files)
from interlatch.core.config import load_config
from interlatch.storage.sqlite import connect as db_connect
from interlatch.ingestion.service import sync

from antigravity_fixture import EMPTY_ID, FOLDER, GAME, NEW_ID, OLD_ID, write_antigravity_home


@pytest.fixture()
def agy(env, monkeypatch):
    env["agy"] = write_antigravity_home(env["tmp"] / "home" / ".gemini" / "antigravity")
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(env["agy"]))
    monkeypatch.setattr("interlatch.connectors.APPLICATIONS", env["tmp"] / "Applications")
    return env


def _parse(home, cid):
    return parse_antigravity_conversation(home / "brain" / cid, title=load_title(home, cid),
                                          db_meta=load_db_meta(home / "conversations" / f"{cid}.db"))


def test_current_conversation(agy):
    home = agy["agy"]
    assert [d.name for d in conversation_dirs(home)] == sorted([NEW_ID, OLD_ID, EMPTY_ID])
    ps = _parse(home, NEW_ID)
    assert ps.ai_title == "Fix the failing test あ" and ps.entrypoint == "antigravity"
    assert (ps.project_path, ps.git_branch, ps.git_remote) == (FOLDER, "feature/fix-test", "git@github.com:org/agy-app.git")
    assert ps.n_prompts == 1 and ps.first_prompt == "fix the failing test" and ps.parse_errors == 0
    assert [t.name for t in ps.tool_calls] == ["run_command", "view_file", "replace_file_content"]
    run = ps.tool_calls[0]
    assert run.command == "pytest -q" and run.duration_ms == 2000 and not run.is_error
    assert ps.files[f"{FOLDER}/app.py"].reads == 1 and ps.files[f"{FOLDER}/app.py"].edits == 1
    assert len(ps.api_calls) == 3 and ps.primary_model == "gemini-3.8-flash"
    assert ps.api_calls[1].input_tokens == 200 and ps.api_calls[1].cache_read_tokens == 1000
    kinds = [e.kind for e in ps.events]
    assert kinds.count("thinking") == 1 and kinds[-1] == "text"
    note = next(e for e in ps.events if e.kind == "notification")
    assert note.text.startswith('Task id "x/task-9" finished')


def test_chunks_read_as_one_log(agy):
    conv = agy["agy"] / "brain" / NEW_ID
    whole, _ = load_steps(conv)
    (conv / ".system_generated" / "logs" / "transcript_full.jsonl").unlink()
    assert [f.parent.name for f in transcript_files(conv)] == ["transcript_full", "transcript_full"]
    assert load_steps(conv) == (whole, 0)


def test_older_conversation(agy):
    ps = _parse(agy["agy"], OLD_ID)
    assert ps.ai_title is None and ps.git_branch is None and ps.project_path == GAME
    assert ps.primary_model == "gemini-3.5-flash" and ps.api_calls == []
    assert ps.n_prompts == 2 and ps.last_prompt == "Comments on artifact URI: implementation_plan.md\n\nThe user has approved this document."
    listing, write, artifact, run = ps.tool_calls
    assert listing.summary == GAME and write.file_path == f"{GAME}/main.ts"
    assert artifact.is_error and run.is_error and run.command == "pnpm -v" and run.duration_ms == 320_000
    assert list(ps.files) == [f"{GAME}/main.ts"]  # not the listed folder, not the agent's own task.md
    assert _parse(agy["agy"], EMPTY_ID) is None


def test_sync_antigravity(agy):
    cfg = agy["cfg"]
    cfg.antigravity_dirs = [agy["agy"]]
    conn = db_connect(cfg.db_path)
    report = sync(cfg, conn)
    assert not report.errors
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT id, agent, title, project_path, git_branch FROM sessions WHERE agent = 'antigravity'")}
    assert set(rows) == {NEW_ID, OLD_ID}
    assert rows[NEW_ID]["title"] == "Fix the failing test あ" and rows[NEW_ID]["git_branch"] == "feature/fix-test"
    archived = {str(p.relative_to(cfg.archive_dir / "antigravity")) for p in (cfg.archive_dir / "antigravity").rglob("*") if p.is_file()}
    assert {f"{NEW_ID}/transcript_full.jsonl.gz", f"{NEW_ID}/task.md", f"{OLD_ID}/transcript.jsonl.gz"} <= archived
    assert not any(p.endswith(".jpg") for p in archived)
    again = sync(cfg, conn)
    assert again.sessions_new == 0 and again.sessions_updated == 0


def test_connect_antigravity(agy):
    from interlatch.connectors import all_status, connect, disconnect

    cfg = agy["cfg"]
    mcp = agy["agy"].parent / "config" / "mcp_config.json"
    mcp.parent.mkdir(parents=True)
    mcp.write_text(json.dumps({"mcpServers": {"unityMCP": {"serverUrl": "http://127.0.0.1:8080/mcp", "type": "http"}}}))
    status = {c["name"]: c for c in all_status(cfg, db_connect(cfg.db_path))}["antigravity"]
    assert status["detected"] and not status["connected"] and status["on_disk"] == 3
    assert "1 kept only in Antigravity's own encrypted store" in status["checks"][-1]["detail"]
    connect(cfg, "antigravity", "/opt/bin/chronicle")
    assert load_config(cfg.home).antigravity_dirs == [agy["agy"]]
    servers = json.loads(mcp.read_text())["mcpServers"]
    assert set(servers) == {"unityMCP", "interlatch"} and servers["interlatch"] == {"command": "/opt/bin/chronicle", "args": ["mcp"]}
    disconnect(cfg, "antigravity")
    assert set(json.loads(mcp.read_text())["mcpServers"]) == {"unityMCP"}
    assert load_config(cfg.home).antigravity_dirs == []
