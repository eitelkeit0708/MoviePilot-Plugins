<script setup>
import {computed,ref,inject} from 'vue';
import {resolve,variant,initial} from './schema.mjs';
import {label as translate,optionLabel} from './labels.mjs';
const props=defineProps({schema:{type:Object,default:()=>({})},modelValue:{},name:{type:String,default:''},readonly:Boolean,options:{type:Array,default:()=>[]}});
const emit=defineEmits(['update:modelValue']);
const catalogs=inject('subscribetter:catalogs',ref({}));
const schema=computed(()=>variant(props.schema,props.modelValue));
const base=computed(()=>resolve(props.schema));
const nullable=computed(()=>base.value.anyOf?.some(s=>s.type==='null'));
const flexible=computed(()=>(!base.value.type&&!base.value.properties&&!base.value.anyOf&&!base.value.enum)||(base.value.anyOf?.filter(s=>s.type!=='null').length>1));
const title=computed(()=>translate(props.name));
const dynamicKey=ref('');const dynamicType=ref('string');
const type=computed(()=>schema.value.type|| (Array.isArray(props.modelValue)?'array':props.modelValue&&typeof props.modelValue==='object'?'object':typeof props.modelValue));
const update=value=>emit('update:modelValue',value);
function child(key,value){update({...props.modelValue,[key]:value})}
function remove(key){const copy={...props.modelValue};delete copy[key];update(copy)}
function item(index,value){const copy=[...props.modelValue];copy[index]=value;update(copy)}
function addKey(){const key=dynamicKey.value.trim();if(!key||Object.hasOwn(props.modelValue||{},key)||['__proto__','prototype','constructor'].includes(key))return;const s=schema.value.additionalProperties;child(key,initial(s&&typeof s==='object'?s:{type:dynamicType.value}));dynamicKey.value=''}
const choices=computed(()=>props.options.length?props.options:catalogs.value[props.name]||schema.value.enum|| (schema.value.const!==undefined?[schema.value.const]:[]));
</script>
<template>
  <div class="sb-field">
    <label v-if="flexible&&!readonly">{{title}} · 值类型<select :value="modelValue===null?'null':type" @change="update(initial({type:$event.target.value}))"><option value="string">文本</option><option value="number">数值</option><option value="boolean">开关</option><option value="object">条件 / 映射</option><option value="array">列表</option><option value="null">空值</option></select></label>
    <label v-if="nullable" class="sb-null"><input type="checkbox" :checked="modelValue!==null" :disabled="readonly" @change="update($event.target.checked?initial(base.anyOf.find(s=>s.type!=='null')):null)"> {{ title }} · 设置明确值</label>
    <span v-if="modelValue===null&&(nullable||flexible)" class="sb-muted">未设置 / 使用该字段的继承语义</span>
    <fieldset v-else-if="type==='object'" :disabled="readonly"><legend>{{title}}</legend>
      <Field v-for="(s,key) in schema.properties||{}" :key="key" :schema="s" :name="key" :model-value="modelValue?.[key]" :readonly="readonly" @update:model-value="child(key,$event)" />
      <div v-for="key in Object.keys(modelValue||{}).filter(k=>!schema.properties?.[k])" :key="key" class="sb-map-entry">
        <Field :schema="typeof schema.additionalProperties==='object'?schema.additionalProperties:{}" :name="key" :model-value="modelValue[key]" :readonly="readonly" @update:model-value="child(key,$event)" />
        <button v-if="!readonly" type="button" @click="remove(key)" :aria-label="'移除 '+key">移除此项</button>
      </div>
      <div v-if="!readonly&&schema.additionalProperties!==false" class="sb-inline"><input v-model="dynamicKey" :aria-label="title+' 新条目名称'" placeholder="条目名称（精确配置引用）"><select v-if="!schema.additionalProperties||schema.additionalProperties===true" v-model="dynamicType" aria-label="新值类型"><option value="string">文本</option><option value="number">数值</option><option value="boolean">开关</option><option value="object">条件 / 映射</option><option value="array">列表</option></select><button type="button" @click="addKey">添加条目</button></div>
    </fieldset>
    <fieldset v-else-if="type==='array'" :disabled="readonly"><legend>{{title}} <span class="sb-muted">{{modelValue?.length||0}} 项</span></legend>
      <div v-for="(value,index) in modelValue||[]" :key="index" class="sb-map-entry"><Field :schema="schema.items||{}" :name="String(index+1)" :model-value="value" :readonly="readonly" @update:model-value="item(index,$event)" /><button v-if="!readonly" type="button" @click="update(modelValue.filter((_,i)=>i!==index))" :aria-label="title+' 移除第 '+(index+1)+' 项'">移除</button></div>
      <button v-if="!readonly" type="button" :disabled="schema.maxItems!==undefined&&(modelValue?.length||0)>=schema.maxItems" @click="update([...(modelValue||[]),initial(schema.items||{})])">添加{{title}}</button>
    </fieldset>
    <label v-else-if="type==='boolean'" class="sb-check"><input type="checkbox" :checked="modelValue" :disabled="readonly||schema.const!==undefined" @change="update($event.target.checked)">{{title}}</label>
    <label v-else-if="choices.length">{{title}}<select :value="modelValue" :disabled="readonly||schema.const!==undefined" @change="update(choices.find(v=>String(typeof v==='object'?v.value:v)===$event.target.value)?.value??choices.find(v=>String(v)===$event.target.value)??$event.target.value)"><option v-for="choice in choices" :key="choice.value??choice" :value="choice.value??choice">{{choice.title??optionLabel(choice)}}</option></select></label>
    <label v-else-if="['integer','number'].includes(type)">{{title}}<input type="number" :value="modelValue" :readonly="readonly" :min="schema.minimum??schema.exclusiveMinimum" :max="schema.maximum" :step="type==='integer'?1:'any'" @input="update($event.target.value===''?null:Number($event.target.value))"></label>
    <label v-else>{{title}}<textarea v-if="/prompt|description|subtitle|original/.test(name)" :value="modelValue??''" :readonly="readonly" :maxlength="schema.maxLength" rows="4" @input="update($event.target.value)"/><input v-else :type="schema.writeOnly?'password':'text'" :value="modelValue??''" :readonly="readonly" :maxlength="schema.maxLength" autocomplete="off" @input="update($event.target.value)"></label>
  </div>
</template>
