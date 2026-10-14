"""Ingest: discover transcripts, archive raw files forever, parse, and store.

Claude Code deletes transcripts after `cleanupPeriodDays` (30 by default). Every file under
projects/ is mirrored into the archive (JSONL gzip-compressed) and never removed there, so the
vault keeps sessions after the originals are gone.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path

from . import artifacts, news
from .config import Config
from .db import kv_get, kv_set
from .ladder import MEMORY_STAGE_REASON
from .codex_parser import (CODEX_PARSER_VERSION, load_imports, load_titles, parent_thread_id, parse_codex_session,
                           read_meta, rollout_files, rollout_id)
from .antigravity_parser import (ANTIGRAVITY_PARSER_VERSION, LOGS, conversation_dirs, load_db_meta, load_title,
                                 parse_antigravity_conversation, transcript_files)
from .bob_parser import BOB_PARSER_VERSION, bob_db, load_tasks, parse_bob_task, task_folder, task_signature
from .copilot_parser import (COPILOT_PARSER_VERSION, agent_session_dirs, chat_files, load_usage, parse_copilot_agent,
                             parse_vscode_chat, workspace_folder)
from .parser import PARSER_VERSION, ParsedSession, parse_history, parse_session, session_dir_for
from .util import dumps, file_lock, one_line, parse_ts, safe_text, to_iso, utcnow_iso

log = logging.getLogger("interlatch.ingest")


@dataclass
class SyncReport:
    sessions_new: int = 0
    sessions_updated: int = 0
    sessions_unchanged: int = 0
    sessions_missing_source: int = 0
    files_archived: int = 0
    bytes_archived: int = 0
    history_sessions: int = 0
    memory_items: int = 0
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    touched: list[str] = field(default_factory=list)
    analyses_imported: int = 0

    def summary(self) -> str:
        return (
            f"{self.sessions_new} new, {self.sessions_updated} updated, {self.sessions_unchanged} unchanged sessions; "
            f"{self.files_archived} files archived ({self.bytes_archived / 1e6:.1f} MB); "
            f"{self.history_sessions} history-only sessions; {self.memory_items} memory notes; "
            + (f"{self.analyses_imported} analyses from other computers; " if self.analyses_imported else "")
            + f"{len(self.errors)} errors in {self.seconds:.1f}s"
        )


# ------------------------------------------------------------------ paths
def archive_key(claude_dir: Path) -> str:
    from .hub import MACHINE_RE, ROOT_RE

    if claude_dir.parent.parent.name == "machines" and MACHINE_RE.match(claude_dir.parent.name) and ROOT_RE.match(claude_dir.name):
        return f"machines/{claude_dir.parent.name}/{claude_dir.name}"  # received from another computer (hub.py)
    for name in ("claude", "codex"):
        if claude_dir.resolve() == Path(f"~/.{name}").expanduser().resolve():
            return name
    return re.sub(r"[^\w.-]+", "_", str(claude_dir).strip("/"))


def project_name_for(path: str | None, project_dir: str | None = None) -> str:
    if path:
        return Path(path.rstrip("/")).name or path
    if project_dir:
        return project_dir.strip("-").split("-")[-1]
    return "unknown"


def peek_cwd(proj: Path) -> str | None:
    """The working directory recorded in the first lines of a project's transcripts (exact, unlike the folder name)."""
    import json

    for main in sorted(proj.glob("*.jsonl"))[:3]:
        try:
            with open(main, errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i > 60:
                        break
                    if '"cwd"' not in line:
                        continue
                    try:
                        cwd = json.loads(line).get("cwd")
                    except ValueError:
                        continue
                    if cwd:
                        return cwd
        except OSError:
            continue
    return None


def decode_project_dir(name: str) -> str:
    """Best-effort inverse of Claude Code's cwd -> folder-name encoding (lossy: '/' and '.' become '-')."""
    tokens = name[1:].split("-") if name.startswith("-") else name.split("-")
    path = Path("/")
    i = 0
    while i < len(tokens):
        matched = False
        for j in range(len(tokens), i, -1):
            for sep in ("-", ".", "_", " "):
                cand = sep.join(tokens[i:j])
                if not cand:
                    continue
                if (path / cand).exists():
                    path, i, matched = path / cand, j, True
                    break
                if tokens[i] == "" and j > i + 1 and (path / ("." + "-".join(tokens[i + 1 : j]))).exists():
                    path, i, matched = path / ("." + "-".join(tokens[i + 1 : j])), j, True
                    break
            if matched:
                break
        if not matched:
            return str(path / "/".join(t for t in tokens[i:] if t))
    return str(path)


# ------------------------------------------------------------------ archive
class Archiver:
    def __init__(self, conn: sqlite3.Connection, report: SyncReport):
        self.conn = conn
        self.report = report
        self.state = {
            r[0]: (r[1], r[2], r[3])
            for r in conn.execute("SELECT path, size, mtime, archive_path FROM files_state").fetchall()
        }

    def archive(self, src: Path, dst: Path) -> Path:
        """Copy src into the archive if it changed; .jsonl files are gzip-compressed."""
        try:
            st = src.stat()
        except FileNotFoundError:
            return dst
        if src.suffix == ".jsonl":
            dst = dst.with_name(dst.name + ".gz")
        prev = self.state.get(str(src))
        if prev and prev[0] == st.st_size and prev[1] == st.st_mtime and dst.exists():
            return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + f".tmp{os.getpid()}")
        try:
            if src.suffix == ".jsonl":
                with open(src, "rb") as fin, gzip.open(tmp, "wb", compresslevel=6) as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)
            else:
                shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            self.report.errors.append(f"archive {src}: {exc}")
            return dst
        self.conn.execute(
            "INSERT INTO files_state(path, size, mtime, archive_path, archived_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
            "archive_path=excluded.archive_path, archived_at=excluded.archived_at",
            (str(src), st.st_size, st.st_mtime, str(dst), utcnow_iso()),
        )
        self.conn.commit()  # never hold the write lock while compressing/parsing the next file
        self.state[str(src)] = (st.st_size, st.st_mtime, str(dst))
        self.report.files_archived += 1
        self.report.bytes_archived += st.st_size
        return dst

    def archive_tree(self, src_root: Path, dst_root: Path) -> None:
        for dirpath, dirnames, filenames in os.walk(src_root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            rel = Path(dirpath).relative_to(src_root)
            for fn in filenames:
                if fn.startswith("."):
                    continue
                self.archive(Path(dirpath) / fn, dst_root / rel / fn)


def files_signature(main: Path, session_dir: Path) -> str:
    h = hashlib.sha1()
    parts = []
    for p in [main, *(sorted(session_dir.rglob("*")) if session_dir.is_dir() else [])]:
        if p.is_file() and (p.name.endswith((".jsonl", ".jsonl.gz")) or p.name.endswith(".json")):
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            parts.append(f"{p.name}:{st.st_size}:{int(st.st_mtime)}")
    h.update("|".join(parts).encode())
    h.update(f"v{PARSER_VERSION}".encode())
    return h.hexdigest()


def paths_signature(paths, version: int) -> str:
    h = hashlib.sha1()
    parts = []
    for p in paths:
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        parts.append(f"{p.name}:{st.st_size}:{int(st.st_mtime)}")
    h.update("|".join(parts).encode())
    h.update(f"v{version}".encode())
    return h.hexdigest()


# ------------------------------------------------------------------ store
def local_machine_id(cfg: Config) -> str:
    from .hub import local_machine

    return local_machine(cfg)["id"]


def _json(counter) -> str:
    if isinstance(counter, Counter):
        return dumps(dict(counter.most_common()))
    return dumps(counter)


def best_title(row: dict) -> str:
    for key in ("llm_title", "custom_title", "ai_title"):
        if row.get(key):
            return one_line(row[key], 120)
    return one_line(row.get("first_prompt") or "(no prompt)", 90)


def store_parsed(
    conn: sqlite3.Connection,
    cfg: Config,
    ps: ParsedSession,
    *,
    claude_dir: Path,
    project_dir: str | None,
    transcript_path: Path,
    archive_path: Path,
    files_sig: str,
    ended: bool = False,
    agent: str = "claude",
    source: str = "transcript",
) -> str:
    """Replace a session's derived rows and upsert its metadata. Returns 'new' or 'updated', or 'foreign' (nothing
    changed) when another computer's session already has this id."""
    from .hub import machine_of, resolver

    prev = conn.execute(
        "SELECT analysis_status, analyzed_prompts, ended_flag, ended_at, llm_title, analysis_reason, source, n_prompts, "
        "parser_version, machine_id FROM sessions WHERE id = ?",
        (ps.id,),
    ).fetchone()
    sid = ps.id
    machine = machine_of(cfg, claude_dir)
    me = local_machine_id(cfg)
    if prev is not None and (prev["machine_id"] or me) != (machine or me):
        # a hub: a transcript a computer sent under the id of a session another computer (or the hub) has
        log.warning("session %s from %s not stored: it belongs to %s", sid, machine or me, prev["machine_id"] or me)
        return "foreign"
    for table in ("events", "tool_calls", "session_files", "subagents", "api_calls", "artifacts"):
        conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (sid,))
    artifacts.finish(ps)

    conn.executemany(
        "INSERT INTO events(session_id, agent_id, seq, ts, role, kind, tool_name, tool_use_id, is_error, searchable, text, meta_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (sid, e.agent_id, e.seq, e.ts, e.role, e.kind, e.tool_name, e.tool_use_id, int(e.is_error),
             int(e.searchable), e.text, dumps(e.meta) if e.meta else None)
            for e in ps.events
        ],
    )
    conn.executemany(
        "INSERT INTO tool_calls(session_id, agent_id, tool_use_id, ts, name, mcp_server, summary, file_path, command, "
        "is_error, duration_ms, result_chars) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (sid, t.agent_id, t.tool_use_id, t.ts, t.name, t.mcp_server, t.summary, t.file_path,
             (t.command or "")[:4000] or None, int(t.is_error), t.duration_ms, t.result_chars)
            for t in ps.tool_calls
        ],
    )
    conn.executemany(
        "INSERT INTO session_files(session_id, path, reads, edits, writes, lines_added, lines_removed) VALUES (?,?,?,?,?,?,?)",
        [(sid, p, f.reads, f.edits, f.writes, f.lines_added, f.lines_removed) for p, f in ps.files.items()],
    )
    conn.executemany(
        "INSERT INTO subagents(session_id, agent_id, agent_type, description, workflow_id, phase, tool_use_id, model, "
        "started_at, ended_at, n_tool_calls, n_tool_errors, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, est_cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (sid, s.agent_id, s.agent_type, s.description, s.workflow_id, s.phase, s.tool_use_id, s.model,
             s.started_at, s.ended_at, s.n_tool_calls, s.n_tool_errors, s.input_tokens, s.output_tokens,
             s.cache_read_tokens, s.cache_write_tokens, round(s.est_cost_usd, 6))
            for s in ps.subagents.values()
        ],
    )
    conn.executemany(
        "INSERT INTO api_calls(session_id, agent_id, msg_id, ts, model, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, cost_usd, speed) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (sid, c.agent_id, c.msg_id, c.ts, c.model, c.input_tokens, c.output_tokens, c.cache_read_tokens,
             c.cache_write_tokens, round(c.cost_usd, 6), c.speed)
            for c in ps.api_calls
        ],
    )

    seqs = {(e.agent_id, e.tool_use_id): e.seq for e in ps.events if e.kind == "tool_use" and e.tool_use_id}
    conn.executemany(
        "INSERT INTO artifacts(session_id, agent_id, tool_use_id, seq, ts, key, kind, action, title, path, url, size, sha256, "
        "versions, meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (sid, o.get("agent_id") or "", o.get("tool_use_id"), seqs.get((o.get("agent_id") or "", o.get("tool_use_id"))),
             o.get("ts"), o["key"], o["kind"], o.get("action"), o.get("title"), o.get("path"), o.get("url"), o.get("size"),
             o.get("sha256"), o.get("versions") or 1, artifacts.meta_json(o))
            for o in ps.outputs
        ],
    )

    totals = ps.totals()
    recorded_path = ps.project_path or (decode_project_dir(project_dir) if project_dir else None)
    # another computer's folder as this hub files it; this computer's own, under a project set up here if any
    project_path = resolver(cfg, conn).resolve(machine, recorded_path, ps.git_remote)
    excluded = cfg.is_excluded(project_path)

    # analysis state machine
    grew = prev is not None and (ps.ended_at or "") > (prev["ended_at"] or "")
    analyzed = prev["analyzed_prompts"] if prev is not None else None
    # a newer parser dropped duplicated prompts the analysis had counted: it still covered every real one
    recounted = bool(analyzed) and ps.n_prompts < analyzed and prev["parser_version"] != PARSER_VERSION
    if recounted:
        analyzed = ps.n_prompts
    if prev is None or prev["source"] == "history":
        status, reason = "pending", None  # new, or a history-only stub whose transcript has now appeared
    else:
        status, reason = prev["analysis_status"], prev["analysis_reason"]
        if status == "done" and (ps.n_prompts > (analyzed or 0) or (recounted and grew)):
            status, reason = "stale", "session continued after analysis"
        elif status == "skipped" and reason in ("too few prompts", "too little content", "excluded project") \
                and ps.n_prompts > (prev["n_prompts"] or 0):
            status, reason = "pending", None  # it has more to say now
        # "running" stays as is; store_analysis marks it stale if prompts arrived meanwhile
    if status in ("pending", "stale") and ps.n_prompts < cfg.analysis.min_prompts:
        status, reason = "skipped", "too few prompts"
    if excluded:
        status, reason = "skipped", "excluded project"
    # SessionEnd marks a session ended; if it keeps growing afterwards (claude --resume), it is live again
    ended_flag = 1 if ended else (1 if prev is not None and prev["ended_flag"] and not grew else 0)

    row = {
        "id": sid,
        "source": source,
        "agent": agent,
        "claude_dir": str(claude_dir),
        "project_dir": project_dir,
        "project_path": project_path,
        "project_name": project_name_for(project_path, project_dir or ""),
        "transcript_path": str(transcript_path),
        "archive_path": str(archive_path),
        "source_present": 1,
        "files_sig": files_sig,
        "parser_version": PARSER_VERSION,
        "ingested_at": utcnow_iso(),
        "ai_title": ps.custom_title or ps.ai_title,
        "first_prompt": ps.first_prompt,
        "last_prompt": ps.last_prompt,
        "started_at": ps.started_at,
        "ended_at": ps.ended_at,
        "duration_s": ps.duration_s,
        "active_s": ps.active_s,
        "git_branch": ps.git_branch,
        "cc_version": ps.cc_version,
        "entrypoint": ps.entrypoint,
        "permission_mode": ps.permission_mode,
        "primary_model": ps.primary_model,
        "models_json": _json(ps.models),
        "tools_json": _json(ps.tools),
        "skills_json": _json(ps.skills),
        "mcp_json": _json(ps.mcp_servers),
        "commands_json": _json(ps.commands),
        "branches_json": _json(ps.branches),
        "prs_json": dumps(ps.prs),
        "artifacts_json": dumps(ps.artifacts),
        "workflows_json": dumps(ps.workflows),
        "hooks_json": dumps(ps.hooks),
        "n_prompts": ps.n_prompts,
        "n_api_calls": ps.n_api_calls,
        "n_tool_calls": len(ps.tool_calls),
        "n_tool_errors": ps.n_tool_errors,
        "n_interrupts": ps.n_interrupts,
        "n_compactions": ps.n_compactions,
        "n_api_errors": ps.n_api_errors,
        "n_subagents": len(ps.subagents),
        "n_images": ps.n_images,
        "n_result_images": ps.n_result_images,
        "n_files": len(ps.files),
        "n_events": len(ps.events),
        "lines_added": ps.lines_added,
        "lines_removed": ps.lines_removed,
        "input_tokens": totals.get("input_tokens", 0),
        "output_tokens": totals.get("output_tokens", 0),
        "cache_read_tokens": totals.get("cache_read_tokens", 0),
        "cache_write_tokens": totals.get("cache_write_tokens", 0),
        "sub_tokens": totals.get("sub_tokens", 0),
        "est_cost_usd": round(totals.get("cost", 0.0), 4),
        "sub_cost_usd": round(totals.get("sub_cost", 0.0), 4),
        "cc_cost_usd": ps.cc_cost_usd,
        "peak_context": ps.peak_context,
        "ended_flag": ended_flag,
        "analysis_status": status,
        "analysis_reason": reason,
        "machine_id": machine or local_machine_id(cfg),
        "machine_path": recorded_path if recorded_path != project_path else None,
    }
    if prev is not None and analyzed != prev["analyzed_prompts"]:
        row["analyzed_prompts"] = analyzed
    row["title"] = best_title({"llm_title": prev["llm_title"] if prev else None, "ai_title": row["ai_title"],
                               "first_prompt": row["first_prompt"]})
    cols = list(row)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
    conn.execute(
        f"INSERT INTO sessions({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        [row[c] for c in cols],
    )
    if prev is None and machine and machine != me and source != "history":  # the Team page's news (news.py)
        news.record(conn, machine, project_path, sessions=1)
    return "new" if prev is None else "updated"


