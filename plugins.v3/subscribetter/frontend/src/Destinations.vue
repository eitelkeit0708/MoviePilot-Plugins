<script setup>
import {useId} from 'vue';import {id} from './client.mjs';
const props=defineProps({modelValue:Array,categories:Array,downloaders:Array,paths:Array,rules:Array,readonly:Boolean,embedded:Boolean,part:String});const emit=defineEmits(['update:modelValue']);
const pathList=useId();
function change(index,key,value){emit('update:modelValue',props.modelValue.map((t,i)=>i===index?{...t,[key]:value}:t))}
function add(){let n=1;while(props.modelValue.some(t=>(t.display_name||t.id)==='方案 '+n))n++;emit('update:modelValue',[...props.modelValue,{id:'scheme-'+id(),display_name:'方案 '+n,category_id:'',downloader:'',save_path:'',organized_rule:null,sites:[],custom_words:[]}])}
</script>
<template><section aria-label="下载与入库方案"><header v-if="!embedded" class="sb-heading"><div><h3>下载与入库方案</h3><p class="sb-muted">将分类、下载器和目录保存为方案，接管已有订阅时直接选择。</p></div><VBtn variant="tonal" :disabled="readonly" @click="add">添加方案</VBtn></header>
 <p v-if="!modelValue.length" class="sb-empty">尚无方案。先添加一个方案，再接管已有订阅。</p>
 <section v-for="(t,index) in modelValue" :key="index" class="sb-plan-row" :class="{'sb-embedded-plan':embedded}"><h4 v-if="!embedded">{{t.display_name||t.id||'未命名方案'}} <span class="sb-muted"> · {{t.downloader||'请选择下载器'}}</span><span class="sb-plan-path">{{t.save_path||'尚未选择目录'}}</span></h4>
  <div class="sb-grid"><label v-if="part!=='download'">方案名称<input :value="t.display_name||t.id" :disabled="readonly" maxlength="256" @input="change(index,'display_name',$event.target.value)"></label>
   <label v-if="part!=='download'">MoviePilot 分类<select :value="t.category_id" :disabled="readonly" @change="change(index,'category_id',$event.target.value)"><option value="" disabled>请选择分类</option><option v-if="t.category_id&&!categories?.some(c=>c.id===t.category_id)" :value="t.category_id">当前分类（宿主暂不可用） · {{t.category_id}}</option><option v-for="c in categories||[]" :key="c.id" :value="c.id" :disabled="!c.enabled">{{c.name}} · {{c.media_type}}</option></select></label>
   <label v-if="part!=='purpose'">下载器<select :value="t.downloader" :disabled="readonly" @change="change(index,'downloader',$event.target.value)"><option value="" disabled>请选择宿主下载器</option><option v-if="t.downloader&&!downloaders?.some(d=>d.name===t.downloader)" :value="t.downloader">{{t.downloader}}（暂不可用）</option><option v-for="d in downloaders||[]" :key="d.name" :value="d.name">{{d.name}} · {{d.type}}</option></select></label>
   <label v-if="part!=='purpose'" class="sb-wide">下载保存到<input :list="pathList+'-'+index" :value="t.save_path" :disabled="readonly" placeholder="选择已有目录或输入容器内绝对路径" @input="change(index,'save_path',$event.target.value)"><datalist :id="pathList+'-'+index"><option v-for="(p,i) in paths||[]" :key="i" :value="p.download_path">{{p.name||p.download_path}}</option></datalist></label>
   <label v-if="!embedded">交付规则<select :value="t.organized_rule||''" :disabled="readonly" @change="change(index,'organized_rule',$event.target.value||null)"><option value="">不绑定交付规则</option><option v-if="t.organized_rule&&!rules?.some(r=>r.id===t.organized_rule)" :value="t.organized_rule">{{t.organized_rule}}（请检查规则）</option><option v-for="r in rules||[]" :key="r.id" :value="r.id">{{r.id}}{{r.enabled?'':' · 未启用'}}</option></select></label>
  </div><p v-if="!embedded" class="sb-muted">已有站点限制和自定义识别词保留。</p>
  <VBtn v-if="!embedded" color="error" variant="text" :disabled="readonly" @click="emit('update:modelValue',modelValue.filter((_,i)=>i!==index))">移除此方案</VBtn>
 </section>
</section></template>
