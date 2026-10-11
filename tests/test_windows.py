"""Windows: the Task Scheduler tasks, the hidden runner, hook command lines, and the file lock. Most of it runs on any
system with Windows' answers faked; the tests marked `on_windows` need the real thing (the Windows CI job)."""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from chronicle import install, windows
from chronicle.config import load_config
from chronicle.util import file_lock

on_windows = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows")
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@pytest.fixture()
def cfg(tmp_path):
    return load_config(tmp_path / "home")


@pytest.fixture()
def schtasks(monkeypatch):
    """Windows, with schtasks answered here: each call is recorded, with the task's XML as Task Scheduler got it."""
    monkeypatch.setattr("platform.system", lambda: "Windows")
    tasks: dict[str, str] = {}
    calls: list[list[str]] = []

    def fake(*args):
        calls.append(list(args))
        name = args[args.index("/TN") + 1] if "/TN" in args else None
        if args[0] == "/Create":
            tasks[name] = Path(args[args.index("/XML") + 1]).read_text(encoding="utf-16")
        if args[0] == "/Delete":
            tasks.pop(name, None)
        rc = 1 if args[0] in ("/Query", "/End", "/Run") and name not in tasks else 0
        return subprocess.CompletedProcess(["schtasks", *args], rc, "", "")

    monkeypatch.setattr(windows, "schtasks", fake)
    return {"tasks": tasks, "calls": calls}


def _task(xml: str) -> ET.Element:
    return ET.fromstring(xml.encode("utf-16"))


def test_the_sync_task_runs_every_interval_without_a_window(cfg, schtasks):
    out = install.install_launchd(cfg, "C:\\Users\\ada\\.local\\bin\\interlatch.exe", interval=600)
    assert out == ["Task Scheduler task \\Interlatch\\Sync runs `interlatch sync --work` every 10 min"]
    task = _task(schtasks["tasks"]["\\Interlatch\\Sync"])
    assert task.findtext("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", namespaces=NS) == "PT10M"
    assert task.find("t:Triggers/t:LogonTrigger", NS) is not None  # and at sign-in, as launchd's RunAtLoad
    settings = task.find("t:Settings", NS)
    assert settings.findtext("t:MultipleInstancesPolicy", namespaces=NS) == "IgnoreNew"
    assert settings.findtext("t:DisallowStartIfOnBatteries", namespaces=NS) == "false"
    assert settings.findtext("t:ExecutionTimeLimit", namespaces=NS) == "PT4H"
    assert task.findtext("t:Principals/t:Principal/t:LogonType", namespaces=NS) == "InteractiveToken"
    args = task.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS)
    assert args.startswith("-m chronicle.windows --log ") and args.endswith("-- sync --work --quiet")
    assert str(cfg.home) in args and str(cfg.logs_dir / "sync") in args
    # replaced, then started now (launchd's RunAtLoad): the next run is the timer's
    assert [c[0] for c in schtasks["calls"]] == ["/End", "/Create", "/Run"]


def test_the_dashboard_task_never_times_out_and_comes_back(cfg, schtasks):
    out = install.install_ui_agent(cfg, "C:\\Users\\ada\\.local\\bin\\interlatch.exe")
    assert out == [f"dashboard always available at http://127.0.0.1:{cfg.server_port}/ (Task Scheduler task "
                   "\\Interlatch\\Dashboard)"]
    task = _task(schtasks["tasks"]["\\Interlatch\\Dashboard"])
    assert task.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=NS) == "PT0S"
    # every 5 minutes it is started again, which does nothing while it runs (IgnoreNew)
    assert task.findtext("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", namespaces=NS) == "PT5M"
    assert task.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS).endswith("-- ui")


def test_a_dry_run_registers_nothing(cfg, schtasks):
    assert install.install_launchd(cfg, "interlatch", dry_run=True) == [
        "Task Scheduler task \\Interlatch\\Sync running `interlatch sync --work --quiet` every 15 min"]
    assert install.install_ui_agent(cfg, "interlatch", dry_run=True)[0].startswith("Task Scheduler task")
    assert schtasks["calls"] == []


def test_uninstall_ends_and_removes_the_tasks(cfg, schtasks):
    install.install_launchd(cfg, "interlatch")
    install.install_ui_agent(cfg, "interlatch")
    assert install.launchd_status(install.LAUNCHD_LABEL)["installed"]
    assert install.uninstall_launchd() == ["removed Task Scheduler task \\Interlatch\\Sync",
                                           "removed Task Scheduler task \\Interlatch\\Dashboard"]
    assert schtasks["tasks"] == {} and install.uninstall_launchd() == []
    assert install.launchd_status(install.UI_LABEL) == {"installed": False, "loaded": False}


def test_status_reads_the_columns_position_not_their_words(monkeypatch):
    """schtasks prints in Windows' own language: only the column's place and the number are read."""
    row = '"PC","\\Interlatch\\Sync","2026/10/11 12:15:00","準備完了","対話型/バックグラウンド","2026/10/11 12:00:00","{}","ada"'

    def answer(result, rc=0):
        monkeypatch.setattr(windows, "schtasks", lambda *a: subprocess.CompletedProcess(a, rc, row.format(result) + "\n", ""))
        return windows.task_status("Sync")

    assert answer("0") == {"installed": True, "loaded": True, "last_exit": "0"}
    assert answer("1") == {"installed": True, "loaded": True, "last_exit": "1"}
    assert answer("267011") == {"installed": True, "loaded": True}  # has not run yet: no exit code
    assert answer("0", rc=1) == {"installed": False, "loaded": False}