# ------------------------------------------------------------------ sync
def _ingest_main(conn, cfg, archiver, claude_dir, proj: Path, main: Path, report: SyncReport, *, ended=False, force=False):
    sid = main.stem
    session_dir = session_dir_for(main)
    sig = files_signature(main, session_dir)
    row = conn.execute("SELECT files_sig, parser_version, ended_flag FROM sessions WHERE id = ?", (sid,)).fetchone()
    archive_root = cfg.archive_dir / archive_key(claude_dir) / "projects" / proj.name
    archive_main = archive_root / (main.name + ".gz")
    if row and row["files_sig"] == sig and row["parser_version"] == PARSER_VERSION and not force:
        if ended and not row["ended_flag"]:
            conn.execute("UPDATE sessions SET ended_flag = 1 WHERE id = ?", (sid,))
        report.sessions_unchanged += 1
        return
    conn.commit()  # parsing can take seconds: hold no write transaction meanwhile
    try:
        ps = parse_session(main, session_dir)
    except Exception as exc:  # never let one corrupt file stop the sync
        log.exception("parse failed: %s", main)
        report.errors.append(f"parse {main}: {exc}")
        return
    if cfg.is_excluded(ps.project_path):
        return
    if kv_get(conn, f"forget:{sid}"):  # forgotten while this sync was running
        return
    try:
        result = store_parsed(
            conn, cfg, ps, claude_dir=claude_dir, project_dir=proj.name, transcript_path=main,
            archive_path=archive_main, files_sig=sig, ended=ended,
        )
        conn.commit()
    except Exception as exc:  # one unstorable session must never block the rest of the sync
        conn.rollback()
        log.exception("store failed: %s", main)
        report.errors.append(f"store {main}: {exc}")
        return
    if result == "foreign":
        return
    report.touched.append(sid)
    if result == "new":
        report.sessions_new += 1
    else:
        report.sessions_updated += 1


