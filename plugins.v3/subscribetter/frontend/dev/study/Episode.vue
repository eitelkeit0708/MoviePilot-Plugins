<script setup>
import {computed,ref} from 'vue';
import {qualityLabel,versionChange,phaseLabels,tone,glyph} from './model.mjs';
import {qualityExtra,qualityOrigin} from '../../src/media.mjs';
import VersionDifference from '../../src/VersionDifference.vue';
const props=defineProps({episode:Object});
const opened=ref(false),checking=ref(false),checkedAt=ref(null);
const change=computed(()=>versionChange(props.episode));
async function reconcile(){
 if(checking.value)return;
 checking.value=true;opened.value=true;checkedAt.value=null;
 await new Promise(r=>setTimeout(r,500));
 checkedAt.value=new Date();checking.value=false;
}
</script>
<template>
 <article class="episode-row" :data-tone="tone(episode.phase)">
  <div class="episode-index"><strong><span>第 </span>{{episode.number}}<span> 集</span></strong></div>
  <VersionDifference class="episode-difference" :current="episode.current" :target="episode.target" :change="episode.change" :current-state="episode.currentState"/>
  <div class="episode-progress"><strong class="status-label" :data-tone="tone(episode.phase)"><i class="state-symbol" aria-hidden="true">{{glyph(episode.phase)}}</i>{{phaseLabels[episode.phase]}}</strong><div v-if="episode.percent!==null" class="download-meter"><progress :value="episode.percent" max="100" :aria-label="'第 '+episode.number+' 集下载进度'"/><b>{{episode.percent}}%</b></div><small>{{episode.progress}}<template v-if="episode.next"> · {{episode.next}}</template></small></div>
  <div class="episode-action"><VBtn v-for="action in episode.actions" :key="action.kind" variant="tonal" :loading="checking" @click="reconcile">{{action.label}}</VBtn><button class="detail-toggle" :aria-label="(opened?'收起':'查看')+'第 '+episode.number+' 集详情'" :aria-expanded="opened" :aria-controls="'episode-'+episode.number+'-details'" @click="opened=!opened"><span>{{opened?'收起':'详情'}}</span><span aria-hidden="true">{{opened?'⌃':'⌄'}}</span></button></div>
  <section v-if="opened" :id="'episode-'+episode.number+'-details'" class="episode-expanded" :aria-label="'第 '+episode.number+' 集版本详情'">
   <div v-if="checking||checkedAt" class="context-result" role="status"><strong>{{checking?'正在核对第 '+episode.number+' 集…':'云端状态仍未确认'}}</strong><p v-if="checkedAt">本次模拟核对：<time :datetime="checkedAt.toISOString()">{{checkedAt.toLocaleTimeString('zh-CN',{hour12:false})}}</time> · 当前库内版本保留。</p><p v-else>读取这次上传的状态，不重新发送文件。</p><template v-if="checkedAt"><p>{{episode.followup?.nextAt?'下次检查：'+episode.followup.nextAt:'尚无自动检查安排，可稍后再次核对。'}}</p><ul class="blocked-actions"><li v-for="action in episode.followup?.blocked||[]" :key="action.label"><button disabled>{{action.label}}</button><span>{{action.reason}}</span></li></ul></template></div>
   <div class="expanded-versions"><div><small>当前版本</small><strong>{{episode.current?qualityLabel(episode.current):change.before}}</strong><small v-if="episode.current">{{qualityExtra(episode.current)}} · {{qualityOrigin(episode.current)}}</small></div><div v-if="episode.target"><small>{{change.targetLabel}}</small><strong>{{qualityLabel(episode.target)}}</strong><small>{{qualityExtra(episode.target)}} · {{qualityOrigin(episode.target)}}</small></div></div>
   <p class="comparison-note"><span>{{episode.target?'本次选择依据':'当前状态'}}</span>{{episode.change?.reason||'比较依据未取得'}}</p><details v-if="episode.target"><summary>文件与处理依据</summary><p v-for="file in episode.files" :key="file" class="path-text">{{file}}</p><p class="quiet">样板中的规格和判断为明确给定的合成事实。目标规格是发布信息，尚未入库。</p></details>
  </section>
 </article>
</template>
