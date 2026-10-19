"""Analysis through OpenCode (`analysis.backend = "opencode"`): a fake `opencode run` reads the agent Interlatch passed
in OPENCODE_CONFIG_CONTENT, answers with the fake claude's reply for that agent's instructions, prints OpenCode's JSON
events and saves the run as a session in its database, which `opencode session delete` removes. Also record only
(`analysis.backend = "none"`): sessions are recorded, and no model is ever called."""

import json
import sqlite3
import stat
import sys

import pytest

from interlatch.analysis.runners.base import NO_TOOLS, LLMError, UsageLimitError
from interlatch.analysis.runners.factory import BACKENDS, make_runner
from interlatch.analysis.runners.off import OffRunner
from interlatch.analysis.runners.opencode import OPENCODE_AGENT, OPENCODE_TITLE, OpenCodeRunner
from interlatch.application.workflows import run_worker
from interlatch.core.config import load_config, set_config_value
from interlatch.interfaces.web import api

from conftest import CWD, SID

FAKE_OPENCODE = r'''#!{python}
import json, os, re, sqlite3, subprocess, sys
args = sys.argv[1:]
db = sqlite3.connect(os.path.join(os.environ["OPENCODE_HOME"], "opencode.db"))
db.execute("CREATE TABLE IF NOT EXISTS session_v2 (id TEXT PRIMARY KEY, directory TEXT, cost REAL, tokens_input INT, "
           "tokens_output INT, tokens_reasoning INT, tokens_cache_read INT, tokens_cache_write INT, model TEXT)")
if args[:2] == ["session", "delete"]:
    db.execute("DELETE FROM session_v2 WHERE id = ?", (args[-1],))
    db.commit()
    with open(os.environ["FAKE_OPENCODE_LOG"], "a") as fh:
        fh.write(json.dumps({"deleted": args[-1], "args": args}) + "\n")
    sys.exit(0)
prompt = sys.stdin.read()
agent = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])["agent"][args[args.index("--agent") + 1]]
role = open(re.fullmatch(r"\{file:(.+)\}", agent["prompt"]).group(1), encoding="utf-8").read()
n = sum(1 for _ in open(os.environ["FAKE_OPENCODE_LOG"])) if os.path.exists(os.environ["FAKE_OPENCODE_LOG"]) else 0
sid = f"ses_{n}"
with open(os.environ["FAKE_OPENCODE_LOG"], "a") as fh:
    fh.write(json.dumps({"args": args, "prompt_chars": len(prompt), "permission": agent["permission"], "cwd": os.getcwd(),
                         "pwd": os.environ.get("PWD"), "internal": os.environ.get("INTERLATCH_INTERNAL"),
                         "role": role, "session": sid}) + "\n")
model = args[args.index("--model") + 1] if "--model" in args else "anthropic/claude-sonnet-5-5"
db.execute("INSERT INTO session_v2 VALUES (?, ?, 0.012, 1000, 200, 50, 300, 0, ?)",
           (sid, os.environ.get("PWD"), json.dumps({"providerID": model.split("/")[0], "id": model.split("/", 1)[1]})))
db.commit()
def emit(**e):
    print(json.dumps({"timestamp": 1792427815804, "sessionID": sid, **e}), flush=True)
how = os.environ.get("FAKE_OPENCODE_MODE", "ok")
if how == "auth":
    emit(type="error", error={"type": "provider.auth", "message": "Invalid API key", "status": 401})
    sys.exit(1)
emit(type="step_start", part={"id": "p0", "type": "step-start"})
if how == "tool":
    emit(type="tool_use", part={"id": "p1", "type": "tool", "tool": "bash", "state": {"input": {"command": "ls"}}})
reply = subprocess.run([os.environ["FAKE_CLAUDE_BIN"], "-p", "--system-prompt", role], input=prompt,
                       capture_output=True, text=True, env={**os.environ, "FAKE_CLAUDE_LOG": ""})
text = json.loads(reply.stdout)["result"]
emit(type="text", part={"id": "p2", "type": "text", "text": text[:20]})  # a part can come again as it grows
emit(type="text", part={"id": "p2", "type": "text", "text": text})
'''


