"""Artifacts: what a session made (files, published pages, PRs, commits, images), from explicit transcript signals."""

import json
import os

import pytest

from chronicle import artifacts
from chronicle.artifacts import commit_message, kind_for_path
from chronicle.claude_export import parse_conversation
from chronicle.codex_parser import parse_codex_session
from chronicle.copilot_parser import tool_summary
from chronicle.parser import parse_session

from conftest import CWD, SID, _line


def test_only_deliverables_count():
    assert kind_for_path(f"{CWD}/docs/design.md") == "doc"
    assert kind_for_path("/private/tmp/claude-501/x/scratchpad/report.html") == "page"
    assert kind_for_path(f"{CWD}/.github/profile/assets/system.svg") == "diagram"
    assert kind_for_path("/out/deck.pptx") == "deck" and kind_for_path("/out/budget.xlsx") == "sheet"
    assert kind_for_path(f"{CWD}/app.py") is None  # code is work, not an artifact
    assert kind_for_path(f"{CWD}/CLAUDE.md") is None and kind_for_path("/Users/x/.claude/projects/p/memory/note.md") is None
    assert kind_for_path(f"{CWD}/src/web/index.html") is None  # a page of the app itself
    assert kind_for_path(f"{CWD}/vscode-extension/media/icon.svg") is None
    assert kind_for_path(f"{CWD}/.coworker/skills/x/resources/table.csv") is None
    assert kind_for_path(f"{CWD}/node_modules/pkg/README.md") is None
    assert kind_for_path(None) is None


def test_commit_messages():
    assert commit_message('git commit -m "Fix the bug"') == "Fix the bug"
    assert commit_message("git -C repo commit -am 'quick fix'") == "quick fix"
    assert commit_message("git commit -q -F - <<'EOF'\nfeat: 見積チャット\n\nbody\nEOF") == "feat: 見積チャット"
    assert commit_message('git commit -m "$(cat <<\'EOF\'\nAdd the thing\n\nCo-Authored-By: x\nEOF\n)"') == "Add the thing"
    assert commit_message("git commit --amend --no-edit") is None


