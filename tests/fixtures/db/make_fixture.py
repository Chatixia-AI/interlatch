"""Write a small, fake archive with an old release's own code, for tests/test_db_compat.py.

    python tests/fixtures/db/make_fixture.py 0.27.8          # one version
    python tests/fixtures/db/make_fixture.py all             # every version in RELEASES

For each version this runs, in a throwaway HOME and with a clean environment (nothing reaches ~/.interlatch or the
checkout's own package):

    env -i HOME=<scratch>/home PATH=/usr/bin:/bin:<uv's folder> \\
        uvx --python 3.12 --from <spec> python -I make_fixture.py --child <scratch>/chronicle.db <scratch>/expected.json

In that child the release's own `connect()` creates the archive (its schema, its migrations, its user_version), then
plain SQL inserts the rows below, keeping only the columns that release's tables have. The archive is VACUUMed (1 KiB pages, to keep it
small) and copied to tests/fixtures/db/<version>/chronicle.db with expected.json, the facts the test checks after the current
code opens it. Every row is made up: no data from this computer.

Not a test module (pytest collects test_*.py only); stdlib only, so it runs under the old release's Python too.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = "https://github.com/Chatixia-AI/interlatch"

# version -> (the package it was published as, what uvx installs). agents-chronicle (the name before the rename to
# Interlatch at 0.22.0) is no longer on PyPI, so its releases install from their git tag; interlatch from PyPI.
RELEASES = {
    "0.5.0": ("agents-chronicle", f"git+{REPO}@v0.5.0"),     # schema 7: before maturity stages, hubs and groups
    "0.21.3": ("agents-chronicle", f"git+{REPO}@v0.21.3"),   # schema 18: the last release named Chronicle
    "0.27.8": ("interlatch", "interlatch==0.27.8"),          # schema 19: the last before schema 20's scrub
}

ALPHA, BETA = "/home/fixture/projects/alpha", "/home/fixture/projects/beta"
S1, S2, S3, S4 = (f"00000000-0000-4000-8000-00000000000{i}" for i in range(1, 5))
MACHINE = "00000000-0000-4000-8000-0000000000aa"
REMOTE_MACHINE = "00000000-0000-4000-8000-0000000000bb"

SESSIONS = [
    dict(id=S1, source="transcript", agent="claude", project_path=ALPHA, project_name="alpha",
         title="Set up the alpha build", llm_title="Set up the alpha build", first_prompt="Set up the alpha build",
         started_at="2026-01-05T09:00:00Z", ended_at="2026-01-05T10:00:00Z", duration_s=3600, active_s=1800,
         n_prompts=2, n_tool_calls=2, n_files=2, lines_added=40, lines_removed=3, input_tokens=1000,
         output_tokens=500, est_cost_usd=0.12, analysis_status="done", outcome="achieved",
         summary="Configured the alpha build with the zephyr cache.", machine_id=MACHINE),
    dict(id=S2, source="transcript", agent="codex", project_path=ALPHA, project_name="alpha",
         title="Fix the flaky alpha test", llm_title="Fix the flaky alpha test", first_prompt="The alpha test flakes",
         started_at="2026-01-12T09:00:00Z", ended_at="2026-01-12T09:30:00Z", duration_s=1800, active_s=900,
         n_prompts=1, n_tool_calls=1, n_files=1, lines_added=5, lines_removed=5, analysis_status="done",
         outcome="achieved", machine_id=MACHINE),
    # the analysis wrote a secret into the title: schema 20 redacts every session's title and analysis text again
    dict(id=S3, source="transcript", agent="claude", project_path=BETA, project_name="beta",
         title="Rotate the beta password=fixture-not-a-secret", llm_title="Rotate the beta password=fixture-not-a-secret",
         first_prompt="Rotate the beta credentials", started_at="2026-01-20T09:00:00Z",
         ended_at="2026-01-20T09:20:00Z", duration_s=1200, active_s=600, n_prompts=1, analysis_status="done",
         outcome="partial", machine_id=MACHINE),
]
# on a hub: a session another computer shared (source 'remote'); schema 20 clears the raw analysis it sent along
REMOTE_SESSION = dict(
    id=S4, source="remote", agent="claude", project_path=BETA, project_name="beta", title="Teammate tunes beta",
    llm_title="Teammate tunes beta", started_at="2026-01-21T09:00:00Z", analysis_status="done", outcome="achieved",
    sentiment="happy", analysis_json='{"raw": "what the other computer sent"}', machine_id=REMOTE_MACHINE)

EXPECTED_TITLES = {S1: "Set up the alpha build", S2: "Fix the flaky alpha test",
                   S3: "Rotate the beta password=[REDACTED]", S4: "Teammate tunes beta"}

SESSION_FILES = [
    dict(session_id=S1, path=f"{ALPHA}/build.toml", reads=1, edits=2, writes=0, lines_added=30, lines_removed=3),
    dict(session_id=S1, path=f"{ALPHA}/README.md", reads=1, edits=0, writes=1, lines_added=10, lines_removed=0),
    dict(session_id=S2, path=f"{ALPHA}/tests/test_alpha.py", reads=2, edits=1, writes=0, lines_added=5, lines_removed=5),
]

EVENTS = [
    dict(id=1, session_id=S1, agent_id="", seq=1, ts="2026-01-05T09:00:00Z", role="user", kind="prompt", searchable=1,
         text="Set up the alpha build and make the quokka step cache its output"),
    dict(id=2, session_id=S1, agent_id="", seq=2, ts="2026-01-05T09:00:10Z", role="assistant", kind="text",
         searchable=1, text="I'll add the zephyr cache flag to the quokka step."),
    dict(id=3, session_id=S1, agent_id="", seq=3, ts="2026-01-05T09:00:20Z", role="assistant", kind="tool_use",
         tool_name="Edit", tool_use_id="toolu_fixture_1", searchable=0, text="build.toml"),
    dict(id=4, session_id=S2, agent_id="", seq=1, ts="2026-01-12T09:00:00Z", role="user", kind="prompt", searchable=1,
         text="The alpha test flakes on the second run"),
    dict(id=5, session_id=S3, agent_id="", seq=1, ts="2026-01-20T09:00:00Z", role="user", kind="prompt", searchable=1,
         text="Rotate the beta credentials"),
]

TOOL_CALLS = [
    dict(id=1, session_id=S1, agent_id="", tool_use_id="toolu_fixture_1", ts="2026-01-05T09:00:20Z", name="Edit",
         summary="build.toml", file_path=f"{ALPHA}/build.toml"),
    dict(id=2, session_id=S1, agent_id="", tool_use_id="toolu_fixture_2", ts="2026-01-05T09:01:00Z", name="Bash",
         summary="make build", command="make build"),
    dict(id=3, session_id=S2, agent_id="", tool_use_id="toolu_fixture_3", ts="2026-01-12T09:05:00Z", name="Bash",
         summary="pytest", command="pytest tests/test_alpha.py", is_error=1),
]

# stage: what it is stored as where the release has the column; derived: what a schema-7 archive gets once the
# current code derives it (ladder.compute_stage, from what the item carries then: no confirmed_json yet)
KNOWLEDGE = [
    dict(id=1, session_id=S1, project_path=ALPHA, project_name="alpha", kind="fix",
         title="Pass the zephyr cache flag to the alpha build", body="Without it the quokka step rebuilds every time.",
         tags_json='["build", "cache"]', confidence="high", source="analysis", agent="claude",
         fingerprint="fixture-k1", status="active", pinned=0, created_at="2026-01-05T10:00:00Z",
         stage="provisional", stage_reason="one session", derived="provisional"),
    dict(id=2, session_id=S2, project_path=ALPHA, project_name="alpha", kind="gotcha",
         title="The alpha test shares a temp folder between runs", body="Give each run its own folder.",
         tags_json='["tests"]', confidence="medium", source="analysis", agent="codex", fingerprint="fixture-k2",
         status="active", pinned=0, created_at="2026-01-12T10:00:00Z", confirmed_json=json.dumps([S1, S2]),
         stage="established", stage_reason="confirmed in 2 sessions (2026-01-05 → 2026-01-12)", derived="provisional"),
    dict(id=3, session_id=S3, project_path=BETA, project_name="beta", kind="decision",
         title="Beta keeps its credentials in the system keychain", body="Never in a dotfile.",
         tags_json='["security"]', confidence="high", source="analysis", agent="claude", fingerprint="fixture-k3",
         status="active", pinned=1, created_at="2026-01-20T10:00:00Z",
         stage="canonical", stage_reason="pinned by you", derived="canonical"),
    dict(id=4, session_id=S1, project_path=ALPHA, project_name="alpha", kind="fix",
         title="Clear the zephyr cache by hand before each build", body="Replaced by the cache flag.",
         tags_json='["build"]', confidence="medium", source="analysis", agent="claude", fingerprint="fixture-k4",
         status="superseded", pinned=0, created_at="2026-01-05T10:00:00Z", superseded_by=1,
         superseded_reason="outdated", superseded_at="2026-01-06T10:00:00Z",
         stage="provisional", stage_reason="one session", derived="provisional"),
    dict(id=5, session_id=None, project_path=ALPHA, project_name="alpha", kind="command",
         title="make build builds alpha", body="Run from the project folder.", tags_json="[]", confidence="high",
         source="memory", agent="claude", source_ref="fixture-memory.md", fingerprint="fixture-k5", status="active",
         pinned=0, created_at="2026-01-05T11:00:00Z",
         stage="established", stage_reason="written by the agent's own memory", derived="established"),
    dict(id=6, session_id=S3, project_path=BETA, project_name="beta", kind="preference",
         title="Prefer short commit messages in beta", body="One line, imperative.", tags_json="[]", confidence="low",
         source="analysis", agent="claude", fingerprint="fixture-k6", status="active", pinned=0,
         created_at="2026-01-20T10:00:00Z", stage="wip", stage_reason="one session, low confidence", derived="wip"),
]
# on a hub: the lesson that came with the remote session
REMOTE_KNOWLEDGE = dict(
    id=7, session_id=S4, project_path=BETA, project_name="beta", kind="fact", title="Beta deploys on Fridays",
    body="The release train leaves at noon.", tags_json="[]", confidence="high", source="analysis", agent="claude",
    fingerprint="fixture-k7", status="active", pinned=0, created_at="2026-01-21T10:00:00Z",
    stage="provisional", stage_reason="one session", derived="provisional")

OTHER = {
    "kv": [dict(key="fixture", value="written by make_fixture.py")],
    "machines": [dict(id=MACHINE, name="fixture-laptop", platform="darwin", role="this",
                      first_seen="2026-01-05T09:00:00Z", last_seen="2026-01-20T09:00:00Z")],
    "project_kb": [dict(project_path=ALPHA, project_name="alpha", updated_at="2026-01-13T00:00:00Z", model="fixture",
                        knowledge_max_id=2, n_items=2, overview="Alpha builds with the zephyr cache.",
                        markdown="# alpha\n\n- Pass the zephyr cache flag to the alpha build\n", kb_json="{}")],
    "glossary": [dict(id=1, term="Zephyr cache", norm="zephyr cache", category="tool",
                      definition="The build cache the alpha project uses.", definition_source=ALPHA, n_sessions=1,
                      n_mentions=2, first_seen="2026-01-05T09:00:00Z", last_seen="2026-01-05T09:00:00Z")],
    # tables a later release added: skipped where the release doesn't have them
    "hub_projects": [dict(path=BETA, created_at="2026-01-15T00:00:00Z", created_by="this computer")],
    "project_groups": [dict(id=1, name="Fixture group", folders_json=json.dumps(["/home/fixture/projects"]),
                            created_at="2026-01-15T00:00:00Z")],
    "artifacts": [dict(id=1, session_id=S1, agent_id="", tool_use_id="toolu_fixture_1", seq=3,
                       ts="2026-01-05T09:00:20Z", key=f"file:{ALPHA}/README.md", kind="doc", action="created",
                       title="Alpha README", path=f"{ALPHA}/README.md", size=120)],
}


def _connect_with_release(db: Path):
    """The release's own connect(): creates the schema its way (and sets WAL, as it did)."""
    for mod in ("interlatch.storage.sqlite", "interlatch.db", "chronicle.db"):
        try:
            return __import__(mod, fromlist=["connect"]).connect(db)
        except ImportError:
            continue
    raise SystemExit("no connect() in this release")


