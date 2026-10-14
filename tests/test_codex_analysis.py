"""Analysis through Codex (`analysis.backend = "codex"`): a fake `codex exec` wraps the fake `claude`."""

import json
import stat
import sys

import pytest

from interlatch.config import load_config, set_config_value
from interlatch.llm import ClaudeRunner, CodexRunner, LLMError, UsageLimitError, make_runner
from interlatch.worker import run_worker

from conftest import CWD, SID

# Answers like `codex exec --json`: the fake claude produces the reply text for the same system prompt
# (read from model_instructions_file), wrapped in Codex's JSONL events.
FAKE_CODEX = r'''#!{python}
import json, os, re, subprocess, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
with open(os.environ["FAKE_CODEX_LOG"], "a") as fh:
    fh.write(json.dumps({"args": args, "prompt_chars": len(prompt)}) + "\n")
path = next(json.loads(a.split("=", 1)[1]) for a in args if a.startswith("model_instructions_file="))
system = open(path).read()
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
def emit(**e):
    print(json.dumps(e), flush=True)
emit(type="thread.started", thread_id="t-1")
emit(type="turn.started")
emit(type="item.completed", item={"id": "item_0", "type": "error", "message": "Code Mode is unavailable because code-mode host is disabled."})
if mode == "limit":
    emit(type="turn.failed", error={"message": "You've hit your usage limit. Try again in 3 hours."})
    sys.exit(1)
if mode == "reconnect":
    emit(type="error", message="Reconnecting... 2/5 (stream disconnected before completion)")
if mode == "tool":
    emit(type="item.started", item={"id": "item_1", "type": "command_execution", "command": "cat ~/.ssh/id_ed25519"})
reply = subprocess.run([os.environ["FAKE_CLAUDE_BIN"], "-p", "--system-prompt", system], input=prompt,
                       capture_output=True, text=True, env={**os.environ, "FAKE_CLAUDE_LOG": ""})
text = json.loads(reply.stdout)["result"]
if mode == "prose" and "repair malformed JSON" not in system:
    text = "Sure! " + text[:-1]  # truncated, with a preamble: needs the repair pass
emit(type="item.completed", item={"id": "item_2", "type": "reasoning", "text": "thinking"})
emit(type="item.completed", item={"id": "item_3", "type": "agent_message", "text": text})
emit(type="turn.completed", usage={"input_tokens": 3794, "cached_input_tokens": 0, "output_tokens": 282, "reasoning_output_tokens": 82})
'''


