"""How Interlatch runs itself and the agents it starts: the command that re-runs it, and the environment that
marks an agent's session as Interlatch's own (its hooks then record nothing)."""

from __future__ import annotations

import os
import subprocess
import sys

INTERNAL_ENV = "INTERLATCH_INTERNAL"  # on the agents Interlatch runs itself (analyses), so their hooks record nothing
INTERNAL_ENVS = (INTERNAL_ENV, "CHRONICLE_INTERNAL")  # and its name before the rename


def internal_env() -> dict[str, str]:
    """The environment for an agent Interlatch runs itself."""
    return {**os.environ, INTERNAL_ENV: "1"}


def self_command() -> list[str]:
    """argv that re-runs this program: the bundled executable inside Interlatch.app, else `python -m interlatch`."""
    return [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "interlatch"]


def console_codepage() -> int | None:
    """The code page Windows' own programs write in here: the console's, else (pythonw has no console) the system's
    OEM one. Both follow Windows' language: 932 on Japanese Windows. None on other systems."""
    if sys.platform != "win32":
        return None
    import ctypes

    kernel32 = ctypes.windll.kernel32
    return kernel32.GetConsoleOutputCP() or kernel32.GetOEMCP()


def program_text(data: bytes | str | None) -> str:
    """Another program's output as text: UTF-8 when it is (the coding agents, git, uv and Interlatch itself write
    that), else the console's code page. schtasks, PowerShell and cmd.exe write Japanese Windows' messages in cp932,
    and read as UTF-8 every one of them came out as "�G���["."""
    if not data:
        return ""
    if isinstance(data, str):
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        codepage = console_codepage()
        try:
            text = data.decode(f"cp{codepage}" if codepage else "utf-8", errors="replace")
        except LookupError:
            text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")  # newlines as text=True gave them


def run_program(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run with the output captured as text by program_text. Not text=True: in UTF-8 mode (Interlatch on
    Windows) that raises UnicodeDecodeError on the first cp932 byte, which an npm-installed `claude.cmd` gives when
    cmd.exe reports an error on Japanese Windows, and the command that asked ended in a traceback."""
    if isinstance(kwargs.get("input"), str):
        kwargs["input"] = kwargs["input"].encode("utf-8")
    proc = subprocess.run(argv, capture_output=True, **kwargs)
    return subprocess.CompletedProcess(argv, proc.returncode, program_text(getattr(proc, "stdout", None)),
                                       program_text(getattr(proc, "stderr", None)))