def _reparse_archived(conn, cfg, report: SyncReport, skip: set[str]) -> None:
    """Re-parse, from the vault's copy, Claude Code sessions an older parser stored whose transcript is gone."""
    rows = conn.execute(
        "SELECT id, claude_dir, project_dir, transcript_path, archive_path FROM sessions "
        "WHERE agent = 'claude' AND source = 'transcript' AND archive_path IS NOT NULL "
        "AND (parser_version IS NULL OR parser_version != ?)",
        (PARSER_VERSION,),
    ).fetchall()
    for r in rows:
        main = Path(r["archive_path"])
        if r["id"] in skip or not main.exists() or (r["transcript_path"] and Path(r["transcript_path"]).exists()):
            continue  # a live transcript is re-parsed by the normal pass
        session_dir = session_dir_for(main)
        conn.commit()
        try:
            ps = parse_session(main, session_dir)
            ps.id = r["id"]
        except Exception as exc:
            log.exception("parse failed: %s", main)
            report.errors.append(f"parse {main}: {exc}")
            continue
        if cfg.is_excluded(ps.project_path) or kv_get(conn, f"forget:{ps.id}"):
            continue
        try:
            if store_parsed(conn, cfg, ps, claude_dir=Path(r["claude_dir"]), project_dir=r["project_dir"],
                            transcript_path=Path(r["transcript_path"] or main), archive_path=main,
                            files_sig=files_signature(main, session_dir)) == "foreign":
                continue
            conn.execute("UPDATE sessions SET source_present = 0 WHERE id = ?", (ps.id,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            log.exception("store failed: %s", main)
            report.errors.append(f"store {main}: {exc}")
            continue
        report.touched.append(ps.id)
        report.sessions_updated += 1


def sync(cfg: Config, conn: sqlite3.Connection, *, only: Path | None = None, ended: bool = False,
         force: bool = False, lock_timeout: float = 900) -> SyncReport:
    """Archive + ingest everything (or a single transcript when `only` is given)."""
    report = SyncReport()
    t0 = time.monotonic()
    with file_lock(cfg.locks_dir / "ingest.lock", timeout=lock_timeout) as got:
        if not got:
            report.errors.append("another sync is running")
            return report
        archiver = Archiver(conn, report)
        skip = forgotten_ids(conn)
        from .hub import apply_analyses, machine_roots, register_local, resolver

        me = register_local(conn, cfg)
        conn.execute("UPDATE sessions SET machine_id = ? WHERE machine_id IS NULL", (me,))  # recorded before machines existed
        conn.commit()
        resolver(cfg, conn, fresh=True)
        received = machine_roots(cfg)  # on a hub: the folders other computers sent
        claude_dirs = [*cfg.claude_dirs, *(d for _m, kind, d in received if kind == "claude" and not only)]
        codex_dirs = [*cfg.codex_dirs, *(d for _m, kind, d in received if kind == "codex")]
        if only:  # the hook's single transcript may be a received one too
            claude_dirs += [d for _m, kind, d in received if kind == "claude" and only.is_relative_to(d)]
        for claude_dir in claude_dirs:
            projects = claude_dir / "projects"
            if not projects.is_dir():
                continue
            key = archive_key(claude_dir)
            proj_dirs = [only.parent] if only else sorted(p for p in projects.iterdir() if p.is_dir())
            known_paths = {r[0]: r[1] for r in conn.execute(
                "SELECT project_dir, project_path FROM sessions WHERE project_dir IS NOT NULL GROUP BY project_dir")}
            for proj in proj_dirs:
                if only and proj.parent != projects:
                    continue
                proj_path = known_paths.get(proj.name) or peek_cwd(proj) or decode_project_dir(proj.name)
                if cfg.is_excluded(proj_path):
                    continue  # excluded projects are neither archived nor ingested
                dst_root = cfg.archive_dir / key / "projects" / proj.name
                mains = [only] if only else sorted(proj.glob("*.jsonl"))
                for main in mains:
                    if not main.exists() or main.stem in skip:
                        continue
                    archiver.archive(main, dst_root / main.name)
                    sdir = session_dir_for(main)
                    if sdir.is_dir():
                        archiver.archive_tree(sdir, dst_root / sdir.name)
                    _ingest_main(conn, cfg, archiver, claude_dir, proj, main, report, ended=ended, force=force)
                    conn.commit()
                mem = proj / "memory"
                if mem.is_dir():
                    archiver.archive_tree(mem, dst_root / "memory")
                    if cfg.import_memory:
                        try:
                            report.memory_items += import_memory_dir(conn, cfg, proj, mem, proj_path)
                        except Exception as exc:
                            conn.rollback()
                            log.exception("memory import failed: %s", mem)
                            report.errors.append(f"memory {mem}: {exc}")
                conn.commit()
            if not only:
                hist = claude_dir / "history.jsonl"
                if hist.exists():
                    archiver.archive(hist, cfg.archive_dir / key / "history.jsonl")
                    if cfg.import_history:
                        try:
                            report.history_sessions += import_history(conn, cfg, hist)
                        except Exception as exc:
                            conn.rollback()
                            log.exception("history import failed: %s", hist)
                            report.errors.append(f"history {hist}: {exc}")
                conn.commit()
        if not only:
            _reparse_archived(conn, cfg, report, skip)
            for codex_dir in codex_dirs:
                try:
                    _sync_codex(cfg, conn, archiver, codex_dir, report, skip, force=force)
                except Exception as exc:
                    conn.rollback()
                    log.exception("codex sync failed: %s", codex_dir)
                    report.errors.append(f"codex {codex_dir}: {exc}")
            if cfg.codex_cloud:
                try:
                    _sync_codex_cloud(cfg, conn, report, skip, force=force)
                except Exception as exc:
                    conn.rollback()
                    log.exception("codex cloud sync failed")
                    report.errors.append(f"codex cloud: {exc}")
            for name, dirs, fn in (("copilot", cfg.copilot_dirs, _sync_copilot), ("bob", cfg.bob_dirs, _sync_bob),
                                   ("antigravity", cfg.antigravity_dirs, _sync_antigravity)):
                for d in dirs:
                    try:
                        fn(cfg, conn, archiver, d, report, skip, force=force)
                    except Exception as exc:
                        conn.rollback()
                        log.exception("%s sync failed: %s", name, d)
                        report.errors.append(f"{name} {d}: {exc}")
            try:  # chats imported from claude.ai or ChatGPT, after a parser upgrade: from the export the archive kept
                from .chat_import import reparse_archived

                report.sessions_updated += reparse_archived(cfg, conn, skip)
            except Exception as exc:
                conn.rollback()
                log.exception("re-reading archived chat exports failed")
                report.errors.append(f"chat exports: {exc}")
            from .statusline import ingest as ingest_statusline

            conn.execute("SAVEPOINT statusline")  # a bad record must not undo the sessions synced above
            try:
                ingest_statusline(conn)  # context and plan usage the status-line collector recorded
                conn.execute("RELEASE statusline")
            except Exception:
                conn.execute("ROLLBACK TO statusline")
                conn.execute("RELEASE statusline")
                log.exception("status-line usage import failed")
            report.sessions_missing_source = mark_missing_sources(conn)
            try:
                report.analyses_imported = apply_analyses(cfg, conn)
            except Exception as exc:
                conn.rollback()
                log.exception("applying analyses from other computers failed")
                report.errors.append(f"imported analyses: {exc}")
            kv_set(conn, "last_sync", utcnow_iso())
        conn.commit()
    report.seconds = time.monotonic() - t0
    log.info("sync: %s", report.summary())
    return report


# ------------------------------------------------------------------ Codex
def _sync_codex(cfg: Config, conn, archiver: Archiver, codex_dir: Path, report: SyncReport, skip: set[str], *, force=False):
    """Archive and ingest Codex rollouts. Claude Code sessions that Codex Desktop imported are recovered under
    their Claude id when the vault lacks the real transcript (Claude Code deletes transcripts after 30 days)."""
    if not (codex_dir / "sessions").is_dir():
        return
    dst = cfg.archive_dir / archive_key(codex_dir)
    for name in ("session_index.jsonl", "external_agent_session_imports.json"):
        if (codex_dir / name).exists():
            archiver.archive(codex_dir / name, dst / name)
    mem = codex_dir / "memories"
    if mem.is_dir():
        archiver.archive_tree(mem, dst / "memories")
    titles, imports = load_titles(codex_dir), load_imports(codex_dir)
    children: dict[str, list[Path]] = {}
    mains: list[Path] = []
    for f in rollout_files(codex_dir):
        parent = parent_thread_id(read_meta(f))
        if parent:
            children.setdefault(parent, []).append(f)
        else:
            mains.append(f)
    real_claude = {r[0] for r in conn.execute("SELECT id FROM sessions WHERE agent = 'claude' AND source = 'transcript'")}
    for f in mains:
        rid = rollout_id(f) or f.stem
        subs = children.get(rid, [])
        archiver.archive(f, dst / f.relative_to(codex_dir))
        for c in subs:
            archiver.archive(c, dst / c.relative_to(codex_dir))
        imp = imports.get(rid)
        if imp and imp.get("claude_id"):
            if imp["claude_id"] in real_claude:
                continue  # the vault has Claude Code's own transcript; Codex's copy adds nothing
            sid, agent, source, title = imp["claude_id"], "claude", "codex-import", imp.get("title") or titles.get(rid)
        else:
            sid, agent, source, title = rid, "codex", "transcript", titles.get(rid)
        if sid in skip:
            continue
        _ingest_codex(conn, cfg, codex_dir, f, subs, dst, report, sid=sid, agent=agent, source=source, title=title, force=force)
        conn.commit()
    if cfg.import_memory and mem.is_dir():
        try:
            report.memory_items += import_codex_memories(conn, cfg, mem)
        except Exception as exc:
            conn.rollback()
            log.exception("codex memory import failed")
            report.errors.append(f"codex memories: {exc}")
    conn.commit()


def _ingest_codex(conn, cfg, codex_dir: Path, main: Path, subs: list[Path], dst: Path, report: SyncReport, *,
                  sid: str, agent: str, source: str, title: str | None, force: bool) -> None:
    sig = paths_signature([main, *subs], CODEX_PARSER_VERSION)
    row = conn.execute("SELECT files_sig, title, ai_title FROM sessions WHERE id = ?", (sid,)).fetchone()
    if row and row["files_sig"] == sig and not force:
        if title and row["ai_title"] != title:  # Codex names threads after the fact
            conn.execute("UPDATE sessions SET ai_title = ?, title = CASE WHEN llm_title IS NULL THEN ? ELSE title END WHERE id = ?",
                         (title, one_line(title, 120), sid))
        report.sessions_unchanged += 1
        return
    conn.commit()
    try:
        ps = parse_codex_session(main, subs, title=title)
        ps.id = sid
    except Exception as exc:
        log.exception("codex parse failed: %s", main)
        report.errors.append(f"parse {main}: {exc}")
        return
    if cfg.is_excluded(ps.project_path) or kv_get(conn, f"forget:{sid}"):
        return
    try:
        result = store_parsed(conn, cfg, ps, claude_dir=codex_dir, project_dir=None, transcript_path=main,
                              archive_path=dst / (str(main.relative_to(codex_dir)) + ".gz"), files_sig=sig,
                              agent=agent, source=source)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        log.exception("store failed: %s", main)
        report.errors.append(f"store {main}: {exc}")
        return
    if result == "foreign":
        return
    report.touched.append(sid)
    if result == "new":
        report.sessions_new += 1
    else:
        report.sessions_updated += 1


# ------------------------------------------------------------------ GitHub Copilot, IBM Bob, Google Antigravity
def snapshot_sqlite(conn, src: Path, dst: Path) -> bool:
    """Archive a live SQLite database consistently (backup API), only when it or its WAL changed."""
    parts = []
    for f in (src, src.with_name(src.name + "-wal")):
        try:
            st = f.stat()
            parts.append(f"{st.st_size}:{int(st.st_mtime)}")
        except FileNotFoundError:
            continue
    sig, key = "|".join(parts), f"snapshot:{src}"
    if not parts or (kv_get(conn, key) == sig and dst.exists()):
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + f".tmp{os.getpid()}")
    tmp.unlink(missing_ok=True)
    try:
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=10)
        target = sqlite3.connect(tmp)
        with target:
            source.backup(target)
        target.close()
        source.close()
        os.replace(tmp, dst)
    except sqlite3.Error as exc:
        tmp.unlink(missing_ok=True)
        log.warning("snapshot of %s failed: %s", src, exc)
        return False
    kv_set(conn, key, sig)
    conn.commit()
    return True


