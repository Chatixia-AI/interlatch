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
// every number in a state is finite
function finite(state) {
  const nums = [state.round, state.foe.hp, state.foe.maxHp, state.foe.power, state.foe.d];
  for (const h of state.heroes) nums.push(h.hp, h.maxHp, h.mp, h.maxMp, h.atk, h.level, ...Object.values(h.skills));
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
