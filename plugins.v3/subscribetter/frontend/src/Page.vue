<script setup>
import {ref,computed,onMounted,onBeforeUnmount} from 'vue';
import Status from './Status.vue';import Resource from './Resource.vue';import Action from './Action.vue';import Migration from './Migration.vue';import PrivateInput from './PrivateInput.vue';import Record from './Record.vue';
import {createClient} from './client.mjs';import {useStatus} from './status.mjs';import {views} from './catalog.mjs';
import './style.css';
const props=defineProps({api:{type:Object,required:true},pluginId:{type:String,required:true},sourcePluginId:String,showSwitch:Boolean});
const emit=defineEmits(['close','switch','layout','action']);
const client=createClient(props.api,props.pluginId,props.sourcePluginId),status=useStatus(client);
const selected=ref('tasks'),resource=ref(views[0].resources[0]),resourceKey=ref(0),resourceComponent=ref(null),action=ref(null),context=ref({}),trail=ref([]);
const view=computed(()=>views.find(v=>v.id===selected.value));
function choose(v){selected.value=v.id;resource.value=v.resources[0];trail.value=[];context.value={};resourceKey.value++}
function chooseResource(r){resource.value=r;trail.value=[];resourceKey.value++;context.value={}}
function open(r){trail.value.push(resource.value);resource.value=r;resourceKey.value++}
function back(){resource.value=trail.value.pop();resourceKey.value++}
async function refresh(){await status.refresh();resourceComponent.value?.load()}
function changed(){refresh()}
function exportSafe(){if(!status.current.value)return;const data={format:'subscribetter-safe-configuration-v1',instance:props.pluginId,revision:status.current.value.revision,digest:status.current.value.digest,config:status.current.value.config};const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='subscribetter-safe-configuration.json';a.click();URL.revokeObjectURL(url)}
onMounted(()=>{emit('layout',{maxWidth:'1280px'});status.refresh()});onBeforeUnmount(()=>{context.value={};action.value=null});
</script>
<template><section class="sb-root" aria-label="subscriBetter 管理"><div class="sb-actions"><h2>subscriBetter</h2><button @click="refresh">刷新当前状态</button><button @click="emit('switch')">配置</button><button @click="emit('close')">关闭</button></div><Status :status="status"/>
 <nav class="sb-nav" aria-label="九个管理视图"><button v-for="v in views" :key="v.id" :aria-current="selected===v.id" @click="choose(v)">{{v.title}}</button></nav><h2>{{view.title}}</h2><p>{{view.note}}</p>
 <template v-if="selected==='health'"><p><a href="/#/setting">打开宿主设置</a> 配置既有下载器、Emby、CD2 与 115 服务。</p><button :disabled="!status.current.value||!!status.error.value" @click="exportSafe">导出当前安全配置备份</button><p class="sb-muted">备份只有安全配置和私密引用，不能替代插件数据库与私密目录的离线一致性备份。不会导出密钥、会话或旧配置原文。</p></template>
 <PrivateInput v-if="selected==='ai'||selected==='migration'" :key="selected" :client="client" :status="status"/>
 <Migration v-if="selected==='migration'" :native-api="api" :client="client" :status="status" @changed="changed" @switch="emit('switch')"/>
 <nav class="sb-nav" aria-label="当前视图资源"><button v-for="r in view.resources" :key="r[0]" @click="chooseResource(r)">{{r[0]}}</button><button v-if="trail.length" @click="back">返回上级明细</button></nav>
 <Resource v-if="status.current.value&&status.health.value" :key="resourceKey" ref="resourceComponent" :status="status" :resource="resource" :client="client" @open="open" @select="context=$event" @action="(a,c)=>{context=c;action=a}"/>
 <details v-if="Object.keys(context).length"><summary>当前选定操作对象（仍需服务端验证）</summary><Record :value="context"/></details>
 <div class="sb-actions"><button v-for="a in view.actions" :key="a[1]" :disabled="!status.current.value||!!status.error.value" @click="action=a">{{a[0]}}</button></div>
 <v-dialog :model-value="!!action" max-width="860" persistent @update:model-value="value=>{if(!value)action=null}"><Action v-if="action" :key="action[1]" :action="action" :client="client" :status="status" :context="context" @close="action=null" @changed="changed"/></v-dialog>
</section></template>
