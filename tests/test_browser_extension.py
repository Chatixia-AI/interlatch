"""The browser extension's files: the folder Chrome loads stays put and follows the installed version's files."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import types

import pytest

from interlatch.runtime import browser_extension

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="Node is needed to run the extension's scripts")

# a tab of claude.ai as overlay.js and capture.js see it: `store` is chrome.storage.local, `shown()` the page message
TAB = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const el = () => {
  const e = { style: { setProperty() {} }, kids: [], classList: { add() {}, remove() {} }, on: {},
    set textContent(v) { this.text = v; }, append(...k) { this.kids.push(...k); }, addEventListener(n, f) { this.on[n] = f; },
    remove() { page.kids = page.kids.filter((x) => x !== e); }, attachShadow: () => e };
  return e;
};
const page = el(), timers = [], store = { consented: true }, sent = [], listeners = [];
const STATE = { signedIn: true, dashboard: true };
const doc = { visibilityState: 'visible', cookie: '', createElement: el, documentElement: page, seen: [],
  addEventListener(n, f) { this.seen.push(f); }, removeEventListener() {} };
const text = (e) => [e.text, ...e.kids.map(text)].filter(Boolean).join(' | ');
const find = (e, label) => e.text === label ? e : e.kids.map((k) => find(k, label)).find(Boolean);
const chrome = {
  i18n: { getMessage: (k) => k },
  storage: { local: {
    get: async (k) => Object.fromEntries([k].flat().filter((n) => n in store).map((n) => [n, store[n]])),
    set: async (v) => { Object.assign(store, v); },
    remove: async (k) => { delete store[k]; },
  }, onChanged: { addListener: (f) => listeners.push(f) } },
  runtime: { sendMessage: async (m) => {
    if (m.type === 'reachable') return STATE.dashboard;
    if (m.type !== 'capture') return undefined;
    sent.push(m.conversation.uuid);
    if (sent.length === STATE.pauseAfter) store.paused = true;
    return { ok: true, result: 'new' };
  } },
};
const updated = new Date().toISOString();
const fetch = async (path) => {
  const json = path === '/api/organizations' ? [{ uuid: 'o' }]
    : path.includes('chat_conversations?') ? (path.includes('offset=0') ? [1, 2, 3, 4, 5].map((i) => ({ uuid: 'c' + i, updated_at: updated })) : [])
    : { uuid: path.split('?')[0].split('/').pop() };
  return { ok: path !== '/api/organizations' || STATE.signedIn, status: 403, json: async () => json };
};
const context = vm.createContext({ chrome, fetch, Date, Promise, console, Object, Number, String, Set, isNaN, Error,
  encodeURIComponent, decodeURIComponent, location: { hostname: 'claude.ai', pathname: '/' }, window: { addEventListener() {} },
  CSSStyleSheet: class { replaceSync() {} }, matchMedia: () => ({ matches: true }), document: doc,
  setTimeout: (f, ms) => { timers.push([f, ms]); return timers.length; }, clearTimeout() {}, setInterval() {} });
const load = (name) => vm.runInContext(fs.readFileSync(process.argv[1] + '/' + name, 'utf8'), context);
const settle = async () => { for (let i = 0; i < 30; i++) await new Promise((r) => setImmediate(r)); };
const shown = async () => { await settle(); return text(page); };
const syncNow = async () => { // the sync a tab starts when it loads, with the short waits between chats run at once
  timers.find(([, ms]) => ms === 3000)[0]();
  for (let i = 0; i < 200 && store['sync:claude-ai']?.running !== null; i++) {
    for (const [f, ms] of timers.splice(0)) if (ms < 1000) f();
    await new Promise((r) => setImmediate(r));
  }
};
load('overlay.js');
'''


def in_a_tab(script):
    result = subprocess.run([NODE, "-e", TAB + f"(async () => {{ {script} }})().catch((e) => {{ console.error(e); process.exit(1); }});",
                             str(browser_extension.BUNDLED)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_folder_follows_the_bundled_files(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "manifest.json").write_text('{"version": "1.0.0"}')
    (bundled / "capture.js").write_text("one")
    monkeypatch.setattr(browser_extension, "BUNDLED", bundled)
    cfg = types.SimpleNamespace(home=tmp_path / "home")

    dst = browser_extension.folder(cfg)
    assert dst == cfg.home / "browser-extension" and (dst / "capture.js").read_text() == "one"
    (bundled / "capture.js").write_text("two")  # a newer Interlatch
    (bundled / "capture.js.map").write_text("")
    (dst / "old.js").write_text("gone in this version")
    browser_extension.folder(cfg)
    assert (dst / "capture.js").read_text() == "two" and (dst / "capture.js.map").exists() and not (dst / "old.js").exists()


def test_no_folder_without_an_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_extension, "BUNDLED", tmp_path / "missing")
    assert browser_extension.folder(types.SimpleNamespace(home=tmp_path)) is None


def test_the_shipped_manifest():
    manifest = json.loads((browser_extension.BUNDLED / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    # it reads only the two sites, and sends only to this computer's dashboard
    assert manifest["host_permissions"] == ["http://127.0.0.1/*"] and manifest["permissions"] == ["storage"]
    assert manifest["content_scripts"][0]["matches"] == ["https://claude.ai/*", "https://chatgpt.com/*"]
    assert manifest["content_scripts"][0]["js"] == ["overlay.js", "capture.js"]  # capture.js calls the overlay
    # the toolbar icon: a pause sign by default, and background.js puts the colour one on a tab of one of the sites
    assert set(manifest["action"]["default_icon"].values()).isdisjoint(manifest["icons"].values())
    background = (browser_extension.BUNDLED / "background.js").read_text(encoding="utf-8")
    icons = set(re.findall(r'"(icon[\w-]*\.png)"', background))
    assert icons == {*manifest["action"]["default_icon"].values(), "icon-16.png", "icon-32.png"}
    assert all((browser_extension.BUNDLED / f).is_file() for f in icons)
    for f in ("background.js", "overlay.js", "capture.js", "popup.html", "popup.js", "popup.css", "welcome.html", "welcome.js",
              "welcome.css", "plex-sans-400.woff2",
              "plex-sans-600.woff2", "plex-mono-400.woff2", f"_locales/{manifest['default_locale']}/messages.json",
              *manifest["icons"].values(), *manifest["action"]["default_icon"].values()):
        assert (browser_extension.BUNDLED / f).is_file(), f


def _messages(lang):
    return json.loads((browser_extension.BUNDLED / "_locales" / lang / "messages.json").read_text(encoding="utf-8"))


def test_both_languages_say_the_same_things():
    en, ja = _messages("en"), _messages("ja")
    assert en.keys() == ja.keys()
    subs = lambda m: sorted(re.findall(r"\$\d", m["message"]))  # noqa: E731
    assert not [k for k in en if subs(en[k]) != subs(ja[k])]
    for messages in (en, ja):  # the Chrome Web Store's limits
        assert len(messages["name"]["message"]) <= 75 and len(messages["description"]["message"]) <= 132


def test_every_word_the_extension_uses_has_a_message():
    root = browser_extension.BUNDLED
    used = set()
    for page in ("popup.html", "welcome.html"):
        used |= set(re.findall(r'data-i18n(?:-title|-label)?="(\w+)"', (root / page).read_text(encoding="utf-8")))
    for script in ("popup.js", "welcome.js", "background.js", "capture.js"):
        source = (root / script).read_text(encoding="utf-8")
        used |= set(re.findall(r'\bt\(\s*"(\w+)"', source))
        for pair in re.findall(r'\bt\([^"]*\?\s*"(\w+)"\s*:\s*"(\w+)"', source):
            used |= set(pair)
    popup = (root / "popup.js").read_text(encoding="utf-8")
    for table in re.findall(r"const (?:TITLES|DETAILS) = \{(.*?)\};", popup, re.S):
        used |= set(re.findall(r':\s*"(\w+)"', table))
    assert used >= {"stInstall", "stStopped", "stSaving", "siteSlowed", "siteNotYetOff", "errNotRunning", "ovSavedMany", "wlStart", "ovNoticePaused"}  # the search found them
    assert not used - _messages("en").keys()


@needs_node
def test_nothing_is_read_or_said_before_start_saving():
    in_a_tab("""
    delete store.consented;
    load('capture.js');
    await syncNow();
    assert.deepEqual(sent, []);
    assert.equal(await shown(), '');
    store.consented = true;
    listeners.forEach((f) => f({ consented: { newValue: true } }));
    assert.match(await shown(), /ovNoticeSaving/);
    assert.deepEqual(sent, ['c1']);  // that choice starts it, without a reload
