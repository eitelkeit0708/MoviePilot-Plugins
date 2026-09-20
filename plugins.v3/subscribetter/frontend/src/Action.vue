<script setup>
import {computed,ref,onBeforeUnmount} from 'vue';
import Field from './Field.vue';import Record from './Record.vue';
import {bodySchema,parameters,initial,clone,validate,resolve} from './schema.mjs';
import {pathFor,applyBody,errorText,id} from './client.mjs';
const props=defineProps({action:Array,client:Object,status:Object,context:{type:Object,default:()=>({})}});
const emit=defineEmits(['close','changed']);let mounted=true;
const schema=bodySchema(props.action[1]);const fields=ref(initial(schema));const paths=ref({});const errors=ref([]),busy=ref(false),preview=ref(null),review=ref(null),result=ref(null),attempted=ref(false);
const op=id();const hidden=['config_revision','runtime_generation','operation_id'];
for(const key of Object.keys(schema.properties||{}))if(props.context[key]!==undefined)fields.value[key]=clone(props.context[key]);
for(const p of parameters(props.action[1]).filter(p=>p.in==='path'))paths.value[p.name]=props.context[p.name]??initial(p.schema);
const pathParams=parameters(props.action[1]).filter(p=>p.in==='path');
const editable=computed(()=>Object.entries(schema.properties||{}).filter(([key])=>!hidden.includes(key)));
const receiptPath=ref('');
async function prepare(){if(busy.value)return;errors.value=[];
 const body=clone(fields.value);if(schema.properties?.config_revision){body.config_revision=props.status.current.value.revision;body.runtime_generation=props.status.health.value.generation;}if(schema.properties?.operation_id)body.operation_id=op;
 errors.value=validate(schema,body);if(errors.value.length)return;
 busy.value=true;try{receiptPath.value=pathFor(props.action[1],paths.value);if(props.action[1].endsWith('/preview')){const p=await props.client.post(receiptPath.value,body);if(mounted)preview.value=Object.freeze(clone(p));}else review.value=Object.freeze(body);}catch(e){if(mounted)errors.value=[errorText(e)];}finally{if(mounted)busy.value=false;}}
async function execute(){if(busy.value||attempted.value)return;busy.value=true;attempted.value=true;errors.value=[];
 try{const r=preview.value?await props.client.post(receiptPath.value.replace(/\/preview$/,'/apply'),applyBody(preview.value,op)):await props.client.post(receiptPath.value,review.value);if(mounted){result.value=r;emit('changed');}}
 catch(e){if(mounted)errors.value=[errorText(e)];}finally{if(mounted)busy.value=false;}}
async function observe(){busy.value=true;try{const r=await props.client.get('/management/operations/'+op);if(mounted)result.value=r;}catch(e){if(mounted)errors.value=[errorText(e)]}finally{if(mounted)busy.value=false}}
onBeforeUnmount(()=>{mounted=false;fields.value={};review.value=null});
</script>
<template><div class="sb-root sb-dialog" role="dialog" aria-modal="true" :aria-label="action[0]"><h2>{{action[0]}}</h2>
 <template v-if="!preview&&!review&&!result"><p>只提交当前表单中的精确范围。服务端仍校验当前配置、代次、归属与权限。</p><Field v-for="p in pathParams" :key="p.name" :name="p.name" :schema="p.schema" v-model="paths[p.name]"/><Field v-for="[key,s] in editable" :key="key" :name="key" :schema="s" v-model="fields[key]"/><button :disabled="busy||!status.current.value||!!status.error.value" @click="prepare">{{action[1].endsWith('/preview')?'取得不可变预览':'核对待提交操作'}}</button></template>
 <template v-else-if="!result"><h3>{{preview?'服务端不可变预览':'待提交精确参数'}}</h3><Record :value="preview||review"/><p v-if="preview?.blockers?.length" role="alert" class="sb-error">存在阻塞；不能确认。刷新实际状态后重新预览。</p><p>操作 ID：<span class="sb-value">{{op}}</span></p><button :disabled="busy||attempted||!!preview?.blockers?.length" @click="execute">确认此对象与范围</button></template>
 <template v-if="result"><h3>服务器操作结果</h3><Record :value="result"/><p v-if="result.state==='UNKNOWN'" class="sb-error">结果仍未知；保留原操作 ID，不重发外部动作。</p><p v-if="result.result?.native_save_required">仅生成配置预览，尚未保存。复制操作 ID <strong>{{op}}</strong>，在配置页“从操作回执加载”后通过宿主 Save 提交。</p></template>
 <ul v-if="errors.length" role="alert" class="sb-error"><li v-for="e in errors" :key="e">{{e}}</li></ul><div class="sb-actions"><button v-if="attempted&&preview" :disabled="busy" @click="observe">按原操作 ID 查询回执</button><button :disabled="busy" @click="emit('close')">关闭</button></div>
</div></template>
