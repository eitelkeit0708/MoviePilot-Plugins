<script setup>
import {computed} from 'vue';
import VersionDifference from './VersionDifference.vue';
import {qualitySummary,qualityExtra,qualityOrigin,stateLabel,fileStateLabel,reasonText,dateText,formatBytes,processingNext,unitLabel,downloadPercent} from './media.mjs';
const props=defineProps({unit:Object,health:Object});
const p=computed(()=>props.unit.processing),percent=computed(()=>downloadPercent(p.value?.download));
const waiting=computed(()=>p.value?.transfer_files?.filter(f=>!f.remote_verified)||[]);
const versions=computed(()=>props.unit.current_quality||[]);
const changeFor=version=>{
 const change=p.value?.change,comparison=change?.baseline_current&&change.versions?.find(v=>v.version_id===version?.version_id);
 return change?{...change,changes:comparison?.changes||[]}:null;
};
</script>
<template><article class="sb-episode" :data-attention="p?.next_step==='RECONCILE'">
 <strong class="sb-episode-number">{{unitLabel(unit)}}</strong>
 <div class="sb-unit-versions"><template v-if="versions.length"><section v-for="(version,index) in versions" :key="version.version_id||index"><small v-if="versions.length>1">在库版本 {{index+1}}</small><VersionDifference :current="version.quality" :target="p?.quality" :change="changeFor(version)" :current-state="unit.current_facts?.state" :pending="!!p"/></section></template><VersionDifference v-else :target="p?.quality" :change="changeFor(null)" :current-state="unit.current_facts?.state" :pending="!!p"/><small v-if="p&&!p.quality">本轮规格尚未确认</small></div>
 <div class="sb-episode-stage"><template v-if="p"><strong class="sb-stage-label" :data-state="p.phase">{{stateLabel(p.phase)}}</strong>
  <template v-if="p.phase==='DOWNLOADING'"><progress v-if="percent!==null" :value="p.download.downloaded_bytes" :max="p.download.total_bytes" :aria-label="unitLabel(unit)+'下载进度'"/><span class="sb-progress-numbers">{{percent!==null?percent+'% · ':''}}{{p.download?.downloaded_bytes==null?'尚未取得下载进度':formatBytes(p.download.downloaded_bytes)+' / '+formatBytes(p.download.total_bytes)}}</span></template>
  <span v-else-if="p.phase==='RAPID_WAIT'&&waiting.length===1">未命中 {{waiting[0].misses??'未知'}} / {{waiting[0].miss_limit??'上限未记录'}} 次</span>
  <span v-else-if="p.phase==='RAPID_WAIT'">{{waiting.length}} 个文件等待秒传</span>
  <span v-else-if="p.phase==='WAITING_ASSETS'&&p.attachments?.required">字幕及附件 {{p.attachments.local_ready}} / {{p.attachments.required}} 已齐备</span>
  <small :title="p.next_at?dateText(p.next_at):undefined">{{processingNext(p,health)}}</small>
 </template><template v-else><strong>{{unit.owner_plan_id?'处理记录待核对':versions.length?'已收录':'等待新资源'}}</strong><small v-if="unit.owner_plan_id">原计划已失效或待核实</small><small v-else-if="unit.cooldown_until">{{dateText(unit.cooldown_until)}} 后检查</small></template></div>
 <details class="sb-episode-details"><summary :aria-label="unitLabel(unit)+'版本与处理详情'">详情</summary><div class="sb-episode-expanded">
  <section><h4>库内记录</h4><div v-for="(version,index) in versions" :key="version.version_id||index"><strong>{{versions.length>1?'版本 '+(index+1)+' · ':''}}{{qualitySummary(version.quality)}}</strong><p>{{qualityExtra(version.quality)}}</p><small>{{qualityOrigin(version.quality)}}</small></div><p v-if="!versions.length">缺少可靠的库内规格记录。</p><small v-if="unit.last_ingest_confirmed_at">最近确认 {{dateText(unit.last_ingest_confirmed_at)}}</small><slot name="archive"/></section>
  <section v-if="p"><h4>本轮处理</h4><p>{{reasonText(p.reason)||'此计划没有记录具体比较原因'}}</p><p v-if="!p.change">此历史计划未记录各维度变化，保留规格但不推断改善标记。</p><p v-else-if="!p.change.baseline_current">库内记录已变化，本轮原比较不能直接套在当前版本上。</p><p v-else-if="p.change.truncated">仅展示前 20 个库内版本的比较，完整记录见版本档案。</p><p v-if="p.change?.kind==='evidence'">这是同质量下的字幕声明依据更新，最终文件关联尚待确认。</p><p v-if="!p.quality">此计划没有单一、完整的规格摘要；请查看关联文件与版本档案。</p><p v-if="p.next_at">计划检查时间：{{dateText(p.next_at)}}</p><small v-if="p.download?.sampled_at">下载采样 {{dateText(p.download.sampled_at)}}{{p.download.shared_file?' · 包含跨集共用文件':''}}</small><p v-for="file in p.files" :key="file" class="sb-filename">{{file}}</p><ul class="sb-transfer-facts"><li v-for="(file,index) in p.transfer_files" :key="index"><strong>{{file.name||((file.role==='video'?'视频':'附件')+' '+(index+1))}}</strong><span>{{file.remote_verified?'云端暂存已确认':fileStateLabel(file.state)}}</span><span v-if="!file.remote_verified&&file.misses!=null">累计有效未命中 {{file.misses}} / {{file.miss_limit??'上限未记录'}} 次</span><span v-if="['CD2_UPLOADING','CD2_PAUSED'].includes(file.state)">向 CD2 发送 {{formatBytes(file.local_bytes_sent)}}；云端结果仍需确认</span></li></ul><small v-if="p.transfer_file_count>20">仅展示前 20 个文件，全部记录见传输页。</small><small v-if="p.attachments?.required">附件 {{p.attachments.required}} 份 · 本地齐备 {{p.attachments.local_ready}} · 云端确认 {{p.attachments.remote_verified}}</small><small>开始于 {{dateText(p.started_at)}}</small></section>
 </div><slot/></details>
</article></template>
