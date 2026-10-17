"""The Adventure's turn-based battles (battle.js): the pure rules, run in node's vm without a DOM."""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
WEB = Path(__file__).resolve().parents[1] / "src" / "interlatch" / "web" / "battle.js"
pytestmark = pytest.mark.skipif(not NODE, reason="Node is needed to run the battle rules")

SETUP = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
globalThis.window = globalThis;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const B = Battle, R = Battle.rules;
const AGENTS = ['claude', 'codex', 'copilot', 'bob', 'antigravity', 'opencode'];
const KINDS = ['fix', 'gotcha', 'decision', 'learning', 'pattern', 'command'];
const ATTRS = ['craft', 'data', 'ops', 'guard', 'arcana', 'lore'];
const STAGE_XP = {provisional: 1, established: 3, canonical: 6};
// a lesson and its party as /api/party/encounter would give them, made up from a seed
function lesson(i) {
  const r = R.rng(i * 7 + 1);
  const stage = r() < 0.6 ? 'provisional' : r() < 0.75 ? 'established' : 'canonical';
  const n = 1 + Math.floor(r() * 3);
  const modules = Array.from({length: n}, (_, k) => ({id: `M-${i}-${k}`, name: `Module ${k}`, attr: ATTRS[Math.floor(r() * 6)]}));
  const lead = AGENTS[Math.floor(r() * 6)];
  const party = AGENTS.map((agent) => {
    const lv = Math.floor(r() * 30), attr_levels = {}, skills = {};
    ATTRS.forEach((a) => { attr_levels[a] = Math.floor(r() * Math.max(1, lv / 2.5)); });
    modules.forEach((m) => { skills[m.id] = r() < 0.5 ? 0 : Math.floor(r() * Math.max(1, lv / 3)); });
    return {agent, name: agent, short: agent, level: agent === lead ? Math.max(1, lv) : (r() < 0.3 ? 0 : lv),
      tier: 'novice', attr_levels, skills};
  });
  return {lesson: {id: i + 1, title: `Lesson ${i}`, kind: KINDS[Math.floor(r() * 6)], stage, agent: lead, at: '2026-10-14',
    xp: STAGE_XP[stage] * n, session_id: null, project: null, body: '## Heading\n\nThe *body* of the `lesson`.',
    case: i % 3 ? null : {scene: 'A scene.', question: 'Why?', answer: 'Because.'}, modules}, party, cleared: false};
}
const boss = (i, n, status = 'open') => ({boss: {id: `T-x-G${n}`, n, text: 'Ship it', track: 'T-x', track_name: 'Backend',
  attr: ATTRS[i % 6], status}, party: lesson(i).party.map(({attr_levels, skills, ...m}) => m)});
// gear for every hero: tier T of the lesson's first module (weapon + armor + charm, as the shop sells them)
const geared = (e, T) => ({...e, party: e.party.map((m) => ({...m, gear: {atk: 3 * T, hp: 12 * T, mp: 6 * T, def: 0.05 * T,
  skills: {[e.lesson.modules[0].id]: 2 * T}}}))});
const ALL_ITEMS = R.ITEMS.map((id) => ({id, name: id, kind: id.startsWith('l:') ? 'loot' : 'consumable', count: 3}));
// a player that uses an item whenever it can, then fights
const itemsFirst = (s) => { const o = R.itemOptions(s).find((x) => x.usable && x.id !== 'c:smoke'); return o ? {type: 'item', id: o.id} : R.policy(s); };
// every number in a state is finite
function finite(state) {
  const nums = [state.round, state.foe.hp, state.foe.maxHp, state.foe.power, state.foe.d];
  for (const h of state.heroes) nums.push(h.hp, h.maxHp, h.mp, h.maxMp, h.atk, h.level, ...Object.values(h.skills));
  for (const it of state.items || []) nums.push(it.count);
  nums.forEach((v) => assert.ok(Number.isFinite(v), 'not a number: ' + JSON.stringify(state.heroes)));
}
'''


def check(script):
    result = subprocess.run([NODE, "-e", SETUP + script, str(WEB)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_same_seed_and_choices_give_the_same_fight_and_attempts_differ():
    check('''
const choices = (s) => (s.round % 3 === 0 ? {type: 'defend'} : R.policy(s));
for (let i = 0; i < 40; i++) {
  const e = lesson(i);
  const a = R.simulate(e, 2, choices), b = R.simulate(e, 2, choices);
  assert.deepEqual(a.log, b.log);
  assert.equal(a.phase, b.phase);
  assert.equal(R.seedOf(e, 2), (e.lesson.id * 1009 + 2) >>> 0);
}
// another attempt rolls other dice
let differ = 0;
for (let i = 0; i < 20; i++) if (JSON.stringify(R.simulate(lesson(i), 0).log) !== JSON.stringify(R.simulate(lesson(i), 1).log)) differ++;
assert.ok(differ >= 15, 'attempts barely differ: ' + differ);
''')


def test_a_typical_lesson_is_winnable_with_attack_and_skill_and_tough_ones_can_be_lost():
    check('''
