<script setup>
import {ref,watch,onBeforeUnmount} from 'vue';import {errorText} from './client.mjs';
const props=defineProps({mapping:Object,client:Object,status:Object,saved:Boolean});defineEmits(['configure']);
const items=ref([]),item=ref(''),loaded=ref(false),offset=ref(0),next=ref(null),busy=ref(false),error=ref(''),result=ref(null);let sequence=0,alive=true;
const fence=()=>({config_revision:props.status.current.value.revision,runtime_generation:props.status.health.value.generation});
const sampleLabel=s=>[s.series||s.name,s.year,s.season!=null?'第 '+s.season+' 季':null,s.episode!=null&&!/^第\s*\d+\s*集$/.test(s.series?'':s.name)?'第 '+s.episode+' 集':null,s.series&&s.name&&!/^第\s*\d+\s*集$/.test(s.name)?s.name:null].filter(v=>v!=null&&v!=='').join(' · ');
watch(()=>JSON.stringify([props.mapping,props.status.current.value?.revision]),()=>{sequence++;result.value=null;error.value='';busy.value=false});
watch(()=>JSON.stringify([props.mapping.emby_service,props.mapping.library_id]),()=>{items.value=[];item.value='';loaded.value=false;offset.value=0;next.value=null});
watch(item,()=>{sequence++;result.value=null;error.value='';busy.value=false});
async function samples(start=0){if(busy.value||!props.mapping.emby_service||!props.mapping.library_id)return;const n=++sequence;busy.value=true;error.value='';result.value=null;
 try{const r=await props.client.post('/configuration/library-samples',{...fence(),service:props.mapping.emby_service,library:props.mapping.library_id,offset:start,limit:25});if(alive&&n===sequence){items.value=r.result.items;offset.value=start;next.value=r.result.next_offset;item.value='';loaded.value=true}}
 catch(e){if(alive&&n===sequence)error.value=errorText(e)}finally{if(alive&&n===sequence)busy.value=false}}
async function check(){if(busy.value||!item.value||props.status.error.value)return;const n=++sequence;busy.value=true;error.value='';result.value=null;
 try{const r=await props.client.post('/configuration/mapping-check',{...fence(),mapping:JSON.parse(JSON.stringify(props.mapping)),item_id:item.value});if(alive&&n===sequence)result.value=r.result}
 catch(e){if(alive&&n===sequence)error.value=errorText(e)}finally{if(alive&&n===sequence)busy.value=false}}
onBeforeUnmount(()=>{alive=false;sequence++});
</script>
<template><section class="sb-sample-check" aria-label="检查当前草稿路径"><header class="sb-heading"><div><h4>选择样本检查路径</h4><small>只读取所选媒体库的一页作品，不启动自动扫描或下载。</small></div><VBtn variant="tonal" :disabled="busy||!mapping.emby_service||!mapping.library_id||!!status.error.value" @click="samples()">{{loaded?'重新读取样本':'选择样本'}}</VBtn></header>
 <template v-if="loaded"><label v-if="items.length">媒体库中的作品<select v-model="item" :disabled="busy"><option value="">请选择一部作品</option><option v-for="s in items" :key="s.id" :value="s.id">{{sampleLabel(s)}}</option></select></label><p v-else>本页没有作品。可以在 Emby 添加一份 STRM 后重新读取。</p><div class="sb-pagination"><VBtn v-if="offset" variant="text" :disabled="busy" @click="samples(Math.max(0,offset-25))">上一页样本</VBtn><VBtn v-if="next!==null" variant="text" :disabled="busy" @click="samples(next)">下一页样本</VBtn><VBtn color="primary" :disabled="busy||!item||!!status.error.value" @click="check">检查当前路径</VBtn></div></template>
 <p v-if="busy" role="status">正在读取所选样本…</p><p v-if="error" role="alert" class="sb-error">路径检查未完成：{{error}}</p>
 <section v-if="result" class="sb-mapping-result" role="status"><strong>{{result.state==='DRAFT_MAPPING_VERIFIED'?'当前草稿的 STRM 路径转换通过':'已找到样本，本地读取范围尚未授权'}}</strong><p v-if="result.state==='READ_SCOPE_REQUIRED'">请先保存本方案中的读取目录，再在此检查；可以保持演练或关闭状态，无需启用扫描或下载。</p><dl v-for="(r,i) in result.locations" :key="i"><dt>Emby 中的 STRM 文件</dt><dd>{{r.emby_path}}</dd><dt>MP 可读 STRM 文件</dt><dd>{{r.local_strm_path}}</dd><dt>STRM 内的本地路径</dt><dd>{{r.content_path||'尚未读取'}}</dd><dt>CD2 内部视频路径</dt><dd>{{r.cd2_path||'尚未确认'}}</dd></dl><small>仅检查这份草稿的路径转换，未核实云端文件存在；修改输入后需重新检查。</small></section>
</section></template>
