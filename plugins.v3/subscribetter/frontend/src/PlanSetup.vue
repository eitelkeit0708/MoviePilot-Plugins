<script setup>
import {ref,computed,watch,onBeforeUnmount,nextTick} from 'vue';
import {contract,initial,clone} from './schema.mjs';
import {canonical} from './legacy.mjs';
import {planIssues,sharedChanges,changedGroups} from './plan-flow.mjs';
import {labels} from './labels.mjs';
import Destinations from './Destinations.vue';import DeliverySettings from './DeliverySettings.vue';
import Quantity from './Quantity.vue';
const props=defineProps({modelValue:Object,api:Object,client:Object,status:Object,base:Object,baseline:Object,categories:Array,downloaders:Array,paths:Array,policyNames:Array,revision:Number,readonly:Boolean,saving:Boolean,submitted:Boolean,saveError:Array});
const emit=defineEmits(['update:modelValue','save','configure','editing']);
const selected=ref(''),name=ref(''),step=ref(0),error=ref([]);
const stepNav=ref(null),body=ref(null),recovery=ref(false);
function focusError(label){nextTick(()=>{const node=[...(body.value?.querySelectorAll('label')||[])].find(n=>n.textContent.trim().startsWith(label));node?.querySelector('input,select,textarea')?.focus();node?.scrollIntoView({block:'center'})})}
function go(index){if(index>step.value){for(let s=step.value;s<index;s++){const issues=planIssues(props.modelValue,selected.value,s);if(issues.length){step.value=s;error.value=issues;focusError(issues[0].label);return}}}step.value=index;error.value=[];nextTick(()=>{if(body.value)body.value.scrollTop=0})}
const steps=['下载与分类','云盘与写入范围','媒体库与播放路径','暂存与整理','检查并保存'];
const plan=computed(()=>props.modelValue.destination_templates.find(t=>t.id===selected.value));
const rule=computed(()=>props.modelValue.delivery.rules.find(r=>r.id===plan.value?.organized_rule));
const scope=computed(()=>rule.value?.cloud_scope_id);
const mapping=computed(()=>props.modelValue.delivery.mappings.find(m=>m.cloud_scope_id===scope.value));
const baseline=computed(()=>props.baseline||props.status?.current.value?.config||props.modelValue);
const impacts=computed(()=>sharedChanges(baseline.value,props.modelValue,selected.value));
const changes=computed(()=>changedGroups(baseline.value,props.modelValue).map(k=>({destination_templates:'下载方案',delivery:'媒体库与整理路径',policy:'收录与升级策略',recovery:'扫描设置',enabled:'启用追踪',dry_run:'演练模式'}[k]||labels[k]||k)));
const saved=computed(()=>changes.value.length===0);
watch([selected,step],()=>emit('editing',{name:selected.value,step:step.value}));
function exit(){selected.value='';step.value=0;error.value=[]}
function submit(){for(let s=0;s<4;s++){const issues=planIssues(props.modelValue,selected.value,s);if(issues.length){step.value=s;error.value=issues;focusError(issues[0].label);return}}emit('save')}
watch(()=>canonical(props.modelValue),()=>{error.value=[]});
onBeforeUnmount(()=>emit('editing',{name:'',step:0}));
function unique(taken,prefix){let n=1;while(taken.includes(prefix+n))n++;return prefix+n}
function start(){const title=name.value.trim();if(props.readonly||!title)return;if(props.modelValue.destination_templates.some(t=>t.id===title)){error.value=['已有同名方案，请直接选择编辑。'];return}
 const d=props.modelValue.delivery,sid=unique(Object.keys(d.cloud_scopes),'云盘 '),rid=unique(d.rules.map(r=>r.id),'交付 '),mid=unique(d.mappings.map(m=>m.id),'媒体库 ');
 const next={...props.modelValue,destination_templates:[...props.modelValue.destination_templates,{id:title,category_id:'',downloader:'',save_path:'',organized_rule:rid,sites:[],custom_words:[]}],delivery:{...d,
  cloud_scopes:{...d.cloud_scopes,[sid]:{...initial(contract.schemas.CloudScope),root:'',allowed_prefixes:[],p115_parents:{}}},
  mappings:[...d.mappings,{...initial(contract.schemas.Mapping),id:mid,cloud_scope_id:sid,revision:'ui-'+Date.now()}],
  rules:[...d.rules,{...initial(contract.schemas.DeliveryRule),id:rid,cloud_scope_id:sid}]}};
 emit('update:modelValue',next);selected.value=title;name.value='';step.value=0;error.value=[];
}
function updatePlan(rows){if(rows.length!==1)return;emit('update:modelValue',{...props.modelValue,destination_templates:props.modelValue.destination_templates.map(t=>t.id===selected.value?rows[0]:t)})}
function policy(value){emit('update:modelValue',{...props.modelValue,policy:{...props.modelValue.policy,classification_revision:props.revision||1,bindings:{...props.modelValue.policy.bindings,[plan.value.category_id]:value}}})}
function updateDelivery(delivery){emit('update:modelValue',{...props.modelValue,delivery})}
function scanLibrary(checked){if(!mapping.value)return;const {emby_service:service,library_id:library}=mapping.value;const ids=props.modelValue.passive_libraries[service]||[];emit('update:modelValue',{...props.modelValue,passive_libraries:{...props.modelValue.passive_libraries,[service]:checked?[...new Set([...ids,library])]:ids.filter(x=>x!==library)}})}
function choose(){step.value=0;error.value=[]}
function link(){if(props.readonly||!plan.value)return;
 const d=clone(props.modelValue.delivery),rid=plan.value.organized_rule||unique(d.rules.map(r=>r.id),'交付 ');
 let r=d.rules.find(r=>r.id===rid);if(!r){r={...initial(contract.schemas.DeliveryRule),id:rid,cloud_scope_id:''};d.rules.push(r)}
 if(!d.cloud_scopes[r.cloud_scope_id]){r.cloud_scope_id=unique(Object.keys(d.cloud_scopes),'云盘 ');d.cloud_scopes[r.cloud_scope_id]={...initial(contract.schemas.CloudScope),root:'',allowed_prefixes:[],p115_parents:{}}}
 if(!d.mappings.some(m=>m.cloud_scope_id===r.cloud_scope_id))d.mappings.push({...initial(contract.schemas.Mapping),id:unique(d.mappings.map(m=>m.id),'媒体库 '),cloud_scope_id:r.cloud_scope_id,revision:'ui-'+Date.now()});
 emit('update:modelValue',{...props.modelValue,delivery:d,destination_templates:props.modelValue.destination_templates.map(t=>t.id===selected.value?{...t,organized_rule:rid}:t)});
}
defineExpose({exit});
</script>
<template><section aria-label="按方案连续配置" :class="{'sb-plan-flow':!!plan}">
 <template v-if="!plan"><header class="sb-heading"><div><h3>下载与入库方案</h3><p class="sb-muted">为一类作品选择下载器、云盘和媒体库。</p></div></header>
 <div class="sb-plan-library"><button v-for="t in modelValue.destination_templates" :key="t.id" class="sb-plan-choice" :disabled="readonly" @click="selected=t.id;choose()"><strong>{{t.id}}</strong><small>{{t.downloader||'未选择下载器'}} · {{categories?.find(c=>c.id===t.category_id)?.name||'未选择分类'}}</small><span class="sb-plan-path">{{t.save_path||'待配置下载目录'}}</span><span class="sb-choice-arrow" aria-hidden="true">›</span></button></div>
 <form class="sb-plan-create" @submit.prevent="start"><label>新方案名称<input v-model="name" :disabled="readonly" maxlength="256" placeholder="例如：剧集升级"/></label><VBtn color="primary" type="submit" :disabled="readonly||!name.trim()">新建方案</VBtn></form>
 <p v-if="error.length" class="sb-error" role="alert">{{error.join('；')}}</p></template>
 <template v-else><nav ref="stepNav" class="sb-setup-steps" aria-label="方案配置步骤"><button v-for="(title,index) in steps" :key="title" :aria-label="'第 '+(index+1)+' 步：'+title" :aria-current="step===index?'step':undefined" :disabled="saving||submitted" @click="go(index)"><span>{{index+1}}</span><b>{{title}}</b></button></nav>
 <div ref="body" class="sb-plan-body"><header class="sb-step-title"><small>第 {{step+1}} 步 / {{steps.length}}</small><h3>{{steps[step]}}</h3></header>
 <ul v-if="error.length" class="sb-form-errors" role="alert"><li v-for="(issue,index) in error" :key="index"><button @click="focusError(issue.label)">{{issue.message||issue}}</button></li></ul>
 <section v-if="step===0"><Destinations :key="selected" embedded :model-value="[plan]" :categories="categories" :downloaders="downloaders" :paths="paths" :rules="modelValue.delivery.rules" :readonly="readonly" @update:model-value="updatePlan"/><label>收录与升级策略<select :value="modelValue.policy.bindings[plan.category_id]||''" :disabled="readonly||!plan.category_id" @change="policy($event.target.value)"><option value="">选择这类作品采用的策略</option><option v-for="title in policyNames||[]" :key="title">{{title}}</option></select><small>同一 MoviePilot 分类的订阅共用此策略。</small></label></section>
 <template v-else-if="!rule||!scope||!mapping"><p>此方案尚未配置云盘或媒体库。</p><VBtn variant="tonal" :disabled="readonly" @click="link">继续补齐设置</VBtn></template>
 <DeliverySettings v-else-if="step<4" :key="selected" :flow="{step,scope,rule:rule.id,category:plan.category_id}" :model-value="modelValue.delivery" :api="api" :client="client" :status="status" :saved="canonical(modelValue.delivery)===canonical(status.current.value?.config.delivery||{})" :templates="modelValue.destination_templates" :categories="categories" :policies="modelValue.policy" :policy-names="policyNames" :revision="revision" :readonly="readonly" @configure="recovery=true" @update:model-value="updateDelivery"/>
 <section v-else aria-label="方案保存检查"><dl class="sb-plan-review"><dt>下载器</dt><dd>{{plan.downloader}}</dd><dt>下载目录</dt><dd>{{plan.save_path}}</dd><dt>分类</dt><dd>{{categories?.find(c=>c.id===plan.category_id)?.name}}</dd><dt>云端暂存</dt><dd>{{rule.staging_root}}</dd><dt>Symedia 入口</dt><dd>{{rule.incoming_root}}</dd><dt>运行方式</dt><dd>{{modelValue.dry_run?'演练，不执行下载上传':modelValue.enabled?'按策略执行':'追踪未启用'}} · {{rule.enabled?'整理规则启用':'整理规则未启用'}}</dd></dl>
 <div class="sb-save-scope"><strong>本次保存的范围</strong><p>MoviePilot 会保存整份设置。当前有改动的部分：{{changes.join('、')||'无改动'}}。</p></div>
 <section class="sb-following-checks"><h4>{{saved?'设置已保存，继续检查播放路径':'保存后的下一步'}}</h4><p>保存只确认设置生效。播放路径需用真实扫描样本检查；云端文件是否完整，仍需实际档案检查。没有样本时，可在下一步直接调整扫描设置。</p><VBtn v-if="saved" variant="tonal" @click="go(2)">检查播放路径</VBtn></section>
 </section>
 <section v-if="recovery&&step===2" class="sb-inline-recovery"><h4>扫描设置</h4><p>扫描会读取媒体库并建立版本档案，不创建订阅。持续扫描也用于发现可升级版本；以下开关保存后才生效。</p><label class="sb-check"><input type="checkbox" :checked="modelValue.passive_libraries[mapping.emby_service]?.includes(mapping.library_id)" :disabled="readonly" @change="scanLibrary($event.target.checked)">自动扫描本步骤选定的媒体库</label><Quantity label="媒体库重新扫描间隔" :model-value="modelValue.recovery.reconcile_seconds" :min="1" :max="86400" :readonly="readonly" @update:model-value="emit('update:modelValue',{...modelValue,recovery:{...modelValue.recovery,reconcile_seconds:$event}})"/><p v-if="!modelValue.enabled||modelValue.dry_run" class="sb-warning">当前运行设置不会执行自动扫描。可先保存方案；准备运行时再修改下方全局开关。</p><details><summary>全局运行开关（影响所有方案）</summary><label class="sb-check"><input type="checkbox" :checked="modelValue.enabled" :disabled="readonly" @change="emit('update:modelValue',{...modelValue,enabled:$event.target.checked})">启用订阅追踪</label><label class="sb-check"><input type="checkbox" :checked="modelValue.dry_run" :disabled="readonly" @change="emit('update:modelValue',{...modelValue,dry_run:$event.target.checked})">演练模式（不执行普通下载、上传与自动扫描）</label></details><VBtn variant="text" @click="recovery=false">收起扫描设置</VBtn></section>
 <aside v-if="impacts.length" class="sb-shared-impact"><h4>这次修改还会影响</h4><div v-for="(impact,index) in impacts" :key="index"><strong>{{impact.plans.join('、')}}</strong><p>{{impact.kind}}：{{impact.fields.map(k=>labels[k]||k).join('、')}}</p></div></aside>
 <ul v-if="saveError?.length" class="sb-error" role="alert"><li v-for="message in saveError" :key="message">{{message}}</li></ul><p v-if="submitted" role="status">提交结果正在核对；不要重复保存。</p>
 </div><footer class="sb-flow-footer"><VBtn variant="text" :disabled="saving||submitted||step===0" @click="go(step-1)">上一步</VBtn><span class="sb-muted">{{saving?'保存并核对中…':submitted?'保存结果待核对':saved?'与已保存设置一致':'修改尚未生效'}}</span><VBtn v-if="step<4" color="primary" :disabled="readonly" @click="go(step+1)">继续</VBtn><VBtn v-else color="primary" :loading="saving" :disabled="readonly||submitted||saved" @click="submit">{{saved?'已保存':'保存方案'}}</VBtn></footer>
 </template>
</section></template>
