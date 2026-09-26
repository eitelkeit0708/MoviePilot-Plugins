import test from 'node:test';
import assert from 'node:assert/strict';
import {createClient, createReadGate, prepareSave, applyBody, id} from '../src/client.mjs';
import {saveFollowup,readFollowup,clearFollowup} from '../src/client.mjs';

test('save continuation contains references only and is scoped to one plugin instance',()=>{
 saveFollowup('first',{receipt:'p',digest:'d',config:{password:'do not retain'}});
 assert.deepEqual(readFollowup('first'),{receipt:'p',digest:'d'});assert.equal(readFollowup('other'),null);
 clearFollowup('first');assert.equal(readFollowup('first'),null);
});
test('operation IDs use secure random bytes without secure-context-only randomUUID',()=>{
  assert.match(id(),/^[a-f0-9]{32}$/);assert.notEqual(id(),id());
});

test('instance paths and bare/envelope responses, including false and data-shaped DTO', async () => {
  const calls=[]; let reply={data:{state:'domain'},state:'AVAILABLE'};
  const api={get:async(...args)=>{calls.push(args);return reply}};
  const client=createClient(api,'Clone 甲','SubscriBetter');
  assert.deepEqual(await client.get('/tasks'),reply);
  assert.equal(calls[0][0],'plugin/Clone%20%E7%94%B2/tasks');
  reply={success:true,message:'ok',data:{revision:7}};
  assert.deepEqual(await client.get('/configuration'),{revision:7});
  reply={success:true,message:'domain value',data:{field:1},state:'AVAILABLE'};
  assert.deepEqual(await client.get('/configuration'),reply);
  reply={success:false,message:'rejected',data:null};
  await assert.rejects(client.get('/configuration'));
});
test('replacement and unmount discard delayed reads even when adapter ignores abort', async () => {
  const gate=createReadGate();const a=gate.begin();const b=gate.begin();
  assert.equal(a.current(),false);assert.equal(a.signal.aborted,true);assert.equal(b.current(),true);
  gate.close();assert.equal(b.current(),false);assert.equal(b.signal.aborted,true);
});
test('one preflight emits exact normalized safe object once; no PUT or postemit read', async () => {
  const calls=[];const emitted=[];const normalized={enabled:false,dry_run:true,configuration_receipt:'receipt'};
  const client={post:async(path,body)=>{calls.push({path,body});return {valid:true,config:normalized,errors:[]}}};
  const result=await prepareSave(client,{revision:7,digest:'a'.repeat(64)}, {enabled:false}, x=>emitted.push(x));
  assert.equal(result,normalized);assert.deepEqual(emitted,[normalized]);assert.equal(calls.length,1);
  assert.deepEqual(calls[0],{path:'/configuration/preview',body:{revision:7,digest:'a'.repeat(64),mode:'replace',patch:{enabled:false}}});
  await assert.rejects(prepareSave({post:async()=>({valid:false,errors:['bad']})},{revision:7,digest:'a'},{},()=>assert.fail()));
});
test('apply scope cannot expand or mutate immutable receipt', () => {
  const receipt={preview_id:'p',preview_digest:'d',objects:{ids:['1']},permissions:{cleanup:false}};
  assert.deepEqual(applyBody(receipt,'op'),{preview_id:'p',preview_digest:'d',operation_id:'op',confirm:true});
  assert.deepEqual(receipt.objects,{ids:['1']});
});
