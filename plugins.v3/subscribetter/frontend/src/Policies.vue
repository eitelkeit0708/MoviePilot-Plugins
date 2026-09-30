<script setup>
import {ref,computed,watch,onMounted,onBeforeUnmount,nextTick} from 'vue';
import PredicateEditor from './PredicateEditor.vue';
import AdmissionEditor from './AdmissionEditor.vue';
import PolicySummary from './PolicySummary.vue';
import PolicySimulation from './PolicySimulation.vue';
import {usePanelPosition} from './panel.mjs';
import BackButton from './BackButton.vue';
import PriorityList from './PriorityList.vue';
const props=defineProps({modelValue:Object,saved:Object,categories:Array,defaults:Object,revision:Number,readonly:Boolean,focus:Object,plans:Array,client:Object,status:Object,catalog:Object,archiveBindings:Object});const emit=defineEmits(['update:modelValue','selection','validation']);
const category=ref(props.focus?.category||Object.keys(props.modelValue.bindings)[0]||''),binding=ref(''),ruleName=ref(''),selected=ref(props.focus?.policy||props.modelValue.bindings[category.value]||Object.keys(props.defaults||{})[0]||''),view=ref(['rules','trial','scope','global','shared'].includes(props.focus?.tab)?props.focus.tab:'rules'),selector=ref(null),position=usePanelPosition();
const dimensions={resolution:'分辨率',picture:'画质 / HDR',special:'特效字幕',source:'片源',hq:'高码率',audio:'音轨',anime:'动画资源偏好'};
const admissionErrors=ref({}),editingRule=ref(''),ruleErrors=ref({}),ruleFilter=ref([]),openedRules=ref(new Set()),usingRule=ref(''),ruleTarget=ref(''),ruleNotice=ref('');
watch(()=>props.saved,()=>{ruleNotice.value=''});
const editorErrors=computed(()=>[...Object.values(admissionErrors.value).flat(),...Object.values(ruleErrors.value).flat(),...Object.entries(props.modelValue.templates||{}).flatMap(([name,t])=>Object.entries(t.allowed||{}).filter(([,values])=>!values.length).map(([d])=>({message:name+'：'+(dimensions[d]||d)+'请至少允许一个子类。'})))]);
watch(editorErrors,value=>emit('validation',value.map(error=>error.message||String(error))),{immediate:true,deep:true});
watch([selected,category,view],()=>emit('selection',{policy:selected.value,category:category.value,tab:view.value,label:view.value==='global'?'全局限制':view.value==='shared'?'共享匹配规则':selected.value}),{immediate:true});
const categoriesUsing=computed(()=>[...new Set([...Object.entries(props.archiveBindings||{}),...Object.entries(props.modelValue.bindings)].filter(([,name])=>name===selected.value).map(([id])=>id))]);
const effectivePolicy=computed(()=>category.value?props.saved?.bindings?.[category.value]||'':'');
const draftPolicy=computed(()=>category.value?props.modelValue.bindings[category.value]||'':'');
const categoryName=computed(()=>props.categories?.find(c=>c.id===category.value)?.name||'分类名称暂不可用');
const policyNames=computed(()=>Object.keys({...props.defaults,...props.modelValue.templates}));
const visiblePolicies=computed(()=>policyNames.value.filter(name=>name===selected.value||Object.values(props.modelValue.bindings).includes(name)||Object.values(props.archiveBindings||{}).includes(name)));
const unusedPolicies=computed(()=>policyNames.value.filter(name=>!visiblePolicies.value.includes(name)));
const namesUsing=computed(()=>categoriesUsing.value.map(id=>props.categories?.find(c=>c.id===id)?.name||'已保存分类（名称暂不可用）'));
const plansUsing=computed(()=>(props.plans||[]).filter(plan=>categoriesUsing.value.includes(plan.category_id)).map(plan=>plan.display_name||plan.id));
const ruleDefinitions=computed(()=>({...props.catalog?.rule_definitions,...props.modelValue.overrides}));
const ruleNames=computed(()=>[...new Set([...(props.catalog?.predicate_rules||[]),...Object.keys(props.modelValue.overrides)])]);
const trialPolicy=computed(()=>({...props.modelValue,bindings:{...props.modelValue.bindings,...(category.value&&selected.value?{[category.value]:selected.value}:{})}}));
watch(()=>props.defaults,defaults=>{if(!selected.value)selected.value=Object.keys(defaults||{})[0]||''});
async function revealSelected(){await nextTick();const list=selector.value,item=list?.querySelector?.('[aria-current="page"]');if(!item||!list.clientWidth)return;const left=item.offsetLeft,right=left+item.offsetWidth;if(left<list.scrollLeft)list.scrollLeft=left;else if(right>list.scrollLeft+list.clientWidth)list.scrollLeft=right-list.clientWidth}
watch(selected,revealSelected);onMounted(()=>{revealSelected();if(typeof window!=='undefined')window.addEventListener('resize',revealSelected)});onBeforeUnmount(()=>{if(typeof window!=='undefined')window.removeEventListener('resize',revealSelected)});
function choose(name){selected.value=name;category.value=Object.keys(props.modelValue.bindings).find(id=>props.modelValue.bindings[id]===name)||'';if(['global','shared'].includes(view.value))view.value='rules'}
function show(value){view.value=value;position.top()}
const template=computed(()=>props.modelValue.templates?.[selected.value]||props.defaults?.[selected.value]);
const activeDimension=ref('picture'),activeFamily=ref(null);
watch(()=>template.value?.dimensions,values=>{if(values&&!values.includes(activeDimension.value))activeDimension.value=values[0]},{immediate:true});
function inspectDimension(d){activeDimension.value=d;activeFamily.value=null}
const currentGroups=computed(()=>groups(activeDimension.value));
const currentFamily=computed(()=>currentGroups.value.find(v=>v.id===activeFamily.value)||currentGroups.value[0]);
const dimensionItems=computed(()=>(template.value?.dimensions||[]).map(id=>({id,title:dimensions[id]})));
const specificationItems=computed(()=>{const d=activeDimension.value,family=currentFamily.value?.id;const order=optionOrder(d,family);return ordered(d,family).map(option=>({...option,title:family?shortTitle(option):option.title,priority:optionRank(option,order)}))});
const hasEqualPriority=computed(()=>new Set(specificationItems.value.map(v=>v.priority)).size<specificationItems.value.length);
const admissionSummary=computed(()=>{const limits=Object.keys(template.value?.allowed||{}).length;return [template.value?.resolutions.map(r=>r===2160?'4K':r+'p').join(' / '),{any:'不限发布组',official:'官方发布组',anime:'动画发布组',hhweb:'HHWEB'}[template.value?.group],{any:'不限片源',movie:'影视片源',web:'WEB 片源'}[template.value?.source],limits?'另有 '+limits+' 项细分限制':''].filter(Boolean).join(' · ')});
const shortTitle=option=>option.title.replace(/^Dolby Vision\s*/,'').replace('（Profile 未标明）','Profile 未标明').replace('（层类型未标明）',' · 层类型未标明');
const update=value=>emit('update:modelValue',{...props.modelValue,...value});
function bind(){if(!category.value||!binding.value)return;update({bindings:{...props.modelValue.bindings,[category.value]:binding.value},classification_revision:props.revision||props.modelValue.classification_revision});selected.value=binding.value}
function remove(categoryId){const bindings={...props.modelValue.bindings};delete bindings[categoryId];update({bindings})}
function edit(key,value){if(!template.value)return;update({templates:{...props.modelValue.templates,[selected.value]:{...template.value,[key]:value}}})}
function rule(name,expr){const rules={...props.modelValue.overrides};if(expr===null){delete rules[name];delete ruleErrors.value[name];if(editingRule.value===name)editingRule.value=''}else rules[name]=expr;update({overrides:rules})}
function openRule(name){openedRules.value.add(name);editingRule.value=editingRule.value===name?'':name}
function addRule(){ruleFilter.value=[];const name=ruleName.value.trim();if(!name||Object.hasOwn(ruleDefinitions.value,name))return;rule(name,{all:[]});openedRules.value.add(name);editingRule.value=name;ruleName.value='';ruleErrors.value={...ruleErrors.value,[name]:[{code:'EMPTY_GROUP',message:'请至少添加一条条件。'}]}}
function reset(){const templates={...props.modelValue.templates};delete templates[selected.value];update({templates})}
function lock(key,value){const locks={...props.modelValue.locks};if(value===null||value==='')delete locks[key];else locks[key]=value;update({locks})}
const qualityOptions=computed(()=>props.catalog?.quality_options||{});
const groupRules={any:['OfficialGroup','HHWEBGroup','VCBGroup','BGlobal','AnimePlatform'],official:['OfficialGroup'],anime:['VCBGroup','BGlobal','AnimePlatform','OfficialGroup'],hhweb:['HHWEBGroup']};
const sourceRules={any:['RemuxSource','MovieSource','WEBDL'],movie:['RemuxSource','MovieSource'],web:['WEBDL']};
const visibleRules=computed(()=>Object.entries(ruleDefinitions.value).filter(([name])=>!ruleFilter.value.length||ruleFilter.value.includes(name)).sort(([a],[b])=>Number(!(props.catalog?.predicate_rules||[]).includes(b))-Number(!(props.catalog?.predicate_rules||[]).includes(a))));
function openRules(names=[]){ruleFilter.value=names;editingRule.value=names[0]||'';if(editingRule.value)openedRules.value.add(editingRule.value);show('shared')}
function allowedValues(d){return template.value.allowed?.[d]??qualityOptions.value[d].values.map(v=>v.id)}
function allow(d,id,enabled){const allowed={...template.value.allowed},list=allowedValues(d);allowed[d]=enabled?[...new Set([...list,id])]:list.filter(v=>v!==id);if(allowed[d].length===qualityOptions.value[d].values.length)delete allowed[d];edit('allowed',allowed)}
function groups(d){const definition=qualityOptions.value[d],list=definition?.groups||[];let order=template.value?.family_preferences?.[d];if(order&&definition?.legacy_groups)order=[...new Set(order.flatMap(id=>definition.legacy_groups[id]||[id]))];return [...list].sort((a,b)=>order?order.indexOf(a.id)-order.indexOf(b.id):b.rank-a.rank)}
function groupValues(d,family){return (qualityOptions.value[d]?.values||[]).filter(v=>family==null||String(v.group??v.family)===family)}
function filterOrder(order,predicate){return order.map(tier=>[tier].flat().filter(predicate)).filter(tier=>tier.length).map(tier=>tier.length===1?tier[0]:tier)}
function optionOrder(d,family){const ids=new Set(groupValues(d,family).map(v=>v.id));return filterOrder(template.value.preferences?.[d]||[],id=>ids.has(id))}
function optionRank(option,order){const index=order.findIndex(tier=>[tier].flat().includes(option.id));return order.length?(index<0?0:order.length-index):option.rank}
function ordered(d,family){const order=optionOrder(d,family);return [...groupValues(d,family)].sort((a,b)=>optionRank(b,order)-optionRank(a,order))}
function reorderOptions(order){const d=activeDimension.value,ids=new Set(order.flat()),others=filterOrder(template.value.preferences?.[d]||[],id=>!ids.has(id));edit('preferences',{...template.value.preferences,[d]:[...others,...order.map(tier=>Array.isArray(tier)&&tier.length===1?tier[0]:tier)]})}
function resetOrder(d,family){const preferences={...template.value.preferences},ids=new Set(groupValues(d,family).map(v=>v.id)),remaining=filterOrder(preferences[d]||[],id=>!ids.has(id));if(remaining.length)preferences[d]=remaining;else delete preferences[d];edit('preferences',preferences)}
function reorderGroups(order){edit('family_preferences',{...template.value.family_preferences,[activeDimension.value]:order})}
function resetGroups(d){const preferences={...template.value.family_preferences};delete preferences[d];edit('family_preferences',preferences)}
function references(expression,name){if(!expression||typeof expression!=='object')return false;return expression.registered===name||Object.values(expression).some(value=>Array.isArray(value)?value.some(v=>references(v,name)):references(value,name))}
function requiresRule(expression,name){return expression?.registered===name||expression?.all?.some(v=>requiresRule(v,name))}
function ruleUses(name){const uses=[];if(references(props.modelValue.admission,name))uses.push('全局');for(const id of policyNames.value){const t=props.modelValue.templates?.[id]||props.defaults?.[id];if(references(t?.admission,name))uses.push(id)}return uses}
function beginUseRule(name){usingRule.value=usingRule.value===name?'':name;ruleTarget.value=selected.value||policyNames.value[0]||'';ruleNotice.value=''}
function applyRule(name){const target=ruleTarget.value,t=props.modelValue.templates?.[target]||props.defaults?.[target];if(props.readonly||!t||ruleErrors.value[name]?.length||admissionErrors.value[target]?.length)return;if(!requiresRule(t.admission,name)){const reference={registered:name},admission=t.admission?{all:[t.admission,reference]}:reference;update({templates:{...props.modelValue.templates,[target]:{...t,admission}}})}ruleNotice.value='已加入“'+target+'”的额外收录条件，保存质量策略后生效。'}
const lockChoices={resolution:[[480,'480p'],[576,'576p'],[720,'720p'],[1080,'1080p'],[2160,'4K · 2160p'],[4320,'8K · 4320p']],picture:[[0,'普通画质'],[1,'HDR'],[2,'Dolby Vision']],source:[['web','WEB'],['bluray','Blu-ray'],['remux','REMUX']],audio:[[0,'基础音轨'],[1,'Dolby Digital Plus'],[2,'空间音频'],[3,'无损音轨']],hq:[[false,'非 HQ'],[true,'HQ']]};
</script>

