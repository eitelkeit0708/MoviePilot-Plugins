<script setup>
import {ref,inject,onBeforeUnmount} from 'vue';import {createReadGate,errorText,unwrap} from './client.mjs';
const props=defineProps({modelValue:String,label:String,readonly:Boolean,browse:Boolean,inputId:String});const emit=defineEmits(['update:modelValue']);const api=inject('subscribetter:api',null);
const open=ref(false),path=ref('/'),page=ref(1),hasNext=ref(false),rows=ref([]),busy=ref(false),error=ref(''),gate=createReadGate();
async function load(value,number=1){if(!api)return;path.value=value;page.value=number;hasNext.value=false;const read=gate.begin();busy.value=true;error.value='';rows.value=[];try{const r=unwrap(await api.post('storage/list',{storage:'local',path:value,type:'dir'},{params:{page:number,count:100},feedback:'silent',signal:read.signal}));if(read.current()){if(!Array.isArray(r))throw Error('DIRECTORY_LIST_UNAVAILABLE');hasNext.value=r.length===100;rows.value=r.filter(f=>f.type==='dir'&&typeof f.path==='string')}}catch(e){if(read.current())error.value=errorText(e)}finally{if(read.current())busy.value=false}}
function show(){open.value=true;load(props.modelValue?.startsWith('/')?props.modelValue:'/')}
function parent(){const p=path.value.replace(/\/+$/,'');load(p.slice(0,p.lastIndexOf('/'))||'/')}
function close(){open.value=false;gate.begin();busy.value=false}
onBeforeUnmount(()=>gate.close());
</script>
<template><div class="sb-path"><label>{{label}}<input :id="inputId" :value="modelValue||''" :disabled="readonly" placeholder="容器内绝对路径" @input="emit('update:modelValue',$event.target.value)"></label><details v-if="modelValue" class="sb-help sb-path-preview"><summary>查看完整路径</summary><p class="sb-path-full">{{modelValue}}</p></details><VBtn v-if="browse&&api" variant="text" :disabled="readonly" @click="show">浏览 MP 目录</VBtn><v-dialog :model-value="open" max-width="680" persistent><section v-if="open" class="sb-root sb-dialog"><h2>选择 MoviePilot 可读目录</h2><p class="sb-path-current">{{path}}</p><p class="sb-muted">目录来自 MP 的本地存储。云盘和其他容器路径请手工填写，不代表 MP 可以直接读取。</p><p v-if="busy" role="status">正在读取目录…</p><p v-if="error" class="sb-error" role="alert">{{error}} 可关闭后手工填写路径。</p><div class="sb-folder-list"><VBtn variant="text" :disabled="busy||path==='/'" @click="parent">返回上级目录</VBtn><button v-for="r in rows" :key="r.path" :disabled="busy" @click="load(r.path)">{{r.name||r.path}} /</button></div><div class="sb-pagination"><VBtn :disabled="busy||page===1" @click="load(path,page-1)">上一页目录</VBtn><span>第 {{page}} 页</span><VBtn :disabled="busy||!hasNext" @click="load(path,page+1)">下一页目录</VBtn></div><div class="sb-actions"><VBtn color="primary" :disabled="busy||!!error||readonly" @click="emit('update:modelValue',path);close()">选择此目录</VBtn><VBtn variant="text" @click="close">关闭</VBtn></div></section></v-dialog></div></template>

<style scoped>
.sb-path-full{margin:6px 0 0;font-size:14px;line-height:1.7;overflow-wrap:anywhere;white-space:pre-wrap;user-select:all;color:rgba(var(--v-theme-on-surface),.72)}
</style>
