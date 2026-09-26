<script setup>
import {ref,computed} from 'vue';import {useRead} from './read.mjs';import {dateText,stateLabel,reasonText,sourceTitle,sourceNotice} from './media.mjs';import Record from './Record.vue';
const props=defineProps({client:Object,status:Object});const emit=defineEmits(['action','configure','diagnostic']);
const source=ref(''),view=ref('all'),offset=ref(0),sourceOffset=ref(0);
const {data,error,busy,refresh}=useRead(async signal=>{const [sources,catalog,records,statistics]=await Promise.all([
 props.client.get('/discovery/sources',{params:{limit:12,offset:sourceOffset.value},signal}),props.client.get('/discovery/catalog',{signal}),
 props.client.get('/discovery/records',{params:{limit:12,offset:offset.value,view:view.value,...(source.value?{source_id:source.value}:{})},signal}),props.client.get('/discovery/statistics',{params:source.value?{source_id:source.value}:{},signal})]);return {sources,catalog,records,statistics}},30000);
const routes=computed(()=>data.value?.catalog.result?.routes||[]);
const savedSources=computed(()=>props.status.current.value?.config.discovery?.sources||[]);
const sourceOptions=computed(()=>[...savedSources.value.map(config=>({source_id:config.id,config})),...(data.value?.sources.items||[]).filter(s=>!savedSources.value.some(c=>c.id===s.source_id))]);
const title=s=>s?sourceTitle(s,routes.value):'来源信息未加载';
const recordSource=id=>sourceOptions.value.find(s=>s.source_id===id);
function filter(id){source.value=id;offset.value=0;refresh()}
function action(name,path,ctx,label,description){emit('action',[name,path],{...ctx,_bound:true,_label:label,_description:description})}
defineExpose({load:refresh});
</script>
<template><section aria-label="豆瓣榜单">
 <header class="sb-heading"><h2>发现 <span v-if="data" class="sb-count">{{data.records.total}}</span></h2><VBtn variant="text" @click="emit('configure','discovery')">管理来源</VBtn></header>
 <p v-if="error" class="sb-error" role="alert">{{error}} <span v-if="data">以下保留上次结果，可能已过期。</span></p><p v-if="busy&&!data" role="status">正在读取榜单…</p>
 <template v-if="data">
 <div class="sb-search"><label class="sb-grow">榜单来源<select v-model="source" @change="offset=0;refresh()"><option value="">全部来源</option><option v-for="s in sourceOptions" :key="s.source_id" :value="s.source_id">{{title(s)}}</option><option v-if="source&&!sourceOptions.some(s=>s.source_id===source)" :value="source">已选来源（其他页）</option></select></label><label>处理结果<select v-model="view" @change="offset=0;refresh()"><option value="all">全部</option><option value="recognized">已识别</option><option value="unrecognized">待识别</option></select></label><VBtn variant="text" :disabled="busy" @click="refresh">刷新</VBtn></div>
 <details class="sb-source-status"><summary>来源状态 · {{data.sources.total}} 个来源<span v-if="data.sources.items.some(s=>sourceNotice(s))"> · 本页有来源需要检查</span></summary>
 <div v-for="s in data.sources.items" :key="s.source_id" class="sb-source-row"><div><strong>{{title(s)}}</strong><span class="sb-muted sb-block">{{sourceNotice(s)||stateLabel(s.last_state)}} · 上次成功 {{dateText(s.last_success)}}</span></div><div class="sb-actions"><VBtn variant="text" @click="filter(s.source_id)">查看作品</VBtn><VBtn variant="text" :disabled="busy||!!error" @click="action('测试榜单抓取','/discovery/test',{source_id:s.source_id},title(s),'仅抓取已保存的榜单，不识别媒体、不创建订阅。')">测试抓取</VBtn><VBtn variant="text" :disabled="busy||!!error" @click="action('运行这个来源','/discovery/run',{source_ids:[s.source_id]},title(s),'按已保存规则识别作品，并可能纳管或创建订阅。')">运行一次</VBtn></div><details class="sb-technical"><summary>读取依据</summary><Record :value="{状态:s.last_state,原因代码:s.last_reason,地址:s.config.url||s.config.route_key,下次可处理时间:s.next_due}"/></details></div>
 <div v-if="data.sources.total>12" class="sb-pagination"><VBtn :disabled="busy||!sourceOffset" @click="sourceOffset-=12;refresh()">上一页来源</VBtn><VBtn :disabled="busy||data.sources.next_offset===null" @click="sourceOffset=data.sources.next_offset;refresh()">下一页来源</VBtn></div>
 </details>
 <div v-if="!data.sources.total" class="sb-empty"><h3>添加一个豆瓣榜单</h3><p>接入自部署 RSSHub 后，发现的作品会显示在这里。</p><VBtn color="primary" @click="emit('configure','discovery')">配置来源</VBtn></div>
 <p v-else-if="!data.records.items.length" class="sb-empty">还没有符合条件的作品。运行来源后会形成发现记录。</p>
 <div class="sb-discovery-list"><article v-for="r in data.records.items" :key="r.id" class="sb-discovery-row"><div><h3>{{r.raw.title||r.raw.name||'未提供标题'}}</h3><span class="sb-muted">{{title(recordSource(r.source_id))}} · 最近出现 {{dateText(r.last_seen)}}</span></div><div><span class="sb-state" :data-state="r.state">{{stateLabel(r.state)}}</span><p v-if="r.reason" class="sb-muted">{{reasonText(r.reason)}}</p></div><div class="sb-actions"><VBtn variant="text" :disabled="busy||!!error" @click="action('重新判定作品','/discovery/reprocess',{record_ids:[r.id]},r.raw.title||'此作品','按当前规则重新判定，不保证订阅成功。')">重新判定</VBtn><VBtn variant="text" @click="emit('diagnostic','discovery',['关联的订阅','/discovery/records/{record_id}/targets',{record_id:r.id}])">关联订阅</VBtn></div><details class="sb-technical"><summary>识别依据</summary><Record :value="{原因代码:r.reason,识别依据:r.evidence}"/></details></article></div>
 <div v-if="data.records.total" class="sb-pagination"><VBtn :disabled="busy||!offset" @click="offset=Math.max(0,offset-12);refresh()">上一页作品</VBtn><span>共 {{data.records.total}} 条 · 第 {{Math.floor(offset/12)+1}} 页</span><VBtn :disabled="busy||data.records.next_offset===null" @click="offset=data.records.next_offset;refresh()">下一页作品</VBtn></div>
 <details class="sb-technical"><summary>统计与缓存详情</summary><Record :value="data.statistics.result"/></details>
 </template>
</section></template>
