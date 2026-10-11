"""The menu-bar item on macOS: Interlatch's mark, which says at a glance whether it is working or needs a look; a click
opens a panel (web/panel.html in a popover: the status, numbers, search and recent sessions), a right-click a quick
native menu.

`interlatch ui` shows it when launchd runs it as the login item (or with --menu-bar), and Interlatch.app (desktop.py)
shows the same with its own menu items added. What it says is worked out without AppKit (snapshot(), summarize());
StatusMenu draws it.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlparse

from .config import Config
from .util import parse_ts, to_iso, utcnow

log = logging.getLogger("interlatch.menubar")

OK, WORKING, ATTENTION, PAUSED = "ok", "working", "attention", "paused"
RECENT_N = 6
POLL_S = 30  # how often the icon catches up with what other processes did
POLL_BUSY_S = 4  # while something runs, so the icon settles soon after it ends
TITLE_MAX = 48
# imported chats are not work done on this computer; the hub's copies of other computers' sessions are ("remote")
RECENT_SKIP = ("history", "chatgpt-export", "claude-ai-export")
JOB_LABELS = {"sync": "Syncing", "push": "Sending to the hub", "import": "Importing chats",
              "screen": "Screening imported chats", "update": "Updating Interlatch", "themes": "Grouping glossary themes",
              "analyze": "Analyzing", "glossary": "Updating the glossary", "review": "Writing the weekly review",
              "synthesize": "Updating knowledge", "menu-bar": "Setting up the menu-bar icon"}
# the panel's picture for each state, from the dashboard's 3D cast (web/art-*.webp)
ART = {OK: "art-book.webp", WORKING: "art-gears.webp", ATTENTION: "art-glitch.webp", PAUSED: "art-clock.webp"}


@dataclass
class Snapshot:
    state: str
    title: str  # "All caught up", "Syncing", "Sync failed"
    sub: str = ""  # "Synced 5 min ago", "2 of 5", the error
    detail: str = ""  # the analysis queue, for the native menu
    hub: str = ""  # a computer that shares knowledge with a hub: what it last sent
    recent: list[dict] = field(default_factory=list)
    update: str | None = None  # the version on offer, "build" for a changed checkout
    syncing: bool = False
    progress: tuple[int, int] | None = None  # (done, total) of the running job
    stats: dict = field(default_factory=dict)  # today, waiting, lessons (the panel's tiles)
    note: str = ""  # what stops the analysis queue, when something does

    @property
    def headline(self) -> str:
        """One line, for the native menu and the icon's tooltip."""
        return f"{self.title} · {self.sub}" if self.sub else self.title

    def to_page(self) -> dict:
        """What the panel (web/panel.js) renders."""
        return {"state": self.state, "title": self.title, "sub": self.sub, "hub": self.hub, "recent": self.recent,
                "update": self.update, "syncing": self.syncing, "stats": self.stats, "note": self.note,
                "art": ART[self.state], "progress": list(self.progress) if self.progress else None}


def when(iso: str | None, now: datetime | None = None) -> str:
    """'at 14:05' today, 'Oct 8 at 14:05' before."""
    dt = parse_ts(iso)
    if dt is None:
        return ""
    dt, today = dt.astimezone(), (now or utcnow()).astimezone().date()
    return f"at {dt:%H:%M}" if dt.date() == today else f"{dt:%b} {dt.day} at {dt:%H:%M}"


def ago(iso: str | None, now: datetime | None = None) -> str:
    """'just now', '5 min ago', '3 h ago', 'yesterday', '3 days ago', then the date."""
    dt = parse_ts(iso)
    if dt is None:
        return ""
    now = now or utcnow()
    secs = (now - dt).total_seconds()
    days = (now.astimezone().date() - dt.astimezone().date()).days
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if days == 0:
        return f"{int(secs // 3600)} h ago"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    local = dt.astimezone()
    return f"{local:%b} {local.day}"


def day_label(iso: str | None, now: datetime | None = None) -> str:
    """The heading a session is listed under: Today, Yesterday, or its date."""
    dt = parse_ts(iso)
    if dt is None:
        return ""
    days = ((now or utcnow()).astimezone().date() - dt.astimezone().date()).days
    local = dt.astimezone()
    return "Today" if days == 0 else "Yesterday" if days == 1 else f"{local:%a}, {local:%b} {local.day}"


