"""A worker run, start to finish and in this order: analyze the sessions waiting, synthesize knowledge bases,
glossaries, themes and the weekly review, export Markdown, rebuild suggestions, then send the hub what was learned
and bring the mirror up to date."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import timedelta

from ..analysis.queue import PAUSE_KEY, pending_sessions, plan_over_limit
from ..analysis.runners.base import LLMError, Runner, UsageLimitError
from ..analysis.runners.factory import make_runner
from ..analysis.worker import WorkReport, analyze_many
from ..core.config import Config
from ..core.util import file_lock, to_iso, utcnow
from ..storage.kv import kv_get, kv_set
from ..storage.sqlite import connect

log = logging.getLogger("interlatch.worker")


def run_worker(cfg: Config, *, session_ids: list[str] | None = None, max_analyses: int | None = None,
               analyze: bool = True, synthesize: bool = True,
               export: Callable[[sqlite3.Connection, Config], int] | None = None, force: bool = False,
               model: str | None = None, progress=None, wait: bool = False) -> WorkReport:
    """`export` writes the Markdown vault when [export] markdown is on: the caller passes
    interfaces.exports.vault.export_markdown, since workflows don't reach up into the interfaces."""
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
        from ..sharing.push import push_knowledge
        from ..sharing.protocol import HubError

        try:
            report.shared = push_knowledge(cfg).summary()
        except HubError as exc:
            report.shared = f"not sent ({exc})"
    from .profiles import joined

    for pcfg in joined(cfg):  # each named profile with a hub of its own: only its sessions, from this archive
        from ..sharing.push import push_knowledge
        from ..sharing.protocol import HubError

        try:
            sent = push_knowledge(pcfg).summary()
        except HubError as exc:
            sent = f"not sent ({exc})"
        label = _profile_label(cfg, pcfg.owner)
        report.shared = "; ".join(x for x in (report.shared, f"{label}: {sent}") if x)
    if cfg.mirror_to and not session_ids:  # a background or `interlatch work` run: bring the mirror up to date
        from ..storage.mirror import sync as mirror_sync

        report.mirrored = mirror_sync(cfg).summary()  # never raises; a missed write is caught up by the next run
    log.info("worker: %s", report.summary())
    return report


def _profile_label(cfg: Config, owner: str) -> str:
    from ..core.profiles import RegistryError, load_registry

    try:
        reg = load_registry(cfg.home)
    except RegistryError:
        return owner
    return reg.label(owner) if reg else owner


def _run_locked(cfg: Config, conn, report: WorkReport, *, session_ids, max_analyses, analyze, synthesize, export,
                force, model, progress) -> None:
    # a crashed worker can leave sessions marked running
    conn.execute("UPDATE sessions SET analysis_status = 'pending' WHERE analysis_status = 'running'")
    conn.commit()
    runner = make_runner(cfg)
    paused = kv_get(conn, PAUSE_KEY)
    # at the plan limit the user set (analysis.plan_limit_pct): runs on their own wait; sessions asked for still run
    capped = None if session_ids or force else plan_over_limit(conn, cfg)
    if capped:
        report.note = (f"Claude plan's {capped['window']} limit at {capped['used_pct']:.0f}% "
                       f"(analysis.plan_limit_pct = {cfg.analysis.plan_limit_pct}); analysis skipped")
    if analyze and not capped and (cfg.analysis.auto or session_ids or force):
        if paused and paused > to_iso(utcnow()) and not session_ids:
            report.paused_until = paused
        elif not runner.available():
            report.note = f"{runner.unavailable_reason()}; analysis skipped"
        else:
            ids = session_ids or pending_sessions(conn, cfg, max_analyses or cfg.analysis.max_per_run)
            analyze_many(cfg, ids, runner, report, model=model, progress=progress)
    auto = cfg.synthesis.auto or force
    # the projects and groups whose weekly re-analysis is due: someone turned each on, so they run without [synthesis] auto
    due = (_weekly_due(cfg, conn) if synthesize and not session_ids and not capped and runner.available()
           and not report.paused_until and not (paused and paused > to_iso(utcnow())) else [])
    if synthesize and not capped and (auto or due) and runner.available() and not report.paused_until:
        _synthesize(cfg, conn, runner, report, force=force, auto=auto, also=_weekly_projects(cfg, conn, due),
                    progress=progress)
        _glossaries(cfg, conn, runner, report, progress=progress)
        _themes(cfg, report, progress=progress)
        _weekly_refresh(cfg, conn, runner, report, due, progress=progress)
        if auto:
            _weekly_review(cfg, conn, runner, report, progress=progress)
    if export and cfg.export_markdown:
        try:
            report.exported = export(conn, cfg)
        except Exception:  # export problems must never block analysis
            conn.rollback()
            log.exception("markdown export failed")
    if not session_ids:  # a background or `interlatch work` run, not one asked for specific sessions
        _suggestions(cfg, conn, report)


