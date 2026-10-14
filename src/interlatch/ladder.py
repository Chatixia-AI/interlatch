"""Knowledge maturity: every item carries a stage that it earns by recurring across sessions.

    wip → provisional → established → canonical

A new item starts at `provisional` (`wip` when the analysis was unsure), or at `established` when Claude Code's own
memory or the user wrote it. It climbs only on evidence: synthesis reports that another session's item states the
same lesson (a duplicate), and the two items' sessions are pooled. Two distinct sessions make an item `established`;
three spread over at least two weeks, or a pin, make it `canonical`. The stage is recomputed from that evidence, never
guessed, and `stage_reason` says what earned it.

Leaving the ladder is a status change (`superseded`, `dismissed`), not a stage: a superseded item keeps the stage it
had, names its successor in `superseded_by` when there is one, and says why in `superseded_reason`. An established or
canonical item that is overturned (not merely merged as a duplicate) is listed in that week's review.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime

from .i18n import lang, tr
from .util import dumps, loads, utcnow_iso

STAGES = ("wip", "provisional", "established", "canonical")
STAGE_RANK = {s: i for i, s in enumerate(STAGES)}
SUPERSEDE_REASONS = ("duplicate", "outdated", "contradicted")
TRUSTED = ("established", "canonical")
MEMORY_STAGE_REASON = "written by the agent's own memory"

ESTABLISHED_SESSIONS = 2
CANONICAL_SESSIONS = 3
CANONICAL_SPAN_DAYS = 14
MAX_CONFIRMATIONS = 50  # session ids kept per item; the count beyond this no longer changes the stage

# ORDER BY fragment: most trusted first (expects the knowledge table aliased as `k`)
STAGE_ORDER_SQL = ("CASE k.stage WHEN 'canonical' THEN 0 WHEN 'established' THEN 1 WHEN 'provisional' THEN 2 "
                   "WHEN 'wip' THEN 3 ELSE 2 END")


def confirmations(row) -> list[str]:
    """Distinct session ids that state this item's lesson (its own session first)."""
    ids = [str(x) for x in (loads(_get(row, "confirmed_json"), []) or []) if x]
    own = _get(row, "session_id")
    if own and own not in ids:
        ids.insert(0, own)
    seen: set[str] = set()
    return [x for x in ids if not (x in seen or seen.add(x))]


def stage_label(row) -> str:
    """Compact trust tag for listings, e.g. `established ×3`."""
    stage = _get(row, "stage") or "provisional"
    n = len(confirmations(row))
    return f"{stage} ×{n}" if n > 1 else stage


def compute_stage(conn: sqlite3.Connection, row) -> tuple[str, str]:
    """(stage, reason) from the evidence an item carries; pure apart from looking up session dates."""
    sessions = confirmations(row)
    n = len(sessions)
    days = _session_days(conn, sessions)
    span = (days[-1] - days[0]).days if len(days) >= 2 else 0
    when = f"{days[0]:%Y-%m-%d} → {days[-1]:%Y-%m-%d}" if len(days) >= 2 else ""
    if _get(row, "pinned"):
        return "canonical", "pinned by you"
    if n >= CANONICAL_SESSIONS and span >= CANONICAL_SPAN_DAYS:
        return "canonical", f"confirmed in {n} sessions over {span} days ({when})"
    if n >= ESTABLISHED_SESSIONS:
        return "established", f"confirmed in {n} sessions" + (f" ({when})" if when else "")
    source = _get(row, "source")
    if source == "memory":
        return "established", MEMORY_STAGE_REASON
    if source == "manual":
        return "established", "added by you"
    if _get(row, "confidence") == "low":
        return "wip", "one session, low confidence"
    return "provisional", "one session"


def reason_text(reason: str | None) -> str | None:
    """A stored stage reason (compute_stage writes it in English) in the dashboard viewer's language (i18n)."""
    if not reason or lang.get() == "en":
        return reason
    m = re.fullmatch(r"confirmed in (\d+) sessions over (\d+) days \((.+)\)", reason)
    if m:
        return tr("confirmed in {n} sessions over {days} days ({when})", n=m.group(1), days=m.group(2), when=m.group(3))
    m = re.fullmatch(r"confirmed in (\d+) sessions(?: \((.+)\))?", reason)
    if m:
        return (tr("confirmed in {n} sessions ({when})", n=m.group(1), when=m.group(2)) if m.group(2)
                else tr("confirmed in {n} sessions", n=m.group(1)))
    return tr(reason)  # pinned by you, added by you, one session, ...


def initial_stage(item: dict, *, source: str = "analysis") -> str:
    if source in ("memory", "manual"):
        return "established"
    return "wip" if item.get("confidence") == "low" else "provisional"


