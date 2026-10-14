"""Chronicle -> Interlatch: the environment variables' old names, and `interlatch migrate` (migrate.py) moving
~/.claude-chronicle to ~/.interlatch and pointing hooks, MCP servers, permissions, instruction files and the background
agents at the new names. HOME is a scratch folder here (conftest.no_real_home); launchctl and systemctl never run."""

from __future__ import annotations

import json
import os
import plistlib
import sqlite3
import stat
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

from interlatch import install, migrate
from interlatch.cli import main
from interlatch.config import interlatch_home, default_home, env, legacy_home
from interlatch.db import connect

EXE = "/opt/bin/interlatch"


# ---------------------------------------------------------------- the variables
def test_interlatch_variables_win_and_chronicle_ones_still_count(monkeypatch):
    assert env("PORT") is None and env("PORT", "1") == "1"
    monkeypatch.setenv("CHRONICLE_PORT", "9000")
    assert env("PORT") == "9000"  # the name before the rename
    monkeypatch.setenv("INTERLATCH_PORT", "9001")
    assert env("PORT") == "9001"
    monkeypatch.setenv("INTERLATCH_PORT", "")  # empty counts as unset (a compose file passing it on empty)
    assert env("PORT") == "9000"
    assert env("PORT", environ={"CHRONICLE_PORT": "1", "INTERLATCH_PORT": "2"}) == "2"


def test_the_home_folder(tmp_path, monkeypatch):
    home = Path(os.environ["HOME"])
    assert interlatch_home() == default_home() == home / ".interlatch"  # a new install
    legacy_home().mkdir()
    assert interlatch_home() == home / ".claude-chronicle"  # not moved yet: the archive is still there
    monkeypatch.setenv("CHRONICLE_HOME", str(tmp_path / "custom"))
    assert interlatch_home() == tmp_path / "custom"
    monkeypatch.setenv("INTERLATCH_HOME", str(tmp_path / "newer"))
    assert interlatch_home() == tmp_path / "newer"
    # what every launchd agent written before the rename sets: the default folder, wherever it is now
    monkeypatch.delenv("INTERLATCH_HOME")
    monkeypatch.setenv("CHRONICLE_HOME", str(legacy_home()) + "/")
    assert interlatch_home() == legacy_home()
    default_home().mkdir()
    assert interlatch_home() == default_home()


def test_internal_runs_are_not_recorded_under_either_name(env, monkeypatch):
    from interlatch import hooks

    monkeypatch.setattr(hooks, "spawn_detached", lambda *a, **k: pytest.fail("recorded an internal run"))
    for name in ("INTERLATCH_INTERNAL", "CHRONICLE_INTERNAL"):
        monkeypatch.setenv(name, "1")
        assert hooks.hook_main("session-end") == 0
        monkeypatch.delenv(name)
    assert hooks.internal_env()["INTERLATCH_INTERNAL"] == "1"


def test_hooks_of_either_name_are_ours():
    ours = ["/Users/a/.local/bin/interlatch hook session-end", "/Users/a/.local/bin/chronicle hook session-end",
            "/Users/a/.claude-chronicle/bin/chronicle hook session-start", "/Users/a/.interlatch/bin/interlatch hook stop",
            "'/usr/bin/python3' -m chronicle hook session-end"]
    assert all(install._is_ours({"command": c}) for c in ours)
    assert not any(install._is_ours({"command": c}) for c in ["interlatch statusline", "other hook session-end",
                                                               "/x/chronicle-notes.sh"])


def test_bob_analyses_from_before_the_move_are_still_internal(env):
    assert env["cfg"].is_internal_path(str(legacy_home() / "workdir" / "bob" / "x"))
    assert not env["cfg"].is_internal_path(str(legacy_home().parent / "Projects" / "app"))


# ---------------------------------------------------------------- an install from before the rename
FAKE_CLAUDE = """#!{python}
# `claude mcp add|remove`, as far as ~/.claude.json goes
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
with open({log!r}, "a") as fh:
    fh.write(" ".join(args) + "\\n")
path = Path(os.environ["HOME"]) / ".claude.json"
data = json.loads(path.read_text()) if path.exists() else {{}}
servers = data.setdefault("mcpServers", {{}})
if args[:2] == ["mcp", "remove"]:
    servers.pop(args[-1], None)
elif args[:2] == ["mcp", "add"]:
    i = args.index("--")
    servers[args[i - 1]] = {{"command": args[i + 1], "args": args[i + 2:]}}
path.write_text(json.dumps(data))
"""


