<script setup>
import {ref,computed,onBeforeUnmount} from 'vue';
import {useRead} from './read.mjs';
import {dateText,stateLabel,reasonText,sourceTitle,sourceLabels,sourceNotice} from './media.mjs';
import {errorText,createReadGate} from './client.mjs';
import {usePanelPosition} from './panel.mjs';
import MediaCover from './MediaCover.vue';
import BackButton from './BackButton.vue';
const props=defineProps({client:Object,status:Object});const emit=defineEmits(['action','configure','work']);
const position=usePanelPosition();
const tabs=[['works','作品'],['runs','运行记录'],['statistics','统计']];
const tab=ref('works'),source=ref(''),view=ref('all'),offset=ref(0),sourceOffset=ref(0);
const targetRecord=ref(null),targetPage=ref(null),targetOffset=ref(0),targetError=ref(''),targetBusy=ref(false),targetGate=createReadGate();
onBeforeUnmount(()=>targetGate.close());
async function readTargets(record,offset=0){targetRecord.value=record;targetOffset.value=offset;const read=targetGate.begin();targetBusy.value=true;targetError.value='';targetPage.value=null;try{const result=await props.client.get('/discovery/records/'+record.id+'/targets',{params:{limit:20,offset},signal:read.signal});if(read.current())targetPage.value=result}catch(e){if(read.current())targetError.value=errorText(e)}finally{if(read.current())targetBusy.value=false}}
function closeTargets(){targetGate.begin();targetRecord.value=null}
const testing=ref(''),testResults=ref({});let alive=true;onBeforeUnmount(()=>{alive=false});
async function testSource(s){
 if(testing.value||busy.value||error.value||!savedSources.value.some(c=>c.id===s.source_id))return;
 testing.value=s.source_id;
 testResults.value={...testResults.value,[s.source_id]:{message:'正在读取这个已保存来源，不识别或创建订阅…'}};
 try{
  const r=await props.client.post('/discovery/test',{source_id:s.source_id});
  if(alive)testResults.value={...testResults.value,[s.source_id]:{message:r.state==='SUCCESS'&&Number.isInteger(r.items)?`抓取 ${r.items} 条；未识别或创建订阅。`:'抓取未成功：'+(reasonText(r.reason)||'请查看读取结果。'),error:r.state!=='SUCCESS',evidence:r}};
 }catch(e){if(alive)testResults.value={...testResults.value,[s.source_id]:{message:errorText(e),error:true}}}
 finally{if(alive)testing.value=''}
}
const {data,error,busy,refresh}=useRead(async signal=>{const sourceId=source.value;const [sources,catalog,records,statistics]=await Promise.all([
 props.client.get('/discovery/sources',{params:{limit:12,offset:sourceOffset.value},signal}),props.client.get('/discovery/catalog',{signal}),
 sourceId?props.client.get('/discovery/records',{params:{limit:12,offset:offset.value,view:view.value,source_id:sourceId},signal}):null,sourceId?props.client.get('/discovery/statistics',{params:{source_id:sourceId},signal}):null]);return {sourceId,sources,catalog,records,statistics}},30000);
