"""A worker run, start to finish and in this order: analyze the sessions waiting, synthesize knowledge bases,
glossaries, themes and the weekly review, export Markdown, rebuild suggestions, then send the hub what was learned
and bring the mirror up to date."""

from __future__ import annotations

import logging
from datetime import timedelta

from ..analysis.queue import PAUSE_KEY, pending_sessions
from ..analysis.runners.base import LLMError, Runner, UsageLimitError
from ..analysis.runners.factory import make_runner
from ..analysis.worker import WorkReport, analyze_many
from ..core.config import Config
from ..core.util import file_lock, to_iso, utcnow
from ..storage.kv import kv_get, kv_set
from ..storage.sqlite import connect

log = logging.getLogger("interlatch.worker")


def run_worker(cfg: Config, *, session_ids: list[str] | None = None, max_analyses: int | None = None,
               analyze: bool = True, synthesize: bool = True, export: bool = True, force: bool = False,
               model: str | None = None, progress=None, wait: bool = False) -> WorkReport:
    report = WorkReport()
    if cfg.sends_files:  # its sessions go to the hub, which analyzes them; analyzing here would pay twice
        report.note = f"analysis runs on the hub ({cfg.hub_url})"
        return report
    if wait and progress:
        with file_lock(cfg.locks_dir / "worker.lock", blocking=False) as free:
            pass
        if not free:
            progress("waiting for the background worker to finish its current batch…")
    with file_lock(cfg.locks_dir / "worker.lock", blocking=wait, timeout=3600 if wait else None) as got:
        if not got:
            report.note = "another worker is running"
            return report
        conn = connect(cfg.db_path)
        try:
            _run_locked(cfg, conn, report, session_ids=session_ids, max_analyses=max_analyses, analyze=analyze,
                        synthesize=synthesize, export=export, force=force, model=model, progress=progress)
        finally:
            conn.rollback()  # never leave a write transaction (and its lock) behind on any exit path
            conn.close()
    if cfg.shares_knowledge:  # send the hub what was learned; a missed send is caught up by the next run
        from ..hub import HubError, push_knowledge

        try:
            report.shared = push_knowledge(cfg).summary()
        except HubError as exc:
            report.shared = f"not sent ({exc})"
    if cfg.mirror_to and not session_ids:  # a background or `interlatch work` run: bring the mirror up to date
        from ..storage.mirror import sync as mirror_sync

        report.mirrored = mirror_sync(cfg).summary()  # never raises; a missed write is caught up by the next run
    log.info("worker: %s", report.summary())
    return report


def _run_locked(cfg: Config, conn, report: WorkReport, *, session_ids, max_analyses, analyze, synthesize, export,
                force, model, progress) -> None:
    # a crashed worker can leave sessions marked running
    conn.execute("UPDATE sessions SET analysis_status = 'pending' WHERE analysis_status = 'running'")
    conn.commit()
    runner = make_runner(cfg)
    paused = kv_get(conn, PAUSE_KEY)
    if analyze and (cfg.analysis.auto or session_ids or force):
        if paused and paused > to_iso(utcnow()) and not session_ids:
            report.paused_until = paused
        elif not runner.available():
            report.note = f"{runner.unavailable_reason()}; analysis skipped"
        else:
            ids = session_ids or pending_sessions(conn, cfg, max_analyses or cfg.analysis.max_per_run)
            analyze_many(cfg, ids, runner, report, model=model, progress=progress)
    if synthesize and (cfg.synthesis.auto or force) and runner.available() and not report.paused_until:
        _synthesize(cfg, conn, runner, report, force=force, progress=progress)
        _glossaries(cfg, conn, runner, report, progress=progress)
        _themes(cfg, report, progress=progress)
        _weekly_review(cfg, conn, runner, report, progress=progress)
    if export and cfg.export_markdown:
        from ..export_md import export_markdown

        try:
            report.exported = export_markdown(conn, cfg)
        except Exception:  # export problems must never block analysis
            conn.rollback()
            log.exception("markdown export failed")
    if not session_ids:  # a background or `interlatch work` run, not one asked for specific sessions
        _suggestions(cfg, conn, report)