let won = 0, rounds = [], lostTough = 0;
for (let i = 0; i < 200; i++) {
  const s = R.simulate(lesson(i), 0, R.policy, 12);
  finite(s);
  if (s.phase === 'won') { won++; rounds.push(s.round); }
}
assert.ok(won >= 180, 'won only ' + won + ' of 200');
rounds.sort((a, b) => a - b);
const median = rounds[rounds.length >> 1];
assert.ok(median >= 3 && median <= 6, 'median rounds ' + median);
// Attack alone (a hero with no relevant skill) still wins most typical lessons
let plain = 0;
for (let i = 0; i < 200; i++) if (R.simulate(lesson(i), 0, () => ({type: 'attack'}), 12).phase === 'won') plain++;
assert.ok(plain >= 150, 'attack alone won only ' + plain);
// the hardest lesson against a weak party, with plain attacks, is lost at least sometimes
const weak = (i) => { const e = lesson(i); e.lesson.stage = 'canonical'; e.lesson.xp = 18;
  e.party = e.party.map((m) => ({...m, level: Math.min(m.level, 4), attr_levels: {}, skills: {}})); return e; };
for (let i = 0; i < 30; i++) if (R.simulate(weak(i), 0, () => ({type: 'attack'}), 40).phase === 'lost') lostTough++;
assert.ok(lostTough >= 5, 'tough lessons never lost: ' + lostTough);
''')


def test_the_party_is_the_teacher_and_the_two_strongest_others():
    check('''
const e = lesson(3);
e.lesson.agent = 'bob';
e.party = AGENTS.map((agent, i) => ({agent, short: agent, level: [29, 14, 6, 2, 0, 9][i], attr_levels: {}, skills: {}}));
assert.deepEqual(R.pickParty(e).map((m) => m.agent), ['bob', 'claude', 'codex']);
e.party.forEach((m) => { if (m.agent !== 'bob') m.level = 0; });
assert.deepEqual(R.pickParty(e).map((m) => m.agent), ['bob']);  // level 0 agents stay home
const s = R.setup(e, 0);
assert.equal(s.heroes[0].lead, true);
assert.deepEqual(R.pickParty(boss(1, 2)).length, 3);
// skill levels come from the encounter, MP pays for them, untrained skills are weak
const t = R.setup(lesson(5), 0), h = t.heroes[0], opt = R.skillOptions(t, 0)[0];
assert.equal(opt.level, h.skills[opt.id]);
const before = h.mp;
R.heroAction(t, {type: 'skill', module: opt.id});
assert.equal(h.mp, before - R.SKILL_COST);
''')


def test_a_boss_never_drops_below_one_hp_and_a_done_boss_is_not_fought():
    check('''
let held = 0;
for (let i = 0; i < 60; i++) for (const n of [1, 2, 3, 4]) {
  const s = R.setup(boss(i, n), 0);
  // a huge blow: the boss still stands at 1 HP and the fight ends there
  for (let k = 0; k < 400 && s.phase !== 'held' && s.phase !== 'lost'; k++) {
    if (s.phase === 'hero') { s.heroes[s.active].atk = 5000; R.heroAction(s, {type: 'attack'}); }
    else R.monsterTurn(s);
    assert.ok(s.foe.hp >= 1, 'boss at ' + s.foe.hp);
  }
  if (s.phase === 'held') held++;
  assert.ok(s.log.some((l) => l.key === 'lineHold'));
  const sim = R.simulate(boss(i, n), 0);
  assert.ok(sim.foe.hp >= 1);
  assert.notEqual(sim.phase, 'won');
  finite(sim);
}
assert.equal(held, 240);
const done = R.setup(boss(1, 2, 'done'), 0);
assert.equal(done.phase, 'done');
assert.deepEqual(R.heroAction(done, {type: 'attack'}), []);
assert.match(R.summary(done).title, /already defeated/);
''')


def test_run_ends_the_fight_and_battles_never_claim_xp():
    check('''
