"""A region's field (field.js, Field.layout and Field.path) is pure and deterministic: one zone per module of the region,
every open tile reachable from where the party starts, the monsters only the zones' newest uncleared lessons (a few a
zone), each at home somewhere the party can walk to."""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
WEB = Path(__file__).resolve().parents[1] / "src" / "interlatch" / "web" / "field.js"
pytestmark = pytest.mark.skipif(not NODE, reason="Node is needed to run the field's layout")

SETUP = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
globalThis.window = globalThis;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const F_ = Field;
// a /api/party/map payload: two regions, the first with `mods` modules and `per` lessons in each
function data(mods = 6, per = 5, cleared = []) {
  const modules = Array.from({length: mods}, (_, i) => ({id: `M-c${i}`, name: `Module ${i}`, lessons: per, xp: per}));
  const lessons = [];
  let id = 1;
  for (const m of modules) for (let k = 0; k < per; k++) lessons.push({id: id++, title: `Lesson ${id}`, kind: 'fix', stage: 'provisional',
    agent: 'claude', at: '2026-10-01', xp: 1, modules: [m.id], session_id: null, project: null, case: false});
  return {regions: [{id: 'craft', name: 'Craft', about: '', modules}, {id: 'data', name: 'Data', about: '', modules: [{id: 'M-d0', name: 'D', lessons: 0, xp: 0}]}],
    lessons, cleared, diagnosed: [], bosses: [], newest: id - 1, gold: 0,
    agents: [{agent: 'claude', name: 'Claude Code', short: 'Claude', level: 3, tier: 'novice'}],
    towns: [{attr: 'craft', name: 'Craft town'}, {attr: 'data', name: 'Data town'}]};
}
function reachable(F) { // flood from the spawn: the open tiles it reaches
  const seen = new Set([F.spawn.y * F.cols + F.spawn.x]), todo = [[F.spawn.x, F.spawn.y]];
  while (todo.length) {
    const [x, y] = todo.pop();
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx, ny = y + dy, i = ny * F.cols + nx;
      if (nx < 0 || ny < 0 || nx >= F.cols || ny >= F.rows || F.block[i] || seen.has(i)) continue;
      seen.add(i); todo.push([nx, ny]);
    }
  }
  return seen;
}
'''


def check(script):
    result = subprocess.run([NODE, "-e", SETUP + script, str(WEB)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_the_field_is_deterministic_with_a_zone_per_module_and_the_town():
    check('''
const a = F_.layout(data(), 'craft'), b = F_.layout(data(), 'craft');
assert.deepEqual([...a.block], [...b.block]);
assert.deepEqual(a.props, b.props);
assert.deepEqual(a.monsters, b.monsters);
assert.notDeepEqual([...F_.layout(data(), 'craft', 2).block], [...a.block]);  // another seed, another field
assert.deepEqual(a.zones.map((z) => z.id), ['M-c0', 'M-c1', 'M-c2', 'M-c3', 'M-c4', 'M-c5']);
assert.deepEqual(a.zones.map((z) => [z.lessons, z.open]), Array(6).fill([5, 5]));
assert.equal(a.town.name, 'Craft town');
assert.equal(a.region.id, 'craft');
// every zone owns tiles, and its sign stands in its own clearing
for (const z of a.zones) {
  assert.ok([...a.zoneOf].some((v) => v === z.index), 'no tiles for ' + z.id);
  assert.equal(a.zoneOf[z.sign.y * a.cols + z.sign.x], z.index);
  assert.ok(a.props.some((p) => p.type === 'sign' && p.x === z.sign.x && p.y === z.sign.y));
}
// an unknown region falls back to the first; a region without lessons has no monsters
assert.equal(F_.layout(data(), 'nowhere').region.id, 'craft');
assert.deepEqual(F_.layout(data(), 'data').monsters, []);
''')


def test_every_open_tile_the_town_gate_and_every_monster_can_be_reached():
    check('''
