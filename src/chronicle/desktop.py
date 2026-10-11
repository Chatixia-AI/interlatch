"""Interlatch.app (macOS): the dashboard in a native window, a menu-bar item, and the background sync.

The app does what `interlatch install` sets up launchd for: it serves the dashboard and runs `sync --work`
every 15 minutes while it is running (it opens at login). Claude Code's hooks and MCP registration point at
a shim (~/.interlatch/bin/interlatch) that the app rewrites on every launch, so they survive the app
being moved or updated. Needs the `app` extra (pywebview + PyObjC).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import types
import webbrowser
from pathlib import Path

from .config import Config, load_config

log = logging.getLogger("interlatch.desktop")

SYNC_INTERVAL_S = 15 * 60
FIRST_SYNC_DELAY_S = 20
ONBOARDED_KEY = "app_onboarded"
EXTRA_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin")
CLI_LINK = "~/.local/bin/interlatch"


def adopt_login_path() -> None:
    """Apps opened from Finder get PATH=/usr/bin:/bin:/usr/sbin:/sbin, but `claude` (and the node an npm
    install of it needs) live elsewhere. Take PATH from the user's login shell, as a terminal would have it."""
    dirs = os.environ.get("PATH", "").split(os.pathsep)
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        out = subprocess.run([shell, "-ilc", 'printf "__PATH__%s__PATH__" "$PATH"'], capture_output=True,
                             text=True, timeout=10, stdin=subprocess.DEVNULL).stdout
        if out.count("__PATH__") >= 2:
            dirs = out.split("__PATH__")[1].split(os.pathsep) + dirs
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("could not read PATH from %s: %s", shell, exc)
    dirs += [str(Path(d).expanduser()) for d in EXTRA_PATH]
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys(d for d in dirs if d))


def reduce_transparency() -> bool:
    """macOS Accessibility > Display > Reduce transparency. WebKit has no `prefers-reduced-transparency`,
    so the app reads the setting and tells the page."""
    try:
        import AppKit

        return bool(AppKit.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceTransparency())
    except Exception:
        return False


def name_process(name: str) -> None:
    """Run from source, macOS names the app after the interpreter ("python3" in the menu bar and Dock).
    Interlatch.app gets its name from Info.plist; this covers `interlatch app` from a checkout or pip install."""
    try:
        import Foundation
    except ImportError:
        return
    try:
        bundle = Foundation.NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = name
    except Exception:
        log.debug("could not rename the bundle", exc_info=True)
    try:
        Foundation.NSProcessInfo.processInfo().setProcessName_(name)
    except Exception:
        log.debug("could not rename the process", exc_info=True)


def app_url(base: str, reduce: bool) -> str:
    """The dashboard as the app window loads it: `app=mac` makes the page transparent over the native glass."""
    return f"{base}?app=mac" + ("&reduce=1" if reduce else "")


def make_bridge(app: DesktopApp):
    """What the page may call in the app window (`window.pywebview.api`): three window actions, nothing else.

    pywebview resolves a call by walking attribute names ("a.b.c") from this object, underscored ones included, so
    the object holds no reference to the app. Each method is a copy of a closure with empty globals and builtins:
    walking `__func__`, `__globals__` or `__builtins__` reaches nothing, and the app sits in a closure cell, which
    attribute lookups cannot open."""

    def set_appearance(self, theme):
        """Keep the native glass in step with the page's theme ("light", "dark" or "system")."""
        app.set_appearance(theme)

    def start_drag(self):
        """The mouse went down on the toolbar or title strip: move the window with it."""
        app.start_drag()

    def title_double_click(self):
        """Double-click on the toolbar: zoom (or minimize) the window, as the system setting says."""
        app.title_double_click()

    def seal(fn):
        sealed = types.FunctionType(fn.__code__, {"__builtins__": {}}, fn.__name__, None, fn.__closure__)
        sealed.__doc__ = fn.__doc__
        return sealed

    methods = {fn.__name__: seal(fn) for fn in (set_appearance, start_drag, title_double_click)}
    return type("Bridge", (), {"__slots__": (), **methods})()


