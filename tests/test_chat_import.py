"""Importing claude.ai and ChatGPT data exports."""

import copy
import gzip
import json
import threading
import time
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from interlatch.ingestion.chat_import import (ExportError, import_export, import_status, not_analyzed_reason, read_export,
                                              summary)
from interlatch.sources.chats import CHATGPT, CLAUDE_AI
from interlatch.sources.chatgpt import parse_conversation as parse_chatgpt
from interlatch.sources.claude.export import parse_conversation
from interlatch.storage.sqlite import connect as db_connect

PROJECT = "11111111-1111-1111-1111-111111111111"
CHAT = "22222222-2222-2222-2222-222222222222"
EMPTY = "33333333-3333-3333-3333-333333333333"
LOOSE = "44444444-4444-4444-4444-444444444444"

CONVERSATIONS = [
    {
        "uuid": CHAT, "name": "Rate limiter design", "summary": "", "project_uuid": PROJECT,
        "created_at": "2026-05-01T09:00:00.000000Z", "updated_at": "2026-05-01T09:05:00.000000Z",
        "account": {"uuid": "acct"},
        "chat_messages": [
            {"uuid": "m1", "sender": "human", "created_at": "2026-05-01T09:00:00.000000Z", "text": "How should I rate limit my API?",
             "content": [{"type": "text", "text": "How should I rate limit my API?", "start_timestamp": "2026-05-01T09:00:00Z"}],
             "attachments": [{"file_name": "api.py", "file_size": 120, "file_type": "text/x-python", "extracted_content": "def handler(): ..."}],
             "files": [{"file_name": "diagram.png"}]},
            {"uuid": "m2", "sender": "assistant", "created_at": "2026-05-01T09:01:00.000000Z", "text": "",
             "content": [
                 {"type": "thinking", "thinking": "Token bucket fits a bursty API.", "start_timestamp": "2026-05-01T09:01:00Z"},
                 {"type": "text", "text": "Use a token bucket per API key.", "start_timestamp": "2026-05-01T09:01:05Z"},
                 {"type": "tool_use", "name": "artifacts", "input": {"id": "limiter", "type": "application/vnd.ant.code",
                                                                      "title": "limiter.py", "command": "create"}},
                 {"type": "tool_result", "name": "artifacts", "content": [{"type": "text", "text": "OK"}], "is_error": False},
             ]},
            {"uuid": "m3", "sender": "human", "created_at": "2026-05-01T09:04:00.000000Z", "text": "And across instances?",
             "content": [{"type": "text", "text": "And across instances?"}]},
            {"uuid": "m4", "sender": "assistant", "created_at": "2026-05-01T09:05:00.000000Z", "text": "Keep the buckets in Redis.",
             "content": [{"type": "text", "text": "Keep the buckets in Redis."}]},
        ],
    },
    {"uuid": EMPTY, "name": "", "created_at": "2026-05-02T10:00:00Z", "updated_at": "2026-05-02T10:00:00Z", "chat_messages": []},
    {"uuid": LOOSE, "name": "Quick question", "created_at": "2026-05-03T10:00:00Z", "updated_at": "2026-05-03T10:01:00Z",
     "chat_messages": [  # an older export: text only, no content blocks
         {"uuid": "n1", "sender": "human", "created_at": "2026-05-03T10:00:00Z", "text": "What does EXPLAIN ANALYZE do?"},
         {"uuid": "n2", "sender": "assistant", "created_at": "2026-05-03T10:01:00Z", "text": "It runs the query and times each step."}]},
]
PROJECTS = [{"uuid": PROJECT, "name": "Backend", "description": "", "docs": []}]


def write_export(path, conversations=CONVERSATIONS):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("conversations.json", json.dumps(conversations))
        z.writestr("projects.json", json.dumps(PROJECTS))
        z.writestr("users.json", json.dumps([{"uuid": "u", "full_name": "Ada", "email_address": "ada@example.com"}]))
    return path


