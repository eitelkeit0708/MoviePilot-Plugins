<script setup>
import {computed} from 'vue';
import {highlights,phaseLabels,tone,glyph,collectionText} from './model.mjs';
const props=defineProps({work:Object});defineEmits(['open']);
const activity=computed(()=>highlights(props.work));
const resting=computed(()=>({PAUSED:'已暂停自动升级',STOPPED:'已停止追踪'}[props.work.state]||'等待识别集数'));
</script>
<template><button class="media-row" :data-media-id="work.id" @click="$emit('open',work.id)" :aria-label="'查看 '+work.title">
 <span class="cover" aria-hidden="true"><img v-if="work.poster" :src="work.poster" alt="" loading="lazy" referrerpolicy="no-referrer"><svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4m10 0h4M3 15h4m10 0h4"/></svg></span>
 <span class="media-identity"><strong>{{work.title}}</strong><span class="quiet">{{[work.year,work.type,work.season?'第 '+work.season+' 季':null].filter(Boolean).join(' · ')}}</span></span>
 <span class="media-collection"><span>{{collectionText(work)}}</span><small v-if="work.quality">{{work.quality}}</small></span>
 <span v-if="activity.length" class="media-activity"><span v-for="u in activity" :key="u.number" :data-tone="tone(u.phase)"><i class="state-symbol" aria-hidden="true">{{glyph(u.phase)}}</i><span>第 {{u.number}} 集</span><b>{{phaseLabels[u.phase]}}</b></span><small v-if="work.stage_sample_count!=null&&work.stage_sample_count<work.processing">已读取 {{work.stage_sample_count}} / {{work.processing}} 集的处理状态</small></span>
 <span v-else class="media-rest"><i class="state-symbol" aria-hidden="true">{{work.state==='PAUSED'?'Ⅱ':'·'}}</i>{{resting}}</span>
 <span class="row-arrow" aria-hidden="true">›</span>
</button></template>
