"""Desktop notification of a new Interlatch release (opt-in: `[updates] notify`).

The background sync calls release_check() on every run. It asks pypi.org at most once a day (update.check_due) and
notifies once per release, so a notification that was ignored or dismissed does not come back. macOS posts through
osascript, Linux through notify-send, Windows through PowerShell (a toast); none needs anything installed.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import sys

from ..core.config import Config

log = logging.getLogger("interlatch.notify")

NOTIFIED_KEY = "update_notified"  # kv: the release the last notification was about
UPGRADE = {"uv": "uv tool upgrade interlatch", "pipx": "pipx upgrade interlatch",
           "pip": "pip install -U interlatch"}


def _applescript(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Windows shows a toast only for an app it knows by its AppUserModelID: an unknown one ("Interlatch") shows nothing and
# raises nothing. This is the ID of the Windows PowerShell shortcut that every Windows has (BurntToast uses it too).
WINDOWS_AUMID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
# The title and message come on stdin as JSON (ASCII: json.dumps escapes the rest), never inside the script itself.
# A toast, else (no WinRT) a notification-area balloon; exit 1 when neither could be shown.
WINDOWS_SCRIPT = r"""$ErrorActionPreference = 'Stop'
try {
    $data = [Console]::In.ReadToEnd() | ConvertFrom-Json
    try {
        $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $text = $xml.GetElementsByTagName('text')
        $null = $text.Item(0).AppendChild($xml.CreateTextNode([string]$data.title))
        $null = $text.Item(1).AppendChild($xml.CreateTextNode([string]$data.message))
        $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('%AUMID%').Show($toast)
    } catch {
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $icon = New-Object System.Windows.Forms.NotifyIcon
        $icon.Icon = [System.Drawing.SystemIcons]::Information
        $icon.Visible = $true
        $icon.ShowBalloonTip(10000, [string]$data.title, [string]$data.message, [System.Windows.Forms.ToolTipIcon]::Info)
        Start-Sleep -Seconds 8  # the balloon goes with its icon
        $icon.Dispose()
    }
} catch {
    exit 1
}
""".replace("%AUMID%", WINDOWS_AUMID)


def windows_command(ps: str) -> list[str]:
    """powershell.exe running WINDOWS_SCRIPT: -EncodedCommand (UTF-16LE, base64), so no quoting of the script is needed."""
    import base64

    return [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand",
            base64.b64encode(WINDOWS_SCRIPT.encode("utf-16-le")).decode("ascii")]


def _post_windows(title: str, message: str) -> bool:
    import json

    ps = shutil.which("powershell.exe") or shutil.which("powershell")
    if not ps:
        return False
    try:  # no console window flashes up when the background sync (pythonw) posts it
        # bytes both ways: PowerShell's own errors come back in the console's code page (cp932 on Japanese Windows)
        proc = subprocess.run(windows_command(ps), input=json.dumps({"title": title, "message": message}).encode("ascii"),
                              capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def post(title: str, message: str) -> bool:
    """Show a desktop notification; False when this system has no way to (or it failed)."""
    if platform.system() == "Darwin":
        cmd = ["osascript", "-e", f"display notification {_applescript(message)} with title {_applescript(title)}"]
    elif sys.platform == "win32" or platform.system() == "Windows":
        return _post_windows(title, message)
    elif shutil.which("notify-send"):
        cmd = ["notify-send", "--app-name=Interlatch", title, message]
    else:
        return False
    try:
        return subprocess.run(cmd, capture_output=True, timeout=10, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def how_to_update(cfg: Config, kind: str) -> str:
    if kind == "app":
        return "Open Interlatch › Status › Updates to download it."
    where = f"the dashboard's Status › Updates (http://127.0.0.1:{cfg.server_port}/#/status?focus=updates)"
    return f"Update from {where}, or run: {_upgrade(kind)}" if kind in UPGRADE else f"Update from {where}."


def _upgrade(kind: str) -> str:
    """The command that updates this install; for one of the agents-chronicle package, the move to interlatch."""
    from .update import check, install_method

    return (check()["command"] if install_method().get("move") else None) or UPGRADE[kind]


def release_check(cfg: Config, conn) -> str | None:
    """Notify about a release newer than this copy, once per release. Returns the version notified about."""
    from . import update
    from ..storage.kv import kv_get, kv_set

    if not cfg.update_notify or not update.compares_online():
        return None
    update.recall(conn)  # the dashboard's checks count too
    if update.check_due():
        update.fetch_latest()
        update.remember(conn)
    offer = update.available()
    if not offer or kv_get(conn, NOTIFIED_KEY) == offer["key"]:
        return None
    if not post(f"Interlatch {offer['to']} is available", how_to_update(cfg, update.install_method()["kind"])):
        log.info("no way to show a notification for Interlatch %s here", offer["to"])
        return None
    kv_set(conn, NOTIFIED_KEY, offer["key"])
    conn.commit()
    log.info("notified about Interlatch %s", offer["to"])
    return offer["to"]


def release_check_safely(cfg: Config) -> None:
    """For the background sync: never let a notification problem fail the sync."""
    from ..storage.sqlite import connect

    if not cfg.update_notify:
        return
    try:
        conn = connect(cfg.db_path)
        try:
            release_check(cfg, conn)
        finally:
            conn.close()
    except Exception:
        log.exception("release notification check failed")
