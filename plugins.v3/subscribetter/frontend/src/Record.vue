<script setup>
import {computed} from 'vue';
import {label} from './labels.mjs';
const props=defineProps({value:{},name:{type:String,default:''},depth:{type:Number,default:0}});
const entries=computed(()=>Object.entries(props.value||{}));
const scalar=v=>v===null||typeof v!=='object';
</script>
<template>
  <span v-if="scalar(value)" class="sb-value">{{value===null?'未观测 / 未设置':typeof value==='boolean'?(value?'是':'否'):value===''?'—':value}}</span>
  <div v-else-if="Array.isArray(value)" class="sb-record-array">
    <p v-if="!value.length" class="sb-muted">无{{label(name)}}记录</p>
    <ol v-else-if="value.every(scalar)"><li v-for="(item,index) in value" :key="index"><Record :value="item" /></li></ol>
    <details v-else v-for="(item,index) in value" :key="index" :open="value.length<=3"><summary>{{item?.title||item?.name||item?.id||item?.target_key||label(name)+' '+(index+1)}}</summary><Record :value="item" :depth="depth+1" /></details>
  </div>
  <dl v-else class="sb-record"><template v-for="[key,item] in entries" :key="key"><dt>{{label(key)}}</dt><dd><details v-if="!scalar(item)&&depth>0"><summary>{{Array.isArray(item)?item.length+' 项':'查看原始数据'}}</summary><Record :value="item" :name="key" :depth="depth+1" /></details><Record v-else :value="item" :name="key" :depth="depth+1" /></dd></template></dl>
</template>
