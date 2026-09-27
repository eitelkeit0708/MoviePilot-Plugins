<script setup>
import {ref,watch,onBeforeUnmount} from 'vue';
import {useRead} from './read.mjs';
import {createReadGate,errorText} from './client.mjs';
import {clean,contract} from './schema.mjs';
import {stateLabel,reasonText} from './media.mjs';
import Record from './Record.vue';
const props=defineProps({client:Object,status:Object,policy:Object,category:String});
const offset=ref(0),candidate=ref(''),current=ref(''),manual=ref(false),title=ref(''),subtitle=ref(''),result=ref(null),error=ref(''),busy=ref(false),gate=createReadGate();
const {data,refresh,error:readError}=useRead(signal=>props.client.get('/candidates',{params:{limit:25,offset:offset.value},signal}));
watch(()=>JSON.stringify([props.policy,props.category,candidate.value,current.value,manual.value,title.value,subtitle.value,props.status.current.value?.revision]),()=>{gate.begin();result.value=null;error.value='';busy.value=false});
onBeforeUnmount(()=>gate.close());
async function simulate(draft){
 const read=gate.begin();busy.value=true;result.value=null;error.value='';
 try {
  const selected=manual.value?{title:title.value,description:subtitle.value}:data.value?.items.find(c=>c.candidate_key===candidate.value)?.evidence;
  if(!selected)throw Error('请选择候选资源或填写样本标题');
  const baseline=data.value?.items.find(c=>c.candidate_key===current.value);
  const body={config_revision:props.status.current.value.revision,runtime_generation:props.status.health.value.generation,category_id:props.category,candidate:clean(contract.schemas.PolicySample,selected),current:baseline?[{version_id:baseline.candidate_key,raw:clean(contract.schemas.PolicySample,baseline.evidence),active:true,reliable:true}]:[],...(draft?{draft_policy:props.policy}:{})};
  const response=await props.client.post('/policies/simulate',body,{signal:read.signal});
  if(read.current())result.value={...response,mode:draft?'本次修改':'当前生效策略'};
 }catch(e){if(read.current())error.value=errorText(e)}finally{if(read.current())busy.value=false}
}
</script>
<template><aside class="sb-policy-simulation" aria-label="策略试算"><h3>试算这份策略</h3><p class="sb-muted">只比较样本，不搜索或下载。</p>
 <VSwitch v-model="manual" label="填写样本标题" hide-details color="primary"/>
 <template v-if="manual"><VTextField v-model="title" label="资源标题" maxlength="16384" variant="outlined"/><VTextarea v-model="subtitle" label="副标题或发布说明" maxlength="16384" rows="3" variant="outlined"/><p class="sb-muted">手填样本，不能作为真实资源验证。</p></template>
 <template v-else><VSelect v-model="candidate" label="候选资源" :items="data?.items||[]" item-title="title" item-value="candidate_key" variant="outlined"/><p v-if="readError" role="alert">候选暂时无法读取。<VBtn variant="text" @click="refresh">重新读取</VBtn></p><p v-else-if="data&&!data.items.length" class="sb-muted">还没有候选记录，可切换到手填样本。</p></template>
 <VSelect v-model="current" label="对照样本（不代表实际在库）" :items="[{title:'无对照，只检查准入',candidate_key:''},...(data?.items||[])]" item-title="title" item-value="candidate_key" variant="outlined"/>
 <div v-if="data?.total>25" class="sb-actions"><VBtn variant="text" :disabled="!offset" @click="offset-=25;candidate='';current='';refresh()">上一页样本</VBtn><VBtn variant="text" :disabled="data.next_offset===null" @click="offset=data.next_offset;candidate='';current='';refresh()">下一页样本</VBtn></div>
 <div class="sb-actions"><VBtn color="primary" :loading="busy" :disabled="busy||!category||!(manual?title.trim():candidate)" @click="simulate(true)">试算本次修改</VBtn><VBtn variant="text" :disabled="busy||!category||!(manual?title.trim():candidate)" @click="simulate(false)">检查当前生效策略</VBtn></div>
 <p v-if="error" role="alert" class="sb-error">{{error}}</p>
 <section v-if="result" class="sb-simulation-result" role="status"><span>{{result.mode}}</span><h3>{{stateLabel(result.evidence?.evaluation?.status||result.status)}}</h3><p>{{reasonText(result.evidence?.evaluation?.reason)}}</p><details><summary>查看具体比较依据</summary><Record :value="result.evidence?.evaluation||result.evidence"/></details></section>
</aside></template>
