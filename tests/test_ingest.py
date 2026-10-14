from pathlib import Path

from interlatch.storage.sqlite import connect
from interlatch.ingestion.service import sync
from interlatch.ingestion.memory import parse_frontmatter
from interlatch.sources.claude.files import decode_project_dir

from conftest import CWD, PROJECT_DIR, SID


def test_sync_ingests_and_archives(synced):
    conn, cfg, report = synced["conn"], synced["cfg"], synced["report"]
    assert report.sessions_new == 1 and not report.errors
    s = conn.execute("SELECT * FROM sessions WHERE id = ?", (SID,)).fetchone()
    assert s["project_path"] == CWD and s["project_name"] == "demo-app"
    assert s["title"] == "Fix login token expiry"
    assert s["n_prompts"] == 2 and s["n_subagents"] == 1 and s["analysis_status"] == "pending"
    archive = Path(s["archive_path"])
    assert str(archive).startswith(str(cfg.archive_dir))
    assert archive.exists() and archive.name.endswith(".jsonl.gz")
    assert (archive.parent / SID / "subagents").is_dir()
    assert (archive.parent / "memory" / "deploy.md").exists()


def test_second_sync_is_incremental(synced):
    report = sync(synced["cfg"], synced["conn"])
    assert report.sessions_new == 0 and report.sessions_updated == 0 and report.sessions_unchanged == 1
    assert report.files_archived == 0


def test_growing_session_marks_analysis_stale(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    conn.execute("UPDATE sessions SET analysis_status='done', analyzed_prompts=2 WHERE id=?", (SID,))
    conn.commit()
    with open(synced["main"], "a") as fh:
        fh.write('{"type":"user","timestamp":"2026-09-20T11:00:00.000Z","origin":{"kind":"human"},'
                 '"message":{"role":"user","content":"one more thing"}}\n')
    sync(cfg, conn)
    row = conn.execute("SELECT analysis_status, n_prompts FROM sessions WHERE id=?", (SID,)).fetchone()
    assert (row["analysis_status"], row["n_prompts"]) == ("stale", 3)


def test_deleted_source_keeps_session(synced):
    conn, cfg = synced["conn"], synced["cfg"]
    synced["main"].unlink()
    report = sync(cfg, conn)
    assert report.sessions_missing_source == 1
    s = conn.execute("SELECT source_present, n_prompts FROM sessions WHERE id=?", (SID,)).fetchone()
    assert s["source_present"] == 0 and s["n_prompts"] == 2
    assert conn.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (SID,)).fetchone()[0] > 0


def test_history_only_sessions_recovered(synced):
    conn = synced["conn"]
    s = conn.execute("SELECT * FROM sessions WHERE id='old-session-1'").fetchone()
    assert s["source"] == "history" and s["n_prompts"] == 2 and s["analysis_status"] == "skipped"
    assert s["project_path"] == "/Users/test"
    # a history entry for a session that has a transcript must not create a duplicate
    assert conn.execute("SELECT source FROM sessions WHERE id=?", (SID,)).fetchone()[0] == "transcript"


def test_memory_notes_become_knowledge(synced):
    conn = synced["conn"]
    k = conn.execute("SELECT * FROM knowledge WHERE source='memory'").fetchone()
    assert k["kind"] == "fact" and "make ship" in k["body"] and k["project_path"] == CWD
    assert k["title"].startswith("demo-app deploys")
    # editing the memory file updates the item instead of duplicating it
    mem = synced["claude_dir"] / "projects" / PROJECT_DIR / "memory" / "deploy.md"
    mem.write_text(mem.read_text().replace("make ship", "make deploy"))
    sync(synced["cfg"], conn)
    rows = conn.execute("SELECT body FROM knowledge WHERE source='memory'").fetchall()
    assert len(rows) == 1 and "make deploy" in rows[0][0]


def test_single_session_ingest_marks_ended(env):
    conn = connect(env["cfg"].db_path)
    sync(env["cfg"], conn, only=env["main"], ended=True)
    assert conn.execute("SELECT ended_flag FROM sessions WHERE id=?", (SID,)).fetchone()[0] == 1