def _short(text: str, n: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _n(n: int, one: str) -> str:
    return f"{n} {one}{'' if n == 1 else 's'}"


def summarize(st: dict, *, now: datetime | None = None, analyzing: int = 0, sync_running: bool = False,
              sync_error: str | None = None, push: dict | None = None, hub_name: str | None = None,
              sends_files: bool = False) -> Snapshot:
    """The icon's state and what the panel's header says, from the dashboard's status (App.status_small()).

    `analyzing`: sessions being analyzed by any process; `sync_running`/`sync_error`: the sync that runs outside the
    dashboard (the app's own loop, or the launchd agent); `push`: a spoke's last push (hub.last_push()),
    `hub_name` what the hub calls itself, `sends_files` whether the hub analyzes this computer's sessions
    (Config.sends_files) or only gets the knowledge learned here."""
    now = now or utcnow()
    jobs = {name: j for name, j in (st.get("jobs") or {}).items() if j.get("state") == "running"}
    failed = (st.get("jobs") or {}).get("sync") or {}
    syncing = sync_running or "sync" in jobs or "push" in jobs
    pending = st.get("pending") or {}
    queued = 0 if sends_files else int(pending.get("queued") or 0)  # else the hub analyzes them
    detail = f"{_n(queued, 'session')} waiting for analysis" if queued else ""
    hub = (hub_name or urlparse(st["hub_url"]).hostname or st["hub_url"]) if st.get("hub_url") else ""
    sent = ago((push or {}).get("at"), now)
    shared = (f"Sharing knowledge with {hub}" + (f" · last sent {sent}" if sent else " · nothing sent yet")
              if hub and not sends_files else "")
    note = _short(pending.get("block") or "", 120) if queued and not sends_files else ""

    def snap(state: str, title: str, sub: str = "", progress: tuple[int, int] | None = None) -> Snapshot:
        return Snapshot(state, title, sub, detail, shared, update=(st.get("update") or {}).get("to"),
                        syncing=syncing, progress=progress, stats={"waiting": None if sends_files else queued},
                        note=note)

    if sync_error:
        return snap(ATTENTION, "Sync failed", _short(sync_error, 90))
    if failed.get("state") == "error":
        return snap(ATTENTION, "Sync failed", _short(failed.get("result") or "see the logs", 90))
    if push and push.get("errors"):
        return snap(ATTENTION, "Sending to the hub failed", _short(push["errors"][0], 90))
    if jobs or syncing or analyzing:
        name, job = next(iter(jobs.items()), ("sync" if syncing else "analyze", {}))
        label = JOB_LABELS.get(name.split(":", 1)[0], "Working")
        message = _short(job.get("message") or "", 60)
        if job.get("total"):
            done = int(job.get("done") or 0)
            return snap(WORKING, label, f"{done} of {job['total']}", progress=(done, int(job["total"])))
        if label == "Analyzing" and analyzing:
            return snap(WORKING, f"Analyzing {_n(analyzing, 'session')}", message)
        return snap(WORKING, label, message)
    paused = st.get("paused_until")
    if paused and paused > to_iso(now):
        return snap(PAUSED, "Analysis paused", f"Resumes {when(paused, now)} (usage limit)")
    if hub and sends_files:
        return snap(OK, f"Sending to {hub}", f"Last sent {sent}" if sent else "Nothing sent yet")
    synced = f"Synced {ago(st.get('last_sync'), now)}" if st.get("last_sync") else "Not synced yet"
    if queued:
        return snap(OK, f"{_n(queued, 'session')} to analyze", synced)
    return snap(OK, "All caught up", synced)


def recent_sessions(conn, n: int = RECENT_N, now: datetime | None = None) -> list[dict]:
    marks = ",".join("?" * len(RECENT_SKIP))
    rows = [dict(r) for r in conn.execute(
        f"SELECT id, title, project_name, agent, outcome, analysis_status, COALESCE(ended_at, started_at) at "
        f"FROM sessions WHERE source NOT IN ({marks}) AND COALESCE(ended_at, started_at) IS NOT NULL "
        f"ORDER BY COALESCE(ended_at, started_at) DESC LIMIT ?", [*RECENT_SKIP, n])]
    for r in rows:
        local = parse_ts(r["at"]).astimezone()
        r["day"], r["time"] = day_label(r["at"], now), f"{local:%H:%M}"
    return rows


def stats(conn, now: datetime | None = None) -> dict:
    """Sessions since midnight and lessons learned in the last seven days."""
    now = now or utcnow()
    midnight = to_iso(now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0))
    marks = ",".join("?" * len(RECENT_SKIP))
    today = conn.execute(f"SELECT COUNT(*) FROM sessions WHERE source NOT IN ({marks}) "
                         f"AND COALESCE(ended_at, started_at) >= ?", [*RECENT_SKIP, midnight]).fetchone()[0]
    lessons = conn.execute("SELECT COUNT(*) FROM knowledge WHERE status = 'active' AND source = 'analysis' "
                           "AND created_at >= ?", [to_iso(now - timedelta(days=7))]).fetchone()[0]
    return {"today": today, "lessons": lessons}


