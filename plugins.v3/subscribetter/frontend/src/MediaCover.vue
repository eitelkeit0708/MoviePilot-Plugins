<script setup>
import {computed,ref,watch} from 'vue';
const props=defineProps({src:String,type:String});
const failed=ref(false);
const source=computed(()=>{try{const u=new URL(props.src);return u.protocol==='https:'&&!u.username&&!u.password&&!u.search&&!u.hash&&(u.hostname==='image.tmdb.org'||u.hostname.endsWith('.doubanio.com'))?u.href:null}catch{return null}});
watch(()=>props.src,()=>{failed.value=false});
</script>
<template><span class="sb-cover" :class="{'sb-cover-empty':!source||failed}"><img v-if="source&&!failed" :src="source" alt="" loading="lazy" referrerpolicy="no-referrer" @error="failed=true"/><svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path :d="type==='电影'?'M7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4':'m8 2 4 3 4-3M9 22h6'"/></svg></span></template>
