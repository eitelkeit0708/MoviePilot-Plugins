<script setup>
import {computed} from 'vue';
import VersionDifference from './VersionDifference.vue';
import {stateLabel,dateText,formatBytes,processingNext,unitLabel,downloadPercent} from './media.mjs';
const props=defineProps({unit:Object,health:Object});
const p=computed(()=>props.unit.processing),percent=computed(()=>downloadPercent(p.value?.download));
const waiting=computed(()=>p.value?.transfer_files?.filter(f=>!f.remote_verified)||[]);
const versions=computed(()=>props.unit.current_quality||[]);
const changeFor=version=>{
 const change=p.value?.change,comparison=change?.baseline_current&&change.versions?.find(v=>v.version_id===version?.version_id);
 return change?{...change,changes:comparison?.changes||[]}:null;
};
</script>
<template><article class="sb-episode" :data-processing="!!p" :data-attention="p?.next_step==='RECONCILE'">
 <strong class="sb-episode-number">{{unitLabel(unit)}}</strong>
 <div class="sb-unit-versions"><template v-if="versions.length"><section v-for="(version,index) in versions" :key="version.version_id||index"><small v-if="versions.length>1">在库版本 {{index+1}}</small><VersionDifference :current="version.quality" :target="p?.quality" :change="changeFor(version)" :current-state="unit.current_facts?.state" :pending="!!p"/></section></template><VersionDifference v-else :target="p?.quality" :change="changeFor(null)" :current-state="unit.current_facts?.state" :pending="!!p"/><small v-if="p&&!p.quality">本轮规格尚未确认</small></div>
 <div class="sb-episode-stage"><template v-if="p"><strong class="sb-stage-label" :data-state="p.phase">{{stateLabel(p.phase)}}</strong>
  <template v-if="p.phase==='DOWNLOADING'"><progress v-if="percent!==null" :value="p.download.downloaded_bytes" :max="p.download.total_bytes" :aria-label="unitLabel(unit)+'下载进度'"/><span class="sb-progress-numbers">{{percent!==null?percent+'% · ':''}}{{p.download?.downloaded_bytes==null?'尚未取得下载进度':formatBytes(p.download.downloaded_bytes)+' / '+formatBytes(p.download.total_bytes)}}</span></template>
  <span v-else-if="p.phase==='RAPID_WAIT'&&waiting.length===1">未命中 {{waiting[0].misses??'未知'}} / {{waiting[0].miss_limit??'上限未记录'}} 次</span>
  <span v-else-if="p.phase==='RAPID_WAIT'">{{waiting.length}} 个文件等待秒传</span>
  <span v-else-if="p.phase==='WAITING_ASSETS'&&p.attachments?.required">必要字幕 {{p.attachments.local_ready}} / {{p.attachments.required}} 已齐备</span>
  <small v-if="p.phase!=='DOWNLOADING'||percent===null||p.next_at||!health?.ordinary_work_active||health?.paused" :title="p.next_at?dateText(p.next_at):undefined">{{processingNext(p,health)}}</small>
 </template><template v-else><strong>{{unit.owner_plan_id?'处理记录待核对':versions.length?'已收录':'等待新资源'}}</strong><small v-if="unit.owner_plan_id">原计划已失效或待核实</small><small v-else-if="unit.cooldown_until">{{dateText(unit.cooldown_until)}} 后检查</small></template></div>
 <div class="sb-unit-actions"><slot name="actions"/><slot/></div><div class="sb-unit-archive"><slot name="archive"/></div>
</article></template>