def snapshot(app, *, sync_running: bool = False, sync_error: str | None = None) -> Snapshot:
    """What the menu shows now. `app`: the dashboard's server.App, read on the caller's thread."""
    from .hub import last_folders, last_push

    try:
        app.refresh_config()
        st = app.status_small()
        conn = app.conn
        analyzing = conn.execute("SELECT COUNT(*) FROM sessions WHERE analysis_status = 'running'").fetchone()[0]
        spoke = app.cfg.is_spoke
        snap = summarize(st, analyzing=analyzing, sync_running=sync_running, sync_error=sync_error,
                         push=last_push(app.cfg) if spoke else None, sends_files=app.cfg.sends_files,
                         hub_name=(last_folders(app.cfg) or {}).get("hub") if spoke else None)
        snap.recent = recent_sessions(conn)
        snap.stats.update(stats(conn))
        return snap
    finally:
        app.release()  # no read transaction held between polls


def agent_sync_error() -> str | None:
    """The launchd sync agent's last run, when it failed (the login item's own sync runs there, not in the menu)."""
    from .install import background_status

    info = background_status()
    code = str(info.get("last_exit") or "0")
    if not info.get("loaded") or code in ("0", "(never exited)"):
        return None
    return f"the background sync exited with code {code} (see sync.err.log)"


SHOWN = False  # this process shows the icon (run_with_server started it)


def app_extra() -> bool:
    """The `app` extra (PyObjC) is installed: the icon can show."""
    import importlib.util

    return importlib.util.find_spec("AppKit") is not None


def extra_command() -> list[str] | None:
    """The command that adds the `app` extra to a uv tool install from PyPI, keeping its other extras and its version;
    None for any other install (a checkout, pip, pipx, the app), which Status › Recording explains instead."""
    from . import __version__
    from .update import DIST, install_method

    m = install_method()
    if m["kind"] != "uv" or m.get("source") or not m.get("command"):
        return None
    extras = ",".join(sorted({*m.get("extras", []), "app"}))
    return [m["command"][0], "tool", "install", "--force", "--python", f"{sys.version_info[0]}.{sys.version_info[1]}",
            f"{DIST}[{extras}]=={__version__}"]


def setting_info(cfg: Config) -> dict:
    """Status › Recording's menu-bar switch: whether it applies here, and what turning it on takes."""
    import shlex

    from .install import UI_LABEL, launchd_status
    from .update import install_method

    if sys.platform != "darwin" or install_method()["kind"] in ("app", "container"):  # the app always shows it
        return {"supported": False}
    cmd = extra_command()
    return {"supported": True, "on": cfg.server_menu_bar, "shown": SHOWN, "app_extra": app_extra(),
            "login_item": os.environ.get("XPC_SERVICE_NAME") == UI_LABEL,  # this dashboard is the one that shows it
            "agent": bool(launchd_status(UI_LABEL).get("loaded")), "can_install": cmd is not None,
            "command": shlex.join(cmd) if cmd else "uv tool install --force --python 3.13 'interlatch[app]'",
            # what System Settings › Menu Bar calls it: the Python it runs on (python3.14), not Interlatch
            "listed_as": os.path.basename(os.path.realpath(sys.executable))}


