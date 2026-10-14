"""Release notifications: opt-in, at most one PyPI request a day, one desktop notification per release."""

import base64
import io
import json
import time

import pytest

from chronicle import notify, update
from chronicle.cli import main
from chronicle.config import load_config
from chronicle.db import connect


@pytest.fixture
def pypi(monkeypatch, env):
    """A PyPI install whose PyPI answers `latest`; records requests and notifications."""
    monkeypatch.setattr(update, "_method", {})
    monkeypatch.setattr(update, "_remote", {})
    monkeypatch.setattr(update, "_install_method", lambda: {"kind": "uv", "label": "uv tool from PyPI", "command": ["uv"]})
    state = {"latest": "99.0.0", "requests": 0, "posted": [], "can_post": True}

    def fake_urlopen(req, timeout):
        state["requests"] += 1
        return io.BytesIO(json.dumps({"info": {"version": state["latest"]}}).encode())

    monkeypatch.setattr(update, "urlopen", fake_urlopen)
    monkeypatch.setattr(notify, "post", lambda title, message: state["posted"].append((title, message)) or state["can_post"])
    env["cfg"].update_notify = True
    state["conn"] = connect(env["cfg"].db_path)
    return state


def test_off_by_default_never_goes_online(pypi, env):
    env["cfg"].update_notify = False
    assert notify.release_check(env["cfg"], pypi["conn"]) is None
    assert pypi["requests"] == 0 and pypi["posted"] == []


def test_notifies_once_per_release(pypi, env, monkeypatch):
    cfg, conn = env["cfg"], pypi["conn"]
    assert notify.release_check(cfg, conn) == "99.0.0"
    title, message = pypi["posted"][0]
    assert title == "Interlatch 99.0.0 is available" and "uv tool upgrade interlatch" in message
    assert "#/status?focus=updates" in message

    assert notify.release_check(cfg, conn) is None  # the next sync, 15 minutes later
    assert pypi["requests"] == 1 and len(pypi["posted"]) == 1

    update._remote.clear()  # a restart: the last answer comes back from the database, the day is not over
    assert notify.release_check(cfg, conn) is None and pypi["requests"] == 1

    pypi["latest"] = "99.1.0"
    tomorrow = time.time() + update.DAY + 1
    monkeypatch.setattr(update.time, "time", lambda: tomorrow)
    assert notify.release_check(cfg, conn) == "99.1.0"
    assert pypi["requests"] == 2 and len(pypi["posted"]) == 2


def test_a_notification_that_could_not_be_shown_is_tried_again(pypi, env):
    pypi["can_post"] = False
    assert notify.release_check(env["cfg"], pypi["conn"]) is None
    pypi["can_post"] = True
    assert notify.release_check(env["cfg"], pypi["conn"]) == "99.0.0"
    assert pypi["requests"] == 1  # the version was already known


def test_nothing_when_up_to_date_or_not_from_pypi(pypi, env, monkeypatch):
    from chronicle import __version__

    pypi["latest"] = __version__
    assert notify.release_check(env["cfg"], pypi["conn"]) is None and pypi["requests"] == update.PYPI_TRIES  # an up-to-date answer is asked again (CDN)
    monkeypatch.setattr(update, "compares_online", lambda: False)  # a checkout: `git pull`, nothing to announce
    update._remote.clear()
    assert notify.release_check(env["cfg"], pypi["conn"]) is None and pypi["requests"] == update.PYPI_TRIES


def test_sync_runs_the_check(pypi, env, monkeypatch):
    from chronicle.config import set_config_value

    set_config_value(env["cfg"], "updates", "notify", "true")
    assert main(["sync", "--quiet"]) == 0
    assert [t for t, _ in pypi["posted"]] == ["Interlatch 99.0.0 is available"]