def _store_other(conn, cfg, ps: ParsedSession, report: SyncReport, *, root: Path, transcript: Path, archive: Path,
                 sig: str, agent: str, source: str = "transcript") -> None:
    """Store a parsed Copilot/Bob/Antigravity/Codex Cloud session (the tail every non-Claude source shares)."""
    if cfg.is_excluded(ps.project_path) or kv_get(conn, f"forget:{ps.id}"):
        return
    try:
        result = store_parsed(conn, cfg, ps, claude_dir=root, project_dir=None, transcript_path=transcript,
                              archive_path=archive, files_sig=sig, agent=agent, source=source)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        log.exception("store failed: %s", transcript)
        report.errors.append(f"store {transcript}: {exc}")
        return
    if result == "foreign":
        return
    report.touched.append(ps.id)
    if result == "new":
        report.sessions_new += 1
    else:
        report.sessions_updated += 1


def _unchanged(conn, sid: str, sig: str, force: bool) -> bool:
    row = conn.execute("SELECT files_sig FROM sessions WHERE id = ?", (sid,)).fetchone()
    return bool(row) and row["files_sig"] == sig and not force


def _sync_copilot(cfg: Config, conn, archiver: Archiver, root: Path, report: SyncReport, skip: set[str], *, force=False):
    """A Copilot agent home (~/.copilot: CLI and VS Code agent-host sessions) or a VS Code User directory (Copilot Chat)."""
    if (root / "session-state").is_dir():
        dst = cfg.archive_dir / "copilot"
        if (root / "session-store.db").exists():
            snapshot_sqlite(conn, root / "session-store.db", dst / "session-store.db")
        usage = load_usage(root)
        for sdir in agent_session_dirs(root):
            archiver.archive_tree(sdir, dst / "session-state" / sdir.name)
            rows = usage.get(sdir.name, [])
            sig = paths_signature([sdir / "events.jsonl", sdir / "workspace.yaml"], COPILOT_PARSER_VERSION) + f":{len(rows)}"
            if sdir.name in skip or _unchanged(conn, sdir.name, sig, force):
                report.sessions_unchanged += 1
                continue
            conn.commit()
            try:
                ps = parse_copilot_agent(sdir, rows)
            except Exception as exc:
                log.exception("copilot parse failed: %s", sdir)
                report.errors.append(f"parse {sdir}: {exc}")
                continue
            _store_other(conn, cfg, ps, report, root=root, transcript=sdir / "events.jsonl",
                         archive=dst / "session-state" / sdir.name / "events.jsonl.gz", sig=sig, agent="copilot")
    files = chat_files(root) if (root / "workspaceStorage").is_dir() or (root / "globalStorage").is_dir() else []
    dst = cfg.archive_dir / "copilot" / re.sub(r"[^\w.-]+", "_", root.parent.name or "vscode")
    for f in files:
        sig = paths_signature([f], COPILOT_PARSER_VERSION)
        if f.stem in skip or _unchanged(conn, f.stem, sig, force) or kv_get(conn, f"empty-chat:{f}") == sig:
            report.sessions_unchanged += 1
            continue
        conn.commit()
        try:
            ps = parse_vscode_chat(f, workspace_folder(f))
        except Exception as exc:
            log.exception("copilot chat parse failed: %s", f)
            report.errors.append(f"parse {f}: {exc}")
            continue
        if ps is None:  # an empty chat panel: remember it so it is not re-read until it changes
            kv_set(conn, f"empty-chat:{f}", sig)
            conn.commit()
            continue
        rel = f.relative_to(root)
        archiver.archive(f, dst / rel)
        if (f.parent.parent / "workspace.json").exists():
            archiver.archive(f.parent.parent / "workspace.json", dst / rel.parent.parent / "workspace.json")
        _store_other(conn, cfg, ps, report, root=root, transcript=f, archive=dst / (str(rel) + ".gz"), sig=sig, agent="copilot")