def test_fts_search(synced):
    from interlatch.application.search import search_events, search_knowledge, search_sessions

    hits = search_events(synced["conn"], "logout flow")
    assert hits and hits[0]["session_id"] == SID and "«logout»" in hits[0]["snippet"]
    assert search_sessions(synced["conn"], "login")[0]["session_id"] == SID
    assert search_knowledge(synced["conn"], "flyctl")[0]["source"] == "memory"
    assert search_events(synced["conn"], "zz") == []  # too short for trigrams: LIKE fallback, no crash


def test_search_all_sessions(synced):
    from interlatch.application.search import search_all

    found = search_all(synced["conn"], "logout", per_session=1)
    top = found["sessions"][0]
    assert found["total"] >= 1 and top["session_id"] == SID and found["mentions"] >= top["hits"] >= 1
    assert len(top["snippets"]) == 1 and "«logout»" in top["snippets"][0]["text"].lower()
    everything = search_all(synced["conn"], "logout", per_session=500)["sessions"][0]["snippets"]
    main = [m["seq"] for m in everything if not m["agent_id"]]
    assert len(everything) == top["hits"] and main == sorted(main)  # every mention, in transcript order
    assert search_all(synced["conn"], "logout", offset=found["total"])["sessions"] == []
    assert search_all(synced["conn"], "   ")["total"] == 0


def test_helpers():
    meta, body = parse_frontmatter("---\nname: x\nmetadata:\n  type: user\n---\nhello")
    assert meta == {"name": "x", "metadata": {"type": "user"}} and body == "hello"
    assert decode_project_dir("-definitely-not-a-real-path") == "/definitely/not/a/real/path"


def test_forget_session(synced):
    from interlatch.ingestion.deletion import forget_session

    conn, cfg = synced["conn"], synced["cfg"]
    archive = Path(conn.execute("SELECT archive_path FROM sessions WHERE id=?", (SID,)).fetchone()[0])
    removed = forget_session(conn, cfg, SID)
    assert removed and not archive.exists()
    assert conn.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (SID,)).fetchone()[0] == 0
    report = sync(cfg, conn)  # the transcript still exists but must not come back
    assert report.sessions_new == 0
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id=?", (SID,)).fetchone()[0] == 0
    assert synced["main"].exists()


def test_forget_keeps_files_other_sessions_share(env, tmp_path):
    """A Bob task's transcript is Bob's whole database, and its archive the snapshot every Bob task shares (as with a
    chat export): forgetting one task removes neither while another session uses it."""
    from interlatch.ingestion.deletion import forget_session

    from copilot_fixture import BOB_TASK, BOB_TOOLS, write_bob_home

    cfg = env["cfg"]
    cfg.bob_dirs = [write_bob_home(tmp_path / "bob")]
    conn = connect(cfg.db_path)
    sync(cfg, conn)
    row = conn.execute("SELECT transcript_path, archive_path FROM sessions WHERE id=?", (BOB_TASK,)).fetchone()
    db, snap = Path(row[0]), Path(row[1])
    assert db.name == "bob.db" and snap.name == "bob.db" and db != snap
    forget_session(conn, cfg, BOB_TASK, delete_transcript=True)
    assert db.exists() and snap.exists()  # the other task still needs both
    forget_session(conn, cfg, BOB_TOOLS)
    assert not snap.exists() and db.exists()  # the last one: its archive goes; Bob's own database stays
    conn.close()


def test_excluded_projects_are_not_archived(env):
    cfg = env["cfg"]
    cfg.exclude_projects = ["/Users/test/Projects/*"]
    conn = connect(cfg.db_path)
    report = sync(cfg, conn)
    assert report.sessions_new == 0 and report.files_archived <= 1  # only history.jsonl
    assert not [p for p in cfg.archive_dir.rglob("*.jsonl.gz") if "/projects/" in str(p)]
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE source='transcript'").fetchone()[0] == 0


def test_cli_search_prints_snippet_labels_as_styling(synced, capsys):
    from interlatch.cli import main

    assert main(["search", "logout", "--sessions-only"]) == 0
    out = capsys.readouterr().out
    assert "[dim]" not in out and "«logout»" in out