def restarts(on: bool, info: dict) -> bool:
    """Whether turning the icon on or off restarts this dashboard: only the login item shows it, and only a fresh
    process picks up the setting (and an `app` extra just installed)."""
    from . import update

    return bool(update.RESTARTABLE and info.get("login_item") and (on != info.get("shown") or (on and not info.get("app_extra"))))


def apply_setting(cfg: Config, on: bool, progress) -> str:
    """Turn the icon on or off ([server] menu_bar), adding the `app` extra first when it is missing, then restart the
    dashboard so it shows or goes. A job (Status › Recording); its result is the line the UI shows."""
    from . import update
    from .config import set_config_value
    from .i18n import tr

    info = setting_info(cfg)
    if on and not info.get("app_extra"):
        cmd = extra_command()
        if not cmd:
            raise RuntimeError(tr("The menu-bar icon needs the app extra: {command}", command=info.get("command", "")))
        progress(tr("installing the app extra…"))
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)
        update._method.clear()  # the reinstall rewrote the receipt
        if r.returncode:
            out = (r.stdout + r.stderr).strip()
            raise RuntimeError(out.splitlines()[-1] if out else f"{cmd[0]} exited with {r.returncode}")
    set_config_value(cfg, "server", "menu_bar", "true" if on else "false")
    if restarts(on, info):
        threading.Timer(3.0, update.restart).start()  # after the next status poll has seen this job finish
        return tr("The menu-bar icon is on; the dashboard is restarting") if on else tr("The menu-bar icon is off; the dashboard is restarting")
    if not on:
        return tr("The menu-bar icon is off")
    return tr("The menu-bar icon is on") if info.get("shown") else tr("The menu-bar icon is on; it shows while the dashboard runs at login")


def wants_menu_bar(cfg: Config) -> bool:
    """`interlatch ui` shows the icon by itself only as the login item, so a second dashboard run from a terminal
    (or a checkout's dev server) adds no second icon."""
    from .install import UI_LABEL

    return (sys.platform == "darwin" and cfg.server_menu_bar
            and os.environ.get("XPC_SERVICE_NAME") == UI_LABEL)


# ---------------------------------------------------------------------------------------------- AppKit from here on
def alert(title: str, text: str, buttons: list[str]) -> int:
    """Modal alert from a background thread; returns the index of the button pressed."""
    import AppKit
    from PyObjCTools import AppHelper

    done, result = threading.Event(), [0]

    def run():
        try:
            a = AppKit.NSAlert.alloc().init()
            a.setMessageText_(title)
            a.setInformativeText_(text)
            for b in buttons:
                a.addButtonWithTitle_(b)
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            result[0] = int(a.runModal()) - AppKit.NSAlertFirstButtonReturn
        finally:
            done.set()

    AppHelper.callAfter(run)
    done.wait()
    return result[0]


