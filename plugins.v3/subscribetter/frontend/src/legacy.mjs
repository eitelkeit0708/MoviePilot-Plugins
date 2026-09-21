import {sha256} from '@noble/hashes/sha256';
import {unwrap} from './client.mjs';
export function rawConfig(text) {
  if(typeof JSON.rawJSON!=='function'||typeof JSON.isRawJSON!=='function')throw Error('浏览器不支持无损 JSON 数值；请更新浏览器后重试。');
  if(typeof text!=='string'||text.length>3000000)throw Error('INVALID_NATIVE_CONFIG');
  const value=JSON.parse(text,(_,v,context)=>typeof v==='number'?JSON.rawJSON(context.source):v);
  const config=unwrap(value);
  if(!config||typeof config!=='object'||Array.isArray(config))throw Error('INVALID_NATIVE_CONFIG');
  return config;
}
function unicodeOrder(a,b){const x=Array.from(a),y=Array.from(b);for(let i=0;i<Math.min(x.length,y.length);i++){const d=x[i].codePointAt(0)-y[i].codePointAt(0);if(d)return d;}return x.length-y.length}
export function canonical(value){
  const keys=new Set();function visit(v){if(!v||typeof v!=='object'||JSON.isRawJSON(v))return;if(Array.isArray(v)){v.forEach(visit);return}for(const [k,item] of Object.entries(v)){keys.add(k);visit(item)}}visit(value);
  return JSON.stringify(value,[...keys].sort(unicodeOrder)).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
}
export async function rawDigest(value){const bytes=sha256(new TextEncoder().encode(canonical(value)));return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('')}
export async function saveLegacy(api,change,step,alive=()=>true){
  if(!step||step.instance_id!==change.instance_id||Object.keys(change.changes).some(k=>!['enabled','recognize'].includes(k)||typeof change.changes[k]!=='boolean'))throw Error('INVALID_SELECTED_CHANGE');
  let raw='',config=null,patched='';
  const path='plugin/'+encodeURIComponent(change.instance_id);
  try{
    raw=await api.get(path,{responseType:'text',feedback:'silent'});config=rawConfig(raw);raw='';
    if(await rawDigest(config)!==change.expected_digest)throw Error('LEGACY_CONFIG_CHANGED');
    const restoring=change.expected_digest===step.after_digest;
    const expectedValues=restoring?step.changes:step.before;
    const expectedChanges=restoring?step.before:step.changes;
    if(JSON.stringify(Object.keys(change.changes).sort())!==JSON.stringify(Object.keys(expectedChanges).sort()))throw Error('LEGACY_SCOPE_CHANGED');
    for(const [key,value] of Object.entries(change.changes)){if(value!==expectedChanges[key]||(config[key]??false)!==expectedValues[key])throw Error('LEGACY_FLAGS_CHANGED');config[key]=value;}
    const expected=restoring?step.restore_digest:step.after_digest;
    if(await rawDigest(config)!==expected)throw Error('LEGACY_PATCH_DIGEST_CHANGED');
    patched=canonical(config);config=null;
    if(!alive())throw Error('VIEW_CLOSED');
    unwrap(await api.put(path,patched,{headers:{'Content-Type':'application/json'},feedback:'silent'}));patched='';
    if(!alive())throw Error('LEGACY_SAVE_NOT_VERIFIED');
    raw=await api.get(path,{responseType:'text',feedback:'silent'});config=rawConfig(raw);raw='';
    if(await rawDigest(config)!==expected)throw Error('LEGACY_SAVE_NOT_VERIFIED');
    return {instance_id:change.instance_id,state:'CONFIG_READBACK',digest:expected,runtime:'REQUIRES_CUTOVER_READBACK'};
  } finally{raw='';patched='';config=null;}
}
