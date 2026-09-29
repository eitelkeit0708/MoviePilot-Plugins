const clone=value=>JSON.parse(JSON.stringify(value));
let sequence=0;
const nextId=()=>`condition-${++sequence}`;
const withNewIds=node=>{
  const next={...clone(node),id:nextId()};
  if(next.kind==='group')next.children=next.children.map(withNewIds);
  if(next.kind==='not')next.child=withNewIds(next.child);
  return next;
};

export const fieldDefinitions={
  text:{label:'标题、说明、标签与字幕说明',kind:'text',operators:['contains','regex','eq','ne']},
  title:{label:'标题',kind:'text',operators:['contains','regex','eq','ne']},
  description:{label:'发布说明',kind:'text',operators:['contains','regex','eq','ne']},
  subtitle_description:{label:'字幕说明',kind:'text',operators:['contains','regex','eq','ne']},
  labels:{label:'资源标签',kind:'list',operators:['intersects']},
  original_language:{label:'原始语言',kind:'category',operators:['eq','ne','in']},
  production_countries:{label:'制作地区',kind:'list',operators:['intersects']},
  origin_country:{label:'出品地区',kind:'list',operators:['intersects']},
  genre_ids:{label:'类型编号',kind:'list',operators:['intersects']},
  media_type:{label:'媒体类型',kind:'category',operators:['eq','ne','in']},
  size:{label:'资源大小',kind:'number',operators:['gt','ge','lt','le','eq','ne']},
  seeders:{label:'做种人数',kind:'number',operators:['gt','ge','lt','le','eq','ne']},
  downloadvolumefactor:{label:'下载系数',kind:'number',operators:['gt','ge','lt','le','eq','ne']},
  publish_minutes:{label:'发布时长',kind:'number',operators:['gt','ge','lt','le','eq','ne']}
};
export const fieldLabels=Object.fromEntries(Object.entries(fieldDefinitions).map(([name,definition])=>[name,definition.label]));
export const operatorLabels={contains:'包含文字（忽略大小写）',regex:'匹配正则',eq:'等于',ne:'不等于',gt:'大于',ge:'大于或等于',lt:'小于',le:'小于或等于',in:'属于以下值',intersects:'包含任一值'};
export const operatorsForField=field=>fieldDefinitions[field]?.operators||['eq','ne'];
export const units={B:1n,KiB:1024n,MiB:1048576n,GiB:1073741824n,MB:1000000n,GB:1000000000n};
export const durationUnits={'分钟':1n,'小时':60n,'天':1440n};

function draft(expression){
  const [operator,value]=Object.entries(expression||{})[0]||['literal',true],id=nextId();
  if(operator==='all'||operator==='any')return {id,kind:'group',operator,children:value.map(draft),collapsed:false};
  if(operator==='not')return {id,kind:'not',child:draft(value),collapsed:false};
  if(operator==='literal')return {id,kind:'literal',value};
  if(operator==='registered')return {id,kind:'registered',name:value};
  const [field,expected]=value;
  return {id,kind:'condition',operator,field,value:clone(expected),unit:field==='size'?preferredUnit(expected):field==='publish_minutes'?preferredDurationUnit(expected):'',incomplete:false};
}

export const astToDraft=expression=>draft(clone(expression));

function ast(node){
  if(node.kind==='group')return {[node.operator]:node.children.map(ast)};
  if(node.kind==='not')return {not:ast(node.child)};
  if(node.kind==='literal')return {literal:node.value};
  if(node.kind==='registered')return {registered:node.name};
  if(node.operator==='contains')return {regex:[node.field,String(node.value).replace(/[.*+?^${}()|[\]\\]/g,'\\$&')]};
  return {[node.operator]:[node.field,clone(node.value)]};
}

export function draftToAst(node){
  if(validateDraft(node).length)throw Error('INCOMPLETE_PREDICATE');
  return ast(node);
}

function mapNode(node,id,change){
  if(node.id===id)return change({...node});
  if(node.kind==='group')return {...node,children:node.children.map(child=>mapNode(child,id,change))};
  if(node.kind==='not')return {...node,child:mapNode(node.child,id,change)};
  return node;
}

function mapChildren(node,id,change){
  if(node.kind==='group'){
    const index=node.children.findIndex(child=>child.id===id);
    if(index>=0)return {...node,children:change(node.children,index)};
    return {...node,children:node.children.map(child=>mapChildren(child,id,change))};
  }
  if(node.kind==='not')return {...node,child:mapChildren(node.child,id,change)};
  return node;
}

export const updateNode=(node,id,values)=>mapNode(node,id,current=>({...current,...clone(values)}));
export const setGroupOperator=(node,id,operator)=>updateNode(node,id,{operator});
export const setConditionOperator=(node,id,operator)=>updateNode(node,id,{operator,incomplete:false});

