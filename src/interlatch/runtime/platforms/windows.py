"""Windows: what launchd and systemd do elsewhere, done with Task Scheduler; and command lines for Claude Code's shell.

`interlatch install` registers two tasks in Task Scheduler's \\Interlatch\\ folder, for this user while they are signed
in: Sync runs `interlatch sync --work --quiet` every 15 minutes, and Dashboard keeps `interlatch ui` running (started
at sign-in, and again within 5 minutes if it stops). Neither shows a window. A task starts pythonw.exe (no console)
with `-m interlatch.windows` (this module's entry point), which runs the real command with python.exe in a console of
its own that has no window: whatever that starts (claude -p, git) shares the console, so nothing flashes up every 15
minutes. Its output goes to logs/, as launchd's StandardOutPath does.

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
    argv = ["-m", "interlatch.windows", "--log", str(log), "--home", str(home)]
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
    """`pythonw -m interlatch.windows --log <prefix> --home <dir> [--path <dir>]... -- <args>`: run `interlatch <args>`
    with python.exe in a console without a window, its output appended to <prefix>.out.log and .err.log. When it
    exits with RESTART_EXIT (an update asked for it), run it again."""
    import argparse

    ap = argparse.ArgumentParser(prog="python -m interlatch.windows")
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
            proc = subprocess.Popen([str(python), "-X", "utf8", "-m", "interlatch", *args], stdin=subprocess.DEVNULL, stdout=out,
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


# ------------------------------------------------------------------ system tray
# Quit under the Dashboard task: Task Scheduler starts the dashboard again within 5 minutes, so Quit leaves this file
# behind (holding the sign-in it was quit in) and `interlatch ui` under the task ends at once while it stands, as the
# macOS menu bar's Quit stops the login item until the next login. `interlatch install` clears it.
QUIT_FILE = "ui-quit"
QUIT_TEXT = ("The dashboard and this icon stop until you next sign in, or until you run `interlatch ui`. "
             "Your sessions are still recorded.")
TRAY_MENU = ((1, "Open Dashboard"), (2, "Sync Now"), (0, None), (3, "Quit Interlatch"))  # (command, label); 0: a line


def mark_quit(home: Path, logon: str | None) -> None:
    if logon:
        (Path(home) / QUIT_FILE).write_text(logon)


def quit_stands(home: Path, logon: str | None) -> bool:
    """The dashboard was quit from the icon in this sign-in (`logon`: logon_id()); unknown, no."""
    try:
        return bool(logon) and (Path(home) / QUIT_FILE).read_text().strip() == logon
    except OSError:
        return False


def clear_quit(home: Path) -> None:
    (Path(home) / QUIT_FILE).unlink(missing_ok=True)


def logon_id() -> str | None:
    """This sign-in's logon session (the AuthenticationId of this process's token): a new one after signing out or a
    restart. None when it can't be told (or not on Windows)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    advapi32.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        return None
    try:
        buf, size = ctypes.create_string_buffer(64), wintypes.DWORD()
        if not advapi32.GetTokenInformation(token, 10, buf, len(buf), ctypes.byref(size)):  # TokenStatistics
            return None
        return buf.raw[8:16].hex()  # TOKEN_STATISTICS: TokenId (LUID), then AuthenticationId (LUID)
    finally:
        kernel32.CloseHandle(token)


def ico_from_pngs(pngs: list[bytes]) -> bytes:
    """An .ico file holding these PNG images (Windows Vista and later read PNG entries), each its own size."""
    import struct

    head = struct.pack("<HHH", 0, 1, len(pngs))
    entries, offset = b"", 6 + 16 * len(pngs)
    for png in pngs:
        w, h = struct.unpack(">II", png[16:24])  # the IHDR chunk
        entries += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(png), offset)  # 256 is written 0
        offset += len(png)
    return head + entries + b"".join(pngs)


def tray_icon_file(home: Path) -> Path | None:
    """Interlatch's icon as an .ico (from the dashboard's own PNGs) in `home`, for the notification area; None if the
    PNGs aren't there."""
    web = Path(__file__).parents[2] / "web"  # the package's web/: interlatch/web
    try:
        pngs = [(web / name).read_bytes() for name in ("favicon.png", "icon.png")]
        out = Path(home) / "tray.ico"
        data = ico_from_pngs(pngs)
        if not out.is_file() or out.read_bytes() != data:
            out.write_bytes(data)
        return out
    except OSError:
        return None


