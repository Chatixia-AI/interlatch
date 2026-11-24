"""`executable()` names the install that is running, whatever else is called `interlatch` on PATH.

It took the first `interlatch` on PATH outside its own environment. In a terminal with another install's environment
switched on (a checkout's `.venv` in an editor), that was the other install: `interlatch status` then said every agent
ran "not this install", and `interlatch connect` wrote the other install's command into the agent's settings. The
other way round, a checkout's command run by its path registered the installed one.
"""

import os
import sys

import pytest

from interlatch.runtime.install import executable

BIN = "Scripts" if os.name == "nt" else "bin"
links = pytest.mark.skipif(os.name == "nt", reason="uv links its commands on macOS and Linux; Windows gets copies")


def _command(folder, name="interlatch"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + (".exe" if os.name == "nt" else ""))
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def _environment(root):
    """A Python environment with both of our commands in it. Returns (the environment, its interlatch)."""
    root.mkdir(parents=True)
    (root / "pyvenv.cfg").write_text("home = /usr/bin\n")
    _command(root / BIN, "chronicle")
    return root, _command(root / BIN)


@pytest.fixture()
def running(tmp_path, monkeypatch):
    """This install: an environment of its own (uv's tool folder). Returns a function that sets PATH and argv[0]."""
    env, command = _environment(tmp_path / "tools" / "interlatch")
    monkeypatch.setattr(sys, "prefix", str(env))
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    def run(path, argv0=command):
        monkeypatch.setenv("PATH", os.pathsep.join(str(p) for p in path))
        monkeypatch.setattr(sys, "argv", [str(argv0), "status"])
        return command

    return run


@links
def test_another_install_first_on_path_is_passed_over_for_this_ones_link(tmp_path, running):
    _, others = _environment(tmp_path / "checkout" / ".venv")
    local = tmp_path / "home" / ".local" / "bin"
    local.mkdir(parents=True)
    for name in ("interlatch", "chronicle"):
        (local / name).symlink_to(tmp_path / "tools" / "interlatch" / BIN / name)
    running([others.parent, local], argv0=local / "interlatch")
    assert executable() == str(local / "interlatch")


@links
def test_a_link_to_another_install_is_not_this_one(tmp_path, running):
    """A checkout's command run by its path, with the installed one's link on PATH: the checkout is what runs."""
    _, installed = _environment(tmp_path / "pypi")
    local = tmp_path / "home" / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "interlatch").symlink_to(installed)
    mine = running([local])
    assert executable() == str(mine.resolve())


def test_another_environments_command_is_never_named(tmp_path, running):
    other, others = _environment(tmp_path / "checkout" / ".venv")
    running([others.parent])
    assert not executable().startswith(str(other))


@links
def test_this_installs_link_outside_its_environment_wins_wherever_it_is_on_path(tmp_path, running):
    local = tmp_path / "home" / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "interlatch").symlink_to(tmp_path / "tools" / "interlatch" / BIN / "interlatch")
    for path in ([local, tmp_path / "tools" / "interlatch" / BIN], [tmp_path / "tools" / "interlatch" / BIN, local]):
        running(path)
        assert executable() == str(local / "interlatch")


def test_a_command_in_no_environment_is_taken_as_before(tmp_path, running):
    """pip install --user, a wrapper script, a copy on Windows: nothing says it is another install's."""
    script = _command(tmp_path / "home" / ".local" / "bin")
    running([script.parent])
    assert executable() == str(script if os.name != "nt" else script.resolve())