const s = R.setup(lesson(1), 0);
R.heroAction(s, {type: 'run'});
assert.equal(s.phase, 'ran');
const won = R.simulate(lesson(2), 0);
assert.equal(won.phase, 'won');
const sum = R.summary(won, B.LABELS, null, lesson(2));
assert.match(sum.text, /learned this on 2026-10-14: \\+\\d+ XP then\\. Battles don't change levels\\./);
assert.equal(sum.quote, lesson(2).lesson.case ? 'Because.' : 'Heading: The body of the lesson.');
// the case scene opens the fight when there is one
const withCase = R.setup(lesson(0), 0);
assert.equal(withCase.log[0].vars.text, 'A scene.');
assert.equal(withCase.log[1].key, 'intro');
''')


def test_labels_have_no_missing_placeholders_and_every_line_fills():
    check('''
const vars = (t) => [...String(t).matchAll(/\\{(\\w+)\\}/g)].map((m) => m[1]);
for (const [k, t] of Object.entries(B.LABELS)) {
  assert.ok(k in B.LABEL_VARS, 'undocumented label ' + k);
  for (const v of vars(t)) assert.ok(B.LABEL_VARS[k].includes(v), `${k} uses {${v}} it isn't given`);
}
// every move a monster can make has a label
for (const [kind, set] of Object.entries(R.MOVES)) for (const m of [...set.list, set.big]) assert.ok(`move.${kind}.${m.id}` in B.LABELS, kind + '.' + m.id);
for (const k of KINDS) assert.ok('monster.' + k in B.LABELS);
// every line said in 200 fights and 40 boss fights fills completely, with no NaN or undefined
const states = [];
for (let i = 0; i < 200; i++) states.push(R.simulate(lesson(i), i % 4, (s) => (s.round % 4 === 2 ? {type: 'defend'} : R.policy(s))));
for (let i = 0; i < 40; i++) states.push(R.simulate(boss(i, 1 + (i % 4)), 0));
for (let i = 0; i < 60; i++) states.push(R.simulate({...geared(lesson(i), 1 + (i % 3)), items: ALL_ITEMS}, 0, itemsFirst));
for (let i = 0; i < 20; i++) states.push(R.simulate({...boss(i, 1 + (i % 4)), items: ALL_ITEMS}, 0, itemsFirst));
for (const s of states) {
  for (const line of s.log) {
    const text = line.key ? B.format(B.LABELS[line.key], line.vars) : line.vars.text;
    assert.ok(line.key == null || line.key in B.LABELS, 'no label ' + line.key);
    assert.ok(!/\\{\\w+\\}|NaN|undefined/.test(text), 'unfilled: ' + text);
  }
  const sum = R.summary(s, B.LABELS);
  for (const t of Object.values(sum)) assert.ok(!/\\{\\w+\\}|NaN|undefined/.test(t), 'unfilled: ' + t);
}
''')


def test_excerpt_strips_markdown_markup_but_keeps_identifiers_and_code():
    check(r'''
const body = [
  '## What happened',
  '',
  'The **worker** read `doc_int_text` from run_ai_analysis_node, so _stale_ values went out.',
  'Call mcp__playwright__browser_navigate with `*args` and a*b*c; see [the docs](https://example.com) and __init__.py.',
  '```python',
  'snake_case_name = 1',
  '```',
  '- check the ~~old~~ snapshot',
  '* re-read after the move',
].join('\n');
const x = R.excerpt(body, 1000);
for (const kept of ['doc_int_text', 'run_ai_analysis_node', 'mcp__playwright__browser_navigate', '*args', 'a*b*c', '__init__.py',
  'snake_case_name = 1']) assert.ok(x.includes(kept), `lost ${kept}: ${x}`);
assert.ok(x.startsWith('What happened: The worker read doc_int_text'), x);
assert.ok(x.includes('so stale values went out.'), x);
assert.ok(x.includes('see the docs and'), x);
assert.ok(x.includes('check the old snapshot. re-read after the move.'), x);
assert.ok(!/[`#]|\*\*|\]\(|```/.test(x), x);
assert.ok(R.excerpt('word '.repeat(200), 50).endsWith('…'));
// the engine adds no spaces around what it fills in (Japanese reads without them)
assert.equal(B.format('{monster}を倒した！', {monster: 'グリッチ'}), 'グリッチを倒した！');
assert.equal(B.format('{hero}はどうする？', {hero: 'Claude'}), 'Claudeはどうする？');
''')


def test_gear_adds_to_battle_numbers_only_and_an_ungeared_party_is_unchanged():
    check(r"""
const m = {agent: 'claude', level: 10, attr_levels: {craft: 4}};
const bare = R.heroStats(m, 'craft');
assert.deepEqual(bare, {hp: 36 + 30 + 20, mp: 14 + 5 + 6, atk: 8 + 6 + Math.round(4.8), def: 0});
const g = R.heroStats({...m, gear: {atk: 9, hp: 36, mp: 18, def: 0.15, skills: {}}}, 'craft');
assert.deepEqual(g, {hp: bare.hp + 36, mp: bare.mp + 18, atk: bare.atk + 9, def: 0.15});
assert.equal(R.heroStats({...m, gear: {def: 5}}, 'craft').def, 0.6);            // capped
assert.deepEqual(R.heroStats({...m, gear: {atk: 'x', hp: null}}, 'craft'), bare); // junk is nothing
// an empty gear object is the same fight as none
for (let i = 0; i < 30; i++) {
  const e = lesson(i), e0 = {...e, party: e.party.map((p) => ({...p, gear: {atk: 0, hp: 0, mp: 0, def: 0, skills: {}}}))};
  assert.deepEqual(R.simulate(e, 1).log, R.simulate(e0, 1).log);
}
// gear's skill levels add to the module's skill in battle, shown apart; the hero's level doesn't move
const e = geared(lesson(5), 3), s = R.setup(e, 0), h = s.heroes[0], mod = e.lesson.modules[0].id;
const opt = R.skillOptions(s, 0).find((o) => o.id === mod);
assert.equal(opt.gear, 6);
assert.equal(opt.own, lesson(5).party.find((p) => p.agent === h.agent).skills[mod] || 0);
assert.equal(opt.level, opt.own + 6);
assert.equal(h.level, Math.max(0, lesson(5).party.find((p) => p.agent === h.agent).level));
// armour cuts the damage taken: the same dice, less damage with def
const hit = (def) => { const st = R.setup({...lesson(7), party: lesson(7).party.map((p) => ({...p, gear: {def}}))}, 0);
  st.phase = 'monster'; R.monsterTurn(st); return st.heroes.reduce((t, x) => t + (x.maxHp - x.hp), 0); };
assert.ok(hit(0.3) < hit(0) * 0.8 && hit(0.3) > 0, `${hit(0.3)} vs ${hit(0)}`);
// the monster is sized to the bare party plus half the gear
const m0 = R.stats(lesson(9)).foe.maxHp, m3 = R.stats(geared(lesson(9), 3)).foe.maxHp;
assert.ok(m3 > m0 && m3 < m0 * 1.6, `${m0} -> ${m3}`);
""")


def test_a_tier_three_party_wins_comfortably_but_not_instantly():
    check(r"""
for (const [T, need, lo, hi] of [[0, 180, 3, 6], [3, 195, 2, 4]]) {
  let won = 0, first = 0, rounds = [];
  for (let i = 0; i < 200; i++) {
    const s = R.simulate(T ? geared(lesson(i), T) : lesson(i), 0, R.policy, 12);
    finite(s);
    if (s.phase === 'won') { won++; rounds.push(s.round); if (s.round === 1) first++; }
  }
  rounds.sort((a, b) => a - b);
  const median = rounds[rounds.length >> 1];
  assert.ok(won >= need, `tier ${T}: won ${won}`);
  assert.ok(median >= lo && median <= hi, `tier ${T}: median ${median}`);
  assert.ok(first <= 10, `tier ${T}: ${first} fights over in the first round`);
}
""")


def test_each_item_does_what_it_says():
    check(r"""
const fresh = (items, e = lesson(4)) => { const s = R.setup({...e, items}, 0); return s; };
const one = (id, n = 1) => [{id, name: id.toUpperCase(), kind: 'consumable', count: n}];
// Potion: 40% of the active hero's max HP
let s = fresh(one('c:potion')), h = s.heroes[0];
h.hp = 1;
R.heroAction(s, {type: 'item', id: 'c:potion'});
assert.equal(h.hp, 1 + Math.ceil(h.maxHp * 0.4));
assert.equal(s.items[0].count, 0);
assert.equal(s.active, s.heroes.length > 1 ? 1 : 0);  // it used the turn
// Ether: 50% MP
s = fresh(one('c:ether')); h = s.heroes[0]; h.mp = 0;
R.heroAction(s, {type: 'item', id: 'c:ether'});
assert.equal(h.mp, Math.ceil(h.maxMp * 0.5));
// Phoenix Down: not usable with nobody down; raises the first one down at 30%
s = fresh(one('c:phoenix'));
assert.equal(R.itemOptions(s)[0].usable, false);
assert.deepEqual(R.heroAction(s, {type: 'item', id: 'c:phoenix'}).filter((e) => e.t !== 'say'), []);
assert.equal(s.items[0].count, 1);
const e3 = lesson(4); assert.ok(R.pickParty(e3).length >= 2, 'needs a party of two');
s = fresh(one('c:phoenix')); s.heroes[1].ko = true; s.heroes[1].hp = 0;
assert.equal(R.itemOptions(s)[0].target, 1);
R.heroAction(s, {type: 'item', id: 'c:phoenix'});
assert.equal(s.heroes[1].ko, false);
assert.equal(s.heroes[1].hp, Math.ceil(s.heroes[1].maxHp * 0.3));
// Smoke Bomb: a sure escape
s = fresh(one('c:smoke')); R.heroAction(s, {type: 'item', id: 'c:smoke'}); assert.equal(s.phase, 'ran');
// Patch: 25% HP to everyone standing
s = fresh(one('l:patch')); s.heroes.forEach((x) => { x.hp = 1; });
R.heroAction(s, {type: 'item', id: 'l:patch'});
s.heroes.forEach((x) => assert.equal(x.hp, 1 + Math.ceil(x.maxHp * 0.25)));
// Insight: 30% MP to everyone
s = fresh(one('l:insight')); s.heroes.forEach((x) => { x.mp = 0; });
R.heroAction(s, {type: 'item', id: 'l:insight'});
s.heroes.forEach((x) => assert.equal(x.mp, Math.ceil(x.maxMp * 0.3)));
// Warning Sign: 40% less damage for two monster turns, then back to normal (same dice with and without)
const taken = (withSign) => {
  const t = fresh(withSign ? one('l:sign') : []);
  if (withSign) t.ward = 2;
  const out = [];
  for (let k = 0; k < 3; k++) { t.heroes.forEach((x) => { x.hp = x.maxHp; x.ko = false; x.guard = false; }); t.phase = 'monster'; R.monsterTurn(t);
    out.push(t.heroes.reduce((a, x) => a + (x.maxHp - x.hp), 0)); }
  return out;
};
const [a, b] = [taken(false), taken(true)];
for (const k of [0, 1]) assert.ok(b[k] <= Math.ceil(a[k] * 0.6) + s.heroes.length, `turn ${k}: ${b[k]} vs ${a[k]}`);
assert.equal(b[2], a[2]);
s = fresh(one('l:sign')); R.heroAction(s, {type: 'item', id: 'l:sign'}); assert.equal(s.ward, 2);
// ADR Scroll: the monster loses its next turn
s = fresh(one('l:adr')); R.heroAction(s, {type: 'item', id: 'l:adr'});
while (s.phase === 'hero') R.heroAction(s, {type: 'defend'});
const hp0 = s.heroes.map((x) => x.hp);
const evs = R.monsterTurn(s);
assert.ok(evs.some((e) => e.t === 'skip'));
assert.deepEqual(s.heroes.map((x) => x.hp), hp0);
assert.equal(s.phase, 'hero');
// Template: the next blow ×1.6 (same dice)
const blow = (tpl) => { const t = fresh([]); t.template = tpl; return R.heroAction(t, {type: 'attack'}).find((e) => e.t === 'strike').n; };
assert.ok(Math.abs(blow(true) - blow(false) * 1.6) <= 2, `${blow(true)} vs ${blow(false)}`);
s = fresh(one('l:template')); R.heroAction(s, {type: 'item', id: 'l:template'}); assert.equal(s.template, true);
// Script: 25% of the monster's max HP
s = fresh(one('l:script')); R.heroAction(s, {type: 'item', id: 'l:script'});
assert.equal(s.foe.maxHp - s.foe.hp, Math.round(s.foe.maxHp * 0.25));
// a Script can finish a monster off
s = fresh(one('l:script')); s.foe.hp = 2; R.heroAction(s, {type: 'item', id: 'l:script'}); assert.equal(s.phase, 'won');
// no item left: nothing happens
s = fresh(one('c:potion', 0)); assert.deepEqual(R.itemOptions(s), []);
assert.deepEqual(R.heroAction(s, {type: 'item', id: 'c:potion'}), []);
""")


def test_a_boss_holds_at_one_hp_against_scripts_and_counts_never_go_negative():
    check(r"""
for (let i = 0; i < 40; i++) for (const n of [1, 2, 3, 4]) {
  const s = R.setup({...boss(i, n), items: [{id: 'l:script', name: 'Script', kind: 'loot', count: 99}]}, 0);
  s.foe.hp = Math.max(2, Math.round(s.foe.maxHp * 0.15));
  for (let k = 0; k < 300 && s.phase !== 'held' && s.phase !== 'lost'; k++) {
    if (s.phase === 'hero') R.heroAction(s, {type: 'item', id: 'l:script'});
    else R.monsterTurn(s);
    assert.ok(s.foe.hp >= 1, 'boss at ' + s.foe.hp);
  }
  assert.equal(s.phase, 'held');
  assert.equal(s.foe.hp, 1);
}
// whatever is pressed, as often as it is pressed, no count drops below zero and nothing breaks
const r = R.rng(5);
for (let i = 0; i < 60; i++) {
  const s = R.setup({...lesson(i), items: ALL_ITEMS.map((it) => ({...it, count: 1 + (i % 2)}))}, 0);
  for (let k = 0; k < 400 && (s.phase === 'hero' || s.phase === 'monster'); k++) {
    if (s.phase === 'monster') { R.monsterTurn(s); continue; }
    const pick = R.ITEMS[Math.floor(r() * R.ITEMS.length)];
    if (r() < 0.7) R.heroAction(s, {type: 'item', id: pick}); else R.heroAction(s, {type: 'attack'});
    s.items.forEach((it) => assert.ok(it.count >= 0 && it.count <= 99));
  }
  finite(s);
}
// counts from the payload are clamped and cleaned: 0 or junk counts are left out, unknown ids are ignored
const s = R.setup({...lesson(1), items: [{id: 'c:potion', count: 500}, {id: 'c:ether', count: -3}, {id: 'g:x:weapon:1', count: 1},
  {id: 'c:smoke', count: 'many'}]}, 0);
assert.deepEqual(s.items.map((x) => [x.id, x.count]), [['c:potion', 99]]);
""")


def test_fights_with_items_are_deterministic():
    check(r"""
for (let i = 0; i < 40; i++) {
  const e = {...geared(lesson(i), 1 + (i % 3)), items: ALL_ITEMS};
  const a = R.simulate(e, 3, itemsFirst), b = R.simulate(e, 3, itemsFirst);
  assert.deepEqual(a.log, b.log);
  assert.deepEqual(a.items, b.items);
  // the encounter's own counts are never touched: the battle counts down its copy
  assert.ok(e.items.every((it) => it.count === 3));
}
""")


def test_items_go_to_who_needs_them_and_are_never_spent_for_nothing():
    check(r"""
const one = (id, n = 2) => [{id, name: id, kind: id.startsWith('l:') ? 'loot' : 'consumable', count: n}];
const fresh = (items) => R.setup({...lesson(4), items}, 0);
assert.ok(R.pickParty(lesson(4)).length >= 3, 'needs a party of three');
// at full HP and MP nothing is offered: Potion, Ether, Patch and Insight are disabled with a reason, and using one
// anyway spends nothing and passes no turn
for (const [id, why] of [['c:potion', 'itemFullHp'], ['c:ether', 'itemFullMp'], ['l:patch', 'itemFullHp'], ['l:insight', 'itemFullMp']]) {
  const s = fresh(one(id)), o = R.itemOptions(s)[0];
  assert.equal(o.usable, false, id);
  assert.equal(o.why, why, id);
  const evs = R.heroAction(s, {type: 'item', id});
  assert.ok(evs.every((e) => e.t === 'say'), id + ' did something');
  assert.equal(evs[0].key, why);
  assert.equal(s.items[0].count, 2, id + ' was spent');
  assert.equal(s.active, 0, id + ' used the turn');
  assert.equal(s.phase, 'hero');
}
// Phoenix Down with nobody down: the same
{ const s = fresh(one('c:phoenix')); assert.equal(R.itemOptions(s)[0].why, 'itemNoTarget'); R.heroAction(s, {type: 'item', id: 'c:phoenix'});
  assert.equal(s.items[0].count, 2); }
// a Potion goes to the standing hero lowest on HP by share of their max, not to the one using it
{ const s = fresh(one('c:potion'));
  s.heroes[1].hp = Math.ceil(s.heroes[1].maxHp * 0.5); s.heroes[2].hp = Math.ceil(s.heroes[2].maxHp * 0.2);
  assert.equal(R.itemOptions(s)[0].target, 2);
  const before = s.heroes[2].hp;
  R.heroAction(s, {type: 'item', id: 'c:potion'});
  assert.equal(s.heroes[2].hp, before + Math.ceil(s.heroes[2].maxHp * 0.4));
  assert.equal(s.items[0].count, 1);
  // a knocked-out hero is no Potion target
  const t = fresh(one('c:potion')); t.heroes[2].ko = true; t.heroes[2].hp = 0; t.heroes[1].hp = t.heroes[1].maxHp - 1;
  assert.equal(R.itemOptions(t)[0].target, 1); }
// an Ether to the one lowest on MP; a target given that is already full is replaced by the one who needs it
{ const s = fresh(one('c:ether')); s.heroes[1].mp = 0;
  assert.equal(R.itemOptions(s)[0].target, 1);
  R.heroAction(s, {type: 'item', id: 'c:ether', target: 0});
  assert.equal(s.heroes[1].mp, Math.ceil(s.heroes[1].maxMp * 0.5));
  assert.equal(s.heroes[0].mp, s.heroes[0].maxMp); }
// Patch and Insight come alive when one hero is short
{ const s = fresh(one('l:patch')); s.heroes[2].hp -= 1; assert.equal(R.itemOptions(s)[0].usable, true); }
{ const s = fresh(one('l:insight')); s.heroes[0].mp -= 1; assert.equal(R.itemOptions(s)[0].usable, true); }
""")


def test_boss_script_hint_reward_lines_and_counts_start_fresh_each_fight():
    check(r"""
assert.match(B.LABELS['hint.l:script.boss'], /10%/);
assert.match(B.LABELS['hint.l:script'], /25%/);
assert.deepEqual(B.LABEL_VARS['hint.l:script.boss'], []);
for (const k of ['itemFullHp', 'itemFullMp', 'rewardFailed', 'more']) assert.ok(k in B.LABELS && k in B.LABEL_VARS, k);
// what onWin's promise resolves to, as the win screen words it
assert.deepEqual(R.rewardLines({gold: 32, loot: [{id: 'l:patch', name: 'Patch'}]}).map((x) => [x.kind, x.text]),
  [['gold', '+32 gold'], ['loot', 'Found: Patch']]);
assert.deepEqual(R.rewardLines(null).map((x) => x.kind), ['none']);
assert.match(R.rewardLines(null)[0].text, /Already cleared before/);
assert.deepEqual(R.rewardLines(false).map((x) => [x.kind, x.text]), [['failed', "The win wasn't saved: try it again from the map."]]);
assert.deepEqual(R.rewardLines(undefined), []);
assert.deepEqual(R.rewardLines({gold: 0, loot: []}), []);
assert.equal(R.rewardLines(false, {rewardFailed: 'X'})[0].text, 'X');
// a fight's counts start from encounter.items every time (a retry is a new fight with the counts it was given)
const e = {...lesson(3), items: ALL_ITEMS};
const a = R.simulate(e, 0, itemsFirst);
assert.ok(a.items.some((it) => it.count < 3), 'the first fight used nothing');
const b = R.setup(e, 1);
assert.deepEqual(b.items.map((it) => it.count), ALL_ITEMS.map(() => 3));
""")


STUDY = r'''
const Q = {scene: 'The tests pass alone.', question: 'Why do they fail together?', options: ['A flaky network', 'A shared token', 'A stale cache', 'A slow disk']};
const studied = (i, q = Q) => ({...lesson(i), study: q});
const answerFirst = (verdict) => (s) => (s.study && !s.study.verdict ? {type: 'answer', verdict, feedback: 'Close.', principle: 'Isolate state', answer: 'A shared token'} : R.policy(s));
'''


def test_a_study_battle_hides_the_title_and_asks_its_question():
    check(STUDY + '''
const s = R.setup(studied(3), 0);
assert.deepEqual(s.log.map((l) => l.key), ['introStudy', null, 'studyAsk']);
assert.ok(!JSON.stringify(s.log).includes('Lesson 3'), 'the title is in the log');
assert.equal(s.log[1].vars.text, 'The tests pass alone.');
assert.equal(s.log[2].vars.question, 'Why do they fail together?');
// no question: an ordinary fight; a boss never studies; a question asked later is said once
assert.equal(R.setup(lesson(3), 0).study, null);
assert.equal(R.setup({...boss(1, 1), study: Q}, 0).study, null);
const later = R.setup({...lesson(3), studyPending: true}, 0);
assert.deepEqual(later.log.map((l) => l.key), ['introStudy']);
assert.equal(R.ask(later, Q).filter((e) => e.t === 'say').length, 2);
assert.deepEqual(R.ask(later, Q), []);
assert.deepEqual(R.ask(R.setup(lesson(3), 0), {question: '  ', options: Q.options}), []);
assert.deepEqual(R.ask(R.setup(lesson(3), 0), {question: 'Why?', options: ['only one']}), []);  // a choice needs two at least
assert.deepEqual(R.ask(R.setup(lesson(3), 0), {question: 'Why?', options: ['one', ' ']}), []);
// the options show in an order of the fight's own: every one once, the same for a seed, another for another try
const order = (i, at) => R.setup(studied(i), at).study.order;
assert.deepEqual(order(3, 0).slice().sort(), [0, 1, 2, 3]);
assert.deepEqual(order(3, 0), order(3, 0));
let moved = 0;
for (let i = 0; i < 20; i++) if (JSON.stringify(order(i, 0)) !== JSON.stringify(order(i, 1))) moved++;
assert.ok(moved >= 10, 'the order barely changes between tries: ' + moved);
// the dice are the same with a question or without, when nobody answers
for (let i = 0; i < 30; i++) {
  const a = R.simulate(studied(i), 1), b = R.simulate(lesson(i), 1);
  assert.equal(a.phase, b.phase);
  assert.equal(a.round, b.round);
}
''')


def test_a_judged_answer_is_the_fights_best_move_once():
    check(STUDY + '''
for (const [verdict, hit, weak] of [['got_it', R.STUDY_HIT.got_it, R.STUDY_WEAK.got_it], ['partly', R.STUDY_HIT.partly, R.STUDY_WEAK.partly]]) {
  const s = R.setup(studied(5), 0);
  s.foe.charged = true;
  const ev = R.heroAction(s, {type: 'answer', verdict, feedback: 'Close.'});
  assert.deepEqual(ev.find((x) => x.t === 'answered'), {t: 'answered', verdict});
  assert.equal(s.foe.maxHp - s.foe.hp, Math.round(s.foe.maxHp * hit));
  assert.equal(s.weak, weak);
  assert.ok(!s.foe.charged && s.log.some((l) => l.key === 'lineBreak'));
  assert.ok(s.log.some((l) => l.key === null && l.vars.text === 'Close.'));
  assert.deepEqual(R.heroAction(s, {type: 'answer', verdict: 'got_it'}), []);  // one answer a fight
}
const n = R.setup(studied(5), 0), hp = n.foe.hp, who = n.active;
R.heroAction(n, {type: 'answer', verdict: 'not_yet'});
assert.equal(n.foe.hp, hp);
assert.ok(n.active !== who || n.phase === 'monster');  // the turn is spent
assert.equal(n.weak, 1);
for (const bad of [{type: 'answer'}, {type: 'answer', verdict: 'maybe'}]) assert.deepEqual(R.heroAction(R.setup(studied(5), 0), bad), []);
assert.deepEqual(R.heroAction(R.setup(lesson(5), 0), {type: 'answer', verdict: 'got_it'}), []);  // no question asked
// over 200 fights: a right answer wins every one, in fewer rounds than fighting alone
let plain = 0, got = 0, won = 0;
for (let i = 0; i < 200; i++) {
  plain += R.simulate(studied(i), 0).round;
  const q = R.simulate(studied(i), 0, answerFirst('got_it'));
  got += q.round; won += q.phase === 'won' ? 1 : 0;
}
assert.equal(won, 200);
assert.ok(got < plain * 0.65, got + ' vs ' + plain);
''')


def test_the_end_screen_says_what_was_understood_and_every_line_fills():
    check(STUDY + '''
const won = R.simulate(studied(2), 0, answerFirst('got_it'));
const sum = R.summary(won, B.LABELS, null, studied(2));
assert.match(sum.note, /^You understood it: it counts toward your readiness in Module 0\\.$/);
assert.equal(sum.feedback, 'Close.');
assert.equal(sum.principle, 'Isolate state');
assert.equal(sum.quote, 'A shared token');
assert.equal(R.summary(R.simulate(studied(2), 0, (st) => (st.study && !st.study.verdict ? {type: 'answer', verdict: 'got_it', back_in: 3} : R.policy(st)))).note,
  'You understood it: it counts toward your readiness in Module 0. It comes back in 3 days for another look.');
assert.equal(R.summary(R.simulate(studied(2), 0, (st) => (st.study && !st.study.verdict ? {type: 'answer', verdict: 'got_it', back_in: 10, owned: true} : R.policy(st)))).note,
  "Got it again on another day: it's yours now (Module 0). Next look in 10 days.");
assert.equal(R.summary(R.simulate(studied(2), 0, answerFirst('partly'))).note, 'Partly there: it will come back so you can try again.');
assert.equal(R.summary(R.simulate(studied(2), 0, answerFirst('not_yet'))).note, 'Not yet: it will come back.');
assert.equal(R.summary(R.simulate(studied(2), 0)).note, "You didn't answer this one: it stays open for you.");
assert.equal(R.summary(R.simulate(lesson(2), 0)).note, '');
for (let i = 0; i < 120; i++) {
  const s = R.simulate(studied(i), i % 3, answerFirst(['got_it', 'partly', 'not_yet'][i % 3]));
  for (const line of s.log) {
    const text = line.key ? B.format(B.LABELS[line.key], line.vars) : line.vars.text;
    assert.ok(line.key == null || line.key in B.LABELS, 'no label ' + line.key);
    assert.ok(!/\\{\\w+\\}|NaN|undefined/.test(text), 'unfilled: ' + text);
  }
  for (const t of Object.values(R.summary(s))) assert.ok(!/\\{\\w+\\}|NaN|undefined/.test(t), 'unfilled: ' + t);
}
''')
