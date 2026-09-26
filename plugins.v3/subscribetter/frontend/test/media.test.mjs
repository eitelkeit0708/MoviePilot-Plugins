import test from 'node:test';
import assert from 'node:assert/strict';
import {adoptionBody,unitLabel,qualitySummary,stateLabel} from '../src/media.mjs';
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