def mark(state: str = OK, size: float = 18.0):
    """Interlatch's mark as a template image (macOS tints it for the menu bar): the stack of pages from the app icon,
    with a dot while it works, a "!" when it needs a look, and dimmed while analysis is paused."""
    import AppKit

    def draw(rect) -> bool:
        s = size / 18.0
        AppKit.NSColor.blackColor().set()

        def page(x, y, w, h, fold=0.0):
            p = AppKit.NSBezierPath.bezierPath()
            p.moveToPoint_((x * s, y * s))
            p.lineToPoint_(((x + w - fold) * s, y * s))
            p.lineToPoint_(((x + w) * s, (y + fold) * s))
            p.lineToPoint_(((x + w) * s, (y + h) * s))
            p.lineToPoint_((x * s, (y + h) * s))
            p.closePath()
            p.setLineWidth_(1.3 * s)
            p.setLineJoinStyle_(AppKit.NSLineJoinStyleRound)
            return p

        def clear(path) -> None:
            ctx = AppKit.NSGraphicsContext.currentContext()
            ctx.saveGraphicsState()
            ctx.setCompositingOperation_(AppKit.NSCompositingOperationClear)
            path.fill()
            ctx.restoreGraphicsState()

        badge = state in (WORKING, ATTENTION)
        for x, y in ((8.2, 1.2), (5.4, 2.6)):  # the pages behind, each covering the one before
            back = page(x, y, 8.4, 11.6)
            clear(back)
            back.stroke()
        front = page(1.6, 4.4, 9.8, 12.8, fold=3.0)
        clear(front)
        front.stroke()
        for y in (10.2, 13.0):  # the two lines on the front page
            line = AppKit.NSBezierPath.bezierPath()
            line.moveToPoint_((4.2 * s, y * s))
            line.lineToPoint_((8.8 * s, y * s))
            line.setLineWidth_(1.3 * s)
            line.setLineCapStyle_(AppKit.NSLineCapStyleRound)
            line.stroke()
        if badge:
            r = 3.0 if state == WORKING else 4.0
            cx, cy = 18.0 - r, 18.0 - r
            ring = AppKit.NSBezierPath.bezierPathWithOvalInRect_((((cx - r - 1.4) * s, (cy - r - 1.4) * s),
                                                                  ((2 * r + 2.8) * s, (2 * r + 2.8) * s)))
            clear(ring)
            AppKit.NSBezierPath.bezierPathWithOvalInRect_((((cx - r) * s, (cy - r) * s), (2 * r * s, 2 * r * s))).fill()
            if state == ATTENTION:  # a "!" cut out of the dot
                clear(AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    (((cx - 0.6) * s, (cy - 2.6) * s), (1.2 * s, 3.2 * s)), 0.6 * s, 0.6 * s))
                clear(AppKit.NSBezierPath.bezierPathWithOvalInRect_((((cx - 0.65) * s, (cy + 1.2) * s),
                                                                     (1.3 * s, 1.3 * s))))
        return True

    image = AppKit.NSImage.imageWithSize_flipped_drawingHandler_((size, size), True, draw)
    if state == PAUSED:  # a template image keeps its alpha: dimmed, as macOS shows an inactive item
        full = image

        def dimmed(rect) -> bool:
            full.drawInRect_fromRect_operation_fraction_(rect, AppKit.NSZeroRect,
                                                         AppKit.NSCompositingOperationSourceOver, 0.45)
            return True

        image = AppKit.NSImage.imageWithSize_flipped_drawingHandler_((size, size), False, dimmed)
    image.setTemplate_(True)
    return image


@dataclass
class Extra:
    """An item an embedding app adds above Quit (Interlatch.app: Connect Claude Code, Open at Login, …)."""
    key: str
    title: str
    action: object  # a callable, run on a background thread
    visible: object = None  # a callable returning bool, asked whenever the menu opens
    checked: object = None


_CLASSES: dict = {}


def _classes() -> dict:
    """The NSObject subclasses the item needs (ObjC classes can only be defined once per process): the target of the
    icon and the menu, and the panel's message handler."""
    if not _CLASSES:
        import AppKit
        import objc
        import WebKit  # noqa: F401  (registers the WKScriptMessageHandler protocol)

        class InterlatchStatusTarget(AppKit.NSObject):
            def act_(self, sender):
                self.owner.act(str(sender.representedObject()))

            def clicked_(self, sender):
                self.owner.clicked()

            def menuWillOpen_(self, menu):
                self.owner.will_open()

        class InterlatchPanelBridge(AppKit.NSObject, protocols=[objc.protocolNamed("WKScriptMessageHandler")]):
            def userContentController_didReceiveScriptMessage_(self, controller, message):
                body = message.body()
                if isinstance(body, AppKit.NSDictionary):
                    self.owner.panel_message({str(k): v for k, v in body.items()})

        _CLASSES.update(target=InterlatchStatusTarget, bridge=InterlatchPanelBridge)
    return _CLASSES