def test_hook_commands_read_the_same_in_git_bash_and_powershell(cfg, monkeypatch):
    """Claude Code runs hooks with Git Bash, or PowerShell without Git for Windows: forward slashes (Bash takes a
    backslash for an escape), and no quotes around the program (PowerShell reads that as a string)."""
    monkeypatch.setattr("platform.system", lambda: "Windows")
    install.install_hooks(cfg, "C:\\Users\\ada\\.local\\bin\\interlatch.exe", inject=True)
    hooks = install._load_settings(install.settings_path(cfg))["hooks"]
    assert hooks["SessionEnd"][0]["hooks"][0]["command"] == "C:/Users/ada/.local/bin/interlatch.exe hook session-end"
    assert install.hooks_installed(cfg) == {"SessionEnd": True, "SessionStart": True}
    install.install_statusline(cfg, "C:\\Users\\ada\\.local\\bin\\interlatch.exe")
    assert install.statusline_installed(cfg)
    # without the `interlatch` command: python -m chronicle, still one word for the program
    monkeypatch.setattr(windows, "short_path", lambda p: "C:\\PROGRA~1\\Python" if p == "C:\\Program Files\\Python" else p)
    assert install._exe_cmd("'C:\\Program Files\\Python\\python.exe' -m chronicle", "statusline") == \
        "C:/PROGRA~1/Python/python.exe -m chronicle statusline"


def test_a_folder_with_a_space_gets_its_short_name(monkeypatch):
    monkeypatch.setattr(windows, "short_path", lambda p: "C:\\Users\\ADALOV~1\\.local\\bin" if " " in p else p)
    exe = "C:\\Users\\Ada Lovelace\\.local\\bin\\interlatch.exe"
    assert windows.shell_command([exe, "hook", "session-end"]) == "C:/Users/ADALOV~1/.local/bin/interlatch.exe hook session-end"
    monkeypatch.setattr(windows, "short_path", lambda p: p)  # a volume that keeps no short names: quotes, for Bash
    assert windows.shell_word(exe) == '"C:/Users/Ada Lovelace/.local/bin/interlatch.exe"'
    assert windows.shell_word("hook") == "hook"


def test_the_runner_starts_the_command_again_when_an_update_asks(tmp_path, monkeypatch):
    runs = []

    class Proc:
        def __init__(self, argv, **kw):
            runs.append((argv, kw["env"]))
            self.code = windows.RESTART_EXIT if len(runs) == 1 else 3

        def wait(self):
            return self.code

    monkeypatch.setattr(windows.subprocess, "Popen", Proc)
    monkeypatch.setattr(windows.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(windows, "_end_with_this_process", lambda proc: None)
    code = windows.run_hidden(["--log", str(tmp_path / "logs" / "ui"), "--home", str(tmp_path / "h"), "--path", str(tmp_path / "claude-bin"),
                               "--", "ui", "--port", "9"])
    assert code == 3 and len(runs) == 2  # the second run's exit is the task's
    argv, env = runs[0]
    assert argv[1:] == ["-X", "utf8", "-m", "chronicle", "ui", "--port", "9"]
    assert env["INTERLATCH_HOME"] == str(tmp_path / "h") and env[windows.RUNNER_ENV] == "1"
    assert env["PATH"].split(windows.os.pathsep)[-1] == str(tmp_path / "claude-bin")
    assert (tmp_path / "logs" / "ui.out.log").exists() and (tmp_path / "logs" / "ui.err.log").exists()


def test_the_file_lock_keeps_a_second_holder_out(tmp_path):
    lock = tmp_path / "locks" / "x.lock"
    with file_lock(lock) as first:
        with file_lock(lock, blocking=False) as second:
            assert first and not second
        with file_lock(lock, timeout=0.3) as third:
            assert not third
        assert lock.read_text().strip().isdigit()  # the holder's pid
    with file_lock(lock, blocking=False) as again:
        assert again


# ------------------------------------------------------------------ on Windows itself
@on_windows
def test_internal_runs_are_known_in_any_case_and_with_either_slash(cfg):
    work = str(cfg.home / "workdir")
    assert cfg.is_internal_path(work.upper()) and cfg.is_internal_path(work.replace("\\", "/") + "/run-1")
    assert not cfg.is_internal_path(work + "-other")


@on_windows
def test_a_real_short_name_has_no_space(tmp_path):
    folder = tmp_path / "with space"
    folder.mkdir()
    (folder / "interlatch.exe").write_bytes(b"")
    word = windows.shell_word(str(folder / "interlatch.exe"))
    assert "\\" not in word and (" " not in word or word.startswith('"'))  # quoted where the volume has no short names


@on_windows
def test_the_runner_runs_a_real_command_in_a_hidden_console(tmp_path):
    code = windows.run_hidden(["--log", str(tmp_path / "v"), "--home", str(tmp_path / "h"), "--", "--version"])
    assert code == 0
    assert "interlatch" in (tmp_path / "v.out.log").read_text().lower()


@on_windows
def test_open_and_reveal_use_windows_own_programs(monkeypatch):
    seen = []
    monkeypatch.setattr(windows.os, "startfile", lambda p: seen.append(("open", p)))
    monkeypatch.setattr(windows.subprocess, "Popen", lambda argv, **kw: seen.append(("reveal", argv)))
    windows.open_path("C:\\x\\report.pdf", reveal=False)
    windows.open_path("C:\\x\\report.pdf", reveal=True)
    assert seen == [("open", "C:\\x\\report.pdf"), ("reveal", ["explorer", "/select,C:\\x\\report.pdf"])]
