"""The sessions waiting for analysis: which are ready, why the others wait (still active, retry later, held), and
whether the queue is paused."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..core.config import Config
from ..core.i18n import tr
from ..core.util import local_str, one_line, parse_ts, to_iso, utcnow
from ..storage.kv import kv_get
from .runners.base import Runner
from .runners.factory import OFF, make_runner

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
    if cfg.analysis.backend == OFF:
        return tr("analysis is off (record only): pick an analyzer in Status › Analysis to analyze sessions")
    if not cfg.analysis.auto:
        return tr("automatic analysis is off (analysis.auto); analyze it from its page or with `interlatch analyze`")
    over = plan_over_limit(conn, cfg)
    if over:
        return tr("your Claude plan's {window} limit is at {pct}%, at or above the {cap}% you set; it resumes after {when}",
                  window=tr(WINDOW_LABEL.get(over["window"], over["window"])), pct=round(over["used_pct"]),
                  cap=cfg.analysis.plan_limit_pct, when=local_str(over["resets_at"], "%m-%d %H:%M"))
    runner = runner or make_runner(cfg)
    if not runner.available():
        return tr("{reason}, so nothing can be analyzed", reason=runner.unavailable_reason())
    return None


WINDOW_LABEL = {"five_hour": "session (5hr)", "seven_day": "weekly (7 day)", "spend_limit": "spend"}  # Claude Code's names


def plan_over_limit(conn, cfg: Config) -> dict | None:
    """The Claude plan window (statusline.py) at or above analysis.plan_limit_pct, while Claude Code analyzes: its
    name, used_pct and resets_at. None when the cap is off, another analyzer runs, or no window is that full."""
    cap = cfg.analysis.plan_limit_pct
    if not cap or cap <= 0 or cfg.analysis.backend != "claude":
        return None
    from ..runtime.statusline import plan_usage

    windows = (plan_usage(conn) or {}).get("limits") or {}
    over = [(name, w) for name, w in windows.items() if name in WINDOW_LABEL and (w.get("used_pct") or 0) >= cap]
    if not over:
        return None
    name, w = max(over, key=lambda x: x[1]["resets_at"])  # the one that frees up last
    return {"window": name, "used_pct": w["used_pct"], "resets_at": w["resets_at"]}


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


RUN_INTERVAL_S = 900  # the background agent: `interlatch sync --work` every 15 minutes (runtime/install.py)


def typical_analysis_s(conn) -> float:
    """How long one session's analysis takes on this computer: the median of the last 20 that got through."""
    took = sorted(r[0] for r in conn.execute(
        "SELECT (julianday(finished_at) - julianday(started_at)) * 86400 FROM analyses WHERE kind = 'session' "
        "AND status = 'done' AND started_at IS NOT NULL AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 20")
        if r[0] and r[0] > 0)
    return round(took[len(took) // 2], 1) if took else 60.0


def queue_list(conn, cfg: Config, limit: int = 200) -> dict:
    """The sessions queued for analysis (the ones the status bar counts), each with its reason and `eta`: when it
    should be analyzed. The background runs are played forward: one every RUN_INTERVAL_S after the last sync, each
    taking max_per_run sessions in pending_sessions() order, a session joining the first run after it is ready.
    No eta while the whole queue is stopped by something that needs a change (automatic analysis off, no agent)."""
    now = utcnow()
    block = queue_block(conn, cfg)
    paused = kv_get(conn, PAUSE_KEY)
    paused = parse_ts(paused) if paused and paused > to_iso(now) else None
    each = typical_analysis_s(conn)
    items = []
    for s in conn.execute(
            "SELECT id, title, project_name, project_path, agent, analysis_status, source, analysis_attempts, analysis_reason, "
            "analysis_not_before, n_prompts, started_at, ended_at, ended_flag FROM sessions WHERE source NOT IN ('history', 'remote') "
            "AND analysis_status IN ('pending','stale','error') ORDER BY ended_flag DESC, ended_at DESC"):
        got = waiting_reason(conn, cfg, s)
        if not got or got[0] not in QUEUED_CODES:
            continue
        code, why = got
        ready_at = (parse_ts(s["analysis_not_before"]) if code == "retry"
                    else (parse_ts(s["ended_at"]) or now) + timedelta(minutes=cfg.analysis.idle_minutes) if code == "active"
                    else now)
        items.append({"id": s["id"], "title": s["title"], "project": s["project_name"], "agent": s["agent"],
                      "prompts": s["n_prompts"], "ended_at": s["ended_at"], "code": code,
                      "label": tr(QUEUE_REASON_LABEL[code]), "why": why, "stale": s["analysis_status"] == "stale",
                      "ready_at": max(ready_at or now, now), "eta": None})

    next_run = None
    if not cfg.sends_files and (not block or paused):  # a pause ends by itself; the other blocks need a change
        last = parse_ts(kv_get(conn, "last_sync"))
        slot = max(last + timedelta(seconds=RUN_INTERVAL_S), now) if last else now + timedelta(seconds=RUN_INTERVAL_S)
        while paused and slot < paused:
            slot += timedelta(seconds=RUN_INTERVAL_S)
        next_run = slot
        left, per_run = list(items), max(1, cfg.analysis.max_per_run)
        for _ in range(2000):  # a guard: a few thousand queued sessions is weeks of runs
            if not left:
                break
            due = [x for x in left if x["ready_at"] <= slot][:per_run]
            if not due:  # nothing ready by this run: skip to the first run after the next one is
                first = min(x["ready_at"] for x in left)
                while slot < first:
                    slot += timedelta(seconds=RUN_INTERVAL_S)
                continue
            for i, x in enumerate(due):
                x["eta"] = to_iso(slot + timedelta(seconds=each * (i + 1)))
            left = [x for x in left if x["eta"] is None]
            slot += timedelta(seconds=RUN_INTERVAL_S)
        items.sort(key=lambda x: x["eta"] or "~")  # stable: the worker's order within one run
    for x in items:
        x["ready_at"] = to_iso(x["ready_at"])
    return {"sessions": items[:limit], "total": len(items), "ready": sum(x["code"] == "ready" for x in items),
            "active": sum(x["code"] == "active" for x in items), "each_s": each, "per_run": cfg.analysis.max_per_run,
            "interval_s": RUN_INTERVAL_S, "next_run": to_iso(next_run), "block": block,
            "paused_until": to_iso(paused), "on_hub": bool(cfg.sends_files)}
