"""The pieces Chronicle.app relies on that run without a GUI: the shim, frozen self-invocation, the server port."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.request

import pytest


@pytest.fixture()
def frozen(env, monkeypatch):
    """Pretend to run inside Chronicle.app: sys.executable is the bundled binary (here a script echoing its args)."""
    exe = env["tmp"] / "Chronicle.app" / "Contents" / "MacOS" / "Chronicle"
    exe.parent.mkdir(parents=True)
    exe.write_text('#!/bin/sh\necho "ran:$*"\n')
    exe.chmod(0o755)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return exe


def test_frozen_executable_is_a_shim_that_runs_the_app(env, frozen):
    from interlatch.runtime.install import executable, shim_path

    exe = executable()
    assert exe == str(shim_path()) == str(env["home"] / "bin" / "interlatch")
    out = subprocess.run([exe, "hook", "session-end"], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "ran:hook session-end"


def test_shim_is_rewritten_only_when_the_app_moves(env, frozen, monkeypatch):
    from interlatch.runtime.install import write_shim

    shim = write_shim(str(frozen))
    mtime = shim.stat().st_mtime_ns
    assert write_shim(str(frozen)).stat().st_mtime_ns == mtime
    moved = env["tmp"] / "Applications" / "Chronicle"
    write_shim(str(moved))
    assert str(moved) in shim.read_text()


def test_frozen_hooks_point_at_the_shim(env, frozen):
    from interlatch.runtime.install import executable, install_hooks, settings_path

    install_hooks(env["cfg"], executable(), inject=False)
    settings = json.loads(settings_path(env["cfg"]).read_text())
    command = settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
    assert command == f"{env['home'] / 'bin' / 'interlatch'} hook session-end"


def test_self_command(monkeypatch):
    from interlatch.core.process import self_command

    assert self_command() == [sys.executable, "-m", "interlatch"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert self_command() == [sys.executable]


def test_make_server_falls_back_to_a_free_port(env):
    from interlatch.server import make_server

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        with pytest.raises(OSError):
            make_server(env["cfg"], port=port)
        httpd = make_server(env["cfg"], port=port, any_port=True)
    bound = httpd.server_address[1]
    assert bound != port
    import threading

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{bound}/api/status", timeout=5) as resp:
            assert "version" in json.loads(resp.read())  # the Host check accepts the fallback port
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_in_temporary_location():
    from interlatch.runtime.platforms.desktop import in_temporary_location

    assert in_temporary_location("/Volumes/Chronicle/Chronicle.app/Contents/MacOS/Chronicle")
    assert in_temporary_location("/private/var/folders/x/AppTranslocation/ABC/d/Chronicle.app/Contents/MacOS/Chronicle")
    assert not in_temporary_location("/Applications/Chronicle.app/Contents/MacOS/Chronicle")


def test_version_comes_from_the_package_metadata():
    from importlib.metadata import version

    import interlatch

    assert interlatch.__version__ == version("interlatch")


def test_app_window_loads_the_transparent_page():
    from interlatch.runtime.platforms.desktop import app_url

    assert app_url("http://127.0.0.1:8765/", False) == "http://127.0.0.1:8765/?app=mac"
    assert app_url("http://127.0.0.1:8765/", True) == "http://127.0.0.1:8765/?app=mac&reduce=1"


def test_bridge_exposes_only_window_actions_and_no_way_back_to_the_app():
    from interlatch.runtime.platforms.desktop import make_bridge

    calls = []

    class FakeApp:
        secret = "install_cli and friends"

        def set_appearance(self, theme):
            calls.append(("appearance", theme))

        def start_drag(self):
            calls.append(("drag",))

        def title_double_click(self):
            calls.append(("zoom",))

    bridge = make_bridge(FakeApp())
    bridge.set_appearance("dark")
    bridge.start_drag()
    bridge.title_double_click()
    assert calls == [("appearance", "dark"), ("drag",), ("zoom",)]
    assert [n for n in dir(bridge) if not n.startswith("_")] == ["set_appearance", "start_drag", "title_double_click"]

    # pywebview resolves "a.b.c" with getattr, underscores included: nothing reachable that way leads to the app
    def walk(obj, depth=0, seen=None):
        seen = seen if seen is not None else set()
        if depth > 4 or id(obj) in seen:
            return
        seen.add(id(obj))
        assert not isinstance(obj, FakeApp)
        if isinstance(obj, dict):
            assert not obj or set(obj) == {"__builtins__"} and obj["__builtins__"] == {}
        for name in ("__func__", "__self__", "__globals__", "__builtins__", "__closure__", "__class__", "__dict__"):
            try:
                walk(getattr(obj, name), depth + 1, seen)
            except AttributeError:
                pass

    for name in ("set_appearance", "start_drag", "title_double_click"):
        walk(getattr(bridge, name))