@pytest.fixture()
def legacy(tmp_path, monkeypatch):
    """~/.claude-chronicle as Chronicle left it: config, an archive with a session, Claude Code connected (hooks, MCP,
    permissions, a block in CLAUDE.md), Codex, Copilot, Bob, Antigravity and Cursor with the 'chronicle' server, and
    both launchd agents."""
    user = Path(os.environ["HOME"])
    old = user / ".claude-chronicle"
    claude = user / ".claude"
    claude.mkdir(parents=True)
    log = tmp_path / "claude.log"
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text(FAKE_CLAUDE.format(python=sys.executable, log=str(log)))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    old.mkdir()
    (old / "config.toml").write_text(textwrap.dedent(f"""\
        [sources]
        claude_dirs = ["{claude}"]
        [analysis]
        claude_bin = "{fake}"
        """))
    project = user / "Projects" / "app"
    (project / ".claude").mkdir(parents=True)
    conn = connect(old / "chronicle.db")
    conn.execute("INSERT INTO sessions(id, agent, project_path, archive_path, transcript_path) VALUES (?, ?, ?, ?, ?)",
                 ("s1", "claude", str(project), f"{old}/archive/claude/projects/x/s1.jsonl.gz",
                  str(claude / "projects" / "x" / "s1.jsonl")))
    conn.execute("INSERT INTO files_state(path, archive_path) VALUES (?, ?)",
                 (f"{old}/machines/m1/claude/projects/p/a.jsonl", f"{old}/archive/machines/m1/claude/a.jsonl.gz"))
    conn.execute("INSERT INTO suggestions(key, kind, origin, title, text, target_path) VALUES (?, ?, ?, ?, ?, ?)",
                 ("friction:zsh:claude:p", "instruction", "friction", "t", "x", str(project / "CLAUDE.md")))
    conn.commit()
    conn.close()

    shim = f"{user}/.local/bin/chronicle"
    (claude / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["Bash(git status)", "mcp__chronicle__search_sessions", "mcp__chronicle",
                                  "mcp__interlatch__get_session", "mcp__chronicle__get_session"],
                        "deny": ["mcp__chronicle__forget"]},
        "hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": f"{shim} hook session-end", "timeout": 15}]}],
                  "Stop": [{"hooks": [{"type": "command", "command": "afplay done.aiff"}]}]},
        "statusLine": {"type": "command", "command": f"{shim} statusline"},
    }, indent=2))
    (project / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"allow": ["mcp__chronicle__*"]}}))
    (project / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["mcp__chronicle__x"]}}))
    (user / ".claude.json").write_text(json.dumps({"mcpServers": {"chronicle": {"command": shim, "args": ["mcp"]}}}))
    block = ("# Mine\n\n- my own line <!-- chronicle:keep-me -->\n\n<!-- BEGIN chronicle -->\n"
             "- Quote globs. <!-- chronicle:friction:zsh-nomatch -->\n<!-- END chronicle -->\n")
    (claude / "CLAUDE.md").write_text(block)
    (project / "CLAUDE.md").write_text(block.replace("\n", "\r\n"))

    codex = user / ".codex"
    codex.mkdir()
    (codex / "config.toml").write_text(textwrap.dedent(f"""\
        model = "gpt-5"  # mine

        [mcp_servers.chronicle]
        command = "{shim}"
        args = ["mcp"]
        startup_timeout_sec = 30

        [mcp_servers.chronicle.env]
        FOO = "bar"

        [mcp_servers.other]
        command = "other"
        """))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    copilot, bob, agy = user / ".copilot", user / ".bob", user / ".gemini" / "antigravity"
    monkeypatch.setenv("COPILOT_HOME", str(copilot))
    monkeypatch.setenv("BOB_HOME", str(bob))
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(agy))
    vscode = user / "Library" / "Application Support" / "Code" / "User"
    monkeypatch.setattr("interlatch.connectors.vscode_user_dirs", lambda: [vscode])
    entry = {"command": shim, "args": ["mcp"]}
    configs = {
        "vscode": (vscode / "mcp.json", "servers", {"first": {}, "chronicle": {"type": "stdio", **entry}, "last": {}}),
        "copilot": (copilot / "mcp-config.json", "mcpServers", {"chronicle": {"type": "local", **entry, "tools": ["*"]}}),
        "bob": (bob / "settings" / "mcp.json", "mcpServers",
                {"chronicle": {**entry, "disabled": False, "alwaysAllow": ["search_sessions"]}}),
        "agy": (agy.parent / "config" / "mcp_config.json", "mcpServers", {"chronicle": entry}),
        "cursor": (user / ".cursor" / "mcp.json", "mcpServers", {"chronicle": entry, "other": {"command": "x"}}),
    }
    for path, key, servers in configs.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"keep": 1, key: servers}))

    for label in install.LEGACY_LABELS.values():
        with open(install.plist_path(label), "wb") as fh:
            plistlib.dump({"Label": label, "ProgramArguments": [shim, "ui"], "StartInterval": 600,
                           "EnvironmentVariables": {"CHRONICLE_HOME": str(old)}}, fh)
    monkeypatch.setattr(install, "executable", lambda: EXE)
    monkeypatch.setattr("interlatch.install.platform.system", lambda: "Darwin")
    monkeypatch.setattr(install, "uses_systemd", lambda: False)
    monkeypatch.setattr(migrate, "APP_DIRS", (tmp_path / "Applications",))
    launchctl = []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def run(cmd, *a, **k):
        if isinstance(cmd, list) and cmd and cmd[0] == "launchctl":
            launchctl.append(("run", *cmd[1:]))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *a, **k)

    class Popen(real_popen):
        def __init__(self, cmd, *a, **k):
            if isinstance(cmd, list) and cmd and cmd[0] == "launchctl":
                launchctl.append(("detached", *cmd[1:]))
                cmd = ["true"]
            super().__init__(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", Popen)
    return {"user": user, "old": old, "new": user / ".interlatch", "claude": claude, "project": project,
            "codex": codex, "configs": configs, "claude_log": log, "launchctl": launchctl, "shim": shim}


def _snapshot(root: Path) -> dict:
    """Every file below `root` (SQLite's own -wal and -shm files aside: reading the database touches them)."""
    return {str(p): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and not p.is_symlink() and not p.name.endswith(("-wal", "-shm"))}


def test_the_move_and_every_integration(legacy):
    old, new, claude, project = legacy["old"], legacy["new"], legacy["claude"], legacy["project"]
    assert migrate.due()
    conn = sqlite3.connect(old / "chronicle.db")  # a dashboard that has the database open while it moves
    lines = migrate.migrate(auto=True)
    text = "\n".join(lines)

    # the folder: one rename, a link back, the database's own paths, a record
    assert new.is_dir() and not new.is_symlink() and old.is_symlink() and os.readlink(old) == ".interlatch"
    assert (old / "config.toml").read_text() == (new / "config.toml").read_text()
    conn.execute("INSERT INTO kv(key, value) VALUES ('written', 'after the move')")
    conn.commit()
    conn.close()
    db = sqlite3.connect(new / "chronicle.db")
    assert db.execute("SELECT value FROM kv WHERE key = 'written'").fetchone() == ("after the move",)
    assert db.execute("SELECT archive_path, transcript_path FROM sessions").fetchone() == (
        f"{new}/archive/claude/projects/x/s1.jsonl.gz", str(claude / "projects" / "x" / "s1.jsonl"))
    assert db.execute("SELECT path, archive_path FROM files_state").fetchall() == [
        (f"{new}/machines/m1/claude/projects/p/a.jsonl", f"{new}/archive/machines/m1/claude/a.jsonl.gz")]
    db.close()
    assert json.loads((new / migrate.MARKER).read_text())["done"] is True and not migrate.due()
    assert "moved" in (new / "logs" / "migrate.log").read_text()

    # Claude Code: hooks and status line run Interlatch, other hooks stay; MCP re-registered; permissions renamed
    settings = json.loads((claude / "settings.json").read_text())
    assert settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"] == f"{EXE} hook session-end"
    assert settings["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "afplay done.aiff"}]}]
    assert settings["statusLine"]["command"] == f"{EXE} statusline"
    assert settings["permissions"] == {"allow": ["Bash(git status)", "mcp__interlatch__search_sessions",
                                                 "mcp__interlatch", "mcp__interlatch__get_session"],
                                       "deny": ["mcp__interlatch__forget"]}
    assert json.loads((project / ".claude" / "settings.local.json").read_text()) == {
        "permissions": {"allow": ["mcp__interlatch__*"]}}
    assert "mcp__chronicle__x" in (project / ".claude" / "settings.json").read_text()  # shared: reported only
    assert "shared with the project" in text
    calls = legacy["claude_log"].read_text().splitlines()
    assert "mcp remove --scope user chronicle" in calls
    assert f"mcp add --scope user --transport stdio interlatch -- {EXE} mcp" in calls

    # CLAUDE.md / AGENTS.md: the block's markers, nothing else (the user's own line keeps its comment)
    assert (claude / "CLAUDE.md").read_text() == (
        "# Mine\n\n- my own line <!-- chronicle:keep-me -->\n\n<!-- BEGIN interlatch -->\n"
        "- Quote globs. <!-- interlatch:friction:zsh-nomatch -->\n<!-- END interlatch -->\n")
    assert (project / "CLAUDE.md").read_bytes().count(b"\r\n") == 7
    assert b"<!-- END interlatch -->" in (project / "CLAUDE.md").read_bytes()

    # Codex: the tables renamed, its settings and the file's layout kept, the command this install's
    codex_text = (legacy["codex"] / "config.toml").read_text()
    assert 'model = "gpt-5"  # mine' in codex_text and "[mcp_servers.interlatch.env]" in codex_text
    servers = tomllib.loads(codex_text)["mcp_servers"]
    assert set(servers) == {"interlatch", "other"}
    assert servers["interlatch"] == {"command": EXE, "args": ["mcp"], "startup_timeout_sec": 30, "env": {"FOO": "bar"}}

    # JSON MCP configs: renamed in place, the old entry's own settings kept, the rest of the file untouched
    for name, (path, key, before) in legacy["configs"].items():
        data = json.loads(path.read_text())
        assert data["keep"] == 1 and "chronicle" not in data[key], name
        assert list(data[key]) == [("interlatch" if k == "chronicle" else k) for k in before], name
        assert data[key]["interlatch"] == {**before["chronicle"], "command": EXE, "args": ["mcp"]}, name

    # launchd: Interlatch's agents run this install with the new home; Chronicle's are gone
    for label in (install.LAUNCHD_LABEL, install.UI_LABEL):
        with open(install.plist_path(label), "rb") as fh:
            plist = plistlib.load(fh)
        assert plist["ProgramArguments"][0] == EXE and plist["EnvironmentVariables"]["INTERLATCH_HOME"] == str(new)
    with open(install.plist_path(install.LAUNCHD_LABEL), "rb") as fh:
        assert plistlib.load(fh)["StartInterval"] == 600  # the interval it had
    assert not any(install.plist_path(label).exists() for label in install.LEGACY_LABELS.values())
    uid = os.getuid()
    assert ("run", "bootout", f"gui/{uid}/com.claude-chronicle.sync") in legacy["launchctl"]
    assert ("run", "bootout", f"gui/{uid}/com.claude-chronicle.ui") in legacy["launchctl"]


def test_running_it_again_changes_nothing(legacy):
    migrate.migrate(auto=True)
    user = legacy["user"]
    before = {**_snapshot(user / ".claude"), **_snapshot(user / ".codex"), **_snapshot(user / ".cursor"),
              **_snapshot(legacy["project"]), **_snapshot(install.plist_path(install.UI_LABEL).parent)}
    before = {k: v for k, v in before.items() if "/backups/" not in k}
    legacy["claude_log"].unlink()
    again = migrate.migrate()  # by hand it only reminds of what is left to do by hand
    assert again and all("(shared with the project) still allows" in line for line in again)
    assert migrate.migrate_if_due() == []
    after = {**_snapshot(user / ".claude"), **_snapshot(user / ".codex"), **_snapshot(user / ".cursor"),
             **_snapshot(legacy["project"]), **_snapshot(install.plist_path(install.UI_LABEL).parent)}
    assert {k: v for k, v in after.items() if "/backups/" not in k} == before
    assert not legacy["claude_log"].exists()  # Claude Code's MCP list was not touched again


def test_a_dry_run_changes_nothing(legacy, capsys):
    user = legacy["user"]
    before = _snapshot(user)
    assert main(["migrate", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"would move {legacy['old']} to {legacy['new']}" in out
    assert "would register the MCP server 'interlatch'" in out and "would rename the MCP server" in out
    assert "would replace Chronicle's background agent com.claude-chronicle.ui" in out
    assert _snapshot(user) == before and not legacy["new"].exists() and not legacy["old"].is_symlink()
    assert not legacy["claude_log"].exists() and legacy["launchctl"] == []


def test_a_folder_set_by_a_variable_is_never_moved(legacy, tmp_path, monkeypatch):
    for name in ("INTERLATCH_HOME", "CHRONICLE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / "elsewhere"))
        assert not migrate.due()
        assert migrate.migrate_if_due() == []
        assert legacy["old"].is_dir() and not legacy["old"].is_symlink() and not legacy["new"].exists()
        monkeypatch.delenv(name)
    # by hand it says so, and still points the integrations at the new names
    monkeypatch.setenv("INTERLATCH_HOME", str(legacy["old"]) + "x")
    lines = migrate.migrate()
    assert any("stays where it is" in line for line in lines) and not legacy["new"].exists()
    assert "interlatch" in (legacy["codex"] / "config.toml").read_text()


def test_chronicles_own_launchd_agent_naming_the_default_folder_moves_it(legacy, monkeypatch):
    monkeypatch.setenv("CHRONICLE_HOME", str(legacy["old"]))  # what Chronicle's launchd agents set
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.claude-chronicle.ui")  # and this process is the dashboard agent
    assert migrate.due()
    lines = migrate.migrate_if_due()
    assert legacy["new"].is_dir() and interlatch_home() == legacy["new"]
    # its own agent goes last, detached (stopping it stops this process), after the move is recorded as done
    uid = os.getuid()
    assert legacy["launchctl"][-1] == ("detached", "bootout", f"gui/{uid}/com.claude-chronicle.ui")
    assert ("run", "bootout", f"gui/{uid}/com.claude-chronicle.sync") in legacy["launchctl"]
    assert "removed Chronicle's background agent com.claude-chronicle.ui (the one doing this)" in lines
    assert json.loads((legacy["new"] / migrate.MARKER).read_text())["done"] is True


def test_a_move_that_did_not_finish_is_taken_up_again(legacy, monkeypatch):
    def stopped(*a, **k):
        raise KeyboardInterrupt  # the process was stopped after the folder moved

    monkeypatch.setattr(migrate, "_hooks", stopped)
    with pytest.raises(KeyboardInterrupt):
        migrate.migrate(auto=True)
    assert legacy["new"].is_dir() and migrate.due()
    monkeypatch.undo()  # HOME and the rest go back too: set them again
    monkeypatch.setenv("HOME", str(legacy["user"]))
    monkeypatch.setattr(install, "executable", lambda: EXE)
    monkeypatch.setattr(install, "uses_systemd", lambda: False)
    monkeypatch.setattr("interlatch.install.platform.system", lambda: "Darwin")
    monkeypatch.setenv("CODEX_HOME", str(legacy["codex"]))
    migrate.migrate_if_due()
    assert not migrate.due()
    assert f"{EXE} hook session-end" in (legacy["claude"] / "settings.json").read_text()


def test_the_commands_that_start_the_dashboard_or_a_sync_migrate_first(env, monkeypatch):
    calls = []
    monkeypatch.setattr("interlatch.migrate.migrate_if_due", lambda quiet=True: calls.append(quiet) or [])
    monkeypatch.setattr("interlatch.server.serve", lambda *a, **k: None)
    assert main(["sync", "--quiet"]) == 0 and main(["ui"]) == 0
    assert calls == [True, False]


def test_both_folders_there_leaves_both_alone(legacy):
    legacy["new"].mkdir()
    assert not migrate.due()
    lines = migrate.migrate()
    assert any("both exist" in line for line in lines)
    assert legacy["old"].is_dir() and not legacy["old"].is_symlink()


def test_chronicle_app_is_reported_not_deleted(legacy, tmp_path):
    app = tmp_path / "Applications" / "Chronicle.app"
    (app / "Contents").mkdir(parents=True)
    with open(app / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump({"CFBundleIdentifier": install.LEGACY_BUNDLE_ID}, fh)
    lines = migrate.migrate()
    assert f"{app} can be deleted: Interlatch replaces it (its Open at Login item goes with it)" in lines
    assert app.is_dir()


def test_systemd_units_get_their_new_names(legacy, monkeypatch):
    units = Path(install.systemd_dir())
    units.mkdir(parents=True)
    for name in ("chronicle-sync.service", "chronicle-sync.timer", "chronicle-ui.service"):
        (units / name).write_text("[Unit]\nOnUnitActiveSec=1200s\n")
    calls = []
    monkeypatch.setattr(install, "uses_systemd", lambda: True)
    monkeypatch.setattr(install, "_systemctl", lambda *a: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    stops = []
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: stops.append(cmd) or subprocess.CompletedProcess(cmd, 0)
                        if cmd[:1] == ["systemctl"] else real_run(cmd, *a, **k))
    migrate.migrate()
    assert sorted(p.name for p in units.iterdir()) == ["interlatch-sync.service", "interlatch-sync.timer",
                                                        "interlatch-ui.service"]
    assert "OnUnitActiveSec=1200s" in (units / "interlatch-sync.timer").read_text()
    assert f"INTERLATCH_HOME={legacy['new']}" in (units / "interlatch-ui.service").read_text()
    assert ("enable", "--now", "interlatch-ui.service") in calls
    assert ("disable", "chronicle-sync.timer", "chronicle-sync.service") in calls
    assert ["systemctl", "--user", "stop", "chronicle-ui.service"] in stops


# ---------------------------------------------------------------- the pieces
def test_permission_rules_are_renamed_and_nothing_else():
    assert migrate.rename_rules(["Bash(ls)", "mcp__chronicle__a", "mcp__chronicles__b", "mcp__chronicle"]) == [
        "Bash(ls)", "mcp__interlatch__a", "mcp__chronicles__b", "mcp__interlatch"]
    assert migrate.rename_rules(["mcp__interlatch__a", "mcp__chronicle__a"]) == ["mcp__interlatch__a"]
    assert migrate.rename_rules(["Bash(ls)"]) is None and migrate.rename_rules(None) is None


def test_the_block_from_before_the_rename_is_read_and_rewritten():
    from interlatch import instructions as ins

    old = "# A\n\n<!-- BEGIN chronicle -->\n- Rule. <!-- chronicle:friction:k -->\n- mine\n<!-- END chronicle -->\n"
    assert ins.parse_block(old) == ([("friction:k", "Rule.")], ["- mine"])
    assert ins.merge(old, [("k2", "Two.")]) == ("# A\n\n<!-- BEGIN interlatch -->\n- Rule. <!-- interlatch:friction:k -->\n"
                                                 "- Two. <!-- interlatch:k2 -->\n- mine\n<!-- END interlatch -->\n")
    assert ins.relabel(old) == old.replace("chronicle", "interlatch")
    assert ins.relabel("no block here <!-- chronicle:x -->\n") == "no block here <!-- chronicle:x -->\n"
    with pytest.raises(ins.MalformedBlock, match="malformed interlatch block"):
        ins.relabel(old + "<!-- BEGIN interlatch -->\n")


def test_the_shim_finds_interlatch_or_chronicle_app(tmp_path, monkeypatch):
    shim = install.write_shim(str(tmp_path / "gone" / "Interlatch"))
    assert shim == default_home() / "bin" / "interlatch"
    apps = Path(os.environ["HOME"]) / "Applications"
    marker = tmp_path / "ran"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def run_shim(found: dict[str, Path], exe_name: str):
        for app in found.values():
            exe = app / "Contents" / "MacOS" / exe_name
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text(f'#!/bin/sh\necho "{app.name} $*" > {marker}\n')
            exe.chmod(0o755)
        cases = "".join(f'*{bid}*) echo "{app}";; ' for bid, app in found.items())
        (bin_dir / "mdfind").write_text(f'#!/bin/sh\ncase "$*" in {cases}esac\n')
        (bin_dir / "mdfind").chmod(0o755)
        marker.unlink(missing_ok=True)
        subprocess.run([str(shim), "hook", "x"], env={**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}, timeout=10)
        return marker.read_text().strip() if marker.exists() else None

    assert run_shim({install.LEGACY_BUNDLE_ID: apps / "Chronicle.app"}, "Chronicle") == "Chronicle.app hook x"
    assert run_shim({install.APP_BUNDLE_ID: apps / "Interlatch.app", install.LEGACY_BUNDLE_ID: apps / "Chronicle.app"},
                    "Interlatch") == "Interlatch.app hook x"


def test_chronicle_apps_shim_runs_the_new_app_too(tmp_path):
    old = default_home() / "bin" / "chronicle"
    old.parent.mkdir(parents=True)
    old.write_text("#!/bin/sh\nexec /Applications/Chronicle.app/Contents/MacOS/Chronicle \"$@\"\n")
    shim = install.write_shim("/Applications/Interlatch.app/Contents/MacOS/Interlatch")
    assert old.read_text() == shim.read_text() and not old.is_symlink()  # a copy: Chronicle.app may rewrite its own


def test_the_command_name(monkeypatch):
    from interlatch.cli import prog_name

    for argv0, name in (("/u/.local/bin/interlatch", "interlatch"), ("/u/.local/bin/chronicle", "chronicle"),
                        ("/x/chronicle/__main__.py", "interlatch")):
        monkeypatch.setattr(sys, "argv", [argv0])
        assert prog_name() == name
