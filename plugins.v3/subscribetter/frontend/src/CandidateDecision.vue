<script setup>
import {ref,computed,watch,onBeforeUnmount} from 'vue';
import {createReadGate,errorText} from './client.mjs';
import {stateLabel,dateText,reasonText,unitLabel,dimensions,comparisonGroups} from './media.mjs';
import Record from './Record.vue';
const props=defineProps({decision:Object,client:Object});
const opened=ref(false),loading=ref(false),loaded=ref(false),error=ref(''),evaluation=ref(null),gate=createReadGate();
const offset=ref(0),outcomes=computed(()=>Object.entries(evaluation.value?.decisions||{}).sort(([a],[b])=>a.localeCompare(b,'zh-CN',{numeric:true})));
const groups=computed(()=>comparisonGroups(props.decision.summary?.outcomes));
function reset(){gate.begin();opened.value=false;loading.value=false;loaded.value=false;error.value='';evaluation.value=null;offset.value=0}
watch(()=>props.decision.id,reset);onBeforeUnmount(()=>gate.close());
async function read(){if(loading.value||loaded.value)return;const request=gate.begin(),key=props.decision.id;loading.value=true;error.value='';
 try{const result=await props.client.get('/candidate-decisions/'+encodeURIComponent(key),{signal:request.signal});if(request.current()&&key===props.decision.id){if(result.id!==key)throw Error('比较记录不一致，请重新读取');evaluation.value=result.evidence?.evaluation||null;loaded.value=true}}
 catch(e){if(request.current())error.value=errorText(e)}finally{if(request.current())loading.value=false}}
function toggle(){opened.value=!opened.value;if(opened.value)read()}
</script>
<template><article class="sb-candidate-decision"><header class="sb-heading"><strong>{{decision.evidence?.observed?.title||'候选资源'}}</strong><span class="sb-state" :data-state="decision.status">{{stateLabel(decision.status)}}</span></header>
 <small class="sb-muted">{{dateText(decision.created_at)}}{{decision.simulation?' · 预演记录':''}}</small>
 <ul v-if="groups.length" class="sb-comparison-summary"><li v-for="(outcome,index) in groups" :key="index"><strong>{{outcome.label}}</strong><span class="sb-state" :data-state="outcome.status">{{stateLabel(outcome.status)}}</span><span>{{reasonText(outcome.reason)||'未记录原因'}}</span><small v-if="outcome.dimensions?.length">比较项：{{outcome.dimensions.map(d=>dimensions[d]||(d==='equal'?'各项相当':'未记录维度')).join('、')}}</small></li></ul>
 <p v-else>{{reasonText(decision.summary?.reason)||'摘要未记录，展开查看完整依据。'}}</p>
 <small v-if="decision.summary?.total>decision.summary?.outcomes?.length" class="sb-muted">摘要覆盖 {{decision.summary.outcomes.length}} / {{decision.summary.total}} 条结果，其余需展开读取。</small>
 <VBtn variant="text" :aria-expanded="opened" @click="toggle">{{opened?'收起比较依据':'查看本次比较依据'}}</VBtn>
 <section v-if="opened" aria-label="本次比较依据"><p v-if="loading" role="status">正在读取完整比较依据…</p><p v-else-if="error" class="sb-error" role="alert">比较依据暂时无法读取。<VBtn variant="text" @click="read">重新读取</VBtn><small>{{error}}</small></p><p v-else-if="!evaluation||!Object.keys(evaluation).length">此记录没有保存完整比较依据。</p><template v-else>
  <p class="sb-muted">以下是本次比较时的记录，当前在库状态可能已经变化。</p><section v-for="[key,outcome] in outcomes.slice(offset,offset+25)" :key="key" class="sb-comparison-outcome"><header class="sb-heading"><strong>{{unitLabel({target_key:key})}}</strong><span>{{stateLabel(outcome.status)}}</span></header><p>{{reasonText(outcome.reason)||'未记录具体原因'}}</p><ul v-if="outcome.comparisons?.length" class="sb-comparison-summary"><li v-for="(comparison,index) in outcome.comparisons.slice(0,20)" :key="index">在库版本 {{index+1}} · {{dimensions[comparison.dimension]||(comparison.dimension==='equal'?'各比较项':'比较项未记录')}}：{{comparison.order===1?'候选更优':comparison.order===-1?'现有版本更优':comparison.order===0?'相当':'结果未记录'}}</li></ul><p v-else class="sb-muted">此目标没有逐版本对比记录。</p><small v-if="outcome.comparisons?.length>20">共 {{outcome.comparisons.length}} 份版本比较，前 20 份显示于此；完整数据见原始技术依据。</small></section>
  <p v-if="!outcomes.length">{{reasonText(evaluation.reason)||'没有分集或正片比较记录。'}}</p><div v-if="outcomes.length>25" class="sb-pagination"><VBtn variant="text" :disabled="offset===0" @click="offset-=25">上一页比较</VBtn><span>共 {{outcomes.length}} 条结果</span><VBtn variant="text" :disabled="offset+25>=outcomes.length" @click="offset+=25">下一页比较</VBtn></div>
  <details class="sb-technical"><summary>原始技术依据</summary><Record :value="evaluation"/></details>
 </template></section>
</article></template>
