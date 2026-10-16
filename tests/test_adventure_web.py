"""The Adventure map's world layout (adventure.js, Adventure.layout) is pure and deterministic: every lesson stands inside
its first module's territory and its region, encounters keep their distance, fog covers only modules with no lessons,
and the bosses wait in their own region. Runs the browser file in node's vm, without a DOM."""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "interlatch" / "web" / "adventure.js"
ROADMAP = ROOT / "src" / "interlatch" / "knowledge" / "roadmap.json"
pytestmark = pytest.mark.skipif(not NODE, reason="Node is needed to run the map's layout")

SETUP = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const A = Adventure, road = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
// a payload like /api/party/map's: ~1100 lessons over the catalog's 70 modules, a few popular, some with none
function payload(n = 1100, seed = 7) {
  let s = seed >>> 0;
  const r = () => { s = (s + 0x6d2b79f5) >>> 0; let t = Math.imul(s ^ (s >>> 15), 1 | s); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  const ids = road.modules.map((m) => m.id), weight = {};
  ids.forEach((id, i) => { weight[id] = i % 7 === 3 ? 0 : i % 11 === 0 ? 40 : i % 5 === 0 ? 8 : 1; });
  const pool = ids.filter((id) => weight[id] > 0), total = pool.reduce((a, id) => a + weight[id], 0);
  const pick = () => { let u = r() * total; for (const id of pool) { u -= weight[id]; if (u <= 0) return id; } return pool[0]; };
  const kinds = ['fix', 'gotcha', 'decision', 'learning', 'pattern', 'command'];
  const lessons = [];
  for (let k = 0; k < n; k++) {
    const first = pick(), extra = ids[Math.floor(r() * ids.length)];
    lessons.push({ id: 5000 + k, title: 'Lesson ' + k, kind: kinds[k % 6], stage: 'provisional', agent: 'claude', at: '2026-10-14', xp: 2,
      modules: extra === first || weight[extra] === 0 ? [first] : [first, extra], session_id: null, project: null });
  }
  const count = {};
  for (const l of lessons) for (const m of l.modules) count[m] = (count[m] || 0) + 1;
  return {
    regions: road.attrs.map((a) => ({ id: a.id, name: a.name, about: a.about, modules: road.modules.filter((m) => m.attr === a.id)
      .map((m) => ({ id: m.id, name: m.name, lessons: count[m.id] || 0, xp: 3 * (count[m.id] || 0) })) })),
    lessons, cleared: lessons.slice(0, 50).map((l) => l.id), newest: lessons[n - 1].id,
    bosses: [{ id: 'G1', n: 1, text: 'one', attr: 'craft', status: 'done' }, { id: 'G2', n: 2, text: 'two', attr: 'guard', status: 'evidence' },
      { id: 'G3', n: 3, text: 'three', attr: 'craft', status: 'open' }, { id: 'G4', n: 4, text: 'four', attr: 'lore', status: 'open' }],
    agents: [{ agent: 'claude', name: 'Claude Code', short: 'Claude', level: 9, tier: 'adept' }],
  };
}
// inside a convex polygon, by at least `m`
function inside(poly, x, y, m = 0) {
  let area = 0;
  for (let i = 0; i < poly.length; i++) { const a = poly[i], b = poly[(i + 1) % poly.length]; area += a[0] * b[1] - b[0] * a[1]; }
  const sg = area >= 0 ? 1 : -1;
  return poly.every((a, i) => { const b = poly[(i + 1) % poly.length], len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    return len < 1e-9 || (sg * ((b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]))) / len >= m - 1e-9; });
}
const inRect = (r, x, y) => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h;
"""


def check(script):
    result = subprocess.run([NODE, "-e", SETUP + script, str(WEB), str(ROADMAP)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_the_layout_is_deterministic_and_the_seed_matters():
    check("""
const d = payload();
const a = JSON.stringify(A.layout(d, 3)), b = JSON.stringify(A.layout(JSON.parse(JSON.stringify(d)), 3));
assert.equal(a, b);
assert.notEqual(a, JSON.stringify(A.layout(d, 4)));
assert.equal(JSON.stringify(A.layout(d)), JSON.stringify(A.layout(d, 1)));
""")


def test_every_lesson_stands_inside_its_first_modules_territory_and_region():
    check("""
