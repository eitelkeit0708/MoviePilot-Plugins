<script setup>
import {computed,nextTick,ref} from 'vue';
const props=defineProps({items:{type:Array,default:()=>[]},label:String,selected:String,selectable:Boolean,readonly:Boolean,editableTiers:Boolean});
const emit=defineEmits(['select','reorder','tiers']);
const list=ref(null),drag=ref(null),announcement=ref(''),selecting=ref(false),chosen=ref([]);
const tiers=computed(()=>{const result=[];for(const item of props.items){const last=result.at(-1);if(item.priority!==undefined&&last?.priority===item.priority)last.items.push(item);else result.push({priority:item.priority,items:[item]})}return result});
const tierIds=()=>tiers.value.map(tier=>tier.items.map(item=>item.id));
function publishTiers(value,message){if(props.readonly)return;emit('tiers',value);announcement.value=message;selecting.value=false;chosen.value=[]}
function merge(){
 if(chosen.value.length<2)return;
 const groups=tierIds(),ids=props.items.filter(item=>chosen.value.includes(item.id)).map(item=>item.id),first=groups.findIndex(tier=>tier.some(id=>ids.includes(id)));
 publishTiers(groups.flatMap((tier,index)=>[...(index===first?[ids]:[]),tier.filter(id=>!ids.includes(id))]).filter(tier=>tier.length),'已设为同等优先');
}
function split(index){publishTiers(tierIds().flatMap((tier,i)=>i===index?tier.map(id=>[id]):[tier]),'已拆为独立优先级')}
function reorder(id,to){
 if(props.readonly)return;
 const ids=props.items.map(item=>item.id),from=ids.indexOf(id);
 if(from<0||to<0||to>=ids.length||from===to)return;
 let position=to;
 if(props.editableTiers){
  const groups=tierIds(),target=groups.findIndex(tier=>tier.includes(ids[to]));
  const result=groups.flatMap((tier,index)=>{const remaining=tier.filter(value=>value!==id);return index===target?(from<to?[remaining,[id]]:[[id],remaining]):[remaining]}).filter(tier=>tier.length);
  position=result.flat().indexOf(id);emit('tiers',result);
 }else{ids.splice(from,1);ids.splice(to,0,id);emit('reorder',ids)}
 announcement.value=props.items.find(item=>item.id===id).title+'已移到第 '+(position+1)+' 位';
 nextTick(()=>Array.from(list.value?.querySelectorAll?.('[data-order-handle]')||[]).find(el=>el.dataset.orderHandle===id)?.focus());
}
function start(event,id){
 if(props.readonly||event.button!==0)return;
 event.preventDefault();event.currentTarget.setPointerCapture?.(event.pointerId);
 drag.value={id,from:props.items.findIndex(item=>item.id===id),to:props.items.findIndex(item=>item.id===id),y:event.clientY,moved:false};
}
function point(event){
 if(!drag.value)return;
 if(Math.abs(event.clientY-drag.value.y)>5)drag.value.moved=true;
 if(!drag.value.moved)return;
 const rows=Array.from(list.value.querySelectorAll('[data-order-id]'));
 const boxes=rows.map(row=>row.getBoundingClientRect());
 const found=boxes.findIndex(box=>event.clientY<box.bottom);
 drag.value.to=found<0?boxes.length-1:found;
 const bounds=list.value.getBoundingClientRect();
 if(event.clientY<bounds.top+30)list.value.scrollTop-=12;
 else if(event.clientY>bounds.bottom-30)list.value.scrollTop+=12;
}
function finish(){const current=drag.value;drag.value=null;if(current?.moved)reorder(current.id,current.to)}
function keyboard(event,id){
 const from=props.items.findIndex(item=>item.id===id),to={ArrowUp:from-1,ArrowDown:from+1,Home:0,End:props.items.length-1}[event.key];
 if(event.key==='Escape'){drag.value=null;return}
 if(to===undefined)return;event.preventDefault();reorder(id,to);
}
</script>

<template>
 <div class="sb-priority-list-wrap">
  <div v-if="editableTiers" class="sb-equal-tools">
   <button type="button" :disabled="readonly||items.length<2" :aria-expanded="selecting" @click="selecting=!selecting;chosen=[]">{{selecting?'取消选择':'设置同等优先'}}</button>
   <template v-if="selecting"><p>勾选至少两项，合并后放在所选项的最前位置。</p><button type="button" :disabled="readonly||chosen.length<2" @click="merge">合并为同等优先</button></template>
  </div>
  <div ref="list" class="sb-order-list" role="list" :aria-label="label">
   <div v-for="(tier,rank) in tiers" :key="tier.items[0].id" class="sb-order-tier" :data-equal="tier.items.length>1">
    <div v-if="tier.items.length>1" class="sb-equal-caption"><span>同等优先 · 继续比较下一项</span><button v-if="editableTiers" type="button" :disabled="readonly" :aria-label="'拆开'+tier.items.map(item=>item.title).join('、')+'的同等优先'" @click="split(rank)">拆开</button></div>
    <div v-for="item in tier.items" :key="item.id" role="listitem" class="sb-order-row" :data-order-id="item.id" :data-selected="selectable&&selected===item.id" :data-dragging="drag?.moved&&drag.id===item.id" :data-drop="drag?.moved&&drag.to!==drag.from&&props.items[drag.to]?.id===item.id?(drag.to<drag.from?'before':'after'):undefined">
     <input v-if="selecting" type="checkbox" class="sb-equal-choice" :aria-label="'选择'+item.title+'设为同等优先'" :checked="chosen.includes(item.id)" :disabled="readonly" @change="chosen=$event.target.checked?[...chosen,item.id]:chosen.filter(id=>id!==item.id)">
     <button v-else type="button" class="sb-drag-handle" :data-order-handle="item.id" :aria-label="'调整'+item.title+'优先级'" title="拖动排序，也可用上下方向键调整" :disabled="readonly||items.length<2" @pointerdown="start($event,item.id)" @pointermove="point" @pointerup="finish" @pointercancel="drag=null" @lostpointercapture="drag=null" @keydown="keyboard($event,item.id)"><svg viewBox="0 0 16 20" width="16" height="20" aria-hidden="true"><circle v-for="(p,i) in [[5,5],[11,5],[5,10],[11,10],[5,15],[11,15]]" :key="i" :cx="p[0]" :cy="p[1]" r="1.4" fill="currentColor"/></svg></button>
     <span class="sb-order-number" aria-hidden="true">{{rank+1}}</span>
     <button v-if="selectable" type="button" class="sb-order-select" :aria-label="'编辑'+item.title+'优先级'" :aria-pressed="selected===item.id" @click="emit('select',item.id)">{{item.title}}<span aria-hidden="true">›</span></button>
     <span v-else class="sb-order-title">{{item.title}}</span>
    </div>
   </div>
  </div>
  <p class="sb-sr-only" role="status" aria-live="polite">{{announcement}}</p>
 </div>
</template>
