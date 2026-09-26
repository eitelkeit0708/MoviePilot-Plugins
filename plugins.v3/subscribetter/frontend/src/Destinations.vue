<script setup>
import {ref,useId} from 'vue';
const props=defineProps({modelValue:Array,categories:Array,downloaders:Array,paths:Array,rules:Array,readonly:Boolean,embedded:Boolean});const emit=defineEmits(['update:modelValue']);
const pathList=useId();const expanded=ref(null),savedIds=new Set(props.modelValue.map(t=>t.id));
function change(index,key,value){emit('update:modelValue',props.modelValue.map((t,i)=>i===index?{...t,[key]:value}:t))}
function add(){let n=1;while(props.modelValue.some(t=>t.id==='方案 '+n))n++;emit('update:modelValue',[...props.modelValue,{id:'方案 '+n,category_id:'',downloader:'',save_path:'',organized_rule:null,sites:[],custom_words:[]}]);expanded.value=props.modelValue.length}
</script>
<template><section aria-label="下载与入库方案"><header v-if="!embedded" class="sb-heading"><div><h3>下载与入库方案</h3><p class="sb-muted">将分类、下载器和目录保存为方案，接管已有订阅时直接选择。</p></div><VBtn variant="tonal" :disabled="readonly" @click="add">添加方案</VBtn></header>
 <p v-if="!modelValue.length" class="sb-empty">尚无方案。先添加一个方案，再接管已有订阅。</p>
 <component :is="embedded?'div':'details'" v-for="(t,index) in modelValue" :key="index" class="sb-plan-row" :class="{'sb-embedded-plan':embedded}" :open="embedded||expanded===index"><summary v-if="!embedded">{{t.id||'未命名方案'}} <span class="sb-muted"> · {{t.downloader||'请选择下载器'}}</span><span class="sb-plan-path">{{t.save_path||'尚未选择目录'}}</span></summary>
  <div class="sb-grid"><label v-if="!embedded">方案名称<input :value="t.id" :disabled="readonly||savedIds.has(t.id)" maxlength="256" @input="change(index,'id',$event.target.value)"><small v-if="savedIds.has(t.id)">已保存名称作为稳定引用；此处不改名。</small></label>
   <label>MoviePilot 分类<select :value="t.category_id" :disabled="readonly" @change="change(index,'category_id',$event.target.value)"><option value="" disabled>请选择分类</option><option v-if="t.category_id&&!categories?.some(c=>c.id===t.category_id)" :value="t.category_id">当前分类（宿主暂不可用） · {{t.category_id}}</option><option v-for="c in categories||[]" :key="c.id" :value="c.id" :disabled="!c.enabled">{{c.name}} · {{c.media_type}}</option></select></label>
   <label>下载器<select :value="t.downloader" :disabled="readonly" @change="change(index,'downloader',$event.target.value)"><option value="" disabled>请选择宿主下载器</option><option v-if="t.downloader&&!downloaders?.some(d=>d.name===t.downloader)" :value="t.downloader">{{t.downloader}}（暂不可用）</option><option v-for="d in downloaders||[]" :key="d.name" :value="d.name">{{d.name}} · {{d.type}}</option></select></label>
   <label class="sb-wide">下载目录<input :list="pathList+'-'+index" :value="t.save_path" :disabled="readonly" placeholder="选择已有目录或输入容器内绝对路径" @input="change(index,'save_path',$event.target.value)"><datalist :id="pathList+'-'+index"><option v-for="(p,i) in paths||[]" :key="i" :value="p.download_path">{{p.name||p.download_path}}</option></datalist></label>
   <label v-if="!embedded">交付规则<select :value="t.organized_rule||''" :disabled="readonly" @change="change(index,'organized_rule',$event.target.value||null)"><option value="">不绑定交付规则</option><option v-if="t.organized_rule&&!rules?.some(r=>r.id===t.organized_rule)" :value="t.organized_rule">{{t.organized_rule}}（请检查规则）</option><option v-for="r in rules||[]" :key="r.id" :value="r.id">{{r.id}}{{r.enabled?'':' · 未启用'}}</option></select></label>
  </div><p v-if="!embedded" class="sb-muted">已有站点限制和自定义识别词保留。</p>
  <VBtn v-if="!embedded" color="error" variant="text" :disabled="readonly" @click="emit('update:modelValue',modelValue.filter((_,i)=>i!==index))">移除此方案</VBtn>
 </component>
</section></template>
