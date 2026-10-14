from pathlib import Path

import pytest

from interlatch.sources.codex.parser import _js_string, parse_codex_session
from interlatch.core.config import Config, load_config, set_config_value
from interlatch.connectors import all_status, connect, disconnect
from interlatch.storage.sqlite import connect as db_connect
from interlatch.analysis.digest import build_digest
from interlatch.ingestion.service import sync

from codex_fixture import (CODE_ID, CODEX_CWD, CODEX_ID, IMP_DUP, IMP_NEW, LEGACY_ID, RECOVERED, SUB_ID, code_mode_rollout,
                           write_codex_home)
from conftest import SID


@pytest.fixture()
def codex(env, monkeypatch):
    home = write_codex_home(env["tmp"] / "codex")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(Config, "codex_bin", lambda self: None)  # never touch the real Codex CLI/config
    env["codex"] = home
    env["day"] = home / "sessions" / "2026" / "09" / "21"
    return env


def test_parse_codex_rollout(codex):
    day = codex["day"]
    ps = parse_codex_session(next(day.glob(f"*{CODEX_ID}.jsonl")), [next(day.glob(f"*{SUB_ID}.jsonl"))], title="Fix flaky test")
    assert ps.id == CODEX_ID and ps.project_path == CODEX_CWD and ps.git_branch == "feat/flaky"
    assert ps.entrypoint == "codex:codex_vscode" and ps.cc_version == "0.147.0" and ps.ai_title == "Fix flaky test"
    assert ps.n_prompts == 2 and ps.first_prompt == "Fix the flaky test in tests/test_app.py"  # IDE envelope unwrapped
    assert ps.n_interrupts == 1 and ps.n_compactions == 1
    names = [t.name for t in ps.tool_calls if not t.agent_id]
    assert names == ["exec", "apply_patch", "exec_command", "collaboration.spawn_agent"]
    exec_call = ps.tool_calls[0]
    assert exec_call.is_error and exec_call.duration_ms == 5000 and exec_call.command == "pytest tests/test_app.py -x"
    assert not ps.tool_calls[1].is_error and not ps.tool_calls[2].is_error
    app, notes = ps.files[f"{CODEX_CWD}/app.py"], ps.files[f"{CODEX_CWD}/notes.md"]
    assert (app.edits, app.lines_added, app.lines_removed) == (1, 2, 1) and (notes.writes, notes.lines_added) == (1, 1)
    main_calls = [c for c in ps.api_calls if not c.agent_id]
    assert len(main_calls) == 1  # token_count repeats the usage record and must not double count
    assert (main_calls[0].input_tokens, main_calls[0].cache_read_tokens, main_calls[0].output_tokens) == (200, 800, 50)
    assert main_calls[0].model == "gpt-5.5" and main_calls[0].cost_usd > 0
    sub = ps.subagents[SUB_ID]
    assert sub.description == "check the docs" and sub.output_tokens == 30
    # newer Codex: FileChange items carry edits, CommandExecution items the real exit code
    guide, abs_app = ps.files[f"{CODEX_CWD}/docs/guide.md"], ps.files["/abs/app.py"]
    assert (guide.writes, guide.lines_added) == (1, 2) and (abs_app.edits, abs_app.lines_added, abs_app.lines_removed) == (1, 1, 1)
    npm = next(t for t in ps.tool_calls if t.command == "npm test")
    assert npm.is_error and sub.n_tool_errors == 1
    kinds = [e.kind for e in ps.events if not e.agent_id]
    assert "thinking" in kinds and kinds.count("prompt") == 2 and "meta" in kinds


def test_code_mode_exec(tmp_path):
    path = tmp_path / f"rollout-2026-09-22T09-00-00-{CODE_ID}.jsonl"
    path.write_text("\n".join(code_mode_rollout()) + "\n")
    ps = parse_codex_session(path)
    calls = {c.tool_use_id: c for c in ps.tool_calls}
    assert [c.name for c in ps.tool_calls] == ["exec_command", "apply_patch", "exec", "mcp__playwright__browser_navigate",
                                              "exec_command", "wait", "wait", "exec_command"]
    lint = calls["e1"]
    assert lint.command == 'rg -n "await tools.nope(" src' and lint.is_error  # nonzero exit code in the JSON chunk
    assert not calls["e2"].is_error and not calls["e3"].is_error and not calls["e4"].is_error
    lib = ps.files[f"{CODEX_CWD}/lib.py"]
    assert (lib.edits, lib.lines_added, lib.lines_removed) == (1, 1, 1)
    assert calls["e3"].summary.startswith("exec_command, view_image: git status") and ps.files["/tmp/shot.png"].reads == 1
    assert calls["e4"].mcp_server == "playwright" and ps.mcp_servers["playwright"] == 1
    build = calls["e5"]  # still running after 10s, failed later: the failure is the build's, not the wait's
    assert build.is_error and build.duration_ms == 60_000 and not calls["w1"].is_error and not calls["w2"].is_error
    assert calls["e6"].is_error  # aborted by the user
    assert ps.n_tool_errors == 3
    result = next(e.text for e in ps.events if e.kind == "tool_result" and e.tool_use_id == "e1")
    assert "Exit code: 2\nrg: regex parse error" in result and "chunk_id" not in result