def _tool(i, name, inp, result, *, tur=None, error=False):
    ts = f"2026-09-20T10:{i:02d}:00.000Z"
    out = [_line(type="assistant", uuid=f"a{i}", timestamp=ts, message={
        "id": f"m{i}", "model": "claude-opus-5-5", "role": "assistant",
        "content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]})]
    extra = {"toolUseResult": tur} if tur is not None else {}
    out.append(_line(type="user", uuid=f"u{i}", timestamp=ts, **extra, message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": f"t{i}", "content": result, "is_error": error}]}))
    return out


def _claude(tmp_path, lines):
    p = tmp_path / f"{SID}.jsonl"
    p.write_text("\n".join([_line(type="user", uuid="u0", timestamp="2026-09-20T10:00:00.000Z",
                                  message={"role": "user", "content": "make the report"}), *lines]) + "\n")
    ps = parse_session(p)
    artifacts.finish(ps)
    return ps


def test_claude_code_session(tmp_path):
    html = "<html>report</html>"
    lines = [
        *_tool(1, "Write", {"file_path": "/tmp/scratchpad/report.html", "content": html}, "File created",
               tur={"type": "create", "filePath": "/tmp/scratchpad/report.html", "content": html}),
        *_tool(2, "Write", {"file_path": "/tmp/scratchpad/report.html", "content": html + "!"}, "File updated",
               tur={"type": "update", "filePath": "/tmp/scratchpad/report.html"}),
        *_tool(3, "Edit", {"file_path": f"{CWD}/README.md", "old_string": "a", "new_string": "b"}, "ok"),
        *_tool(4, "Write", {"file_path": f"{CWD}/notes.md", "content": "x"}, "denied", error=True),
        *_tool(5, "Artifact", {"file_path": "/tmp/scratchpad/report.html"}, "Published https://claude.ai/code/artifact/abc-123",
               tur={"url": "https://claude.ai/code/artifact/abc-123", "title": "Weekly report", "artifact_id": "abc-123",
                    "version": "v2", "path": "/tmp/scratchpad/report.html"}),
        *_tool(6, "Artifact", {"action": "read", "url": "https://claude.ai/code/artifact/zzz"}, "page https://claude.ai/code/artifact/zzz"),
        *_tool(7, "Bash", {"command": 'gh pr create --title "Add the report" --body x'}, "https://github.ibm.com/team/repo/pull/12\n"),
        *_tool(8, "Bash", {"command": "git commit -m 'Add report'"}, "[main 1a2b3c4] Add report\n 1 file changed"),
        *_tool(9, "Bash", {"command": "git commit -q -m 'Tidy' && git log --oneline -1"}, "5e6f7a8 Tidy\n"),
        *_tool(10, "Bash", {"command": "git commit -q -F - <<'EOF'\nQuiet one\nEOF"}, ""),
        *_tool(11, "Bash", {"command": "git commit -m 'Nope'"}, "nothing to commit", error=True),
        *_tool(12, "Read", {"file_path": "/tmp/shot.png"}, [{"type": "image", "source": {"data": "x"}}]),
    ]
    ps = _claude(tmp_path, lines)
    by = {o["key"]: o for o in ps.outputs}
    page = by["file:/tmp/scratchpad/report.html"]
    assert (page["kind"], page["action"], page["versions"]) == ("page", "created", 2)  # written, then rewritten
    assert page["sha256"] == artifacts._digest(html + "!")[1] and page["tool_use_id"] == "t2"
    assert f"file:{CWD}/README.md" not in by and f"file:{CWD}/notes.md" not in by  # an edit; a failed write
    pub = by["url:https://claude.ai/code/artifact/abc-123"]
    assert (pub["kind"], pub["title"], pub["meta"]["version"]) == ("published", "Weekly report", "v2")
    assert "url:https://claude.ai/code/artifact/zzz" not in by  # reading an artifact made nothing
    pr = by["url:https://github.ibm.com/team/repo/pull/12"]
    assert (pr["kind"], pr["title"], pr["meta"]["number"]) == ("pr", "Add the report", 12)
    assert by["commit:1a2b3c4"]["title"] == "Add report" and by["commit:1a2b3c4"]["meta"]["branch"] == "main"
    assert by["commit:5e6f7a8"]["title"] == "Tidy"
    quiet = [o for o in ps.outputs if o["kind"] == "commit" and not o["meta"].get("sha")]
    assert [o["title"] for o in quiet] == ["Quiet one"]  # and the failed commit is not one
    assert ps.n_result_images == 1
    assert any(p["url"].endswith("/pull/12") for p in ps.prs)  # the session's PR list learns it too
    assert any(a["url"].endswith("abc-123") for a in ps.artifacts)


def test_codex_session(tmp_path):
    def rec(t, payload, ts="2026-09-20T10:00:00.000Z"):
        return json.dumps({"timestamp": ts, "type": t, "payload": payload})
    p = tmp_path / "rollout-2026-09-20T10-00-00-0199aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee.jsonl"
    patch = "*** Begin Patch\n*** Add File: docs/guide.md\n+# Guide\n*** Update File: app.py\n+x\n*** End Patch"
    script = 'const r = await tools.image_gen__imagegen({prompt:"a cat"}); text(r);'
    p.write_text("\n".join([
        rec("session_meta", {"id": "0199aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee", "cwd": CWD}),
        rec("turn_context", {"cwd": CWD, "model": "gpt-5"}),
        rec("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "write a guide"}]}),
        rec("response_item", {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c1", "input": patch}),
        rec("response_item", {"type": "custom_tool_call_output", "call_id": "c1", "output": "Success"}),
        rec("response_item", {"type": "function_call", "name": "exec_command", "call_id": "c2",
                              "arguments": json.dumps({"cmd": "gh pr create --fill"})}),
        rec("response_item", {"type": "function_call_output", "call_id": "c2", "output": "https://github.com/o/r/pull/3"}),
        rec("response_item", {"type": "custom_tool_call", "name": "exec", "call_id": "c3", "input": script}),
        rec("response_item", {"type": "custom_tool_call_output", "call_id": "c3",
                              "output": "Script completed\n/Users/x/.codex/generated_images/abc/exec-1.png"}),
    ]) + "\n")
    ps = parse_codex_session(p)
    artifacts.finish(ps)
    by = {o["key"]: o for o in ps.outputs}
    assert by[f"file:{CWD}/docs/guide.md"]["kind"] == "doc" and f"file:{CWD}/app.py" not in by
    assert by["url:https://github.com/o/r/pull/3"]["kind"] == "pr"
    image = by["file:/Users/x/.codex/generated_images/abc/exec-1.png"]
    assert (image["action"], image["title"], image["meta"]["prompt"]) == ("generated", "a cat", "a cat")
    assert artifacts.prompt_title("Use case: logo\nAsset type: square brand icon, flat\nPrimary request: x") == "square brand icon, flat"
    assert artifacts.prompt_title("Create a cover. Wide, 3:2.") == "Create a cover"


def test_claude_ai_chat():
    conv = {"uuid": "conv-1", "name": "Deck", "created_at": "2026-09-20T10:00:00Z", "updated_at": "2026-09-20T10:05:00Z",
            "chat_messages": [
                {"uuid": "m1", "sender": "human", "created_at": "2026-09-20T10:00:00Z", "content": [{"type": "text", "text": "make a deck"}]},
                {"uuid": "m2", "sender": "assistant", "created_at": "2026-09-20T10:01:00Z", "content": [
                    {"type": "tool_use", "id": "x1", "name": "artifacts",
                     "input": {"id": "plan", "type": "text/markdown", "title": "Plan", "command": "create", "content": "# Plan"}},
                    {"type": "tool_result", "tool_use_id": "x1", "name": "artifacts", "content": [{"type": "text", "text": "OK"}]},
                    {"type": "tool_use", "id": "x2", "name": "artifacts",
                     "input": {"id": "plan", "command": "update", "old_str": "Plan", "new_str": "Better plan"}},
                    {"type": "tool_use", "id": "x3", "name": "create_file", "input": {"path": "/home/claude/draft.md", "file_text": "d"}},
                    {"type": "tool_use", "id": "x4", "name": "present_files", "input": {"filepaths": ["/mnt/user-data/outputs/deck.pptx"]}},
                    {"type": "tool_result", "tool_use_id": "x4", "name": "present_files", "content": [
                        {"type": "local_resource", "file_path": "/mnt/user-data/outputs/deck.pptx", "name": "Deck",
                         "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}]}]}]}
    ps = parse_conversation(conv, {})
    by = {o["key"]: o for o in ps.outputs}
    plan = by["claude-ai:conv-1:plan"]
    assert (plan["kind"], plan["title"], plan["versions"]) == ("doc", "Plan", 2)
    assert by["file:/mnt/user-data/outputs/deck.pptx"]["kind"] == "deck"
    assert "file:/home/claude/draft.md" not in by  # a draft in claude.ai's sandbox, not what it handed over
    # claude.ai's artifacts tool has a "command" (create/update), which is not a shell command
    assert tool_summary("artifacts", {"id": "plan", "command": "create", "title": "Plan"}) == ("create Plan", None, None)
    assert all(c.command is None for c in ps.tool_calls if c.name == "artifacts")


def test_stored_with_the_session(synced):
    from chronicle.ingest import forget_session

    conn = synced["conn"]
    rows = [dict(r) for r in conn.execute("SELECT * FROM artifacts WHERE session_id = ?", (SID,))]
    pr = next(r for r in rows if r["kind"] == "pr")  # the transcript's pr-link record
    assert pr["url"] == "https://github.com/x/y/pull/7" and pr["title"] == "x/y#7"
    from chronicle.mcp_server import TOOLS, Tools
    from chronicle.server import App

    assert any(t["name"] == "find_artifacts" and t["annotations"]["readOnlyHint"] for t in TOOLS)
    out = Tools(synced["cfg"]).find_artifacts(kind="pr")
    assert "**x/y#7**" in out and "github.com/x/y/pull/7" in out and "link" in out
    assert Tools(synced["cfg"]).find_artifacts(query="nothing-like-this") == "No artifacts match."
    app = App(synced["cfg"])
    data = app.artifacts({"kind": "pr"})
    assert data["total"] == 1 and data["counts"] == {"pr": 1} and data["items"][0]["status"] == "link"
    assert app.session(SID)["outputs"][0]["url"] == "https://github.com/x/y/pull/7"
    assert app.project(CWD)["artifacts"]["total"] == 1
    forget_session(conn, synced["cfg"], SID)
    assert not conn.execute("SELECT COUNT(*) FROM artifacts WHERE session_id = ?", (SID,)).fetchone()[0]


def test_a_mention_of_git_commit_is_not_a_commit():
    assert commit_message("""python3 - <<'EOF'\nprint(commit_message("git -C repo commit -am 'quick fix'"))\nEOF""") is None
    assert commit_message("cd repo && git add -A && git commit -q -m 'Real one'") == "Real one"
    assert commit_message("git commit -q -F - <<'EOF'\nSubject\nEOF") == "Subject"
    assert commit_message("git commit -q -F msg.txt") is None  # the message is in a file we cannot see
    written = "cat >> t.py <<'EOF'\nassert run(\"cd r && git commit -q -m 'x'\")\nEOF"
    assert commit_message(written) is None  # a heredoc's body is a file being written, not commands being run


def test_mentions_in_a_script_make_no_commit(tmp_path):
    script = "python3 - <<'EOF'\nimport json\nprint('git commit -m \"x\"')\nEOF"
    ps = _claude(tmp_path, _tool(1, "Bash", {"command": script}, "[main 1a2b3c4] x\n"))
    assert not [o for o in ps.outputs if o["kind"] == "commit"]


def test_images_are_served_only_as_recorded_artifacts(synced, tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from chronicle.server import App, make_handler

    conn = synced["conn"]
    svg = tmp_path / "flow.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    note = tmp_path / "notes.md"
    note.write_text("# notes")

    def add(path, kind):
        cur = conn.execute("INSERT INTO artifacts(session_id, key, kind, action, title, path) VALUES (?, ?, ?, 'created', ?, ?)",
                           (SID, f"file:{path}", kind, os.path.basename(path), str(path)))
        return cur.lastrowid
    svg_id, note_id, gone_id = add(svg, "diagram"), add(note, "doc"), add(tmp_path / "gone.png", "image")
    conn.commit()
    app = App(synced["cfg"])
    items = {i["id"]: i for i in app.artifacts({})["items"]}
    assert items[svg_id]["preview"] and not items[note_id]["preview"] and not items[gone_id]["preview"]
    assert app.artifact_file(svg_id)[1] == "image/svg+xml"
    assert app.artifact_file(note_id) is None and app.artifact_file(gone_id) is None and app.artifact_file(999999) is None

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    httpd.RequestHandlerClass = make_handler(app, httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/api/artifacts/{svg_id}/file", timeout=10) as r:
            assert r.headers["Content-Type"] == "image/svg+xml" and r.headers["X-Content-Type-Options"] == "nosniff"
            assert "sandbox" in r.headers["Content-Security-Policy"]  # opened on its own, its script cannot run
            assert b"<svg" in r.read()
        for bad in (note_id, gone_id, "../../etc/passwd"):
            try:
                urllib.request.urlopen(f"{base}/api/artifacts/{bad}/file", timeout=10)
                raise AssertionError(f"served {bad}")
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
    finally:
        httpd.shutdown()


def test_open_a_file_even_after_it_is_gone(synced, monkeypatch):
    """Open serves the file from disk, or as the agent wrote it from the archived transcript once it is deleted;
    the Mac actions run only for this computer's own browser."""
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from chronicle.ingest import sync
    from chronicle.server import App, make_handler

    from conftest import PROJECT_DIR

    cfg, conn, tmp = synced["cfg"], synced["conn"], synced["tmp"]
    report, notes = tmp / "out" / "report.html", tmp / "out" / "notes.md"
    report.parent.mkdir()
    html = "<html><script>document.title='live'</script><body>Weekly report</body></html>"
    sid = "22222222-3333-4444-5555-666666666666"
    lines = [_line(type="user", uuid="w0", sessionId=sid, timestamp="2026-09-21T10:00:00.000Z",
                   message={"role": "user", "content": "write the report"})]
    for i, (path, content) in enumerate(((report, html), (notes, "# notes"))):
        lines += [line.replace(SID, sid) for line in _tool(i + 1, "Write", {"file_path": str(path), "content": content}, "ok",
                                                          tur={"type": "create", "filePath": str(path), "content": content})]
        path.write_text(content)
    (synced["claude_dir"] / "projects" / PROJECT_DIR / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")
    sync(cfg, conn)
    ids = {r["path"]: r["id"] for r in conn.execute("SELECT id, path FROM artifacts WHERE session_id = ?", (sid,))}
    report.unlink()  # gone from disk; the archive still holds what was written

    app = App(cfg)
    items = {i["id"]: i for i in app.artifacts({})["items"]}
    assert items[ids[str(report)]]["status"] == "gone" and items[ids[str(report)]]["openable"]
    f = app.artifact_open(ids[str(report)])
    assert (f["body"].decode(), f["source"], f["ctype"]) == (html, "archive", "text/html; charset=utf-8")
    assert "sandbox allow-scripts" in f["policy"] and "allow-same-origin" not in f["policy"]  # its scripts run, walled off
    assert app.artifact_open(ids[str(notes)])["ctype"] == "text/plain; charset=utf-8"

    launched = []
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: launched.append(cmd))
    assert app.artifact_reveal(ids[str(report)], "open") == "that file is not on this computer"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def call(path, *, method="GET", headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b'{"how": "reveal"}' if method == "POST" else None,
                                     headers={"X-Interlatch": "1", "Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()
    try:
        status, headers, body = call(f"/api/artifacts/{ids[str(report)]}/open")
        assert status == 200 and body.decode() == html and "allow-scripts" in headers["Content-Security-Policy"]
        assert headers["X-Interlatch-Source"] == "archive"
        local = json.loads(call("/api/artifacts")[2])["local"]
        status, _, _ = call(f"/api/artifacts/{ids[str(notes)]}/reveal", method="POST")
        assert (status, len(launched)) == ((200, 1) if local else (403, 0))
        serve = {"X-Forwarded-For": "100.64.0.2", "Tailscale-User-Login": "me@github"}
        status, _, _ = call(f"/api/artifacts/{ids[str(notes)]}/reveal", method="POST", headers=serve)
        assert status == 403 and len(launched) == (1 if local else 0)  # through Tailscale Serve: never
        assert json.loads(call("/api/artifacts", headers=serve)[2])["local"] is None
    finally:
        httpd.shutdown()


def test_decks_and_pdfs_a_script_saved(tmp_path, monkeypatch):
    """A deck or PDF a script saved has no Write call: it counts when the session names it and it was created during
    the session; a file the session only read existed before."""
    import time

    # pytest's temporary folder is in macOS's per-user temp area, which is otherwise ignored as app scratch space
    monkeypatch.setattr(artifacts, "_NOT_MADE_HERE", (os.path.expanduser("~/Library/"),))
    from datetime import datetime, timedelta, timezone

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    now = datetime.now(timezone.utc)
    out = tmp_path / "out"
    out.mkdir()
    deck, report, given = out / "plan.pptx", out / "summary.pdf", tmp_path / "brief.pdf"
    for f in (deck, report, given):
        f.write_bytes(b"%PDF or PK: not parsed")
    old = time.time() - 7200  # the brief was there two hours before the session started
    os.utime(given, (old, old))
    key = out / "server.key"
    key.write_bytes(b"stands in for a TLS private key")
    lock, cached = out / "~$plan.pptx", out / "pdf_cache" / ("ab" * 32 + ".pdf")
    batch = [out / "scans" / f"scan-{i}.pdf" for i in range(10)]  # a download loop, not ten deliverables
    for f in (lock, cached, *batch):
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(b"x")
    cmd = f"cd {out} && python make_deck.py --brief {given} && ls"
    result = f"Saved deck to {deck}\nwrote summary.pdf\nserver.key {lock} {cached}\n" + "\n".join(map(str, batch))
    lines = [
        _line(type="user", uuid="u0", timestamp=iso(now - timedelta(minutes=5)), message={"role": "user", "content": "make the deck"}),
        _line(type="assistant", uuid="a1", timestamp=iso(now - timedelta(minutes=4)), message={
            "id": "m1", "model": "claude-opus-5-5", "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": cmd}}]}),
        _line(type="user", uuid="u1", timestamp=iso(now + timedelta(seconds=5)), message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": result, "is_error": False}]}),
    ]
    p = tmp_path / f"{SID}.jsonl"
    p.write_text("\n".join(lines) + "\n")
    ps = parse_session(p)
    ps.project_path = str(out)  # "summary.pdf" is relative to where the session ran
    artifacts.finish(ps)
    by = {o["key"]: o for o in ps.outputs}
    assert by[f"file:{deck}"]["kind"] == "deck" and by[f"file:{deck}"]["action"] == "saved"
    assert by[f"file:{report}"]["kind"] == "doc" and by[f"file:{deck}"]["tool_use_id"] == "t1"
    assert f"file:{given}" not in by  # read, not made
    assert not any(o["path"] and o["path"].endswith(".key") for o in ps.outputs)  # a private key is never a "deck"
    assert artifacts.kind_for_path("/x/server.key") is None
    assert not {f"file:{lock}", f"file:{cached}", *(f"file:{b}" for b in batch)} & set(by)  # lock file, cache, batch


@pytest.mark.skipif(not artifacts.can_thumbnail(), reason="Quick Look thumbnails are a macOS feature")
def test_quick_look_draws_a_first_page(tmp_path):
    pdf = tmp_path / "one.pdf"
    pdf.write_bytes(b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    png = artifacts.quicklook_thumbnail(str(pdf), tmp_path / "thumbs", size=200)
    assert png and png.startswith(b"\x89PNG")
    assert artifacts.quicklook_thumbnail(str(pdf), tmp_path / "thumbs", size=200) == png  # kept until the file changes
    assert artifacts.quicklook_thumbnail(str(tmp_path / "missing.pdf"), tmp_path / "thumbs") is None