for (const [mods, per] of [[1, 1], [6, 5], [12, 40], [16, 3]]) {
  const F = F_.layout(data(mods, per), 'craft'), seen = reachable(F);
  for (let i = 0; i < F.block.length; i++) if (!F.block[i]) assert.ok(seen.has(i), `open tile ${i} cut off (${mods} modules)`);
  assert.ok(F_.path(F, F.spawn, F.gate).length > 1);
  for (const m of F.monsters) {
    assert.ok(!F.block[m.y * F.cols + m.x], 'a monster at home on a blocked tile');
    const route = F_.path(F, F.spawn, m);
    assert.ok(route.length > 1, 'no way to monster ' + m.id);
    for (const [x, y] of route) assert.ok(!F.block[y * F.cols + x]);  // the route never crosses what's in the way
  }
}
''')


def test_the_monsters_are_each_zones_newest_uncleared_lessons_a_few_at_a_time():
    check('''
const zonesOf = (F) => { const by = new Map(); for (const m of F.monsters) by.set(m.zone, [...(by.get(m.zone) || []), m.id]); return by; };
const F = F_.layout(data(6, 5, [5, 4]), 'craft');
const byZone = zonesOf(F);
// zone 0 holds lessons 1-5; 5 and 4 are cleared, so its newest uncleared are 3 and 2 (two a zone off the main quest)
assert.deepEqual(byZone.get(0), [3, 2]);
assert.deepEqual(byZone.get(1), [10, 9]);
assert.ok([...byZone.values()].every((ids) => ids.length <= 2));
assert.equal(F.zones[0].open, 3);
// lessons with study material first; the agents' friction never (nor counted as open); a main quest zone has four
const d = data(6, 5);
d.lessons.find((l) => l.id === 1).study = true;
d.lessons.find((l) => l.id === 5).friction = true;
d.goal = {id: 'T', name: 'Track', modules: ['M-c1']};
d.readiness = {'M-c0': 2};
const G = F_.layout(d, 'craft'), byG = zonesOf(G);
assert.deepEqual(byG.get(0), [1, 4]);
assert.deepEqual(byG.get(1), [10, 9, 8, 7]);
assert.equal(G.zones[0].open, 4);
assert.deepEqual(G.zones.map((z) => [z.goal, z.you]), [[false, 2], [true, 0], [false, 0], [false, 0], [false, 0], [false, 0]]);
// where the person studies, a lesson stays out until it's understood, cleared or not
const S = data(6, 5, [5, 4, 3]);
Object.assign(S, {studying: true, understood: [5, 1]});
assert.deepEqual(zonesOf(F_.layout(S, 'craft')).get(0), [4, 3]);
assert.equal(F_.layout(S, 'craft').zones[0].open, 3);
// back for another look: out again, first in its zone, marked
S.back = [1];
const B = F_.layout(S, 'craft');
assert.deepEqual(zonesOf(B).get(0), [1, 4]);
assert.deepEqual(B.monsters.filter((m) => m.zone === 0).map((m) => m.back), [true, false]);
// a lesson fought today rests until tomorrow, whatever else holds: the next one takes its place
S.today = [1, 4];
assert.deepEqual(zonesOf(F_.layout(S, 'craft')).get(0), [3, 2]);
// no two monsters share a home; none starts right by the spawn
const homes = new Set(F.monsters.map((m) => m.x + ',' + m.y));
assert.equal(homes.size, F.monsters.length);
for (const m of F.monsters) assert.ok(Math.hypot(m.x - F.spawn.x, m.y - F.spawn.y) >= 6);
// all cleared: no monsters left in the zone
const all = F_.layout(data(2, 3, [1, 2, 3]), 'craft');
assert.deepEqual(all.monsters.filter((m) => m.zone === 0), []);
assert.equal(all.zones[0].open, 0);
''')


def test_path_finds_a_way_and_refuses_blocked_ends():
    check('''
const F = F_.layout(data(), 'craft');
assert.deepEqual(F_.path(F, F.spawn, F.spawn), [[F.spawn.x, F.spawn.y]]);
const tree = F.props.find((p) => p.type === 'tree');
assert.deepEqual(F_.path(F, F.spawn, tree), []);
assert.deepEqual(F_.path(F, {x: -1, y: 0}, F.spawn), []);
const route = F_.path(F, F.spawn, F.gate);
assert.deepEqual(route[0], [F.spawn.x, F.spawn.y]);
assert.deepEqual(route[route.length - 1], [F.gate.x, F.gate.y]);
for (let i = 1; i < route.length; i++) assert.ok(Math.max(Math.abs(route[i][0] - route[i - 1][0]), Math.abs(route[i][1] - route[i - 1][1])) === 1);
''')