""")


@needs_node
def test_the_notice_says_where_chats_go_and_can_pause():
    in_a_tab("""
    load('capture.js');
    assert.match(await shown(), /ovNoticeSaving \\| ovPause/);
    assert.ok(store['notice:claude-ai'] > 0);
    find(page, 'ovPause').on.click();
    const said = await shown();
    assert.equal(store.paused, true);
    assert.match(said, /ovNoticePaused \\| ovResume/);
    assert.doesNotMatch(said, /ovNoticeSaving/);
    find(page, 'ovResume').on.click();
    await settle();
    assert.equal(store.paused, false);
""")


@needs_node
@pytest.mark.parametrize("setup", [
    "store.paused = true;",
    "store.noOverlay = true;",
    "store['notice:claude-ai'] = Date.now() - 59 * 60e3;",  # shown within the hour
    "STATE.signedIn = false;",  # nothing is being saved, so it doesn't say so
    "STATE.dashboard = false;",
])
def test_the_notice_stays_away(setup):
    in_a_tab(f"""
    {setup}
    const before = store['notice:claude-ai'];
    load('capture.js');
    assert.equal(await shown(), '');
    assert.equal(store['notice:claude-ai'], before);  // an hour isn't used up saying nothing
""")


@needs_node
def test_the_notice_comes_back_after_an_hour():
    in_a_tab("""
    store['notice:claude-ai'] = Date.now() - 61 * 60e3;
    load('capture.js');
    assert.match(await shown(), /ovNoticeSaving/);