def test_post_uses_the_system_notifier(monkeypatch):
    ran = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: ran.append(cmd) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    assert notify.post('Chronicle "1.0"', "run: a\\b")
    assert ran[-1] == ["osascript", "-e", 'display notification "run: a\\\\b" with title "Chronicle \\"1.0\\""']
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/notify-send")
    assert notify.post("T", "M") and ran[-1] == ["notify-send", "--app-name=Interlatch", "T", "M"]
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert not notify.post("T", "M")

    # Windows: a PowerShell toast, the text on stdin as JSON (never in the command line), no console window
    calls = []

    def fake_win_run(cmd, input=None, **k):
        calls.append((cmd, input, k))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("subprocess.CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" if "powershell" in name else None)
    monkeypatch.setattr("subprocess.run", fake_win_run)
    title, message = "Interlatch 1.0'; Remove-Item -Recurse C:\\ #", 'run: "uv tool upgrade" $(whoami) ü'
    assert notify.post(title, message)
    cmd, stdin, kw = calls[-1]
    assert cmd[0].endswith("powershell.exe") and cmd[-2] == "-EncodedCommand"
    script = base64.b64decode(cmd[-1]).decode("utf-16-le")
    assert script == notify.WINDOWS_SCRIPT and notify.WINDOWS_AUMID in script and "%AUMID%" not in script
    assert "CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe')" in script
    assert "Remove-Item" not in " ".join(cmd) and "whoami" not in " ".join(cmd)
    assert json.loads(stdin) == {"title": title, "message": message} and stdin.isascii()
    assert kw["creationflags"] == 0x08000000
    calls.clear()
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: type("R", (), {"returncode": 1})())
    assert not notify.post("T", "M")  # neither a toast nor a balloon could show
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert not notify.post("T", "M")


def test_how_to_update_matches_the_install(env):
    cfg = env["cfg"]
    assert "Status › Updates" in notify.how_to_update(cfg, "app") and "run:" not in notify.how_to_update(cfg, "app")
    assert notify.how_to_update(cfg, "pipx").endswith("run: pipx upgrade interlatch")


# ------------------------------------------------------------------------------------------- install
@pytest.fixture
def setup(env, monkeypatch):
    """`chronicle install` with the background sync running and a PyPI install."""
    monkeypatch.setattr("chronicle.update.compares_online", lambda: True)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("chronicle.install.launchd_status", lambda label="": {"loaded": True})
    monkeypatch.setattr("chronicle.install.install_launchd", lambda *a, **k: [])
    monkeypatch.setattr("chronicle.install.install_ui_agent", lambda *a, **k: [])
    monkeypatch.setattr("chronicle.install.install_mcp", lambda *a, **k: [])
    return env


def _run(monkeypatch, answers, *extra):
    import sys

    asked = []

    class Tty:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr("builtins.input", lambda q: asked.append(q) or next((a for k, a in answers.items() if k in q), ""))
    assert main(["install", "--exe", "/opt/bin/chronicle", "--no-sync", *extra]) == 0
    return [q for q in asked if "new version" in q]


@pytest.mark.parametrize("answer", ["y", "n"])
def test_install_asks_once(setup, monkeypatch, capsys, answer):
    assert len(_run(monkeypatch, {"new version": answer, "Open": "n"})) == 1
    assert load_config(setup["home"]).update_notify == (answer == "y")
    out = capsys.readouterr().out
    assert ("a desktop notification when a new version is out" in out) == (answer == "y")
    assert _run(monkeypatch, {"Open": "n"}) == []  # a re-run does not ask again


def test_install_flag_sets_it_without_asking(setup, monkeypatch):
    assert _run(monkeypatch, {"Open": "n"}, "--notify-updates") == []
    assert load_config(setup["home"]).update_notify
    assert _run(monkeypatch, {"Open": "n"}, "--no-notify-updates") == []
    assert not load_config(setup["home"]).update_notify


def test_install_does_not_ask_without_the_background_sync_or_a_terminal(setup, monkeypatch):
    assert _run(monkeypatch, {"Open": "n"}, "--no-launchd") == []
    assert main(["install", "--exe", "/opt/bin/chronicle", "--no-sync", "--yes"]) == 0
    assert not load_config(setup["home"]).update_notify
