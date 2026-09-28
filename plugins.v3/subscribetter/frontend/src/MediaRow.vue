<script setup>
defineProps({title:String,meta:String,poster:String,type:String,interactive:Boolean,selected:Boolean});defineEmits(['open']);
</script>
<template><component :is="interactive?'button':'article'" class="sb-media-row" :type="interactive?'button':undefined" :aria-pressed="interactive?selected:undefined" @click="interactive&&$emit('open',$event)">
 <span class="sb-media-cover"><img v-if="poster" :src="poster" alt="" loading="lazy"/><svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path :d="type==='电影'?'M7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4':'m8 2 4 3 4-3M9 22h6'"/></svg></span>
 <span class="sb-media-identity"><strong>{{title}}</strong><span>{{meta}}</span></span><span class="sb-media-collection"><slot name="collection"/></span><span class="sb-media-progress"><slot name="progress"/></span><span class="sb-media-actions"><slot name="actions"><span v-if="interactive" class="sb-row-action">查看</span></slot></span>
</component></template>
