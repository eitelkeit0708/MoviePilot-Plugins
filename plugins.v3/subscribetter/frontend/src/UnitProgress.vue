<script setup>
import {computed} from 'vue';
import {compactQuality,qualitySummary,qualityExtra,qualityOrigin,stateLabel,fileStateLabel,reasonText,dateText,formatBytes,processingNext,unitLabel,downloadPercent} from './media.mjs';
const props=defineProps({unit:Object,health:Object});
const p=computed(()=>props.unit.processing),percent=computed(()=>downloadPercent(p.value?.download));
const waiting=computed(()=>p.value?.transfer_files?.filter(f=>!f.remote_verified)||[]);
const versions=computed(()=>props.unit.current_quality||[]);
</script>
<template><article class="sb-episode" :data-attention="p?.next_step==='RECONCILE'">
 <strong class="sb-episode-number">{{unitLabel(unit)}}</strong>
 <div class="sb-version"><span class="sb-mobile-label">库内版本</span><strong>{{versions.length===1?compactQuality(versions[0].quality):versions.length>1?versions.length+' 个已确认版本':unit.current_facts?.state==='MISSING'?'尚未在库内找到':'库内信息待核实'}}</strong><small v-if="versions.length===1">{{qualityExtra(versions[0].quality)}}</small></div>
 <div class="sb-version sb-version-new"><span class="sb-mobile-label">升级版本</span><strong>{{p?.quality?compactQuality(p.quality):p?'升级规格未记录':'没有在途升级'}}</strong><small v-if="p?.quality">{{qualityExtra(p.quality)}}</small><small v-if="p?.quality" class="sb-origin">{{qualityOrigin(p.quality)}}</small></div>
 <div class="sb-episode-stage"><template v-if="p"><strong class="sb-stage-label" :data-state="p.phase">{{stateLabel(p.phase)}}</strong>
  <template v-if="p.phase==='DOWNLOADING'"><progress v-if="percent!==null" :value="p.download.downloaded_bytes" :max="p.download.total_bytes" :aria-label="unitLabel(unit)+'下载进度'"/><span class="sb-progress-numbers">{{percent!==null?percent+'% · ':''}}{{p.download?.downloaded_bytes==null?'尚未取得下载进度':formatBytes(p.download.downloaded_bytes)+' / '+formatBytes(p.download.total_bytes)}}</span></template>
  <span v-else-if="p.phase==='RAPID_WAIT'&&waiting.length===1">未命中 {{waiting[0].misses??'未知'}} / {{waiting[0].miss_limit??'上限未记录'}} 次</span>
  <span v-else-if="p.phase==='RAPID_WAIT'">{{waiting.length}} 个文件等待秒传</span>
  <span v-else-if="p.phase==='WAITING_ASSETS'&&p.attachments?.required">字幕及附件 {{p.attachments.local_ready}} / {{p.attachments.required}} 已齐备</span>
  <small :title="p.next_at?dateText(p.next_at):undefined">{{processingNext(p,health)}}</small>
 </template><template v-else><strong>{{unit.owner_plan_id?'处理记录待核对':unit.cooldown_until?'等待再次检查升级':'等待新资源'}}</strong><small v-if="unit.owner_plan_id">原计划已失效或待核实</small><small v-else-if="unit.cooldown_until">{{dateText(unit.cooldown_until)}}</small></template></div>
 <details class="sb-episode-details"><summary :aria-label="unitLabel(unit)+'版本与处理详情'">详情</summary><div class="sb-episode-expanded">
  <section><h4>库内记录</h4><div v-for="(version,index) in versions" :key="version.version_id||index"><strong>{{versions.length>1?'版本 '+(index+1)+' · ':''}}{{qualitySummary(version.quality)}}</strong><p>{{qualityExtra(version.quality)}}</p><small>{{qualityOrigin(version.quality)}}</small></div><p v-if="!versions.length">缺少可靠的库内规格记录。</p><small v-if="unit.last_ingest_confirmed_at">最近确认 {{dateText(unit.last_ingest_confirmed_at)}}</small><slot name="archive"/></section>
  <section v-if="p"><h4>本轮升级</h4><p>{{reasonText(p.reason)||'此计划没有记录具体比较原因'}}</p><p v-if="!p.quality">此计划没有单一、完整的规格摘要；请查看关联文件与版本档案。</p><p v-if="p.next_at">计划检查时间：{{dateText(p.next_at)}}</p><small v-if="p.download?.sampled_at">下载采样 {{dateText(p.download.sampled_at)}}{{p.download.shared_file?' · 包含跨集共用文件':''}}</small><p v-for="file in p.files" :key="file" class="sb-filename">{{file}}</p><ul class="sb-transfer-facts"><li v-for="(file,index) in p.transfer_files" :key="index"><strong>{{file.name||((file.role==='video'?'视频':'附件')+' '+(index+1))}}</strong><span>{{file.remote_verified?'云端暂存已确认':fileStateLabel(file.state)}}</span><span v-if="!file.remote_verified&&file.misses!=null">累计有效未命中 {{file.misses}} / {{file.miss_limit??'上限未记录'}} 次</span><span v-if="['CD2_UPLOADING','CD2_PAUSED'].includes(file.state)">向 CD2 发送 {{formatBytes(file.local_bytes_sent)}}；云端结果仍需确认</span></li></ul><small v-if="p.transfer_file_count>20">仅展示前 20 个文件，全部记录见传输页。</small><small v-if="p.attachments?.required">附件 {{p.attachments.required}} 份 · 本地齐备 {{p.attachments.local_ready}} · 云端确认 {{p.attachments.remote_verified}}</small><small>开始于 {{dateText(p.started_at)}}</small></section>
 </div><slot/></details>
</article></template>
