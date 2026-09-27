<script setup>
import {ref,computed,onMounted,onBeforeUnmount,provide} from 'vue';
import Status from './Status.vue';import Resource from './Resource.vue';import Action from './Action.vue';import Migration from './Migration.vue';import PrivateInput from './PrivateInput.vue';import Record from './Record.vue';
import {createClient} from './client.mjs';import {useStatus} from './status.mjs';import {views} from './catalog.mjs';
import './style.css';
import Subscriptions from './Subscriptions.vue';import Discovery from './Discovery.vue';import Transfers from './Transfers.vue';import PolicyOverview from './PolicyOverview.vue';import SettingsOverview from './SettingsOverview.vue';
import {readFollowup,saveFollowup} from './client.mjs';
const props=defineProps({api:{type:Object,required:true},pluginId:{type:String,required:true},sourcePluginId:String,showSwitch:Boolean});
const emit=defineEmits(['close','switch','layout','action']);
const client=createClient(props.api,props.pluginId,props.sourcePluginId),status=useStatus(client);
const selected=ref('tasks'),resource=ref(views[0].resources[0]),resourceKey=ref(0),resourceComponent=ref(null),action=ref(null),context=ref({}),trail=ref([]);
const section=ref('subscriptions'),advanced=ref(false),content=ref(null);provide('subscribetter:scroll',content);
const sections=[['subscriptions','订阅','tasks'],['discovery','发现','discovery'],['delivery','传输','delivery'],['policy','策略','policy'],['settings','设置','health']];
function navigate(item){if(content.value)content.value.scrollTop=0;section.value=item[0];advanced.value=false;choose(views.find(v=>v.id===item[2]))}
function configure(group){saveFollowup(props.pluginId,{...readFollowup(props.pluginId),group});emit('switch')}
function invoke(a,c){context.value=c;action.value=a}
function diagnostic(domain,ctx={}){advanced.value=true;choose(views.find(v=>v.id===domain));if(Array.isArray(ctx)){resource.value=ctx;context.value={...ctx[2]};return}if(domain==='candidates'&&ctx.task_id){resource.value=['此作品的候选比较','/candidate-decisions',{task_id:ctx.task_id}];context.value=ctx;return}if(domain==='tasks'&&ctx.task_id){resource.value=['作品处理依据','/tasks/{task_id}',ctx];context.value=ctx}}
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
<template><section class="sb-root sb-app sb-manager" aria-label="subscriBetter 管理"><header class="sb-app-header"><div class="sb-brand"><span class="sb-brand-mark" aria-hidden="true">sB</span><strong>subscriBetter</strong></div>
 <nav class="sb-primary-nav" aria-label="主要功能"><button v-for="item in sections" :key="item[0]" :aria-current="!advanced&&section===item[0]" @click="navigate(item)">{{item[1]}}</button></nav>
 <div class="sb-app-tools"><Status :status="status"/><details class="sb-overflow-menu"><summary aria-label="管理工具">更多</summary><div class="sb-menu-items"><VBtn variant="text" @click="refresh">刷新</VBtn><VBtn variant="text" @click="advanced=!advanced">{{advanced?'返回管理':'高级诊断'}}</VBtn></div></details><VBtn variant="text" @click="emit('close')">关闭</VBtn></div></header>
 <main ref="content" class="sb-app-content"><Subscriptions v-if="!advanced&&section==='subscriptions'&&status.current.value&&status.health.value" ref="resourceComponent" :api="api" :client="client" :status="status" @configure="configure" @diagnostic="diagnostic" @action="invoke" @changed="status.refresh()"/>
 <Discovery v-else-if="!advanced&&section==='discovery'" ref="resourceComponent" :client="client" :status="status" @configure="configure" @action="invoke" @diagnostic="diagnostic"/>
 <Transfers v-else-if="!advanced&&section==='delivery'" ref="resourceComponent" :client="client" :status="status" @action="invoke" @diagnostic="diagnostic"/>
 <PolicyOverview v-else-if="!advanced&&section==='policy'" ref="resourceComponent" :client="client" :status="status" @configure="configure"/>
 <SettingsOverview v-else-if="!advanced&&section==='settings'" :status="status" @configure="configure" @diagnostic="diagnostic" @export="exportSafe" @migration="section='migration'"/>
 <section v-else-if="!advanced&&section==='migration'"><VBtn variant="text" @click="section='settings'">返回设置</VBtn><Migration :native-api="api" :client="client" :status="status" @changed="changed" @switch="emit('switch')"/></section>
 <template v-else-if="advanced">
 <nav v-if="advanced" class="sb-nav" aria-label="高级诊断九个业务域"><button v-for="v in views" :key="v.id" :aria-current="selected===v.id" @click="choose(v)">{{v.title}}</button></nav><h2>{{view.title}}</h2><p>{{view.note}}</p>
 <template v-if="selected==='health'"><p><a href="/#/setting">打开宿主设置</a> 配置既有下载器、Emby、CD2 与 115 服务。</p><button :disabled="!status.current.value||!!status.error.value" @click="exportSafe">导出当前安全配置备份</button><p class="sb-muted">备份只有安全配置和私密引用，不能替代插件数据库与私密目录的离线一致性备份。不会导出密钥、会话或旧配置原文。</p></template>
 <PrivateInput v-if="selected==='ai'||selected==='migration'" :key="selected" :client="client" :status="status"/>
 <Migration v-if="selected==='migration'" :native-api="api" :client="client" :status="status" @changed="changed" @switch="emit('switch')"/>
 <nav class="sb-nav" aria-label="当前视图资源"><button v-for="r in view.resources" :key="r[0]" @click="chooseResource(r)">{{r[0]}}</button><button v-if="trail.length" @click="back">返回上级明细</button></nav>
 <Resource v-if="status.current.value&&status.health.value" :key="resourceKey" ref="resourceComponent" :status="status" :resource="resource" :client="client" @open="open" @select="context=$event" @action="(a,c)=>{context=c;action=a}"/>
 <details v-if="Object.keys(context).length"><summary>当前选定操作对象（仍需服务端验证）</summary><Record :value="context"/></details>
 <div class="sb-actions"><button v-for="a in view.actions" :key="a[1]" :disabled="!status.current.value||!!status.error.value" @click="action=a">{{a[0]}}</button></div>
 <VBtn v-if="section==='settings'" color="primary" @click="emit('switch')">打开设置面板</VBtn>
 </template>
 </main><v-dialog :model-value="!!action" max-width="860" persistent @update:model-value="value=>{if(!value)action=null}"><Action v-if="action" :key="action[1]" :action="action" :client="client" :status="status" :context="context" @close="action=null" @changed="changed" @configure="action=null;emit('switch')"/></v-dialog>
</section></template>
