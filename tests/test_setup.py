"""`interlatch install`: detect the agents on this machine, ask which to record, import, point at the dashboard."""

import json
import os
import sys

import pytest

from interlatch.runtime import connectors
from interlatch.interfaces.cli.main import main
from interlatch.core.config import load_config
from interlatch.storage.sqlite import connect
from interlatch.runtime.install import hooks_installed


class _Tty:
    def isatty(self):
        return True


@pytest.fixture()
def machine(env, monkeypatch):
    """A home with Claude Code (env's tree), Codex and Cursor installed, and nothing reaching the real one."""
    home = env["tmp"] / "home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    (home / ".cursor").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setattr("interlatch.runtime.connectors.APPLICATIONS", env["tmp"] / "Applications")
    monkeypatch.setattr("interlatch.core.config.Config.codex_bin", lambda self: None)
    monkeypatch.setitem(connectors.CONNECTORS, "bob",
                        (lambda cfg, conn: {"name": "bob", "label": "IBM Bob", "detected": False, "connected": False, "on_disk": 0},
                         None, None))
    mcp_calls = []
    monkeypatch.setattr("interlatch.runtime.install.install_mcp", lambda cfg, exe, dry_run=False: mcp_calls.append(exe) or ["mcp ok"])
    env["home_dir"] = home
    env["mcp_calls"] = mcp_calls
    return env


def _answer(monkeypatch, answers):
    """Answer each prompt by the first key found in its question; record what was asked."""
    asked = []

    def fake_input(question):
        asked.append(question)
        return next((a for k, a in answers.items() if k in question), "")

    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", fake_input)
    return asked


def _install(*extra):
    return main(["install", "--no-launchd", "--no-ui", "--exe", "/opt/bin/chronicle", *extra])


