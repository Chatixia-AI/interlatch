"""Running an agent's CLI for one answer: a wall-clock deadline that notices the computer slept, and the whole
process group stopped on a timeout."""

from __future__ import annotations

import os
import subprocess
import time

from .base import LLMError, SleepInterruptedError

SLEEP_GRACE_S = 120  # wall-clock time not seen by the monotonic clock = the machine was asleep
POLL_S = 10


def _kill_group(proc: subprocess.Popen) -> None:
    import signal

    if os.name == "nt":  # no process groups: taskkill ends the tree (an npm install's claude.cmd runs node under cmd)
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def run_sleep_aware(cmd: list[str], prompt: str, *, cwd, env, timeout: int, name: str = "claude -p") -> tuple[str, str, int]:
    """subprocess.run with a wall-clock deadline that also notices system sleep.

    subprocess timeouts use the monotonic clock, which stops while a Mac sleeps, so a call frozen
    by sleep (its HTTPS connection dead) would otherwise hang for its full timeout after wake.
    """
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=cwd, env=env, start_new_session=True)
    except OSError as exc:  # a configured path that does not exist, or is not executable
        raise LLMError(f"{name} could not start: {exc}") from exc
    wall0, mono0 = time.time(), time.monotonic()
    pending_input: str | None = prompt
    while True:
        try:
            out, err = proc.communicate(input=pending_input, timeout=POLL_S)
            return out or "", err or "", proc.returncode
        except subprocess.TimeoutExpired:
            pending_input = None  # input is only sent once; later calls just keep collecting output
            wall = time.time() - wall0
            slept = wall - (time.monotonic() - mono0)
            if slept > SLEEP_GRACE_S:
                _kill_group(proc)
                raise SleepInterruptedError(f"machine slept for {int(slept)}s during the call") from None
            if wall > timeout:
                _kill_group(proc)
                raise LLMError(f"{name} timed out after {int(wall)}s") from None
        except BaseException:
            _kill_group(proc)
            raise
