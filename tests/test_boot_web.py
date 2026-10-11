"""boot.js, the dashboard's first script: what a browser remembered under Chronicle's names carries over to Interlatch's."""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
WEB = Path(__file__).resolve().parents[1] / "src" / "chronicle" / "web" / "boot.js"
pytestmark = pytest.mark.skipif(not NODE, reason="Node is needed to run the page's boot script")


def check(script):
    setup = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Store {
  constructor(o) { this.m = new Map(Object.entries(o)); }
  get length() { return this.m.size; }
  key(i) { return [...this.m.keys()][i] ?? null; }
  getItem(k) { return this.m.has(k) ? this.m.get(k) : null; }
  setItem(k, v) { this.m.set(k, String(v)); }
  removeItem(k) { this.m.delete(k); }
  all() { return Object.fromEntries(this.m); }
}
const root = { dataset: {}, classList: { names: new Set(), add(c) { this.names.add(c); } } };
const define = (name, value) => Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
define('window', globalThis);
define('document', { documentElement: root });
define('location', { search: '' });
define('navigator', { languages: ['en-US'], language: 'en-US' });
define('matchMedia', () => ({ matches: false, addEventListener() {} }));
const boot = () => vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
'''
    result = subprocess.run([NODE, "-e", setup + script, str(WEB)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_what_the_browser_kept_under_chronicles_names_carries_over_once():
    check('''
const local = new Store({'chronicle-theme': 'dark', 'chronicle-lang': 'ja', 'chronicle-sidebar': '0', 'chronicle.view.sessions': 'table',
  'chronicle-motion': 'on', 'interlatch-motion': 'off', 'someone-else': 'x'});
const session = new Store({'chronicle-updating': '0.21.3'});
define('localStorage', local); define('sessionStorage', session);
boot();
assert.deepEqual(local.all(), {'interlatch-theme': 'dark', 'interlatch-lang': 'ja', 'interlatch-sidebar': '0',
  'interlatch.view.sessions': 'table', 'interlatch-motion': 'off', 'someone-else': 'x'});  // a value set since wins
assert.deepEqual(session.all(), {'interlatch-updating': '0.21.3'});  // the update's "now on" note survives the reload
assert.equal(root.dataset.theme, 'dark'); assert.equal(root.lang, 'ja'); assert.equal(root.dataset.motion, 'off');
assert.ok(root.classList.names.has('no-sidebar'));
boot();  // the next page load: nothing left to move, nothing changes
assert.equal(local.all()['interlatch-theme'], 'dark');
''')


def test_blocked_storage_still_boots():
    check('''
Object.defineProperty(globalThis, 'localStorage', { get() { throw Error('blocked'); }, configurable: true });
Object.defineProperty(globalThis, 'sessionStorage', { get() { throw Error('blocked'); }, configurable: true });
boot();
assert.equal(root.lang, 'en'); assert.equal(root.dataset.motion, 'on');
''')