const d = payload(), L = A.layout(d, 1);
assert.equal(L.lessons.length, d.lessons.length);
assert.deepEqual(L.unplaced, []);
const byId = new Map(d.lessons.map((l) => [l.id, l]));
for (const p of L.lessons) {
  const t = L.territories[p.territory], r = L.regions[t.region];
  assert.equal(p.module, byId.get(p.id).modules[0]);
  assert.equal(t.id, p.module);
  assert.equal(r.id, p.attr);
  assert.ok(Number.isInteger(p.x) && Number.isInteger(p.y));
  assert.ok(inside(t.poly, p.x, p.y, 8), `lesson ${p.id} is not inside ${t.id}`);
  assert.ok(inRect(r.rect, p.x, p.y), `lesson ${p.id} is not inside ${r.id}`);
  for (const q of r.reserve) assert.ok(!(p.x > q.x && p.x < q.x + q.w && p.y > q.y && p.y < q.y + q.h), `lesson ${p.id} stands on the gate or banner`);
}
// the territories (and the gate's and banner's plazas) tile their region
for (const r of L.regions) {
  const area = L.territories.filter((t) => t.region === r.index).reduce((a, t) => a + t.area, 0)
    + r.plazas.reduce((a, p) => { let s = 0; p.poly.forEach((v, i) => { const w = p.poly[(i + 1) % p.poly.length]; s += v[0] * w[1] - w[0] * v[1]; }); return a + Math.abs(s) / 2; }, 0);
  assert.ok(Math.abs(area - r.rect.w * r.rect.h) < 1, `${r.id} is not tiled`);
}
""")


def test_no_two_encounters_stand_closer_than_the_minimum():
    check("""
for (const seed of [1, 2, 3, 5, 8]) {
  const L = A.layout(payload(1100, seed), seed);
  assert.equal(L.minDist, 16, `seed ${seed} had to crowd its encounters`);
  const ps = L.lessons;
  let min = Infinity;
  for (let i = 0; i < ps.length; i++) for (let j = i + 1; j < ps.length; j++) min = Math.min(min, Math.hypot(ps[i].x - ps[j].x, ps[i].y - ps[j].y));
  assert.ok(min >= 16, `seed ${seed}: two encounters ${min} apart`);
}
""")


def test_fog_covers_only_modules_with_no_lessons():
    check("""
const d = payload(), L = A.layout(d, 1);
const lessons = new Map(), firsts = new Set(d.lessons.map((l) => l.modules[0]));
for (const r of d.regions) for (const m of r.modules) lessons.set(m.id, m.lessons);
assert.equal(L.territories.length, 70);
let fog = 0;
for (const t of L.territories) {
  assert.equal(t.fog, lessons.get(t.id) === 0 && !firsts.has(t.id), t.id);
  if (t.fog) { fog++; assert.equal(t.count, 0); assert.equal(t.hole, null); }
  else if (t.count) assert.ok(t.hole, `${t.id} has no clearing`);
}
assert.ok(fog > 0 && fog < 70);
// no lessons at all: everything under fog, no party; no regions: an empty world
const bare = { ...d, lessons: [], newest: null, regions: d.regions.map((r) => ({ ...r, modules: r.modules.map((m) => ({ ...m, lessons: 0 })) })) };
const B = A.layout(bare, 1);
assert.ok(B.territories.every((t) => t.fog));
assert.equal(B.party, null);
const E = A.layout({}, 1);
assert.deepEqual([E.regions.length, E.lessons.length, E.party], [0, 0, null]);
""")


def test_bosses_wait_in_their_region_and_the_party_at_the_newest_lesson():
    check("""
const d = payload(), L = A.layout(d, 1);
assert.equal(L.bosses.length, 4);
for (const b of L.bosses) {
  const r = L.regions[b.region];
  assert.equal(r.id, d.bosses.find((x) => x.id === b.id).attr);
  assert.ok(inRect(r.rect, b.x, b.y), `boss ${b.id} is outside ${r.id}`);
}
const craft = L.bosses.filter((b) => b.attr === 'craft');
assert.ok(Math.abs(craft[0].x - craft[1].x) >= 30, 'two bosses at one gate stand apart');
const unknown = A.layout({ ...d, bosses: [{ id: 'X', n: 1, text: '?', attr: 'nowhere', status: 'open' }] }, 1);
assert.equal(unknown.bosses.length, 0);
const newest = L.lessons.find((p) => p.id === d.newest);
assert.equal(L.party.territory, newest.territory);
assert.equal(L.party.lesson, d.newest);
assert.ok(inside(L.territories[L.party.territory].poly, L.party.x, L.party.y));
""")
