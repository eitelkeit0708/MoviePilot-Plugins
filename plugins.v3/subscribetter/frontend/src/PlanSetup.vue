<script setup>
import {ref,computed,watch,onBeforeUnmount,nextTick} from 'vue';
import {contract,initial,clone} from './schema.mjs';
import {canonical} from './legacy.mjs';
import {planDraft} from './configuration-draft.mjs';
import {planIssues,sharedChanges,changedGroups} from './plan-flow.mjs';
import SitePicker from './SitePicker.vue';
import {labels} from './labels.mjs';
import Destinations from './Destinations.vue';import DeliverySettings from './DeliverySettings.vue';
import PolicySummary from './PolicySummary.vue';import {id} from './client.mjs';
import {usePanelPosition} from './panel.mjs';
const props=defineProps({modelValue:Object,api:Object,client:Object,status:Object,base:Object,baseline:Object,categories:Array,downloaders:Array,paths:Array,policyNames:Array,revision:Number,readonly:Boolean,saving:Boolean,submitted:Boolean,saveError:Array,focus:Object,editScope:Object});
const emit=defineEmits(['update:modelValue','save','configure','editing','confirm']);
const selected=ref(props.focus?.id||''),name=ref(''),step=ref(Math.min(4,Math.max(0,Number(props.focus?.step)||0))),error=ref([]);
const position=usePanelPosition();
const ownership=ref(selected.value&&props.editScope?{[selected.value]:clone(props.editScope)}:{});
watch(()=>canonical(props.editScope||{}),()=>{if(selected.value&&!Object.keys(ownership.value[selected.value]||{}).length&&props.editScope)ownership.value={...ownership.value,[selected.value]:clone(props.editScope)}});
const removing=ref(false);
const stepNav=ref(null),body=ref(null);
function focusError(label){nextTick(()=>{const node=[...(body.value?.querySelectorAll('label')||[])].find(n=>n.textContent.trim().startsWith(label));node?.querySelector('input,select,textarea')?.focus();node?.scrollIntoView({block:'center'})})}
function go(index){if(index>step.value){for(let s=step.value;s<index;s++){const issues=planIssues(props.modelValue,selected.value,s);if(issues.length){step.value=s;error.value=issues;focusError(issues[0].label);return}}}step.value=index;error.value=[];position.top()}
const steps=['概览','下载','上传与整理','媒体库与路径','清理与保留'];
const plan=computed(()=>props.modelValue.destination_templates.find(t=>t.id===selected.value));
const rule=computed(()=>props.modelValue.delivery.rules.find(r=>r.id===plan.value?.organized_rule));
const scope=computed(()=>rule.value?.cloud_scope_id);
const mapping=computed(()=>props.modelValue.delivery.mappings.find(m=>m.cloud_scope_id===scope.value));
const baseline=computed(()=>props.baseline||props.status?.current.value?.config||props.modelValue);
const existing=computed(()=>!!baseline.value.destination_templates.find(t=>t.id===selected.value));
const editScope=computed(()=>ownership.value[selected.value]||{});
const scopedDraft=computed(()=>planDraft(baseline.value,props.modelValue,selected.value,editScope.value));
const impacts=computed(()=>sharedChanges(baseline.value,scopedDraft.value,selected.value));
const changes=computed(()=>changedGroups(baseline.value,scopedDraft.value).map(k=>({destination_templates:'下载方案',delivery:'媒体库与整理路径',policy:'收录与升级策略',recovery:'扫描设置',enabled:'启用追踪',dry_run:'演练模式'}[k]||labels[k]||k)));
const deleted=computed(()=>!plan.value&&baseline.value.destination_templates.find(p=>p.id===selected.value));
const saved=computed(()=>changes.value.length===0);
watch(()=>[selected.value,step.value,plan.value?.display_name,canonical(editScope.value)],()=>emit('editing',{id:selected.value,name:plan.value?.display_name||deleted.value?.display_name||selected.value,step:step.value,scope:clone(editScope.value)}),{immediate:true});
watch(()=>props.baseline,()=>{if(selected.value&&!plan.value&&!deleted.value)exit()});
function exit(){selected.value='';step.value=0;error.value=[]}
function consume(id,scope){const current=ownership.value[id];if(!current)return;const remaining=Object.fromEntries(Object.entries(current).map(([kind,values])=>[kind,values.filter(value=>!new Set(scope?.[kind]||[]).has(value))]).filter(([,values])=>values.length)),next={...ownership.value};if(Object.keys(remaining).length)next[id]=remaining;else delete next[id];ownership.value=next}
function submit(){for(let s=0;s<4;s++){const issues=planIssues(props.modelValue,selected.value,s);if(issues.length){step.value=s;error.value=issues;focusError(issues[0].label);return}}emit('save')}
watch(()=>canonical(props.modelValue),()=>{error.value=[]});
onBeforeUnmount(()=>emit('editing',{name:'',step:0}));
function unique(taken,prefix){let n=1;while(taken.includes(prefix+n))n++;return prefix+n}
function start(){const title=name.value.trim();if(props.readonly||!title)return;if(props.modelValue.destination_templates.some(t=>(t.display_name||t.id)===title)){error.value=['已有同名方案，请直接选择编辑。'];return}
 const pid='scheme-'+id(),d=props.modelValue.delivery,sid=unique(Object.keys(d.cloud_scopes),'云盘 '),rid=unique(d.rules.map(r=>r.id),'交付 '),mid=unique(d.mappings.map(m=>m.id),'媒体库 ');
 const next={...props.modelValue,destination_templates:[...props.modelValue.destination_templates,{id:pid,display_name:title,category_id:'',downloader:'',save_path:'',organized_rule:rid,sites:[],custom_words:[]}],delivery:{...d,
  cloud_scopes:{...d.cloud_scopes,[sid]:{...initial(contract.schemas.CloudScope),root:'',allowed_prefixes:[],p115_parents:{}}},
  mappings:[...d.mappings,{...initial(contract.schemas.Mapping),id:mid,cloud_scope_id:sid,revision:'ui-'+Date.now()}],
  rules:[...d.rules,{...initial(contract.schemas.DeliveryRule),id:rid,cloud_scope_id:sid}]}};
 emit('update:modelValue',next);selected.value=pid;name.value='';step.value=0;error.value=[];
}
function updatePlan(rows){if(rows.length!==1)return;emit('update:modelValue',{...props.modelValue,destination_templates:props.modelValue.destination_templates.map(t=>t.id===selected.value?rows[0]:t)})}
function own(kind,values){const current=ownership.value[selected.value]||{};ownership.value={...ownership.value,[selected.value]:{...current,[kind]:[...new Set([...(current[kind]||[]),...values])]}}}
function policy(value){const category=plan.value.category_id,old=props.modelValue.policy.bindings[category],bindings=props.modelValue.delivery.policy_bindings;own('categories',[category]);emit('update:modelValue',{...props.modelValue,policy:{...props.modelValue.policy,classification_revision:props.revision||1,bindings:{...props.modelValue.policy.bindings,[category]:value}},delivery:{...props.modelValue.delivery,classification_revision:props.revision||props.modelValue.delivery.classification_revision,policy_bindings:{...bindings,...(!bindings[category]||bindings[category]===old?{[category]:value}:{})}}})}
function markDelivery(delivery){
 const before=props.modelValue.delivery,changed=(a,b)=>canonical(a)!==canonical(b);
 for(const key of ['rules','mappings'])own(key,[...new Set([...(before[key]||[]),...(delivery[key]||[])].filter(row=>changed((before[key]||[]).find(old=>old.id===row.id),(delivery[key]||[]).find(next=>next.id===row.id))).map(row=>row.id))]);
 own('scopes',[...new Set([...Object.keys(before.cloud_scopes||{}),...Object.keys(delivery.cloud_scopes||{})].filter(key=>changed(before.cloud_scopes?.[key],delivery.cloud_scopes?.[key])))]);
}
function updateDelivery(delivery){markDelivery(delivery);emit('update:modelValue',{...props.modelValue,delivery})}
function updateRule(key,value){updateDelivery({...props.modelValue.delivery,rules:props.modelValue.delivery.rules.map(row=>row.id===rule.value?.id?{...row,[key]:value}:row)})}
function choose(){step.value=0;error.value=[];position.top()}
function selectPlan(value){if(value){selected.value=value;choose()}else{exit();position.top()}}
function link(){if(props.readonly||!plan.value)return;
 const d=clone(props.modelValue.delivery),rid=plan.value.organized_rule||unique(d.rules.map(r=>r.id),'交付 ');
 let r=d.rules.find(r=>r.id===rid);if(!r){r={...initial(contract.schemas.DeliveryRule),id:rid,cloud_scope_id:''};d.rules.push(r)}
 if(!d.cloud_scopes[r.cloud_scope_id]){r.cloud_scope_id=unique(Object.keys(d.cloud_scopes),'云盘 ');d.cloud_scopes[r.cloud_scope_id]={...initial(contract.schemas.CloudScope),root:'',allowed_prefixes:[],p115_parents:{}}}
 if(!d.mappings.some(m=>m.cloud_scope_id===r.cloud_scope_id))d.mappings.push({...initial(contract.schemas.Mapping),id:unique(d.mappings.map(m=>m.id),'媒体库 '),cloud_scope_id:r.cloud_scope_id,revision:'ui-'+Date.now()});
 markDelivery(d);
 emit('update:modelValue',{...props.modelValue,delivery:d,destination_templates:props.modelValue.destination_templates.map(t=>t.id===selected.value?{...t,organized_rule:rid}:t)});
}
defineExpose({exit,consume});
</script>
<template><section aria-label="按方案连续配置" :class="{'sb-plan-flow':!!plan}">
 <section v-if="deleted" class="sb-form-section"><h3>删除 {{deleted.display_name||deleted.id}}</h3><p>保存后移除此方案。文件、下载任务、共用云盘和媒体库设置保留；仍在使用的关联会在保存时检查。</p><VBtn variant="text" :disabled="readonly" @click="emit('update:modelValue',{...modelValue,destination_templates:[...modelValue.destination_templates,clone(deleted)]})">恢复方案</VBtn><VBtn v-if="submitted" variant="tonal" @click="emit('confirm')">核对保存结果</VBtn><VBtn v-else color="error" :disabled="readonly" @click="emit('save')">保存删除</VBtn><p v-for="message in saveError||[]" :key="message" role="alert">{{message}}</p></section>
 <template v-else-if="!plan"><header class="sb-heading"><div><h3>下载与入库方案</h3><p class="sb-muted">为一类作品选择下载器、云盘和媒体库。</p></div></header>
 <div class="sb-plan-library"><button v-for="t in modelValue.destination_templates" :key="t.id" class="sb-plan-choice" :disabled="readonly" @click="selected=t.id;choose()"><strong>{{t.display_name||t.id}}</strong><small>{{t.downloader||'未选择下载器'}} · {{categories?.find(c=>c.id===t.category_id)?.name||'未选择分类'}}</small><span class="sb-plan-path">{{t.save_path||'待配置下载目录'}}</span><span class="sb-choice-arrow" aria-hidden="true">›</span></button></div>
 <form class="sb-plan-create" @submit.prevent="start"><label>新方案名称<input v-model="name" :disabled="readonly" maxlength="256" placeholder="例如：剧集升级"/></label><VBtn color="primary" type="submit" :disabled="readonly||!name.trim()">新建方案</VBtn></form>
 <p v-if="error.length" class="sb-error" role="alert">{{error.join('；')}}</p></template>
  <template v-else><div :class="{'sb-plan-workspace':existing}"><label v-if="existing" class="sb-plan-picker-mobile">下载方案<select :value="selected" :disabled="saving||submitted" @change="selectPlan($event.target.value)"><option v-for="item in modelValue.destination_templates" :key="item.id" :value="item.id">{{item.display_name||item.id}}</option><option value="">＋ 新建方案</option></select></label><nav v-if="existing" class="sb-plan-selector" aria-label="下载方案列表"><button v-for="item in modelValue.destination_templates" :key="item.id" :aria-current="selected===item.id?'page':undefined" @click="selectPlan(item.id)"><strong>{{item.display_name||item.id}}</strong><small>{{item.downloader||'未选择下载器'}}</small></button><button @click="selectPlan('')"><strong>＋ 新建方案</strong></button></nav><div class="sb-plan-edit-body"><nav ref="stepNav" :class="existing?'sb-settings-tabs':'sb-setup-steps'" :aria-label="existing?'方案功能':'方案配置步骤'"><button v-for="(title,index) in steps" :key="title" :data-plan-section="index" :aria-label="existing?title:'第 '+(index+1)+' 步：'+title" :aria-current="step===index?(existing?'page':'step'):undefined" :disabled="saving||submitted" @click="go(index)"><span v-if="!existing">{{index+1}}</span><b>{{title}}</b></button></nav><label v-if="existing" class="sb-plan-step-picker-mobile">当前内容<select :value="step" :disabled="saving||submitted" @change="go(Number($event.target.value))"><option v-for="(title,index) in steps" :key="title" :value="index">{{title}}</option></select></label>
  <div ref="body" class="sb-plan-body"><h3 class="sb-sr-only">{{steps[step]}}</h3>
 <ul v-if="error.length" class="sb-form-errors" role="alert"><li v-for="(issue,index) in error" :key="index"><button @click="focusError(issue.label)">{{issue.message||issue}}</button></li></ul>
 <section v-if="step===0"><Destinations :key="selected" part="purpose" embedded :model-value="[plan]" :categories="categories" :downloaders="downloaders" :paths="paths" :rules="modelValue.delivery.rules" :readonly="readonly" @update:model-value="updatePlan"/><label>收录与升级策略<select :value="modelValue.policy.bindings[plan.category_id]||''" :disabled="readonly||!plan.category_id" @change="policy($event.target.value)"><option value="">选择这类作品采用的策略</option><option v-for="title in policyNames||[]" :key="title">{{title}}</option></select><small>同一 MoviePilot 分类的订阅共用此策略。</small></label><VBtn variant="text" :disabled="!modelValue.policy.bindings[plan.category_id]" @click="emit('configure','policy',{category:plan.category_id,policy:modelValue.policy.bindings[plan.category_id],returnLabel:plan.display_name||plan.id})">调整所选策略</VBtn><PolicySummary :policy="modelValue.policy" :category="plan.category_id" :client="client" :status="status" :schedule="modelValue.schedule"/></section>
  <section v-else-if="step===1"><Destinations part="download" embedded :model-value="[plan]" :categories="categories" :downloaders="downloaders" :paths="paths" :rules="modelValue.delivery.rules" :readonly="readonly" @update:model-value="updatePlan"/><section class="sb-form-section"><h3>搜索站点</h3><SitePicker :api="api" :model-value="plan.sites" :readonly="readonly" @update:model-value="updatePlan([{...plan,sites:$event}])"/></section><section class="sb-form-section"><h3>名称替换词</h3><label>每行一个替换词<textarea :value="plan.custom_words.join('\n')" :disabled="readonly" @input="updatePlan([{...plan,custom_words:$event.target.value.split('\n').filter(Boolean)}])"></textarea></label></section></section>
 <template v-else-if="!rule||!scope||!mapping"><p>此方案尚未配置云盘或媒体库。</p><VBtn variant="tonal" :disabled="readonly" @click="link">继续补齐设置</VBtn></template>
 <DeliverySettings v-else-if="step<4" :key="selected" :flow="{step:step===2?'upload':2,scope,rule:rule.id,category:plan.category_id}" :model-value="modelValue.delivery" :api="api" :client="client" :status="status" :saved="canonical(modelValue.delivery)===canonical(status.current.value?.config.delivery||{})" :templates="modelValue.destination_templates" :categories="categories" :policies="modelValue.policy" :policy-names="policyNames" :revision="revision" :readonly="readonly" @configure="emit('configure','recovery',{returnLabel:plan.display_name||plan.id})" @update:model-value="updateDelivery"/>
  <section v-else aria-label="清理与保留"><h3>清理与保留</h3><p class="sb-muted">这里只声明此方案允许提出的处理；维护与安全中的全局权限仍会单独限制实际操作。</p><label v-for="[key,title,note] in [['cleanup_success','成功交付后清理本地文件','仅在交付与入库结果确认后处理。'],['cleanup_staging','成功后清理 115 暂存副本','只处理本方案暂存范围内的副本。'],['cleanup_abandoned','放弃交付后清理文件','保留中的下载或共享文件仍受服务端保护。'],['remove_downloader_task_enabled','完成后移除下载器任务','仅允许处理任务记录。'],['delete_downloader_data_enabled','同时删除下载器数据','需要独立全局授权和精确范围预览。']]" :key="key" class="sb-toggle-row"><span><strong>{{title}}</strong><small>{{note}}</small></span><input type="checkbox" :checked="rule[key]" :disabled="readonly" @change="updateRule(key,$event.target.checked)"></label><VBtn variant="text" color="error" :disabled="readonly" @click="removing=true">删除方案</VBtn><dl class="sb-plan-review"><dt>下载器</dt><dd>{{plan.downloader}}</dd><dt>下载保存到</dt><dd>{{plan.save_path}}</dd><dt>从哪里上传</dt><dd>{{rule.local_root}}</dd><dt>分类</dt><dd>{{categories?.find(c=>c.id===plan.category_id)?.name}}</dd><dt>云端暂存</dt><dd>{{rule.staging_root}}</dd><dt>Symedia 入口</dt><dd>{{rule.incoming_root}}</dd><dt>运行方式</dt><dd>{{modelValue.dry_run?'演练，不执行下载上传':modelValue.enabled?'按策略执行':'追踪未启用'}} · {{rule.enabled?'整理规则启用':'整理规则未启用'}}</dd></dl>
 <div class="sb-save-scope"><strong>本次保存的范围</strong><p>只提交本方案编辑产生的修改，包含本次明确修改的共享设置：{{changes.join('、')||'无改动'}}。</p></div>
 <section class="sb-following-checks"><h4>{{saved?'设置已保存，继续检查播放路径':'保存后的下一步'}}</h4><p>可直接选择媒体库中的一部作品检查路径，无需启用追踪或扫描。路径转换与云端文件是否存在分别核实。</p><VBtn v-if="saved" variant="tonal" @click="go(3)">检查播放路径</VBtn></section>
 </section>
 <aside v-if="impacts.length" class="sb-shared-impact"><h4>这次修改还会影响</h4><div v-for="(impact,index) in impacts" :key="index"><strong>{{impact.plans.join('、')}}</strong><p>{{impact.kind}}：{{impact.fields.map(k=>labels[k]||k).join('、')}}</p></div></aside>
 <ul v-if="saveError?.length" class="sb-error" role="alert"><li v-for="message in saveError" :key="message">{{message}}</li></ul><p v-if="submitted" role="status">提交结果正在核对；不要重复保存。</p>
  </div><footer class="sb-flow-footer"><VBtn v-if="!existing" variant="text" :disabled="saving||submitted||step===0" @click="go(step-1)">上一步</VBtn><span class="sb-muted">{{saving?'保存并核对中…':submitted?'保存结果待核对':saved?'与已保存设置一致':'修改尚未生效'}}</span><VBtn v-if="submitted" variant="tonal" :disabled="saving" @click="emit('confirm')">核对保存结果</VBtn><VBtn v-else-if="!existing&&step<4" color="primary" :disabled="readonly" @click="go(step+1)">继续</VBtn><VBtn v-else color="primary" :loading="saving" :disabled="readonly||submitted||saved" @click="submit">{{saved?'已保存':'保存方案'}}</VBtn></footer></div></div>
  </template>
<v-dialog :model-value="removing" max-width="520"><section class="sb-root sb-dialog"><h2>删除此方案？</h2><p>仅移除方案设置，保留文件、下载任务与共用设置。确认后还需保存。</p><VBtn color="error" @click="emit('update:modelValue',{...modelValue,destination_templates:modelValue.destination_templates.filter(p=>p.id!==selected)});removing=false">从草稿移除</VBtn><VBtn variant="text" @click="removing=false">继续编辑</VBtn></section></v-dialog></section></template>