def test_parse_conversation():
    ps = parse_conversation(CONVERSATIONS[0], {PROJECT: "Backend"})
    assert ps.id == CHAT and ps.custom_title == "Rate limiter design" and ps.project_path == "claude.ai/Backend"
    assert ps.n_prompts == 2 and ps.first_prompt.startswith("How should I rate limit my API?")
    assert "[attached api.py, 18 chars]" in ps.first_prompt and "[file diagram.png]" in ps.first_prompt and ps.n_images == 1
    assert [t.name for t in ps.tool_calls] == ["artifacts"]
    kinds = [e.kind for e in ps.events]
    assert kinds == ["prompt", "thinking", "text", "tool_use", "tool_result", "prompt", "text"]
    assert ps.started_at == "2026-05-01T09:00:00.000Z" and ps.ended_at == "2026-05-01T09:05:00.000Z"
    assert parse_conversation(CONVERSATIONS[1], {}) is None
    loose = parse_conversation(CONVERSATIONS[2], {})
    assert loose.project_path == "claude.ai" and loose.n_prompts == 1 and loose.events[1].text.startswith("It runs")


def test_detects_the_format(tmp_path):
    assert read_export(write_export(tmp_path / "claude.zip")).format is CLAUDE_AI
    assert read_export(write_chatgpt_export(tmp_path / "chatgpt.zip")).format is CHATGPT
    (tmp_path / "conversations.json").write_text(json.dumps([{"foo": 1}]))
    with pytest.raises(ExportError, match="neither"):
        read_export(tmp_path / "conversations.json")
    with pytest.raises(ExportError, match="not a chat export"):
        read_export(Path("/etc/hosts"))


def test_import_and_reimport(env):
    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    counts = import_export(cfg, conn, write_export(env["tmp"] / "data-2026-05-04.zip"))
    assert counts == {"format": "claude.ai", "conversations": 3, "new": 2, "updated": 0, "unchanged": 0, "empty": 1, "excluded": 0}
    row = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (CHAT,)).fetchone())
    assert row["agent"] == "claude-ai" and row["source"] == "claude-ai-export" and row["project_name"] == "Backend"
    assert row["analysis_status"] == "skipped" and row["analysis_reason"] == not_analyzed_reason(CLAUDE_AI)
    archived = list((cfg.archive_dir / "claude-ai").glob("*/*"))
    assert sorted(p.name for p in archived) == ["conversations.json.gz", "projects.json.gz"]  # users.json is not kept
    with gzip.open(archived[0].parent / "conversations.json.gz") as f:
        assert len(json.load(f)) == 3
    hits = conn.execute("SELECT COUNT(*) FROM events WHERE session_id = ? AND text LIKE '%token bucket%'", (CHAT,)).fetchone()[0]
    assert hits

    again = import_export(cfg, conn, env["tmp"] / "data-2026-05-04.zip")
    assert again["unchanged"] == 2 and again["empty"] == 1 and again["new"] == again["updated"] == 0
    newer = copy.deepcopy(CONVERSATIONS)
    newer[2]["chat_messages"] += [{"uuid": "n3", "sender": "human", "created_at": "2026-05-05T08:00:00Z", "text": "Thanks!"}]
    newer[2]["updated_at"] = "2026-05-05T08:00:00Z"
    counts = import_export(cfg, conn, write_export(env["tmp"] / "data-2026-05-06.zip", newer), analyze=True)
    assert counts["updated"] == 1 and counts["unchanged"] == 1
    assert conn.execute("SELECT n_prompts FROM sessions WHERE id = ?", (LOOSE,)).fetchone()[0] == 2
    status = import_status(conn)["claude-ai"]
    assert status["sessions"] == 2 and status["last_import"]["file"] == "data-2026-05-06.zip"


