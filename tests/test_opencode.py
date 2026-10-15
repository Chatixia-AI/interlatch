import gzip
import json
import sqlite3

import pytest

from interlatch.core.config import load_config
from interlatch.ingestion.service import sync
from interlatch.sources.opencode import list_sessions, load_tree, mcp_servers, parse_opencode_session, session_trees
from interlatch.storage.sqlite import connect as db_connect

from opencode_fixture import CHILD_ID, EMPTY_ID, FOLDER, ROOT_ID, T0, write_opencode_config, write_opencode_home


@pytest.fixture()
def oc(env, monkeypatch):
    env["oc"] = write_opencode_home(env["tmp"] / "home" / ".local" / "share" / "opencode")
    env["oc_config"] = write_opencode_config(env["tmp"] / "home" / ".config" / "opencode")
    monkeypatch.setenv("OPENCODE_HOME", str(env["oc"]))
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(env["oc_config"]))
    monkeypatch.setattr("interlatch.runtime.connectors.shutil.which", lambda name: None)
    return env


def _parse(home, sid=ROOT_ID, servers=None):
    db = home / "opencode.db"
    root, children = next((r, c) for r, c in session_trees(list_sessions(db)) if r["id"] == sid)
    return parse_opencode_session(load_tree(db, root, children), servers=servers)


def test_session(oc):
    ps = _parse(oc["oc"], servers=mcp_servers(oc["oc_config"]))
    assert (ps.ai_title, ps.project_path, ps.entrypoint, ps.cc_version) == ("Fix flaky login test", FOLDER, "opencode", "1.19.0")
    # the synthetic text OpenCode adds to a prompt (a file it read in) is not the user's words; the image is counted
    assert ps.n_prompts == 3 and ps.first_prompt == "fix the flaky login test" and ps.last_prompt == "stop"
    assert ps.n_images == 1 and ps.n_interrupts == 1 and ps.n_api_errors == 1 and ps.parse_errors == 0
    names = [(t.name, t.agent_id) for t in ps.tool_calls]
    assert names == [("read", ""), ("bash", ""), ("edit", ""), ("task", ""), ("interlatch_search_sessions", ""),
                     ("apply_patch", ""), ("write", ""), ("bash", ""), ("grep", CHILD_ID)]
    bash, failed = ps.tool_calls[1], ps.tool_calls[7]
    assert bash.command == "npm test -- login" and bash.duration_ms == 2400 and not bash.is_error
    assert failed.is_error and failed.command == "rm -rf build"
    assert ps.tool_calls[4].mcp_server == "interlatch" and ps.mcp_servers == {"interlatch": 1}


def test_files_and_lines(oc):
    files = {k: (v.reads, v.edits, v.writes, v.lines_added, v.lines_removed) for k, v in _parse(oc["oc"]).files.items()}
    assert files == {
        f"{FOLDER}/test/login.test.ts": (1, 1, 0, 1, 1),  # read, then edited (lines from the edit's diff)
        f"{FOLDER}/docs/notes.md": (0, 0, 1, 2, 0),  # apply_patch added it (its paths are relative to the session)
        f"{FOLDER}/src/app.ts": (0, 1, 0, 1, 1),
        f"{FOLDER}/README.md": (0, 0, 1, 2, 0),
    }  # not the folder grep searched


def test_tokens_and_cost(oc):
    calls = [(c.agent_id, c.model, c.input_tokens, c.output_tokens, c.cache_read_tokens) for c in _parse(oc["oc"]).api_calls]
    assert calls == [("", "claude-sonnet-4-5", 9100, 120, 20100), ("", "claude-sonnet-4-5", 9800, 300, 21000),
                     ("", "gpt-5.5", 5000, 250, 0),  # reasoning tokens are billed as output
                     (CHILD_ID, "claude-haiku-4-5", 3000, 40, 0)]
    costs = [c.cost_usd for c in _parse(oc["oc"]).api_calls]
    assert costs[0] == pytest.approx(0.0123) and costs[3] == pytest.approx(0.002)  # OpenCode's own figure
    assert costs[1] > 0 and costs[2] > 0  # priced at 0 by OpenCode (a subscription): the list-price estimate


def test_subagent_session_is_folded_into_its_parent(oc):
    ps = _parse(oc["oc"])
    sub = ps.subagents[CHILD_ID]
    assert (sub.agent_type, sub.description, sub.tool_use_id, sub.model) == ("explore", "Find auth helpers", "toolu_04",
                                                                             "claude-haiku-4-5")
    assert sub.n_tool_calls == 1 and sub.input_tokens == 3000 and sub.started_at.startswith("2026-10-15T09:00:20")
    assert ps.n_prompts == 3  # the subagent's brief is not a prompt of yours
    assert [r["id"] for r, _ in session_trees(list_sessions(oc["oc"] / "opencode.db"))] == [EMPTY_ID, ROOT_ID]


def test_unused_session_is_skipped(oc):
    assert _parse(oc["oc"], EMPTY_ID) is None


