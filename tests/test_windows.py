"""Windows: the Task Scheduler tasks, the hidden runner, hook command lines, and the file lock. Most of it runs on any
system with Windows' answers faked; the tests marked `on_windows` need the real thing (the Windows CI job)."""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from interlatch import install, windows
from interlatch.core.config import load_config
from interlatch.core.util import file_lock

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
    assert args.startswith("-m interlatch.windows --log ") and args.endswith("-- sync --work --quiet")
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
    # without the `interlatch` command: python -m interlatch, still one word for the program
    monkeypatch.setattr(windows, "short_path", lambda p: "C:\\PROGRA~1\\Python" if p == "C:\\Program Files\\Python" else p)
    assert install._exe_cmd("'C:\\Program Files\\Python\\python.exe' -m interlatch", "statusline") == \
        "C:/PROGRA~1/Python/python.exe -m interlatch statusline"


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
    assert argv[1:] == ["-X", "utf8", "-m", "interlatch", "ui", "--port", "9"]
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


# ------------------------------------------------------------------ Windows paths (on any system)
def test_windows_paths_are_told_apart_and_spelled_one_way():
    from interlatch.core.paths import clean_abs_path, is_abs_path, normalize_project_path, path_kind, path_variants

    for p in ("C:\\Users\\ada\\project", "c:/work/app", "\\\\server\\share\\repo", "//server/share", "/Users/ada", "/c:/x"):
        assert is_abs_path(p), p
    for p in ("relative/path", "", None, "C:", "C:relative", "1:/x", "::/x", "é:/x", "\\\\", "\\x"):
        assert not is_abs_path(p), p  # a drive is an ASCII letter
    assert [path_kind(p) for p in ("C:\\x", "/c:/x", "\\\\s\\x", "//s/x", "/x", "x")] == ["drive", "drive", "unc", "unc",
                                                                                         "posix", None]
    # one spelling: forward slashes, an upper-case drive letter (the rest's case kept), no trailing separator
    assert normalize_project_path("C:\\Users\\Ada\\Project\\") == "C:/Users/Ada/Project"
    assert normalize_project_path("c:/Users/Ada") == normalize_project_path("C:\\Users\\Ada") == "C:/Users/Ada"
    assert normalize_project_path("/c:/Users/Ada") == "C:/Users/Ada"  # a VS Code file URI's path
    assert [normalize_project_path(p) for p in ("C:\\", "c:/", "/c:")] == ["C:/"] * 3
    assert normalize_project_path("\\\\server\\share\\repo\\") == "//server/share/repo"
    # a POSIX path keeps its backslashes: there they are part of a name
    assert normalize_project_path("/Users/ada/a\\b/") == "/Users/ada/a\\b"
    assert normalize_project_path("/") == "/" and normalize_project_path("relative/path/") == "relative/path/"
    assert path_variants("c:\\work\\app") == ["C:/work/app", "C:\\work\\app", "/C:/work/app",
                                              "c:/work/app", "c:\\work\\app", "/c:/work/app"]
    assert path_variants("\\\\s\\share") == ["//s/share", "\\\\s\\share"] and path_variants("/a\\b") == ["/a\\b"]
    assert clean_abs_path("C:\\work\\.\\app\\..\\lib\\x.py") == "C:/work/lib/x.py"
    assert clean_abs_path("/a/./b/../c") == "/a/c" and clean_abs_path("c:\\") == "C:/" and clean_abs_path("x.py") == ""


def test_the_sql_filter_keeps_absolute_paths_of_either_kind():
    import sqlite3

    from interlatch.core.paths import abs_path_sql

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (p TEXT)")
    rows = ["/x", "C:\\x", "c:/x", "\\\\s\\x", "x/y", "x", None]
    conn.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    assert [r[0] for r in conn.execute(f"SELECT p FROM t WHERE {abs_path_sql('p')}")] == rows[:4]
    assert [r[0] for r in conn.execute(f"SELECT p FROM t WHERE NOT {abs_path_sql('p')}")] == ["x/y", "x"]