def test_import_from_dashboard_upload(env):
    from interlatch.interfaces.web.app import App
    from interlatch.interfaces.web.handler import make_handler

    app = App(env["cfg"])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    body = write_export(env["tmp"] / "export.zip").read_bytes()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/import", data=body, method="POST",
                                     headers={"X-Interlatch": "1", "Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert json.loads(r.read())["started"]
        for _ in range(100):
            job = app.jobs.snapshot().get("import", {})
            if job.get("state") != "running":
                break
            time.sleep(0.05)
        assert job["state"] == "done" and "2 new" in job["result"]
        assert not list((env["cfg"].home / "imports").glob("*"))  # the upload is deleted after the import
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/imports", timeout=10) as r:
            assert json.loads(r.read())["claude-ai"]["sessions"] == 2
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ ChatGPT
def _node(nid, parent, children, role=None, content=None, t=None, **extra):
    msg = None if role is None else {"id": nid, "author": {"role": role, **({"name": extra.pop("name")} if "name" in extra else {})},
                                     "create_time": t, "content": content, "recipient": extra.pop("recipient", "all"),
                                     "metadata": extra.pop("metadata", {})}
    return nid, {"id": nid, "message": msg, "parent": parent, "children": children}


GPT = {
    "title": "Plot sine", "create_time": 1746090000.0, "update_time": 1746090100.0, "conversation_id": "gpt-1", "id": "gpt-1",
    "current_node": "a2", "default_model_slug": "gpt-5",
    "mapping": dict([
        _node("root", None, ["sys"]),
        _node("sys", "root", ["u1"], "system", {"content_type": "text", "parts": [""]},
              metadata={"is_visually_hidden_from_conversation": True}),
        _node("u1", "sys", ["a0", "a1"], "user", {"content_type": "multimodal_text", "parts": [
            {"content_type": "image_asset_pointer", "asset_pointer": "file-service://file-1"}, "Plot sin(x) from this data"]},
            1746090000.0, metadata={"attachments": [{"name": "data.csv"}]}),
        _node("a0", "u1", [], "assistant", {"content_type": "text", "parts": ["An answer that was regenerated away"]}, 1746090010.0),
        _node("a1", "u1", ["c1"], "assistant", {"content_type": "thoughts", "thoughts": [{"summary": "Plan", "content": "Use matplotlib."}]},
              1746090020.0),
        _node("c1", "a1", ["t1"], "assistant", {"content_type": "code", "language": "python", "text": "import numpy as np"},
              1746090030.0, recipient="python"),
        _node("t1", "c1", ["a2"], "tool", {"content_type": "execution_output", "text": "<Figure size 640x480>"}, 1746090040.0,
              name="python", metadata={"aggregate_result": {"status": "success"}}),
        _node("a2", "t1", [], "assistant", {"content_type": "text", "parts": ["Here is the plot."]}, 1746090100.0,
              metadata={"model_slug": "gpt-5"}),
    ]),
}


def write_chatgpt_export(path, conversations=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("conversations.json", json.dumps(conversations or [GPT]))
        z.writestr("user.json", json.dumps({"id": "user-1", "email": "ada@example.com"}))
        z.writestr("chat.html", "<html></html>")
    return path


def test_parse_chatgpt_follows_the_shown_branch():
    ps = parse_chatgpt(GPT)
    assert ps.id == "gpt-1" and ps.custom_title == "Plot sine" and ps.project_path == "chatgpt.com"
    assert [e.kind for e in ps.events] == ["prompt", "thinking", "tool_use", "tool_result", "text"]
    assert "regenerated" not in " ".join(e.text for e in ps.events)
    assert ps.first_prompt.startswith("Plot sin(x) from this data") and "[attached data.csv]" in ps.first_prompt
    assert "[1 image]" in ps.first_prompt and ps.n_images == 1
    assert [t.name for t in ps.tool_calls] == ["python"] and not ps.tool_calls[0].is_error
    assert ps.primary_model == "gpt-5" and ps.started_at.startswith("2025-05-01T09:00:00")


def test_import_chatgpt_export(env):
    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    counts = import_export(cfg, conn, write_chatgpt_export(env["tmp"] / "chatgpt.zip"))
    assert counts["format"] == "ChatGPT" and counts["new"] == 1
    row = dict(conn.execute("SELECT * FROM sessions WHERE id = 'gpt-1'").fetchone())
    assert row["agent"] == "chatgpt" and row["source"] == "chatgpt-export" and row["project_name"] == "chatgpt.com"
    assert row["analysis_reason"] == not_analyzed_reason(CHATGPT)
    assert [p.name for p in (cfg.archive_dir / "chatgpt").glob("*/*")] == ["conversations.json.gz"]  # not user.json or chat.html
    assert import_export(cfg, conn, env["tmp"] / "chatgpt.zip")["unchanged"] == 1
    assert import_status(conn)["chatgpt"]["sessions"] == 1


def test_cut_off_download_keeps_the_chats_before_the_cut(env):
    """A large ChatGPT export puts its conversations first and gigabytes of attachments after them, so an interrupted
    download lacks only the zip's end (its directory), which `zipfile` cannot do without."""
    full = env["tmp"] / "full.zip"
    second = {**GPT, "conversation_id": "gpt-2", "id": "gpt-2", "title": "Second"}
    with zipfile.ZipFile(full, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("conversations-000.json", json.dumps([GPT]))
        z.writestr("conversations-001.json", json.dumps([second]))
        z.writestr("file_0001.dat", bytes(range(256)) * 400, compress_type=zipfile.ZIP_STORED)
        z.writestr("user.json", json.dumps({"email": "ada@example.com"}))
    raw = full.read_bytes()
    cut = env["tmp"] / "cut.zip"
    cut.write_bytes(raw[:raw.index(b"file_0001.dat") + 50_000])
    assert not zipfile.is_zipfile(cut)

    export = read_export(cut)
    assert export.format is CHATGPT and len(export.conversations) == 2
    assert "cut off" in export.note and "file_0001.dat" in export.note
    counts = import_export(env["cfg"], db_connect(env["cfg"].db_path), cut)
    assert counts["new"] == 2 and "incomplete download" in summary(counts)

    inside = env["tmp"] / "inside.zip"
    inside.write_bytes(raw[:raw.index(b"conversations-001.json") + 40])
    with pytest.raises(ExportError, match="incomplete download.*inside conversations-001.json.*Download the export again"):
        read_export(inside, "chatgpt-export.zip")


def test_claude_keeps_the_branch_shown():
    msg = lambda uid, parent, sender, text, t: {"uuid": uid, "parent_message_uuid": parent, "sender": sender, "text": text,
                                                 "created_at": f"2026-06-01T10:0{t}:00Z", "content": [{"type": "text", "text": text}]}
    conv = {"uuid": "fork", "name": "Forked", "created_at": "2026-06-01T10:00:00Z", "updated_at": "2026-06-01T10:05:00Z",
            "chat_messages": [
                msg("h1", None, "human", "Name a colour", 0), msg("a1", "h1", "assistant", "Red", 1),
                msg("h2", "a1", "human", "Another one", 2), msg("a2", "h2", "assistant", "Blue (abandoned retry)", 3),
                msg("a3", "h2", "assistant", "Green", 4)]}
    conv["chat_messages"][0]["content"].append({"type": "image", "file_uuid": "img-1"})
    ps = parse_conversation(conv, {})
    texts = [e.text for e in ps.events]
    assert "Green" in texts and not any("abandoned" in t for t in texts) and ps.n_prompts == 2
    assert ps.n_images == 1 and "[1 image]" in ps.first_prompt


def test_projects_zip_is_explained(tmp_path):
    path = tmp_path / "projects-000.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("projects/0001.json", json.dumps({"uuid": "0001", "name": "Chatixia"}))
    with pytest.raises(ExportError, match="projects, not chats"):
        read_export(path, "projects-000.zip")


# ------------------------------------------------------------------ the browser extension (capture)
def _status(conn, sid):
    return conn.execute("SELECT analysis_status, analysis_reason, project_path, source FROM sessions WHERE id = ?", (sid,)).fetchone()


def test_capture_stores_a_chat_once_and_again_when_it_changes(env):
    from interlatch.ingestion.chat_import import capture

    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    chat = copy.deepcopy(CONVERSATIONS[0])
    chat["project"] = {"uuid": PROJECT, "name": "Backend"}  # the extension copies it from the site's list
    assert capture(cfg, conn, "claude-ai", chat) == {"id": CHAT, "result": "new"}
    status, reason, project, source = _status(conn, CHAT)
    assert (status, project, source) == ("skipped", "claude.ai/Backend", "claude-ai-export")
    assert reason.startswith("captured claude.ai chat: not analyzed automatically")
    archived = cfg.archive_dir / "claude-ai" / "captured" / CHAT / "conversations.json.gz"
    assert json.loads(gzip.decompress(archived.read_bytes()))[0]["uuid"] == CHAT

    assert capture(cfg, conn, "claude-ai", chat)["result"] == "unchanged"
    chat["chat_messages"].append({"uuid": "m5", "sender": "human", "created_at": "2026-05-01T09:06:00Z", "text": "Thanks"})
    chat["updated_at"] = "2026-05-01T09:06:00.000000Z"
    assert capture(cfg, conn, "claude-ai", chat)["result"] == "updated"
    assert len(json.loads(gzip.decompress(archived.read_bytes()))[0]["chat_messages"]) == 5  # the latest copy
    status = import_status(conn)["claude-ai"]
    assert status["sessions"] == 1 and status["last_capture"]["captured"] == 2


def test_capture_and_export_are_the_same_session(env):
    from interlatch.ingestion.chat_import import capture

    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    capture(cfg, conn, "claude-ai", copy.deepcopy(CONVERSATIONS[0]))
    counts = import_export(cfg, conn, write_export(env["tmp"] / "export.zip"))
    assert counts["unchanged"] == 1 and counts["new"] == 1  # the captured chat; the other one (the third is empty)
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE source = 'claude-ai-export'").fetchone()[0] == 2


def test_capture_refuses_what_is_not_a_chat(env):
    from interlatch.ingestion.chat_import import capture

    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    for key, conv in (("claude-ai", {**CONVERSATIONS[0], "uuid": "../../escape"}), ("claude-ai", GPT),
                      ("chatgpt", CONVERSATIONS[0]), ("gemini", CONVERSATIONS[0]), ("claude-ai", ["not", "a", "chat"])):
        with pytest.raises(ExportError):
            capture(cfg, conn, key, conv)
    assert not (cfg.archive_dir / "claude-ai").exists() and not (cfg.archive_dir / "escape").exists()


def test_capture_waits_for_a_sync_then_asks_to_retry(env, monkeypatch):
    from interlatch.core.util import file_lock
    from interlatch.ingestion import chat_import

    cfg = env["cfg"]
    monkeypatch.setattr(chat_import, "CAPTURE_WAIT", 0.2)
    with file_lock(cfg.locks_dir / "ingest.lock", timeout=1) as got:
        assert got
        with pytest.raises(chat_import.CaptureBusy):
            chat_import.capture(cfg, db_connect(cfg.db_path), "claude-ai", copy.deepcopy(CONVERSATIONS[0]))


def test_capture_chatgpt_chat_in_a_project(env):
    from interlatch.ingestion.chat_import import capture

    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    chat = {**copy.deepcopy(GPT), "project": {"id": "g-p-1", "name": "Plots"}}
    assert capture(cfg, conn, "chatgpt", chat)["result"] == "new"
    assert _status(conn, "gpt-1")[2:] == ("chatgpt.com/Plots", "chatgpt-export")


def test_reparse_finds_captured_chats(env):
    from interlatch.ingestion.chat_import import capture, reparse_archived

    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    capture(cfg, conn, "claude-ai", copy.deepcopy(CONVERSATIONS[0]))
    conn.execute("UPDATE sessions SET files_sig = 'old|v1' WHERE id = ?", (CHAT,))  # stored by an older parser
    conn.commit()
    assert reparse_archived(cfg, conn) == 1
    assert conn.execute("SELECT files_sig FROM sessions WHERE id = ?", (CHAT,)).fetchone()[0].endswith("|v3")


def test_claude_follows_the_leaf_the_site_shows():
    msg = lambda uid, parent, sender, text, t: {"uuid": uid, "parent_message_uuid": parent, "sender": sender, "text": text,
                                                 "created_at": f"2026-06-01T10:0{t}:00Z", "content": [{"type": "text", "text": text}]}
    conv = {"uuid": "fork", "name": "Forked", "created_at": "2026-06-01T10:00:00Z", "updated_at": "2026-06-01T10:05:00Z",
            "current_leaf_message_uuid": "a2",  # went back to the older answer
            "chat_messages": [msg("h1", None, "human", "Name a colour", 0), msg("a2", "h1", "assistant", "Blue", 1),
                              msg("a3", "h1", "assistant", "Green", 2)]}
    texts = [e.text for e in parse_conversation(conv, {}).events]
    assert "Blue" in texts and "Green" not in texts


def test_capture_from_the_browser_extension(env):
    from interlatch.interfaces.web.app import App
    from interlatch.interfaces.web.handler import make_handler

    app = App(env["cfg"])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def post(body, **headers):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/capture", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        chat = {"format": "claude-ai", "conversation": CONVERSATIONS[0]}
        # a web page can't send the header; the extension's service worker does, from this computer
        assert post(chat, Origin="chrome-extension://abc")[0] == 403
        assert post(chat, **{"X-Interlatch": "1", "Origin": "chrome-extension://abc"}) == (200, {"id": CHAT, "result": "new"})
        assert post({"format": "claude-ai", "conversation": {}}, **{"X-Interlatch": "1"})[0] == 400
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/imports", timeout=10) as r:
            status = json.loads(r.read())["claude-ai"]
        assert status["last_capture"]["captured"] == 1
        assert Path(status["extension_dir"], "manifest.json").is_file()  # copied to a folder that stays put
    finally:
        httpd.shutdown()
