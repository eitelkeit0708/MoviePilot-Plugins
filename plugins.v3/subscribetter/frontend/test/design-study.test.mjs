import test from 'node:test';
import assert from 'node:assert/strict';
import {media,highlights,exampleScheme,blankScheme,issues,mappingKey,samples,simulatePathCheck} from '../dev/study/model.mjs';

test('design study: exact episode highlights, bounded draft checks and stable scheme identity',()=>{
 assert.deepEqual(highlights(media[0]).map(u=>u.number),[5,1,6]);
 assert.equal(media[0].episodes.filter(u=>u.percent!==null).length,1);
 assert.equal(highlights(media[1]).length,0);
 const blank=blankScheme();assert.ok(issues(blank,0).some(e=>e.key==='name'));
 const renamed={...exampleScheme,name:'自定义中文名称'};assert.equal(renamed.id,exampleScheme.id);
 for(let step=0;step<4;step++)assert.deepEqual(issues(renamed,step),[]);
 assert.ok(issues({...renamed,staging:renamed.incoming+'/staging'},3).length);
 const result=simulatePathCheck(renamed,samples[0]);assert.equal(result.ok,true);assert.equal(result.local,'/media/strm/series/GATE24/S01E01.strm');
 assert.equal(simulatePathCheck({...renamed,emby:'/strm/elsewhere'},samples[0]).ok,false);
 assert.equal(simulatePathCheck(renamed,{...samples[0],content:'http://different.example/file'}).ok,false);
 assert.notEqual(mappingKey(renamed),mappingKey({...renamed,local:'/changed'}));
 assert.equal(mappingKey(renamed),mappingKey({...renamed,name:'Only display name changes'}));
});
