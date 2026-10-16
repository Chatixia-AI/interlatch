"""Analysis through IBM Bob Shell (`analysis.backend = "bob"`): a fake `bob run` reads the mode Chronicle wrote into
its workspace, answers with the fake claude's reply for that system prompt, and prints Bob's stream-json events.
Also: the dashboard's model and effort settings for Claude Code and Codex."""

import json
import sqlite3
import stat
import sys

import pytest

from interlatch.core.config import load_config, set_config_value
from interlatch.analysis.runners.base import LLMError, UsageLimitError
from interlatch.analysis.runners.factory import make_runner
from interlatch.analysis.runners.bob import BOB_TOOL_GROUPS, NO_TOOLS, BobRunner
from interlatch.analysis.runners.settings import key_source, set_key
from interlatch.application.workflows import run_worker

from conftest import CWD, SID
from interlatch.interfaces.web import api

FAKE_BOB = r'''#!{python}
import json, os, subprocess, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
ws = args[args.index("--workspace") + 1]
modes = json.load(open(os.path.join(ws, ".bob", "custom_modes.yaml")))["customModes"]
mode = next(m for m in modes if m["slug"] == args[args.index("--mode") + 1])
with open(os.environ["FAKE_BOB_LOG"], "a") as fh:
    fh.write(json.dumps({"args": args, "prompt_chars": len(prompt), "groups": mode["groups"], "cwd": os.getcwd(),
                         "key": os.environ.get("BOB_API_KEY"), "internal": os.environ.get("INTERLATCH_INTERNAL"),
                         "role": mode["roleDefinition"]}) + "\n")
how = os.environ.get("FAKE_BOB_MODE", "ok")
if how == "tool-once":  # only the first call reaches for a tool
    how = "tool" if sum(1 for _ in open(os.environ["FAKE_BOB_LOG"])) == 1 else "ok"
if how == "license":
    print("Error: A license agreement is required. Please accept the license terms before proceeding.", file=sys.stderr)
    sys.exit(1)
def emit(**e):
    print(json.dumps({"timestamp": "2026-10-07T01:44:00Z", **e}), flush=True)
emit(type="message", role="user", content=prompt)
if how == "tool":
    emit(type="tool_use", tool_name="execute_command", tool_id="t1", parameters={"command": "cat ~/.ssh/id_ed25519"})
    emit(type="tool_result", tool_id="t1", status="success", output="...")
reply = subprocess.run([os.environ["FAKE_CLAUDE_BIN"], "-p", "--system-prompt", mode["roleDefinition"]], input=prompt,
                       capture_output=True, text=True, env={**os.environ, "FAKE_CLAUDE_LOG": ""})
text = json.loads(reply.stdout)["result"]
for i in range(0, len(text), 40):  # Bob sends the reply in pieces
    emit(type="message", role="assistant", content=text[i:i + 40])
emit(type="result", status="success", stats={"task_id": "abc", "duration_ms": 3313, "session_costs": 0.031, "max_cost": 3,
                                             "tool_calls": 1 if how == "tool" else 0})
'''


