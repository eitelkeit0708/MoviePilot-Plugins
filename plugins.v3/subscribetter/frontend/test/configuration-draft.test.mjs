import test from 'node:test';
import assert from 'node:assert/strict';
import {contract} from '../src/schema.mjs';
import {mergeDraft,planDraft} from '../src/configuration-draft.mjs';

test('scoped save removes locks, retains other drafts, and merges unrelated saved changes', () => {
  const base = {policy:{locks:{resolution:2160, picture:2}}, enabled:false};
  const draft = {policy:{locks:{picture:2}}, enabled:true};
  const saved = {policy:{locks:{resolution:2160, picture:1}}, enabled:false};
  const result = mergeDraft(base, draft, saved, ['policy']);
  assert.deepEqual(result.config, {policy:{locks:{picture:1}}, enabled:false});
  assert.deepEqual(result.conflicts, []);
});

test('nested policy save preserves unsaved plan and other plans; discarding outer draft keeps saved policy', () => {
  const base = {policy:{templates:{drama:{dimensions:['resolution','audio']}}}, destination_templates:[{id:'a',save_path:'/old'},{id:'b',save_path:'/other'}]};
  const outer = structuredClone(base); outer.destination_templates[0].save_path = '/new';
  const saved = structuredClone(base); saved.policy.templates.drama.dimensions = ['audio','resolution']; saved.destination_templates[1].save_path = '/remote';
  const rebased = mergeDraft(base, outer, saved, Object.keys(outer), {keepLocal:true});
  assert.equal(rebased.config.destination_templates[0].save_path, '/new');
  assert.equal(rebased.config.destination_templates[1].save_path, '/remote');
  assert.deepEqual(rebased.config.policy, saved.policy);
  assert.deepEqual(rebased.conflicts, []);
  assert.equal(saved.destination_templates[0].save_path, '/old');
  assert.deepEqual(saved.policy.templates.drama.dimensions, ['audio','resolution']);
});

test('concurrent edits and delete-versus-edit are conflicts, never silent overwrites', () => {
  const base = {policy:{locks:{resolution:2160}}, plans:[{id:'a',path:'/old'}]};
  const local = {policy:{locks:{}}, plans:[]};
  const remote = {policy:{locks:{resolution:1080}}, plans:[{id:'a',path:'/new'}]};
  const result = mergeDraft(base, local, remote);
  assert.deepEqual(result.conflicts, [['policy','locks','resolution'],['plans','a']]);
  assert.deepEqual(result.config, remote);
  assert.deepEqual(mergeDraft(base, local, remote, Object.keys(local), {keepLocal:true}).config, local);
});

test('saving or discarding one scheme preserves the other scheme draft and unrelated library choices',()=>{
 const base=structuredClone(contract.defaults);base.destination_templates=[{id:'a',category_id:'tv',organized_rule:'r-a',save_path:'/a'},{id:'b',category_id:'movie',organized_rule:'r-b',save_path:'/b'}];base.delivery={rules:[{id:'r-a',cloud_scope_id:'a',local_root:'/organized/a'},{id:'r-b',cloud_scope_id:'b',local_root:'/organized/b'}],cloud_scopes:{a:{root:'/115'},b:{root:'/115'}},mappings:[{id:'ma',cloud_scope_id:'a',emby_service:'Emby',library_id:'one'},{id:'mb',cloud_scope_id:'b',emby_service:'Emby',library_id:'two'}],libraries:{Emby:['one','two']},policy_bindings:{},classification_revision:1};
 const draft=structuredClone(base);draft.destination_templates[0].save_path='/a-new';draft.destination_templates[1].save_path='/b-new';draft.delivery.rules[1].local_root='/b-other';draft.delivery.libraries.Emby.push('other');
 const saved=planDraft(base,draft,'a');assert.equal(saved.destination_templates.find(p=>p.id==='a').save_path,'/a-new');assert.equal(saved.destination_templates.find(p=>p.id==='b').save_path,'/b');assert.equal(saved.delivery.rules.find(r=>r.id==='r-b').local_root,'/organized/b');assert.ok(!saved.delivery.libraries.Emby.includes('other'));
 const retained=mergeDraft(base,draft,saved).config;assert.equal(retained.destination_templates.find(p=>p.id==='b').save_path,'/b-new');
 const discarded=planDraft(draft,base,'a');assert.equal(discarded.destination_templates.find(p=>p.id==='a').save_path,'/a');assert.equal(discarded.destination_templates.find(p=>p.id==='b').save_path,'/b-new');assert.ok(discarded.delivery.libraries.Emby.includes('other'));
});

test('RT02 non-overlapping edits merge and equal same-field edits do not create conflicts', () => {
  const base = {a:1, b:1};
  assert.deepEqual(mergeDraft(base, {a:2, b:1}, {a:1, b:3}), {config:{a:2, b:3}, conflicts:[], changed:[['a']]});
  assert.deepEqual(mergeDraft(base, {a:2, b:1}, {a:2, b:1}).conflicts, []);
});

test('shared cloud scope does not make another scheme mapping part of this save or discard',()=>{
 const base=structuredClone(contract.defaults);base.destination_templates=[{id:'a',category_id:'tv',organized_rule:'r-a',save_path:'/a'},{id:'b',category_id:'movie',organized_rule:'r-b',save_path:'/b'}];base.delivery={rules:[{id:'r-a',cloud_scope_id:'shared',local_root:'/organized/a'},{id:'r-b',cloud_scope_id:'shared',local_root:'/organized/b'}],cloud_scopes:{shared:{root:'/115'}},mappings:[{id:'ma',cloud_scope_id:'shared',emby_service:'Emby',library_id:'one',emby_prefix:'/a'},{id:'mb',cloud_scope_id:'shared',emby_service:'Emby',library_id:'two',emby_prefix:'/b'}],libraries:{Emby:['one','two']},policy_bindings:{},classification_revision:1};
 const draft=structuredClone(base);draft.destination_templates[0].save_path='/a-new';draft.delivery.mappings.find(row=>row.id==='mb').emby_prefix='/b-draft';
 const saved=planDraft(base,draft,'a');assert.equal(saved.destination_templates[0].save_path,'/a-new');assert.equal(saved.delivery.mappings.find(row=>row.id==='mb').emby_prefix,'/b');
 const discarded=planDraft(draft,base,'a');assert.equal(discarded.destination_templates[0].save_path,'/a');assert.equal(discarded.delivery.mappings.find(row=>row.id==='mb').emby_prefix,'/b-draft');
 const explicit=planDraft(base,draft,'a',{mappings:['mb']});assert.equal(explicit.delivery.mappings.find(row=>row.id==='mb').emby_prefix,'/b-draft');
});
