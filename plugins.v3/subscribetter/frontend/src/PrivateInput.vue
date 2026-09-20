<script setup>
import {ref,onBeforeUnmount} from 'vue';import {errorText,id} from './client.mjs';
const props=defineProps({client:Object,status:Object});let mounted=true;
const kind=ref('key'),value=ref(''),busy=ref(false),reference=ref(''),error=ref('');
async function write(){busy.value=true;reference.value='';error.value='';try{const r=await props.client.post('/credentials',{revision:props.status.current.value.revision,digest:props.status.current.value.digest,operation_id:id(),kind:kind.value,value:value.value});if(mounted)reference.value=r.reference;}catch(e){if(mounted)error.value=errorText(e)}finally{value.value='';if(mounted)busy.value=false}}
onBeforeUnmount(()=>{mounted=false;value.value=''});
</script>
<template><details><summary>私密凭据写入 / 轮换</summary><p>仅返回私密引用；输入不进入普通配置、导出、日志或浏览器存储。轮换后在配置中选择新引用，旧引用保留在私密存储预算内。</p><label>类型<select v-model="kind"><option value="key">AI 密钥</option><option value="endpoint">AI 端点</option><option value="legacy_source">授权旧服务地址</option><option value="legacy_token">旧服务读取凭据</option></select></label><label>一次性私密输入<input v-model="value" type="password" maxlength="4096" autocomplete="off"></label><button :disabled="busy||!value||!status.current.value||!!status.error.value" @click="write">写入并清空输入</button><p v-if="reference" role="status">已写入引用：<span class="sb-value">{{reference}}</span>。尚未更改普通配置。</p><p v-if="error" role="alert" class="sb-error">{{error}}</p></details></template>
