<script setup>
import {computed} from 'vue';
import {durationDisplay,durationMinutes,fieldLabels,operatorLabels,sizeDisplay,sizeBytes} from './predicate-editor.mjs';
const props=defineProps({node:Object,readonly:Boolean,fields:Array,rules:Array,depth:{type:Number,default:0},root:Boolean,errors:{type:Array,default:()=>[]}}),emit=defineEmits(['action']);
const numericFields=new Set(['size','seeders','downloadvolumefactor','publish_minutes']);
const textFields=new Set(['text','title','description','subtitle_description','original_language','media_type']);
const rowErrors=computed(()=>props.errors.filter(error=>error.id===props.node.id));
const operators=computed(()=>numericFields.has(props.node.field)?['gt','ge','lt','le','eq','ne']:textFields.has(props.node.field)?['contains','regex','eq','ne','in']:['eq','ne','in','intersects']);
const groupId=computed(()=>`condition-group-${props.node.id}`);
const groupTitle=computed(()=>{
 if(props.root)return '满足以下';
 if(props.node.name)return props.node.name;
 const children=props.node.children||[],field=children[0]?.field;
 if(field&&children.every(child=>child.kind==='condition'&&child.field===field))return `${fieldLabels[field]||field}${children.every(child=>['contains','regex'].includes(child.operator))?'匹配':''}组`;
 return '条件组';
});
const patch=value=>emit('action',{type:'patch',id:props.node.id,value});
function field(event){const value=event.target.value;patch({field:value,unit:value==='size'?'B':value==='publish_minutes'?'分钟':''})}
function scalar(event){const raw=event.target.value;if(raw===''){patch({raw,incomplete:true});return}const value=Number(raw);patch(Number.isFinite(value)?{value,raw:'',incomplete:false}:{raw,incomplete:true})}
function size(event){const raw=event.target.value;try{patch({value:sizeBytes(raw,props.node.unit||'B'),raw:'',incomplete:false})}catch{patch({raw,incomplete:true})}}
function unit(event){const next=event.target.value;patch({unit:next,raw:sizeDisplay(props.node.value,next)})}
function duration(event){const raw=event.target.value;try{patch({value:durationMinutes(raw,props.node.unit||'分钟'),raw:'',incomplete:false})}catch{patch({raw,incomplete:true})}}
function durationUnit(event){const next=event.target.value;patch({unit:next,raw:durationDisplay(props.node.value,next)})}
function textValue(event){patch({value:event.target.value,raw:'',incomplete:event.target.value===''})}
function arrayValue(event){const values=event.target.value.split(',').map(value=>value.trim()).filter(Boolean);patch({value:values,incomplete:!values.length})}
</script>