const membership=r=>({managed:r.managed_count<r.target_count?'已管理，部分收录记录待处理':'已由 subscriBetter 管理',native:'已交回 MoviePilot 管理',library:'媒体库中已有记录',not_added:'未加入',unrecognized:'等待识别'})[r.membership]||'收录结果待确认';
const routes=computed(()=>data.value?.catalog.result?.routes||[]);
const savedSources=computed(()=>props.status.current.value?.config.discovery?.sources||[]);
const sourceOptions=computed(()=>[...savedSources.value.map(config=>({...data.value?.sources.items.find(s=>s.source_id===config.id),source_id:config.id,config,configured:true})),...(data.value?.sources.items||[]).filter(s=>!savedSources.value.some(c=>c.id===s.source_id))]);
const sourceLabelMap=computed(()=>sourceLabels(sourceOptions.value,routes.value));
const statistics=computed(()=>data.value?.statistics?.result||{});
const stages=computed(()=>[['recognition','识别完成'],['intent_ack','已加入管理'],['download_acceptance','下载器已接受'],['delivery_completion','整理交付已确认'],['ingest','媒体库入库已确认']].map(([key,title])=>({key,title,...(statistics.value.stages?.[key]||{})})).filter(row=>Number.isInteger(row.numerator)&&Number.isInteger(row.denominator)));
const title=s=>s?sourceTitle(s,routes.value):'来源信息未加载';
const sourceLabel=s=>s?sourceLabelMap.value.get(String(s.source_id))||title(s):'来源信息未加载';
const recordSource=id=>sourceOptions.value.find(s=>s.source_id===id);
const activeSource=computed(()=>recordSource(source.value));
const detailReady=computed(()=>!!source.value&&data.value?.sourceId===source.value);
function showTab(key){tab.value=key;position.top()}
async function filter(id,event){closeTargets();if(id)position.remember(event);source.value=id;view.value='all';offset.value=0;tab.value='works';if(id)position.top();await refresh();if(!id)await position.restore()}
function configure(sourceId){emit('configure','discovery',{sourceId,returnLabel:source.value?sourceLabel(activeSource.value):'榜单'})}
function action(name,path,ctx,label,description){emit('action',[name,path],{...ctx,_bound:true,_label:label,_description:description})}
function viewState(){return {tab:tab.value,source:source.value,view:view.value,offset:offset.value,sourceOffset:sourceOffset.value,targetRecord:targetRecord.value,targetOffset:targetOffset.value}}
async function restore(value={}){tab.value=tabs.some(([key])=>key===value.tab)?value.tab:'works';source.value=value.source||'';view.value=value.view||'all';offset.value=Number(value.offset)||0;sourceOffset.value=Number(value.sourceOffset)||0;await refresh();if(value.targetRecord)await readTargets(value.targetRecord,Number(value.targetOffset)||0)}
function openWork(task,event,close=false){emit('work',task,{label:sourceLabel(activeSource.value),state:viewState(),trigger:event?.currentTarget});if(close)closeTargets()}
defineExpose({load:refresh,viewState,restore});
</script>

