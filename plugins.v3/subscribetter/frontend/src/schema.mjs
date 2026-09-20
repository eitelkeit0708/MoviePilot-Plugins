import contract from './contract.json' with {type:'json'};
export {contract};
export const clone=value=>JSON.parse(JSON.stringify(value));
export function resolve(schema={}) {return schema.$ref?{...contract.schemas[schema.$ref.split('/').pop()],...Object.fromEntries(Object.entries(schema).filter(([k])=>k!=='$ref'))}:schema}
export function variant(schema,value) {
  schema=resolve(schema);
  if(schema.anyOf) return resolve(schema.anyOf.find(s=>s.type===typeof value || (Array.isArray(value)&&s.type==='array') || (value===null&&s.type==='null')) || schema.anyOf.find(s=>s.type!=='null') || {});
  return schema;
}
export function initial(schema) {
  schema=resolve(schema);
  if(Object.hasOwn(schema,'default'))return clone(schema.default);
  if(Object.hasOwn(schema,'const'))return clone(schema.const);
  if(schema.anyOf)return initial(schema.anyOf.find(s=>s.type!=='null')||{});
  if(schema.enum)return schema.enum[0];
  if(schema.type==='object'||schema.properties)return Object.fromEntries(Object.entries(schema.properties||{}).filter(([key,s])=>(schema.required||[]).includes(key)||Object.hasOwn(resolve(s),'default')).map(([key,s])=>[key,initial(s)]));
  if(schema.type==='array')return [];
  if(schema.type==='boolean')return false;
  if(schema.type==='null')return null;
  if(schema.type==='integer'||schema.type==='number')return schema.minimum??(schema.exclusiveMinimum!==undefined?schema.exclusiveMinimum+1:0);
  return '';
}
export const bodySchema=path=>resolve(contract.paths[path]?.post?.requestBody?.content?.['application/json']?.schema||{});
export const parameters=path=>(contract.paths[path]?.get?.parameters||contract.paths[path]?.post?.parameters||[]).filter(p=>p.in==='path'||p.in==='query');
export function clean(schema,value) {
  const s=variant(schema,value);
  if(value===null)return null;
  if(s.properties)return Object.fromEntries(Object.entries(value||{}).filter(([k])=>Object.hasOwn(s.properties,k)).map(([k,v])=>[k,clean(s.properties[k],v)]));
  if(s.type==='array')return (value||[]).map(v=>clean(s.items,v));
  return clone(value);
}
export function validate(schema,value,path='') {
  const base=resolve(schema),s=variant(base,value),errors=[];
  if(value===null){if(base.anyOf?.some(v=>v.type==='null')||base.type==='null')return [];return [path+' 不能为空'];}
  if(s.const!==undefined&&value!==s.const)errors.push(path+' 必须为 '+s.const);
  if(s.enum&&!s.enum.includes(value))errors.push(path+' 请选择有效选项');
  if(s.type==='integer'&&!Number.isSafeInteger(value))errors.push(path+' 必须为安全整数');
  if(['number','integer'].includes(s.type)) {
    if(typeof value!=='number'||!Number.isFinite(value))errors.push(path+' 必须为有限数值');
    for(const [k,test] of [['minimum',v=>value<v],['maximum',v=>value>v],['exclusiveMinimum',v=>value<=v],['exclusiveMaximum',v=>value>=v]])if(s[k]!==undefined&&test(s[k]))errors.push(path+' 超出边界 '+s[k]);
  }
  if(s.type==='boolean'&&typeof value!=='boolean')errors.push(path+' 必须为布尔值');
  if(s.type==='string'){
    if(typeof value!=='string')errors.push(path+' 必须为文本');
    else {if(s.minLength&&value.length<s.minLength||s.maxLength&&value.length>s.maxLength)errors.push(path+' 文本长度不合规');if(s.pattern&&!new RegExp(s.pattern).test(value))errors.push(path+' 格式不合规');}
  }
  if(s.type==='array') {
    if(!Array.isArray(value))return [path+' 必须为列表'];
    if(s.minItems&&value.length<s.minItems||s.maxItems&&value.length>s.maxItems)errors.push(path+' 条目数超出边界');
    value.forEach((v,i)=>errors.push(...validate(s.items||{},v,`${path}[${i+1}]`)));
  }
  if(s.properties){
    for(const key of s.required||[])if(value[key]===undefined)errors.push(path+'.'+key+' 必填');
    for(const [key,v] of Object.entries(value))if(s.properties[key])errors.push(...validate(s.properties[key],v,path+'.'+key));else if(s.additionalProperties===false)errors.push(path+'.'+key+' 不允许');
  }
  return errors;
}
