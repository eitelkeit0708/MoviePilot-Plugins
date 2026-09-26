<script setup>
import {ref,computed,watch,onBeforeUnmount,nextTick} from 'vue';
import {contract,initial,clean,validate,clone} from './schema.mjs';
import {canonical} from './legacy.mjs';
import {errorText} from './client.mjs';
import Destinations from './Destinations.vue';import DeliverySettings from './DeliverySettings.vue';
const props=defineProps({modelValue:Object,api:Object,client:Object,status:Object,base:Object,categories:Array,downloaders:Array,paths:Array,policyNames:Array,revision:Number,readonly:Boolean});
const emit=defineEmits(['update:modelValue','save','configure']);
const selected=ref(''),name=ref(''),step=ref(0),error=ref([]),checking=ref(false),checked=ref('');let epoch=0,alive=true;
const stepNav=ref(null);
function go(index){step.value=index;nextTick(()=>{const scroller=stepNav.value?.closest?.('.sb-app-content');if(scroller)scroller.scrollTop+=stepNav.value.getBoundingClientRect().top-scroller.getBoundingClientRect().top})}
const steps=['下载与分类','云盘与写入范围','媒体库与播放路径','暂存与整理','检查并保存'];
const plan=computed(()=>props.modelValue.destination_templates.find(t=>t.id===selected.value));
const rule=computed(()=>props.modelValue.delivery.rules.find(r=>r.id===plan.value?.organized_rule));
const scope=computed(()=>rule.value?.cloud_scope_id);
const mapping=computed(()=>props.modelValue.delivery.mappings.find(m=>m.cloud_scope_id===scope.value));
const shared=computed(()=>props.modelValue.destination_templates.filter(t=>t.id!==selected.value&&props.modelValue.delivery.rules.some(r=>r.id===t.organized_rule&&r.cloud_scope_id===scope.value)).length);
const draftKey=computed(()=>canonical(clean(contract.schemas.Config,props.modelValue)));
watch(draftKey,()=>{epoch++;checked.value='';checking.value=false;error.value=[]});
onBeforeUnmount(()=>{alive=false;epoch++});
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
function choose(){step.value=0;error.value=[];checked.value='';epoch++;checking.value=false}
function link(){if(props.readonly||!plan.value)return;
 const d=clone(props.modelValue.delivery),rid=plan.value.organized_rule||unique(d.rules.map(r=>r.id),'交付 ');
 let r=d.rules.find(r=>r.id===rid);if(!r){r={...initial(contract.schemas.DeliveryRule),id:rid,cloud_scope_id:''};d.rules.push(r)}
 if(!d.cloud_scopes[r.cloud_scope_id]){r.cloud_scope_id=unique(Object.keys(d.cloud_scopes),'云盘 ');d.cloud_scopes[r.cloud_scope_id]={...initial(contract.schemas.CloudScope),root:'',allowed_prefixes:[],p115_parents:{}}}
 if(!d.mappings.some(m=>m.cloud_scope_id===r.cloud_scope_id))d.mappings.push({...initial(contract.schemas.Mapping),id:unique(d.mappings.map(m=>m.id),'媒体库 '),cloud_scope_id:r.cloud_scope_id,revision:'ui-'+Date.now()});
 emit('update:modelValue',{...props.modelValue,delivery:d,destination_templates:props.modelValue.destination_templates.map(t=>t.id===selected.value?{...t,organized_rule:rid}:t)});
}
async function check(){if(checking.value||props.readonly||!props.base)return;error.value=validate(contract.schemas.Config,props.modelValue);if(error.value.length)return;const n=++epoch,key=draftKey.value;checking.value=true;checked.value='';
 try{const result=await props.client.post('/configuration/preview',{revision:props.base.revision,digest:props.base.digest,mode:'replace',patch:clean(contract.schemas.Config,props.modelValue)});if(alive&&n===epoch&&key===draftKey.value){if(result.valid)checked.value=key;else error.value=result.errors||['草稿检查未通过']}}
 catch(e){if(alive&&n===epoch)error.value=e.validationErrors||[errorText(e)]}finally{if(alive&&n===epoch)checking.value=false}}
