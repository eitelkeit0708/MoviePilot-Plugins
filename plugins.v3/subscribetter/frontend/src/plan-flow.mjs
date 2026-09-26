import {canonical} from './legacy.mjs';
const key=value=>value===undefined?'undefined':canonical(value);
export function planIssues(config,id,step){
 const plan=config.destination_templates.find(p=>p.id===id),rule=config.delivery.rules.find(r=>r.id===plan?.organized_rule),scope=config.delivery.cloud_scopes[rule?.cloud_scope_id],mappings=config.delivery.mappings.filter(m=>m.cloud_scope_id===rule?.cloud_scope_id),errors=[];
 const required=(value,label)=>{if(typeof value!=='string'||!value.trim())errors.push({label,message:'请选择或填写'+label})};
 const path=(value,label)=>{required(value,label);if(value&&(!value.startsWith('/')||value.split('/').some(s=>s==='..')||/[\r\n\0]/.test(value)))errors.push({label,message:label+'需要容器内的绝对路径，不能包含 ..'})};
 if(!plan)return [{label:'方案',message:'请选择方案'}];
 if(step===0){required(plan.category_id,'MoviePilot 分类');required(plan.downloader,'下载器');path(plan.save_path,'下载目录');required(config.policy.bindings[plan.category_id],'收录与升级策略')}
 if(step===1){required(scope?.cd2_plugin,'CD2 插件实例');required(scope?.p115_plugin,'115 插件实例');path(scope?.root,'云盘根目录');if(!scope?.allowed_prefixes?.length)errors.push({label:'允许写入的路径',message:'至少填写一个允许写入的路径'});for(const p of scope?.allowed_prefixes||[]){path(p,'允许写入的路径');if(scope.root&&!(p===scope.root||p.startsWith(scope.root.replace(/\/$/,'')+'/')))errors.push({label:'允许写入的路径',message:'允许写入的路径必须位于云盘根目录内'})}}
 if(step===2){if(!mappings.length)errors.push({label:'媒体库',message:'请先补齐媒体库设置'});for(const m of mappings){required(m.emby_service,'Emby 服务');required(m.library_id,'媒体库');for(const [key,label] of [['emby_prefix','Emby 中的 STRM'],['local_strm_prefix','MP 可以读取'],['playback_prefix','STRM 内容'],['cd2_prefix','CD2 内部']])required(m[key],label)}required(config.delivery.policy_bindings[plan.category_id],'在库版本的比较策略')}
 if(step===3){for(const [key,label] of [['local_root','本地监控目录'],['staging_root','115 暂存目录'],['incoming_root','Symedia 接收入口']])path(rule?.[key],label);if(!rule?.consumer_roots?.length)errors.push({label:'Symedia 监控目录',message:'填写 Symedia 实际监控的目录'});if(rule?.consumer_roots?.some(p=>rule.staging_root===p||rule.staging_root?.startsWith(p.replace(/\/$/,'')+'/')))errors.push({label:'115 暂存目录',message:'暂存目录必须位于 Symedia 监控范围外'})}
 return errors;
}
export function changedGroups(before,after){return Object.keys(after||{}).filter(k=>k!=='configuration_receipt'&&key(before?.[k])!==key(after[k]))}
export function sharedChanges(before,after,id){
 const plan=after.destination_templates.find(p=>p.id===id),rule=after.delivery.rules.find(r=>r.id===plan?.organized_rule);if(!rule)return [];
 const result=[],scope=rule.cloud_scope_id,plans=after.destination_templates;
 const inspect=(kind,name,previous,current,affected)=>{if(!previous||!current)return;const fields=Object.keys(current).filter(k=>!['revision','id'].includes(k)&&key(previous[k])!==key(current[k]));if(fields.length&&affected.length)result.push({kind,name,fields,plans:affected.map(p=>p.id)})};
 const sameScope=plans.filter(p=>p.id!==id&&after.delivery.rules.some(r=>r.id===p.organized_rule&&r.cloud_scope_id===scope));
 inspect('云盘',scope,before.delivery?.cloud_scopes[scope],after.delivery.cloud_scopes[scope],sameScope);
 inspect('整理设置',rule.id,before.delivery?.rules.find(r=>r.id===rule.id),rule,plans.filter(p=>p.id!==id&&p.organized_rule===rule.id));
 for(const m of after.delivery.mappings.filter(m=>m.cloud_scope_id===scope))inspect('媒体库路径',m.id,before.delivery?.mappings.find(x=>x.id===m.id),m,sameScope);
 for(const key of ['policy','delivery'])if(before[key]?.[key==='policy'?'bindings':'policy_bindings']?.[plan.category_id]!==after[key][key==='policy'?'bindings':'policy_bindings'][plan.category_id]){const affected=plans.filter(p=>p.id!==id&&p.category_id===plan.category_id);if(affected.length)result.push({kind:key==='policy'?'订阅策略':'库内版本策略',fields:['policy'],plans:affected.map(p=>p.id)})}
 return result;
}
