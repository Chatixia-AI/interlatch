"""Hark: the chats the browser extension saves from hark.com (sources/hark.py, capture.js) and the Mac app's log
(runtime/hark_app.py)."""

from __future__ import annotations

import copy
import gzip
import json
import subprocess
from datetime import datetime, timezone

import pytest

from interlatch.ingestion.chat_import import ExportError, capture, import_status, reparse_archived
from interlatch.runtime import browser_extension, hark_app
from interlatch.sources.hark import parse_conversation
from interlatch.storage.sqlite import connect as db_connect

from test_browser_extension import NODE, needs_node

CHAT = "35e51392-a311-4a63-908a-ec8769cb4aa5"
T0 = 1795737389716  # 2026-11-27 morning, Tokyo


def _msg(i, role, content, at, **kw):
    return {"id": f"m{i}", "role": role, "content": content, "timestamp": T0 + at * 1000, "source": "web", "thought": None,
            "task_name": None, "brief": None, "jobStatus": None, "threadId": None, "toolCalls": [], **kw}


# one day of the main chat, as capture.js sends it
DAY = {
    "hark": 1, "id": f"{CHAT}_2026-11-27", "day": "2026-11-27", "updated": T0 + 400_000,
    "conversation": {"id": CHAT, "kind": "main", "title": None, "createdAt": "2026-11-26T10:10:45.076Z"},
    "messages": [
        _msg(1, "assistant", "Good morning. Two emails need you.", 0, task_name="checkin", brief="morning_brief"),
        _msg(2, "user", "Open a PR adding Interlatch to the awesome list", 60),
        _msg(3, "assistant", "On it.", 62, thought="The list is on GitHub; fork it first.",
             toolCalls=[{"toolName": "browser", "status": "completed", "input": {"task": "open a PR"},
                         "dispatchId": "d1", "dispatchedAt": T0 + 63_000, "updatedAt": T0 + 123_000}]),
        _msg(4, "assistant", "", 130, toolCalls=[{"toolName": "files.list", "status": "failed", "input": {"path": "~/Downloads"},
                                                   "dispatchId": "d2", "dispatchedAt": T0 + 131_000, "updatedAt": T0 + 132_000}]),
        _msg(5, "assistant", "The PR is open.", 400),
    ],
    "panels": [
        {"dispatchId": "d1", "kind": "browser", "status": "completed", "narrative": "Opening a pull request",
         "latestNarration": "Opened the pull request", "startedAt": T0 + 63_000,
         "phases": [{"title": "Fork the list", "status": "completed"}, {"title": "Open the PR", "status": "completed"}]},
        {"dispatchId": "d9", "kind": "browser", "status": "failed", "narrative": "Checking the weather site",
         "latestNarration": "The site did not load", "startedAt": T0 + 300_000, "phases": []},
    ],
}


def test_a_day_of_the_main_chat():
    ps = parse_conversation(copy.deepcopy(DAY))
    assert ps.id == f"{CHAT}_2026-11-27" and ps.project_path == "hark.com" and ps.entrypoint == "hark"
    assert ps.custom_title is None  # the main chat has no title: the session gets one from its prompts
    assert ps.n_prompts == 1 and ps.first_prompt == "Open a PR adding Interlatch to the awesome list"
    kinds = [(e.kind, e.text) for e in ps.events]
    assert ("text", "Good morning. Two emails need you.") in kinds  # a check-in no prompt asked for
    assert ("thinking", "The list is on GitHub; fork it first.") in kinds
    calls = {c.tool_use_id: c for c in ps.tool_calls}
    assert set(calls) == {"d1", "d2", "d9"}  # d9: work dispatched with no message naming it
    assert calls["d1"].name == "browser" and not calls["d1"].is_error and calls["d1"].duration_ms == 60_000
    assert calls["d2"].name == "files.list" and calls["d2"].is_error and calls["d2"].file_path == "~/Downloads"
    assert calls["d9"].is_error
    result = next(e.text for e in ps.events if e.kind == "tool_result" and e.tool_use_id == "d1")
    assert result == "Opened the pull request\n- Fork the list (completed)\n- Open the PR (completed)"
    assert ps.started_at == "2026-11-26T23:56:29.716Z" and ps.active_s > 0  # in UTC; the day is local time