def test_under_compares_windows_paths_by_one_spelling_and_never_across_kinds():
    from interlatch.hub import under

    assert under("C:\\Users\\ada\\project\\sub\\file.py", "C:/Users/ada/project")
    assert under("c:/users/ada/Project", "C:\\")  # the drive letter's case doesn't matter
    assert under("C:/Users/ada/project", "C:\\Users\\ada\\project\\")
    assert not under("C:/Users/ada/project2", "C:/Users/ada/project")
    assert not under("C:/Users/Ada/project", "C:/Users/ada/project")  # only the drive letter is case-folded
    assert not under("D:/Users/ada/project", "C:/")
    # a POSIX folder never holds a drive or a UNC path, nor the other way round
    assert not under("C:/work/app", "/") and not under("\\\\server\\share\\app", "/")
    assert not under("//server/share/app", "/") and not under("/Users/ada", "C:/")
    assert not under("/C:/x", "/")  # a VS Code URI's drive is a drive
    assert under("\\\\server\\share\\app", "//server/share") and not under("//server/share2", "//server/share")
    assert under("/Users/ada/app", "/") and under("/Users/ada/a\\b/c", "/Users/ada/a\\b")
    # no folder holds nothing (it used to hold every absolute path)
    assert not under("/Users/ada", "") and not under("C:/x", "") and not under("", "/") and not under(None, "/")


def test_windows_hub_and_people_path_normalization():
    from interlatch.hub import clean_folders, clean_left
    from interlatch.people import PeopleError, clean_projects

    folders = clean_folders({"C:\\Users\\ada\\app": "c:\\hub\\projects\\app\\", "/posix/path/": "/hub/posix/",
                             "1:\\x": "C:/y", "/a\\b": "/hub/ab"})
    assert folders == {"C:/Users/ada/app": "C:/hub/projects/app", "/posix/path": "/hub/posix", "/a\\b": "/hub/ab"}
    left = clean_left(["C:\\Users\\ada\\app\\", "c:/Users/ada/app", "/posix/proj", "1:/x", "relative"])
    assert left == ["/posix/proj", "C:/Users/ada/app"]
    assert clean_projects(["C:\\Users\\ada\\app", "c:/Users/ada/app", "/var/repo"]) == ["/var/repo", "C:/Users/ada/app"]
    with pytest.raises(PeopleError):
        clean_projects(["1:/x"])


def test_the_hub_files_a_windows_computer_s_folder_under_one_spelling(tmp_path):
    from interlatch.storage.sqlite import connect
    from interlatch.hub import ProjectResolver

    cfg = load_config(tmp_path / "home")
    conn = connect(cfg.db_path)
    r = ProjectResolver(cfg, conn)
    assert r.resolve("windows-pc", "c:\\work\\app") == r.resolve("windows-pc", "C:/work/app\\") == "C:/work/app"
    assert r.resolve("windows-pc", "/Users/ada/a\\b") == "/Users/ada/a\\b"  # a POSIX path as it is
    assert r.resolve(r.me, "c:\\work\\app") == "c:\\work\\app"  # this computer's own sessions: as recorded
    conn.close()


def _windows_transcript(sid: str, cwd: str) -> str:
    def line(**kw):
        return json.dumps({"isSidechain": False, "userType": "external", "entrypoint": "cli", "cwd": cwd,
                           "sessionId": sid, "version": "2.1.282", **kw})

    def tool(n, ts, name, inp, result):
        return [line(type="assistant", uuid=f"a{n}", timestamp=ts, message={
                    "id": f"msg_{n}", "model": "claude-opus-5-5", "role": "assistant",
                    "content": [{"type": "tool_use", "id": f"toolu_{n}", "name": name, "input": inp}],
                    "usage": {"input_tokens": 5, "output_tokens": 5}}),
                line(type="user", uuid=f"r{n}", timestamp=ts.replace(":10.", ":12."), message={
                    "role": "user", "content": [{"type": "tool_result", "tool_use_id": f"toolu_{n}", "content": result}]})]

    lines = [line(type="user", uuid="u1", timestamp="2026-10-14T10:00:00.000Z",
                  message={"role": "user", "content": "Fix main.py"})]
    lines += tool(1, "2026-10-14T10:01:10.000Z", "Read", {"file_path": f"{cwd}\\src\\util.py"}, "x = 1")
    lines += tool(2, "2026-10-14T10:02:10.000Z", "Edit",
                  {"file_path": f"{cwd}\\main.py", "old_string": "a", "new_string": "b"}, "The file was updated")
    return "\n".join(lines) + "\n"


