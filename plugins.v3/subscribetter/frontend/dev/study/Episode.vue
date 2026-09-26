<script setup>
import {ref} from 'vue';
import {qualityLabel,phaseLabels,tone,glyph} from './model.mjs';
import {qualityExtra,qualityOrigin} from '../../src/media.mjs';
const props=defineProps({episode:Object});
const opened=ref(false),checking=ref(false),checked=ref(false),result=ref('');
async function reconcile(){if(checking.value)return;checking.value=true;opened.value=true;await new Promise(r=>setTimeout(r,500));result.value='模拟核对结果：云端状态仍未确认。当前库内版本保留。';checked.value=true;checking.value=false;}
</script>
<template><article class="episode-row" :data-tone="tone(episode.phase)">
 <div class="episode-index"><span class="quiet">EPISODE</span><strong>{{String(episode.number).padStart(2,'0')}}</strong></div>
 <div class="episode-versions"><div class="current-version"><small>当前在库</small><span>{{qualityLabel(episode.current)}}</span></div><span class="version-arrow" aria-hidden="true">→</span><div class="target-version"><small>本轮目标</small><strong><mark v-if="episode.improvements.includes('resolution')">{{episode.target?.resolution===2160?'4K':episode.target?.resolution+'p'}}</mark><template v-else>{{episode.target?.resolution}}p</template><span> · {{qualityLabel(episode.target).split(' · ').slice(1).join(' · ')}}</span></strong></div></div>
 <div class="episode-mobile-quality">{{episode.current?.resolution?episode.current.resolution+'p':'当前规格未知'}} <span aria-hidden="true">→</span> <strong>{{qualityLabel(episode.target).split(' · ').slice(0,2).join(' · ')}}</strong></div>
 <div class="episode-progress"><strong class="status-label" :data-tone="tone(episode.phase)"><i class="state-symbol" aria-hidden="true">{{glyph(episode.phase)}}</i>{{phaseLabels[episode.phase]}}</strong><div v-if="episode.percent!==null" class="download-meter"><progress :value="episode.percent" max="100" :aria-label="'第 '+episode.number+' 集下载进度'"/><b>{{episode.percent}}%</b></div><small>{{episode.progress}}<template v-if="episode.next"> · {{episode.next}}</template></small></div>
 <div class="episode-action"><VBtn v-for="action in episode.actions" :key="action.kind" variant="tonal" :loading="checking" @click="reconcile">{{action.label}}</VBtn><button class="detail-toggle" :aria-label="(opened?'收起':'查看')+'第 '+episode.number+' 集详情'" :aria-expanded="opened" :aria-controls="'episode-'+episode.number+'-details'" @click="opened=!opened"><span>{{opened?'收起':'详情'}}</span><span aria-hidden="true">{{opened?'⌃':'⌄'}}</span></button></div>
 <section v-if="opened" :id="'episode-'+episode.number+'-details'" class="episode-expanded" :aria-label="'第 '+episode.number+' 集版本详情'">
  <div v-if="checking||checked" class="context-result" role="status"><strong>{{checking?'正在核对第 '+episode.number+' 集…':'本次核对'}}</strong><p>{{result||'读取这次上传的状态，不重新发送文件。'}}</p></div>
  <div class="expanded-versions"><div><small>当前可播版本</small><strong>{{qualityLabel(episode.current)}}</strong><small>{{qualityExtra(episode.current)}} · {{qualityOrigin(episode.current)}}</small></div><div><small>升级目标</small><strong>{{qualityLabel(episode.target)}}</strong><small>{{qualityExtra(episode.target)}} · {{qualityOrigin(episode.target)}}</small></div></div>
  <p class="comparison-note"><span>本次选择依据</span>{{episode.reason}}</p><details><summary>文件与处理依据</summary><p v-for="file in episode.files" :key="file" class="path-text">{{file}}</p><p class="quiet">样板中的规格和判断来自合成夹具。目标规格是发布信息，尚未入库。</p></details>
 </section>
</article></template>
