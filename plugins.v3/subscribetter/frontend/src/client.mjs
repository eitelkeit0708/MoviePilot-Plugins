export function unwrap(value) {
  if (value && Object.keys(value).length===3 && typeof value.success === 'boolean' && typeof value.message === 'string' && Object.hasOwn(value,'data')) {
    if (!value.success) throw new Error('请求被宿主拒绝，请刷新当前状态后检查。');
    return value.data;
  }
  return value;
}
export function createClient(api,pluginId,sourcePluginId) {
  if (!api || !pluginId) throw new Error('缺少宿主实例 API。请从插件页面打开。');
  const prefix='plugin/'+encodeURIComponent(pluginId);
  const call=async(method,path,body,options={})=>{
    if (!path.startsWith('/')) throw new Error('INVALID_API_PATH');
    return unwrap(await (method==='get'?api.get(prefix+path,{...options,feedback:'silent'}):api.post(prefix+path,body,{...options,feedback:'silent'})));
  };
  return {get:(path,options)=>call('get',path,null,options),post:(path,body,options)=>call('post',path,body,options),pluginId,sourcePluginId};
}
export function createReadGate() {
  let sequence=0,closed=false,controller;
  return {begin(){controller?.abort();controller=new AbortController();const n=++sequence;return {signal:controller.signal,current:()=>!closed&&n===sequence}},close(){closed=true;sequence++;controller?.abort()}};
}
export async function prepareSave(client,current,draft,emit) {
  const preview=await client.post('/configuration/preview',{revision:current.revision,digest:current.digest,mode:'replace',patch:draft});
  if (!preview.valid || !preview.config) {const error=new Error('配置预检失败');error.validationErrors=preview.errors||['配置预检失败'];throw error;}
  if(client.pluginId)saveFollowup(client.pluginId,{receipt:preview.config.configuration_receipt,digest:preview.digest});
  emit(preview.config);
  return preview.config;
}
const followups=new Map();
const followupKey=instance=>'subscribetter:followup:'+instance;
export function saveFollowup(instance,value){
 const safe=Object.fromEntries(['receipt','digest','operation','group'].filter(k=>typeof value[k]==='string').map(k=>[k,value[k]]));
 followups.set(instance,safe);try{sessionStorage.setItem(followupKey(instance),JSON.stringify(safe))}catch{}
}
export function readFollowup(instance){
 try{const value=JSON.parse(sessionStorage.getItem(followupKey(instance))||'null');if(value&&typeof value==='object')return Object.fromEntries(['receipt','digest','operation','group'].filter(k=>typeof value[k]==='string').map(k=>[k,value[k]]))}catch{}
 return followups.get(instance)||null;
}
export function clearFollowup(instance){followups.delete(instance);try{sessionStorage.removeItem(followupKey(instance))}catch{}}
export const applyBody=(receipt,operationId)=>({preview_id:receipt.preview_id,preview_digest:receipt.preview_digest,operation_id:operationId,confirm:true});
export const receiptBody=(receipt,operationId)=>({receipt_id:receipt.receipt_id,revision:receipt.revision,digest:receipt.digest,operation_id:operationId,confirm:true});
export const id=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
export const pathFor=(template,values)=>template.replace(/\{([^}:]+)(?::path)?\}/g,(_,key)=>{if(values[key]===undefined||values[key]==='')throw new Error('请选择 '+key);return encodeURIComponent(values[key])});
export function errorText(error) {
  const status=error?.response?.status||error?.status;
  const detail=error?.response?.data?.detail||error?.message;
  const reason=typeof detail==='string'&&/^[A-Z0-9_: .-]{1,180}$/.test(detail)?detail:'';
  const messages={AI_CONNECTION_UNCONFIGURED:'请先保存服务地址、密钥和模型，再测试连接。',AI_DISABLED:'AI 尚未启用，不能发送模型测试请求。',TASK_PLANS_REQUIRE_RECONCILE:'仍有在途计划或发布结果待核实，请先在传输与待处理中对账。',TASK_NOT_PAUSED:'作品已不处于暂停状态，请刷新后再操作。',STALE_TASK:'作品状态已变化，请刷新详情。',NATIVE_IDENTITY_MISMATCH:'原生订阅不存在或身份已变化，不能恢复，请核对宿主订阅。',NATIVE_SUBSCRIPTION_CHANGED:'原生订阅已变化，请关闭后重新选择。',STALE_CONFIGURATION:'配置已被更新，请刷新后重新编辑。',STALE_READ_REFRESH_REQUIRED:'页面数据已过期，请刷新后再操作。',CONFIG_PREFLIGHT_REQUIRED:'设置尚未通过当前版本校验，请重新保存。',INVALID_CONFIGURATION:'配置不符合要求，请检查所选服务、目录、策略与权限。',STALE_OPERATION:'操作依据已过期，请从原页面重新发起。',STALE_ROLLBACK:'恢复依据已过期，请重新读取迁移状态。',CUTOVER_NOT_READY:'迁移尚未准备好，请先完成页面列出的前置步骤。',MANAGEMENT_STORE_UNAVAILABLE:'暂时无法读取插件记录，请稍后刷新。',WAIT_OWNER:'另一个处理者仍在管理此范围，请先检查插件共存状态。'};
  return [status?`HTTP ${status}`:'请求未完成',messages[reason]||reason||'请检查当前状态；不自动重发操作。'].join(' · ');
}
