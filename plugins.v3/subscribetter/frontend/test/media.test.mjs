import test from 'node:test';
import assert from 'node:assert/strict';
import {adoptionBody,unitLabel,qualitySummary,stateLabel,reasonText,sourceNotice,taskNext,taskProgress,sourceTitle,deliveryNext} from '../src/media.mjs';
test('adoption requires an existing native ID and preserves provider, season and episode group',()=>{
 const row={id:42,type:'电视剧',media_source:'douban',media_id:'37029663',tmdb_id:999,name:'侠女内莉',year:'2026',season:1,episode_group:'group'};
 const body=adoptionBody(row,'target','op');
 assert.equal(body.native_id,42);assert.equal(body.adopt,true);assert.equal(body.media_source,'douban');assert.equal(body.media_id,'37029663');assert.equal(body.season,1);assert.equal(body.episode_group,'group');
 for(const bad of [{id:null},{id:'42'},{media_id:null},{season:null}])assert.throws(()=>adoptionBody({...row,...bad},'target','op'));
});

test('display uses target episodes and actual quality facts, not internal keys or made-up totals',()=>{
 assert.equal(unitLabel({target_key:'["电视剧","douban","24",1,"",9]'}),'第 9 集');
 assert.equal(unitLabel({target_key:'["电影","themoviedb","3",null,"",null]'}),'正片');
 assert.equal(unitLabel({target_key:'bad'}),'待确认目标');
 assert.equal(qualitySummary(null),'尚无已确认的质量信息');
 assert.match(qualitySummary({resolution:1080,source:'web'}),/1080p/);
});

test('policy ALLOW is shown as eligible',()=>assert.equal(stateLabel('ALLOW'),'符合策略'));
test('historical due dates never promise a future run and known targets do not imply full season scope',()=>{
 const health={ordinary_work_active:true},due='2026-01-01T00:00:00Z',now=Date.parse('2026-09-26');
 assert.equal(deliveryNext({state:'CONFIRMED',due},health,now),'已结束本次交付；时间见处理记录');
 assert.equal(deliveryNext({state:'UPLOADING',due},{dry_run:true},now),'演练中，普通交付不调度');
 assert.equal(deliveryNext({state:'PUBLISH_OUTCOME_UNKNOWN',due},health,now),'先核对发布结果，再决定后续动作');
 assert.match(taskNext({state:'ACTIVE',progress:{observation_until:due}},health,now),/记录已到期/);
 assert.equal(taskProgress({media_type:'电视剧',progress:{targets:2,present:1}}),'已知 2 集 · 在库 1');
});
test('display separates unconfirmed results, stale source errors, paused tasks and unknown scope',()=>{
 assert.equal(reasonText('CD2_REMOTE_UNSETTLED'),'CD2 上传结果尚未确认');
 assert.equal(sourceNotice({last_state:'OK',last_reason:'OLD_ERROR'}),'');
 assert.equal(sourceNotice({last_state:'OK',last_reason:'SOURCE_CONFIG_CHANGED'}),'来源设置已变更，尚未重新检查');
 assert.equal(taskNext({state:'PAUSED',progress:{observation_until:'2099-01-01'}},{ordinary_work_active:true}),'恢复追踪后继续');
 assert.equal(taskProgress({progress:{targets:0,confirmed:0}}),'集数范围待确认');
 assert.equal(sourceTitle({name:'每周口碑',url:'http://private/path'}),'每周口碑');
});


test('quality distinguishes picture, four audio tiers and explicit versus inferred subtitles without defaulting unknown',()=>{
 const base={resolution:2160,source:'web',group:'HHWEB',platform:'NF'};
 const a=qualitySummary({...base,picture:0,audio:1,special_zh_subtitles:false});
 const b=qualitySummary({...base,picture:2,audio:3,special_zh_subtitles:true,evidence:'explicit'});
 assert.notEqual(a,b);assert.match(a,/SDR/);assert.match(a,/DDP/);assert.match(b,/Dolby Vision/);assert.match(b,/无损/);assert.match(b,/特效字幕/);
 for(const [audio,label] of [[0,'普通音轨'],[1,'DDP'],[2,'沉浸式'],[3,'无损']])assert.ok(qualitySummary({audio}).includes(label));
 assert.match(qualitySummary({audio:null,picture:null}),/音频未知/);assert.match(qualitySummary({special_zh_subtitles:true,evidence:'inferred_pgs'}),/PGS.*推断/);
 assert.ok(!qualitySummary({special_zh_subtitles:true,evidence:'inferred_pgs'}).includes('特效字幕已确认'));
});
