"""The sessions waiting for analysis: which are ready, why the others wait (still active, retry later, held), and
whether the queue is paused."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..core.config import Config
from ..core.i18n import tr
from ..core.util import local_str, one_line, to_iso, utcnow
from ..storage.kv import kv_get
from .runners.base import Runner
from .runners.factory import make_runner

PAUSE_KEY = "analysis_paused_until"


def pending_sessions(conn, cfg: Config, limit: int) -> list[str]:
    now = utcnow()
    params = {
        "now": to_iso(now),
        "idle": to_iso(now - timedelta(minutes=cfg.analysis.idle_minutes)),
        "min_prompts": cfg.analysis.min_prompts,
        "installed": kv_get(conn, "installed_at") or "0000",
    }
    sql = (
        "SELECT id, project_path FROM sessions WHERE source NOT IN ('history', 'remote') "
        f"AND analysis_status IN ('pending', 'stale', 'error') AND analysis_attempts < {MAX_ATTEMPTS} "
        "AND (analysis_not_before IS NULL OR analysis_not_before <= :now) "
        "AND n_prompts >= :min_prompts AND (ended_flag = 1 OR ended_at <= :idle)"
    )
    if not cfg.analysis.backfill:
        sql += " AND started_at >= :installed"
    sql += " ORDER BY ended_flag DESC, ended_at DESC"
    ids = []
    for sid, project_path in conn.execute(sql, params):
        if cfg.is_excluded(project_path):  # excluded after it was queued: never send it to Claude
            continue
        ids.append(sid)
        if len(ids) >= limit:
            break
    return ids


MAX_ATTEMPTS = 4
# Why a session that is not analyzed yet is waiting. "Queued" codes clear by themselves; "held" codes need a change
# (a setting, or a longer session) before the session is ever analyzed. One function computes them, mirroring
# pending_sessions(), so every surface (session page, Status, CLI) names the same reason.
QUEUED_CODES = ("ready", "active", "retry")
HELD_CODES = ("excluded", "too_short", "before_install", "gave_up")
QUEUE_REASON_LABEL = {"ready": "ready now", "active": "still active", "retry": "retry later", "excluded": "project excluded",
                      "too_short": "too few prompts", "before_install": "before install (backfill off)",
                      "gave_up": "failed, not retried"}


def waiting_reason(conn, cfg: Config, s) -> tuple[str, str] | None:
    """(code, explanation) for a session still waiting for analysis, or None when it is not waiting."""
    status = s["analysis_status"]
    if s["source"] == "history" or status not in ("pending", "stale", "error"):
        return None
    attempts = s["analysis_attempts"] or 0
    if attempts >= MAX_ATTEMPTS:
        reason = one_line(s["analysis_reason"] or tr("no error recorded"), 160)
        return "gave_up", (tr("analysis failed {n}; it will not be retried automatically ({reason})", n=attempts, reason=reason)
                           if attempts < 99 else
                           tr("analysis failed for good; it will not be retried automatically ({reason})", reason=reason))
    if cfg.is_excluded(s["project_path"]):
        return "excluded", tr("its project is excluded from analysis (sources.exclude_projects)")
    if (s["n_prompts"] or 0) < cfg.analysis.min_prompts:
        return "too_short", tr("fewer than {n} prompts (analysis.min_prompts)", n=cfg.analysis.min_prompts)
    if not cfg.analysis.backfill and (s["started_at"] or "") < (kv_get(conn, "installed_at") or "0000"):
        return "before_install", tr("it started before Interlatch was installed, and analysis.backfill is off")
    now = utcnow()
    if s["analysis_not_before"] and s["analysis_not_before"] > to_iso(now):
        return "retry", tr("retrying at {when} after a failed attempt ({reason})",
                           when=local_str(s["analysis_not_before"], "%m-%d %H:%M"), reason=one_line(s["analysis_reason"] or "", 160))
    idle_at = (s["ended_at"] or "")
    if not s["ended_flag"] and idle_at > to_iso(now - timedelta(minutes=cfg.analysis.idle_minutes)):
        ready_at = datetime.fromisoformat(idle_at.replace("Z", "+00:00")) + timedelta(minutes=cfg.analysis.idle_minutes)
        return "active", tr("the session may still be going; it is analyzed once idle for {minutes} minutes (around {at})",
                            minutes=cfg.analysis.idle_minutes, at=local_str(to_iso(ready_at), "%H:%M"))
    block = queue_block(conn, cfg)
    if block:
        return "ready", tr("ready, but {block}", block=block)
    return "ready", (tr("ready: the background agent analyzes it on its next run (every 15 minutes)") if status != "stale"
                     else tr("ready to re-analyze: the session continued after it was analyzed"))


def queue_block(conn, cfg: Config, runner: Runner | None = None) -> str | None:
    """What stops the whole queue right now, if anything."""
    paused = kv_get(conn, PAUSE_KEY)
    if paused and paused > to_iso(utcnow()):
        return tr("analysis is paused until {when} (usage limit); it resumes by itself", when=local_str(paused, "%m-%d %H:%M"))
    if not cfg.analysis.auto:
        return tr("automatic analysis is off (analysis.auto); analyze it from its page or with `interlatch analyze`")
    runner = runner or make_runner(cfg)
    if not runner.available():
        return tr("{reason}, so nothing can be analyzed", reason=runner.unavailable_reason())
    return None


def count_pending(conn, cfg: Config) -> dict:
    """The analysis queue: `ready` now, `queued` (everything that will be analyzed by itself, ready included),
    `held` (needs a change first), each with a count per reason, and `block` when the whole queue is stopped."""
    reasons: dict[str, int] = {}
    for s in conn.execute(
            "SELECT analysis_status, source, analysis_attempts, analysis_reason, analysis_not_before, project_path, n_prompts, "
            "started_at, ended_at, ended_flag FROM sessions WHERE source NOT IN ('history', 'remote') "
            "AND analysis_status IN ('pending','stale','error')"):
        got = waiting_reason(conn, cfg, s)
        if got:
            reasons[got[0]] = reasons.get(got[0], 0) + 1
    return {"ready": reasons.get("ready", 0), "queued": sum(reasons.get(c, 0) for c in QUEUED_CODES),
            "held": sum(reasons.get(c, 0) for c in HELD_CODES), "reasons": reasons, "block": queue_block(conn, cfg),
            "labels": {code: tr(label) for code, label in QUEUE_REASON_LABEL.items()}}


def queued_ids(conn, cfg: Config, code: str, limit: int) -> list[str]:
    """The sessions waiting for one reason ("ready" or "active"), newest first: what the Queue's Analyze now covers."""
    if code == "ready":
        return pending_sessions(conn, cfg, limit)
    ids = []
    for s in conn.execute(
            "SELECT id, analysis_status, source, analysis_attempts, analysis_reason, analysis_not_before, project_path, "
            "n_prompts, started_at, ended_at, ended_flag FROM sessions WHERE source NOT IN ('history', 'remote') "
            "AND analysis_status IN ('pending','stale','error') ORDER BY ended_at DESC"):
        got = waiting_reason(conn, cfg, s)
        if got and got[0] == code:
            ids.append(s["id"])
            if len(ids) >= limit:
                break
    return ids