def test_a_real_windows_transcript_s_backslash_paths_are_found(tmp_path, monkeypatch):
    """A Claude Code session on Windows records C:\\work\\app; the VS Code extension asks with c:\\ (fsPath) or C:/."""
    from interlatch.storage.sqlite import connect
    from interlatch.ingestion.service import sync
    from interlatch.server import App

    for name in ("COPILOT_HOME", "BOB_HOME", "ANTIGRAVITY_HOME"):
        monkeypatch.setenv(name, str(tmp_path / "none"))
    monkeypatch.setattr("interlatch.connectors.vscode_user_dirs", lambda: [])
    sid = "33333333-2222-3333-4444-555555555555"
    claude = tmp_path / "claude"
    proj = claude / "projects" / "C--work-app"
    proj.mkdir(parents=True)
    (proj / f"{sid}.jsonl").write_text(_windows_transcript(sid, "C:\\work\\app"))
    cfg = load_config(tmp_path / "home")
    cfg.claude_dirs, cfg.codex_dirs = [claude], []
    cfg.ensure_dirs()
    conn = connect(cfg.db_path)
    sync(cfg, conn)
    assert conn.execute("SELECT project_path FROM sessions WHERE id = ?", (sid,)).fetchone()[0] == "C:\\work\\app"
    assert sorted(r[0] for r in conn.execute("SELECT path FROM session_files WHERE session_id = ?", (sid,))) == [
        "C:\\work\\app\\main.py", "C:\\work\\app\\src\\util.py"]  # stored as recorded: matched when asked
    # Codex on Windows: a path relative to the session's folder, with a backslash
    conn.execute("INSERT INTO sessions(id, agent, project_path, started_at) VALUES ('codex-w', 'codex', 'c:\\work\\app', "
                 "'2026-10-15T09:00:00Z')")
    conn.executemany("INSERT INTO session_files(session_id, path, reads, edits) VALUES (?,?,?,?)",
                     [("codex-w", "src\\util.py", 1, 0), ("codex-w", "main.py", 0, 1)])
    conn.commit()
    conn.close()

    app = App(cfg)
    for asked in ("C:\\work\\app\\main.py", "c:\\work\\app\\main.py", "C:/work/app/main.py", "/c:/work/app/main.py"):
        found = app.file_sessions(asked)
        assert found["path"] == "C:/work/app/main.py" and [s["id"] for s in found["sessions"]] == ["codex-w", sid], asked
    assert next(s for s in found["sessions"] if s["id"] == sid)["file"]["changes"] == 1
    assert [s["id"] for s in app.file_sessions("c:\\work\\app\\src\\util.py")["sessions"]] == ["codex-w", sid]
    assert app.file_sessions("D:\\work\\app\\main.py")["sessions"] == []
    for root in ("C:\\work\\app", "c:\\work\\app\\", "C:/work/app"):
        listed = app.folder_files(root)
        assert listed["root"] == "C:/work/app"
        assert sorted((f["rel"], f["sessions"]) for f in listed["files"]) == [("main.py", 2), ("src/util.py", 2)], root
        assert all(f["path"] == "C:/work/app/" + f["rel"] for f in listed["files"])
    assert [f["rel"] for f in app.folder_files("C:\\work\\app\\src")["files"]] == ["util.py"]
    assert app.folder_files("C:\\")["files"] == [] and app.folder_files("c:")["files"] == []
    assert app.folder_files("C:\\work\\ap")["files"] == []  # a folder that only shares the prefix


def test_copilot_s_file_uris_give_windows_paths():
    from interlatch.sources.builder import uri_path

    assert uri_path("file:///c%3A/Users/ada/app") == "c:/Users/ada/app"
    assert uri_path({"path": "/c:/Users/ada/app"}) == "c:/Users/ada/app"
    assert uri_path({"fsPath": "c:\\Users\\ada\\app", "path": "/c:/Users/ada/app"}) == "c:\\Users\\ada\\app"
    assert uri_path("file:///Users/ada/app") == "/Users/ada/app" and uri_path("file:///c%3A") == "c:"


