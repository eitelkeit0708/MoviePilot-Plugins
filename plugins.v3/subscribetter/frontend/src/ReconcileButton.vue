<script setup>
import {ref,onBeforeUnmount} from 'vue';import {errorText} from './client.mjs';import {dateText} from './media.mjs';
const props=defineProps({action:Object,client:Object,status:Object,disabled:Boolean,unknown:Boolean});const emit=defineEmits(['checked']);const busy=ref(false),checked=ref(null),error=ref('');let alive=true;
async function check(){if(busy.value||props.disabled||props.status.error.value)return;busy.value=true;error.value='';checked.value=null;try{const r=await props.client.post('/health/reconcile',{...props.action,config_revision:props.status.current.value.revision,runtime_generation:props.status.health.value.generation});if(alive){checked.value=r.result?.checked_at||null;emit('checked')}}catch(e){if(alive)error.value=errorText(e)}finally{if(alive)busy.value=false}}
onBeforeUnmount(()=>{alive=false});
</script>
<template><div class="sb-reconcile"><VBtn color="warning" variant="tonal" :disabled="busy||disabled||!!status.error.value" @click="check">{{busy?'正在核对…':'核对结果'}}</VBtn><p v-if="checked" role="status"><span>{{unknown?'外部结果仍待确认。':'已完成本次核对。'}}</span><small>{{dateText(checked)}}</small></p><div v-if="error" role="alert">本次核对未完成，当前状态保留。<details><summary>查看原因</summary>{{error}}</details></div></div></template>