<template>
 <fieldset v-if="node.kind==='group'" class="sb-condition-group" :style="{'--condition-depth':Math.min(depth,3)}">
  <legend><span>{{groupTitle}}</span><select aria-label="组合方式" :value="node.operator" :disabled="readonly" @change="emit('action',{type:'relation',id:node.id,value:$event.target.value})"><option value="all">全部条件（并且）</option><option value="any">任一条件（或者）</option></select><span>{{node.children.length}} 条</span><button type="button" :aria-expanded="String(!node.collapsed)" :aria-controls="groupId" @click="emit('action',{type:'collapse',id:node.id})">{{node.collapsed?'展开':'折叠'}}</button><button v-if="!root" type="button" :disabled="readonly" @click="emit('action',{type:'remove',id:node.id})">删除整组（{{node.children.length}} 条）</button></legend>
  <div :id="groupId" v-show="!node.collapsed" class="sb-condition-group-body">
   <template v-for="(child,index) in node.children" :key="child.id"><span v-if="index" class="sb-condition-join">{{node.operator==='all'?'并且':'或者'}}</span><ConditionNode :node="child" :readonly="readonly" :fields="fields" :rules="rules" :depth="depth+1" :errors="errors" @action="emit('action',$event)"/></template>
   <div class="sb-condition-add"><button type="button" :disabled="readonly" @click="emit('action',{type:'add',id:node.id})">添加条件</button><button type="button" :disabled="readonly||depth>=13" @click="emit('action',{type:'add-group',id:node.id})">添加子组</button><button type="button" :disabled="readonly" @click="emit('action',{type:'negate',id:node.id})">取反此组</button></div>
  </div>
 </fieldset>
 <fieldset v-else-if="node.kind==='not'" class="sb-condition-group sb-condition-not"><legend>此组取反 <button type="button" :disabled="readonly" @click="emit('action',{type:'negate',id:node.id})">取消取反</button><button v-if="!root" type="button" :disabled="readonly" @click="emit('action',{type:'remove',id:node.id})">删除整组</button></legend><ConditionNode :node="node.child" :readonly="readonly" :fields="fields" :rules="rules" :depth="depth+1" :errors="errors" @action="emit('action',$event)"/></fieldset>
 <div v-else-if="node.kind==='condition'" class="sb-condition-row" :data-condition-id="node.id">
  <label><span class="sb-sr-only">字段</span><select aria-label="字段" :value="node.field" :disabled="readonly" @change="field"><option v-for="field in fields" :key="field" :value="field">{{fieldLabels[field]||field}}</option></select></label>
  <label><span class="sb-sr-only">判断关系</span><select aria-label="判断关系" :value="node.operator" :disabled="readonly" @change="emit('action',{type:'operator',id:node.id,value:$event.target.value})"><option v-for="operator in operators" :key="operator" :value="operator">{{operatorLabels[operator]}}</option></select></label>
  <template v-if="node.field==='size'&&typeof node.value==='number'"><label class="sb-condition-value"><span class="sb-sr-only">比较值</span><input aria-label="比较值" inputmode="decimal" :value="node.raw!==undefined&&node.raw!==''?node.raw:sizeDisplay(node.value,node.unit||'B')" :disabled="readonly" @input="size"></label><label class="sb-condition-unit"><span class="sb-sr-only">单位</span><select aria-label="单位" :value="node.unit||'B'" :disabled="readonly" @change="unit"><option v-for="name in ['B','KiB','MiB','GiB','MB','GB']" :key="name">{{name}}</option></select></label></template>
  <template v-else-if="node.field==='publish_minutes'&&typeof node.value==='number'"><label class="sb-condition-value"><span class="sb-sr-only">比较值</span><input aria-label="比较值" inputmode="decimal" :value="node.raw!==undefined&&node.raw!==''?node.raw:durationDisplay(node.value,node.unit||'分钟')" :disabled="readonly" @input="duration"></label><label class="sb-condition-unit"><span class="sb-sr-only">单位</span><select aria-label="单位" :value="node.unit||'分钟'" :disabled="readonly" @change="durationUnit"><option v-for="name in ['分钟','小时','天']" :key="name">{{name}}</option></select></label></template>
  <template v-else-if="Array.isArray(node.value)"><label class="sb-condition-value"><span class="sb-sr-only">比较值</span><input aria-label="比较值" :value="node.value.join(', ')" :disabled="readonly" @input="arrayValue"></label></template>
  <template v-else-if="typeof node.value==='boolean'"><label class="sb-condition-value"><span class="sb-sr-only">比较值</span><select aria-label="比较值" :value="String(node.value)" :disabled="readonly" @change="patch({value:$event.target.value==='true'})"><option value="true">是</option><option value="false">否</option></select></label></template>
  <template v-else-if="node.value===null||typeof node.value==='object'"><span class="sb-condition-advanced">高级值：{{node.value===null?'空值':JSON.stringify(node.value)}}</span></template>
  <template v-else-if="typeof node.value==='number'"><label class="sb-condition-value"><span class="sb-sr-only">比较值</span><input aria-label="比较值" type="number" :value="node.raw!==undefined&&node.raw!==''?node.raw:node.value" :disabled="readonly" @input="scalar"></label></template>
  <template v-else><label class="sb-condition-value"><span class="sb-sr-only">比较值</span><textarea v-if="node.operator==='regex'" aria-label="正则表达式" rows="1" :value="node.value" :disabled="readonly" @input="textValue"/><input v-else aria-label="比较值" :value="node.value" :disabled="readonly" @input="textValue"></label></template>
  <div v-if="!root" class="sb-condition-actions"><button type="button" :disabled="readonly" @click="emit('action',{type:'duplicate',id:node.id})">复制</button><button type="button" :disabled="readonly" @click="emit('action',{type:'wrap',id:node.id})">包为子组</button><button type="button" :disabled="readonly" @click="emit('action',{type:'remove',id:node.id})">删除</button></div>
  <p v-for="error in rowErrors" :key="error.code" class="sb-condition-error" role="alert">{{error.message}}</p>
 </div>
 <div v-else-if="node.kind==='registered'" class="sb-condition-row" :data-condition-id="node.id"><span>使用规则</span><select aria-label="使用的规则" :value="node.name" :disabled="readonly" @change="patch({name:$event.target.value})"><option v-for="name in rules" :key="name">{{name}}</option></select><div v-if="!root" class="sb-condition-actions"><button type="button" :disabled="readonly" @click="emit('action',{type:'duplicate',id:node.id})">复制</button><button type="button" :disabled="readonly" @click="emit('action',{type:'wrap',id:node.id})">包为子组</button><button type="button" :disabled="readonly" @click="emit('action',{type:'remove',id:node.id})">删除</button></div><p v-for="error in rowErrors" :key="error.code" class="sb-condition-error" role="alert">{{error.message}}</p></div>
 <div v-else class="sb-condition-row" :data-condition-id="node.id"><span>固定结果</span><select aria-label="固定结果" :value="String(node.value)" :disabled="readonly" @change="patch({value:$event.target.value==='true'})"><option value="true">恒为通过</option><option value="false">恒为不通过</option></select><div v-if="!root" class="sb-condition-actions"><button type="button" :disabled="readonly" @click="emit('action',{type:'duplicate',id:node.id})">复制</button><button type="button" :disabled="readonly" @click="emit('action',{type:'wrap',id:node.id})">包为子组</button><button type="button" :disabled="readonly" @click="emit('action',{type:'remove',id:node.id})">删除</button></div></div>
</template>
