"""Windows: what launchd and systemd do elsewhere, done with Task Scheduler; and command lines for Claude Code's shell.

`interlatch install` registers two tasks in Task Scheduler's \\Interlatch\\ folder, for this user while they are signed
in: Sync runs `interlatch sync --work --quiet` every 15 minutes, and Dashboard keeps `interlatch ui` running (started
at sign-in, and again within 5 minutes if it stops). Neither shows a window. A task starts pythonw.exe (no console)
with `-m chronicle.windows`, which runs the real command with python.exe in a console of its own that has no window:
whatever that starts (claude -p, git) shares the console, so nothing flashes up every 15 minutes. Its output goes to
logs/, as launchd's StandardOutPath does.

Claude Code runs a hook's command with Git Bash when Git for Windows is installed, else with PowerShell, so the
commands written into its settings read the same in both (shell_command).
"""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

TASK_FOLDER = "\\Interlatch\\"
RUNNER_ENV = "INTERLATCH_WINDOWS_TASK"  # set in the command a task runs: update.restart() asks the runner to restart it
RESTART_EXIT = 75  # the command's exit code that asks the runner for a fresh copy of it (after an update)
# schtasks' Last Result for a task that is running, or has not run yet: not an exit code
NOT_EXITED = {"267009", "267011"}


# ------------------------------------------------------------------ command lines for Claude Code
def short_path(path: str) -> str:
    """The 8.3 form of `path` (no spaces in it), or `path` itself when its volume keeps no short names."""
    import ctypes

    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
    return buf.value if 0 < n < len(buf) else path


def shell_word(arg: str) -> str:
    """`arg` as one word that Git Bash and PowerShell both read the same way. A path gets forward slashes (Bash takes a
    backslash for an escape), and a folder with a space in it its 8.3 name: PowerShell reads a quoted first word as a
    string, not a command, so quotes are the last resort, for a path whose folder has no short name."""
    if "\\" in arg or (len(arg) > 2 and arg[1] == ":"):
        if " " in arg:
            head, _, name = arg.replace("/", "\\").rpartition("\\")
            arg = (short_path(head) if " " in head else head) + "\\" + name
        arg = arg.replace("\\", "/")
    return f'"{arg}"' if " " in arg or not arg else arg


def shell_command(argv: list[str]) -> str:
    return " ".join(shell_word(a) for a in argv)


# ------------------------------------------------------------------ Task Scheduler
def task_name(name: str) -> str:
    return TASK_FOLDER + name


def _user() -> str:
    import getpass

    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{getpass.getuser()}" if domain else getpass.getuser()


def pythonw() -> Path:
    """The windowless python of this environment (python.exe when there is none: a window then shows, nothing else)."""
    exe = Path(sys.executable)
    for name in ("pythonw.exe", exe.name):
        if (exe.parent / name).is_file():
            return exe.parent / name
    return exe


def runner_arguments(args: list[str], *, log: Path, home: Path, path_dirs: list[str]) -> str:
    """pythonw.exe's arguments for a task that runs `interlatch <args>` (run_hidden reads them)."""
    argv = ["-m", "chronicle.windows", "--log", str(log), "--home", str(home)]
    for d in path_dirs:
        argv += ["--path", d]
    return subprocess.list2cmdline([*argv, "--", *args])


def task_xml(*, description: str, command: str, arguments: str, every_minutes: int, keep_alive: bool) -> str:
    """A task for this user while signed in: at sign-in, and every `every_minutes` from now on. A run still going when
    the next is due is left alone (IgnoreNew), so for the dashboard, which never ends, the repeat only restarts it
    after it stopped. Laptops on battery run it too, which Task Scheduler's defaults would not."""
    user = escape(_user())
    start = datetime.now().replace(microsecond=0).isoformat()
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape(description)}</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
    <TimeTrigger>
      <Repetition>
        <Interval>PT{every_minutes}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>{"PT0S" if keep_alive else "PT4H"}</ExecutionTimeLimit>
    <Priority>{6 if keep_alive else 7}</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, errors="replace")