class StatusMenu:
    """The menu-bar item. `open_page(hash)` opens a dashboard page ("#/", "#/session/<id>"), `refresh()` returns a
    Snapshot (run on the poll thread), `sync_now()` and `quit()` run on a background thread. Main thread: install().

    With `base_url` (the dashboard serving web/panel.html), a click opens the panel, a popover over the menu-bar glass,
    and a right-click (or Control-click) the native menu; without it, a click opens the menu."""

    PANEL_WIDTH = 360
    PANEL_MAX_HEIGHT = 720

    def __init__(self, *, refresh, open_page, sync_now, quit, open_title: str = "Open Dashboard",
                 extras: list[Extra] | None = None, base_url: str | None = None):
        self.refresh, self.open_page, self.sync_now, self.quit = refresh, open_page, sync_now, quit
        self.open_title = open_title
        self.base_url = base_url
        self.popover = None
        self.webview = None
        self.panel_ready = False
        self.extras = extras or []
        self.items: dict = {}
        self.recent_items: list = []
        self.snap: Snapshot | None = None
        self.wake = threading.Event()
        self.images: dict = {}

    # -------------------------------------------------------------- building (main thread)
    def install(self) -> None:
        import AppKit

        self.target = _classes()["target"].alloc().init()
        self.target.owner = self
        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        menu.setDelegate_(self.target)
        self.menu = menu

        def add(key, title, enabled=True, quit_key=""):
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "act:" if enabled else None,
                                                                                quit_key)
            item.setTarget_(self.target)
            item.setRepresentedObject_(key)
            item.setEnabled_(enabled)
            menu.addItem_(item)
            self.items[key] = item
            return item

        add("headline", "Starting…").setToolTip_("Open the Activity page")
        add("detail", "", enabled=False).setHidden_(True)
        add("hub", "", enabled=False).setHidden_(True)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        header = (AppKit.NSMenuItem.sectionHeaderWithTitle_("Recent Sessions")
                  if hasattr(AppKit.NSMenuItem, "sectionHeaderWithTitle_") else None)
        if header is None:
            header = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Recent Sessions", None, "")
            header.setEnabled_(False)
        menu.addItem_(header)
        self.items["recent"] = header
        self.items["recent_sep"] = AppKit.NSMenuItem.separatorItem()
        menu.addItem_(self.items["recent_sep"])
        add("search", "Search Sessions…")
        add("open", self.open_title)
        add("sync", "Sync Now")
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        add("update", "Update Interlatch…").setHidden_(True)
        for extra in self.extras:
            add(f"extra:{extra.key}", extra.title)
        if self.extras:
            menu.addItem_(AppKit.NSMenuItem.separatorItem())
        add("quit", "Quit Interlatch", quit_key="q")

        self.status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength)
        button = self.status_item.button()
        if self.base_url:
            button.setTarget_(self.target)
            button.setAction_("clicked:")
            button.sendActionOn_(AppKit.NSEventMaskLeftMouseUp | AppKit.NSEventMaskRightMouseUp)
        else:
            self.status_item.setMenu_(menu)
        self.status_item.button().setImage_(self._image(OK))
        self.status_item.button().setToolTip_("Interlatch")
        if self.base_url:
            self._build_panel()  # loaded and filled in before the first click
        self.will_open()
        threading.Thread(target=self._poll, name="interlatch-menubar", daemon=True).start()

    def _image(self, state: str):
        if state not in self.images:
            self.images[state] = mark(state)
        return self.images[state]

    # -------------------------------------------------------------- keeping it current
    def poke(self) -> None:
        """Something changed (a sync started or ended): refresh now instead of at the next poll."""
        self.wake.set()

    def _poll(self) -> None:
        from PyObjCTools import AppHelper

        while True:
            self.wake.clear()
            try:
                snap = self.refresh()
            except Exception:
                log.exception("menu-bar refresh failed")
                snap = None
            if snap is not None:
                AppHelper.callAfter(self.apply, snap)
            self.wake.wait(POLL_BUSY_S if snap is not None and snap.state == WORKING else POLL_S)

    def apply(self, snap: Snapshot) -> None:
        """Main thread: show a snapshot (also while the menu is open: NSMenu redraws the items it changes)."""
        import AppKit

        self.snap = snap
        button = self.status_item.button()
        button.setImage_(self._image(snap.state))
        button.setToolTip_(f"Interlatch: {snap.headline}")
        button.setAccessibilityLabel_(f"Interlatch, {snap.headline}")
        self.items["headline"].setTitle_(snap.headline)
        self.items["detail"].setTitle_(snap.detail)
        self.items["detail"].setHidden_(not snap.detail or bool(self.base_url))
        self.items["hub"].setTitle_(snap.hub)
        self.items["hub"].setHidden_(not snap.hub)
        self.items["sync"].setEnabled_(not snap.syncing)
        self.items["update"].setHidden_(not snap.update)
        if snap.update:
            self.items["update"].setTitle_("Update Available…" if snap.update == "build"
                                           else f"Update to {snap.update}…")

        for item in self.recent_items:
            self.menu.removeItem_(item)
        self.recent_items = []
        at = self.menu.indexOfItem_(self.items["recent"]) + 1
        for i, s in enumerate([] if self.base_url else snap.recent):  # the panel lists them
            title = _short(s.get("title") or "(untitled session)", TITLE_MAX)
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "act:", "")
            item.setTarget_(self.target)
            item.setRepresentedObject_(f"session:{s['id']}")
            sub = " · ".join(x for x in (s.get("project_name"), when(s.get("at")).removeprefix("at ")) if x)
            if sub and item.respondsToSelector_("setSubtitle:"):  # macOS 14.4+
                item.setSubtitle_(sub)
            elif sub:
                item.setToolTip_(sub)
            self.menu.insertItem_atIndex_(item, at + i)
            self.recent_items.append(item)
        self.items["recent"].setHidden_(not self.recent_items)
        self.items["recent_sep"].setHidden_(not self.recent_items)
        self._push()

    def will_open(self) -> None:
        """Main thread, as the menu opens: the embedding app's items, and a fresh snapshot on its way."""
        import AppKit

        for extra in self.extras:
            item = self.items[f"extra:{extra.key}"]
            try:
                item.setHidden_(bool(extra.visible) and not extra.visible())
                if extra.checked:
                    item.setState_(AppKit.NSControlStateValueOn if extra.checked() else AppKit.NSControlStateValueOff)
            except Exception:
                log.exception("menu item %s", extra.key)
        self.poke()

    # -------------------------------------------------------------- the panel (main thread)
    def clicked(self) -> None:
        """The icon was clicked: the panel, or with the right button or Control the native menu."""
        import AppKit

        event = AppKit.NSApp.currentEvent()
        secondary = event is not None and (event.type() == AppKit.NSEventTypeRightMouseUp
                                           or event.modifierFlags() & AppKit.NSEventModifierFlagControl)
        if secondary:
            self._close_panel()
            self.status_item.setMenu_(self.menu)  # shown by the click below, which returns once the menu closes
            self.status_item.button().performClick_(None)
            self.status_item.setMenu_(None)
        elif self.popover is not None and self.popover.isShown():
            self._close_panel()
        else:
            self._show_panel()

    def _build_panel(self) -> None:
        import AppKit
        import WebKit

        classes = _classes()
        url = self.base_url + "panel.html"
        self.bridge = classes["bridge"].alloc().init()
        self.bridge.owner = self
        config = WebKit.WKWebViewConfiguration.alloc().init()
        config.userContentController().addScriptMessageHandler_name_(self.bridge, "interlatch")
        view = WebKit.WKWebView.alloc().initWithFrame_configuration_(((0, 0), (self.PANEL_WIDTH, 420)), config)
        view.setValue_forKey_(False, "drawsBackground")  # transparent: the popover's glass shows through
        # The page loads nothing but itself: it has no links or forms, session text goes in as text, its CSP allows
        # this dashboard only, and panel.js cancels any link click. Its actions come through the bridge above.
        view.loadRequest_(AppKit.NSURLRequest.requestWithURL_(AppKit.NSURL.URLWithString_(url)))
        controller = AppKit.NSViewController.alloc().init()
        controller.setView_(view)
        popover = AppKit.NSPopover.alloc().init()
        popover.setContentViewController_(controller)
        popover.setContentSize_((self.PANEL_WIDTH, 420))
        popover.setBehavior_(AppKit.NSPopoverBehaviorTransient)
        popover.setAnimates_(True)
        self.webview, self.popover = view, popover

    def _show_panel(self) -> None:
        import AppKit

        if self.popover is None:
            self._build_panel()
        AppKit.NSApp.activateIgnoringOtherApps_(True)  # so the search field takes the keyboard
        button = self.status_item.button()
        self.popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, AppKit.NSRectEdgeMinY)
        self.popover.contentViewController().view().window().makeKeyWindow()
        self._push()
        self._js("window.interlatch && interlatch.opened()")
        self.will_open()  # the extras' state for the More menu, and a fresh snapshot

    def _close_panel(self) -> None:
        if self.popover is not None and self.popover.isShown():
            self.popover.performClose_(None)

    def _js(self, code: str) -> None:
        if self.webview is not None and self.panel_ready:
            self.webview.evaluateJavaScript_completionHandler_(code, None)

    def _push(self) -> None:
        if self.snap is not None:
            self._js(f"window.interlatch && interlatch.render({json.dumps(self.snap.to_page())})")

    def panel_message(self, msg: dict) -> None:
        """What the panel page sent (web/panel.js): {type: open|sync|update|menu|close|size|ready, …}."""
        import AppKit

        kind = str(msg.get("type") or "")
        if kind == "ready":
            self.panel_ready = True
            self._push()
        elif kind == "size":
            height = max(160, min(self.PANEL_MAX_HEIGHT, int(msg.get("height") or 0)))
            self.popover.setContentSize_((self.PANEL_WIDTH, height))
        elif kind == "open":
            page = str(msg.get("page") or "#/")
            if page.startswith("#/"):  # dashboard pages only
                self._close_panel()
                self._run(lambda: self.open_page(page))
        elif kind == "sync":
            self._run(self._sync)
        elif kind == "update":
            self._close_panel()
            self._run(lambda: self.open_page("#/status"))
        elif kind == "menu":
            self.will_open()
            AppKit.NSMenu.popUpContextMenu_withEvent_forView_(self.menu, AppKit.NSApp.currentEvent(), self.webview)
        elif kind == "close":
            self._close_panel()

    # -------------------------------------------------------------- actions
    def _run(self, fn) -> None:
        threading.Thread(target=self._safely, args=(fn,), daemon=True).start()

    def act(self, key: str) -> None:
        """Main thread: hand the action to a thread, since it may wait (an alert, the browser, a job)."""
        if key.startswith("session:"):
            fn = lambda: self.open_page(f"#/session/{key.split(':', 1)[1]}")  # noqa: E731
        elif key.startswith("extra:"):
            fn = next(e.action for e in self.extras if f"extra:{e.key}" == key)
        else:
            fn = {"headline": lambda: self.open_page("#/"), "search": lambda: self.open_page("#/search"),
                  "open": lambda: self.open_page("#/"), "update": lambda: self.open_page("#/status"),
                  "sync": self._sync, "quit": self.quit}.get(key)
        if fn is not None:
            self._close_panel()
            self._run(fn)

    def _safely(self, fn) -> None:
        try:
            fn()
        except Exception:
            log.exception("menu-bar action failed")

    def _sync(self) -> None:
        self.sync_now()
        self.poke()