def test_a_project_chat_is_named_after_its_project():
    day = {**copy.deepcopy(DAY), "conversation": {"id": "p1", "kind": "project", "title": "Chatixia Research"}}
    ps = parse_conversation(day)
    assert ps.project_path == "hark.com/Chatixia Research" and ps.custom_title == "Chatixia Research · 2026-11-27"


def test_a_day_with_nothing_said_is_no_session():
    assert parse_conversation({**DAY, "messages": []}) is None
    assert parse_conversation({**DAY, "messages": [_msg(1, "assistant", "", 0)], "panels": []}) is None


def test_capture_stores_each_day_and_again_when_it_changes(env):
    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    day = copy.deepcopy(DAY)
    assert capture(cfg, conn, "hark", day) == {"id": day["id"], "result": "new"}
    row = conn.execute("SELECT agent, source, project_path, analysis_status FROM sessions WHERE id = ?", (day["id"],)).fetchone()
    assert tuple(row) == ("hark", "hark-capture", "hark.com", "skipped")  # not analyzed automatically, as other web chats
    archived = cfg.archive_dir / "hark" / "captured" / day["id"] / "conversations.json.gz"
    assert json.loads(gzip.decompress(archived.read_bytes()))[0]["id"] == day["id"]

    assert capture(cfg, conn, "hark", day)["result"] == "unchanged"
    day["messages"].append(_msg(6, "user", "Thanks", 500))
    day["updated"] = T0 + 500_000
    assert capture(cfg, conn, "hark", day)["result"] == "updated"
    other = {**copy.deepcopy(DAY), "id": f"{CHAT}_2026-11-26", "day": "2026-11-26"}
    assert capture(cfg, conn, "hark", other)["result"] == "new"
    status = import_status(conn)["hark"]
    assert status["sessions"] == 2 and status["exportable"] is False and status["last_capture"]["captured"] == 3


def test_capture_refuses_what_is_not_a_hark_day(env):
    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    for key, conv in (("hark", {**DAY, "id": "../../escape"}), ("hark", {**DAY, "hark": None}),
                      ("chatgpt", DAY), ("hark", {"uuid": "x", "chat_messages": []})):
        with pytest.raises(ExportError):
            capture(cfg, conn, key, copy.deepcopy(conv))
    assert not (cfg.archive_dir / "hark").exists() and not (cfg.archive_dir / "escape").exists()


def test_reparse_finds_captured_hark_days(env):
    cfg = env["cfg"]
    conn = db_connect(cfg.db_path)
    capture(cfg, conn, "hark", copy.deepcopy(DAY))
    conn.execute("UPDATE sessions SET files_sig = 'old|v0' WHERE id = ?", (DAY["id"],))  # stored by an older parser
    conn.commit()
    assert reparse_archived(cfg, conn) == 1


# ------------------------------------------------------------------ the Mac app's log
LOG = """\
[2026-11-20][08:00:00][hark_desktop_lib::diagnostics][INFO] diagnostics: Hark 0.1.20 launched
[2026-11-20][08:00:01][hark_desktop_lib::device_tools::link][INFO] device link: connected as 908d95ed
[2026-11-27][00:52:05][hark_desktop_lib::diagnostics][INFO] diagnostics: Hark 0.1.24 launched
[2026-11-27][00:52:24][hark_desktop_lib::searchable][INFO] sign-in sheet: the google sign-in handed its code to the window to redeem in us, with no retry
[2026-11-27][00:52:40][hark_desktop_lib::device_tools::link][INFO] device link: connected as 908d95ed
[2026-11-27][00:53:00][hark_desktop_lib::device_tools::mcp][INFO] device link: the tool list changed to files.list, shell.run, location.get
[2026-11-27][00:58:08][hark_desktop_lib::push][WARN] push: the notification permission request failed
not a log line
[2026-11-27][01:10:00][hark_desktop_lib::device_tools::link][WARN] device link: the socket failed: connection reset
"""
NOW = datetime(2026, 11, 27, 2, 0, tzinfo=timezone.utc)


