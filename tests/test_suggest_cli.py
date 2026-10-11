"""Suggestions and 'what goes wrong' from the outside: the CLI, the dashboard API, and the background run's hook."""

import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from http.server import ThreadingHTTPServer

import pytest
from friction_fixture import ago, make_archive

from chronicle import suggest
from chronicle.cli import main
from chronicle.instructions import BEGIN


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    yield from make_archive(tmp_path, monkeypatch)


@pytest.fixture()
def queued(archive):
    """zsh-nomatch in 3 sessions of 3 projects: an instruction line for ~/.claude/CLAUDE.md and a setup step."""
    a, conn = archive["a"], archive["conn"]
    for i in range(3):
        p = a.project(f"proj{i}")
        a.session(f"zsh-{i}", p)
        a.cause(f"zsh-{i}", "zsh-nomatch", ago(1))
    a.session("noise-0", a.project("proj0"))
    a.result("noise-0", ago(1), "Bash", "Exit code 1\nFAILED tests/test_x.py::test_y - AssertionError\n1 failed",
             command="uv run pytest -q")
    conn.commit()
    suggest.refresh(conn, archive["cfg"])
    by_key = {s["key"]: s for s in suggest.list_suggestions(conn)}
    archive["line"] = by_key["friction:zsh-nomatch:claude:user"]
    archive["env"] = by_key["friction:zsh-nomatch:all:user:environment"]
    return archive


