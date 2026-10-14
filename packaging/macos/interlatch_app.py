"""Entry point of Interlatch.app's executable.

The one binary is the app (no arguments) and the `interlatch` CLI (any arguments): Claude Code's hooks and
MCP servers run it through the shim in ~/.interlatch/bin/ with a subcommand.
"""

import sys


def run() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]  # Finder adds -psn_… on old macOS
    if args:
        from interlatch.cli import main

        return main(args)
    from interlatch.runtime.platforms.desktop import main

    return main()


if __name__ == "__main__":
    sys.exit(run())
