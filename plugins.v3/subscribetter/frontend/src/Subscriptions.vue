<script setup>
import {ref,computed,onMounted,onBeforeUnmount} from 'vue';
import {usePanelPosition} from './panel.mjs';
import AdoptSubscription from './AdoptSubscription.vue';
import Record from './Record.vue';
import CandidateDecision from './CandidateDecision.vue';
import UnitProgress from './UnitProgress.vue';
import {createReadGate,errorText,id} from './client.mjs';
import {stateLabel,dateText,unitLabel,qualitySummary,qualityExtra,reasonText,taskProgress,taskNext} from './media.mjs';
const props=defineProps({api:Object,client:Object,status:Object});const emit=defineEmits(['diagnostic','changed','action']);
const query=ref(''),filter=ref(''),type=ref(''),offset=ref(0),page=ref(null),selected=ref(null),detail=ref(null),unitOffset=ref(0);
const loading=ref(false),detailLoading=ref(false),error=ref(''),detailError=ref(''),adopting=ref(false),confirmation=ref(null),actionBusy=ref(false),attempted=ref(false),actionError=ref(''),notice=ref('');
const position=usePanelPosition(),detailTab=ref('versions');
const listGate=createReadGate(),detailGate=createReadGate();let mounted=true,timer;
async function load(reset=false){if(reset)offset.value=0;const read=listGate.begin();loading.value=true;error.value='';
 try{const result=await props.client.get('/tasks',{params:{limit:25,offset:offset.value,query:query.value,...(filter.value?{state:filter.value}:{}),...(type.value?{media_type:type.value}:{})},signal:read.signal});if(read.current())page.value=result}
 catch(e){if(read.current())error.value=errorText(e)}finally{if(read.current())loading.value=false}}
async function choose(task,reset=true,event){if(reset){position.remember(event);detailTab.value='versions'}selected.value=task;if(reset){unitOffset.value=0;detail.value=null;position.top()}const read=detailGate.begin();detailLoading.value=true;detailError.value='';
 try{const [result,observations,decisions,plans]=await Promise.all([props.client.get('/tasks/'+encodeURIComponent(task.id),{params:{limit:25,offset:unitOffset.value},signal:read.signal}),props.client.get('/tasks/'+encodeURIComponent(task.id)+'/observations',{params:{limit:25,offset:0},signal:read.signal}),props.client.get('/candidate-decisions',{params:{task_id:task.id,limit:5,offset:0,sort:'newest'},signal:read.signal}),props.client.get('/tasks/'+encodeURIComponent(task.id)+'/plans',{params:{limit:5,offset:0,sort:'newest'},signal:read.signal})]);if(read.current()){detail.value={...result,observations,decisions,plans};selected.value=result.task}}
 catch(e){if(read.current())detailError.value=errorText(e)}finally{if(read.current())detailLoading.value=false}}
const opportunity=computed(()=>detail.value?.opportunities?.items?.find(o=>o.state==='ACTIVE'));
function immediate(unit){emit('action',[selected.value.media_type==='电影'?'立即检查版本':'立即检查此集','/tasks/{task_id}/immediate'],{task_id:selected.value.id,generation:selected.value.generation,opportunity_id:opportunity.value.id,target_keys:[unit.target_key],_bound:true,_label:selected.value.title+' · '+unitLabel(unit),_description:'只越过此目标的等待时间；所有权、准入、交付与权限检查仍然生效。'})}
async function release(){const task={...selected.value},read=detailGate.begin();detailLoading.value=true;detailError.value='';try{const r=await props.client.get('/tasks/'+task.id+'/release-preview',{signal:read.signal});if(read.current())emit('action',['交还 MoviePilot 管理','/tasks/{task_id}/release'],{task_id:task.id,revision:r.revision,_bound:true,_label:task.title,_description:'保留文件与下载任务，将订阅控制交还 MoviePilot，恢复接管前记录的原生状态；不会恢复旧过滤条件。'})}catch(e){if(read.current())detailError.value=errorText(e)}finally{if(read.current())detailLoading.value=false}}
function back(){detailGate.begin();selected.value=null;detail.value=null;detailLoading.value=false;detailError.value='';position.restore()}
function confirm(action){confirmation.value={action,task:{...selected.value},operation:'ui-'+id()};attempted.value=false;actionError.value=''}
async function execute(){if(actionBusy.value||attempted.value||!confirmation.value)return;const {action,task,operation}=confirmation.value;actionBusy.value=true;attempted.value=true;actionError.value='';
 try{if(action==='pause')await props.client.post('/tasks/'+task.id+'/state',{state:'PAUSED'});
  else await props.client.post('/tasks/'+task.id+'/resume',{generation:task.generation});
  if(mounted){confirmation.value=null;notice.value=action==='pause'?'已暂停追踪，现有文件和下载任务保留。':'已提交恢复请求，以下显示服务端当前状态。';emit('changed');await Promise.all([load(),choose(task,false)])}}
 catch(e){if(mounted)actionError.value=errorText(e)+' 请关闭确认框并刷新，核对当前状态。'}finally{if(mounted)actionBusy.value=false}}
