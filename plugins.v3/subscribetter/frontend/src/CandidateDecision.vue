<script setup>
import {ref,computed,watch,onBeforeUnmount} from 'vue';
import {createReadGate,errorText} from './client.mjs';
import {stateLabel,dateText,reasonText,dimensions,comparisonGroups} from './media.mjs';
const props=defineProps({decision:Object,client:Object});
const opened=ref(false),loading=ref(false),loaded=ref(false),error=ref(''),evaluation=ref(null),gate=createReadGate();
const outcomes=computed(()=>Object.entries(evaluation.value?.decisions||{}).sort(([a],[b])=>a.localeCompare(b,'zh-CN',{numeric:true})));
const groups=computed(()=>comparisonGroups(props.decision.summary?.outcomes));
const detailedGroups=computed(()=>comparisonGroups(outcomes.value.map(([target_key,outcome])=>({target_key,status:outcome.status,reason:outcome.reason,dimensions:[...new Set((outcome.comparisons||[]).filter(row=>row.order!==0||row.evidence).map(row=>row.dimension))]}))));
function reset(){gate.begin();opened.value=false;loading.value=false;loaded.value=false;error.value='';evaluation.value=null}
watch(()=>props.decision.id,reset);onBeforeUnmount(()=>gate.close());
async function read(){if(loading.value||loaded.value)return;const request=gate.begin(),key=props.decision.id;loading.value=true;error.value='';
 try{const result=await props.client.get('/candidate-decisions/'+encodeURIComponent(key),{signal:request.signal});if(request.current()&&key===props.decision.id){if(result.id!==key)throw Error('比较记录不一致，请重新读取');evaluation.value=result.evidence?.evaluation||null;loaded.value=true}}
 catch(e){if(request.current())error.value=errorText(e)}finally{if(request.current())loading.value=false}}
function toggle(){opened.value=!opened.value;if(opened.value)read()}
</script>
<template><article class="sb-candidate-decision"><header class="sb-heading"><strong>{{decision.evidence?.observed?.title||'候选资源'}}</strong><span class="sb-state" :data-state="decision.status">{{stateLabel(decision.status)}}</span></header>
 <small class="sb-muted">{{dateText(decision.created_at)}}{{decision.simulation?' · 预演记录':''}}</small>
 <ul v-if="groups.length" class="sb-comparison-summary"><li v-for="(outcome,index) in groups" :key="index"><strong>{{outcome.label}}</strong><span class="sb-state" :data-state="outcome.status">{{stateLabel(outcome.status)}}</span><span>{{reasonText(outcome.reason)||'未记录原因'}}</span><small v-if="outcome.dimensions?.length">比较项：{{outcome.dimensions.map(d=>dimensions[d]||(d==='equal'?'各项相当':'未记录维度')).join('、')}}</small></li></ul>
 <p v-else>{{reasonText(decision.summary?.reason)||'尚未记录具体比较原因。'}}</p>
 <small v-if="decision.summary?.total>decision.summary?.outcomes?.length" class="sb-muted">当前摘要显示 {{decision.summary.outcomes.length}} / {{decision.summary.total}} 条结果，可读取完整比较。</small>
 <VBtn variant="text" :aria-expanded="opened" @click="toggle">{{opened?'收起比较依据':'查看本次比较依据'}}</VBtn>
 <section v-if="opened" aria-label="本次比较依据"><p v-if="loading" role="status">正在读取完整比较依据…</p><div v-else-if="error" class="sb-error" role="alert"><p>比较详情暂时无法加载。已显示的摘要保留。</p><VBtn variant="text" @click="read">重新加载</VBtn></div><p v-else-if="!evaluation||!Object.keys(evaluation).length">此记录没有保存完整比较依据。</p><template v-else>
  <p class="sb-muted">按结论和决定性比较项合并显示；记录的是当时的判断，当前在库状态可能已经变化。</p><section v-for="(outcome,index) in detailedGroups.slice(0,12)" :key="index" class="sb-comparison-outcome"><header class="sb-heading"><strong>{{outcome.label}}</strong><span>{{stateLabel(outcome.status)}}</span></header><p>{{reasonText(outcome.reason)||'未记录具体原因'}}</p><p v-if="outcome.dimensions?.length">决定性比较项：{{outcome.dimensions.map(d=>dimensions[d]||(d==='equal'?'各比较项':'比较项未记录')).join('、')}}</p><p v-else class="sb-muted">此组没有记录决定性的规格差异。</p></section>
  <p v-if="!outcomes.length">{{reasonText(evaluation.reason)||'没有分集或正片比较记录。'}}</p><p v-if="detailedGroups.length>12" class="sb-muted">共 {{detailedGroups.length}} 组判断；普通页面显示前 12 组，完整记录保留在审计数据中。</p>
 </template></section>
</article></template>
