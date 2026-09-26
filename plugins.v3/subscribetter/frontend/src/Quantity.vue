<script setup>
import {ref,computed} from 'vue';
const props=defineProps({modelValue:Number,label:String,kind:{default:'time'},readonly:Boolean,min:{default:0},max:Number,note:String});const emit=defineEmits(['update:modelValue']);
const units=computed(()=>props.kind==='bytes'?[[1,'B'],[1024,'KiB'],[1048576,'MiB'],[1073741824,'GiB']]:[[1,'秒'],[60,'分钟'],[3600,'小时'],[86400,'天']]);
const unit=ref([...units.value].reverse().find(([n])=>props.modelValue>0&&props.modelValue%n===0)?.[0]||1);
function edit(event){const raw=event.target.value;emit('update:modelValue',raw===''?null:Number(raw)*unit.value)}
</script>
<template><label class="sb-quantity">{{label}}<span class="sb-quantity-input"><input type="number" :aria-label="label" :value="modelValue==null?'':modelValue/unit" :min="min/unit" :max="max==null?undefined:max/unit" step="any" :disabled="readonly" placeholder="未填写" @input="edit"><select v-model.number="unit" :aria-label="label+'单位'" :disabled="readonly"><option v-for="[n,title] in units" :key="n" :value="n">{{title}}</option></select></span><small v-if="note" class="sb-muted">{{note}}</small></label></template>
