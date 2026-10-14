"""The dashboard's long actions (sync, analysis, imports, updates): one of each kind at a time, run in the
background, with progress the page polls."""

from __future__ import annotations

import logging
import threading
import time

from ...core.util import PROGRESS_RE

log = logging.getLogger("interlatch.server")


class Jobs:
    """Tiny in-process registry of background jobs started from the UI."""

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}

    def start(self, name: str, fn) -> bool:
        with self.lock:
            if self.jobs.get(name, {}).get("state") == "running":
                return False
            self.jobs[name] = {"state": "running", "started": time.time(), "message": "", "result": None, "done": None, "total": None}

        def run():
            try:
                result = fn(lambda m: self._msg(name, m))
                self._finish(name, "done", result)
            except Exception as exc:  # surfaced in the UI
                log.exception("job %s failed", name)
                self._finish(name, "error", str(exc))

        threading.Thread(target=run, daemon=True).start()
        return True

    def _msg(self, name, message):
        m = PROGRESS_RE.search(message or "")
        done, total = (int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))) if m else (None, None)
        with self.lock:
            self.jobs[name].update(message=message, done=done, total=total if total and done <= total else None)

    def _finish(self, name, state, result):
        with self.lock:
            self.jobs[name].update(state=state, result=result, finished=time.time())

    def snapshot(self) -> dict:
        with self.lock:
            return {k: dict(v) for k, v in self.jobs.items()}