def test_the_log_says_when_it_ran_and_how_its_link_is():
    log = hark_app.read_log(LOG.splitlines(), now=NOW)
    assert log["last_launch"] == "2026-11-27T00:52:05Z" and log["launched_version"] == "0.1.24"
    assert log["launches_7d"] == 2 and log["signed_in"] == "2026-11-27T00:52:24Z"
    assert log["tools"] == "files.list, shell.run, location.get"
    assert log["device_link"] == {"state": "trouble", "at": "2026-11-27T01:10:00Z", "detail": "the socket failed: connection reset"}
    assert [p["at"] for p in log["problems"]] == ["2026-11-27T01:10:00Z", "2026-11-27T00:58:08Z"]  # newest first


def test_a_new_launch_starts_without_a_link():
    lines = LOG.splitlines()[:2] + [LOG.splitlines()[2]]
    assert hark_app.read_log(lines, now=NOW)["device_link"] is None
    assert hark_app.read_log(LOG.splitlines()[:5], now=NOW)["device_link"] == {"state": "connected", "at": "2026-11-27T00:52:40Z"}


def test_only_the_end_of_a_long_log_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(hark_app, "TAIL_BYTES", 200)
    log = tmp_path / "shell.log"
    log.write_text(LOG)
    lines = hark_app._tail(log)
    assert lines[-1].endswith("connection reset") and len("\n".join(lines)) <= 200
    assert all(line.startswith("[") or line == "not a log line" for line in lines)  # the cut first line is dropped


def test_no_app_no_status(tmp_path, monkeypatch):
    monkeypatch.setattr(hark_app, "app_paths", lambda: [tmp_path / "Hark.app"])
    assert hark_app.status() == {"installed": False}


def test_the_app_is_read_from_its_bundle(tmp_path, monkeypatch):
    import plistlib

    app = tmp_path / "Hark.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "0.1.24"}))
    log = tmp_path / "shell.log"
    log.write_text(LOG)
    monkeypatch.setattr(hark_app.sys, "platform", "darwin")
    monkeypatch.setattr(hark_app, "app_paths", lambda: [app])
    monkeypatch.setattr(hark_app, "log_path", lambda: log)
    monkeypatch.setattr(hark_app, "_running", lambda: True)
    st = hark_app.status()
    assert st["installed"] and st["version"] == "0.1.24" and st["running"] and st["last_launch"] == "2026-11-27T00:52:05Z"