def in_temporary_location(exe: str | None = None) -> bool:
    """Running from a mounted disk image or a quarantined (translocated) copy, not from where it will stay."""
    exe = exe or sys.executable
    return "/AppTranslocation/" in exe or exe.startswith("/Volumes/")


class Background:
    """`interlatch sync --work` every 15 minutes, on a thread; the file locks keep it from overlapping with a
    hook-spawned run or a launchd agent left over from a CLI install."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.wake = threading.Event()
        self.running = False
        self.error: str | None = None

    def start(self) -> None:
        threading.Thread(target=self._loop, name="interlatch-sync", daemon=True).start()

    def sync_now(self) -> None:
        self.wake.set()

    def _loop(self) -> None:
        self.wake.wait(FIRST_SYNC_DELAY_S)
        while True:
            self.wake.clear()
            self.run_once()
            self.wake.wait(SYNC_INTERVAL_S)

    def run_once(self) -> None:
        from .db import connect
        from .ingest import sync
        from .notify import release_check_safely
        from .worker import run_worker

        self.running = True
        self.on_change()
        try:
            cfg = load_config()
            cfg.ensure_dirs()
            release_check_safely(cfg)
            if cfg.sends_files:  # this Mac sends its sessions to a hub, which records and analyzes them
                from .hub import push

                log.info("app push: %s", push(cfg).summary())
                self.error = None
                return
            conn = connect(cfg.db_path)
            try:
                report = sync(cfg, conn)
            finally:
                conn.close()
            work = run_worker(cfg)
            log.info("app sync: %s · work: %s", report.summary(), work.summary())
            self.error = None
        except Exception as exc:
            log.exception("app sync failed")
            self.error = str(exc)
        finally:
            self.running = False
            self.on_change()


class DesktopApp:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.frozen = bool(getattr(sys, "frozen", False))
        self.quitting = False
        self.window = None
        self.url = ""
        self.menu = None
        self.server_app = None
        self.styled = False
        self.last_mouse_down = None
        self.mouse_monitor = None
        self.bg = Background(self._menu_changed)

    # ------------------------------------------------------------------ lifecycle
    def run(self) -> int:
        if not self.frozen:
            name_process("Interlatch")  # before pywebview creates the NSApplication
        import webview

        from .install import write_shim
        from .server import make_server

        adopt_login_path()
        if self.frozen:
            write_shim(sys.executable)
        httpd = make_server(self.cfg, any_port=True)
        self.server_app = httpd.app
        self.url = f"http://127.0.0.1:{httpd.server_address[1]}/"
        threading.Thread(target=httpd.serve_forever, name="interlatch-ui", daemon=True).start()
        log.info("app started (%s), dashboard at %s", sys.executable, self.url)

        self._install_app_delegate()
        webview.settings["ALLOW_DOWNLOADS"] = True  # session exports: the window asks where to save them
        # a transparent page over native vibrancy; the page asks to drag the window from its toolbar (start_drag)
        self.window = webview.create_window("Interlatch", app_url(self.url, reduce_transparency()), width=1440, height=920,
                                            min_size=(900, 600), text_select=True, zoomable=True,
                                            transparent=True, vibrancy=True, js_api=make_bridge(self))
        self.window.events.closing += self._on_closing
        self.window.events.loaded += self._on_loaded
        self.bg.start()
        webview.start(self._started, private_mode=False, storage_path=str(self.cfg.home / "webview"))
        httpd.shutdown()
        return 0

    def _install_app_delegate(self) -> None:
        """Extend pywebview's NSApplication delegate: Quit really quits (closing the window only hides it),
        and clicking the Dock icon brings the window back."""
        import objc
        from webview.platforms.cocoa import BrowserView

        app = self
        base = BrowserView.AppDelegate

        class InterlatchAppDelegate(base):
            def applicationShouldTerminate_(self, nsapp):
                app.quitting = True
                return objc.super(InterlatchAppDelegate, self).applicationShouldTerminate_(nsapp)

            def applicationShouldHandleReopen_hasVisibleWindows_(self, nsapp, flag):
                app.show_window()
                return True

        BrowserView.AppDelegate = InterlatchAppDelegate

    def _started(self) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._build_status_item)
        if not self.frozen:
            AppHelper.callAfter(self._use_app_icon)
        try:
            self.onboard()
        except Exception:
            log.exception("onboarding failed")

    def _on_loaded(self) -> None:
        if not self.styled:
            self.styled = True
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._style_window)

    def _style_window(self) -> None:
        """macOS 26 look: content runs under a transparent titlebar, the traffic lights float over the sidebar
        (an empty unified toolbar gives them the 52pt band the page's toolbar is centred on), the window keeps
        its shadow, and the vibrancy uses the sidebar material. Runs once the web view is the content view."""
        import AppKit

        try:
            win = self.window.native
            win.setStyleMask_(win.styleMask() | AppKit.NSWindowStyleMaskFullSizeContentView)
            win.setTitlebarAppearsTransparent_(True)
            win.setTitleVisibility_(AppKit.NSWindowTitleHidden)
            toolbar = AppKit.NSToolbar.alloc().initWithIdentifier_("interlatch")
            toolbar.setShowsBaselineSeparator_(False)
            win.setToolbar_(toolbar)
            win.setToolbarStyle_(AppKit.NSWindowToolbarStyleUnified)
            win.setHasShadow_(True)  # pywebview's transparent mode turns it off
            # pywebview paints the titlebar container with the window colour; clear it so the page shows through
            for view in (win.contentView().superview().subviews() or []):
                if view.className() == "NSTitlebarContainerView":
                    view.setBackgroundColor_(AppKit.NSColor.clearColor())
            stack = [win.contentView()]  # the vibrancy pywebview put behind the page
            while stack:
                view = stack.pop()
                if isinstance(view, AppKit.NSVisualEffectView):
                    view.setMaterial_(AppKit.NSVisualEffectMaterialSidebar)
                    view.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
                    view.setState_(AppKit.NSVisualEffectStateFollowsWindowActiveState)
                stack.extend(view.subviews() or [])
            win.invalidateShadow()
        except Exception:
            log.exception("could not style the window")

        # Dragging: the page has no title bar to grab, so it tells us when the mouse went down on its toolbar.
        # By then WebKit has consumed the event, so keep the latest mouse-down to hand to the window server.
        def remember(event):
            self.last_mouse_down = event
            return event

        try:
            self.mouse_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                AppKit.NSEventMaskLeftMouseDown, remember)
        except Exception:
            log.exception("could not watch for window drags")

    def start_drag(self) -> None:
        import AppKit
        from PyObjCTools import AppHelper

        def drag():
            event, win = self.last_mouse_down, self.window.native
            if event is not None and event.window() == win and AppKit.NSEvent.pressedMouseButtons() & 1:
                win.performWindowDragWithEvent_(event)

        AppHelper.callAfter(drag)

    def title_double_click(self) -> None:
        import AppKit
        from PyObjCTools import AppHelper

        def act():
            win = self.window.native
            action = AppKit.NSUserDefaults.standardUserDefaults().stringForKey_("AppleActionOnDoubleClick") or "Maximize"
            if action == "Minimize":
                win.performMiniaturize_(None)
            elif action != "None":
                win.performZoom_(None)

        AppHelper.callAfter(act)

    def set_appearance(self, theme: str) -> None:
        import AppKit
        from PyObjCTools import AppHelper

        names = {"light": AppKit.NSAppearanceNameAqua, "dark": AppKit.NSAppearanceNameDarkAqua}

        def apply():
            try:
                name = names.get(theme)
                self.window.native.setAppearance_(AppKit.NSAppearance.appearanceNamed_(name) if name else None)
            except Exception:
                log.exception("could not set the window appearance")

        AppHelper.callAfter(apply)

    def _use_app_icon(self) -> None:
        """From source the Dock would show Python's icon; Interlatch.app has its own .icns."""
        import AppKit

        from .server import WEB_DIR

        image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(WEB_DIR / "icon.png"))
        if image is not None:
            AppKit.NSApp.setApplicationIconImage_(image)

    def _on_closing(self):
        if self.quitting:
            return True
        import AppKit
        from PyObjCTools import AppHelper

        self.window.hide()  # keep running in the menu bar, without a Dock icon
        AppHelper.callAfter(AppKit.NSApp.setActivationPolicy_, AppKit.NSApplicationActivationPolicyAccessory)
        return False

    def show_window(self) -> None:
        import AppKit
        from PyObjCTools import AppHelper

        def show():
            AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
            self.window.show()

        AppHelper.callAfter(show)

    # ------------------------------------------------------------------ menu bar
    def _build_status_item(self) -> None:
        """The same menu as the login item's (menubar.py), opening pages in this window, plus the app's own items."""
        import AppKit
        from PyObjCTools import AppHelper

        from .install import hooks_installed
        from .menubar import Extra, StatusMenu, snapshot

        self.menu = StatusMenu(
            refresh=lambda: snapshot(self.server_app, sync_running=self.bg.running, sync_error=self.bg.error),
            open_page=self.open_page, sync_now=self.bg.sync_now, open_title="Open Interlatch", base_url=self.url,
            quit=lambda: AppHelper.callAfter(AppKit.NSApp.terminate_, None),
            extras=[
                Extra("browser", "Open in Browser", lambda: webbrowser.open(self.url)),
                Extra("connect", "Connect Claude Code…", self.connect_claude,
                      visible=lambda: not hooks_installed(self.cfg).get("SessionEnd")),
                Extra("login", "Open at Login", self.toggle_login_item, visible=lambda: self.frozen,
                      checked=lambda: self.login_item_status() == "enabled"),
                Extra("cli", "Install Command-Line Tool", self.install_cli, visible=lambda: self.frozen),
                Extra("data", "Open Data Folder", lambda: subprocess.Popen(["open", str(self.cfg.home)])),
            ])
        self.menu.install()

    def _menu_changed(self) -> None:
        if self.menu is not None:
            self.menu.poke()

    def open_page(self, page: str) -> None:
        """A dashboard page in the app window ("#/session/<id>"), brought to the front."""
        try:
            self.window.evaluate_js(f"location.hash = {json.dumps(page)}")
        except Exception:
            log.exception("could not open %s in the window", page)
        self.show_window()

    # ------------------------------------------------------------------ dialogs
    def alert(self, title: str, text: str, buttons: list[str]) -> int:
        """Modal alert from a background thread; returns the index of the button pressed."""
        from .menubar import alert

        return alert(title, text, buttons)

    # ------------------------------------------------------------------ setup
    def onboard(self) -> None:
        """First launch: offer to connect Claude Code (hooks + MCP). Asked once; the menu keeps the option."""
        from .db import connect, kv_get, kv_set
        from .install import hooks_installed

        if self.frozen and in_temporary_location():
            self.alert("Move Interlatch to Applications",
                       "Interlatch is running from a disk image or a temporary location. Drag it into your "
                       "Applications folder and open it from there, so it stays available to Claude Code.", ["OK"])
            return
        if hooks_installed(self.cfg).get("SessionEnd"):
            return
        conn = connect(self.cfg.db_path)
        try:
            if kv_get(conn, ONBOARDED_KEY):
                return
            kv_set(conn, ONBOARDED_KEY, "1")
            conn.commit()
        finally:
            conn.close()
        self.connect_claude(first_run=True)

    def connect_claude(self, first_run: bool = False) -> None:
        from .connectors import connect
        from .install import executable

        if not self.cfg.claude_bin():
            self.alert("Claude Code not found",
                       "Interlatch records your coding-agent sessions and analyzes them through your own Claude Code or "
                       "Codex login. To record Claude Code, install it from claude.com/claude-code and sign in, then "
                       "choose “Connect Claude Code…” from Interlatch's menu-bar icon. Codex and the other agents are "
                       "under Settings › Sources in the dashboard, and Status › Analysis picks the agent that analyzes.",
                       ["OK"])
            return
        steps = ["• add a SessionEnd hook to ~/.claude/settings.json (a backup is kept), so each session is "
                 "recorded when it ends",
                 "• register the interlatch MCP server, so Claude can search your past sessions"]
        if self.frozen:
            steps.append("• open Interlatch at login, so sessions are analyzed in the background")
        from .llm import make_runner

        runner = make_runner(self.cfg)
        how = (f"Analysis runs on this Mac with {runner.label}." if runner.local()
               else f"Analysis runs through your {runner.label} API key or sign-in." if runner.describe()["kind"] == "api"
               else f"Analysis runs through your own {runner.label} login and counts toward your plan's usage.")
        text = "Interlatch will:\n" + "\n".join(steps) + f"\n\n{how} Everything else stays on this Mac."
        if self.alert("Connect Claude Code?", text, ["Connect", "Not Now"]) != 0:
            return
        for action in connect(self.cfg, "claude", executable()):
            log.info("connect: %s", action)
        if self.frozen and self.login_item_status() != "enabled":
            self.set_login_item(True)
        self.bg.sync_now()
        self._menu_changed()
        if first_run:
            self.alert("Claude Code connected",
                       "Interlatch is importing your past sessions now; analysis follows in the background. "
                       "Interlatch stays in the menu bar when you close its window.", ["OK"])

    # ------------------------------------------------------------------ login item (macOS 13+)
    def login_item_status(self) -> str:
        try:
            from ServiceManagement import SMAppService

            status = SMAppService.mainAppService().status()
        except Exception:
            return "unavailable"
        return {0: "not registered", 1: "enabled", 2: "requires approval", 3: "not found"}.get(status, str(status))

    def set_login_item(self, on: bool) -> None:
        from ServiceManagement import SMAppService

        service = SMAppService.mainAppService()
        ok, err = service.registerAndReturnError_(None) if on else service.unregisterAndReturnError_(None)
        if not ok:
            log.warning("login item %s failed: %s", "register" if on else "unregister", err)
            self.alert("Open at Login", f"macOS refused: {err.localizedDescription() if err else 'unknown error'}",
                       ["OK"])
        elif on and self.login_item_status() == "requires approval":
            SMAppService.openSystemSettingsLoginItems()
        self._menu_changed()

    def toggle_login_item(self) -> None:
        self.set_login_item(self.login_item_status() != "enabled")

    # ------------------------------------------------------------------ command-line tool
    def install_cli(self) -> None:
        from .install import shim_path, write_shim

        shim = write_shim(sys.executable)
        link = Path(CLI_LINK).expanduser()
        if link.is_symlink() and link.resolve() == shim.resolve():
            self.alert("Command-line tool", f"`interlatch` is already installed at {CLI_LINK}.", ["OK"])
            return
        if link.exists() or link.is_symlink():
            self.alert("Command-line tool",
                       f"{CLI_LINK} already exists (for example from `uv tool install`), so it was left alone. "
                       f"The app's own command is {shim_path()}.", ["OK"])
            return
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(shim)
        on_path = str(link.parent) in os.environ.get("PATH", "").split(os.pathsep)
        self.alert("Command-line tool", f"Installed `interlatch` at {CLI_LINK}."
                   + ("" if on_path else " Add ~/.local/bin to your PATH to use it."), ["OK"])


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "darwin":
        print("The Interlatch desktop app is macOS-only for now; run `interlatch ui` for the dashboard.", file=sys.stderr)
        return 1
    try:
        import webview  # noqa: F401
    except ImportError:
        print("The desktop app needs the `app` extra: uv tool install 'interlatch[app]'", file=sys.stderr)
        return 1
    from .migrate import migrate_if_due
    from .util import file_lock, setup_logging

    migrate_if_due()  # Chronicle's folder becomes Interlatch's
    cfg = load_config()
    cfg.ensure_dirs()
    setup_logging(cfg.logs_dir)
    with file_lock(cfg.locks_dir / "app.lock", blocking=False) as got:
        if not got:
            print("Interlatch is already running (see its menu-bar icon).", file=sys.stderr)
            return 0
        return DesktopApp(cfg).run()
