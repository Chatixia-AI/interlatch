"""`interlatch status` (`doctor`): a line per agent found here, with whether Interlatch's MCP server (and Claude Code's
hooks) are in it and whether the command each one runs is still there and is this install."""

import json
import sys

import pytest

from interlatch.interfaces.cli.main import main
from interlatch.runtime import connectors
from interlatch.runtime.connectors import agents_doctor, command_problem, connect
from interlatch.runtime.install import install_hooks
from interlatch.storage.sqlite import connect as open_db


def _program(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


@pytest.fixture()
def machine(env, monkeypatch):
    """Claude Code (env's tree), Codex and Cursor installed; Windsurf, Gemini CLI, Copilot, Bob, Antigravity and
    OpenCode not. This install's command is bin/interlatch, reached through a link the way ~/.local/bin's is."""
    home = env["tmp"] / "home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    (home / ".cursor").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setattr(connectors, "APPLICATIONS", env["tmp"] / "Applications")
    monkeypatch.setattr(connectors.shutil, "which", lambda name: None)
    monkeypatch.setattr("interlatch.core.config.Config.codex_bin", lambda self: None)
    monkeypatch.setitem(connectors.CONNECTORS, "bob",  # Bob's status also looks in the real /Applications
                        (lambda cfg, conn: {"name": "bob", "label": "IBM Bob", "detected": False, "checks": []}, None, None))
    current = _program(env["tmp"] / "tool" / "bin" / "interlatch")
    link = home / ".local" / "bin" / "interlatch"
    link.parent.mkdir(parents=True)
    link.symlink_to(current)
    env.update(home_dir=home, current=str(link), conn=open_db(env["cfg"].db_path))
    yield env
    env["conn"].close()


def _register(machine, *, codex: str, cursor: str):
    home = machine["home_dir"]
    (home / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"interlatch": {"type": "stdio", "command": machine["current"], "args": ["mcp"]}}}))
    install_hooks(machine["cfg"], machine["current"], inject=False)
    (home / ".codex" / "config.toml").write_text(f'[mcp_servers.chronicle]\ncommand = "{codex}"\nargs = ["mcp"]\n')
    (home / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {"interlatch": {"command": cursor, "args": ["mcp"]}}}))


@pytest.mark.skipif(sys.platform == "win32", reason="links and sh programs")
def test_a_row_per_agent_found_with_stale_commands(machine):
    other = _program(machine["tmp"] / "checkout" / ".venv" / "bin" / "interlatch")
    _register(machine, codex=str(machine["home_dir"] / ".local" / "bin" / "chronicle"), cursor=str(other))
    rows = {r["name"]: r for r in agents_doctor(machine["cfg"], machine["conn"], machine["current"])}

    assert list(rows) == ["claude", "codex", "cursor"]  # Windsurf, Gemini CLI, Copilot, ... are not installed: no row
    assert rows["claude"]["mcp"] and rows["claude"]["problems"] == []  # through the link: this install
    assert rows["claude"]["config"] == "~/.claude.json"
    assert rows["codex"]["mcp"] and rows["codex"]["problems"] == [  # Chronicle's server, its command gone
        {"what": "mcp", "problem": "missing", "command": "~/.local/bin/chronicle"}]
    assert rows["codex"]["config"] == "~/.codex/config.toml"
    assert rows["cursor"]["optional"] and rows["cursor"]["problems"] == [
        {"what": "mcp", "problem": "other", "command": str(other)}]


@pytest.mark.skipif(sys.platform == "win32", reason="links and sh programs")
def test_a_hook_whose_command_is_gone(machine):
    gone = machine["tmp"] / "old" / "bin" / "chronicle"
    install_hooks(machine["cfg"], str(gone), inject=True)  # SessionEnd and SessionStart: said once
    rows = {r["name"]: r for r in agents_doctor(machine["cfg"], machine["conn"], machine["current"])}
    assert not rows["claude"]["mcp"]
    assert rows["claude"]["problems"] == [{"what": "hook", "problem": "missing", "command": str(gone)}]
    assert not rows["codex"]["mcp"] and rows["codex"]["problems"] == []


@pytest.mark.skipif(sys.platform == "win32", reason="links and sh programs")
def test_status_prints_one_line_per_agent(machine, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr("interlatch.runtime.install.executable", lambda: machine["current"])
    _register(machine, codex=str(machine["home_dir"] / ".local" / "bin" / "chronicle"), cursor=machine["current"])
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    lines = out[out.index("  agents:\n"):].splitlines()[1:4]
    assert lines == [
        "    ✓ Claude Code  MCP server + SessionEnd hook · ~/.claude.json",
        "    ✗ Codex        MCP server runs ~/.local/bin/chronicle, which is gone (`interlatch connect codex` fixes it) · "
        "~/.codex/config.toml",
        "    ✓ Cursor       MCP server · ~/.cursor/mcp.json",
    ]
    assert "Windsurf" not in out and "Gemini CLI" not in out

    (machine["home_dir"] / ".cursor" / "mcp.json").write_text("{}")
    (machine["home_dir"] / ".codex" / "config.toml").write_text("")
    main(["doctor"])
    out = capsys.readouterr().out
    assert "    ✗ Codex        no MCP server (`interlatch connect codex` adds it) · ~/.codex/config.toml" in out
    assert "    – Cursor       no MCP server (optional: `interlatch connect cursor`) · ~/.cursor/mcp.json" in out


def test_command_problem(tmp_path):
    exe = _program(tmp_path / "bin" / "interlatch")
    assert command_problem([str(exe), "mcp"], str(exe)) is None
    assert command_problem([str(tmp_path / "gone"), "mcp"], str(exe)) == "missing"
    python = f"{sys.executable} -m interlatch"
    assert command_problem([sys.executable, "-m", "interlatch", "mcp"], python) is None
    assert command_problem([sys.executable, "-m", "interlatch"], python) is None  # a hook's part before `hook`
    assert command_problem([sys.executable, "-m", "chronicle", "mcp"], python) == "other"
    assert command_problem([str(exe), "mcp"], python) == "other"


def test_connect_codex_replaces_a_registration_for_another_install(env, monkeypatch, tmp_path):
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr("interlatch.core.config.Config.codex_bin", lambda self: "/opt/bin/codex")
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(connectors, "run_program", lambda cmd, **kw: calls.append(cmd) or Done())
    exe = _program(tmp_path / "bin" / "interlatch")
    (home / "config.toml").write_text('[mcp_servers.chronicle]\ncommand = "/gone/chronicle"\nargs = ["mcp"]\n')
    connect(env["cfg"], "codex", str(exe))
    assert calls == [["/opt/bin/codex", "mcp", "remove", "chronicle"],
                     ["/opt/bin/codex", "mcp", "add", "interlatch", "--", str(exe), "mcp"]]

    calls.clear()
    (home / "config.toml").write_text(f'[mcp_servers.interlatch]\ncommand = "{exe}"\nargs = ["mcp"]\n')
    connect(env["cfg"], "codex", str(exe))
    assert calls == []  # already this install's: left alone
