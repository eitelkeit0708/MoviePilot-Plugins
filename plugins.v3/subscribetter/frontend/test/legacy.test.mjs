import test from 'node:test';import assert from 'node:assert/strict';
import {rawConfig,canonical,rawDigest,saveLegacy} from '../src/legacy.mjs';
import fixture from './fixtures/legacy-native-json.json' with {type:'json'};
test('production helper equals independent Python canonical and digest fixture',async()=>{
 const config=rawConfig(fixture.raw);assert.equal(canonical(config),fixture.expected);assert.equal(await rawDigest(config),fixture.sha256);config.enabled=false;assert.equal(canonical(config),fixture.patched);
});
test('raw native roundtrip preserves Python number tokens, numeric key ordering and Unicode escaping',()=>{
 const wire='{"2": 1.0, "10": -0.0, "big": 9007199254740993, "文字": "中😀\\u007f", "nested": [1e-07, 1e+20]}';
 assert.equal(canonical(rawConfig(wire)),'{"10":-0.0,"2":1.0,"big":9007199254740993,"nested":[1e-07,1e+20],"\\u6587\\u5b57":"\\u4e2d\\ud83d\\ude00\\u007f"}');
});
test('selected old Save preserves unrelated private bytes and verifies before/after full digests',async()=>{
 let body='{"recognize":true,"chat_enabled":true,"token":"FICTIONAL_PRIVATE","budget":1.0}';const original=rawConfig(body);const before=await rawDigest(original);original.recognize=false;const after=await rawDigest(original);const calls=[];
 const api={get:async(p,o)=>{calls.push(['get',p,o.responseType]);return body},put:async(p,b)=>{calls.push(['put',p]);body=b;return {success:true,message:'ok',data:null}}};
 const step={instance_id:'Old',before:{recognize:true},changes:{recognize:false},before_digest:before,after_digest:after,restore_digest:before};
 const result=await saveLegacy(api,{instance_id:'Old',changes:{recognize:false},expected_digest:before},step);
 assert.equal(result.state,'CONFIG_READBACK');assert.equal(calls.filter(c=>c[0]==='put').length,1);assert.match(body,/"budget":1\.0/);assert.match(body,/"chat_enabled":true/);assert.match(body,/FICTIONAL_PRIVATE/);assert.ok(!JSON.stringify(result).includes('FICTIONAL_PRIVATE'));
 await assert.rejects(saveLegacy(api,{instance_id:'Old',changes:{recognize:false},expected_digest:before},step));assert.equal(calls.filter(c=>c[0]==='put').length,1);
});

test('HTTP native hashing does not require Web Crypto subtle',async()=>{
 const descriptor=Object.getOwnPropertyDescriptor(globalThis,'crypto');
 Object.defineProperty(globalThis,'crypto',{configurable:true,value:{getRandomValues(){throw Error('hash must not need random')}}});
 try{assert.equal(await rawDigest(rawConfig(fixture.raw)),fixture.sha256)}finally{Object.defineProperty(globalThis,'crypto',descriptor)}
});
