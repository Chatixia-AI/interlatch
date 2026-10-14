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
import sqlite3
import time
from pathlib import Path

from ..core.config import Config
from ..core.util import file_lock, one_line, utcnow_iso
from ..sources.antigravity import (ANTIGRAVITY_PARSER_VERSION, LOGS, conversation_dirs, load_db_meta, load_title,
                                   parse_antigravity_conversation, transcript_files)
from ..sources.bob import BOB_PARSER_VERSION, bob_db, load_tasks, parse_bob_task, task_folder, task_signature
from ..sources.claude.files import decode_project_dir, peek_cwd, session_dir_for
from ..sources.claude.parser import PARSER_VERSION, parse_session
from ..sources.codex.parser import (CODEX_PARSER_VERSION, load_imports, load_titles, parent_thread_id,
                                    parse_codex_session, read_meta, rollout_files, rollout_id)
from ..sources.copilot import (COPILOT_PARSER_VERSION, agent_session_dirs, chat_files, load_usage, parse_copilot_agent,
                               parse_vscode_chat, workspace_folder)
from ..storage.kv import kv_get, kv_set
from .archive import Archiver, archive_key, files_signature, paths_signature, snapshot_sqlite
from .deletion import forgotten_ids, mark_missing_sources
from .history import import_history
from .memory import import_codex_memories, import_memory_dir
from .persistence import store_source_session, is_unchanged, store_parsed
from .report import SyncReport

log = logging.getLogger("interlatch.ingest")


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
        from .analyses import apply_analyses
        from .persistence import register_local
        from .archive import machine_roots
        from ..projects.routing import resolver

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
            from ..statusline import ingest as ingest_statusline

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
            if sdir.name in skip or is_unchanged(conn, sdir.name, sig, force):
                report.sessions_unchanged += 1
                continue
            conn.commit()
            try:
                ps = parse_copilot_agent(sdir, rows)
            except Exception as exc:
                log.exception("copilot parse failed: %s", sdir)
                report.errors.append(f"parse {sdir}: {exc}")
                continue
            store_source_session(conn, cfg, ps, report, root=root, transcript=sdir / "events.jsonl",
                         archive=dst / "session-state" / sdir.name / "events.jsonl.gz", sig=sig, agent="copilot")
    files = chat_files(root) if (root / "workspaceStorage").is_dir() or (root / "globalStorage").is_dir() else []
    dst = cfg.archive_dir / "copilot" / re.sub(r"[^\w.-]+", "_", root.parent.name or "vscode")
    for f in files:
        sig = paths_signature([f], COPILOT_PARSER_VERSION)
        if f.stem in skip or is_unchanged(conn, f.stem, sig, force) or kv_get(conn, f"empty-chat:{f}") == sig:
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
        store_source_session(conn, cfg, ps, report, root=root, transcript=f, archive=dst / (str(rel) + ".gz"), sig=sig, agent="copilot")


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
        if sid in skip or is_unchanged(conn, sid, sig, force):
            report.sessions_unchanged += 1
            continue
        ps = parse_bob_task(task)
        if ps is None:
            continue  # a task that never got a prompt
        store_source_session(conn, cfg, ps, report, root=root, transcript=db, archive=snap, sig=sig, agent="bob")


def _sync_antigravity(cfg: Config, conn, archiver: Archiver, root: Path, report: SyncReport, skip: set[str], *, force=False):
    """Google Antigravity conversations from ~/.gemini/antigravity (read-only): each one's step log, title and database."""
    dst = cfg.archive_dir / "antigravity"
    for conv in conversation_dirs(root):
        cid, logs = conv.name, transcript_files(conv)
        sig = paths_signature([*logs, root / "annotations" / f"{cid}.pbtxt"], ANTIGRAVITY_PARSER_VERSION)
        if cid in skip or is_unchanged(conn, cid, sig, force):
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
        store_source_session(conn, cfg, ps, report, root=root, transcript=logs[0], archive=archived[0], sig=sig, agent="antigravity")


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
    from ..sources.codex.cloud import CloudError, list_tasks, parse_cloud_task, save_status, task_diff
    from ..sources.codex.cloud import task_signature as cloud_signature

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
        if sid in skip or is_unchanged(conn, sid, sig, force):
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
        store_source_session(conn, cfg, ps, report, root=dst, transcript=archive, archive=archive, sig=sig, agent="codex",
                     source="codex-cloud")