<template>
<section aria-label="分类质量策略" class="sb-policy-editor">
 <nav ref="selector" class="sb-policy-selector" aria-label="策略列表">
  <button class="sb-policy-global" :aria-current="view==='global'?'page':undefined" @click="show('global')"><strong>全局限制</strong><span>对所有策略生效</span></button>
  <button :aria-current="view==='shared'?'page':undefined" @click="openRules()"><strong>共享匹配规则</strong><span>定义发布组、片源与识别规则</span></button>
  <button v-for="name in visiblePolicies" :key="name" :aria-current="!['global','shared'].includes(view)&&selected===name?'page':undefined" @click="choose(name)"><strong>{{name}}</strong><span>{{Object.values(modelValue.bindings).filter(v=>v===name).length}} 个分类使用</span></button>
  <details v-if="unusedPolicies.length" class="sb-unused-policies"><summary>未使用的策略 <span>{{unusedPolicies.length}}</span></summary><button v-for="name in unusedPolicies" :key="name" @click="choose(name)"><strong>{{name}}</strong><span>0 个分类使用</span></button></details>
 </nav>
 <div class="sb-policy-edit-body">
  <div v-if="!['global','shared'].includes(view)" class="sb-policy-workspace-nav"><nav class="sb-settings-tabs" aria-label="策略功能">
   <button :aria-current="view==='rules'?'page':undefined" @click="show('rules')">规则</button>
   <button :aria-current="view==='trial'?'page':undefined" @click="show('trial')">试算</button>
   <button :aria-current="view==='scope'?'page':undefined" @click="show('scope')">使用范围</button>
  </nav><VBtn v-if="view==='rules'" variant="text" :disabled="readonly||!modelValue.templates?.[selected]" @click="reset">恢复默认</VBtn></div>
  <div v-if="view==='scope'" class="sb-policy-rules">
   <section class="sb-content-group"><header class="sb-group-heading"><h3>哪些分类使用这份策略</h3></header><p>{{namesUsing.join('、')||'尚未应用到分类'}}</p><p v-if="plansUsing.length" class="sb-muted">关联方案：{{plansUsing.join('、')}}</p></section>
   <section class="sb-content-group"><header class="sb-group-heading"><h3>调整分类与策略</h3></header>
    <div v-for="(name,categoryId) in modelValue.bindings" :key="categoryId" class="sb-binding-row"><strong>{{categories?.find(c=>c.id===categoryId)?.name||'已保存分类（名称暂不可用）'}}</strong><span>{{name}}</span><VBtn variant="text" :disabled="readonly" @click="remove(categoryId)">移除绑定</VBtn></div>
    <div class="sb-grid"><VSelect v-model="category" label="MoviePilot 分类" :items="(categories||[]).map(c=>({...c,props:{disabled:!c.enabled}}))" item-title="name" item-value="id" :disabled="readonly" variant="outlined"/><VSelect v-model="binding" label="质量策略" :items="Object.keys({...defaults,...modelValue.templates})" :disabled="readonly" variant="outlined"/></div>
    <VBtn variant="tonal" :disabled="readonly||!category||!binding" @click="bind">应用到此分类</VBtn>
   </section>
  </div>
  <div v-else-if="view==='rules'" class="sb-policy-rules sb-policy-choices">
   <template v-if="template">
    <section class="sb-content-group sb-priority-workspace" aria-label="优先选择哪个版本">
     <header class="sb-priority-heading"><div><h3>版本优先级</h3><p>按以下顺序比较，第一处差异决定选择。</p></div><VBtn variant="text" @click="show('trial')">试算效果 <span aria-hidden="true">↗</span></VBtn></header>
     <div class="sb-priority-board">
      <section class="sb-priority-column sb-comparison-column" aria-label="比较项目">
       <header><span class="sb-step-number">1</span><div><h4>比较项</h4><p>从上到下，先比较靠前的项目</p></div></header>
       <PriorityList :items="dimensionItems" label="比较顺序" selectable :selected="activeDimension" :readonly="readonly" @select="inspectDimension" @reorder="edit('dimensions',$event)"/>
       <details v-if="Object.keys(dimensions).some(d=>!template.dimensions.includes(d))" class="sb-add-dimension"><summary>＋ 添加比较项</summary><button v-for="d in Object.keys(dimensions).filter(d=>!template.dimensions.includes(d))" :key="d" type="button" :disabled="readonly" @click="edit('dimensions',[...template.dimensions,d]);inspectDimension(d)">{{dimensions[d]}}</button></details>
       <VBtn variant="text" :aria-label="'移除'+dimensions[activeDimension]" :disabled="readonly||template.dimensions.length===1" @click="edit('dimensions',template.dimensions.filter(v=>v!==activeDimension))">移除当前比较项</VBtn>
      </section>
      <div class="sb-priority-branch" :data-flat="!currentGroups.length" :aria-label="dimensions[activeDimension]+'比较设置'">
       <section v-if="currentGroups.length" class="sb-priority-column sb-family-column">
        <header><span class="sb-step-number">2</span><div><h4>{{dimensions[activeDimension]}}主类</h4><p>靠上的类型优先</p></div></header>
        <PriorityList :items="currentGroups" :label="dimensions[activeDimension]+'主类顺序'" selectable :selected="currentFamily?.id" :readonly="readonly" @select="activeFamily=$event" @reorder="reorderGroups"/>
        <VBtn v-if="template.family_preferences?.[activeDimension]" variant="text" :disabled="readonly" @click="resetGroups(activeDimension)">恢复默认</VBtn>
       </section>
       <section class="sb-priority-column sb-specification-column" :aria-label="(currentFamily?.title||dimensions[activeDimension])+'规格顺序'">
        <header><span class="sb-step-number">{{currentGroups.length?3:2}}</span><div><h4>{{currentFamily?.title||dimensions[activeDimension]}}规格</h4><p>{{currentGroups.length?'同一主类中，靠上的规格优先':'靠上的规格优先'}}</p></div></header>
        <PriorityList :key="activeDimension+':'+currentFamily?.id" :items="specificationItems" :label="(currentFamily?.title||dimensions[activeDimension])+'规格优先级'" :readonly="readonly" editable-tiers @tiers="reorderOptions"/>
        <p v-if="hasEqualPriority" class="sb-order-help">同框内不分先后。拖动单项可单独排序，也可点“拆开”解除整组。</p>
        <VBtn v-if="optionOrder(activeDimension,currentFamily?.id).length" variant="text" :disabled="readonly" @click="resetOrder(activeDimension,currentFamily?.id)">恢复默认</VBtn>
       </section>
      </div>
     </div>
     <p class="sb-priority-guidance">拖动手柄调整顺序。先比较左侧项目，相同时再比较右侧；缺少规格信息时等待核实。</p>
    </section>

    <details class="sb-content-group sb-admission-range" aria-label="允许哪些资源">
     <summary><span><strong>收录范围</strong><small>{{admissionSummary}}</small></span><span class="sb-edit-indicator">调整范围 <span aria-hidden="true">⌄</span></span></summary>
     <div class="sb-admission-fields">
     <fieldset :disabled="readonly" class="sb-resolution-options"><legend>允许的分辨率</legend><label v-for="r in (qualityOptions.resolution?.values.map(v=>Number(v.id))||[2160,1080])" :key="r" class="sb-choice"><input type="checkbox" :checked="template.resolutions.includes(r)" @change="edit('resolutions',$event.target.checked?[...template.resolutions,r]:template.resolutions.filter(v=>v!==r))"><span>{{r===2160?'4K':r+'p'}}</span><small v-if="r===2160">2160p</small></label></fieldset>
     <div class="sb-grid"><label>发布组<select :value="template.group" :disabled="readonly" @change="edit('group',$event.target.value)"><option value="any">不限制发布组</option><option value="official">官方发布组规则</option><option value="anime">动画发布组规则</option><option value="hhweb">HHWEB 规则</option></select></label><label>片源<select :value="template.source" :disabled="readonly" @change="edit('source',$event.target.value)"><option value="any">不额外限制片源</option><option value="movie">影视片源规则</option><option value="web">仅 WEB</option></select></label></div>
     <div class="sb-actions"><VBtn variant="text" @click="openRules(groupRules[template.group])">定义发布组规则</VBtn><VBtn variant="text" @click="openRules(sourceRules[template.source])">定义片源规则</VBtn><VBtn variant="text" @click="openRules(['GeneralFilter','MandarinAudio','ChineseSubtitles','NativeLanguageGuard','CNSUB'])">基础与语言规则</VBtn></div>
     <p class="sb-range-help">勾选允许收录的规格；未勾选的不会收录。</p>
     <details v-for="(definition,d) in Object.fromEntries(Object.entries(qualityOptions).filter(([key])=>key!=='resolution'))" :key="d" class="sb-quality-filter"><summary><strong>{{definition.title}}</strong><span>{{allowedValues(d).length===definition.values.length?'全部允许':'已选 '+allowedValues(d).length+' / '+definition.values.length}}</span></summary>
      <template v-if="definition.groups"><fieldset v-for="family in definition.groups" :key="family.id" :disabled="readonly" class="sb-filter-family"><legend>{{family.title}}</legend><div class="sb-quality-options"><label v-for="option in groupValues(d,family.id)" :key="option.id" class="sb-choice"><input type="checkbox" :aria-label="'允许'+option.title" :checked="allowedValues(d).includes(option.id)" @change="allow(d,option.id,$event.target.checked)"><span>{{shortTitle(option)}}</span></label></div></fieldset></template>
      <div v-else class="sb-quality-options"><label v-for="option in definition.values" :key="option.id" class="sb-choice"><input type="checkbox" :aria-label="'允许'+option.title" :disabled="readonly" :checked="allowedValues(d).includes(option.id)" @change="allow(d,option.id,$event.target.checked)"><span>{{option.title}}</span></label></div>
      <p v-if="allowedValues(d).length===0" role="alert" class="sb-error">请至少勾选一种允许收录的规格。</p>
     </details>
    </div></details>

   </template>
  </div>
  <section v-else-if="view==='global'" class="sb-global-policy">
   <p class="sb-scope-note">以下设置对全部分类和下载方案生效。</p>
   <details class="sb-content-group sb-disclosure" :open="Object.keys(modelValue.locks).length>0">
    <summary><span><strong>固定收录规格</strong><small>{{Object.keys(modelValue.locks).length?Object.keys(modelValue.locks).length+' 项已锁定':'可选 · 当前未锁定规格'}}</small></span></summary>
    <p class="sb-muted">只接收符合以下规格的资源。留空表示不限制。</p>
    <div class="sb-grid"><label v-for="(choices,key) in lockChoices" :key="key">{{dimensions[key]}}<select :value="modelValue.locks[key]===undefined?'':String(modelValue.locks[key])" :disabled="readonly" @change="lock(key,$event.target.value===''?null:choices.find(v=>String(v[0])===$event.target.value)[0])"><option value="">不锁定</option><option v-for="[value,title] in choices" :key="String(value)" :value="String(value)">{{title}}</option></select></label><label v-for="[key,title] in [['group','发布组'],['platform','平台']]" :key="key">{{title}}<input :value="modelValue.locks[key]||''" :disabled="readonly" placeholder="不锁定" @input="lock(key,$event.target.value)"></label><label>只接收指定季度<input type="number" min="0" max="999" placeholder="不锁定" :value="modelValue.locks.season??''" :disabled="readonly" @input="lock('season',$event.target.value===''?null:Number($event.target.value))"></label></div>
   </details>

  </section>
  <div v-else-if="view==='trial'" class="sb-policy-test-column">
   <header class="sb-group-heading"><div><h3>看看资源会如何被选择</h3><p>选择分类，再用实际候选比较保存前后的结果。</p></div></header>
   <VSelect v-model="category" label="试算分类" :items="(categories||[]).filter(c=>c.enabled)" item-title="name" item-value="id" variant="outlined"/>
   <p v-if="!category" class="sb-notice">请先选择要试算的分类。</p>
   <p v-else-if="draftPolicy!==effectivePolicy" class="sb-notice">当前生效：“{{effectivePolicy||'未绑定策略'}}”。保存后改用：“{{draftPolicy||'未绑定策略'}}”。下方分别比较。</p>
   <p v-else-if="effectivePolicy!==selected" class="sb-notice">当前生效：“{{effectivePolicy||'未绑定策略'}}”。下方试算的是尚未应用的“{{selected}}”。</p>
   <PolicySummary v-if="client&&status&&category" :policy="trialPolicy" :category="category" :client="client" :status="status"/>
   <PolicySimulation v-if="client&&status" :client="client" :status="status" :policy="trialPolicy" :category="category" :category-name="categoryName" :policy-name="selected" :current-policy-name="effectivePolicy" :effective="!!category&&effectivePolicy===selected"/>
  </div>
  <KeepAlive><AdmissionEditor v-if="view==='global'||view==='rules'&&template" :key="view==='global'?'global':selected" :scope="view==='global'?'全局':selected" :model-value="view==='global'?modelValue.admission:template.admission" :readonly="readonly" :fields="catalog?.predicate_fields||[]" :rules="ruleNames" :client="client" :status="status" :policy="modelValue" @validity="admissionErrors={...admissionErrors,[$event.scope]:$event.errors}" @update:model-value="view==='global'?update({admission:$event}):edit('admission',$event)"/></KeepAlive>
  <section v-if="view==='shared'||view==='global'"><BackButton v-if="view==='shared'" :to="selected||'策略'" @click="show('rules')"/>
   <details class="sb-content-group sb-disclosure" :open="view==='shared'">
    <summary><span><strong>共享匹配规则</strong><small>{{Object.keys(modelValue.overrides).length}} 条自定义规则 · 供其他条件重复使用</small></span></summary>
    <p class="sb-scope-note">修改同名规则会影响所有引用位置，包括系统质量识别。</p>
    <p v-if="!visibleRules.length" class="sb-muted">没有可显示的规则。</p>
    <form class="sb-add-rule" @submit.prevent="addRule"><label>规则名称<input v-model="ruleName" maxlength="80" :disabled="readonly" placeholder="例如：只收录高做种资源"></label><VBtn type="submit" variant="tonal" :disabled="readonly||!ruleName.trim()||Object.hasOwn(ruleDefinitions,ruleName.trim())">添加共享规则</VBtn></form>
    <p class="sb-rule-usage-help">添加并填写条件后，点“用于策略”选择使用位置。保存质量策略后生效。</p>
    <article v-for="[name,expression] in visibleRules" :key="name" class="sb-shared-rule" :aria-label="'共享规则 '+name">
     <div class="sb-rule-list-row"><div><strong>{{catalog?.rule_descriptions?.[name]?.title||name}}</strong><p>{{Object.hasOwn(modelValue.overrides,name)?'使用自定义匹配条件':catalog?.rule_descriptions?.[name]?.summary||'展开编辑以查看匹配条件'}}</p><small>{{Object.hasOwn(modelValue.overrides,name)?((catalog?.predicate_rules||[]).includes(name)?'已覆盖内置规则':'自定义共享规则'):'内置规则'}}<span v-if="ruleUses(name).length"> · 已用于 {{ruleUses(name).join('、')}}</span></small></div><div class="sb-actions"><VBtn variant="text" :aria-expanded="editingRule===name" @click="openRule(name)">{{editingRule===name?'收起编辑':'编辑规则'}}</VBtn><VBtn variant="text" :disabled="readonly||!!ruleErrors[name]?.length" @click="beginUseRule(name)">用于策略</VBtn><VBtn v-if="Object.hasOwn(modelValue.overrides,name)" variant="text" :disabled="readonly||!catalog?.rule_definitions?.[name]&&!!ruleUses(name).length" @click="rule(name,null)">{{catalog?.rule_definitions?.[name]?'恢复内置':'移除'}}</VBtn></div></div>
     <section v-if="openedRules.has(name)" v-show="editingRule===name" class="sb-shared-rule-editor" :aria-label="'编辑共享规则 '+name"><p class="sb-muted">修改后影响所有引用这条规则的位置。</p><PredicateEditor :model-value="expression" :readonly="readonly" :fields="catalog?.predicate_fields||[]" :rules="ruleNames.filter(rule=>rule!==name)" @validity="ruleErrors={...ruleErrors,[name]:$event}" @update:model-value="rule(name,$event)"/></section>
     <div v-if="usingRule===name" class="sb-use-rule"><div class="sb-use-rule-form"><label>使用这条规则的策略<select v-model="ruleTarget" :disabled="readonly"><option v-for="policy in policyNames" :key="policy" :value="policy">{{policy}}</option></select></label><VBtn variant="tonal" :disabled="readonly||!ruleTarget||!!ruleErrors[name]?.length||!!admissionErrors[ruleTarget]?.length" @click="applyRule(name)">加入收录条件</VBtn></div><p>候选需同时满足这条规则和策略原有的条件。</p><p v-if="admissionErrors[ruleTarget]?.length" class="sb-error">请先完成这份策略中尚未填写的条件。</p><div v-if="ruleNotice" class="sb-rule-applied" role="status"><span>{{ruleNotice}}</span><VBtn variant="text" @click="choose(ruleTarget);show('rules')">查看策略</VBtn></div></div>
    </article>
   </details>
  </section>
 </div>
</section>
</template>