async function adopted(task){adopting.value=false;notice.value='已提交纳管请求，以下显示服务端当前状态。';emit('changed');await load(true);if(mounted)await choose(task)}
function diagnostics(domain){emit('diagnostic',domain,selected.value?{task_id:selected.value.id}:{} )}
function poll(){if(document.visibilityState==='hidden'||adopting.value||confirmation.value||loading.value||detailLoading.value)return;load();if(selected.value)choose(selected.value,false)}
function visibility(){if(document.visibilityState==='hidden'){listGate.begin();detailGate.begin();loading.value=false;detailLoading.value=false}else poll()}
onMounted(()=>{load();timer=setInterval(poll,15000);document.addEventListener?.('visibilitychange',visibility)});
onBeforeUnmount(()=>{mounted=false;clearInterval(timer);listGate.close();detailGate.close();document.removeEventListener?.('visibilitychange',visibility)});
defineExpose({load});
</script>
<template><section class="sb-subscriptions" aria-label="订阅作品">
 <header v-if="!selected" class="sb-heading"><h2>订阅 <span v-if="page" class="sb-count">{{page.total}}</span></h2><div class="sb-actions"><span class="sb-muted">新增订阅使用 MoviePilot</span><VBtn color="primary" @click="adopting=true">纳管已有订阅</VBtn></div></header>
 <p v-if="notice" class="sb-notice" role="status">{{notice}}</p>
 <form v-show="!selected" class="sb-search" @submit.prevent="load(true)"><label class="sb-grow">查找订阅<input v-model="query" maxlength="300" placeholder="输入作品名称"></label><label>状态<select v-model="filter" @change="load(true)"><option value="">全部状态</option><option value="ACTIVE">追踪中</option><option value="PAUSED">已暂停</option><option value="PASSIVE">仅观察</option><option value="PENDING">等待接管</option><option value="STOPPED">已停止</option><option value="RELEASED_NATIVE">已交还 MP</option></select></label><label>类型<select v-model="type" @change="load(true)"><option value="">全部类型</option><option>电影</option><option>电视剧</option></select></label><VBtn type="submit" variant="tonal" :disabled="loading">{{loading?'读取中…':'查找 / 刷新'}}</VBtn></form>
 <p v-if="error" role="alert" class="sb-error">{{error}}<span v-if="page"> 以下保留上次读取结果，可能已过期。</span></p>
 <div class="sb-workspace"><section v-show="!selected" class="sb-subscription-list" aria-label="作品列表">
  <p v-if="loading&&!page" class="sb-empty" role="status">正在读取订阅…</p>
  <div v-else-if="page&&!page.items.length&&!error" class="sb-empty"><h3>{{query||filter||type?'没有符合筛选条件的订阅':'还没有纳管的作品'}}</h3><p>在 MoviePilot 原生界面添加订阅，或在发现页面配置豆瓣榜单。</p><VBtn variant="tonal" @click="adopting=true">纳管已有订阅</VBtn></div>
  <div v-if="page?.items.length" class="sb-list-columns" aria-hidden="true"><span>作品</span><span>最近档案与版本</span><span>当前处理</span><span>下一步</span></div>
  <button v-for="task in page?.items||[]" :key="task.id" class="sb-subscription-card" :aria-pressed="selected?.id===task.id" @click="choose(task,true,$event)"><span class="sb-card-copy"><strong>{{task.title}}</strong><span class="sb-muted sb-block">{{task.year||'年份待确认'}} · {{task.media_type}}{{task.season!=null?' · 第 '+task.season+' 季':''}}</span></span><span class="sb-row-cell"><span>{{taskProgress(task)}}</span><small>{{task.progress?.resolutions?.length?task.progress.resolutions.map(r=>r+'p').join(' / '):'版本信息待确认'}}</small></span><span class="sb-row-cell"><span class="sb-state" :data-state="task.state">{{stateLabel(task.state)}}</span><small v-if="task.progress?.processing">{{task.progress.processing}} 个目标仍有在途计划</small></span><span class="sb-row-cell"><span>{{taskNext(task,status.health.value)}}</span><small>查看详情 ›</small></span></button>
  <div v-if="page?.total" class="sb-pagination"><VBtn :disabled="loading||offset===0" @click="offset=Math.max(0,offset-25);load()">上一页</VBtn><span>共 {{page.total}} 部 · 第 {{Math.floor(offset/25)+1}} 页</span><VBtn :disabled="loading||page.next_offset===null" @click="offset=page.next_offset;load()">下一页</VBtn></div>
 </section>
 <section v-if="selected" class="sb-work-detail" aria-label="作品详情"><header class="sb-heading sb-detail-header"><div><p class="sb-eyebrow">{{selected.media_type}}{{selected.season!==null?' · 第 '+selected.season+' 季':''}}</p><h2>{{selected.title||'正在读取作品…'}}</h2><small>{{stateLabel(selected.state)}}<span v-if="detail"> · 已知 {{detail.units.total}} 个目标</span></small></div><div v-if="detail" class="sb-actions"><VBtn v-if="['ACTIVE','PASSIVE','PENDING'].includes(selected.state)" :disabled="!!detailError||detailLoading||!!status.error.value" @click="confirm('pause')">暂停追踪</VBtn><VBtn v-if="selected.state==='PAUSED'" color="primary" :disabled="!!detailError||detailLoading||!!status.error.value" @click="confirm('resume')">恢复追踪</VBtn><VBtn variant="text" :disabled="detailLoading" @click="choose(selected,false)">刷新详情</VBtn><VBtn v-if="selected.native_id&&!['RELEASED_NATIVE','RELEASING'].includes(selected.state)" variant="text" :disabled="detailLoading||!!detailError||!!status.error.value" @click="release">交还 MP 管理</VBtn></div><VBtn variant="text" @click="back">返回列表</VBtn></header>
  <p v-if="detailLoading&&!detail" role="status">正在读取分集与版本信息…</p><p v-if="detailError" class="sb-error" role="alert">{{detailError}} 详情可能已过期，暂不可操作。</p>
  <template v-if="detail">

   <p v-if="selected.state==='PAUSED'" class="sb-muted">已暂停新的追踪处理。现有下载与文件保留，安全隔离仍按当前服务状态运行。</p>
   <p v-else-if="!detail.units.total" class="sb-muted">尚未确认目标范围，不能判断是否齐集。可查看处理依据了解当前进展。</p>
   <p v-if="detail.lifecycle?.expires_at" class="sb-muted">追踪期限至 {{dateText(detail.lifecycle.expires_at)}}</p><p v-if="detail.effective?.effective?.template?.id" class="sb-muted">当前交付方案：{{detail.effective.effective.template.id}}</p><p class="sb-muted">下一步：{{taskNext(selected,status.health.value)}}</p><nav class="sb-nav" aria-label="作品详情内容"><button v-for="[key,title] in [['versions','分集与版本'],['candidates','候选比较'],['history','处理记录']]" :key="key" :aria-current="detailTab===key" @click="detailTab=key">{{title}}</button></nav><section v-show="detailTab==='versions'">
   <div class="sb-unit-columns" aria-hidden="true"><span>目标</span><span>最近在库记录</span><span>本轮目标与进展</span></div>
   <div v-for="unit in detail.units.items" :key="unit.target_key" class="sb-episode">
    <div><strong>{{unitLabel(unit)}}</strong><div class="sb-actions"><VBtn v-if="opportunity" variant="text" :disabled="detailLoading||!!detailError||!!status.error.value" @click="immediate(unit)">{{selected.media_type==='电影'?'立即检查版本':'立即检查此集'}}</VBtn><VBtn variant="text" @click="emit('diagnostic','archive',[selected.title+' · '+unitLabel(unit)+'版本档案','/archive/targets/{target_key}',{target_key:unit.target_key}])">版本档案</VBtn></div></div>
    <div><p v-for="(version,index) in unit.current_quality||[]" :key="version.version_id||index" class="sb-current-quality"><small v-if="unit.current_quality.length>1">在库版本 {{index+1}}</small>{{qualitySummary(version.quality,true)}}<small class="sb-quality-extra">{{qualityExtra(version.quality)}}</small></p><span v-if="!unit.current_quality?.length">{{qualitySummary(unit.current_facts)}}</span><small v-if="unit.last_ingest_confirmed_at">上次入库确认 {{dateText(unit.last_ingest_confirmed_at)}}</small><small v-else>尚无入库确认记录</small></div>
    <UnitProgress :unit="unit" :health="status.health.value"/>
   </div>
   <div v-if="detail.units.total>25" class="sb-pagination"><VBtn :disabled="detailLoading||unitOffset===0" @click="unitOffset=Math.max(0,unitOffset-25);choose(selected,false)">上页分集</VBtn><span>第 {{Math.floor(unitOffset/25)+1}} 页</span><VBtn :disabled="detailLoading||detail.units.next_offset===null" @click="unitOffset=detail.units.next_offset;choose(selected,false)">下页分集</VBtn></div>
   </section><details v-if="detailTab==='history'&&detail.observations?.items?.length" class="sb-technical"><summary>历史观察记录</summary><p v-for="o in detail.observations.items" :key="o.id" class="sb-muted">{{unitLabel(o.data)}} · 比较截止 {{dateText(o.data.deadline)}} · 最后改善 {{dateText(o.data.last_better)}}</p></details>
   <section v-if="detailTab==='candidates'"><p v-if="!detail.decisions?.items?.length" class="sb-muted">还没有候选比较记录。</p><h3>候选比较记录</h3><CandidateDecision v-for="d in detail.decisions.items" :key="d.id" :decision="d" :client="client"/></section>
   <section v-if="detailTab==='history'"><h3>最近处理计划</h3><p v-if="!detail.plans?.items?.length" class="sb-muted">暂无处理计划。</p><p v-for="plan in detail.plans?.items||[]" :key="plan.id">{{stateLabel(plan.state)}} · 最后记录阶段：{{stateLabel(plan.data?.transfer_phase)}} <small>{{dateText(plan.data?.created_at)}}</small></p></section><div v-show="detailTab==='history'" class="sb-actions"><VBtn variant="text" @click="diagnostics('tasks')">查看处理依据与计划</VBtn><VBtn variant="text" @click="diagnostics('candidates')">查看候选资源</VBtn><VBtn variant="text" @click="diagnostics('archive')">查看全部版本档案</VBtn></div>
   <details v-show="detailTab==='history'" class="sb-technical"><summary>高级诊断</summary><Record :value="{身份:{来源:selected.media_source,编号:selected.media_id},生命周期:detail.lifecycle,生效策略:detail.effective,快照:detail.snapshot}"/></details>
  </template>
 </section></div>
 <v-dialog :model-value="adopting" max-width="860" persistent @update:model-value="adopting=$event"><AdoptSubscription v-if="adopting" :api="api" :client="client" :status="status" @close="adopting=false" @adopted="adopted"/></v-dialog>
 <v-dialog :model-value="!!confirmation" max-width="520" persistent><section v-if="confirmation" class="sb-root sb-dialog"><h2>{{confirmation.action==='pause'?'暂停追踪':'恢复追踪'}} · {{confirmation.task.title}}</h2><p>{{confirmation.action==='pause'?'暂停新的追踪处理，保留现有下载任务和文件。':'按已保存范围恢复追踪，保留原观察期限；如有在途计划或未核实发布结果，必须先对账。'}}</p><p v-if="actionError" role="alert" class="sb-error">{{actionError}}</p><div class="sb-actions"><VBtn color="primary" :disabled="actionBusy||attempted" @click="execute">{{actionBusy?'正在提交…':'确认'}}</VBtn><VBtn variant="text" :disabled="actionBusy" @click="confirmation=null">关闭</VBtn></div></section></v-dialog>
</section></template>
