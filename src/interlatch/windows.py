"""`pythonw -m interlatch.windows …`: the command Task Scheduler's tasks run (runtime/platforms/windows.py has the
runner). A stable entry point: tasks registered by any version name this module, so it stays where it is."""

import sys

from .runtime.platforms.windows import RESTART_EXIT as RESTART_EXIT
from .runtime.platforms.windows import run_hidden

if __name__ == "__main__":
    raise SystemExit(run_hidden(sys.argv[1:]))