@pytest.fixture()
def codex_env(synced, monkeypatch):
    fake = synced["tmp"] / "bin" / "codex"
    fake.write_text(FAKE_CODEX.replace("{python}", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(synced["tmp"] / "fake_codex.log"))
    monkeypatch.setenv("FAKE_CLAUDE_BIN", str(synced["fake"]))
    set_config_value(synced["cfg"], "analysis", "backend", '"codex"')
    set_config_value(synced["cfg"], "analysis", "codex_bin", json.dumps(str(fake)))
    synced["cfg"] = load_config(synced["cfg"].home)
    return synced


def codex_calls(env) -> list[dict]:
    path = env["tmp"] / "fake_codex.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_backend_picks_the_runner(env):
    assert isinstance(make_runner(env["cfg"]), ClaudeRunner)
    env["cfg"].analysis.backend = "codex"
    assert isinstance(make_runner(env["cfg"]), CodexRunner)
    env["cfg"].analysis.backend = "something-else"
    assert isinstance(make_runner(env["cfg"]), ClaudeRunner)  # an unknown value falls back to Claude


def test_codex_runs_isolated_with_no_tools(codex_env):
    cfg = codex_env["cfg"]
    res = CodexRunner(cfg).run("<transcript>hello</transcript>", {"type": "object"}, system="Summarize the session.",
                               model="sonnet", effort="max")
    assert res.data["title"] == "Fixed login token expiry bug"
    assert res.model == "codex" and res.cost_usd == 0.0 and (res.input_tokens, res.output_tokens) == (3794, 364)
    args = codex_calls(codex_env)[0]["args"]
    assert args[:2] == ["exec", "--ephemeral"] and args[-1] == "-"
    for flag in ("--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--json"):
        assert flag in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    configs = [args[i + 1] for i, a in enumerate(args) if a == "-c"]
    for c in ("features.shell_tool=false", "features.unified_exec=false", "features.multi_agent=false",
              "features.hooks=false", "features.plugins=false", 'web_search="disabled"', "project_doc_max_bytes=0",
              "skills.include_instructions=false", 'model_reasoning_effort="xhigh"'):
        assert c in configs
    assert "-m" not in args  # "sonnet" is a Claude model: Codex uses its own default
    instructions = next(json.loads(c.split("=", 1)[1]) for c in configs if c.startswith("model_instructions_file="))
    assert not __import__("os").path.exists(instructions)  # the system-prompt file is removed after the call


def test_codex_model_setting(codex_env):
    cfg = codex_env["cfg"]
    cfg.analysis.codex_model = "gpt-5.5-codex"
    runner = CodexRunner(cfg)
    assert runner.model_label("sonnet") == "gpt-5.5-codex" and runner.model_label("gpt-5.4") == "gpt-5.4"
    res = runner.run("x", {"type": "object"}, system="Summarize the session.", model="sonnet")
    args = codex_calls(codex_env)[-1]["args"]
    assert args[args.index("-m") + 1] == "gpt-5.5-codex" and res.model == "codex:gpt-5.5-codex"


def test_codex_reply_after_a_tool_call_is_discarded(codex_env, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "tool")
    with pytest.raises(LLMError, match="used a tool .command_execution.; the reply was discarded"):
        CodexRunner(codex_env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")


def test_codex_usage_limit_pauses(codex_env, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "limit")
    with pytest.raises(UsageLimitError, match="usage limit"):
        CodexRunner(codex_env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")


def test_codex_reconnect_notice_then_reply_succeeds(codex_env, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "reconnect")
    res = CodexRunner(codex_env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")
    assert res.data["title"] == "Fixed login token expiry bug"


def test_codex_unparseable_reply_gets_the_repair_pass(codex_env, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "prose")
    res = CodexRunner(codex_env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")
    assert res.data and len(codex_calls(codex_env)) == 2


def test_codex_missing_binary(env):
    env["cfg"].analysis.backend = "codex"
    env["cfg"].analysis.codex_bin = str(env["tmp"] / "nowhere" / "codex")
    runner = make_runner(env["cfg"])
    assert runner.available()  # an explicit path is trusted, like claude_bin; the call then fails
    with pytest.raises(LLMError):
        runner.run("x", {"type": "object"}, system="s")


def test_worker_end_to_end_with_codex(codex_env):
    cfg, conn = codex_env["cfg"], codex_env["conn"]
    report = run_worker(cfg)
    assert report.analyzed == [SID] and not report.failed and CWD in report.synthesized
    row = conn.execute("SELECT model, cost_usd FROM analyses WHERE kind = 'session' AND target = ?", (SID,)).fetchone()
    assert row["model"] == "codex" and row["cost_usd"] == 0
    assert conn.execute("SELECT COUNT(*) FROM knowledge WHERE session_id = ?", (SID,)).fetchone()[0] >= 1
    assert not (codex_env["tmp"] / "fake_claude.log").exists()  # Claude was never called
    assert codex_calls(codex_env)


def test_config_set_backend(env, capsys):
    from interlatch.cli import main

    assert main(["config", "set", "analysis.backend", "codex"]) == 0
    assert load_config(env["cfg"].home).analysis.backend == "codex"
    assert main(["config", "set", "analysis.backend", "gemini"]) == 2
    assert main(["config", "set", "analysis.max_per_run", "4"]) == 0
    cfg = load_config(env["cfg"].home)
    assert cfg.analysis.backend == "codex" and cfg.analysis.max_per_run == 4
    assert main(["config", "set", "analysis"]) == 2


def test_setup_offers_codex_when_claude_is_missing(env, monkeypatch):
    from rich.console import Console

    from interlatch.cli import _choose_analyzer

    cfg = env["cfg"]
    monkeypatch.setattr(type(cfg), "claude_bin", lambda self: None)
    monkeypatch.setattr(type(cfg), "codex_bin", lambda self: "/opt/bin/codex")
    asked = []
    runner = _choose_analyzer(cfg, Console(file=open("/dev/null", "w")), ask=lambda q, d: asked.append(q) or True, dry_run=False)
    assert runner.name == "codex" and "Analyze sessions with Codex instead" in asked[0]
    assert load_config(cfg.home).analysis.backend == "codex"


def test_dashboard_switches_the_backend(env, monkeypatch):
    from interlatch.server import App

    monkeypatch.setattr(type(env["cfg"]), "codex_bin", lambda self: "/opt/bin/codex")
    app = App(env["cfg"])
    status = app.status()
    assert status["analysis"]["backend"] == "claude" and status["analysis"]["auto"] is True
    assert [c["name"] for c in status["analysis"]["choices"]][:2] == ["claude", "codex"]
    assert app.action_backend("codex")["backend"] == "codex"
    assert load_config(env["cfg"].home).analysis.backend == "codex" and app.status_small()["analysis"]["label"] == "Codex"
    assert "error" in app.action_backend("gemini")
