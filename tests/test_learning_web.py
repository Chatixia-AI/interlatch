"""Complete category libraries and private reading history in the dependency-free browser."""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
WEB = Path(__file__).resolve().parents[1] / "src" / "interlatch" / "web" / "learning.js"
pytestmark = pytest.mark.skipif(not NODE, reason="Node is needed to exercise the browser scheduler")


def check(script):
    setup = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const P = LearningPractice, DAY = 86400000, now = Date.UTC(2026,9,9);
const item = (id, extra={}) => ({id, fingerprint:'case:'+id, kind:'fix', title:'A stale config was pushed', status:'active', confidence:'high',
  stage:'provisional', created_at:'2026-10-09', body:'The old config was pushed.', tags:['config','worker'],
  case:{scene:'A folder moved.', question:'What state was pushed?', answer:'An older snapshot.',
    clues:[], checklist:[], ruled_out:[], principle:'Check snapshot freshness.'}, ...extra});
'''
    result = subprocess.run([NODE, "-e", setup + script, str(WEB)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_reading_keeps_lessons_available_and_only_changes_revisit_badges():
    check('''
const k=item(1);
for(const outcome of ['read','later']) {
  const r=P.record(k,null,outcome,now);
  assert.equal(r.attempts.length,0);
  assert.equal(r.dueAt,now+7*DAY);
  assert.equal(P.select([k],{[P.key(k)]:r},now).length,1);
  assert.equal(P.state(k,{[P.key(k)]:r},now),'read');
  assert.equal(P.state(k,{[P.key(k)]:r},now+7*DAY),'due');
  assert.equal(P.select([k],{[P.key(k)]:r},now+7*DAY).length,1);
}
const wrong=P.record(k,null,'again',now);
assert.equal(wrong.dueAt,now+3*DAY);
assert.equal(wrong.attempts[0].outcome,'again');
const sooner=P.record(k,null,'soon',now);
assert.equal(sooner.dueAt,now+3*DAY);
assert.equal(sooner.attempts.length,0);
assert.equal(sooner.recalls,0);
''')


def test_later_success_extends_interval_even_after_the_explanation_is_read():
    check('''
const k=item(1), first=P.record(k,null,'recalled',now);
const exposure=P.record(k,first,'read',now+7*DAY);
const second=P.record(k,exposure,'recalled',now+7*DAY);
assert.equal(second.dueAt,now+21*DAY);
assert.equal(second.attempts.length,2);
assert.equal(second.exposures,1);
assert.equal(P.record(k,second,'again',now+21*DAY).recalls,0);
''')


def test_library_includes_every_new_due_and_read_lesson_without_a_batch_limit():
    check('''
const items=Array.from({length:12},(_,i)=>item(i+1)), history={};
items.slice(0,6).forEach(k=>history[P.key(k)]=P.record(k,null,'soon',now-20*DAY));
items.slice(6,10).forEach(k=>history[P.key(k)]=P.record(k,null,'read',now));
const batch=P.select(items,history,now);
assert.equal(batch.length,12);
assert.equal(batch.filter(k=>P.state(k,history,now)==='due').length,6);
assert.equal(batch.filter(k=>P.state(k,history,now)==='read').length,4);
assert.equal(batch.filter(k=>P.state(k,history,now)==='new').length,2);
assert.deepEqual(batch.map(P.key),P.select(items,{},now).map(P.key));
const large=Array.from({length:2100},(_,i)=>item(i+100));
assert.equal(P.select(large,{},now).length,2100);
''')


def test_reanalysis_keeps_history_by_fingerprint_but_changed_material_is_revisited():
    check('''
const k=item(1), r=P.record(k,null,'recalled',now);
const replacement={...k,id:999};
assert.equal(P.state(replacement,{[P.key(k)]:r},now),'read');
replacement.case={...k.case,answer:'A corrected diagnosis.'};
assert.equal(P.select([replacement],{[P.key(k)]:r},now).length,1);
assert.equal(P.state(replacement,{[P.key(k)]:r},now),'new');
assert.equal(P.record(replacement,r,'read',now).attempts.length,0);
''')


def test_hypotheses_and_references_are_not_scored_as_practice():
    check('''
const cases=[item(1),item(2,{kind:'fact'}),item(3,{confidence:'low'}),item(4,{stage:'wip'}),
  item(5,{case:null,body:'  '}),item(6,{status:'dismissed'})];
assert.deepEqual(P.select(cases,{},now).map(k=>k.id),[1]);
assert.equal(P.related(item(1),[item(1),item(2,{tags:['other'],case:{...item(1).case,principle:'Different'}})]).length,0);
''')


def test_private_history_is_separate_for_each_viewer_and_survives_blocked_storage():
    check('''
const stored=new Map();
global.ME={viewer:{id:1}};
global.t=x=>x; global.toast=()=>{};
global.localStorage={getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v)};
const first=learningStore(); first.data.notes['case:1']={text:'Private note'};
assert.equal(first.save(),true);
ME.viewer.id=2;
assert.equal(learningStore().data.notes['case:1'],undefined);
ME.viewer.id=1;
learningMemory.clear();
assert.equal(learningStore().data.notes['case:1'].text,'Private note');
ME.viewer.id=3;
localStorage.getItem=()=>{throw Error('blocked')};
localStorage.setItem=()=>{throw Error('blocked')};
const blocked=learningStore();
assert.equal(blocked.available,false);
blocked.data.notes['case:1']={text:'This tab only'};
assert.equal(blocked.save(),false);
assert.equal(learningStore().data.notes['case:1'].text,'This tab only');
''')


def test_legacy_note_does_not_call_a_diagnosis_a_checklist():
    check('''