@pytest.fixture()
def opencode_env(synced, monkeypatch):
    fake = synced["tmp"] / "bin" / "opencode"
    fake.write_text(FAKE_OPENCODE.replace("{python}", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (synced["tmp"] / "no-opencode").mkdir(exist_ok=True)
    monkeypatch.setenv("FAKE_OPENCODE_LOG", str(synced["tmp"] / "fake_opencode.log"))
    monkeypatch.setenv("FAKE_CLAUDE_BIN", str(synced["fake"]))
    set_config_value(synced["cfg"], "analysis", "backend", '"opencode"')
    set_config_value(synced["cfg"], "analysis", "opencode_bin", json.dumps(str(fake)))
    synced["cfg"] = load_config(synced["cfg"].home)
    return synced


def opencode_calls(env) -> list[dict]:
    path = env["tmp"] / "fake_opencode.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def sessions_left(env) -> list[str]:
    db = sqlite3.connect(env["tmp"] / "no-opencode" / "opencode.db")
    try:
        return [r[0] for r in db.execute("SELECT id FROM session_v2")]
    finally:
        db.close()


def test_opencode_runs_headless_with_no_tools(opencode_env):
    cfg = opencode_env["cfg"]
    runner = make_runner(cfg)
    assert isinstance(runner, OpenCodeRunner) and runner.available()
    res = runner.run("<transcript>hello</transcript>", {"type": "object"}, system="Summarize the session.", model="sonnet")
    assert res.data["title"] == "Fixed login token expiry bug"
    assert res.model == "opencode:anthropic/claude-sonnet-5-5" and res.cost_usd == pytest.approx(0.012)
    assert (res.input_tokens, res.output_tokens) == (1300, 250)  # cache reads count as input, reasoning as output
    call, deleted = opencode_calls(opencode_env)
    args = call["args"]
    assert args[:4] == ["run", "--standalone", "--format", "json"] and "--auto" not in args
    assert args[args.index("--agent") + 1] == OPENCODE_AGENT and args[args.index("--title") + 1] == OPENCODE_TITLE
    assert "--model" not in args  # a Claude alias is not an OpenCode model: OpenCode's default
    assert call["permission"] == {"*": "deny"} and call["internal"] == "1"
    assert call["role"].startswith("Summarize the session.") and call["role"].endswith(NO_TOOLS)
    workdir = cfg.home / "workdir" / "opencode"
    assert call["cwd"] == call["pwd"] == str(workdir) and cfg.is_internal_path(call["pwd"])  # OpenCode files it under $PWD
    assert deleted["deleted"] == call["session"] and "--standalone" in deleted["args"] and sessions_left(opencode_env) == []
    assert not list((cfg.home / "workdir").glob("opencode-system-*"))  # the instructions file is removed


def test_opencode_model_setting(opencode_env):
    cfg = opencode_env["cfg"]
    set_config_value(cfg, "analysis", "opencode_model", '"ollama/qwen3.8"')
    runner = make_runner(load_config(cfg.home))
    assert runner.model_label() == "ollama/qwen3.8" and runner.model_label("sonnet") == "ollama/qwen3.8"
    res = runner.run("x", {"type": "object"}, system="Summarize the session.")
    assert res.model == "opencode:ollama/qwen3.8"
    assert opencode_calls(opencode_env)[0]["args"][-2:] == ["--model", "ollama/qwen3.8"]


def test_opencode_reply_after_a_tool_call_is_discarded(opencode_env, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_MODE", "tool")
    with pytest.raises(LLMError, match=r"opencode used a tool \(bash\); the reply was discarded"):
        make_runner(opencode_env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")
    assert sessions_left(opencode_env) == []  # deleted all the same


def test_opencode_sign_in_problem_pauses(opencode_env, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_MODE", "auth")
    with pytest.raises(UsageLimitError, match="Invalid API key"):
        make_runner(opencode_env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")


def test_worker_end_to_end_with_opencode(opencode_env):
    cfg, conn = opencode_env["cfg"], opencode_env["conn"]
    report = run_worker(cfg)
    assert report.analyzed == [SID] and not report.failed and CWD in report.synthesized
    row = conn.execute("SELECT model, cost_usd FROM analyses WHERE kind = 'session' AND target = ?", (SID,)).fetchone()
    assert row["model"] == "opencode:anthropic/claude-sonnet-5-5" and row["cost_usd"] == pytest.approx(0.012)
    assert not (opencode_env["tmp"] / "fake_claude.log").exists() and sessions_left(opencode_env) == []


def test_opencode_not_installed(env, monkeypatch):
    monkeypatch.setattr("interlatch.core.config.shutil.which", lambda _name: None)
    monkeypatch.setattr("interlatch.core.config._installed_bin", lambda *_a: None)
    monkeypatch.setattr("interlatch.core.config._desktop_opencode", lambda: None)
    set_config_value(env["cfg"], "analysis", "backend", '"opencode"')
    runner = make_runner(load_config(env["cfg"].home))
    assert not runner.available() and runner.unavailable_reason() == "OpenCode (`opencode`) was not found"


def test_opencode_from_the_desktop_app(tmp_path, monkeypatch):
    """A Mac with only the OpenCode desktop app: its bundled CLI, the newest version."""
    from interlatch.core import config

    root = tmp_path / "Library" / "Application Support" / "ai.opencode.desktop" / "cli"
    for v in ("2.0.9-arm64", "2.0.29-arm64", "1.9.0-arm64"):
        cli = root / v / "opencode-cli"
        cli.parent.mkdir(parents=True)
        cli.write_text("#!/bin/sh\n")
        cli.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(config.sys, "platform", "darwin")
    assert config._desktop_opencode() == str(root / "2.0.29-arm64" / "opencode-cli")
    monkeypatch.setattr(config.sys, "platform", "linux")
    assert config._desktop_opencode() is None


def test_dashboard_sets_the_opencode_model(env):
    from interlatch.interfaces.web.app import App

    app = App(env["cfg"])
    choices = {c["name"]: c for c in api.settings.analysis_backends(app.context())["choices"]}
    assert choices["opencode"]["kind"] == "agent" and choices["opencode"]["settings"] == {"model": ""}
    ok = api.settings.action_agent(app.context(), {"agent": "opencode", "settings": {"model": "ollama/qwen3.8"}})
    assert "error" not in ok and load_config(env["cfg"].home).analysis.opencode_model == "ollama/qwen3.8"
    assert "error" not in api.settings.action_agent(app.context(), {"agent": "opencode", "settings": {"model": ""}})
    assert load_config(env["cfg"].home).analysis.opencode_model == ""
    for bad in ("sonnet", "ollama/qwen 3", "/"):  # not provider/model
        assert "error" in api.settings.action_agent(app.context(), {"agent": "opencode", "settings": {"model": bad}})
    assert "error" in api.settings.action_agent(app.context(), {"agent": "opencode", "settings": {"effort": "high"}})


# ------------------------------------------------------------------ record only
@pytest.fixture()
def record_only(synced):
    set_config_value(synced["cfg"], "analysis", "backend", '"none"')
    synced["cfg"] = load_config(synced["cfg"].home)
    return synced


def test_record_only_never_calls_a_model(record_only):
    cfg, conn = record_only["cfg"], record_only["conn"]
    runner = make_runner(cfg)
    assert isinstance(runner, OffRunner) and not runner.available() and runner.local()
    with pytest.raises(LLMError, match=r"record only"):
        runner.run("x", {"type": "object"}, system="Summarize the session.")
    report = run_worker(cfg, force=True)  # force: knowledge bases and reviews too
    assert not report.analyzed and not report.synthesized and not report.failed
    assert not (record_only["tmp"] / "fake_claude.log").exists()
    status = conn.execute("SELECT analysis_status FROM sessions WHERE id = ?", (SID,)).fetchone()[0]
    assert status == "pending"  # recorded, waiting for an analyzer


def test_record_only_says_why_the_queue_waits(record_only):
    from interlatch.analysis.queue import count_pending

    block = count_pending(record_only["conn"], record_only["cfg"])["block"]
    assert block == "analysis is off (record only): pick an analyzer in Status › Analysis to analyze sessions"


def test_record_only_from_the_cli(record_only, capsys):
    from interlatch.interfaces.cli.main import main

    assert main(["analyze", SID]) == 1
    assert "analysis is off (record only)" in capsys.readouterr().err
    assert not (record_only["tmp"] / "fake_claude.log").exists()
    assert main(["analyze", SID, "--backend", "claude"]) == 0  # one run with an analyzer, analysis stays off
    assert (record_only["tmp"] / "fake_claude.log").exists()
    assert load_config(record_only["cfg"].home).analysis.backend == "none"
    assert main(["config", "set", "analysis.backend", "none"]) == 0
    assert "never analyzed" in capsys.readouterr().out


def test_install_no_analysis(env):
    from interlatch.interfaces.cli.commands.setup import ANALYZER_ASKED_KEY, _choose_analyzer
    from interlatch.interfaces.cli.parser import build_parser
    from interlatch.storage.kv import kv_get
    from interlatch.storage.sqlite import connect

    assert build_parser().parse_args(["install", "--no-analysis"]).backend == "none"
    assert build_parser().parse_args(["install", "--backend", "none"]).backend == "none"
    assert BACKENDS["none"] == "Record only"
    printed = []

    class Console:
        def print(self, text, **_kw):
            printed.append(text)

    conn = connect(env["cfg"].db_path)
    asked = []
    ask = lambda q, default: asked.append(q) or default  # noqa: E731
    assert _choose_analyzer(env["cfg"], Console(), ask=ask, dry_run=False, conn=conn, interactive=True,
                            choice="none") is None
    assert load_config(env["cfg"].home).analysis.backend == "none" and kv_get(conn, ANALYZER_ASKED_KEY)
    assert printed[-1].startswith("Record only: sessions are recorded but not analyzed")
    # a later install keeps it: no list, no offer to switch to another agent
    assert _choose_analyzer(load_config(env["cfg"].home), Console(), ask=ask, dry_run=False, conn=conn,
                            interactive=True) is None
    assert not asked and load_config(env["cfg"].home).analysis.backend == "none"
    conn.close()


def test_dashboard_turns_analysis_off(env):
    from interlatch.interfaces.web.app import App

    app = App(env["cfg"])
    choices = {c["name"]: c for c in api.settings.analysis_backends(app.context())["choices"]}
    assert choices["none"]["kind"] == "off" and not choices["none"]["available"]
    out = api.settings.action_backend(app.context(), "none")
    assert out["backend"] == "none" and load_config(env["cfg"].home).analysis.backend == "none"
    assert api.settings.action_provider_test(app.context(), "none")["error"].startswith("Analysis is off")
    assert api.settings.action_backend(app.context(), "claude")["backend"] == "claude"  # back on


def test_install_asks_the_opencode_model(opencode_env, monkeypatch):
    """Picking OpenCode at install asks for a provider/model (a bad one again) and offers one test call."""
    from interlatch.interfaces.cli.commands import setup

    answers = iter(["4", "sonnet", "ollama/qwen3.8"])
    monkeypatch.setattr(setup, "_input", lambda _q: next(answers))
    asked = []

    class Console:
        def print(self, *_a, **_kw):
            pass

    cfg = opencode_env["cfg"]
    set_config_value(cfg, "analysis", "backend", '"claude"')
    picked = setup._pick_analyzer(load_config(cfg.home), Console(), ask=lambda q, d: asked.append(q) or d)
    saved = load_config(cfg.home).analysis
    assert picked.name == "opencode" and (saved.backend, saved.opencode_model) == ("opencode", "ollama/qwen3.8")
    assert asked == ["  Check that it works now? (one small call to OpenCode, ollama/qwen3.8)"]
    assert opencode_calls(opencode_env)[0]["args"][-2:] == ["--model", "ollama/qwen3.8"]