def install_task(name: str, xml: str) -> str | None:
    """Register (or replace) the task and start it now; an error message, or None."""
    fd, tmp = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as fh:  # what Task Scheduler's own exports are in
            fh.write(xml)
        schtasks("/End", "/TN", task_name(name))  # a running copy of the old definition (the job ends its command too)
        proc = schtasks("/Create", "/TN", task_name(name), "/XML", tmp, "/F")
    finally:
        os.unlink(tmp)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
    schtasks("/Run", "/TN", task_name(name))
    return None


def delete_task(name: str) -> bool:
    """End and remove the task; whether there was one."""
    if schtasks("/Query", "/TN", task_name(name)).returncode != 0:
        return False
    schtasks("/End", "/TN", task_name(name))
    schtasks("/Delete", "/TN", task_name(name), "/F")
    return True


def task_status(name: str) -> dict:
    """installed/loaded (registered), and the last run's exit code. The CSV's columns are in a fixed order, while
    their names and the status words are in Windows' own language, so only the position and the number are read."""
    proc = schtasks("/Query", "/TN", task_name(name), "/V", "/FO", "CSV", "/NH")
    info = {"installed": proc.returncode == 0, "loaded": proc.returncode == 0}
    rows = [r for r in csv.reader(proc.stdout.splitlines()) if len(r) > 6] if proc.returncode == 0 else []
    if rows and rows[0][6].strip().lstrip("-").isdigit() and rows[0][6].strip() not in NOT_EXITED:
        info["last_exit"] = rows[0][6].strip()
    return info


# ------------------------------------------------------------------ the runner a task starts
def _end_with_this_process(proc: subprocess.Popen) -> object | None:
    """Put `proc` (and whatever it starts) in a job that ends when this process does: ending the task stops the
    dashboard too, not only the runner. Returns the job's handle, to keep open while `proc` runs."""
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("read_ops", "write_ops", "other_ops", "read", "write", "other")]

    class BasicLimits(ctypes.Structure):
        _fields_ = [("per_process_user_time", ctypes.c_int64), ("per_job_user_time", ctypes.c_int64),
                    ("limit_flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                    ("max_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority_class", wintypes.DWORD), ("scheduling_class", wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t),
                    ("peak_job_memory", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = ExtendedLimits()
    limits.basic.limit_flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))  # ExtendedLimitInformation
    kernel32.AssignProcessToJobObject(job, int(proc._handle))
    return job


def run_hidden(argv: list[str]) -> int:
    """`pythonw -m chronicle.windows --log <prefix> --home <dir> [--path <dir>]... -- <args>`: run `interlatch <args>`
    with python.exe in a console without a window, its output appended to <prefix>.out.log and .err.log. When it
    exits with RESTART_EXIT (an update asked for it), run it again."""
    import argparse

    ap = argparse.ArgumentParser(prog="python -m chronicle.windows")
    ap.add_argument("--log", required=True)
    ap.add_argument("--home")
    ap.add_argument("--path", action="append", default=[])
    ap.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    args = a.args[1:] if a.args[:1] == ["--"] else a.args
    env = {**os.environ, RUNNER_ENV: "1", "PYTHONUTF8": "1"}  # UTF-8 mode, as cli.main would restart it in
    if a.home:
        env["INTERLATCH_HOME"] = a.home
    dirs = env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join(d for d in [*dirs, *(p for p in a.path if p not in dirs)] if d)
    exe = Path(sys.executable)
    python = exe.with_name("python.exe") if exe.with_name("python.exe").is_file() else exe
    Path(a.log).parent.mkdir(parents=True, exist_ok=True)
    while True:
        with open(f"{a.log}.out.log", "ab") as out, open(f"{a.log}.err.log", "ab") as err:
            proc = subprocess.Popen([str(python), "-X", "utf8", "-m", "chronicle", *args], stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=err, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            job = _end_with_this_process(proc)  # noqa: F841 (held open while the command runs)
            code = proc.wait()
        if code != RESTART_EXIT:
            return code


# ------------------------------------------------------------------ files
def open_path(path: str, *, reveal: bool) -> None:
    """Open a file in its app, or show it selected in File Explorer."""
    if reveal:
        subprocess.Popen(["explorer", f"/select,{path}"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    else:
        os.startfile(path)  # noqa: S606 (a recorded document, checked by artifacts.artifact_reveal)


if __name__ == "__main__":
    raise SystemExit(run_hidden(sys.argv[1:]))
