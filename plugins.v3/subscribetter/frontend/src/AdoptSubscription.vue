<script setup>
import {computed,ref,onMounted,onBeforeUnmount} from 'vue';
import {createReadGate,errorText,id,unwrap} from './client.mjs';
import {adoptionBody} from './media.mjs';
const props=defineProps({api:Object,client:Object,status:Object});const emit=defineEmits(['close','adopted']);
const page=ref(1),rows=ref([]),hasNext=ref(false),selected=ref(null),template=ref(''),loading=ref(false),saving=ref(false),attempted=ref(false),error=ref('');
const gate=createReadGate(),operation='ui-'+id();let mounted=true;
const templates=computed(()=>props.status.current.value?.config.destination_templates||[]);
const writable=computed(()=>props.status.current.value?.config.enabled&&!props.status.current.value?.config.dry_run&&!props.status.error.value);
const canSubmit=computed(()=>writable.value&&selected.value&&template.value&&!loading.value&&!saving.value&&!attempted.value);
async function load(){const read=gate.begin();loading.value=true;error.value='';rows.value=[];hasNext.value=false;selected.value=null;
 try{const result=unwrap(await props.api.get('subscribe/',{params:{page:page.value,count:25},signal:read.signal,feedback:'silent'}));
  if(read.current()){if(!Array.isArray(result))throw Error('原生订阅列表格式无法读取');rows.value=result.filter(r=>['电影','电视剧'].includes(r.type));hasNext.value=result.length===25}}
 catch(e){if(read.current())error.value=errorText(e)}finally{if(read.current())loading.value=false}}
function select(row){try{adoptionBody(row,'check',operation);selected.value=row;error.value=''}catch(e){error.value=e.message}}
async function submit(){if(!canSubmit.value)return;saving.value=true;attempted.value=true;error.value='';
 try{const body=adoptionBody(selected.value,template.value,operation);
  const fresh=unwrap(await props.api.get('subscribe/'+body.native_id,{feedback:'silent'}));
  if(!mounted)return;
  if(JSON.stringify(adoptionBody(fresh,template.value,operation))!==JSON.stringify(body))throw Error('NATIVE_SUBSCRIPTION_CHANGED');
  if(!writable.value)throw Error('STALE_READ_REFRESH_REQUIRED');
  const result=await props.client.post('/intents',body);if(mounted)emit('adopted',result)}
 catch(e){if(mounted)error.value=errorText(e)+' 请关闭窗口并刷新，核对状态后再操作。'}finally{if(mounted)saving.value=false}}
onMounted(load);onBeforeUnmount(()=>{mounted=false;gate.close()});
</script>
<template><section class="sb-root sb-dialog" aria-label="纳管已有订阅">
 <header class="sb-heading"><div><p class="sb-eyebrow">来自 MoviePilot</p><h2>纳管已有订阅</h2></div><VBtn variant="text" :disabled="saving" @click="emit('close')">关闭</VBtn></header>
 <p class="sb-muted">请先在 MoviePilot 原生界面添加订阅。这里保留原有媒体身份、季和分集组，只改变该订阅的管理方式。</p>
 <p v-if="!writable" class="sb-notice">当前只能查看。启用插件、退出演练模式且服务状态正常后才能纳管。</p>
 <template v-if="!selected"><p v-if="loading" role="status">正在读取原生订阅…</p><p v-else-if="!rows.length&&!error" class="sb-empty">本页没有电影或剧集订阅。</p>
  <div class="sb-media-results"><button v-for="row in rows" :key="row.id" class="sb-media-result" @click="select(row)"><span class="sb-poster-placeholder" aria-hidden="true">{{row.type==='电影'?'影':'剧'}}</span><span><strong>{{row.name}}</strong><span class="sb-muted sb-block">{{row.year||'年份待确认'}} · {{row.type}}{{row.type==='电视剧'?' · 第 '+row.season+' 季':''}}</span></span></button></div>
  <div class="sb-pagination"><VBtn :disabled="loading||page===1" @click="page--;load()">上一页</VBtn><span>第 {{page}} 页</span><VBtn :disabled="loading||!hasNext" @click="page++;load()">下一页</VBtn></div>
 </template>
 <form v-else class="sb-stack" @submit.prevent="submit"><h3>{{selected.name}}{{selected.type==='电视剧'?' · 第 '+selected.season+' 季':''}}</h3>
  <p>确认纳管后，原生订阅将暂停执行，由 subscriBetter 负责资源筛选、质量升级和交付。现有订阅记录与下载任务保留。</p>
  <label>下载与入库方案<select v-model="template" :disabled="saving||attempted"><option value="" disabled>请选择已配置的方案</option><option v-for="t in templates" :key="t.id" :value="t.id">{{t.display_name||t.id}} · {{t.downloader}}</option></select></label>
  <p v-if="!templates.length" class="sb-error">请先在设置中配置下载与入库方案。</p>
  <div class="sb-actions"><VBtn type="submit" color="primary" :disabled="!canSubmit">{{saving?'正在提交…':attempted?'已尝试，请核对状态':'确认纳管'}}</VBtn><VBtn :disabled="saving||attempted" @click="selected=null">重新选择</VBtn></div>
 </form><p v-if="error" role="alert" class="sb-error">{{error}}</p>
</section></template>
