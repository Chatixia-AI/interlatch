"""Archives written by old releases open with the current code, with everything in them still readable.

tests/fixtures/db/<version>/chronicle.db was written by that release's own code (make_fixture.py there says how), with
a handful of made-up rows; expected.json beside it holds what the current code must read back. Each test works on a
copy, so the committed file never changes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from interlatch.application.records import session_record
from interlatch.application.search import search_knowledge, search_sessions
from interlatch.interfaces.exports.markdown import session_markdown
from interlatch.storage.schema import SCHEMA, SCHEMA_VERSION
from interlatch.storage.sqlite import connect

FIXTURES = Path(__file__).parent / "fixtures" / "db"
VERSIONS = sorted((d.name for d in FIXTURES.iterdir() if (d / "chronicle.db").is_file()),
                  key=lambda v: tuple(int(p) for p in v.split(".")))
ALPHA = "/home/fixture/projects/alpha"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain(path: Path) -> sqlite3.Connection:
    """A read-only connection that never migrates (the app's connect() would)."""
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _dump(path: Path) -> list[str]:
    conn = _plain(path)
    try:
        return [f"user_version {conn.execute('PRAGMA user_version').fetchone()[0]}", *conn.iterdump()]
    finally:
        conn.close()


def _layout(conn: sqlite3.Connection) -> tuple[dict, set]:
    """Every table's columns, and the names of the indexes, triggers and full-text tables."""
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%fts%'")]
    cols = {t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})")} for t in names}
    other = {(r[0], r[1]) for r in conn.execute("SELECT type, name FROM sqlite_master WHERE type IN "
                                                "('index', 'trigger') OR name LIKE '%fts'")}
    return cols, other


@pytest.fixture(params=VERSIONS)
def old_archive(request, tmp_path):
    src = FIXTURES / request.param / "chronicle.db"
    expected = json.loads((FIXTURES / request.param / "expected.json").read_text(encoding="utf-8"))
    before = _sha(src)
    db = tmp_path / "chronicle.db"
    shutil.copyfile(src, db)
    yield db, expected
    assert _sha(src) == before, "the committed fixture changed"
    assert not [p.name for p in src.parent.iterdir() if p.name.startswith("chronicle.db-")]


def test_fixtures_present():
    assert len(VERSIONS) >= 3, VERSIONS


def test_fixture_is_the_old_release_s(old_archive):
    db, expected = old_archive
    conn = _plain(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == expected["written_by"]["user_version"]
    finally:
        conn.close()
    assert expected["written_by"]["user_version"] < SCHEMA_VERSION


def test_old_archive_migrates_and_reads_back(old_archive):
    db, expected = old_archive
    conn = connect(db)  # as the app opens it: brought up to the current schema
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES ('integrity-check')")

        # every table, column, index and trigger a new archive has
        ref = sqlite3.connect(":memory:")
        ref.executescript(SCHEMA)
        want_cols, want_other = _layout(ref)
        ref.close()
        have_cols, have_other = _layout(conn)
        for table, cols in want_cols.items():
            assert cols <= have_cols.get(table, set()), (table, cols - have_cols.get(table, set()))
        assert want_other <= have_other, want_other - have_other

        for table, n in expected["counts"].items():
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == n, table

        for sid, want in expected["sessions"].items():
            row = conn.execute("SELECT title, project_name, agent, source FROM sessions WHERE id = ?", (sid,)).fetchone()
            assert dict(row) == want, sid

        for kid, want in expected["knowledge"].items():
            row = conn.execute("SELECT kind, title, status, pinned, project_name, stage FROM knowledge WHERE id = ?",
                               (int(kid),)).fetchone()
            assert dict(row) == want, kid
        # the fixture covers what the ladder and its exits look like: every stage, a pinned and a superseded item
        assert {v["stage"] for v in expected["knowledge"].values()} >= {"wip", "provisional", "established", "canonical"}
        assert {"superseded", "active"} <= {v["status"] for v in expected["knowledge"].values()}
        assert any(v["pinned"] for v in expected["knowledge"].values())

        if remote := expected["remote_session"]:  # schema 20: a hub keeps no raw analysis of a shared session
            row = conn.execute("SELECT analysis_json, sentiment FROM sessions WHERE id = ?", (remote,)).fetchone()
            assert tuple(row) == (None, None)

        # what the MCP server and the dashboard read
        assert [k["id"] for k in search_knowledge(conn, "zephyr")] == expected["search"]["knowledge zephyr"]
        assert sorted(k["id"] for k in search_knowledge(conn, "zephyr", include_inactive=True)) == \
            expected["search"]["knowledge zephyr, inactive too"]
        alpha = search_knowledge(conn, None, project=ALPHA)
        assert {k["id"] for k in alpha} == {int(k) for k, v in expected["knowledge"].items()
                                            if v["project_name"] == "alpha" and v["status"] == "active"}
        assert alpha[0]["stage"] in ("canonical", "established")  # most trusted first
        assert [s["session_id"] for s in search_sessions(conn, "quokka")] == expected["search"]["sessions quokka"]
        for sid, paths in expected["session_files"].items():
            record = session_record(conn, sid)
            assert sorted(f["path"] for f in record["files"]) == paths
            assert expected["sessions"][sid]["title"] in session_markdown(record)
    finally:
        conn.close()


def test_opening_again_changes_nothing(old_archive):
    db, _ = old_archive
    connect(db).close()
    first = _dump(db)
    connect(db).close()
    assert _dump(db) == first
