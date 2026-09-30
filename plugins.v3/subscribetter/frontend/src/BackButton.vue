<script setup>
defineProps({to:{type:String,default:'上一页'},disabled:Boolean});
</script>
<template><button type="button" class="sb-back-button" :disabled="disabled"><svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg><span>返回{{to}}</span></button></template>
