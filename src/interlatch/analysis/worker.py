"""Analyzing the sessions a worker run picked, several at once: what each came to, and when the run stops early (a
usage limit, the budget, the computer going to sleep). application/workflows.py runs it, then what follows."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta

from ..core.config import Config
from ..core.util import to_iso, utcnow
from ..storage.kv import kv_set
from ..storage.sqlite import connect
from .queue import PAUSE_KEY
from .runners.base import BudgetExceededError, LLMError, Runner, UsageLimitError

log = logging.getLogger("interlatch.worker")


@dataclass
class WorkReport:
    analyzed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    synthesized: list[str] = field(default_factory=list)
    reviews: list[str] = field(default_factory=list)
    exported: int = 0
    suggestions: int = 0  # new suggestions this run's refresh found
    shared: str | None = None  # [hub] share = "knowledge": what was sent to the hub
    mirrored: str | None = None  # [mirror] to = "postgres": what was written to the mirror
    cost_usd: float = 0.0
    paused_until: str | None = None
    note: str | None = None

    def summary(self) -> str:
        parts = [f"{len(self.analyzed)} analyzed", f"{len(self.skipped)} skipped", f"{len(self.failed)} failed",
                 f"{len(self.synthesized)} knowledge bases", f"{len(self.reviews)} reviews", f"{self.exported} notes exported"]
        if self.suggestions:
            parts.append(f"{self.suggestions} new suggestion{'s' if self.suggestions != 1 else ''}")
        if self.paused_until:
            parts.append(f"paused until {self.paused_until}")
        if self.shared:
            parts.append(f"hub: {self.shared}")
        if self.mirrored:
            parts.append(f"mirror: {self.mirrored}")
        if self.note:
            parts.append(self.note)
        return ", ".join(parts)


def analyze_many(cfg: Config, ids: list[str], runner: Runner, report: WorkReport, *, model=None, progress=None):
    from .session import AnalysisSkipped, analyze_session

    stop = threading.Event()
    lock = threading.Lock()
    finished = 0

    def task(sid: str):
        if stop.is_set():
            return sid, "cancelled", None
        conn = connect(cfg.db_path)
        try:
            if progress:
                progress(f"analyzing {sid[:8]}…" if len(ids) == 1 else f"analyzing sessions: {finished} of {len(ids)} done")
            analyze_session(conn, cfg, sid, runner, model=model)
            cost = conn.execute(
                "SELECT cost_usd FROM analyses WHERE target = ? AND kind = 'session' ORDER BY id DESC LIMIT 1", (sid,)
            ).fetchone()
            return sid, "done", cost[0] if cost else 0.0
        except AnalysisSkipped as exc:
            return sid, "skipped", str(exc)
        except UsageLimitError as exc:
            stop.set()
            until = to_iso(utcnow() + timedelta(hours=1))
            kv_set(conn, PAUSE_KEY, until)
            conn.commit()
            return sid, "paused", f"{exc} (paused until {until})"
        except (LLMError, BudgetExceededError) as exc:
            return sid, "failed", str(exc)
        except Exception as exc:  # keep the batch going
            log.exception("analysis crashed for %s", sid)
            conn.execute("UPDATE sessions SET analysis_status='error', analysis_reason=?, "
                         "analysis_attempts = analysis_attempts + 1 WHERE id=?", (str(exc)[:500], sid))
            conn.commit()
            return sid, "failed", str(exc)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=max(1, cfg.analysis.concurrency)) as pool:
        futures = [pool.submit(task, sid) for sid in ids]
        try:
            for fut in as_completed(futures):
                sid, status, info = fut.result()
                with lock:
                    finished += 1
                    if status == "done":
                        report.analyzed.append(sid)
                        report.cost_usd += info or 0.0
                    elif status == "skipped":
                        report.skipped.append(sid)
                    elif status == "paused":
                        report.failed.append((sid, info))
                        report.paused_until = to_iso(utcnow() + timedelta(hours=1))
                    elif status == "failed":
                        report.failed.append((sid, info))
                if progress:
                    progress(f"{sid[:8]}: {status}" if len(ids) == 1 else f"analyzing sessions: {finished} of {len(ids)} done")
        except KeyboardInterrupt:  # Ctrl-C: drop the queue; the calls already running finish and are saved
            stop.set()
            for fut in futures:
                fut.cancel()
            if progress:
                progress("stopping: finishing the sessions already in progress…")
            raise