def _release() -> tuple[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    for name in ("interlatch", "agents-chronicle"):
        try:
            return name, version(name)
        except PackageNotFoundError:
            continue
    raise SystemExit("neither interlatch nor agents-chronicle is installed")


def _insert(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if not have:
        return -1  # the release has no such table
    for row in rows:
        row = {k: v for k, v in row.items() if k in have}
        conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    return len(rows)


def child(db: Path, expected_path: Path) -> None:
    package, ver = _release()
    conn = _connect_with_release(db)
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]
    kcols = {r[1] for r in conn.execute("PRAGMA table_info(knowledge)")}
    hub = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'hub_projects'").fetchone())
    sessions = SESSIONS + ([REMOTE_SESSION] if hub else [])
    knowledge = KNOWLEDGE + ([REMOTE_KNOWLEDGE] if hub else [])
    counts = {"sessions": _insert(conn, "sessions", sessions), "session_files": _insert(conn, "session_files", SESSION_FILES),
              "events": _insert(conn, "events", EVENTS), "tool_calls": _insert(conn, "tool_calls", TOOL_CALLS),
              "knowledge": _insert(conn, "knowledge", [{k: v for k, v in item.items() if k != "derived"}
                                                       for item in knowledge])}
    for table, rows in OTHER.items():
        n = _insert(conn, table, rows)
        if n >= 0:
            counts[table] = n
    conn.commit()
    # small files: 1 KiB pages (an archive's ~30 tables, indexes and full-text tables take a page each, 4 KiB by
    # default). The page size can't change in WAL mode, so leave it, VACUUM, and go back to what the release set.
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("PRAGMA page_size = 1024")
    conn.execute("VACUUM")
    conn.execute(f"PRAGMA journal_mode = {journal}")
    conn.close()
    expected = {
        "written_by": {"package": package, "version": ver, "user_version": user_version,
                       "source": RELEASES[ver][1] if ver in RELEASES else None},
        "counts": counts,
        "sessions": {s["id"]: {"title": EXPECTED_TITLES[s["id"]], "project_name": s["project_name"],
                               "agent": s["agent"], "source": s["source"]} for s in sessions},
        "knowledge": {str(k["id"]): {"kind": k["kind"], "title": k["title"], "status": k["status"],
                                     "pinned": k["pinned"], "project_name": k["project_name"],
                                     "stage": k["stage"] if "stage" in kcols else k["derived"]} for k in knowledge},
        "session_files": {S1: sorted(f["path"] for f in SESSION_FILES if f["session_id"] == S1)},
        "search": {"knowledge zephyr": [1], "knowledge zephyr, inactive too": [1, 4], "sessions quokka": [S1]},
        "remote_session": S4 if hub else None,
    }
    expected_path.write_text(json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def make(ver: str, scratch: Path) -> None:
    package, spec = RELEASES[ver]
    work = scratch / ver
    shutil.rmtree(work, ignore_errors=True)
    (work / "home").mkdir(parents=True)
    uvx = shutil.which("uvx")
    if not uvx:
        raise SystemExit("uvx not found")
    db, expected = work / "chronicle.db", work / "expected.json"
    cmd = ["uvx", "--python", "3.12", "--from", spec, "python", "-I", str(Path(__file__).resolve()), "--child",
           str(db), str(expected)]
    env = {"HOME": str(work / "home"), "PATH": f"/usr/bin:/bin:{Path(uvx).parent}"}
    print(f"{ver}: {package} via {spec}", flush=True)
    subprocess.run(cmd, env=env, check=True)
    for extra in ("-wal", "-shm", "-journal"):
        if Path(f"{db}{extra}").exists():
            raise SystemExit(f"{db}{extra} left behind: the archive wasn't closed cleanly")
    out = HERE / ver
    out.mkdir(exist_ok=True)
    shutil.copyfile(db, out / "chronicle.db")
    shutil.copyfile(expected, out / "expected.json")
    print(f"{ver}: wrote {out / 'chronicle.db'} ({(out / 'chronicle.db').stat().st_size} bytes)")


def main(argv: list[str]) -> None:
    if argv[:1] == ["--child"]:
        child(Path(argv[1]), Path(argv[2]))
        return
    if not argv or argv[0] not in (*RELEASES, "all"):
        raise SystemExit(f"usage: make_fixture.py <{'|'.join(RELEASES)}|all> [scratch folder]")
    scratch = Path(argv[1]) if len(argv) > 1 else Path(tempfile.mkdtemp(prefix="interlatch-db-fixture-"))
    for ver in (RELEASES if argv[0] == "all" else [argv[0]]):
        make(ver, scratch)


if __name__ == "__main__":
    main(sys.argv[1:])