# ------------------------------------------------------------------ capture.js on hark.com
HARK_TAB = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const store = { consented: true }, sent = [], fetched = [], timers = [];
const DAY = 86400e3, now = Date.now();
const msg = (id, role, at, extra = {}) => ({ id, role, content: 'text ' + id, timestamp: at, source: 'web', ...extra });
// two pages of the main chat, newest first: today, yesterday, and one from before the first sync's 30 days
const pages = [
  { messages: { a: msg('a', 'user', now - 60e3), b: msg('b', 'assistant', now - 30e3, { toolCalls: [{ toolName: 'browser',
      status: 'waiting_for_input', input: {}, dispatchId: 'd1', dispatchedAt: now - 29e3, updatedAt: now - 20e3, secretField: 'x' }] }) },
    windows: { w: { at: now - 29e3, panels: [{ dispatchId: 'd1', kind: 'browser', status: 'waiting_for_input', narrative: 'Signing in',
      screenshot: { downloadUrl: 'https://shot' }, liveViewUrl: 'https://live', userInputRequest: { screen: { secret: { login_methods: ['x'] } } } }] } },
    asks: { k: { request: { screen: { secret: { login_methods: ['password'] } } } } },
    older: { hasMore: true, cursor: { id: 'a', createdAt: now - 60e3 } } },
  { messages: { c: msg('c', 'user', now - DAY - 60e3), d: msg('d', 'assistant', now - DAY), z: msg('z', 'user', now - 40 * DAY) },
    older: { hasMore: true, cursor: { id: 'z', createdAt: now - 40 * DAY } } },
];
const chrome = {
  i18n: { getMessage: (k) => k },
  storage: { local: {
    get: async (k) => Object.fromEntries([k].flat().filter((n) => n in store).map((n) => [n, store[n]])),
    set: async (v) => { Object.assign(store, v); }, remove: async (k) => { delete store[k]; },
  }, onChanged: { addListener() {} } },
  runtime: { sendMessage: async (m) => {
    if (m.type === 'reachable') return true;
    if (m.type !== 'capture') return undefined;
    sent.push(m);
    return { ok: true, result: 'new' };
  } },
};
const fetch = async (path) => {
  fetched.push(path);
  const json = path === '/api/conversations'
    ? { conversations: [{ id: 'main1', kind: 'main', title: null, activityAt: new Date(now - 20e3).toISOString() },
                        { id: 'old1', kind: 'project', title: 'Old', activityAt: new Date(now - 50 * DAY).toISOString() }] }
    : path.includes('before=a') ? pages[1] : pages[0];
  return { ok: true, status: 200, json: async () => json };
};
const doc = { visibilityState: 'hidden', cookie: '', addEventListener() {}, removeEventListener() {}, documentElement: {} };
const context = vm.createContext({ chrome, fetch, Date, Promise, console, Object, Number, String, Set, Map, Math, Array, isNaN, Error,
  encodeURIComponent, decodeURIComponent, location: { hostname: 'hark.com', pathname: '/chat' }, window: { addEventListener() {} },
  interlatchOverlay: { say() {}, clear() {}, show() {} }, document: doc,
  setTimeout: (f, ms) => { timers.push([f, ms]); return timers.length; }, clearTimeout() {}, setInterval() {} });
vm.runInContext(fs.readFileSync(process.argv[1] + '/capture.js', 'utf8'), context);
(async () => {
  timers.find(([, ms]) => ms === 3000)[0]();
  for (let i = 0; i < 300 && store['sync:hark']?.running !== null; i++) {
    for (const [f, ms] of timers.splice(0)) if (ms < 1000) f();
    await new Promise((r) => setImmediate(r));
  }
  const pad = (n) => String(n).padStart(2, '0');
  const dayOf = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  const got = JSON.parse(JSON.stringify(sent)); // out of the script's realm: deepEqual compares prototypes
  assert.deepEqual(got.map((m) => m.format), ['hark', 'hark']);
  assert.deepEqual(got.map((m) => m.conversation.id), [`main1_${dayOf(now)}`, `main1_${dayOf(now - DAY)}`]); // newest day first
  const [today, yesterday] = got.map((m) => m.conversation);
  assert.deepEqual(today.messages.map((m) => m.id), ['a', 'b']);
  assert.deepEqual(yesterday.messages.map((m) => m.id), ['c', 'd']); // not z, from before the 30 days
  assert.equal(today.hark, 1);
  assert.equal(today.panels[0].narrative, 'Signing in');
  assert.equal(today.messages[1].toolCalls[0].toolName, 'browser');
  const json = JSON.stringify(sent);
  for (const leak of ['secret', 'login_methods', 'liveViewUrl', 'https://live', 'https://shot', 'asks', 'userInputRequest']) {
    assert.ok(!json.includes(leak), leak);
  }
  assert.ok(!fetched.some((p) => p.includes('old1')), 'a chat unchanged since the sync window is not read');
  assert.equal(fetched.filter((p) => p.startsWith('/api/messages/history')).length, 2); // stops at the page that goes back too far
  assert.equal(store['sync:hark'].error, null);
})().catch((e) => { console.error(e); process.exit(1); });
'''


@needs_node
def test_capture_js_sends_hark_one_day_at_a_time_and_nothing_secret():
    result = subprocess.run([NODE, "-e", HARK_TAB, str(browser_extension.BUNDLED)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