def test_js_string_escapes():
    src = r'x("a\nbé😀\"c\x41", 1)'
    value, end = _js_string(src, 2)
    assert value == 'a\nbé😀"cA' and src[end:] == ", 1)"
    assert _js_string('"never closed', 0)[0] is None


def test_legacy_token_count_fallback(codex):
    ps = parse_codex_session(next(codex["day"].glob(f"*{LEGACY_ID}.jsonl")))
    assert [c.input_tokens for c in ps.api_calls] == [100, 150]
    assert ps.primary_model == "gpt-5.4"


def test_sync_codex_home(codex):
    cfg = codex["cfg"]
    cfg.codex_dirs = [codex["codex"]]
    conn = db_connect(cfg.db_path)
    report = sync(cfg, conn)
    assert not report.errors
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT id, agent, source, title, n_prompts, n_subagents, project_path FROM sessions")}
    assert rows[CODEX_ID]["agent"] == "codex" and rows[CODEX_ID]["title"] == "Fix flaky test" and rows[CODEX_ID]["n_subagents"] == 1
    assert rows[LEGACY_ID]["agent"] == "codex"
    assert SUB_ID not in rows  # a subagent thread belongs to its parent
    assert IMP_DUP not in rows and IMP_NEW not in rows
    assert rows[SID]["source"] == "transcript"  # Claude's own transcript wins over Codex's copy
    rec = rows[RECOVERED]
    assert (rec["agent"], rec["source"], rec["title"], rec["n_prompts"]) == ("claude", "codex-import", "Set up the repo", 1)
    mem = conn.execute("SELECT title, project_path, agent FROM knowledge WHERE source = 'memory' AND agent = 'codex'").fetchone()
    assert mem["title"] == "Task Group: codex-app seeding" and mem["project_path"] == CODEX_CWD
    archived = {p.name for p in cfg.archive_dir.rglob("*") if p.is_file()}
    assert f"rollout-2026-09-21T10-00-00-{CODEX_ID}.jsonl.gz" in archived and "MEMORY.md" in archived and "config" not in archived
    again = sync(cfg, conn)
    assert again.sessions_new == 0 and again.sessions_updated == 0
    _, digest = build_digest(conn, CODEX_ID, 100_000)
    assert "CODEX: I'll run the tests first." in digest.text and "✗ Exit code: 1" in digest.text
    header, _ = build_digest(conn, RECOVERED, 100_000)
    assert "agent: Claude Code (session imported into Codex)" in header


def test_codex_is_opt_in(codex):
    cfg = codex["cfg"]
    assert cfg.codex_dirs == []
    conn = db_connect(cfg.db_path)
    sync(cfg, conn)
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE agent = 'codex'").fetchone()[0] == 0


def test_connectors_status_connect_disconnect(codex):
    cfg = codex["cfg"]
    conn = db_connect(cfg.db_path)
    status = {c["name"]: c for c in all_status(cfg, conn)}
    assert status["claude"]["connected"] and status["codex"]["detected"] and not status["codex"]["connected"]
    assert status["codex"]["on_disk"] == 5 and status["codex"]["imports"] == 2
    hook = next(c for c in status["codex"]["checks"] if c["label"] == "Session-end hook")
    assert "notifier" in hook["detail"]  # explains why no hook: the notify slot is taken
    actions = connect(cfg, "codex", "/opt/bin/chronicle")
    assert any("recording" in a for a in actions) and any("codex CLI not found" in a for a in actions)
    cfg = load_config(cfg.home)
    assert cfg.codex_dirs == [Path(str(codex["codex"]).replace(str(Path.home()), "~", 1)).expanduser()]
    sync(cfg, conn)
    status = {c["name"]: c for c in all_status(cfg, conn)}
    assert status["codex"]["connected"] and status["codex"]["recorded"]["sessions"] == 2 and status["codex"]["recovered"] == 1
    disconnect(cfg, "codex")
    assert load_config(cfg.home).codex_dirs == []


def test_set_config_value_keeps_layout(env):
    cfg = env["cfg"]
    cfg.config_path.write_text('# my notes\n[sources]\n# keep me\nclaude_dirs = ["~/.claude"]\ncodex_dirs = [\n  "~/a",\n  "~/b",\n]\n\n[analysis]\nauto = true\n')
    set_config_value(cfg, "sources", "codex_dirs", '["~/.codex"]')
    set_config_value(cfg, "analysis", "model", '"opus"')
    set_config_value(cfg, "server", "port", "9000")
    text = cfg.config_path.read_text()
    assert "# my notes" in text and "# keep me" in text and text.count("[sources]") == 1
    loaded = load_config(cfg.home)
    assert loaded.codex_dirs == [Path("~/.codex").expanduser()] and loaded.analysis.model == "opus" and loaded.server_port == 9000
    with pytest.raises(ValueError):  # tomllib.TOMLDecodeError
        set_config_value(cfg, "sources", "codex_dirs", "[unterminated")
    assert load_config(cfg.home).server_port == 9000  # a bad value never reaches the file
