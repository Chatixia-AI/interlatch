"""`pythonw -m chronicle.windows …`, as Task Scheduler tasks registered before the rename run it: interlatch.windows.

RESTART_EXIT too: a dashboard that was started before an update and asks for its restart after it looks it up here."""

import sys

from interlatch.windows import RESTART_EXIT as RESTART_EXIT
from interlatch.windows import run_hidden

if __name__ == "__main__":
    raise SystemExit(run_hidden(sys.argv[1:]))