def _sync_bob(cfg: Config, conn, archiver: Archiver, root: Path, report: SyncReport, skip: set[str], *, force=False):
    """IBM Bob tasks from ~/.bob/db/bob.db (read-only; login state elsewhere in ~/.bob is never read)."""
    db = bob_db(root)
    if not db.exists():
        return
    snap = cfg.archive_dir / "bob" / "bob.db"
    snapshot_sqlite(conn, db, snap)
    for task in load_tasks(root):
        if cfg.is_internal_path(task_folder(task)):
            continue  # Interlatch's own analysis, run through Bob (analysis.backend = "bob")
        sid = task["id"]
        sig = hashlib.sha1(f"{task_signature(task)}|v{BOB_PARSER_VERSION}".encode()).hexdigest()
        if sid in skip or _unchanged(conn, sid, sig, force):
            report.sessions_unchanged += 1
            continue
        ps = parse_bob_task(task)
        if ps is None:
            continue  # a task that never got a prompt
        _store_other(conn, cfg, ps, report, root=root, transcript=db, archive=snap, sig=sig, agent="bob")


def _sync_antigravity(cfg: Config, conn, archiver: Archiver, root: Path, report: SyncReport, skip: set[str], *, force=False):
    """Google Antigravity conversations from ~/.gemini/antigravity (read-only): each one's step log, title and database."""
    dst = cfg.archive_dir / "antigravity"
    for conv in conversation_dirs(root):
        cid, logs = conv.name, transcript_files(conv)
        sig = paths_signature([*logs, root / "annotations" / f"{cid}.pbtxt"], ANTIGRAVITY_PARSER_VERSION)
        if cid in skip or _unchanged(conn, cid, sig, force):
            report.sessions_unchanged += 1
            continue
        conn.commit()
        try:
            ps = parse_antigravity_conversation(conv, title=load_title(root, cid),
                                                db_meta=load_db_meta(root / "conversations" / f"{cid}.db"))
        except Exception as exc:
            log.exception("antigravity parse failed: %s", conv)
            report.errors.append(f"parse {conv}: {exc}")
            continue
        if ps is None:
            continue  # a conversation that never got a prompt
        archived = [archiver.archive(f, dst / cid / f.relative_to(conv / LOGS)) for f in logs]
        for f in [*conv.glob("*.md"), *conv.glob("*.md.metadata.json")]:  # task, plan and walkthrough (not the images)
            archiver.archive(f, dst / cid / f.name)
        _store_other(conn, cfg, ps, report, root=root, transcript=logs[0], archive=archived[0], sig=sig, agent="antigravity")


