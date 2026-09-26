<script setup>
import {ref,computed,onMounted,onBeforeUnmount} from 'vue';import {unwrap} from './client.mjs';
const props=defineProps({api:Object,modelValue:Object,readonly:Boolean});const emit=defineEmits(['update:modelValue']);
const services=ref([]),libraries=ref({}),errors=ref({}),busy=ref({}),selected=ref('');let alive=true;
const names=computed(()=>[...new Set([...services.value.map(s=>s.name),...Object.keys(props.modelValue||{})])]);
async function read(service){if(!service||busy.value[service])return;busy.value={...busy.value,[service]:true};errors.value={...errors.value,[service]:''};try{const rows=unwrap(await props.api.get('mediaserver/library',{params:{server:service},feedback:'silent'}));if(!Array.isArray(rows))throw Error();if(alive)libraries.value={...libraries.value,[service]:rows}}catch{if(alive)errors.value={...errors.value,[service]:'媒体库名称暂不可用，已保存的选择仍保留。'}}finally{if(alive)busy.value={...busy.value,[service]:false}}}
async function start(){try{const rows=unwrap(await props.api.get('mediaserver/clients',{feedback:'silent'}));if(!Array.isArray(rows))throw Error();if(alive){services.value=rows.filter(s=>String(s.type).toLowerCase()==='emby');errors.value={...errors.value,services:''}}}catch{if(alive)errors.value={...errors.value,services:'暂时无法读取 Emby 服务。'}}if(alive){selected.value=names.value.includes(selected.value)?selected.value:names.value[0]||'';await read(selected.value)}}
const choices=computed(()=>{const rows=libraries.value[selected.value]||[],saved=props.modelValue?.[selected.value]||[];return [...rows.map(r=>({id:String(r.id),name:r.name})),...saved.filter(id=>!rows.some(r=>String(r.id)===id)).map((id,index)=>({id,name:'已保存的媒体库 '+(index+1)+'，名称暂不可用'}))]});
function toggle(id,checked){if(props.readonly)return;const map={...props.modelValue},ids=map[selected.value]||[];map[selected.value]=checked?[...new Set([...ids,id])]:ids.filter(v=>v!==id);if(!map[selected.value].length)delete map[selected.value];emit('update:modelValue',map)}
onMounted(start);onBeforeUnmount(()=>{alive=false});
</script>
<template><div class="sb-library-picker"><div class="sb-inline"><label class="sb-grow">Emby 服务<select v-model="selected" @change="read(selected)"><option value="" disabled>选择 Emby 服务</option><option v-for="name in names" :key="name">{{name}}</option></select></label><VBtn variant="text" :disabled="busy[selected]" @click="errors.services?start():selected?read(selected):start()">重试读取</VBtn></div>
 <p v-if="errors.services||errors[selected]" class="sb-muted" role="status">{{errors[selected]||errors.services}}</p><p v-if="busy[selected]" role="status">正在读取媒体库…</p>
 <div class="sb-choice-grid"><label v-for="library in choices" :key="library.id" class="sb-check"><input type="checkbox" :checked="modelValue?.[selected]?.includes(library.id)" :disabled="readonly" @change="toggle(library.id,$event.target.checked)">{{library.name}}</label></div>
 <p v-if="selected&&!busy[selected]&&!errors[selected]&&!choices.length" class="sb-muted">此服务没有可选媒体库。</p>
</div></template>