def _suggestions(cfg: Config, conn, report: WorkReport) -> None:
    """Rebuild the suggestions queue (deterministic, no LLM) and notify about new ones when [suggestions] notify."""
    from ..knowledge.suggest import after_sync

    got = after_sync(conn, cfg)  # never raises; None when [suggestions] enabled = false
    if got and got["new"]:
        report.suggestions = got["new"]


def _synthesize(cfg: Config, conn, runner: Runner, report: WorkReport, *, force=False, auto=True, also=(), progress=None):
    """auto: the knowledge bases [synthesis] refreshes by itself; also: the ones a weekly re-analysis refreshes."""
    from ..knowledge.synthesize import GLOBAL, global_needs_synthesis, projects_needing_synthesis, synthesize_project

    targets = projects_needing_synthesis(conn, cfg, force=force) if auto else []
    if auto and (global_needs_synthesis(conn, cfg) or (force and targets)):
        targets.append(GLOBAL)
    targets += [p for p in also if p not in targets]
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
    from ..knowledge.reviews import generate_review, review_ready

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


def _weekly_due(cfg: Config, conn) -> list[tuple[str, str]]:
    from ..knowledge.weekly import due

    return due(conn, cfg)


def _weekly_projects(cfg: Config, conn, due) -> list[str]:
    """The projects the due weekly re-analyses cover that have something new to synthesize."""
    from ..knowledge import weekly

    out: list[str] = []
    for kind, target in due:
        out += [p for p in weekly.members(conn, kind, target) if p not in out and weekly.project_changed(conn, cfg, p)]
    return out


def _weekly_refresh(cfg: Config, conn, runner: Runner, report: WorkReport, due, *, progress=None):
    """Finish each due weekly re-analysis: _synthesize re-synthesized its changed projects (and _glossaries rebuilt
    their glossaries); a group's links are checked again when they changed. Then it counts as this week's refresh,
    unless a usage limit cut the run short: what's left runs once the pause ends."""
    from ..knowledge import group_graph, weekly

    for kind, target in due:
        if report.paused_until:
            return
        paths = weekly.members(conn, kind, target)
        errors = [m for t, m in report.failed if t in paths or t.removeprefix("glossary:") in paths]
        detail = {"projects": sum(1 for p in paths if p in report.synthesized), "graph": False,
                  "error": errors[0] if errors else None}
        if kind == "group" and paths and weekly.graph_changed(conn, int(target), paths):
            try:
                if progress:
                    progress(f"checking the links of group {target}…")
                group_graph.analyze(cfg, conn, int(target), paths, runner=runner, concurrency=cfg.analysis.concurrency,
                                    progress=progress)
                detail["graph"] = True
            except UsageLimitError as exc:
                until = to_iso(utcnow() + timedelta(hours=1))
                kv_set(conn, PAUSE_KEY, until)
                conn.commit()
                report.paused_until = until
                report.failed.append((f"group:{target}", str(exc)))
                return
            except (LLMError, ValueError) as exc:
                detail["error"] = str(exc)
                report.failed.append((f"group:{target}", str(exc)))
            except Exception as exc:
                conn.rollback()
                log.exception("weekly links check of group %s failed", target)
                detail["error"] = f"{type(exc).__name__}: {exc}"
                report.failed.append((f"group:{target}", detail["error"]))
        result = "failed" if detail["error"] else "refreshed" if detail["projects"] or detail["graph"] else "unchanged"
        weekly.record(conn, kind, target, result, detail)


def _themes(cfg: Config, report: WorkReport, *, progress=None):
    """Re-group the glossary categories whose terms changed in this run's glossary rebuilds (the Map's Theme level)."""
    from ..knowledge.glossary import build_themes

    if not report.synthesized or report.paused_until:
        return
    for cat, result in build_themes(cfg, concurrency=cfg.analysis.concurrency, progress=progress).items():
        if isinstance(result, str) and result.startswith("failed"):
            report.failed.append((f"themes:{cat}", result))


def _glossaries(cfg: Config, conn, runner: Runner, report: WorkReport, *, progress=None):
    """Refresh the glossary of every project whose knowledge base was just re-synthesized."""
    from ..knowledge.glossary import build_glossaries

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
