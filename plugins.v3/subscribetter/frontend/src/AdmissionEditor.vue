<script setup>
import {ref,watch} from 'vue';
import PredicateEditor from './PredicateEditor.vue';
import ConditionTest from './ConditionTest.vue';
const props=defineProps({modelValue:Object,scope:String,readonly:Boolean,fields:Array,rules:Array,client:Object,status:Object,policy:Object});
const emit=defineEmits(['update:modelValue','validity']);
const copy=value=>value==null?value:JSON.parse(JSON.stringify(value));
const enabled=ref(!!props.modelValue),draft=ref(copy(props.modelValue)),cache=ref(copy(props.modelValue)),errors=ref([]),test=ref(null);
let pending;
watch(()=>props.modelValue,value=>{if(JSON.stringify(value)===pending){pending=undefined;return}enabled.value=!!value;draft.value=copy(value);cache.value=copy(value);errors.value=[];test.value=null},{deep:true});
watch(errors,value=>emit('validity',{scope:props.scope,errors:value}),{deep:true,immediate:true});
function write(value){pending=JSON.stringify(value);emit('update:modelValue',value)}
function change(value){draft.value=copy(value);cache.value=copy(value);test.value=null;write(value)}
function toggle(value){enabled.value=value;test.value=null;if(!value){errors.value=[];write(null)}else if(cache.value){draft.value=copy(cache.value);write(copy(cache.value))}else clear()}
function clear(){draft.value={all:[]};errors.value=[{code:'EMPTY_GROUP',message:'请至少添加一条条件。'}];write(null)}
</script>
<template><section class="sb-content-group sb-admission-editor" :aria-label="scope+'额外收录条件'"><div class="sb-group-heading sb-group-toggle"><div><h3>额外收录条件</h3><p>{{scope==='全局'?'所有候选还需满足这组条件。':'只对“'+scope+'”生效，并且仍需满足全局条件。'}}</p></div><label class="sb-check"><input type="checkbox" :checked="enabled" :disabled="readonly" @change="toggle($event.target.checked)">启用额外收录条件</label></div><PredicateEditor v-if="enabled" testable :model-value="draft" :readonly="readonly" :fields="fields" :rules="rules" @validity="errors=$event" @test="test=$event.expression" @update:model-value="change"/><ConditionTest v-if="test&&client&&status" :client="client" :status="status" :policy="policy" :expression="test"/><div v-if="enabled" class="sb-actions"><VBtn variant="text" :disabled="readonly" @click="clear">清空条件</VBtn><VBtn v-if="!modelValue&&cache" variant="text" :disabled="readonly" @click="toggle(true)">撤销清空</VBtn></div><p v-else-if="cache" class="sb-muted">已停用。重新启用可恢复本次编辑的条件。</p></section></template>
