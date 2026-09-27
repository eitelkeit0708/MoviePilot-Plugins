<script setup>
import Page from './Page.vue';
defineProps({api:Object,pluginId:String,sourcePluginId:String,initialConfig:Object});
const emit=defineEmits(['save','close','switch','layout']);
</script>
<template><Page :api="api" :plugin-id="pluginId" :source-plugin-id="sourcePluginId" start-section="settings" @save="emit('save',$event)" @close="emit('close')" @switch="emit('switch')" @layout="emit('layout',$event)"/></template>