def _cloud_project(conn, label: str | None) -> str | None:
    """A Codex Cloud environment is named after its repository: use the local project of that name when there is one."""
    if not label:
        return None
    name = label.rstrip("/").rsplit("/", 1)[-1]
    row = conn.execute("SELECT project_path FROM sessions WHERE project_name = ? AND source != 'codex-cloud' "
                       "AND project_path IS NOT NULL ORDER BY ended_at DESC LIMIT 1", (name,)).fetchone()
    return row[0] if row else name


def _sync_codex_cloud(cfg: Config, conn, report: SyncReport, skip: set[str], *, force=False):
    """Codex Cloud tasks through `codex cloud list/diff`; each new or changed task is archived with its diff."""
    from .codex_cloud import CloudError, list_tasks, parse_cloud_task, save_status, task_diff
    from .codex_cloud import task_signature as cloud_signature

    binary = cfg.codex_bin()
    if not binary:
        save_status(conn, ok=False, error="codex CLI not found")
        return
    try:
        tasks = list_tasks(binary)
    except CloudError as exc:  # not logged in or offline: shown on the Sources page, retried next sync
        log.warning("codex cloud: %s", exc)
        save_status(conn, ok=False, error=str(exc))
        conn.commit()
        return
    save_status(conn, ok=True, tasks=len(tasks))
    conn.commit()
    dst = cfg.archive_dir / "codex-cloud"
    for task in tasks:
        sid, sig = task["id"], cloud_signature(task)
        if sid in skip or _unchanged(conn, sid, sig, force):
            report.sessions_unchanged += 1
            continue
        diff = task_diff(binary, sid)
        archive = dst / f"{sid}.json.gz"
        archive.parent.mkdir(parents=True, exist_ok=True)
        tmp = archive.with_name(archive.name + f".tmp{os.getpid()}")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:  # kept even after the task expires in the cloud
            json.dump({"task": task, "diff": diff}, f, ensure_ascii=False)
        os.replace(tmp, archive)
        ps = parse_cloud_task(task, diff, _cloud_project(conn, task.get("environment_label")))
        # no prompts, so analysis skips it ("too few prompts")
        _store_other(conn, cfg, ps, report, root=dst, transcript=archive, archive=archive, sig=sig, agent="codex",
                     source="codex-cloud")


