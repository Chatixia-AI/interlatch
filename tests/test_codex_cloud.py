"""Codex Cloud tasks, through a fake `codex` CLI on PATH."""

import gzip
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from interlatch.sources.codex.cloud import diff_files, load_status, parse_cloud_task
from interlatch.core.config import load_config
from interlatch.storage.sqlite import connect as db_connect
from interlatch.ingestion.service import sync

from conftest import CWD

DIFF = """diff --git a/README.md b/README.md
index 1111111..2222222 100644
--- a/README.md
+++ b/README.md
@@ -1,2 +1,3 @@
 # App
-old line
+new line
+another line
diff --git a/src/new.py b/src/new.py
new file mode 100644
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1 @@
+print("hi")
"""


def _task(tid, title, env, updated, status="ready"):
    return {"id": tid, "url": f"https://chatgpt.com/codex/tasks/{tid}", "title": title, "status": status,
            "updated_at": updated, "environment_id": None, "environment_label": env,
            "summary": {"files_changed": 2, "lines_added": 3, "lines_removed": 1}, "is_review": True, "attempt_total": 1}


FAKE_CODEX = """#!{python}
import json, os, sys
state = json.load(open(os.environ["FAKE_CODEX_STATE"]))
with open(os.environ["FAKE_CODEX_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 9.9.9"); sys.exit(0)
if state.get("fail"):
    print("Error: not logged in; run `codex login`", file=sys.stderr); sys.exit(1)
if args[:2] == ["cloud", "list"]:
    pages = state["pages"]
    i = int(args[args.index("--cursor") + 1]) if "--cursor" in args else 0
    print(json.dumps({"tasks": pages[i], "cursor": str(i + 1) if i + 1 < len(pages) else None})); sys.exit(0)
if args[:2] == ["cloud", "diff"]:
    print(state["diff"], end=""); sys.exit(0)
sys.exit(2)
"""


@pytest.fixture()
def cloud(env, monkeypatch):
    t = env["tmp"]
    bindir = t / "codex-bin"
    bindir.mkdir()
    fake = bindir / "codex"
    fake.write_text(FAKE_CODEX.replace("{python}", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(t / "codex.log"))
    monkeypatch.setenv("FAKE_CODEX_STATE", str(t / "codex-state.json"))
    local = Path(CWD).name

    def set_state(**state):
        (t / "codex-state.json").write_text(json.dumps(state))

    set_state(pages=[[_task("task_e_1", "Add a health check", local, "2026-09-20T12:00:00.123456789Z")],
                     [_task("task_e_2", "Fix the docs", "someone/other-repo", "2026-09-19T08:00:00.5Z", status="pending")]],
              diff=DIFF)
    env["set_state"] = set_state
    env["calls"] = lambda: (t / "codex.log").read_text().splitlines() if (t / "codex.log").exists() else []
    return env


def test_diff_files_counts_lines_per_file():
    assert diff_files(DIFF) == {"README.md": (2, 1), "src/new.py": (1, 0)}
    assert diff_files(None) == {}


def test_parse_cloud_task():
    ps = parse_cloud_task(_task("task_e_9", "Tidy up", "repo", "2026-09-20T12:00:00.123456789Z"), DIFF, "/src/repo")
    assert ps.custom_title == "Tidy up" and ps.entrypoint == "codex:cloud" and ps.n_prompts == 0
    assert ps.started_at == ps.ended_at == "2026-09-20T12:00:00.123Z"
    assert ps.lines_added == 3 and ps.lines_removed == 1 and set(ps.files) == {"README.md", "src/new.py"}
    assert ps.artifacts[0]["url"].endswith("/task_e_9")
    texts = [e.text for e in ps.events]
    assert texts[0].startswith("Codex Cloud task · repo · ready") and "```diff" in texts[2]


def test_codex_cloud_is_opt_in(cloud):
    conn = db_connect(cloud["cfg"].db_path)
    sync(cloud["cfg"], conn)
    assert cloud["calls"]() == [] and not conn.execute("SELECT COUNT(*) FROM sessions WHERE source = 'codex-cloud'").fetchone()[0]


def test_sync_records_tasks_and_skips_unchanged(cloud):
    from interlatch.runtime.connectors import all_status, connect, disconnect

    cfg = cloud["cfg"]
    assert connect(cfg, "codex-cloud", "/opt/bin/chronicle") == ["recording Codex Cloud tasks"]
    cfg = load_config(cfg.home)
    assert cfg.codex_cloud
    conn = db_connect(cfg.db_path)
    report = sync(cfg, conn)
    assert report.sessions_new >= 2 and not report.errors
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM sessions WHERE source = 'codex-cloud'")}
    assert set(rows) == {"task_e_1", "task_e_2"}
    one = rows["task_e_1"]
    assert one["agent"] == "codex" and one["project_path"] == CWD  # matched to the local project of that name
    assert rows["task_e_2"]["project_name"] == "other-repo"
    assert one["title"] == "Add a health check" and one["lines_added"] == 3 and one["analysis_status"] == "skipped"
    with gzip.open(one["transcript_path"], "rt") as f:
        assert json.load(f)["diff"] == DIFF
    assert sum(c.startswith("cloud diff") for c in cloud["calls"]()) == 2

    status = next(c for c in all_status(cfg, conn) if c["name"] == "codex-cloud")
    assert status["connected"] and status["on_disk"] == 2 and status["recorded"]["sessions"] == 2 and status["agent"] == "codex"
    codex = next(c for c in all_status(cfg, conn) if c["name"] == "codex")
    assert codex["recorded"]["sessions"] == 0  # cloud tasks are counted on their own card

    before = len(cloud["calls"]())
    report = sync(cfg, conn)
    assert not any(c.startswith("cloud diff") for c in cloud["calls"]()[before:])  # unchanged: no diff fetched again

    disconnect(cfg, "codex-cloud")
    assert not load_config(cfg.home).codex_cloud


def test_listing_failure_is_reported_not_raised(cloud):
    from interlatch.runtime.connectors import connect

    connect(cloud["cfg"], "codex-cloud", "/opt/bin/chronicle")
    cfg = load_config(cloud["cfg"].home)
    cloud["set_state"](fail=True)
    conn = db_connect(cfg.db_path)
    report = sync(cfg, conn)
    assert not report.errors
    status = load_status(conn)
    assert status["ok"] is False and "not logged in" in status["error"]