export function isConditionSupported(node){
  if(node.kind!=='condition'||!operatorsForField(node.field).includes(node.operator))return false;
  const kind=fieldDefinitions[node.field]?.kind;
  if(kind==='number')return typeof node.value==='number';
  if(kind==='text')return typeof node.value==='string';
  if(kind==='list')return Array.isArray(node.value);
  if(kind==='category')return node.operator==='in'?Array.isArray(node.value):typeof node.value==='string';
  return false;
}

function emptyForField(field){
  const definition=fieldDefinitions[field],operator=definition?.operators[0]||'eq';
  if(definition?.kind==='number')return {field,operator,value:0,raw:'',unit:field==='size'?'B':field==='publish_minutes'?'分钟':'',incomplete:true};
  if(definition?.kind==='list'||operator==='in')return {field,operator,value:[],unit:'',incomplete:true};
  return {field,operator,value:'',unit:'',incomplete:true};
}

export function setConditionField(node,id,field){
  return mapNode(node,id,current=>{
    if(current.kind!=='condition')return current;
    const candidate={...current,field,unit:field==='size'?'B':field==='publish_minutes'?'分钟':''};
    return isConditionSupported(candidate)?candidate:{...current,...emptyForField(field)};
  });
}

const emptyCondition=()=>({id:nextId(),kind:'condition',operator:'contains',field:'title',value:'',unit:'',incomplete:true});

export function addCondition(node,groupId){
  const id=groupId||node.id;
  return mapNode(node,id,current=>current.kind==='group'?{...current,children:[...current.children,emptyCondition()]}:current);
}

export function addGroup(node,groupId){
  const id=groupId||node.id;
  return mapNode(node,id,current=>current.kind==='group'?{...current,children:[...current.children,{id:nextId(),kind:'group',operator:'all',children:[emptyCondition()],collapsed:false}]}:current);
}

export function removeNode(node,id){
  if(node.kind==='group')return {...node,children:node.children.filter(child=>child.id!==id).map(child=>removeNode(child,id))};
  if(node.kind==='not'&&node.child.id!==id)return {...node,child:removeNode(node.child,id)};
  return node;
}

export const duplicateNode=(node,id)=>mapChildren(node,id,(children,index)=>[
  ...children.slice(0,index+1),withNewIds(children[index]),...children.slice(index+1)
]);

export const wrapNode=(node,id)=>mapChildren(node,id,(children,index)=>[
  ...children.slice(0,index),
  {id:nextId(),kind:'group',operator:'all',children:[children[index]],collapsed:false,name:'条件组'},
  ...children.slice(index+1)
]);

export function toggleNot(node,id){
  if(node.id===id)return node.kind==='not'?node.child:{id:nextId(),kind:'not',child:node,collapsed:false};
  if(node.kind==='group')return {...node,children:node.children.map(child=>toggleNot(child,id))};
  if(node.kind==='not')return {...node,child:toggleNot(node.child,id)};
  return node;
}

export const restoreSnapshot=(_current,snapshot)=>clone(snapshot);

function preferredUnit(value){
  if(!Number.isSafeInteger(value)||value<=0)return 'B';
  for(const unit of ['GiB','MiB','KiB'])if(BigInt(value)%units[unit]===0n)return unit;
  return 'B';
}

function preferredDurationUnit(value){
  if(!Number.isSafeInteger(value)||value<=0)return '分钟';
  for(const unit of ['天','小时'])if(BigInt(value)%durationUnits[unit]===0n)return unit;
  return '分钟';
}

function scaledInteger(raw,factors,unit,error){
  const value=String(raw).trim(),factor=factors[unit];
  if(!factor||!/^\d+(?:\.\d+)?$/.test(value))throw Error(error);
  const [whole,fraction='']=value.split('.'),scale=10n**BigInt(fraction.length);
  const numerator=BigInt(whole+fraction)*factor;
  if(numerator%scale)throw Error(`INEXACT_${error}`);
  const result=numerator/scale;
  if(result>BigInt(Number.MAX_SAFE_INTEGER))throw Error('SAFE_INTEGER');
  return Number(result);
}

export function sizeBytes(raw,unit='B'){
  return scaledInteger(raw,units,unit,'SIZE');
}

export const durationMinutes=(raw,unit='分钟')=>scaledInteger(raw,durationUnits,unit,'DURATION');

function scaledDisplay(amount,factor,error){
  const value=BigInt(amount),whole=value/factor;let remainder=value%factor;
  if(!remainder)return String(whole);
  let fraction='';
  for(let i=0;remainder&&i<30;i++){remainder*=10n;fraction+=String(remainder/factor);remainder%=factor}
  if(remainder)throw Error(error);
  return `${whole}.${fraction.replace(/0+$/,'')}`;
}

export function sizeDisplay(bytes,unit='B'){
  if(!Number.isSafeInteger(bytes)||bytes<0||!units[unit])throw Error('INVALID_SIZE');
  return scaledDisplay(bytes,units[unit],'INEXACT_SIZE');
}

