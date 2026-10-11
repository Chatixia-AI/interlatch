"""The dashboard's Update button: install detection, update checks and the update command."""

import io
import json
import os
import subprocess
import sys
import time

import pytest

from chronicle import __version__, update


@pytest.fixture
def method(monkeypatch):
    """Pretend Interlatch was installed a given way; returns a setter."""
    monkeypatch.setattr(update, "_method", {})
    monkeypatch.setattr(update, "_remote", {})

    def set_method(**m):
        update._method.clear()
        monkeypatch.setattr(update, "_install_method", lambda: {"label": m.get("kind"), "command": None, **m})

    return set_method


def test_version_order():
    assert update._vkey("0.1.10") > update._vkey("0.1.9") > update._vkey("0.1") > update._vkey(None)
    assert update._vkey("0.2.0rc1") == (0, 2, 0)


def test_pypi_install_checks_only_when_asked(method, monkeypatch):
    method(kind="uv", command=["uv", "tool", "upgrade", "interlatch"])
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        return io.BytesIO(json.dumps({"info": {"version": "99.0.0"}}).encode())

    monkeypatch.setattr(update, "urlopen", fake_urlopen)
    info = update.check()
    assert not calls and not info["available"] and info["checked_at"] is None
    assert update.available_version() is None  # the status bar poll never goes online

    info = update.check(remote=True)
    assert calls == [update.PYPI_JSON]
    assert info["available"] and info["latest"] == "99.0.0" and info["can_update"]
    assert info["command"] == "uv tool upgrade interlatch"
    assert update.available_version() == "99.0.0"  # remembered from the check
    assert update.available() == {"to": "99.0.0", "key": "99.0.0"}
    assert info["notes_url"].endswith("/releases/tag/v99.0.0")
    assert len(calls) == 1


def test_pypi_unreachable_is_reported(method, monkeypatch):
    method(kind="pipx", command=["pipx", "upgrade", "interlatch"])

    def offline(req, timeout):
        raise OSError("no network")

    monkeypatch.setattr(update, "urlopen", offline)
    info = update.check(remote=True)
    assert not info["available"] and "Could not reach PyPI" in info["error"]


def test_stale_cdn_answer_is_asked_again(method, monkeypatch):
    """Right after a release, some of PyPI's cache servers still serve the old JSON: one click finds the release."""
    method(kind="uv", command=["uv", "tool", "upgrade", "interlatch"])
    answers = [__version__, "99.0.0", "0.0.1"]
    calls = []

    def cdn(req, timeout):
        calls.append(req.full_url)
        return io.BytesIO(json.dumps({"info": {"version": answers[len(calls) - 1]}}).encode())

    monkeypatch.setattr(update, "urlopen", cdn)
    info = update.check(remote=True)
    assert info["available"] and info["latest"] == "99.0.0" and not info["error"]
    assert len(calls) == 2  # stops once an answer is newer than this copy

    calls.clear()
    answers[:] = [__version__] * 3
    info = update.check(remote=True)
    assert not info["available"] and info["latest"] == __version__ and len(calls) == update.PYPI_TRIES


def test_failed_retry_keeps_the_answer(method, monkeypatch):
    method(kind="uv", command=["uv", "tool", "upgrade", "interlatch"])
    calls = []

    def flaky(req, timeout):
        calls.append(req.full_url)
        if len(calls) > 1:
            raise OSError("dropped")
        return io.BytesIO(json.dumps({"info": {"version": __version__}}).encode())

    monkeypatch.setattr(update, "urlopen", flaky)
    info = update.check(remote=True)
    assert info["latest"] == __version__ and not info["error"] and info["checked_at"]
    assert len(calls) == 2


def test_checkout_install_compares_file_times(method, tmp_path):
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "interlatch"\nversion = "{__version__}"\n')
    (tmp_path / "src" / "chronicle").mkdir(parents=True)
    (tmp_path / "src" / "chronicle" / "server.py").write_text("")
    installed = time.time() + 60
    method(kind="uv", source=str(tmp_path), local=True, installed_at=installed, command=["uv", "tool", "upgrade", "--reinstall", "x"])
    info = update.check()
    assert not info["available"] and info["latest"] == __version__

    os.utime(tmp_path / "src" / "chronicle" / "server.py", (installed + 5, installed + 5))
    info = update.check()
    assert info["available"] and info["can_update"]
    assert update.available_version() == "build"  # same version number, newer files
    assert "changes" not in info  # the status bar's poll does not list them
    info = update.check(detail=True)
    assert info["changes"]["files"] == ["src/chronicle/server.py"] and info["changes"]["commits"] == []