def _suggestions(cfg: Config, conn, report: WorkReport) -> None:
    """Rebuild the suggestions queue (deterministic, no LLM) and notify about new ones when [suggestions] notify."""
    from ..suggest import after_sync

    got = after_sync(conn, cfg)  # never raises; None when [suggestions] enabled = false
    if got and got["new"]:
        report.suggestions = got["new"]


def _synthesize(cfg: Config, conn, runner: Runner, report: WorkReport, *, force=False, progress=None):
    from ..synthesize import GLOBAL, global_needs_synthesis, projects_needing_synthesis, synthesize_project

    targets = projects_needing_synthesis(conn, cfg, force=force)
    if global_needs_synthesis(conn, cfg) or (force and targets):
        targets.append(GLOBAL)
    for path in targets:
        try:
            if progress:
                progress(f"synthesizing {path.rsplit('/', 1)[-1]}…")
            synthesize_project(conn, cfg, path, runner)
            report.synthesized.append(path)
        except UsageLimitError as exc:
            until = to_iso(utcnow() + timedelta(hours=1))
            kv_set(conn, PAUSE_KEY, until)
            conn.commit()
            report.paused_until = until
            report.failed.append((path, str(exc)))
            break
        except (LLMError, ValueError) as exc:
            report.failed.append((path, str(exc)))
        except Exception as exc:  # a malformed reply must not stop the other projects, the review or the export
            conn.rollback()
            log.exception("synthesis failed for %s", path)
            report.failed.append((path, f"{type(exc).__name__}: {exc}"))


def _weekly_review(cfg: Config, conn, runner: Runner, report: WorkReport, *, progress=None):
    """Write the review of the last completed week once all its sessions are analyzed."""
    from ..reviews import generate_review, review_ready

    ready, key = review_ready(conn)
    if not ready:
        return
    try:
        if progress:
            progress(f"writing weekly review {key}…")
        generate_review(conn, cfg, key, runner)
        report.reviews.append(key)
    except UsageLimitError as exc:
        kv_set(conn, PAUSE_KEY, to_iso(utcnow() + timedelta(hours=1)))
        conn.commit()
        report.failed.append((key, str(exc)))
    except (LLMError, ValueError) as exc:
        report.failed.append((key, str(exc)))
    except Exception as exc:
        conn.rollback()
        log.exception("weekly review %s failed", key)
        report.failed.append((key, f"{type(exc).__name__}: {exc}"))


def _themes(cfg: Config, report: WorkReport, *, progress=None):
    """Re-group the glossary categories whose terms changed in this run's glossary rebuilds (the Map's Theme level)."""
    from ..glossary import build_themes

    if not report.synthesized or report.paused_until:
        return
    for cat, result in build_themes(cfg, concurrency=cfg.analysis.concurrency, progress=progress).items():
        if isinstance(result, str) and result.startswith("failed"):
            report.failed.append((f"themes:{cat}", result))


def _glossaries(cfg: Config, conn, runner: Runner, report: WorkReport, *, progress=None):
    """Refresh the glossary of every project whose knowledge base was just re-synthesized."""
    from ..glossary import build_glossaries

    if not report.synthesized or report.paused_until:
        return
    results = build_glossaries(cfg, list(report.synthesized), concurrency=cfg.analysis.concurrency, progress=progress)
    for path, result in results.items():
        if isinstance(result, str) and result.startswith("failed"):
            report.failed.append((f"glossary:{path}", result))
            if any(m in result.lower() for m in ("usage limit", "rate limit", "429")):
                kv_set(conn, PAUSE_KEY, to_iso(utcnow() + timedelta(hours=1)))
                conn.commit()
                report.paused_until = kv_get(conn, PAUSE_KEY)
