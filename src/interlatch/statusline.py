"""Claude Code status-line collector: real context and plan usage, recorded per session.

Claude Code hands its status-line command a JSON snapshot after every turn: the context window in use and, for
Pro and Max plans, how much of the 5-hour and 7-day limits are used. That is the only place those numbers exist,
so `interlatch install --statusline` points the status line at `interlatch statusline`, which

1. records the snapshot for its session (atomically: a reader never sees half a file), then
2. runs the status-line command the user already had, with the same input, and prints its output unchanged; with
   none, it prints a short line of its own.

The sync then copies each session's latest record into the database. Nothing is sent anywhere.

Claude Code runs this often and cancels a run that is still going when the next update arrives, so it is a fast
path: standard library only, the record is written before anything slow, and no error ever reaches the status line.
Sessions without a record (headless runs, other agents, the collector off) stay unknown, never zero.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WINDOWS = ("five_hour", "seven_day", "spend_limit")
WINDOW_LABEL = {"five_hour": "5h", "seven_day": "7d", "spend_limit": "spend"}
MAX_SEGMENTS = 24
KEEP_DAYS = 30
COMMAND_MARKER = " statusline"


def state_dir() -> Path:
    from .core.config import interlatch_home

    return interlatch_home() / "statusline"


def samples_dir() -> Path:
    return state_dir() / "sessions"


def wrapped_path() -> Path:
    return state_dir() / "wrapped.json"


def is_ours(status_line) -> bool:
    cmd = (status_line or {}).get("command") if isinstance(status_line, dict) else None
    return bool(cmd) and ("interlatch" in cmd or "chronicle" in cmd) and f"{COMMAND_MARKER} " in f" {cmd} "


# ------------------------------------------------------------------ the command Claude Code runs
def main() -> int:
    raw = sys.stdin.buffer.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    try:
        if isinstance(payload, dict):
            record(payload)
    except Exception as exc:  # a broken record must never break the user's status line
        print(f"interlatch statusline: {exc}", file=sys.stderr)
    wrapped = _read_json(wrapped_path())
    command = wrapped.get("command") if isinstance(wrapped, dict) else None
    if command:
        try:
            done = subprocess.run(command, shell=True, input=raw, capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"interlatch statusline: {exc}", file=sys.stderr)
            return 0
        sys.stdout.buffer.write(done.stdout)
        sys.stderr.buffer.write(done.stderr)
        return done.returncode
    sys.stdout.write(default_line(payload if isinstance(payload, dict) else {}))
    return 0


def default_line(payload: dict) -> str:
    """What the status line shows when the user had none: model, context, and plan limits when known."""
    parts = []
    model = (payload.get("model") or {}).get("display_name") if isinstance(payload.get("model"), dict) else None
    if model:
        parts.append(str(model))
    pct = _num((payload.get("context_window") or {}).get("used_percentage"))
    if pct is not None:
        parts.append(f"{pct:.0f}% context")
    limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
    for name in WINDOWS:
        used = _num((limits.get(name) or {}).get("used_percentage")) if isinstance(limits.get(name), dict) else None
        if used is not None:
            parts.append(f"{WINDOW_LABEL[name]} {used:.0f}%")
    return " · ".join(parts)


def record(payload: dict, now: datetime | None = None) -> Path | None:
    sid = payload.get("session_id")
    if not sid:
        return None
    path = samples_dir() / f"{_safe(str(sid))}.json"
    prev = _read_json(path)
    sample = merge(prev if isinstance(prev, dict) else {}, payload, now)
    _write_atomic(path, sample)
    return path


def merge(prev: dict, payload: dict, now: datetime | None = None) -> dict:
    """Fold one status-line snapshot into the session's running record."""
    now_iso = _iso(now or datetime.now(timezone.utc))
    out = dict(prev)
    out["session_id"] = payload.get("session_id")
    out.setdefault("first_seen", now_iso)
    out["last_seen"] = now_iso
    for key, value in (("transcript_path", payload.get("transcript_path")), ("cwd", payload.get("cwd")),
                       ("model", (payload.get("model") or {}).get("id") if isinstance(payload.get("model"), dict) else None),
                       ("version", payload.get("version"))):
        if value:
            out[key] = value
    ctx = payload.get("context_window") if isinstance(payload.get("context_window"), dict) else {}
    size, used = _num(ctx.get("context_window_size")), _num(ctx.get("used_percentage"))
    if size is not None or used is not None:
        c = dict(out.get("context") or {})
        if size is not None:
            c["size"] = int(size)
        if used is not None:
            c["used_pct"] = used
            c["peak_pct"] = max(used, _num(c.get("peak_pct")) or 0)
        out["context"] = c
    cost = _num((payload.get("cost") or {}).get("total_cost_usd")) if isinstance(payload.get("cost"), dict) else None
    if cost is not None:
        out["cc_cost_usd"] = cost
    limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
    windows = dict(out.get("limits") or {})
    for name in WINDOWS:
        w = limits.get(name)
        if not isinstance(w, dict):
            continue  # absent: not a Pro/Max plan, not yet known, or the window just reset
        used, resets = _num(w.get("used_percentage")), _reset_iso(w.get("resets_at"))
        if used is None or not resets:
            continue
        prev_w = dict(windows.get(name) or {})
        segments = dict(prev_w.get("segments") or {})
        first = segments.get(resets, [used, used])[0]
        segments[resets] = [first, used]
        segments = dict(sorted(segments.items())[-MAX_SEGMENTS:])
        windows[name] = {"used_pct": used, "resets_at": resets, "segments": segments,
                         "moved_pct": round(sum(max(0.0, b - a) for a, b in segments.values()), 2)}
    if windows:
        out["limits"] = windows
    return out