_APPLIES_RE = re.compile(r"applies_to:.*?cwd=(\S+?)(?:;|\s|$)")


def import_codex_memories(conn, cfg: Config, mem: Path) -> int:
    """Codex's memory notes (MEMORY.md, memory_summary.md, rollout_summaries/*.md) as knowledge items."""
    changed = 0
    prefix = str(mem) + "/"
    for r in conn.execute("SELECT id, source_ref FROM knowledge WHERE source = 'memory' AND agent = 'codex' "
                          "AND status = 'active' AND source_ref LIKE ?", (prefix + "%",)).fetchall():
        if not Path(r["source_ref"]).exists():
            conn.execute("UPDATE knowledge SET status = 'superseded', updated_at = ? WHERE id = ?", (utcnow_iso(), r["id"]))
            changed += 1
    files = [p for p in mem.rglob("*.md") if ".git" not in p.parts and p.name != "raw_memories.md"]
    for f in sorted(files):
        try:
            text = f.read_text(errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        first = next((l.lstrip("# ").strip() for l in text.splitlines() if l.strip()), f.stem)
        title = one_line(safe_text(first), 120)
        body = safe_text(text if len(text) <= 8000 else text[:8000] + "\n…")
        m = _APPLIES_RE.search(text)
        project_path = m.group(1).rstrip("/") if m else None
        if cfg.is_excluded(project_path):
            continue
        fp = f"memory:{f}"
        now = utcnow_iso()
        tags = dumps(["memory", "codex", "rollout-summary" if f.parent.name == "rollout_summaries" else "summary"])
        existing = conn.execute("SELECT id, body, title, status FROM knowledge WHERE fingerprint = ?", (fp,)).fetchone()
        if existing:
            if existing["body"] != body or existing["title"] != title or existing["status"] == "superseded":
                conn.execute("UPDATE knowledge SET title=?, body=?, tags_json=?, updated_at=?, project_path=?, project_name=?, "
                             "status = CASE WHEN status = 'dismissed' THEN status ELSE 'active' END WHERE id=?",
                             (title, body, tags, now, project_path, project_name_for(project_path) if project_path else None, existing["id"]))
                changed += 1
            continue
        conn.execute(
            "INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, tags_json, scope, confidence, "
            "evidence, source, agent, source_ref, fingerprint, created_at, updated_at, stage, stage_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (None, project_path, project_name_for(project_path) if project_path else None, "fact", title, body, tags,
             "project" if project_path else "global", "high", "Codex memory", "memory", "codex", str(f), fp,
             to_iso(datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)), now, "established", MEMORY_STAGE_REASON))
        changed += 1
    return changed


def forgotten_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0].split(":", 1)[1] for r in conn.execute("SELECT key FROM kv WHERE key LIKE 'forget:%'")}


def forget_session(conn: sqlite3.Connection, cfg: Config, sid: str, *, delete_transcript: bool = False,
                   remember: bool = True) -> list[str]:
    """Remove every trace of a session from the vault (and optionally the original transcript); never re-ingest it,
    unless `remember` is off (a hub giving back what a computer took back: it may share it again later)."""
    with file_lock(cfg.locks_dir / "ingest.lock", timeout=900) as got:
        if not got:
            raise RuntimeError("a sync is still running; try again in a minute")
        if remember:
            kv_set(conn, f"forget:{sid}", utcnow_iso())  # first, so a racing ingest also skips it
            conn.commit()
        return _forget(conn, sid, delete_transcript)