<template><section aria-label="豆瓣榜单">
 <BackButton v-if="source" to="榜单列表" @click="filter('')"/>
 <header class="sb-heading">
  <div><h2>{{source?sourceLabel(activeSource):'榜单'}}</h2><p v-if="!source" class="sb-muted">选择一个榜单，查看作品与收录进度。</p></div>
  <div class="sb-actions"><template v-if="!source"><VBtn variant="text" @click="configure('service')">更新设置</VBtn><VBtn color="primary" @click="configure('new')">添加榜单</VBtn></template><template v-else><VBtn v-if="activeSource?.configured" variant="text" @click="configure(source)">榜单设置</VBtn><VBtn color="primary" :disabled="busy||!!error||!activeSource?.configured" @click="action('更新榜单','/discovery/run',{source_ids:[source]},sourceLabel(activeSource),'按已保存规则读取此榜单并检查收录条件，可能创建或接管订阅。')">更新榜单</VBtn></template></div>
 </header>
 <p v-if="error" class="sb-error" role="alert">{{error}} <span v-if="data&&(!source||detailReady)">以下保留上次结果，可能已过期。</span><VBtn variant="text" :disabled="busy" @click="refresh">重新读取</VBtn></p>
 <p v-if="busy&&(!data||source&&!detailReady)" role="status">正在读取榜单…</p>
 <div v-if="data" v-show="!source">
  <div v-if="!sourceOptions.length" class="sb-empty"><h3>还没有添加榜单</h3><p>接入 RSSHub 后，选择你想关注的豆瓣榜单。</p><VBtn color="primary" @click="configure('new')">添加第一个榜单</VBtn></div>
  <div v-else class="sb-board-index" aria-label="榜单列表">
   <button v-for="s in sourceOptions" :key="s.source_id" class="sb-board-entry" :aria-label="'打开'+sourceLabel(s)" @click="filter(s.source_id,$event)">
    <span class="sb-board-entry-top"><span>{{s.config?.source_type_hint==='Movie'?'电影榜单':s.config?.source_type_hint==='TV'?'剧集榜单':'榜单'}}</span><span>{{!s.configured?'历史榜单':s.config?.enabled===false?'已停用':'已添加'}}</span></span>
    <strong>{{sourceLabel(s)}}</strong>
    <span v-if="sourceNotice(s)" class="sb-warning sb-board-notice">{{sourceNotice(s)}}</span>
    <span v-else class="sb-muted">{{s.last_success?'上次更新 '+dateText(s.last_success):'尚无成功更新记录'}}</span>
    <span class="sb-board-entry-link">查看作品 <span aria-hidden="true">→</span></span>
   </button>
  </div>
  <div class="sb-pagination"><template v-if="data.sources.total>12"><VBtn :disabled="busy||!sourceOffset" @click="sourceOffset-=12;refresh()">上一页榜单</VBtn><VBtn :disabled="busy||data.sources.next_offset===null" @click="sourceOffset=data.sources.next_offset;refresh()">下一页榜单</VBtn></template><VBtn variant="text" :disabled="busy" @click="refresh">刷新列表</VBtn></div>
 </div>
 <template v-if="source">
  <p v-if="activeSource&&!activeSource.configured" class="sb-muted">这个榜单已移除，仍可查看之前的作品和运行记录。</p>
  <nav class="sb-settings-tabs" aria-label="当前榜单功能"><button v-for="[key,label] in tabs" :key="key" :aria-current="tab===key?'page':undefined" @click="showTab(key)">{{label}}</button></nav>
  <template v-if="detailReady">
   <div v-if="tab==='works'" class="sb-stack">
    <div class="sb-search"><label>收录状态<select v-model="view" @change="offset=0;refresh()"><option value="all">全部</option><option value="managed">已管理</option><option value="native">已交回 MP</option><option value="library">媒体库已有</option><option value="not_added">未加入</option><option value="unrecognized">等待识别</option></select></label><span class="sb-muted">共 {{data.records.total}} 条</span><VBtn variant="text" :disabled="busy" @click="refresh">刷新</VBtn></div>
    <div v-if="!data.records.items.length" class="sb-empty"><h3>{{view==='all'?'这个榜单还没有作品':'没有符合此状态的作品'}}</h3><p>{{view==='all'?(activeSource?.configured?'更新榜单后，读取到的作品会出现在这里。':'这个历史榜单没有可显示的作品记录。'):'可以选择其他收录状态。'}}</p></div>
    <div class="sb-board-grid"><article v-for="r in data.records.items" :key="r.id" class="sb-board-card" :class="{'sb-board-card-text':!r.raw.poster}"><MediaCover v-if="r.raw.poster" :src="r.raw.poster"/><div class="sb-board-content"><h3>{{r.raw.title||r.raw.name||'未提供标题'}}</h3><span class="sb-membership" :data-state="r.membership">{{membership(r)}}</span><p v-if="r.reason">{{reasonText(r.reason)}}</p><div v-if="r.targets?.length" class="sb-target-summary"><span v-for="(target,index) in r.targets.slice(0,3)" :key="index">{{target.season!=null?'第 '+target.season+' 季 · ':''}}{{stateLabel(target.task_state||target.state)}}<small v-if="target.reason"> · {{reasonText(target.reason)}}</small></span></div><div class="sb-actions"><VBtn v-for="target in r.targets?.filter(t=>t.task_id)||[]" :key="target.task_id" variant="tonal" @click="openWork({id:target.task_id,title:r.raw.title},$event)">{{target.season!=null?'查看第 '+target.season+' 季':'查看作品'}}</VBtn><VBtn variant="text" :disabled="busy||!!error||!activeSource?.configured" @click="action('重新检查收录条件','/discovery/reprocess',{record_ids:[r.id]},r.raw.title||'此作品','按当前规则重新识别并检查收录条件，可能创建或接管订阅。')">重新检查收录条件</VBtn></div><VBtn v-if="r.target_count>3" variant="text" @click="readTargets(r)">查看全部 {{r.target_count}} 个季度结果</VBtn><small>最近出现 {{dateText(r.last_seen)}}</small></div></article></div>
    <div v-if="data.records.total" class="sb-pagination"><VBtn :disabled="busy||!offset" @click="offset=Math.max(0,offset-12);refresh()">上一页作品</VBtn><span>第 {{Math.floor(offset/12)+1}} 页</span><VBtn :disabled="busy||data.records.next_offset===null" @click="offset=data.records.next_offset;refresh()">下一页作品</VBtn></div>
   </div>
   <section v-else-if="tab==='runs'" class="sb-content-group"><h3>最近一次运行</h3><template v-if="activeSource"><p>{{sourceNotice(activeSource)||stateLabel(activeSource.last_state)||'尚无运行记录'}}</p><p class="sb-muted">上次成功 {{dateText(activeSource.last_success)}}</p><div class="sb-actions"><VBtn variant="tonal" :disabled="!activeSource.configured||busy||!!error||!!testing" @click="testSource(activeSource)">试读榜单</VBtn><span class="sb-muted">只读取作品，不会创建订阅。</span></div><p v-if="testResults[source]" role="status" :class="{'sb-error':testResults[source].error}">{{testResults[source].message}}</p></template></section>
   <section v-else class="sb-statistics" aria-label="榜单统计"><h3>收录进度</h3><p v-if="statistics.cohort?.first_seen||statistics.cohort?.last_seen" class="sb-muted">{{dateText(statistics.cohort.first_seen)}} 至 {{dateText(statistics.cohort.last_seen)}}</p><div class="sb-stat-summary"><div v-if="Number.isInteger(statistics.record_denominator)"><span>榜单条目</span><strong>{{statistics.record_denominator}}</strong></div><div v-if="Number.isInteger(statistics.target_denominator)"><span>处理目标</span><strong>{{statistics.target_denominator}}</strong></div></div><div class="sb-stat-stages"><div v-for="stage in stages" :key="stage.key"><span>{{stage.title}}</span><strong>{{stage.numerator}} / {{stage.denominator}}</strong><small v-if="stage.eligible_denominator!==stage.denominator">本阶段可处理 {{stage.eligible_denominator}}</small></div></div><p v-if="statistics.cohort?.includes_hidden" class="sb-muted">统计包含这个榜单已经隐藏的历史条目。</p><p v-if="!Number.isInteger(statistics.record_denominator)&&!Number.isInteger(statistics.target_denominator)" class="sb-empty">统计范围尚未取得。</p></section>
  </template>
 </template>
 <v-dialog :model-value="!!targetRecord" max-width="700" @update:model-value="v=>{if(!v)closeTargets()}"><section class="sb-root sb-dialog"><h2>{{targetRecord?.raw.title}} · 全部季度</h2><p v-if="targetBusy" role="status">正在读取…</p><p v-if="targetError" role="alert">{{targetError}} <VBtn variant="text" @click="readTargets(targetRecord,targetOffset)">重新读取</VBtn></p><div v-for="row in targetPage?.items||[]" :key="row.id" class="sb-binding-row"><span>{{row.data.season!=null?'第 '+row.data.season+' 季':'电影'}} · {{stateLabel(row.state||row.data.state)}}<small class="sb-block">{{reasonText(row.data.reason)}}</small></span><VBtn v-if="row.data.task_id" variant="text" @click="openWork({id:row.data.task_id,title:targetRecord.raw.title},$event,true)">查看作品</VBtn></div><div class="sb-pagination"><VBtn :disabled="targetBusy||!targetOffset" @click="readTargets(targetRecord,targetOffset-20)">上一页季度</VBtn><VBtn :disabled="targetBusy||!targetPage||targetPage.next_offset===null" @click="readTargets(targetRecord,targetPage.next_offset)">下一页季度</VBtn><VBtn variant="text" @click="closeTargets">关闭</VBtn></div></section></v-dialog>
</section></template>