def run_tray_with_server(cfg, httpd, url: str) -> bool:
    """`interlatch ui` with an icon in the notification area: the dashboard serves on a thread while this (main) thread
    runs a Win32 message loop. Left-click opens the dashboard; right-click shows Open Dashboard, Sync Now and Quit. The
    icon comes back when Explorer restarts (TaskbarCreated). False, with nothing started, when it can't be shown."""
    import ctypes
    import logging
    import threading
    import webbrowser
    from ctypes import wintypes

    log = logging.getLogger("interlatch.windows")

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (OSError, AttributeError) as exc:
        log.warning("cannot show the notification-area icon: %s", exc)
        return False

    WM_DESTROY, WM_CLOSE, WM_TIMER = 0x0002, 0x0010, 0x0113
    WM_TRAY = 0x0400 + 20  # WM_USER + 20
    WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
    NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
    NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
    TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
    MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
    MB_OKCANCEL, MB_ICONQUESTION, MB_SETFOREGROUND, IDOK = 0x1, 0x20, 0x10000, 1
    IMAGE_ICON, LR_LOADFROMFILE, SM_CXSMICON, SM_CYSMICON = 1, 0x10, 49, 50

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON),
        ]

    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HICON),
            ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    # 64-bit handles: every handle-returning call gets its restype, or ctypes cuts it to an int
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
    user32.RegisterWindowMessageW.restype = wintypes.UINT
    user32.TrackPopupMenu.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wintypes.HWND, ctypes.c_void_p)
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.AppendMenuW.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR)
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.DestroyMenu.argtypes = (wintypes.HMENU,)
    user32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
    user32.MessageBoxW.restype = ctypes.c_int
    shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.c_void_p)
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    shell32.ExtractIconW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT)
    shell32.ExtractIconW.restype = wintypes.HICON
    user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  wintypes.UINT)
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadIconW.argtypes = (wintypes.HINSTANCE, ctypes.c_void_p)
    user32.LoadIconW.restype = wintypes.HICON
    user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, ctypes.c_void_p)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.RegisterClassW.argtypes = (ctypes.c_void_p,)
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = (wintypes.LPCWSTR, wintypes.HINSTANCE)
    user32.GetMessageW.argtypes = (ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT)
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = (ctypes.c_void_p,)
    user32.DispatchMessageW.argtypes = (ctypes.c_void_p,)
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.DestroyWindow.argtypes = (wintypes.HWND,)
    user32.PostQuitMessage.argtypes = (ctypes.c_int,)
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.GetCursorPos.argtypes = (ctypes.c_void_p,)
    user32.SetTimer.argtypes = (wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p)
    user32.SetTimer.restype = ctypes.c_size_t
    user32.KillTimer.argtypes = (wintypes.HWND, ctypes.c_size_t)

    hinst = kernel32.GetModuleHandleW(None)
    class_name = f"InterlatchTray_{os.getpid()}"
    taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")  # Explorer (re)started: add the icon again
    nid = NOTIFYICONDATAW()

    def open_dashboard() -> None:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()

    def refresh_tip() -> None:
        try:
            from .menubar import snapshot

            snap = snapshot(httpd.app)
            if snap:
                nid.szTip = f"Interlatch: {snap.headline}"[:127]
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:  # noqa: BLE001 (a tooltip is not worth stopping the dashboard for)
            log.debug("tray tooltip not updated", exc_info=True)

    def quit_(hwnd) -> None:
        if os.environ.get(RUNNER_ENV):  # the Dashboard task would start it again within 5 minutes
            if user32.MessageBoxW(hwnd, QUIT_TEXT, "Quit Interlatch?", MB_OKCANCEL | MB_ICONQUESTION | MB_SETFOREGROUND) != IDOK:
                return
            mark_quit(cfg.home, logon_id())
        user32.DestroyWindow(hwnd)

    def menu(hwnd) -> None:
        hmenu, cmd = user32.CreatePopupMenu(), 0
        try:
            for item, label in TRAY_MENU:
                user32.AppendMenuW(hmenu, MF_STRING if item else MF_SEPARATOR, item, label)
            user32.SetForegroundWindow(hwnd)  # or the menu doesn't close when clicking elsewhere
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            cmd = user32.TrackPopupMenu(hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0, hwnd, None)
            user32.PostMessageW(hwnd, 0, 0, 0)
        finally:
            user32.DestroyMenu(hmenu)
        if cmd == 1:
            open_dashboard()
        elif cmd == 2:
            from ...interfaces.web import api

            threading.Thread(target=lambda: api.status.action_sync(httpd.app.context()), daemon=True).start()
        elif cmd == 3:
            quit_(hwnd)

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                open_dashboard()
            elif event == WM_RBUTTONUP:
                menu(hwnd)
            return 0
        if taskbar_created and msg == taskbar_created:
            shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
            return 0
        if msg == WM_TIMER:
            refresh_tip()
            return 0
        if msg in (WM_CLOSE, WM_DESTROY):
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc_ref = WNDPROC(wnd_proc)  # kept referenced while the window lives
    wc = WNDCLASSW()
    wc.lpfnWndProc = proc_ref
    wc.hInstance = hinst
    wc.lpszClassName = class_name
    if not user32.RegisterClassW(ctypes.byref(wc)):
        log.warning("notification-area icon: window class not registered (%s)", ctypes.get_last_error())
        return False
    # a hidden top-level window (not a message-only one): only those hear Explorer's TaskbarCreated broadcast
    hwnd = user32.CreateWindowExW(0, class_name, "Interlatch", 0, 0, 0, 0, 0, None, None, hinst, None)
    if not hwnd:
        user32.UnregisterClassW(class_name, hinst)
        log.warning("notification-area icon: no window (%s)", ctypes.get_last_error())
        return False

    ico = tray_icon_file(cfg.home)
    hicon = user32.LoadImageW(None, str(ico), IMAGE_ICON, user32.GetSystemMetrics(SM_CXSMICON),
                              user32.GetSystemMetrics(SM_CYSMICON), LR_LOADFROMFILE) if ico else None
    if not hicon:
        hicon = shell32.ExtractIconW(hinst, sys.executable, 0)  # Python's own icon
    if not hicon or hicon == 1:
        hicon = user32.LoadIconW(None, 32512)  # IDI_APPLICATION
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = hicon
    nid.szTip = "Interlatch"
    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(class_name, hinst)
        log.warning("notification-area icon not added")
        return False

    threading.Thread(target=httpd.serve_forever, name="interlatch-ui", daemon=True).start()
    log.info("notification-area icon on, dashboard at %s", url)
    refresh_tip()
    user32.SetTimer(hwnd, 1, 10000, None)  # the tooltip's line, every 10 s
    try:
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            user32.KillTimer(hwnd, 1)
            user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(class_name, hinst)
        except Exception:  # noqa: BLE001
            pass
        httpd.shutdown()
        httpd.server_close()
    return True


if __name__ == "__main__":
    raise SystemExit(run_hidden(sys.argv[1:]))
