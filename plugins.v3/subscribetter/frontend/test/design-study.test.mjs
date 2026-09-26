import test from 'node:test';
import assert from 'node:assert/strict';
import {media,highlights,exampleScheme,blankScheme,issues,mappingKey,samples,simulatePathCheck} from '../dev/study/model.mjs';
import * as study from '../dev/study/model.mjs';

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

test('review cases use declared differences, trustworthy totals and local STRM prefixes',()=>{
 assert.equal(typeof study.versionChange,'function');
 const audio=study.episodes.find(u=>u.change?.changes.length===1&&u.change.changes[0].dimension==='audio');
 assert.equal(study.versionChange(audio).before,'DDP');
 assert.equal(study.versionChange(audio).after,'无损音频');
 const picture=study.episodes.find(u=>u.change?.changes.length===1&&u.change.changes[0].dimension==='picture');
 assert.equal(study.versionChange(picture).before,'SDR');
 assert.equal(study.versionChange(picture).after,'Dolby Vision');
 const evidence=study.episodes.find(u=>u.change?.kind==='evidence');
 assert.equal(study.versionChange(evidence).after,'发布者明确标注');
 assert.equal(evidence.current.special_zh_subtitles,evidence.target.special_zh_subtitles);
 const acquire=study.episodes.find(u=>u.change?.kind==='acquire');
 assert.equal(study.versionChange(acquire).before,'尚未在库');
 assert.match(study.versionChange(study.episodes.find(u=>u.phase==='PRESENT')).label,/已收录/);
 assert.equal(study.versionChange({...audio,change:{kind:'quality',changes:[]}}).before,'变化依据未取得');
 assert.equal(study.collectionText({present:6,known:6,seasonTotal:null}),'已收录 6 集 · 总集数待确认');
 assert.equal(study.collectionText({present:6,seasonTotal:6,totalReliable:true}),'6 / 6 集在库');
 const mapped=simulatePathCheck(exampleScheme,samples[0]);
 assert.ok(mapped.content.startsWith('/vol3/1000/docker/clouddrive2/CloudNAS/CloudDrive/115/'));
 assert.equal(mapped.remote,'/115/series/GATE24/S01E01.mkv');
 assert.ok(issues({...exampleScheme,playback:'relative/path'},2).some(e=>e.key==='playback'));
 assert.ok(study.policies.some(p=>p.id===exampleScheme.policy&&p.revision===exampleScheme.policyRevision));
 assert.ok(issues({...exampleScheme,policy:'missing'},0).some(e=>e.key==='policy'));
 assert.ok(issues({...exampleScheme,policyRevision:'stale'},0).some(e=>e.key==='policy'));
});


test('multi-dimension upgrades retain every supplied change and keep reductions distinct',()=>{
 const multi=study.versionChange(study.episodes[0]);
 assert.deepEqual(multi.items.map(v=>v.dimension),['resolution','picture','audio']);
 assert.match(multi.after,/4K.*Dolby Vision.*DDP/);
 const mixed=study.versionChange(study.episodes[4]);
 assert.deepEqual(mixed.items.map(v=>v.order),[1,1,-1]);
 assert.match(mixed.before,/无损/);
 assert.deepEqual(study.versionChange(study.episodes[5]).items.map(v=>v.dimension),['special','audio']);
 const unknown=study.versionChange({...study.episodes[0],change:{kind:'quality',changes:[{dimension:'audio',order:null}]}});
 assert.equal(unknown.items.length,0);
});