def refresh(conn: sqlite3.Connection, ids) -> None:
    """Recompute stage and reason for these items from their evidence."""
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return
    rows = conn.execute(f"SELECT * FROM knowledge WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    for r in rows:
        stage, reason = compute_stage(conn, r)
        if stage != r["stage"] or reason != r["stage_reason"]:
            conn.execute("UPDATE knowledge SET stage = ?, stage_reason = ? WHERE id = ?", (stage, reason, r["id"]))


def refresh_all(conn: sqlite3.Connection) -> None:
    refresh(conn, [r[0] for r in conn.execute("SELECT id FROM knowledge")])


def apply_synthesis(conn: sqlite3.Connection, superseded: list[dict], valid_ids: set[int], *, retire: bool) -> list[int]:
    """Apply what a synthesis reported about its items. Returns the ids it retired.

    `superseded` entries are {id, by, reason}. A duplicate pools its sessions into the item it duplicates, which is
    how knowledge climbs. With `retire` (a project's own synthesis) the reported items also leave the ladder; the
    cross-project playbook only pools evidence, since a duplicate across projects stays in each project.
    Pinned items and Claude Code memory items are never retired, as before.
    """
    now = utcnow_iso()
    touched: set[int] = set()
    retired: list[int] = []
    for entry in superseded:
        kid, by, reason = entry.get("id"), entry.get("by"), entry.get("reason") or "outdated"
        if kid not in valid_ids or kid == by:
            continue
        by = by if by in valid_ids else None
        row = conn.execute("SELECT * FROM knowledge WHERE id = ?", (kid,)).fetchone()
        if row is None:
            continue
        if reason == "duplicate" and by is not None:
            survivor = conn.execute("SELECT * FROM knowledge WHERE id = ?", (by,)).fetchone()
            pooled = _pool(confirmations(survivor), confirmations(row))
            conn.execute("UPDATE knowledge SET confirmed_json = ? WHERE id = ?", (dumps(pooled), by))
            touched.add(by)
            if not retire:
                conn.execute("UPDATE knowledge SET confirmed_json = ? WHERE id = ?", (dumps(_pool(confirmations(row), pooled)), kid))
                touched.add(kid)
        if not retire or row["pinned"] or row["source"] == "memory" or row["status"] != "active":
            continue
        conn.execute(
            "UPDATE knowledge SET status = 'superseded', superseded_by = ?, superseded_reason = ?, superseded_at = ?, "
            "updated_at = ? WHERE id = ?", (by, reason, now, now, kid))
        retired.append(kid)
    refresh(conn, touched)
    return retired


def before_reanalysis(conn: sqlite3.Connection, session_id: str) -> dict[str, dict]:
    """Re-analysis replaces a session's items. Keep the evidence they earned, keyed by fingerprint, and put back
    the items that were merged into them, since their successor is about to disappear."""
    rows = conn.execute(
        "SELECT * FROM knowledge WHERE session_id = ? AND source = 'analysis' AND pinned = 0 AND status != 'dismissed'",
        (session_id,)).fetchall()
    kept = {r["fingerprint"]: {"confirmed": confirmations(r)} for r in rows if r["fingerprint"]}
    ids = [r["id"] for r in rows]
    if ids:
        marks = ",".join("?" * len(ids))
        orphans = [r[0] for r in conn.execute(
            f"SELECT id FROM knowledge WHERE superseded_by IN ({marks}) AND superseded_reason = 'duplicate' "
            f"AND status = 'superseded'", ids)]
        if orphans:
            conn.execute(
                f"UPDATE knowledge SET status = 'active', superseded_by = NULL, superseded_reason = NULL, superseded_at = NULL "
                f"WHERE id IN ({','.join('?' * len(orphans))})", orphans)
        conn.execute(f"UPDATE knowledge SET superseded_by = NULL WHERE superseded_by IN ({marks})", ids)
    return kept


def after_reanalysis(conn: sqlite3.Connection, session_id: str, kept: dict[str, dict]) -> None:
    rows = conn.execute("SELECT id, fingerprint FROM knowledge WHERE session_id = ? AND source = 'analysis'",
                        (session_id,)).fetchall()
    ids = []
    for r in rows:
        prev = kept.get(r["fingerprint"])
        if prev and len(prev["confirmed"]) > 1:
            conn.execute("UPDATE knowledge SET confirmed_json = ? WHERE id = ?", (dumps(prev["confirmed"]), r["id"]))
        ids.append(r["id"])
    refresh(conn, ids)


def overturned(conn: sqlite3.Connection, since: str, until: str) -> list[dict]:
    """Trusted knowledge that a newer session overturned in this window (merged duplicates are not news)."""
    return [dict(r) for r in conn.execute(
        "SELECT k.id, k.kind, k.title, k.project_name, k.stage, k.superseded_reason, k.superseded_by, "
        "n.title AS successor_title FROM knowledge k LEFT JOIN knowledge n ON n.id = k.superseded_by "
        "WHERE k.status = 'superseded' AND k.stage IN ('established', 'canonical') "
        "AND COALESCE(k.superseded_reason, '') != 'duplicate' AND k.superseded_at >= ? AND k.superseded_at < ? "
        "ORDER BY k.superseded_at", (since, until))]


def bullet_trust(conn: sqlite3.Connection, sources: list[int]) -> dict | None:
    """The best-supported source of a knowledge-base bullet: its stage and how many sessions back it."""
    if not sources:
        return None
    rows = conn.execute(f"SELECT * FROM knowledge WHERE id IN ({','.join('?' * len(sources))})", sources).fetchall()
    if not rows:
        return None
    best = max(rows, key=lambda r: (STAGE_RANK.get(r["stage"] or "provisional", 1), len(confirmations(r))))
    sessions: set[str] = set()
    for r in rows:
        sessions.update(confirmations(r))
    return {"stage": best["stage"] or "provisional", "sessions": len(sessions)}


def trust_tag(trust: dict | None) -> str:
    if not trust:
        return ""
    return trust["stage"] + (f" ×{trust['sessions']}" if trust["sessions"] > 1 else "")


def _pool(a: list[str], b: list[str]) -> list[str]:
    out = list(a)
    for x in b:
        if x not in out:
            out.append(x)
    return out[:MAX_CONFIRMATIONS]


def _session_days(conn: sqlite3.Connection, sessions: list[str]) -> list[datetime]:
    if not sessions:
        return []
    rows = conn.execute(
        f"SELECT started_at FROM sessions WHERE id IN ({','.join('?' * len(sessions))}) AND started_at IS NOT NULL",
        sessions).fetchall()
    out = []
    for (ts,) in rows:
        try:
            out.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
        except (ValueError, AttributeError):
            continue
    return sorted(out)


def _get(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None
