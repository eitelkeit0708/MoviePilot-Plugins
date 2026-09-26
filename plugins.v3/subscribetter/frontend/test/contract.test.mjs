import test from 'node:test';import assert from 'node:assert/strict';
import {contract,bodySchema,initial,validate,clean} from '../src/schema.mjs';
import {views} from '../src/catalog.mjs';import {pathFor} from '../src/client.mjs';
test('all domain reads/actions use actual mounted paths and complete Config retains strict safe groups',()=>{
 assert.equal(views.length,9);
 for(const view of views){for(const r of view.resources)assert.ok(contract.paths[r[1]]?.get,r[1]);for(const a of view.actions)assert.ok(contract.paths[a[1]]?.post?.requestBody,a[1]);}
 assert.deepEqual(validate(contract.schemas.Config,contract.defaults),[]);
 assert.equal(Object.keys(clean(contract.schemas.Config,{...contract.defaults,credential:'PRIVATE',preview:{}})).length,Object.keys(contract.defaults).length);
 assert.ok(!Object.hasOwn(clean(contract.schemas.Config,{...contract.defaults,credential:'PRIVATE'}),'credential'));
 assert.deepEqual(Object.values(contract.defaults.permissions),[false,false,false,false,false]);
});
test('configured Unicode/space/slash paths retain exact names and explicit numeric validation',()=>{
 assert.equal(pathFor('/policies/{category_id}',{category_id:'动漫 / 喜剧'}),'/policies/%E5%8A%A8%E6%BC%AB%20%2F%20%E5%96%9C%E5%89%A7');
 assert.equal(pathFor('/downloads/{downloader}/{infohash}/reconcile',{downloader:'subscriBetter qB test',infohash:'a'.repeat(40)}),'/downloads/subscriBetter%20qB%20test/'+ 'a'.repeat(40)+'/reconcile');
 assert.ok(validate({type:'integer',minimum:0},'9').length);assert.ok(validate({type:'number'},NaN).length);assert.deepEqual(validate({type:'number'},0),[]);
});

test('nested configuration errors use human field names and item positions',()=>{
 const errors=validate({type:'object',properties:{delivery:{type:'object',properties:{mappings:{type:'array',items:{type:'object',properties:{cloud_scope_id:{type:'string',minLength:1}}}}}}}},{delivery:{mappings:[{cloud_scope_id:''}]}});
 assert.equal(errors.length,1);assert.ok(errors[0].includes('两段路径映射（第 1 项）'));assert.ok(errors[0].includes('请填写此项'));assert.ok(!errors[0].includes('cloud_scope_id'));
});
