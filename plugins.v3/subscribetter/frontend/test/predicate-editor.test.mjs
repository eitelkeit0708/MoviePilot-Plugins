import test from 'node:test';
import assert from 'node:assert/strict';
import {
  astToDraft,draftToAst,setGroupOperator,setConditionOperator,
  addCondition,duplicateNode,durationDisplay,durationMinutes,removeNode,restoreSnapshot,sizeBytes,sizeDisplay,wrapNode,
  predicateSummary,referencedFields,validateDraft
} from '../src/predicate-editor.mjs';

const sample={all:[
  {gt:['size',0]},
  {le:['size',21474836480]},
  {any:[{regex:['title','^WEB\\.DL$']},{regex:['title','line1\nline2']}]}
]};

test('CE-T01 untouched expression round trips byte for byte',()=>{
  const draft=astToDraft(sample);
  assert.deepEqual(draftToAst(draft),sample);
  assert.match(predicateSummary(draft),/资源大小大于 0 B/);
  assert.match(predicateSummary(draft),/20 GiB/);
  assert.match(predicateSummary(draft),/（标题匹配正则 [\s\S]+，或者 标题匹配正则 [\s\S]+）/);
});

test('CE-T02 and CE-T03 changing group relation never wraps or replaces children',()=>{
  const draft=astToDraft(sample),rootId=draft.id,ids=draft.children.map(row=>row.id);
  const any=setGroupOperator(draft,rootId,'any');
  assert.deepEqual(any.children.map(row=>row.id),ids);
  assert.deepEqual(draftToAst(any),{any:sample.all});
  const all=setGroupOperator(any,rootId,'all');
  assert.deepEqual(all.children.map(row=>row.id),ids);
  assert.deepEqual(draftToAst(all),sample);
});

test('CE-T04 compatible comparison change preserves field value and unit',()=>{
  const draft=astToDraft({gt:['size',21474836480]}),id=draft.id;
  draft.unit='GiB';
  const changed=setConditionOperator(draft,id,'le');
  assert.equal(changed.field,'size');
  assert.equal(changed.value,21474836480);
  assert.equal(changed.unit,'GiB');
  assert.deepEqual(draftToAst(changed),{le:['size',21474836480]});
});

test('CE-T07 new rows remain incomplete and cannot serialize',()=>{
  const draft=addCondition(astToDraft({all:[{literal:true}]}),null);
  const errors=validateDraft(draft);
  assert.equal(errors.length,1);
  assert.match(errors[0].message,/请填写/);
  assert.throws(()=>draftToAst(draft),/INCOMPLETE_PREDICATE/);
});

test('CE-T08 binary and decimal unit switches keep exact bytes',()=>{
  const bytes=21474836480;
  assert.equal(sizeDisplay(bytes,'GiB'),'20');
  assert.equal(sizeDisplay(bytes,'GB'),'21.47483648');
  assert.equal(sizeBytes(sizeDisplay(bytes,'GB'),'GB'),bytes);
  assert.equal(sizeBytes(sizeDisplay(bytes,'B'),'B'),bytes);
});

test('publish age units and new contains-text input compile to the existing backend AST',()=>{
  assert.equal(durationDisplay(2880,'天'),'2');
  assert.equal(durationDisplay(90,'小时'),'1.5');
  assert.equal(durationMinutes('1.5','小时'),90);
  const draft=addCondition(astToDraft({all:[]}),null),row=draft.children[0];
  row.value='WEB-DL [1080p]';row.incomplete=false;
  assert.deepEqual(draftToAst(draft),{all:[{regex:['title','WEB-DL \\[1080p\\]']}]});
});

test('CE-T09 invalid and unsafe size input is rejected rather than rounded',()=>{
  for(const value of ['', 'NaN', 'Infinity', '-1', '0.1'])assert.throws(()=>sizeBytes(value,'B'));
  assert.throws(()=>sizeBytes('9007199.254740993','GB'),/SAFE_INTEGER/);
});

test('CE-T10 regex text including anchors escapes and newlines is unchanged',()=>{
  const expression={regex:['text','^(?:WEB\\-DL|BluRay)\nS\\d{2}$']};
  assert.deepEqual(draftToAst(astToDraft(expression)),expression);
});

test('CE-T18 delete and undo restore stable IDs and values',()=>{
  const original=astToDraft(sample),middle=original.children[1],snapshot=structuredClone(original);
  const removed=removeNode(original,middle.id);
  assert.equal(removed.children.some(row=>row.id===middle.id),false);
  const restored=restoreSnapshot(removed,snapshot);
  assert.deepEqual(restored.children.map(row=>row.id),snapshot.children.map(row=>row.id));
  assert.deepEqual(draftToAst(restored),sample);
});

test('copy and wrap are explicit operations and keep the original condition intact',()=>{
  const original=astToDraft({all:[{gt:['size',1]},{regex:['title','WEB']}]}),target=original.children[0];
  const copied=duplicateNode(original,target.id);
  assert.equal(copied.children.length,3);
  assert.deepEqual(draftToAst(copied),{all:[{gt:['size',1]},{gt:['size',1]},{regex:['title','WEB']}]});
  assert.notEqual(copied.children[0].id,copied.children[1].id);
  const wrapped=wrapNode(original,target.id);
  assert.equal(wrapped.children[0].kind,'group');
  assert.equal(wrapped.children[0].children[0].id,target.id);
  assert.deepEqual(draftToAst(wrapped),{all:[{all:[{gt:['size',1]}]},{regex:['title','WEB']}]});
});

test('CE-T21 advanced literals sets references booleans and null round trip unchanged',()=>{
  const expressions=[
    {literal:false},{registered:'SharedRule'},{eq:['hq',true]},{eq:['group',null]},
    {in:['media_type',['movie','tv']]},{intersects:['genre_ids',[16,99]]},
    {not:{all:[{ne:['seeders',0]},{eq:['description',{raw:'legacy'}]}]}}
  ];
  for(const expression of expressions)assert.deepEqual(draftToAst(astToDraft(expression)),expression);
});

test('incompatible operator change keeps original value as an explicit invalid draft',()=>{
  const draft=astToDraft({gt:['size',21474836480]});
  const changed=setConditionOperator(draft,draft.id,'regex');
  assert.equal(changed.value,21474836480);
  assert.equal(validateDraft(changed)[0].code,'INCOMPATIBLE_VALUE');
  assert.throws(()=>draftToAst(changed),/INCOMPLETE_PREDICATE/);
});

test('manual samples request the fields referenced by the condition tree',()=>{
  assert.deepEqual(referencedFields({all:[{gt:['size',0]},{regex:['text','WEB']},{registered:'Shared'}]}),
    ['description','labels','size','subtitle_description','title']);
});