export function durationDisplay(minutes,unit='分钟'){
  if(!Number.isSafeInteger(minutes)||minutes<0||!durationUnits[unit])throw Error('INVALID_DURATION');
  return scaledDisplay(minutes,durationUnits[unit],'INEXACT_DURATION');
}

const numericFields=new Set(['size','seeders','downloadvolumefactor','publish_minutes']);
const numericOperators=new Set(['gt','ge','lt','le']);

export function validateDraft(node,errors=[]){
  if(node.kind==='group'){
    if(!node.children.length)errors.push({id:node.id,code:'EMPTY_GROUP',message:'请至少添加一条条件。'});
    node.children.forEach(child=>validateDraft(child,errors));
  }else if(node.kind==='not')validateDraft(node.child,errors);
  else if(node.kind==='literal'&&typeof node.value!=='boolean')errors.push({id:node.id,code:'INVALID_LITERAL',message:'固定结果无效。'});
  else if(node.kind==='registered'&&!node.name)errors.push({id:node.id,code:'MISSING_RULE',message:'请选择已有规则。'});
  else if(node.kind==='condition'){
    if(node.incomplete)errors.push({id:node.id,code:'INCOMPLETE',message:'请填写这条条件。'});
    else if(!node.field||!node.operator)errors.push({id:node.id,code:'INCOMPLETE',message:'请完整选择字段和判断关系。'});
    else if(!operatorsForField(node.field).includes(node.operator))errors.push({id:node.id,code:'INCOMPATIBLE_FIELD',message:'这个字段不支持当前判断关系；原条件仍保留。'});
    else if((node.operator==='regex'||node.operator==='contains')&&typeof node.value!=='string')errors.push({id:node.id,code:'INCOMPATIBLE_VALUE',message:'文字条件需要文字内容；原值仍保留。'});
    else if((node.operator==='regex'||node.operator==='contains')&&!node.value)errors.push({id:node.id,code:'EMPTY_REGEX',message:'请填写要匹配的文字。'});
    else if(numericOperators.has(node.operator)&&typeof node.value!=='number')errors.push({id:node.id,code:'INCOMPATIBLE_VALUE',message:'数值比较需要数字；原值仍保留。'});
    else if(numericOperators.has(node.operator)&&!numericFields.has(node.field))errors.push({id:node.id,code:'INCOMPATIBLE_FIELD',message:'当前字段不支持数值比较；原值仍保留。'});
    else if((node.operator==='in'||node.operator==='intersects')&&!Array.isArray(node.value))errors.push({id:node.id,code:'INCOMPATIBLE_VALUE',message:'集合比较需要一组值；原值仍保留。'});
    else if(node.field==='size'&&typeof node.value==='number'&&(!Number.isSafeInteger(node.value)||node.value<0))errors.push({id:node.id,code:'INVALID_SIZE',message:'资源大小必须是可精确保存的非负字节数。'});
  }
  return errors;
}

function valueText(node){
  if(node.field==='size'&&typeof node.value==='number')return `${sizeDisplay(node.value,node.unit||preferredUnit(node.value))} ${node.unit||preferredUnit(node.value)}`;
  if(node.field==='publish_minutes'&&typeof node.value==='number')return `${durationDisplay(node.value,node.unit||preferredDurationUnit(node.value))} ${node.unit||preferredDurationUnit(node.value)}`;
  if(Array.isArray(node.value))return node.value.map(value=>JSON.stringify(value)).join('、');
  if(node.value===null)return '空值';
  if(typeof node.value==='object')return '高级值';
  return String(node.value);
}

function summary(node,nested=false){
  if(node.kind==='group'){
    const text=node.children.map(child=>summary(child,true)).join(node.operator==='all'?'，并且 ':'，或者 ');
    return nested?`（${text}）`:text;
  }
  if(node.kind==='not')return `不满足（${predicateSummary(node.child)}）`;
  if(node.kind==='literal')return node.value?'恒为通过':'恒为不通过';
  if(node.kind==='registered')return `使用规则“${node.name}”`;
  return `${fieldLabels[node.field]||node.field}${operatorLabels[node.operator]||node.operator} ${valueText(node)}`;
}

export const predicateSummary=node=>summary(node);

export function referencedFields(expression){
  const fields=new Set();
  function visit(node){
    const [operator,args]=Object.entries(node||{})[0]||[];
    if(operator==='all'||operator==='any')args.forEach(visit);
    else if(operator==='not')visit(args);
    else if(operator&&operator!=='literal'&&operator!=='registered'){
      if(args[0]==='text')['title','description','labels','subtitle_description'].forEach(field=>fields.add(field));
      else fields.add(args[0]);
    }
  }
  visit(expression);
  return [...fields].sort();
}
