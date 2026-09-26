<script setup>
import {computed} from 'vue';
import {qualityParts,versionChange,dimensions} from './media.mjs';
const props=defineProps({current:Object,target:Object,change:Object,currentState:String,pending:Boolean});
const summary=computed(()=>versionChange(props));
const relation=dimension=>props.change?.changes?.find(v=>v.dimension===dimension);
const parts=q=>qualityParts(q).filter(p=>['resolution','picture','audio'].includes(p.dimension)||summary.value.items.some(v=>v.dimension===p.dimension));
const improves=d=>relation(d)?.order===1||relation(d)?.evidence===true;
</script>
<template><div class="sb-difference">
 <div class="sb-difference-desktop">
  <div class="sb-difference-current"><small>当前在库</small><span v-if="current"><template v-for="(part,i) in parts(current)" :key="part.dimension"><span v-if="i"> · </span><span>{{part.dimension==='special'?'特效字幕：':''}}{{part.text}}</span></template></span><span v-else>{{summary.before}}</span></div>
  <template v-if="target"><span class="sb-difference-arrow" aria-hidden="true">→</span><div class="sb-difference-target"><small>{{summary.targetLabel}}</small><strong><template v-for="(part,i) in parts(target)" :key="part.dimension"><span v-if="i"> · </span><mark v-if="improves(part.dimension)" :title="(dimensions[part.dimension]||part.dimension)+(relation(part.dimension)?.evidence?'：声明依据更新':'：按本次策略改善')">{{part.dimension==='special'?'特效字幕：':''}}{{part.text}}</mark><span v-else :class="{'sb-difference-reduced':relation(part.dimension)?.order===-1}" :title="relation(part.dimension)?.order===-1?'此维度的策略偏好降低':undefined">{{part.text}}</span></template></strong><small v-if="change?.kind==='evidence'">同质量 · 更新特效字幕的声明依据</small></div></template>
  <span v-else class="sb-difference-rest">{{pending?'目标规格尚未确认':'当前没有在途任务'}}</span>
 </div>
 <div class="sb-difference-mobile"><template v-if="summary.items.length"><div v-for="item in summary.items" :key="item.dimension"><small v-if="item.dimension==='special'">特效字幕：</small><span>{{item.before}}</span><span aria-hidden="true"> → </span><strong :class="{'sb-difference-reduced':item.order===-1}">{{item.after}}</strong><small v-if="item.order===-1">（偏好降低）</small></div></template><span v-else>{{summary.before}}<template v-if="summary.after"> → <strong>{{summary.after}}</strong></template></span></div>
</div></template>
<style scoped>
.sb-difference{min-width:0;font-size:14px;line-height:1.65;color:rgb(var(--v-theme-on-surface))}.sb-difference-desktop{display:grid;grid-template-columns:minmax(0,1fr) 16px minmax(0,1.2fr);gap:12px;align-items:center}.sb-difference-desktop small{display:block;font-size:12px;color:rgba(var(--v-theme-on-surface),.74);margin-bottom:4px}.sb-difference-current,.sb-difference-target{min-width:0;overflow-wrap:anywhere}.sb-difference-target strong{font-size:15px;font-weight:600}.sb-difference mark{color:rgb(var(--v-theme-primary));background:rgba(var(--v-theme-primary),.1);padding:1px 3px;border-radius:4px;box-decoration-break:clone}.sb-difference .sb-difference-reduced{color:rgb(var(--v-theme-warning));text-decoration:underline dotted;text-underline-offset:4px}.sb-difference-arrow{color:rgba(var(--v-theme-on-surface),.6)}.sb-difference-rest{grid-column:2/4;color:rgba(var(--v-theme-on-surface),.74);font-size:13px}.sb-difference-mobile{display:none}
@media(max-width:650px){.sb-difference-desktop{display:none}.sb-difference-mobile{display:grid;gap:3px;font-size:13px;line-height:1.8}.sb-difference-mobile strong{font-size:14px;font-weight:600}.sb-difference-mobile small{font-size:12px}}
</style>
