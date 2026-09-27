<script setup>
import {ref,onMounted,onBeforeUnmount,computed} from 'vue';import {unwrap} from './client.mjs';
const props=defineProps({api:Object,modelValue:Array,readonly:Boolean});const emit=defineEmits(['update:modelValue']);const sites=ref([]),error=ref(''),busy=ref(false);let alive=true;
const rows=computed(()=>[...sites.value,...props.modelValue.filter(id=>!sites.value.some(s=>s.id===id)).map(id=>({id,name:'已保存的站点，名称暂不可用',missing:true}))]);
async function load(){busy.value=true;error.value='';try{const data=unwrap(await props.api.get('site/',{feedback:'silent'}));if(!Array.isArray(data))throw Error();if(alive)sites.value=data.filter(s=>Number.isSafeInteger(s.id)&&s.id>0).map(s=>({id:s.id,name:s.name||'未命名站点'}))}catch{if(alive)error.value='暂时无法读取站点名称；已保存的选择保持不变。'}finally{if(alive)busy.value=false}}
function select(id,checked){emit('update:modelValue',checked?[...new Set([...props.modelValue,id])]:props.modelValue.filter(v=>v!==id))}
onMounted(load);onBeforeUnmount(()=>{alive=false});
</script>
<template><div><div class="sb-choice-grid"><label v-for="s in rows" :key="s.id" class="sb-check"><input type="checkbox" :checked="modelValue.includes(s.id)" :disabled="readonly" @change="select(s.id,$event.target.checked)">{{s.name}}</label></div><p v-if="error" role="status" class="sb-muted">{{error}}</p><p v-if="!busy&&!rows.length&&!error" class="sb-muted">MoviePilot 尚未添加站点。</p><VBtn variant="text" :disabled="busy" @click="load">{{busy?'读取站点…':'刷新站点列表'}}</VBtn></div></template>