def test_install_asks_per_agent_and_connects_the_chosen(machine, monkeypatch, capsys):
    asked = _answer(monkeypatch, {"Claude Code": "y", "Codex": "n", "Cursor": "y", "Analyze": "n", "Open": "n"})
    assert _install() == 0
    out = capsys.readouterr().out
    assert [q.split("?")[0] for q in asked] == ["Which one", "Record Claude Code sessions", "Record Codex sessions",
                                                 "Give Cursor Interlatch's MCP tools (search your past sessions)",
                                                 "Turn on game mode", "Analyze it now and show the progress"]
    cfg = load_config(machine["home"])
    assert not cfg.game_enabled  # opt-in: Enter says no
    assert hooks_installed(cfg).get("SessionEnd") and machine["mcp_calls"] == ["/opt/bin/chronicle"]
    assert cfg.codex_dirs == [] and not cfg.codex_cloud
    cursor = json.loads((machine["home_dir"] / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["interlatch"] == {"command": "/opt/bin/chronicle", "args": ["mcp"]}
    assert connect(cfg.db_path).execute("SELECT COUNT(*) FROM sessions WHERE agent = 'claude'").fetchone()[0] >= 1
    assert "imported: 1 new" in out and "interlatch ui --open" in out


def test_declining_claude_stops_scanning_it(machine, monkeypatch, capsys):
    _answer(monkeypatch, {"Claude Code": "n", "Codex": "y", "Cursor": "n"})
    assert _install() == 0
    cfg = load_config(machine["home"])
    assert cfg.claude_dirs == [] and not hooks_installed(cfg) and machine["mcp_calls"] == []
    assert [d.name for d in cfg.codex_dirs] == [".codex"]


def test_yes_records_everything_found_but_codex_cloud(machine, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail(f"asked {q!r} with --yes"))
    assert _install("--yes", "--no-sync") == 0
    cfg = load_config(machine["home"])
    assert hooks_installed(cfg).get("SessionEnd") and cfg.codex_dirs and not cfg.codex_cloud
    assert (machine["home_dir"] / ".cursor" / "mcp.json").exists()
    assert not cfg.game_enabled  # opt-in: never turned on without asking
    assert "imported" not in capsys.readouterr().out


def test_rerun_only_asks_about_new_agents(machine, monkeypatch, capsys):
    _answer(monkeypatch, {"Claude Code": "y", "Codex": "y", "Cursor": "y"})
    _install("--no-sync")
    asked = _answer(monkeypatch, {})
    assert _install("--no-sync") == 0
    assert asked == []
    cfg = load_config(machine["home"])
    assert hooks_installed(cfg).get("SessionEnd") and cfg.codex_dirs


def test_game_mode_is_asked_once_and_the_flag_sets_it(machine, monkeypatch, capsys):
    _answer(monkeypatch, {"game mode": "y"})
    assert _install("--no-sync") == 0
    assert load_config(machine["home"]).game_enabled
    assert "game mode on" in capsys.readouterr().out
    asked = _answer(monkeypatch, {})
    assert _install("--no-sync", "--no-game") == 0
    assert not any("game mode" in q for q in asked) and not load_config(machine["home"]).game_enabled
    assert _install("--no-sync") == 0  # answered before: not asked again, and left as it is
    assert not any("game mode" in q for q in asked) and not load_config(machine["home"]).game_enabled
    assert _install("--no-sync", "--game", "--dry-run") == 0
    assert "would turn game mode on" in capsys.readouterr().out and not load_config(machine["home"]).game_enabled


def test_dry_run_changes_nothing(machine, capsys):
    assert _install("--dry-run") == 0
    out = capsys.readouterr().out
    assert "Would record: claude, codex" in out and "cursor" in out
    cfg = load_config(machine["home"])
    assert not hooks_installed(cfg) and cfg.codex_dirs == [] and not (machine["home_dir"] / ".cursor" / "mcp.json").exists()


@pytest.mark.parametrize("answer", ["y", "n"])
def test_asks_whether_to_run_in_the_background(machine, monkeypatch, capsys, answer):
    calls = []
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("interlatch.runtime.install.launchd_status", lambda label="": {"loaded": False})
    monkeypatch.setattr("interlatch.runtime.install.install_launchd", lambda cfg, exe, interval=900, dry_run=False: calls.append("sync") or [])
    monkeypatch.setattr("interlatch.runtime.install.install_ui_agent",
                        lambda cfg, exe, dry_run=False: calls.append("ui") or ["dashboard always available at http://127.0.0.1:11524/"])
    asked = _answer(monkeypatch, {"background": answer, "Open": "n"})
    assert main(["install", "--exe", "/opt/bin/chronicle", "--no-sync"]) == 0
    out = capsys.readouterr().out
    assert any("starting at login" in q for q in asked)
    if answer == "y":
        assert calls == ["sync", "ui"] and "Dashboard: http://127.0.0.1:11524/" in out and any(q.startswith("Open") for q in asked)
    else:
        assert calls == [] and "Not running in the background: Claude Code sessions are still recorded" in out
        assert "interlatch ui --open" in out


def test_background_already_running_is_kept_without_asking(machine, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("interlatch.runtime.install.launchd_status", lambda label="": {"loaded": True})
    monkeypatch.setattr("interlatch.runtime.install.install_launchd", lambda cfg, exe, interval=900, dry_run=False: calls.append("sync") or [])
    monkeypatch.setattr("interlatch.runtime.install.install_ui_agent", lambda cfg, exe, dry_run=False: calls.append("ui") or [])
    asked = _answer(monkeypatch, {"Claude Code": "y", "Codex": "y", "Cursor": "y"})
    assert main(["install", "--exe", "/opt/bin/chronicle", "--no-sync"]) == 0
    assert not any("starting at login" in q for q in asked) and calls == ["sync", "ui"]


def test_no_launchd_no_ui_turns_background_running_off(machine, monkeypatch, capsys):
    removed = []
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("interlatch.runtime.install.launchd_status", lambda label="": {"loaded": True})
    monkeypatch.setattr("interlatch.runtime.install.install_launchd", lambda *a, **k: pytest.fail("installed the sync agent"))
    monkeypatch.setattr("interlatch.runtime.install.install_ui_agent", lambda *a, **k: pytest.fail("installed the dashboard agent"))
    monkeypatch.setattr("interlatch.runtime.install.uninstall_launchd",
                        lambda labels: removed.extend(labels) or [f"removed launchd agent {label}" for label in labels])
    _answer(monkeypatch, {"Claude Code": "y"})
    assert _install("--no-sync") == 0
    out = capsys.readouterr().out
    assert removed == ["com.interlatch.sync", "com.interlatch.ui"]
    assert "removed launchd agent com.interlatch.ui" in out and "interlatch ui --open" in out
    assert hooks_installed(load_config(machine["home"])).get("SessionEnd")  # the rest stays connected


def test_uninstall_launchd_removes_only_the_given_agents(tmp_path, monkeypatch):
    from interlatch.runtime import install

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    booted_out = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: booted_out.append(cmd[-1]))
    for label in (install.LAUNCHD_LABEL, install.UI_LABEL):
        install.plist_path(label).parent.mkdir(parents=True, exist_ok=True)
        install.plist_path(label).write_text("<plist/>")
    assert install.uninstall_launchd([install.UI_LABEL]) == [f"removed launchd agent {install.UI_LABEL}"]
    assert not install.plist_path(install.UI_LABEL).exists() and install.plist_path(install.LAUNCHD_LABEL).exists()
    assert booted_out == [f"gui/{os.getuid()}/{install.UI_LABEL}"]


def _glossary_terms(cfg) -> int:
    return connect(cfg.db_path).execute("SELECT COUNT(*) FROM glossary").fetchone()[0]


def test_install_explains_analysis_and_builds_the_glossary_now_when_asked(machine, monkeypatch, capsys):
    asked = _answer(monkeypatch, {"Claude Code": "y", "Codex": "n", "Cursor": "n", "Analyze": "y", "Open": "n"})
    assert _install() == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "Until sessions are analyzed, the Glossary and the Map stay empty" in out and "1 past session waiting" in out
    assert any(q.startswith("Analyze it now and show the progress? (about") for q in asked)
    cfg = load_config(machine["home"])
    conn = connect(cfg.db_path)
    assert conn.execute("SELECT analysis_status FROM sessions WHERE agent = 'claude' AND source != 'history'").fetchone()[0] == "done"
    assert _glossary_terms(cfg) > 0 and conn.execute("SELECT COUNT(*) FROM project_kb").fetchone()[0] >= 1
    assert "analyzed 1 session, built" in out and "glossary term" in out
    assert "The Glossary and the Map are ready on the dashboard." in out


@pytest.fixture()
def no_providers(monkeypatch):
    """No API key, AWS or Azure sign-in, Ollama host, IBM Bob or OpenCode from the computer that runs the tests."""
    from interlatch.analysis.runners.settings import KEY_ENVS

    for name in [*KEY_ENVS.values(), "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "OLLAMA_HOST"]:
        if name:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("interlatch.core.config.Config.bob_bin", lambda self: None)
    monkeypatch.setattr("interlatch.core.config.Config.opencode_bin", lambda self: None)
    monkeypatch.setattr("interlatch.analysis.runners.api._tool", lambda name: None)


def test_first_install_lists_every_backend_and_enter_keeps_the_ready_one(machine, no_providers, monkeypatch, capsys):
    asked = _answer(monkeypatch, {"Claude Code": "y", "Codex": "n", "Cursor": "n", "Analyze": "n", "Open": "n"})
    assert _install("--no-sync") == 0
    out = " ".join(capsys.readouterr().out.split())
    assert asked[0] == "Which one? [1-12, Enter = 1]: "
    assert "1 Claude Code ready · your own login and plan ← Enter" in out and "2 Codex not installed" in out
    assert "4 OpenCode not installed" in out and "5 Anthropic API add an API key" in out
    assert "10 Ollama choose a model" in out and "12 Record only never analyze: only record sessions" in out
    assert "Sessions are analyzed with Claude Code." in out
    assert load_config(machine["home"]).analysis.backend == "claude"
    asked = _answer(monkeypatch, {})
    assert _install("--no-sync") == 0
    assert not any(q.startswith("Which one") for q in asked)  # asked once; the choice is kept


def test_picking_a_provider_asks_for_its_key_and_checks_it(machine, no_providers, monkeypatch, capsys):
    from interlatch.analysis.runners.api import ApiRunner
    from interlatch.analysis.runners.base import LLMResult
    from interlatch.analysis.runners.settings import key_source

    checked = []
    monkeypatch.setattr("getpass.getpass", lambda q: "sk-ant-test")
    monkeypatch.setattr(ApiRunner, "check", lambda self: checked.append(self.name) or LLMResult(
        {"ok": True}, 0.0, 800, "claude-sonnet-5-5", 10, 5))
    asked = _answer(monkeypatch, {"Which one": "5", "Check": "y", "Claude Code": "y", "Codex": "n", "Cursor": "n",
                                  "Analyze": "n", "Open": "n"})
    assert _install() == 0
    out = " ".join(capsys.readouterr().out.split())
    cfg = load_config(machine["home"])
    assert cfg.analysis.backend == "anthropic" and key_source(cfg, "anthropic") == ("sk-ant-test", "stored")
    assert checked == ["anthropic"] and "It works (claude-sonnet-5-5, 0.8 s)" in out
    assert "Sessions are analyzed with Anthropic API." in out
    assert "reads each session through Anthropic API (claude-sonnet-5-5)" in out and "is billed to your key" in out
    assert any(q.startswith("Analyze it now") for q in asked)


def test_picking_ollama_offers_its_models(machine, no_providers, monkeypatch, capsys):
    from interlatch.analysis.runners.api import ApiRunner

    monkeypatch.setattr(ApiRunner, "list_models", lambda self, timeout=15: ["llama3.2", "qwen3"])
    _answer(monkeypatch, {"Which one": "ollama", "Model": "qwen3", "Check": "n", "Claude Code": "y", "Codex": "n",
                          "Cursor": "n", "Analyze": "n", "Open": "n"})
    assert _install() == 0
    out = " ".join(capsys.readouterr().out.split())
    cfg = load_config(machine["home"])
    assert cfg.analysis.backend == "ollama" and cfg.providers["ollama"]["model"] == "qwen3"
    assert "Models it offers: llama3.2, qwen3" in out and "with Ollama on this computer" in out


def test_a_setup_left_blank_goes_back_to_the_list(machine, no_providers, monkeypatch, capsys):
    picks = iter(["7", ""])  # OpenAI API, then (its model left blank) Enter for the ready one

    def fake_input(q):
        if q.startswith("Which one"):
            return next(picks)
        return "" if "Model" in q else "n"

    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("getpass.getpass", lambda q: "sk-test")
    monkeypatch.setattr("interlatch.analysis.runners.api.ApiRunner.list_models", lambda self, timeout=15: [])
    assert _install("--no-sync") == 0
    out = " ".join(capsys.readouterr().out.split())
    assert out.count("What analyzes your sessions") == 2
    assert load_config(machine["home"]).analysis.backend == "claude"


def test_backend_flag_sets_it_without_asking_and_says_what_is_missing(machine, no_providers, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail(f"asked {q!r} with --yes"))
    assert _install("--yes", "--backend", "openai") == 0
    out = " ".join(capsys.readouterr().out.split())
    assert load_config(machine["home"]).analysis.backend == "openai"
    assert ("OpenAI API: choose a model (providers.openai.model). Sessions are recorded but not analyzed until it is "
            "set up: `interlatch config set providers.openai.model NAME`, `interlatch config set-key openai`") in out
    assert "past session waiting" not in out


@pytest.fixture()
def bob_key_in_shell(no_providers, monkeypatch):
    """IBM Bob installed and its key exported in this shell only, which launchd's jobs and the dashboard don't get."""
    monkeypatch.setattr("interlatch.core.config.Config.bob_bin", lambda self: "/opt/bin/bob")
    monkeypatch.setenv("BOB_API_KEY", "bob-from-shell")


def test_a_key_only_in_the_shell_is_saved_when_asked(machine, bob_key_in_shell, monkeypatch, capsys):
    from interlatch.analysis.runners.settings import key_source

    asked = _answer(monkeypatch, {"Save": "y", "Which one": "bob", "Claude Code": "y", "Codex": "n", "Cursor": "n",
                                  "Analyze": "n", "Open": "n"})
    assert _install("--no-sync") == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "3 IBM Bob ready in this terminal only · $BOB_API_KEY" in out
    assert any(q.startswith("Your IBM Bob key is in $BOB_API_KEY") for q in asked) and "Saved your IBM Bob key." in out
    monkeypatch.delenv("BOB_API_KEY")
    assert key_source(load_config(machine["home"]), "bob") == ("bob-from-shell", "stored")


def test_a_key_only_in_the_shell_is_not_saved_without_asking(machine, bob_key_in_shell, monkeypatch, capsys):
    from interlatch.analysis.runners.settings import key_source

    monkeypatch.setattr("builtins.input", lambda q: pytest.fail(f"asked {q!r} with --yes"))
    assert _install("--yes", "--no-sync", "--backend", "bob") == 0
    out = " ".join(capsys.readouterr().out.split())
    assert ("Your IBM Bob key is only in $BOB_API_KEY: `interlatch analyze` works from this terminal, but background "
            "analysis and the dashboard don't see it. `interlatch config set-key bob` saves it.") in out
    assert key_source(load_config(machine["home"]), "bob") == ("bob-from-shell", "$BOB_API_KEY")
    assert main(["config", "set", "analysis.backend", "bob"]) == 0
    assert "note: the IBM Bob key is only in $BOB_API_KEY" in capsys.readouterr().out
    assert main(["config", "set-key", "bob", "bob-saved"]) == 0
    assert _install("--yes", "--no-sync") == 0
    assert "only in $BOB_API_KEY" not in capsys.readouterr().out  # a saved key wins


def test_backend_flag_with_dry_run_changes_nothing(machine, capsys):
    assert _install("--dry-run", "--backend", "codex") == 0
    assert "Would analyze sessions with Codex (Codex (`codex`) was not found)." in capsys.readouterr().out
    assert load_config(machine["home"]).analysis.backend == "claude"


def test_install_without_a_terminal_leaves_analysis_for_later_and_says_how(machine, capsys):
    assert _install("--yes") == 0
    out = " ".join(capsys.readouterr().out.split())
    assert _glossary_terms(load_config(machine["home"])) == 0
    assert "1 past session waiting" in out
    assert "The 1 waiting session is analyzed when you run `interlatch analyze --pending` (nothing runs in the background)" in out


def test_analyze_flag_runs_it_without_asking(machine, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail(f"asked {q!r} with --yes"))
    assert _install("--yes", "--analyze", "all") == 0
    assert _glossary_terms(load_config(machine["home"])) > 0
    assert "analyzed 1 session" in capsys.readouterr().out


def test_background_analysis_says_how_long_the_backlog_takes(machine, monkeypatch, capsys):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("interlatch.runtime.install.launchd_status", lambda label="": {"loaded": True})
    monkeypatch.setattr("interlatch.runtime.install.install_launchd", lambda *a, **k: [])
    monkeypatch.setattr("interlatch.runtime.install.install_ui_agent", lambda *a, **k: [])
    _answer(monkeypatch, {"Analyze": "later", "Open": "n"})
    assert main(["install", "--exe", "/opt/bin/chronicle"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "Later: in the background, 6 every 15 min, so all of them in about 15 min" in out
    assert "The 1 waiting session is analyzed in the background" in out


def test_background_is_not_promised_where_it_cannot_run(machine, monkeypatch, capsys):
    monkeypatch.setattr("platform.system", lambda: "FreeBSD")  # no launchd, systemd or Task Scheduler
    _answer(monkeypatch, {"Analyze": "n", "Open": "n"})
    assert main(["install", "--exe", "/opt/bin/chronicle"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "Not running in the background" in out and "analyzed in the background" not in out


def test_stage_progress_draws_one_bar_per_stage():
    from rich.console import Console
    from rich.progress import Progress

    from interlatch.interfaces.cli.commands.setup import _StageProgress

    with Progress(console=Console(file=open(os.devnull, "w"))) as bar:
        stages = _StageProgress(bar)
        for m in ["analyzing sessions: 0 of 3 done", "analyzing sessions: 3 of 3 done", "synthesizing demo-app…",
                  "synthesizing __global__…", "glossary 1/2: demo-app -> 4", "glossary 2/2: __global__ -> 1",
                  "themes 1/1: tool -> 2"]:
            stages(m)
        stages.finish()
        rows = {t.description: (t.completed, t.total, t.fields["detail"]) for t in bar.tasks}
    assert rows == {"Sessions": (3, 3, "done"), "Knowledge bases": (1, 1, "2 built"), "Glossary": (2, 2, "done"),
                    "Map themes": (1, 1, "done")}


def test_stage_progress_takes_messages_from_several_threads_at_once():
    import threading

    from rich.console import Console
    from rich.progress import Progress

    from interlatch.interfaces.cli.commands.setup import _StageProgress

    with Progress(console=Console(file=open(os.devnull, "w"))) as bar:
        stages = _StageProgress(bar)
        go = threading.Barrier(8)
        threads = [threading.Thread(target=lambda: (go.wait(), stages("analyzing sessions: 0 of 8 done"))) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert [t.description for t in bar.tasks] == ["Sessions"]


def test_version_flag_reports_the_installed_version(capsys):
    from interlatch import __version__

    with pytest.raises(SystemExit) as exit_:
        main(["--version"])
    assert exit_.value.code == 0 and capsys.readouterr().out.strip() == f"interlatch {__version__}"


def test_tests_never_reach_this_computers_own_agents():
    """conftest's no_real_background_agents: a test that runs `chronicle install --no-ui` must not boot out the
    developer's real dashboard, as one did."""
    import subprocess
    from pathlib import Path

    from interlatch.runtime import install

    assert Path("~/Library/LaunchAgents").expanduser() not in install.plist_path(install.UI_LABEL).parents
    assert Path("~/.config").expanduser() not in install.systemd_dir().parents
    r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{install.UI_LABEL}"], capture_output=True, text=True)
    assert r.returncode == 113 and r.stdout == ""