# ------------------------------------------------------------------ ingest into the database
def ingest(conn, now: float | None = None) -> int:
    """Copy each session's latest record into sessions.statusline_json; keep the newest plan limits in kv.

    A record whose session is not in the database yet stays for the next sync. Records idle for KEEP_DAYS go."""
    from .db import kv_get, kv_set

    folder = samples_dir()
    if not folder.is_dir():
        return 0
    now = now or time.time()
    updated = 0
    latest = _read_json_text(kv_get(conn, "plan_usage_latest"))
    for path in folder.glob("*.json"):
        sample = _read_json(path)
        if not isinstance(sample, dict) or not sample.get("session_id"):
            continue
        text = json.dumps(sample, ensure_ascii=False, sort_keys=True)
        cur = conn.execute("UPDATE sessions SET statusline_json = ? WHERE id = ? AND COALESCE(statusline_json, '') != ?",
                           (text, sample["session_id"], text))
        updated += cur.rowcount
        if sample.get("limits") and (sample.get("last_seen") or "") > ((latest or {}).get("as_of") or ""):
            latest = {"as_of": sample["last_seen"], "session_id": sample["session_id"],
                      "limits": {k: {"used_pct": v["used_pct"], "resets_at": v["resets_at"]} for k, v in sample["limits"].items()}}
        try:
            if now - path.stat().st_mtime > KEEP_DAYS * 86400:
                path.unlink()
        except OSError:
            pass
    if latest:
        kv_set(conn, "plan_usage_latest", json.dumps(latest, ensure_ascii=False))
    return updated


def plan_usage(conn) -> dict | None:
    """The newest plan limits seen, with windows whose reset has already passed dropped (they no longer apply)."""
    from .db import kv_get

    latest = _read_json_text(kv_get(conn, "plan_usage_latest"))
    if not latest:
        return None
    now = _iso(datetime.now(timezone.utc))
    limits = {k: v for k, v in (latest.get("limits") or {}).items() if (v.get("resets_at") or "") > now}
    return {**latest, "limits": limits}


def session_usage(statusline_json: str | None) -> dict | None:
    """What a session page shows: peak context and how far each plan limit moved while the session ran."""
    data = _read_json_text(statusline_json)
    if not data:
        return None
    ctx = data.get("context") or {}
    return {"context_peak_pct": ctx.get("peak_pct"), "context_size": ctx.get("size"),
            "limits_moved": {k: v.get("moved_pct") for k, v in (data.get("limits") or {}).items()},
            "cc_cost_usd": data.get("cc_cost_usd"), "last_seen": data.get("last_seen")}


# ------------------------------------------------------------------ helpers
def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _reset_iso(v) -> str | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            return _iso(datetime.fromtimestamp(v, timezone.utc))
        except (OverflowError, OSError, ValueError):
            return None
    return v if isinstance(v, str) and v else None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", key)[:200]


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _read_json_text(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)