</script>
<template><section aria-label="按方案连续配置"><p v-if="!plan" class="sb-muted">围绕一个方案完成下载、交付和入库设置。修改只留在草稿中，最后统一保存。</p>
 <div class="sb-plan-start"><label>编辑已有方案<select v-model="selected" :disabled="readonly" @change="choose"><option value="">选择方案</option><option v-for="t in modelValue.destination_templates" :key="t.id">{{t.id}}</option></select></label><VBtn v-if="plan" variant="text" :disabled="readonly" @click="selected=''">新建另一份方案</VBtn><form v-else @submit.prevent="start"><label>新方案名称<input v-model="name" :disabled="readonly" maxlength="256" placeholder="例如：剧集升级"/></label><VBtn variant="tonal" type="submit" :disabled="readonly||!name.trim()">新建方案</VBtn></form></div>
 <template v-if="plan"><nav ref="stepNav" class="sb-setup-steps" aria-label="方案配置步骤"><button v-for="(title,index) in steps" :key="title" :aria-current="step===index?'step':undefined" @click="go(index)"><span>{{index+1}}</span>{{title}}</button></nav>
 <p v-if="shared" class="sb-notice">此云盘和路径设置还被 {{shared}} 个方案使用。修改会同时影响这些方案。</p>
 <section v-if="step===0"><Destinations :key="selected" embedded :model-value="[plan]" :categories="categories" :downloaders="downloaders" :paths="paths" :rules="modelValue.delivery.rules" :readonly="readonly" @update:model-value="updatePlan"/><label v-if="plan.category_id">收录与升级策略<select :value="modelValue.policy.bindings[plan.category_id]||''" :disabled="readonly" @change="policy($event.target.value)"><option value="">选择这类作品采用的策略</option><option v-for="title in policyNames||[]" :key="title">{{title}}</option></select><small>同一 MoviePilot 分类的订阅共用这份策略。</small></label></section>
 <template v-else-if="!rule||!scope||!mapping"><p>这份方案尚未连接完整的交付与媒体库设置。</p><VBtn variant="tonal" :disabled="readonly" @click="link">补齐此方案的配置步骤</VBtn></template>
 <DeliverySettings v-else-if="step<4" :key="selected" :flow="{step,scope,rule:rule.id}" :model-value="modelValue.delivery" :api="api" :client="client" :status="status" :saved="canonical(modelValue.delivery)===canonical(status.current.value?.config.delivery||{})" :templates="modelValue.destination_templates" :categories="categories" :policies="modelValue.policy" :policy-names="policyNames" :revision="revision" :readonly="readonly" @configure="emit('configure',$event)" @update:model-value="updateDelivery"/>
 <section v-else aria-label="方案保存检查"><h4>{{plan.id}}</h4><dl class="sb-plan-review"><dt>下载</dt><dd>{{plan.downloader||'尚未选择'}} · {{plan.save_path||'目录未填'}}</dd><dt>分类</dt><dd>{{categories?.find(c=>c.id===plan.category_id)?.name||'尚未选择分类'}}</dd><dt>暂存</dt><dd>{{rule.staging_root||'目录未填'}}</dd><dt>整理入口</dt><dd>{{rule.incoming_root||'目录未填'}}</dd><dt>运行</dt><dd>{{modelValue.dry_run?'演练模式':modelValue.enabled?'已启用追踪':'追踪未启用'}} · {{rule.enabled?'交付规则启用':'交付规则未启用'}}</dd></dl>
 <p>草稿检查只确认配置与关联条件。真实映射需保存生效后，在“媒体库与播放路径”用扫描样本检查。</p><div class="sb-actions"><VBtn variant="tonal" :disabled="readonly||checking" @click="check">{{checking?'正在检查草稿…':'检查这份草稿'}}</VBtn></div><p v-if="checked===draftKey" role="status">草稿检查通过。点击底部“保存设置”提交；这还不是实际路径验证结果。</p>
 </section><div class="sb-setup-paging"><VBtn variant="text" :disabled="step===0" @click="go(step-1)">上一步</VBtn><VBtn v-if="step<4" color="primary" @click="go(step+1)">继续：{{steps[step+1]}}</VBtn></div></template>
 <ul v-if="error.length" role="alert" class="sb-error"><li v-for="message in error" :key="message">{{message}}</li></ul>
</section></template>
