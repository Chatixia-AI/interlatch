"""Running a command: parse the arguments, re-run in UTF-8 mode where the console needs it (Windows), and call the
command's function."""

from __future__ import annotations

import sys

from .parser import build_parser


def _rerun_in_utf8_mode(argv: list[str]) -> int:
    """Windows: Claude Code and the other agents write UTF-8, while Python there reads and writes text in the ANSI code
    page (cp1252, cp932) unless in UTF-8 mode, which is set as Python starts. So this command runs again in it, as do
    the Pythons it starts (PYTHONUTF8). Windows has no exec: this process waits, its stdin and stdout passed on."""
    import os
    import subprocess

    proc = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "interlatch", *argv], env={**os.environ, "PYTHONUTF8": "1"})
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:  # Ctrl+C reaches both: the command decides how it ends
            continue


def main(argv: list[str] | None = None) -> int:
    if argv is None and sys.platform == "win32" and not sys.flags.utf8_mode:
        return _rerun_in_utf8_mode(sys.argv[1:])
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["hook"] and len(argv) >= 2:  # fast path: no argparse/rich import cost
        from ...runtime.hooks import hook_main

        return hook_main(argv[1])
    if argv[:1] == ["statusline"]:  # Claude Code runs this after every turn: same fast path
        from ...runtime.statusline import main as statusline_main

        return statusline_main()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        from ...core.config import interlatch_home

        parser.print_help()
        if not (interlatch_home() / "config.toml").exists():
            print("\nNew here? Run `interlatch install` to pick which coding agents to record and start the dashboard.")
        return 0
    try:
        return args.fn(args) or 0
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
