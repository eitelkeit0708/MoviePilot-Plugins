import test from 'node:test';import assert from 'node:assert/strict';
import {comparisonGroups,downloadPercent,processingNext} from '../src/media.mjs';
import {planIssues,sharedChanges} from '../src/plan-flow.mjs';
import {contract} from '../src/schema.mjs';
test('episode grouping preserves different reasons, seasons and incomplete coverage; progress refuses missing counters',()=>{
 const item=(episode,reason='QUALITY_UPGRADE',season=1)=>({target_key:JSON.stringify(['电视剧','tmdb','1',season,'',episode]),status:'ALLOW',reason,dimensions:['picture']});
 const groups=comparisonGroups([item(2),item(1),item(3),item(7),item(8,'MISSING')]);assert.equal(groups[0].label,'第 1–3、7 集');assert.equal(groups[1].targets.length,1);assert.equal(groups[0].targets.length,4);
 assert.equal(comparisonGroups([item(1),item(2,'QUALITY_UPGRADE',2)])[0].label,'第 1 集、第 2 集');
 assert.equal(downloadPercent({downloaded_bytes:25,total_bytes:100}),25);for(const d of [{downloaded_bytes:null,total_bytes:100},{downloaded_bytes:110,total_bytes:100},{downloaded_bytes:0,total_bytes:0}])assert.equal(downloadPercent(d),null);
 assert.match(processingNext({next_step:'RECONCILE',next_at:'2099-01-01'},{ordinary_work_active:true}),/外部结果尚未确认/);
 assert.match(processingNext({next_at:'2099-01-01'},{ordinary_work_active:true,paused:true}),/恢复追踪/);
});
test('step validation blocks missing dependencies and reports only actually changed shared fields',()=>{
 const config=structuredClone(contract.defaults);config.destination_templates=[{id:'A',category_id:'tv',organized_rule:'r'},{id:'B',category_id:'tv',organized_rule:'r'}];config.delivery={cloud_scopes:{s:{root:'/115',cd2_plugin:'cd2',p115_plugin:'p115',allowed_prefixes:['/115/test']}},rules:[{id:'r',cloud_scope_id:'s',local_root:'/downloads'}],mappings:[],policy_bindings:{}};
 assert.ok(planIssues(config,'A',0).some(e=>e.label==='下载器'));assert.equal(planIssues(config,'A',1).length,0);
 const changed=structuredClone(config);changed.delivery.rules[0].local_root='/new';assert.deepEqual(sharedChanges(config,changed,'A').map(i=>({fields:i.fields,plans:i.plans})),[{fields:['local_root'],plans:['B']}]);
 assert.deepEqual(sharedChanges(config,config,'A'),[]);changed.delivery.cloud_scopes.s.allowed_prefixes=['/other'];assert.ok(planIssues(changed,'A',1).some(e=>e.message.includes('根目录内')));
});