# ------------------------------------------------------------------ the notification-area icon (pure parts)
def test_the_tray_icon_is_interlatch_s_own(tmp_path):
    import struct

    png = (Path(windows.__file__).parent / "web" / "favicon.png").read_bytes()
    ico = windows.ico_from_pngs([png])
    assert struct.unpack("<HHH", ico[:6]) == (0, 1, 1)
    w, h, _, _, planes, bits, size, offset = struct.unpack("<BBBBHHII", ico[6:22])
    assert (w, h, planes, bits, size, offset) == (32, 32, 1, 32, len(png), 22) and ico[22:] == png
    path = windows.tray_icon_file(tmp_path)
    data = path.read_bytes()
    assert path.name == "tray.ico" and struct.unpack("<H", data[4:6]) == (2,)
    assert data[6] == 32 and data[22] == 0  # 32 px, and 256 px (written 0)


def test_quit_from_the_icon_lasts_until_the_next_sign_in(tmp_path, monkeypatch):
    windows.mark_quit(tmp_path, "logon-1")
    assert windows.quit_stands(tmp_path, "logon-1")
    assert not windows.quit_stands(tmp_path, "logon-2")  # signed in again
    assert not windows.quit_stands(tmp_path, None)  # can't tell: start
    windows.clear_quit(tmp_path)
    assert not windows.quit_stands(tmp_path, "logon-1")
    windows.mark_quit(tmp_path, None)
    assert not (tmp_path / windows.QUIT_FILE).exists()
    if sys.platform != "win32":
        assert windows.logon_id() is None


def test_the_dashboard_task_does_not_start_again_after_quit(tmp_path, monkeypatch):
    from interlatch import server, update

    cfg = load_config(tmp_path / "home")
    monkeypatch.setattr(update, "RESTARTABLE", update.RESTARTABLE)  # serve() sets it
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(windows, "logon_id", lambda: "logon-1")
    monkeypatch.setattr(server, "make_server", lambda *a, **k: pytest.fail("started after Quit"))
    windows.mark_quit(cfg.home, "logon-1")
    monkeypatch.setenv(windows.RUNNER_ENV, "1")
    server.serve(cfg)  # returns at once
    monkeypatch.delenv(windows.RUNNER_ENV)
    with pytest.raises(pytest.fail.Exception):
        server.serve(cfg)  # `interlatch ui` by hand starts it


def test_installing_the_dashboard_task_undoes_a_quit(cfg, schtasks):
    windows.mark_quit(cfg.home, "logon-1")
    install.install_ui_agent(cfg, "C:\\Users\\ada\\.local\\bin\\interlatch.exe")
    assert not windows.quit_stands(cfg.home, "logon-1")


def test_windows_menubar_setting_and_wants(cfg, monkeypatch):
    from interlatch import menubar

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(windows, "task_status", lambda name: {"installed": True, "loaded": True})
    monkeypatch.setenv("INTERLATCH_WINDOWS_TASK", "1")

    info = menubar.setting_info(cfg)
    assert info["supported"] is True
    assert info["app_extra"] is True
    assert info["login_item"] is True
    assert info["agent"] is True

    cfg.server_menu_bar = True
    assert menubar.wants_menu_bar(cfg) is True
    monkeypatch.delenv("INTERLATCH_WINDOWS_TASK", raising=False)
    assert menubar.wants_menu_bar(cfg) is False


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


@on_windows
def test_the_notification_script_parses_in_windows_powershell(tmp_path):
    from interlatch import notify

    script = tmp_path / "toast.ps1"
    script.write_text(notify.WINDOWS_SCRIPT, encoding="utf-8-sig")
    check = (f"$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$null, "
             "[ref]$e); $e | ForEach-Object { $_.Message }; exit $e.Count")
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", check], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@on_windows
def test_the_sign_in_is_known_and_the_tray_icon_loads(tmp_path):
    import ctypes
    from ctypes import wintypes

    assert windows.logon_id() and windows.logon_id() == windows.logon_id()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  wintypes.UINT)
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.DestroyIcon.argtypes = (wintypes.HICON,)
    for size in (16, 32):
        icon = user32.LoadImageW(None, str(windows.tray_icon_file(tmp_path)), 1, size, size, 0x10)  # IMAGE_ICON, from file
        assert icon, ctypes.get_last_error()
        user32.DestroyIcon(icon)
