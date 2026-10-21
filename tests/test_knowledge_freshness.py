"""A lesson records the files it was learned from, and reading it later says when they have changed since: in the
SessionStart text and in the MCP server's search. Only files the session read or changed, in the project's folder on
this computer, are recorded."""

import dataclasses

from interlatch.analysis.session import normalize_knowledge, store_analysis
from interlatch.knowledge import freshness
from interlatch.runtime.hooks import build_session_context


def _project(tmp_path):
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "billing.py").write_text("RATE = 0.1\n")
    (root / "src" / "tax.py").write_text("VAT = 0.2\n")
    (root / "README.md").write_text("# shop\n")
    return root


def _session(conn, root, sid="fresh-1", files=("src/billing.py", "src/tax.py")):
    conn.execute("INSERT INTO sessions(id, project_path, project_name, started_at, title, source, agent, analysis_status) "
                 "VALUES (?, ?, 'shop', '2026-10-20T10:00:00.000Z', 'Billing', 'claude', 'claude', 'done')",
                 (sid, str(root)))
    for f in files:
        conn.execute("INSERT INTO session_files(session_id, path, reads) VALUES (?, ?, 1)", (sid, str(root / f)))


def test_only_files_the_session_touched_in_the_project_are_recorded(synced, tmp_path):
    conn = synced["conn"]
    root = _project(tmp_path)
    _session(conn, root)
    got = freshness.record(conn, "fresh-1", str(root),
                           ["src/billing.py", "tax.py", "README.md", "/etc/hosts", "../outside.py", "src/billing.py", ""])
    assert [s["path"] for s in got] == ["src/billing.py", "src/tax.py"], "a bare name it used is found; README wasn't read"
    assert all(len(s["sha256"]) == 64 and s["size"] > 0 for s in got)
    assert freshness.record(conn, "fresh-1", str(tmp_path / "not-here"), ["src/billing.py"]) == []


def test_a_lesson_is_fresh_until_its_file_changes_or_goes(synced, tmp_path):
    conn = synced["conn"]
    root = _project(tmp_path)
    _session(conn, root)
    sources = freshness.record(conn, "fresh-1", str(root), ["src/billing.py"])
    assert freshness.check(str(root), sources) == {"state": "fresh", "changed": []}
    assert freshness.stale_note(str(root), sources) == ""
    (root / "src" / "billing.py").write_text("RATE = 0.15\n")
    assert freshness.check(str(root), sources) == {"state": "stale", "changed": ["src/billing.py"]}
    assert freshness.stale_note(str(root), sources) == " (may be out of date: src/billing.py changed since)"
    (root / "src" / "billing.py").unlink()
    assert freshness.check(str(root), sources)["state"] == "stale"
    assert freshness.check(str(tmp_path / "elsewhere"), sources) is None, "a folder not on this computer: unknown"
    assert freshness.check(str(root), None) is None


def test_the_analysis_keeps_the_files_each_lesson_names():
    [item] = normalize_knowledge([{"kind": "gotcha", "title": "Rates are fractions", "files": ["src/billing.py", 3, ""]}])
    assert item["files"] == ["src/billing.py", "3"]
    [old] = normalize_knowledge([{"kind": "gotcha", "title": "From before files were asked for"}])
    assert old["files"] == []


def test_a_stored_lesson_is_marked_once_its_file_changes(synced, tmp_path, monkeypatch):
    conn, cfg = synced["conn"], synced["cfg"]
    root = _project(tmp_path)
    _session(conn, root)
    data = {"title": "Billing", "summary": "s", "knowledge": normalize_knowledge([
        {"kind": "fact", "title": "The billing rate is a fraction", "body": "RATE in billing.py is 0.1, not 10.",
         "files": ["src/billing.py"]},
        {"kind": "preference", "title": "Keep answers short", "files": []}])}
    store_analysis(conn, cfg, "fresh-1", data, "test-model", 1)
    conn.commit()
    rows = {r["title"]: r["sources_json"] for r in conn.execute("SELECT title, sources_json FROM knowledge")}
    assert "src/billing.py" in rows["The billing rate is a fraction"] and rows["Keep answers short"] is None

    cfg = dataclasses.replace(cfg, inject_max_chars=3000)
    assert "may be out of date" not in build_session_context(cfg, str(root))
    (root / "src" / "billing.py").write_text("RATE = 10\n")
    text = build_session_context(cfg, str(root))
    line = next(x for x in text.splitlines() if "The billing rate is a fraction" in x)
    assert line.endswith("(may be out of date: src/billing.py changed since)")

    from interlatch.interfaces.mcp import Tools

    monkeypatch.chdir(root)
    found = Tools(cfg).search_knowledge("billing rate", project=str(root))
    assert "may be out of date: src/billing.py changed since" in found


def test_an_archive_from_before_the_column_still_answers(synced):
    conn = synced["conn"]
    conn.execute("CREATE TEMP TABLE knowledge (id INTEGER PRIMARY KEY, project_path TEXT)")  # shadows the real one
    assert freshness.stale_ids(conn, [1, 2]) == {}


def test_a_knowledge_base_bullet_is_marked_when_a_lesson_behind_it_went_stale(synced, tmp_path):
    import json

    conn, cfg = synced["conn"], synced["cfg"]
    root = _project(tmp_path)
    _session(conn, root)
    store_analysis(conn, cfg, "fresh-1", {"title": "Billing", "knowledge": normalize_knowledge([
        {"kind": "fact", "title": "The billing rate is a fraction", "files": ["src/billing.py"]},
        {"kind": "fact", "title": "VAT lives in tax.py", "files": ["src/tax.py"]}])}, "test-model", 1)
    ids = dict(conn.execute("SELECT title, id FROM knowledge"))
    conn.execute("INSERT INTO project_kb(project_path, project_name, updated_at, kb_json) VALUES (?, 'shop', "
                 "'2026-10-20T11:00:00.000Z', ?)", (str(root), json.dumps({"sections": [{"title": "Facts", "items": [
                     {"text": "Billing rate is a fraction of 1", "sources": [ids["The billing rate is a fraction"]],
                      "stage": "established", "sessions": 2},
                     {"text": "VAT is in tax.py", "sources": [ids["VAT lives in tax.py"]], "stage": "established"}]}]})))
    conn.commit()
    (root / "src" / "billing.py").write_text("RATE = 10\n")
    lines = build_session_context(cfg, str(root)).splitlines()
    assert next(x for x in lines if "Billing rate" in x).endswith("(may be out of date: src/billing.py changed since)")
    assert "out of date" not in next(x for x in lines if "VAT is in tax.py" in x)