""")


@needs_node
def test_a_tab_loaded_in_the_background_says_it_when_you_look_at_it():
    in_a_tab("""
    doc.visibilityState = 'hidden';
    load('capture.js');
    assert.equal(await shown(), '');
    assert.equal(store['notice:claude-ai'], undefined);  // an hour isn't used up on a message nobody saw
    doc.visibilityState = 'visible';
    doc.seen.forEach((f) => f());
    assert.match(await shown(), /ovNoticeSaving/);
""")


@needs_node
def test_saved_chats_wait_behind_the_notice_but_a_failure_does_not():
    in_a_tab("""
    load('capture.js');
    await shown();
    const o = context.interlatchOverlay;
    o.say({ text: 'saved' });
    assert.doesNotMatch(await shown(), /saved/);
    find(page, '×').on.click();  // the notice is done
    assert.match(await shown(), /saved/);
    o.show({ text: 'notice', kind: 'notice', dismiss: 'x' });
    await settle();
    o.say({ text: 'broken', kind: 'bad', dismiss: 'x' });
    assert.match(await shown(), /broken/);
""")


@needs_node
def test_pausing_during_a_sync_stops_it_and_keeps_the_rest_for_later():
    in_a_tab("""
    STATE.pauseAfter = 2;
    load('capture.js');
    await syncNow();
    assert.deepEqual(sent, ['c1', 'c2']);
    assert.ok(store['sync:claude-ai'].lastRun);
    assert.equal(store['sync:claude-ai'].since, undefined);  // the next sync still looks for the chats not read
""")


@needs_node
@pytest.mark.parametrize("reason, before, opened, consented", [
    ("install", "{}", 1, "undefined"),  # asks first
    ("update", "{'sync:claude-ai': { captured: 3 }}", 0, "true"),  # someone already saving keeps going
    ("update", "{}", 0, "undefined"),  # loaded but never started: still asks
    ("update", "{consented: false}", 0, "false"),
])
def test_installing_asks_first_and_an_update_keeps_going(reason, before, opened, consented):
    script = f"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const store = {before}, tabs = [], installed = [];
const on = {{ addListener() {{}} }};
const chrome = {{
  i18n: {{ getMessage: (k) => k }},
  tabs: {{ create: async (t) => {{ tabs.push(t.url); }} }},
  storage: {{ onChanged: on, local: {{
    get: async (k) => Object.fromEntries([k].flat().filter((n) => n in store).map((n) => [n, store[n]])),
    set: async (v) => {{ Object.assign(store, v); }},
  }} }},
  action: {{ setBadgeBackgroundColor: async () => {{}}, setBadgeTextColor: async () => {{}}, setBadgeText: async () => {{}} }},
  runtime: {{ onStartup: on, onMessage: on, onInstalled: {{ addListener: (f) => installed.push(f) }} }},
}};
vm.runInNewContext(fs.readFileSync(process.argv[1] + '/background.js', 'utf8'), {{ chrome, Object, Number, String, Promise }});
(async () => {{
  await Promise.all(installed.map((f) => f({{ reason: '{reason}' }})));
  assert.equal(tabs.length, {opened});
  if (tabs.length) assert.equal(tabs[0], 'welcome.html');
  assert.equal(store.consented, {consented});
}})().catch((e) => {{ console.error(e); process.exit(1); }});
"""
    result = subprocess.run([NODE, "-e", script, str(browser_extension.BUNDLED)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