def test_cli_lists_shows_applies_and_undoes(queued, capsys):
    line, home = queued["line"], queued["home"]
    target = home / ".claude" / "CLAUDE.md"
    assert main(["suggest", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["id"] for r in rows} == {line["id"], queued["env"]["id"]} and rows[0]["evidence"]["line"]

    assert main(["suggest"]) == 0
    out = capsys.readouterr().out
    assert f"#{line['id']}" in out and "~/.claude/CLAUDE.md" in out and "seen in 3 sessions across 3 projects" in out

    assert main(["suggest", "show", str(line["id"])]) == 0
    assert f"+{BEGIN}" in capsys.readouterr().out and not target.exists()  # show writes nothing

    assert main(["suggest", "apply", str(line["id"]), "--yes"]) == 0
    assert "applied to ~/.claude/CLAUDE.md" in capsys.readouterr().out
    assert "<!-- interlatch:friction:zsh-nomatch -->" in target.read_text()  # a path-free marker
    assert suggest.get(queued["conn"], line["id"])["status"] == "applied"

    assert main(["suggest"]) == 0  # applied ones leave the default list
    assert f"#{line['id']}" not in capsys.readouterr().out
    assert main(["suggest", "--all"]) == 0
    assert "(applied)" in capsys.readouterr().out

    assert main(["suggest", "undo", str(line["id"])]) == 0
    assert not target.exists()  # Chronicle created it, so undo removes it
    assert suggest.get(queued["conn"], line["id"])["status"] == "new"


def test_cli_apply_asks_first_and_refuses_setup_steps(queued, capsys, monkeypatch):
    line, env, home = queued["line"], queued["env"], queued["home"]
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert main(["suggest", "apply", str(line["id"])]) == 0
    assert "skipped" in capsys.readouterr().out and not (home / ".claude" / "CLAUDE.md").exists()

    assert main(["suggest", "apply", str(env["id"]), "--yes"]) == 1  # Chronicle never runs commands
    out = capsys.readouterr().out
    assert "run it yourself" in out and "NO_NOMATCH" in out
    assert main(["suggest", "done", str(env["id"])]) == 0
    assert suggest.get(queued["conn"], env["id"])["status"] == "done"
    assert main(["suggest", "done", str(line["id"])]) == 1  # only setup steps are marked done

    assert main(["suggest", "dismiss", str(line["id"]), "--reason", "not for me"]) == 0
    row = suggest.get(queued["conn"], line["id"])
    assert row["status"] == "dismissed" and row["dismissed_reason"] == "not for me"
    assert main(["suggest", "refresh"]) == 0
    assert "0 new" in capsys.readouterr().out.splitlines()[-1]
    assert suggest.get(queued["conn"], line["id"])["status"] == "dismissed"  # never comes back
    assert main(["suggest", "show", "99999"]) == 1


def test_cli_friction(queued, capsys):
    assert main(["friction", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert [c["id"] for c in got["causes"]] == ["zsh-nomatch"] and got["noise_summary"]["occurrences"] == 1
    assert got["tool_errors"][0]["tool"] == "Bash"

    assert main(["friction"]) == 0
    out = capsys.readouterr().out
    assert "zsh-nomatch" in out and " yes " in out and "filtered as noise: 1 failure in 1 session" in out
    assert "expected-test-failures" not in out
    assert main(["friction", "--noise", "-p", "proj0"]) == 0
    out = capsys.readouterr().out
    assert "expected-test-failures" in out and "Noise (expected failures" in out


def _serve(cfg):
    from chronicle.server import App, make_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    httpd.RequestHandlerClass = make_handler(App(cfg), httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _call(base, path, body=None, *, header=True):
    headers = {"X-Interlatch": "1", "Content-Type": "application/json"} if header else {}
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method="GET" if body is None else "POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_api_routes(queued):
    line, env, home = queued["line"], queued["env"], queued["home"]
    target = home / ".claude" / "CLAUDE.md"
    httpd, base = _serve(queued["cfg"])
    try:
        status, got = _call(base, "/api/suggestions")
        assert status == 200 and got["counts"]["new"] == 2 and len(got["suggestions"]) == 2
        assert isinstance(got["suggestions"][0]["evidence"], dict) and "evidence_json" not in got["suggestions"][0]
        assert _call(base, "/api/suggestions?status=dismissed")[1]["suggestions"] == []
        assert _call(base, "/api/suggestions/unseen") == (200, {"unseen": 2})

        assert _call(base, "/api/suggestions/seen", {}, header=False)[0] == 403  # writes need the dashboard's header
        assert _call(base, f"/api/suggestions/{line['id']}/apply", {}, header=False)[0] == 403
        assert not target.exists()
        assert _call(base, "/api/suggestions/seen", {}) == (200, {"ok": True})
        assert _call(base, "/api/suggestions/unseen")[1] == {"unseen": 0}

        status, pv = _call(base, f"/api/suggestions/{line['id']}/preview?text=Quote%20globs%20in%20zsh.")
        assert status == 200 and pv["path"] == str(target) and "+- Quote globs in zsh." in pv["diff"]
        assert pv["warnings"]["sensitive"] == [] and not target.exists()
        assert _call(base, f"/api/suggestions/{env['id']}/preview")[1]["command"]
        assert _call(base, "/api/suggestions/99999/preview")[0] == 404

        status, res = _call(base, f"/api/suggestions/{line['id']}/apply", {"text": "Quote globs in zsh."})
        assert status == 200 and res["ok"] and res["path"] == str(target) and f"+{BEGIN}" in res["diff"]
        assert "Quote globs in zsh." in target.read_text()
        assert _call(base, f"/api/suggestions/{line['id']}/unapply", {})[1]["ok"]
        assert not target.exists()  # Chronicle created it, so undo removes it

        status, res = _call(base, f"/api/suggestions/{env['id']}/apply", {})
        assert status == 400 and "mark it done" in res["error"]
        assert _call(base, f"/api/suggestions/{env['id']}/done", {}) == (200, {"ok": True})
        assert _call(base, f"/api/suggestions/{line['id']}/dismiss", {"reason": "nope"}) == (200, {"ok": True})
        assert _call(base, "/api/suggestions/99999/dismiss", {})[0] == 404
        assert _call(base, "/api/suggestions/refresh", {}) == (200, {"new": 0, "updated": 0, "stale": 0})
        counts = _call(base, "/api/suggestions")[1]["counts"]
        assert counts == {"new": 0, "applied": 0, "dismissed": 1, "stale": 0, "done": 1}

        status, fr = _call(base, "/api/friction?days=30")
        assert status == 200 and [c["id"] for c in fr["causes"]] == ["zsh-nomatch"]
        assert fr["noise_summary"] == {"occurrences": 1, "sessions": 1} and fr["tool_errors"]
        assert "expected-test-failures" in [c["id"] for c in _call(base, "/api/friction?noise=1")[1]["causes"]]
    finally:
        httpd.shutdown()


def test_background_run_refreshes_and_notifies(archive, monkeypatch):
    from chronicle.worker import run_worker

    a, conn, cfg = archive["a"], archive["conn"], archive["cfg"]
    cfg.ensure_dirs()
    for i in range(3):
        a.session(f"s{i}", a.project(f"p{i}"))
        a.cause(f"s{i}", "zsh-nomatch", ago(1))
    conn.commit()
    posted = []
    monkeypatch.setattr("chronicle.notify.post", lambda title, message: posted.append((title, message)) or True)

    report = run_worker(replace(cfg, suggestions_notify=True), analyze=False, synthesize=False, export=False)
    assert report.suggestions == 2 and "2 new suggestions" in report.summary()
    assert posted == [("Interlatch", "2 new suggestions. Open the dashboard > Suggestions")]
    assert run_worker(replace(cfg, suggestions_notify=True), analyze=False, synthesize=False, export=False).suggestions == 0
    assert len(posted) == 1  # nothing new, no notification

    conn.execute("DELETE FROM suggestions")
    conn.commit()
    run_worker(replace(cfg, suggestions_enabled=False), analyze=False, synthesize=False, export=False)
    assert conn.execute("SELECT COUNT(*) FROM suggestions").fetchone()[0] == 0