@pytest.fixture()
def bob_env(synced, monkeypatch):
    fake = synced["tmp"] / "bin" / "bob"
    fake.write_text(FAKE_BOB.replace("{python}", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_BOB_LOG", str(synced["tmp"] / "fake_bob.log"))
    monkeypatch.setenv("FAKE_CLAUDE_BIN", str(synced["fake"]))
    monkeypatch.delenv("BOB_API_KEY", raising=False)
    set_config_value(synced["cfg"], "analysis", "backend", '"bob"')
    set_config_value(synced["cfg"], "analysis", "bob_bin", json.dumps(str(fake)))
    synced["cfg"] = load_config(synced["cfg"].home)
    set_key(synced["cfg"], "bob", "bob-test-key")
    return synced


def bob_calls(env) -> list[dict]:
    path = env["tmp"] / "fake_bob.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_bob_runs_headless_with_no_tools(bob_env):
    cfg = bob_env["cfg"]
    runner = make_runner(cfg)
    assert isinstance(runner, BobRunner) and runner.available()
    res = runner.run("<transcript>hello</transcript>", {"type": "object"}, system="Summarize the session.", model="sonnet")
    assert res.data["title"] == "Fixed login token expiry bug"
    assert res.model == "bob" and res.cost_usd == pytest.approx(0.031) and res.duration_ms == 3313
    call = bob_calls(bob_env)[0]
    args = call["args"]
    assert args[:3] == ["run", "--format", "stream-json"] and "--trust" not in args
    assert args[args.index("--max-turns") + 1] == "1" and "--disable-mcp" in args and "--disable-subagents" in args
    assert args[args.index("--disable-tool-groups") + 1].split(",") == list(BOB_TOOL_GROUPS)
    assert args[args.index("--max-cost") + 1] == "3.00"
    assert call["groups"] == [] and call["key"] == "bob-test-key" and call["internal"] == "1"
    assert call["role"].startswith("Summarize the session.") and call["role"].endswith(NO_TOOLS)
    workspace = args[args.index("--workspace") + 1]
    assert cfg.is_internal_path(workspace) and call["cwd"] == workspace
    assert not (cfg.home / "workdir" / "bob").exists() or not any((cfg.home / "workdir" / "bob").iterdir())  # removed


def test_bob_reply_after_a_tool_call_is_discarded(bob_env, monkeypatch):
    monkeypatch.setenv("FAKE_BOB_MODE", "tool")
    with pytest.raises(LLMError, match=r"used a tool \(execute_command\); the reply was discarded"):
        make_runner(bob_env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")
    assert len(bob_calls(bob_env)) == 2  # tried again once


def test_bob_retries_once_after_a_tool_call(bob_env, monkeypatch):
    """Bob reads tool calls out of the reply's text, so the model can write one with every group off: one more try."""
    monkeypatch.setenv("FAKE_BOB_MODE", "tool-once")
    res = make_runner(bob_env["cfg"]).run("<transcript>hello</transcript>", {"type": "object"},
                                          system="Summarize the session.")
    assert res.data["title"] == "Fixed login token expiry bug"
    assert len(bob_calls(bob_env)) == 2 and res.cost_usd == pytest.approx(0.062)  # the discarded call is paid for too


def test_bob_license_or_key_problem_pauses(bob_env, monkeypatch):
    monkeypatch.setenv("FAKE_BOB_MODE", "license")
    with pytest.raises(UsageLimitError, match="license agreement"):
        make_runner(bob_env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")


def test_bob_needs_a_key(bob_env, monkeypatch):
    cfg = bob_env["cfg"]
    set_key(cfg, "bob", None)
    runner = make_runner(cfg)
    assert not runner.available() and "add a Bob API key (BOB_API_KEY)" in runner.unavailable_reason()
    monkeypatch.setenv("BOB_API_KEY", "from-env")
    assert make_runner(cfg).available() and key_source(cfg, "bob") == ("from-env", "$BOB_API_KEY")
    assert "from-env" not in json.dumps(make_runner(cfg).describe())


def test_worker_end_to_end_with_bob(bob_env):
    cfg, conn = bob_env["cfg"], bob_env["conn"]
    report = run_worker(cfg)
    assert report.analyzed == [SID] and not report.failed and CWD in report.synthesized
    row = conn.execute("SELECT model, cost_usd FROM analyses WHERE kind = 'session' AND target = ?", (SID,)).fetchone()
    assert row["model"] == "bob" and row["cost_usd"] == pytest.approx(0.031)
    assert not (bob_env["tmp"] / "fake_claude.log").exists() and bob_calls(bob_env)


def test_chronicles_own_bob_tasks_are_not_imported(env, tmp_path):
    from interlatch.runtime.connectors import bob_status
    from interlatch.storage.sqlite import connect
    from interlatch.ingestion.service import sync

    from copilot_fixture import BOB_TASK, write_bob_home

    cfg = env["cfg"]
    home = write_bob_home(tmp_path / "bobhome")
    db = sqlite3.connect(home / "db" / "bob.db")
    run = cfg.home / "workdir" / "bob" / "run-abc"
    db.execute("INSERT INTO tasks (id, project_id, title, directory, created_at, updated_at, task_type) VALUES "
               "('mine', ?, 'Summarize', '', 1, 2, 'normal')", (f"file:{run}",))
    db.execute("INSERT INTO messages VALUES ('x1', 'mine', 'user', ?, 1)", (json.dumps({"role": "user", "content": "x"}),))
    db.commit()
    db.close()
    cfg.bob_dirs = [home]
    conn = connect(cfg.db_path)
    sync(cfg, conn)
    ids = {r[0] for r in conn.execute("SELECT id FROM sessions WHERE agent = 'bob'")}
    assert BOB_TASK in ids and "mine" not in ids
    assert bob_status(cfg, conn)["on_disk"] == len([i for i in ids])  # Chronicle's own run isn't counted either
    conn.close()


def test_cli_set_key_bob(env):
    from interlatch.interfaces.cli.main import main

    assert main(["config", "set-key", "bob", "k-1"]) == 0
    assert key_source(load_config(env["cfg"].home), "bob") == ("k-1", "stored")
    assert main(["config", "set", "analysis.backend", "bob"]) == 0
    assert load_config(env["cfg"].home).analysis.backend == "bob"


def test_dashboard_sets_agent_models(env):
    from interlatch.interfaces.web.app import App

    app = App(env["cfg"])
    choices = {c["name"]: c for c in api.settings.analysis_backends(app.context())["choices"]}
    assert list(choices)[:3] == ["claude", "codex", "bob"]
    assert choices["claude"]["settings"] == {"model": "sonnet", "synthesis_model": "sonnet", "screen_model": "haiku",
                                            "effort": "medium"}
    out = api.settings.action_agent(app.context(), {"agent": "claude", "settings": {"model": "claude-opus-5-5", "synthesis_model": "opus",
                                                            "screen_model": "haiku", "effort": "high"}})
    assert "error" not in out
    cfg = load_config(env["cfg"].home)
    assert (cfg.analysis.model, cfg.synthesis.model, cfg.analysis.effort) == ("claude-opus-5-5", "opus", "high")
    assert make_runner(cfg).model_label() == "claude-opus-5-5"
    bad = api.settings.action_agent(app.context(), {"agent": "claude", "settings": {"model": "", "effort": "turbo", "claude_bin": "/tmp/x"}})
    assert bad["error"] == "not saved: model, effort, claude_bin"
    assert load_config(env["cfg"].home).analysis.model == "claude-opus-5-5"
    assert "error" not in api.settings.action_agent(app.context(), {"agent": "codex", "settings": {"model": "gpt-5.5", "effort": "max"}})
    assert load_config(env["cfg"].home).analysis.codex_model == "gpt-5.5"
    assert "error" not in api.settings.action_agent(app.context(), {"agent": "codex", "settings": {"model": ""}})  # Codex's own default
    assert load_config(env["cfg"].home).analysis.codex_model == ""
    assert "error" in api.settings.action_agent(app.context(), {"agent": "bob", "settings": {"model": "x"}})  # Bob picks its own model
    assert "error" in api.settings.action_agent(app.context(), {"agent": "claude", "settings": {"model": "sonnet\nbackend = 'x'"}})


def test_failed_lines_name_projects_not_cut_paths():
    """A worker run's error lines: `! /Users/a: …` named a knowledge base that failed only by its path's first 8
    characters."""
    from interlatch.interfaces.cli.common import failed_label

    assert failed_label("0eb2ccdb-58fc-420f-b7ac-cbdbabfe1f9a") == "0eb2ccdb"
    assert failed_label("/Users/a/Projects/agents-chronicle") == "agents-chronicle"
    assert failed_label("glossary:/Users/a/Projects/agents-chronicle/") == "glossary agents-chronicle"
    assert failed_label(r"C:\Users\a\Projects\app") == "app"
    assert failed_label("__global__") == "global playbook" and failed_label("themes:Tooling and CI") == "themes Tooling and CI"
    assert failed_label("2026-W41") == "2026-W41"