def test_checkout_notification_key_follows_commits(method, tmp_path):
    """Editing files keeps one notification; a new commit brings it back."""
    import subprocess

    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "interlatch"\nversion = "{__version__}"\n')
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    installed = time.time() - 60
    method(kind="uv", source=str(tmp_path), local=True, installed_at=installed, command=["uv"])
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "First change"], check=True)
    first = update.available()
    assert first["to"] == "build" and first["key"].startswith("build:")
    info = update.check(detail=True)
    assert [c["subject"] for c in info["changes"]["commits"]] == ["First change"]

    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "interlatch"\nversion = "{__version__}"\n# edit\n')
    assert update.available()["key"] == first["key"]
    subprocess.run([*git, "commit", "-qam", "Second change"], check=True)
    assert update.available()["key"] != first["key"]


def test_checkout_version_comes_from_git_tags(tmp_path):
    """With a dynamic version (hatch-vcs), the checkout builds as its latest vX.Y.Z tag, or the next dev release."""
    import subprocess

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "interlatch"\ndynamic = ["version"]\n')
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "First"], check=True)
    assert update._checkout_version(str(tmp_path)) is None  # no tag yet
    subprocess.run([*git, "tag", "v0.7.0"], check=True)
    assert update._checkout_version(str(tmp_path)) == "0.7.0"
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "Second"], check=True)
    sha = subprocess.run([*git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert update._checkout_version(str(tmp_path)) == f"0.7.1.dev1+g{sha}"


def test_app_and_source_installs_do_not_run_commands(method, monkeypatch):
    method(kind="app")
    monkeypatch.setattr(update, "urlopen", lambda req, timeout: io.BytesIO(b'{"info": {"version": "99.0.0"}}'))
    info = update.check(remote=True)
    assert info["available"] and not info["can_update"] and info["releases_url"].endswith("/releases/latest")
    with pytest.raises(RuntimeError, match="cannot update itself"):
        update.run_update(lambda m: None)

    method(kind="source")
    assert update.check(remote=True)["note"].startswith("Running from a source checkout")


def test_pypi_check_is_kept_in_the_database(method, env, monkeypatch):
    """The last answer outlives a restart (the in-memory copy is gone; the database still has it)."""
    from chronicle.db import connect

    method(kind="uv", command=["uv", "tool", "upgrade", "interlatch"])
    monkeypatch.setattr(update, "urlopen", lambda req, timeout: io.BytesIO(b'{"info": {"version": "99.0.0"}}'))
    conn = connect(env["cfg"].db_path)
    update.check(remote=True)
    update.remember(conn)
    update._remote.clear()  # a restart
    assert update.available_version() is None
    update.recall(conn)
    assert update.available_version() == "99.0.0"


def test_daily_check_is_opt_in_and_runs_once_a_day(method, env, monkeypatch):
    from chronicle.server import App

    method(kind="uv", command=["uv", "tool", "upgrade", "interlatch"])
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        return io.BytesIO(b'{"info": {"version": "99.0.0"}}')

    monkeypatch.setattr(update, "urlopen", fake_urlopen)
    app = App(env["cfg"])
    assert app.status_small()["update"] is None and not calls  # off by default: never online on its own

    info = app.action_update_setting("check_daily", True)
    assert info["check_daily"] and "check_daily = true" in env["cfg"].config_path.read_text()
    for _ in range(50):  # the first check runs in the background
        if not app._update_check.locked() and calls:
            break
        time.sleep(0.05)
    assert calls == [update.PYPI_JSON]
    assert app.status_small()["update"]["to"] == "99.0.0"
    assert len(calls) == 1  # the next one is due a day later
    assert update.check_due(now=time.time() + update.DAY + 1)

    assert not app.action_update_setting("check_daily", False)["check_daily"]


def test_checkouts_never_check_pypi_daily(method, tmp_path):
    method(kind="uv", source=str(tmp_path), local=True, installed_at=time.time(), command=["uv"])
    assert not update.check_due()


# ---------------------------------------------------------------- an install of agents-chronicle (before the rename)
def _receipt(tmp_path, monkeypatch, name, extras=()):
    prefix = tmp_path / "tools" / name
    prefix.mkdir(parents=True)
    req = f'{{ name = "{name}"' + (f", extras = {json.dumps(list(extras))}" if extras else "") + " }"
    (prefix / "uv-receipt.toml").write_text(f"[tool]\nrequirements = [{req}]\n")
    monkeypatch.setattr(update.sys, "prefix", str(prefix))
    monkeypatch.setattr(update, "_which", lambda name: f"/opt/{name}")
    monkeypatch.setattr(update, "_method", {})
    monkeypatch.setattr(update, "_remote", {})


def test_a_uv_tool_named_interlatch_upgrades(tmp_path, monkeypatch):
    _receipt(tmp_path, monkeypatch, "interlatch", ["app"])
    m = update.install_method()
    assert m["command"] == ["/opt/uv", "tool", "upgrade", "interlatch"] and not m.get("move")


def test_a_uv_tool_named_agents_chronicle_moves_to_interlatch(tmp_path, monkeypatch):
    _receipt(tmp_path, monkeypatch, "agents-chronicle", ["team", "app"])
    py = f"{sys.version_info[0]}.{sys.version_info[1]}"
    # both installs force: uv answers a second plain install with "already installed" and links nothing, so the
    # commands the uninstall took stay gone
    install = ["/opt/uv", "tool", "install", "--force", "--python", py, "interlatch[app,team]"]
    m = dict(update.install_method())
    assert m["move"] and m["extras"] == ["team", "app"]
    assert m["steps"] == [install, ["/opt/uv", "tool", "uninstall", "agents-chronicle"], install]

    def pypi(req, timeout):
        return io.BytesIO(json.dumps({"info": {"version": __version__}}).encode())

    monkeypatch.setattr(update, "urlopen", pypi)
    info = update.check(remote=True)
    # the move is offered at the version it runs too: agents-chronicle X.Y.Z is interlatch X.Y.Z under its old name
    assert info["available"] and info["move"] and info["can_update"] and info["note"] == update.MOVE_NOTE
    assert info["command"].startswith("/opt/uv tool install --force") and " && /opt/uv tool uninstall agents-chronicle && " in info["command"]
    assert update.available() == {"to": __version__, "key": f"move:{__version__}"}

    ran = []
    monkeypatch.setattr(update.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(update, "_new_version", lambda: "9.9.9")
    monkeypatch.setattr(update, "RESTARTABLE", False)
    assert update.run_update(lambda message: None) == "Updated to Interlatch 9.9.9; quit and reopen Interlatch to use it"
    assert ran == m["steps"]


def test_a_move_that_leaves_no_command_fails_instead_of_restarting(tmp_path, monkeypatch):
    _receipt(tmp_path, monkeypatch, "agents-chronicle", ["app"])
    update.install_method()
    monkeypatch.setattr(update, "_which", lambda name: None)  # the uninstall took the commands, nothing put them back
    monkeypatch.setattr(update.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    restarts = []
    monkeypatch.setattr(update, "RESTARTABLE", True)
    monkeypatch.setattr(update, "restart", lambda: restarts.append(1))
    with pytest.raises(RuntimeError, match=r"no interlatch command; run /opt/uv tool install --force .*'interlatch\[app\]'"):
        update.run_update(lambda message: None)
    assert not restarts


def test_a_failed_step_stops_the_move(tmp_path, monkeypatch):
    _receipt(tmp_path, monkeypatch, "agents-chronicle")
    ran = []

    def run(cmd, **kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "error: no network")

    monkeypatch.setattr(update.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="no network"):
        update.run_update(lambda message: None)
    assert len(ran) == 1 and ran[0][3] == "--force"  # nothing was uninstalled


def test_other_installers_of_agents_chronicle_move_too(tmp_path, monkeypatch):  # pipx, pip
    monkeypatch.setattr(update, "_method", {})
    monkeypatch.setattr(update, "_which", lambda name: f"/opt/{name}")
    monkeypatch.setattr(update, "_distribution", lambda: ("interlatch", None))
    monkeypatch.setattr(update.sys, "prefix", str(tmp_path / "pipx" / "venvs" / "agents-chronicle"))
    m = update.install_method()
    assert m["kind"] == "pipx" and m["steps"] == [["/opt/pipx", "uninstall", "agents-chronicle"],
                                                    ["/opt/pipx", "install", "interlatch"]]

    update._method.clear()
    monkeypatch.setattr(update.sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(update, "_installed", lambda name: name == "agents-chronicle")
    monkeypatch.setattr(update.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0))
    pip = [sys.executable, "-m", "pip"]
    assert update.install_method()["steps"] == [[*pip, "install", "--upgrade", "interlatch"],
                                                [*pip, "uninstall", "-y", "agents-chronicle"],
                                                [*pip, "install", "--force-reinstall", "--no-deps", "interlatch"]]


def test_the_dashboard_agent_restarts_under_the_label_it_runs_as(monkeypatch):
    started = []
    monkeypatch.setattr(update.sys, "platform", "darwin")
    monkeypatch.setattr(update.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    monkeypatch.setattr(update.os, "execv", lambda *a: pytest.fail("restarted in place"))
    for label in ("com.interlatch.ui", "com.claude-chronicle.ui"):
        monkeypatch.setenv("XPC_SERVICE_NAME", label)
        update.restart()
        assert started[-1] == ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"]
