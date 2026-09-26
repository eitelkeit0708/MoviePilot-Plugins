<script setup>
import {qualitySummary,qualityExtra,qualityOrigin,stateLabel,fileStateLabel,reasonText,dateText,formatBytes,processingNext} from './media.mjs';
defineProps({unit:Object,health:Object});
</script>
<template><div class="sb-upgrade-progress"><template v-if="unit.processing">
 <strong class="sb-target-quality">{{unit.processing.quality?qualitySummary(unit.processing.quality,true):'目标规格未记录'}}</strong><span v-if="unit.processing.quality" class="sb-quality-extra">{{qualityExtra(unit.processing.quality)}}</span>
 <small class="sb-muted">{{unit.processing.quality?qualityOrigin(unit.processing.quality)+' · 尚未确认入库':'旧计划缺少完整规格，不能从文件名推断'}}</small>
 <div class="sb-progress-stage"><span class="sb-state">{{stateLabel(unit.processing.phase)}}</span><span v-if="unit.processing.reason">{{reasonText(unit.processing.reason)}}</span></div>
 <p v-if="unit.processing.phase==='DOWNLOADING'" class="sb-progress-fact">已下载 {{unit.processing.download?.downloaded_bytes==null?'未取得':formatBytes(unit.processing.download.downloaded_bytes)}} / {{formatBytes(unit.processing.download?.total_bytes)}}<small>{{unit.processing.download?.shared_file?'包含跨集共用文件':'本集所选视频及附件'}} · {{unit.processing.download?.sampled_at?'采样 '+dateText(unit.processing.download.sampled_at):'尚无采样记录'}}</small></p>
 <ul v-if="unit.processing.transfer_files?.length" class="sb-transfer-facts"><li v-for="(file,index) in unit.processing.transfer_files" :key="index"><span>{{file.role==='video'?'视频':'附件'}}{{unit.processing.transfer_files.length>1?' '+(index+1):''}} · {{file.remote_verified?'暂存已确认':fileStateLabel(file.state)}}</span><span v-if="!file.remote_verified">秒传已确认未命中 {{file.misses??'未取得'}} / {{file.miss_limit??'上限未记录'}} 次</span><span v-if="['CD2_UPLOADING','CD2_PAUSED'].includes(file.state)">本次向 CD2 发送 {{file.local_bytes_sent==null?'未取得':formatBytes(file.local_bytes_sent)}}（不代表云端已确认）</span></li></ul>
 <small v-if="unit.processing.attachments?.required">必要附件 {{unit.processing.attachments.required}} 份 · 本地确认 {{unit.processing.attachments.local_ready}} · 暂存确认 {{unit.processing.attachments.remote_verified}}</small>
 <p class="sb-next-action">{{processingNext(unit.processing,health)}}</p>
 <small v-if="unit.current_facts?.state==='PRESENT'" class="sb-muted">已有在库记录继续单独展示；本轮入库尚未确认。</small>
 <details class="sb-technical"><summary>文件与采样详情</summary><p v-for="file in unit.processing.files" :key="file" class="sb-filename">{{file}}</p><p v-if="unit.processing.transfer_file_count>20">共 {{unit.processing.transfer_file_count}} 个文件，此处仅显示前 20 个，完整进展见传输页。</p><p>开始于 {{dateText(unit.processing.started_at)}}</p><p>下载速度 {{unit.processing.download?.speed==null?'未取得':formatBytes(unit.processing.download.speed)+'/s'}}</p><code v-if="unit.processing.reason">{{unit.processing.reason}}</code></details>
 </template><span v-else>{{unit.owner_plan_id?'原处理计划已失效或待核实，当前进展未取得':'当前没有在途版本'}}</span>
 <small v-if="unit.publish_phase&&unit.publish_phase!=='NOT_SENT'">交付状态：{{stateLabel(unit.publish_phase)}}</small><small v-if="unit.cooldown_until">冷却记录截止 {{dateText(unit.cooldown_until)}}</small>
</div></template>