def _forget(conn: sqlite3.Connection, sid: str, delete_transcript: bool) -> list[str]:
    row = conn.execute("SELECT transcript_path, archive_path FROM sessions WHERE id = ?", (sid,)).fetchone()
    removed = []
    for table in ("events", "tool_calls", "session_files", "subagents", "api_calls", "artifacts"):
        conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (sid,))
    n = conn.execute("DELETE FROM knowledge WHERE session_id = ?", (sid,)).rowcount
    conn.execute("DELETE FROM analyses WHERE target = ?", (sid,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
    removed.append(f"database rows (incl. {n} knowledge items)")
    def in_use(path: str) -> bool:  # also another session's: a chat export, Bob's or Copilot's database
        return conn.execute("SELECT 1 FROM sessions WHERE archive_path = ? OR transcript_path = ? LIMIT 1",
                            (path, path)).fetchone() is not None

    paths = []
    if row and row["archive_path"] and not in_use(row["archive_path"]):
        paths += [Path(row["archive_path"]), session_dir_for(Path(row["archive_path"]))]
    if delete_transcript and row and row["transcript_path"] and not in_use(row["transcript_path"]):
        paths += [Path(row["transcript_path"]), session_dir_for(Path(row["transcript_path"]))]
    for p in paths:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(str(p))
        elif p.exists():
            p.unlink()
            removed.append(str(p))
    note = kv_get(conn, f"export:{sid}")
    if note:
        Path(note.split("|", 1)[0]).unlink(missing_ok=True)
        conn.execute("DELETE FROM kv WHERE key = ?", (f"export:{sid}",))
        removed.append("markdown note")
    conn.execute("DELETE FROM files_state WHERE path LIKE ?", (f"%{sid}%",))
    conn.commit()
    return removed


def mark_missing_sources(conn: sqlite3.Connection) -> int:
    missing = 0
    for r in conn.execute("SELECT id, transcript_path, source_present FROM sessions "
                          "WHERE source NOT IN ('history', 'remote')").fetchall():  # remote: its transcript is elsewhere
        present = 1 if r["transcript_path"] and Path(r["transcript_path"]).exists() else 0
        if present != r["source_present"]:
            conn.execute("UPDATE sessions SET source_present = ? WHERE id = ?", (present, r["id"]))
        missing += 1 - present
    return missing


# ------------------------------------------------------------------ history.jsonl
def import_history(conn: sqlite3.Connection, cfg: Config, path: Path) -> int:
    """Create 'history-only' sessions for prompts whose transcripts no longer exist."""
    st = path.stat()
    marker = f"{st.st_size}:{int(st.st_mtime)}"
    if kv_get(conn, f"history_sig:{path}") == marker:
        return 0
    created = 0
    from .hub import machine_of, resolver

    machine = machine_of(cfg, path)
    me = local_machine_id(cfg)
    # a stub another computer's history made is that computer's, as a transcript is
    known = {r[0] for r in conn.execute("SELECT id FROM sessions WHERE source != 'history' OR COALESCE(machine_id, ?) != ?",
                                        (me, machine or me))} | forgotten_ids(conn)
    for sid, rec in parse_history(path).items():
        rec["project"] = resolver(cfg, conn).resolve(machine, rec.get("project"))
        if sid in known or cfg.is_excluded(rec.get("project")):
            continue
        prompts = [(ts, safe_text(text)) for ts, text in rec["prompts"] if str(text).strip()]
        if not prompts:
            continue
        project = rec.get("project")
        real = [p for p in prompts if not p[1].startswith("/") or " " in p[1]]
        first = (real or prompts)[0][1]
        started, ended = prompts[0][0], prompts[-1][0]
        conn.execute("DELETE FROM events WHERE session_id = ?", (sid,))
        conn.executemany(
            "INSERT INTO events(session_id, agent_id, seq, ts, role, kind, searchable, text) VALUES (?,?,?,?,?,?,?,?)",
            [(sid, "", i, ts, "user", "command" if text.startswith("/") and " " not in text else "prompt", 1, text[:20000])
             for i, (ts, text) in enumerate(prompts)],
        )
        dur = 0.0
        if started and ended:
            dur = (parse_ts(ended) - parse_ts(started)).total_seconds()
        conn.execute(
            "INSERT INTO sessions(id, source, claude_dir, project_path, project_name, source_present, title, first_prompt, "
            "last_prompt, started_at, ended_at, duration_s, active_s, n_prompts, n_events, analysis_status, analysis_reason, "
            "ingested_at, parser_version, machine_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET n_prompts = excluded.n_prompts, n_events = excluded.n_events, "
            "last_prompt = excluded.last_prompt, ended_at = excluded.ended_at, duration_s = excluded.duration_s "
            "WHERE sessions.source = 'history'",
            (sid, "history", str(path.parent), project, project_name_for(project), 0, one_line(first, 90), first,
             prompts[-1][1], started, ended, dur, min(dur, len(prompts) * 300.0), len(real), len(prompts), "skipped",
             "history only (transcript deleted before Interlatch)", utcnow_iso(), PARSER_VERSION,
             machine or me),
        )
        created += 1
    kv_set(conn, f"history_sig:{path}", marker)
    return created


# ------------------------------------------------------------------ memory files
_MEMORY_KIND = {"user": "preference", "feedback": "preference", "project": "fact", "reference": "reference"}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head, body = text[3:end], text[end + 4 :]
    meta: dict = {}
    parent = None
    for line in head.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([\w.-]+):\s*(.*)$", line)
        if not m:
            continue
        indent, key, value = m.groups()
        value = value.strip().strip('"').strip("'")
        if indent and parent is not None and isinstance(meta.get(parent), dict):
            meta[parent][key] = value
        elif value == "":
            meta[key] = {}
            parent = key
        else:
            meta[key] = value
            parent = None
    return meta, body.lstrip("\n")


def import_memory_dir(conn: sqlite3.Connection, cfg: Config, proj: Path, mem: Path, project_path: str | None = None) -> int:
    row = conn.execute(
        "SELECT project_path FROM sessions WHERE project_dir = ? AND project_path IS NOT NULL "
        "GROUP BY project_path ORDER BY COUNT(*) DESC LIMIT 1",
        (proj.name,),
    ).fetchone()
    project_path = row[0] if row else (project_path or peek_cwd(proj) or decode_project_dir(proj.name))
    if cfg.is_excluded(project_path):
        return 0
    changed = 0
    # Claude curates its memory: a deleted note is retired here too (the archive keeps the file)
    for r in conn.execute(
        "SELECT id, source_ref FROM knowledge WHERE source = 'memory' AND status = 'active' AND source_ref LIKE ?",
        (str(mem) + "/%",),
    ).fetchall():
        if not Path(r["source_ref"]).exists():
            conn.execute("UPDATE knowledge SET status = 'superseded', updated_at = ? WHERE id = ?", (utcnow_iso(), r["id"]))
            changed += 1
    for f in sorted(mem.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        meta, body = parse_frontmatter(text)
        mtype = meta["metadata"].get("type") if isinstance(meta.get("metadata"), dict) else meta.get("type")
        mtype = mtype if isinstance(mtype, str) else None
        kind = _MEMORY_KIND.get((mtype or "").strip(), "fact")
        title = next((v for v in (meta.get("description"), meta.get("name")) if isinstance(v, str) and v), f.stem)
        name = meta.get("name") if isinstance(meta.get("name"), str) else f.stem
        fp = f"memory:{f}"
        existing = conn.execute("SELECT id, body, title, status FROM knowledge WHERE fingerprint = ?", (fp,)).fetchone()
        now = utcnow_iso()
        tags = dumps(["memory", mtype] if mtype else ["memory"])
        body, title = safe_text(body), safe_text(title)
        if existing:
            if existing["body"] != body.strip() or existing["title"] != title or existing["status"] == "superseded":
                conn.execute(
                    "UPDATE knowledge SET title=?, body=?, kind=?, tags_json=?, updated_at=?, project_path=?, project_name=?, "
                    "status = CASE WHEN status = 'dismissed' THEN status ELSE 'active' END WHERE id=?",
                    (title, body.strip(), kind, tags, now, project_path, project_name_for(project_path), existing["id"]),
                )
                changed += 1
            continue
        conn.execute(
            "INSERT INTO knowledge(session_id, project_path, project_name, kind, title, body, tags_json, scope, confidence, "
            "evidence, source, source_ref, fingerprint, created_at, updated_at, stage, stage_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (None, project_path, project_name_for(project_path), kind, title, body.strip(), tags,
             "global" if mtype in ("user", "feedback") else "project", "high", name, "memory", str(f), fp,
             to_iso(datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)), now, "established", MEMORY_STAGE_REASON),
        )
        changed += 1
    return changed
