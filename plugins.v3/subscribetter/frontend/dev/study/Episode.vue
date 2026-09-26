<script setup>
import {computed,ref} from 'vue';
import {qualityLabel,qualityParts,versionChange,phaseLabels,tone,glyph} from './model.mjs';
import {qualityExtra,qualityOrigin} from '../../src/media.mjs';
const props=defineProps({episode:Object});
const opened=ref(false),checking=ref(false),checkedAt=ref(null);
const change=computed(()=>versionChange(props.episode));
const parts=q=>qualityParts(q).filter(p=>p.dimension!=='special'||props.episode.change?.dimensions.includes('special'));
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
  <div class="episode-versions" :class="{'version-collected':!episode.target}">
   <div class="current-version"><small>当前在库</small><span v-if="episode.current"><template v-for="(part,i) in parts(episode.current)" :key="part.dimension"><span v-if="i"> · </span><b :class="{'decisive-current':episode.change?.dimensions.includes(part.dimension)}">{{part.text}}</b></template></span><span v-else>{{change.before}}</span></div>
   <template v-if="episode.target"><span class="version-arrow" aria-hidden="true">→</span><div class="target-version"><small>{{change.targetLabel}}</small><strong><template v-for="(part,i) in parts(episode.target)" :key="part.dimension"><span v-if="i"> · </span><mark v-if="episode.change?.dimensions.includes(part.dimension)">{{part.text}}</mark><span v-else>{{part.text}}</span></template></strong><small v-if="episode.change?.kind==='evidence'" class="evidence-change">同质量 · 更新字幕声明依据</small></div></template>
   <div v-else class="collected-note">{{episode.change?.reason}}</div>
  </div>
  <div class="episode-mobile-quality"><small v-if="episode.change?.kind==='evidence'">同质量 · 字幕依据更新</small><span>{{change.before}}</span><template v-if="change.after"> <span aria-hidden="true">→</span> <strong>{{change.after}}</strong></template></div>
  <div class="episode-progress"><strong class="status-label" :data-tone="tone(episode.phase)"><i class="state-symbol" aria-hidden="true">{{glyph(episode.phase)}}</i>{{phaseLabels[episode.phase]}}</strong><div v-if="episode.percent!==null" class="download-meter"><progress :value="episode.percent" max="100" :aria-label="'第 '+episode.number+' 集下载进度'"/><b>{{episode.percent}}%</b></div><small>{{episode.progress}}<template v-if="episode.next"> · {{episode.next}}</template></small></div>
  <div class="episode-action"><VBtn v-for="action in episode.actions" :key="action.kind" variant="tonal" :loading="checking" @click="reconcile">{{action.label}}</VBtn><button class="detail-toggle" :aria-label="(opened?'收起':'查看')+'第 '+episode.number+' 集详情'" :aria-expanded="opened" :aria-controls="'episode-'+episode.number+'-details'" @click="opened=!opened"><span>{{opened?'收起':'详情'}}</span><span aria-hidden="true">{{opened?'⌃':'⌄'}}</span></button></div>
  <section v-if="opened" :id="'episode-'+episode.number+'-details'" class="episode-expanded" :aria-label="'第 '+episode.number+' 集版本详情'">
   <div v-if="checking||checkedAt" class="context-result" role="status"><strong>{{checking?'正在核对第 '+episode.number+' 集…':'云端状态仍未确认'}}</strong><p v-if="checkedAt">本次模拟核对：<time :datetime="checkedAt.toISOString()">{{checkedAt.toLocaleTimeString('zh-CN',{hour12:false})}}</time> · 当前库内版本保留。</p><p v-else>读取这次上传的状态，不重新发送文件。</p><template v-if="checkedAt"><p>{{episode.followup?.nextAt?'下次检查：'+episode.followup.nextAt:'尚无自动检查安排，可稍后再次核对。'}}</p><ul class="blocked-actions"><li v-for="action in episode.followup?.blocked||[]" :key="action.label"><button disabled>{{action.label}}</button><span>{{action.reason}}</span></li></ul></template></div>
   <div class="expanded-versions"><div><small>当前版本</small><strong>{{episode.current?qualityLabel(episode.current):change.before}}</strong><small v-if="episode.current">{{qualityExtra(episode.current)}} · {{qualityOrigin(episode.current)}}</small></div><div v-if="episode.target"><small>{{change.targetLabel}}</small><strong>{{qualityLabel(episode.target)}}</strong><small>{{qualityExtra(episode.target)}} · {{qualityOrigin(episode.target)}}</small></div></div>
   <p class="comparison-note"><span>{{episode.target?'本次选择依据':'当前状态'}}</span>{{episode.change?.reason||'比较依据未取得'}}</p><details v-if="episode.target"><summary>文件与处理依据</summary><p v-for="file in episode.files" :key="file" class="path-text">{{file}}</p><p class="quiet">样板中的规格和判断为明确给定的合成事实。目标规格是发布信息，尚未入库。</p></details>
  </section>
 </article>
</template>