global.t=x=>x;
global.location={origin:'http://localhost',pathname:'/'};
const draft=learningNote(item(1));
assert.ok(draft.includes('Explanation\\nThe old config was pushed.'));
assert.ok(!draft.includes('Checks for next time'));
const grounded=learningNote(item(2,{case:{...item(1).case,checklist:['Compare timestamps.']}}));
assert.ok(grounded.includes('Checks for next time\\n- Compare timestamps.'));
''')


def test_interests_prioritize_relevant_concepts_over_recent_incidental_cases():
    check('''
const api=item(1,{learning_topics:['api'],created_at:'2026-09-09'});
const shell=item(2,{learning_topics:[],created_at:'2026-10-09'});
const backend=item(3,{learning_topics:['backend'],created_at:'2026-10-08'});
const interests=[{topic:'api',score:3},{topic:'backend',score:1}];
assert.deepEqual(P.select([shell,backend,api],{},now,interests).map(k=>k.id),[1,3,2]);
const read=P.record(api,null,'read',now);
assert.equal(P.select([api],{[P.key(api)]:read},now,interests).length,1);
''')


def test_all_existing_lesson_kinds_need_no_invented_case_evidence():
    check('''
const lesson=item(1,{kind:'learning',title:'API versioning',body:'Keep old clients compatible.',case:null,learning_topics:['api']});
for(const kind of ['fix','gotcha','decision','learning','pattern']) {
  assert.equal(P.select([{...lesson,kind}],{},now).length,1);
}
const selected=P.select([lesson],{},now);
assert.equal(selected.length,1);
assert.equal(selected[0].body,lesson.body);
assert.deepEqual(selected[0].case,{});  // nothing stands in for material the analysis never found
assert.equal(P.select([{...lesson,confidence:'low'}],{},now).length,0);
''')


def test_topic_metadata_does_not_reset_recall_history():
    check('''
const k=item(1), read=P.record(k,null,'read',now);
const categorized={...k,case:{...k.case,topics:['backend']},learning_topics:['backend']};
assert.equal(P.state(categorized,{[P.key(k)]:read},now),'read');
''')


def test_diagrams_come_only_from_the_analysis_and_keep_reading_history():
    check('''
global.t=x=>x;
// no diagram the analysis drew, no diagram: no boxes made of the page's headings, no stock picture for a keyword
assert.equal(learningVisualData(item(1,{learning_topics:['api','data_modeling']})),null);
assert.equal(learningVisualData(item(2,{kind:'learning',case:null,title:'Data modeling',body:'A foreign key relates entities.'})),null);
assert.equal(learningVisualData(item(3,{kind:'learning',case:null,title:'React state',body:'The parent owns state and passes it to children.'})),null);
assert.equal(learningVisualData(item(5,{case:{...item(1).case,visual:{type:'flow',title:'One part',nodes:[{id:'a',label:'A'}],edges:[]}}})),null);
const owner={type:'relationship',title:'Who owns the state',nodes:[{id:'p',label:'Page'},{id:'a',label:'List'},{id:'b',label:'Filter'}],
  edges:[{from:'p',to:'a',label:'items'},{from:'p',to:'b',label:'query'}]};
assert.equal(learningVisualData(item(6,{case:{visual:owner}})).branch,true);
const diagram={type:'comparison',title:'Two storage choices',nodes:[
  {id:'one',label:'SQLite',detail:'Local writes.'},{id:'two',label:'Postgres',detail:'Shared writes.'}],edges:[]};
const k=item(4), read=P.record(k,null,'read',now), enriched={...k,case:{...k.case,visual:diagram}};
assert.equal(learningVisualData(enriched).title,diagram.title);
assert.equal(P.state(enriched,{[P.key(k)]:read},now),'read');
''')


def test_category_lists_cover_all_lessons_and_prioritize_interests_without_duplicates():
    check('''
const api=Array.from({length:8},(_,i)=>item(i+1,{learning_topics:['api','api']}));
const backend=Array.from({length:12},(_,i)=>item(i+10,{learning_topics:['backend']}));
const shared=item(30,{learning_topics:['api','backend']}), other=item(31,{learning_topics:[]});
const items=P.select([...api,...backend,shared,other,{...api[0],id:99}],{},now);
assert.equal(items.length,22);
const groups=P.categories(items,[{topic:'api',score:3}]);
assert.equal(groups[0].id,'api');
assert.equal(groups[0].items.length,9);
assert.equal(groups.find(g=>g.id==='backend').items.length,13);
assert.deepEqual(groups.find(g=>g.id==='other').items.map(P.key),[P.key(other)]);
assert.equal(new Set(groups.flatMap(g=>g.items.map(P.key))).size,items.length);
const history=Object.fromEntries(items.map(k=>[P.key(k),P.record(k,null,'read',now)]));
assert.equal(P.categories(P.select(items,history,now)).flatMap(g=>g.items).length,23);
''')