def test_sync_opencode(oc):
    cfg = oc["cfg"]
    cfg.opencode_dirs = [oc["oc"]]
    conn = db_connect(cfg.db_path)
    report = sync(cfg, conn)
    assert not report.errors
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT id, agent, title, project_path FROM sessions WHERE agent = 'opencode'")}
    assert set(rows) == {ROOT_ID} and rows[ROOT_ID]["title"] == "Fix flaky login test"
    archived = cfg.archive_dir / "opencode" / f"{ROOT_ID}.json.gz"
    doc = json.loads(gzip.open(archived, "rt").read())
    assert doc["info"]["id"] == ROOT_ID and [c["info"]["id"] for c in doc["children"]] == [CHILD_ID]
    assert parse_opencode_session(doc).n_prompts == 3  # the archive alone is enough to read the session again
    assert "must-never-be-read" not in archived.read_bytes().decode("latin-1") and not (cfg.archive_dir / "opencode" / "opencode.db").exists()

    again = sync(cfg, conn)
    assert again.sessions_new == 0 and again.sessions_updated == 0

    db = sqlite3.connect(oc["oc"] / "opencode.db")  # the session goes on: a new prompt
    db.execute("INSERT INTO message VALUES ('msg_zz1', ?, ?, ?, ?)", (ROOT_ID, T0 + 90_000, T0 + 90_000, json.dumps(
        {"role": "user", "time": {"created": T0 + 90_000}, "agent": "build"})))
    db.execute("INSERT INTO part VALUES ('prt_zz1', 'msg_zz1', ?, ?, ?, ?)", (ROOT_ID, T0 + 90_000, T0 + 90_000,
                                                                             json.dumps({"type": "text", "text": "thanks"})))
    db.commit()
    db.close()
    later = sync(cfg, conn, force=False)
    assert later.sessions_updated == 1
    assert conn.execute("SELECT n_prompts FROM sessions WHERE id = ?", (ROOT_ID,)).fetchone()[0] == 4


def test_connect_opencode(oc):
    from interlatch.runtime.connectors import all_status, connect, disconnect

    cfg = oc["cfg"]
    status = {c["name"]: c for c in all_status(cfg, db_connect(cfg.db_path))}["opencode"]
    assert status["detected"] and not status["connected"] and status["on_disk"] == 1  # not the unused one
    assert status["checks"][2]["ok"]  # opencode.jsonc already names an interlatch server
    jsonc = (oc["oc_config"] / "opencode.jsonc").read_text()
    connect(cfg, "opencode", "/opt/bin/interlatch")
    assert load_config(cfg.home).opencode_dirs == [oc["oc"]]
    written = json.loads((oc["oc_config"] / "opencode.json").read_text())
    assert written["mcp"]["interlatch"] == {"type": "local", "command": ["/opt/bin/interlatch", "mcp"], "enabled": True}
    assert (oc["oc_config"] / "opencode.jsonc").read_text() == jsonc  # comments and all: never rewritten
    disconnect(cfg, "opencode")
    assert json.loads((oc["oc_config"] / "opencode.json").read_text())["mcp"] == {}
    assert load_config(cfg.home).opencode_dirs == []


# ------------------------------------------------------------------ OpenCode 2.x
def test_v2_session(env):
    from opencode_fixture import V2_CHILD, V2_FOLDER, V2_ROOT, write_opencode2_home

    db = write_opencode2_home(env["tmp"] / "oc2") / "opencode.db"
    trees = session_trees(list_sessions(db))
    assert [(r["id"], [c["id"] for c in kids]) for r, kids in trees] == [(V2_ROOT, [V2_CHILD])]
    ps = parse_opencode_session(load_tree(db, *trees[0]))
    assert (ps.ai_title, ps.project_path, ps.cc_version) == ("Greeting", V2_FOLDER, "2.0.29")
    assert ps.n_prompts == 2 and ps.first_prompt == "hio there" and ps.last_prompt == "now summarize"
    assert ps.n_images == 1 and ps.n_api_errors == 1 and ps.n_interrupts == 1 and ps.n_compactions == 1
    assert [(t.name, t.agent_id) for t in ps.tool_calls] == [
        ("shell", ""), ("read", ""), ("edit", ""), ("write", ""), ("shell", ""), ("subagent", ""), ("shell", ""),
        ("glob", V2_CHILD)]
    shell, failed, mine = ps.tool_calls[0], ps.tool_calls[4], ps.tool_calls[6]
    assert shell.command == "git status" and shell.duration_ms == 250 and not shell.is_error
    assert failed.is_error and mine.command == "ls"  # a command the user ran with `!`
    files = {k: (v.reads, v.edits, v.writes, v.lines_added, v.lines_removed) for k, v in ps.files.items()}
    assert files == {f"{V2_FOLDER}/src/app.ts": (1, 1, 0, 1, 1), f"{V2_FOLDER}/notes.md": (0, 0, 1, 1, 0)}
    calls = [(c.agent_id, c.model, c.input_tokens, c.output_tokens, c.cache_read_tokens) for c in ps.api_calls]
    assert calls == [("", "claude-sonnet-4-5", 1000, 60, 4000), ("", "claude-sonnet-4-5", 1200, 30, 4100),
                     ("", "claude-sonnet-4-5", 5000, 100, 0), (V2_CHILD, "claude-haiku-4-5", 800, 20, 0)]
    assert ps.api_calls[0].cost_usd == pytest.approx(0.004) and ps.api_calls[1].cost_usd > 0
    sub = ps.subagents[V2_CHILD]
    assert (sub.agent_type, sub.description, sub.tool_use_id) == ("explore", "Find the tests", "call_6")
    kinds = [e.kind for e in ps.events if not e.agent_id]
    assert "compact_summary" in kinds and kinds[-1] == "interrupt"


def test_v2_status_counts_sessions_with_a_prompt(env):
    from interlatch.sources.opencode import root_folders
    from opencode_fixture import V2_FOLDER, write_opencode2_home

    assert root_folders(write_opencode2_home(env["tmp"] / "oc2") / "opencode.db") == [V2_FOLDER]