# ---------------------------------------------------------------------------------------------- `interlatch ui`
def run_with_server(cfg: Config, httpd, url: str) -> bool:
    """`interlatch ui` with the menu-bar item: the dashboard serves on a thread, AppKit runs on this (main) thread
    until Quit or Ctrl+C. False, with nothing started, when the `app` extra (PyObjC) is not installed."""
    try:
        import AppKit
        from PyObjCTools import AppHelper
    except ImportError:
        log.info("no menu-bar icon: it needs the `app` extra (uv tool install 'interlatch[app]')")
        return False
    from .install import UI_LABEL

    global SHOWN
    SHOWN = True
    app = httpd.app
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    threading.Thread(target=httpd.serve_forever, name="interlatch-ui", daemon=True).start()

    def quit_() -> None:
        if os.environ.get("XPC_SERVICE_NAME") == UI_LABEL:  # launchd would start a KeepAlive agent right back up
            if alert("Quit Interlatch?",
                     "The dashboard and this icon stop until you next log in, or until you run `interlatch ui`. "
                     "Your sessions are still recorded.", ["Quit", "Cancel"]) != 0:
                return
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{UI_LABEL}"], capture_output=True)
        AppHelper.callAfter(AppKit.NSApp.terminate_, None)

    menu = StatusMenu(refresh=lambda: snapshot(app, sync_error=agent_sync_error()),
                      open_page=lambda page: webbrowser.open(url + page), sync_now=app.action_sync, quit=quit_,
                      base_url=url)
    AppHelper.callAfter(menu.install)
    log.info("menu-bar icon on, dashboard at %s", url)
    AppHelper.runEventLoop(installInterrupt=True)
    return True
