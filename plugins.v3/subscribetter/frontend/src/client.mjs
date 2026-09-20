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
  const preview=await client.post('/configuration/preview',{revision:current.revision,digest:current.digest,patch:draft});
  if (!preview.valid || !preview.config) {const error=new Error('配置预检失败');error.validationErrors=preview.errors||['配置预检失败'];throw error;}
  emit(preview.config);
  return preview.config;
}
export const applyBody=(receipt,operationId)=>({preview_id:receipt.preview_id,preview_digest:receipt.preview_digest,operation_id:operationId,confirm:true});
export const receiptBody=(receipt,operationId)=>({receipt_id:receipt.receipt_id,revision:receipt.revision,digest:receipt.digest,operation_id:operationId,confirm:true});
export const id=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
export const pathFor=(template,values)=>template.replace(/\{([^}:]+)(?::path)?\}/g,(_,key)=>{if(values[key]===undefined||values[key]==='')throw new Error('请选择 '+key);return encodeURIComponent(values[key])});
export function errorText(error) {
  const status=error?.response?.status||error?.status;
  const detail=error?.response?.data?.detail||error?.message;
  const reason=typeof detail==='string'&&/^[A-Z0-9_: .-]{1,180}$/.test(detail)?detail:'';
  return [status?`HTTP ${status}`:'请求未完成',reason||'请检查当前状态；不自动重发操作。'].join(' · ');
}
