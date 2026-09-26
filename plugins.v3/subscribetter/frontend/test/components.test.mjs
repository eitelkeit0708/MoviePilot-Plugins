import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';import {pathToFileURL,fileURLToPath} from 'node:url';
import {parse,compileScript} from '@vue/compiler-sfc';
import {createRenderer,h,nextTick,ref} from 'vue';
import {contract} from '../src/schema.mjs';
import {useStatus} from '../src/status.mjs';import {saveFollowup,clearFollowup} from '../src/client.mjs';
const source=fileURLToPath(new URL('../src/',import.meta.url)),out=fileURLToPath(new URL('../.test-build/',import.meta.url));fs.mkdirSync(out,{recursive:true});
for(const filename of fs.readdirSync(source).filter(f=>f.endsWith('.vue'))){const {descriptor}=parse(fs.readFileSync(path.join(source,filename),'utf8'),{filename});let code=compileScript(descriptor,{id:filename,inlineTemplate:true}).content;code=code.replace(/import\s+['"]\.\/style\.css['"];?/g,'').replace(/from\s+(['"])(\.\/[^'"]+)\1/g,(_,quote,p)=>'from '+JSON.stringify(p.endsWith('.vue')?'./'+path.basename(p,'.vue')+'.mjs':pathToFileURL(path.join(source,p)).href));fs.writeFileSync(path.join(out,filename.replace('.vue','.mjs')),code)}
const {default:Action}=await import(pathToFileURL(path.join(out,'Action.mjs')));
const {default:Resource}=await import(pathToFileURL(path.join(out,'Resource.mjs')));
const {default:Page}=await import(pathToFileURL(path.join(out,'Page.mjs')));const {default:Config}=await import(pathToFileURL(path.join(out,'Config.mjs')));
const {default:Subscriptions}=await import(pathToFileURL(path.join(out,'Subscriptions.mjs')));
const {default:AdoptSubscription}=await import(pathToFileURL(path.join(out,'AdoptSubscription.mjs')));
globalThis.document={activeElement:null};
function element(type){return {type,tagName:type.toUpperCase(),props:{},children:[],style:{},events:{},addEventListener(k,fn){this.events[k]=fn},removeEventListener(k){delete this.events[k]},getAttribute(k){return this.props[k]},get options(){return this.children.filter(n=>n.type==='option')}}}
const renderer=createRenderer({insertStaticContent(value,parent,anchor){const node={type:'#static',text:value,parent};const i=parent.children.indexOf(anchor);if(i<0)parent.children.push(node);else parent.children.splice(i,0,node);return [node,node]},createElement:element,createText:text=>({type:'#text',text}),createComment:text=>({type:'#comment',text}),setText:(n,t)=>n.text=t,setElementText:(n,t)=>n.text=t,patchProp:(n,k,_,v)=>{n.props[k]=v;if(k==='value')n.value=v},insert(n,p,anchor){p.children??=[];if(n.parent)n.parent.children=n.parent.children.filter(x=>x!==n);const i=p.children.indexOf(anchor);if(i<0)p.children.push(n);else p.children.splice(i,0,n);n.parent=p},remove(n){if(n.parent)n.parent.children=n.parent.children.filter(x=>x!==n)},parentNode:n=>n?.parent,nextSibling:n=>n?.parent?.children[n.parent.children.indexOf(n)+1]||null});
const walk=(node,predicate)=>[...(predicate(node)?[node]:[]),...(node.children||[]).flatMap(n=>walk(n,predicate))];const text=node=>[node.text||'',...(node.children||[]).map(text)].join('');
const settle=async()=>{for(let i=0;i<8;i++){await Promise.resolve();await nextTick()}};
test('saved confirmation requires applied readback and disappears when a later refresh fails',async()=>{
 let status,fail=false;const client={pluginId:'status-test',get:async p=>{if(fail)throw {status:503};if(p==='/configuration')return {revision:4,digest:'digest',config:{configuration_receipt:'receipt'}};if(p==='/diagnostics')return {snapshot:{config_revision:4}};return {state:'APPLIED'}}};
 const root={children:[]},app=renderer.createApp({setup(){status=useStatus(client);return()=>h('div')}});app.mount(root);
 try{saveFollowup(client.pluginId,{receipt:'receipt',digest:'other'});await status.refresh();assert.match(status.saveState.value,/尚未确认/);saveFollowup(client.pluginId,{receipt:'receipt',digest:'digest'});await status.refresh();assert.equal(status.saveState.value,'设置已保存并确认生效');fail=true;await status.refresh();assert.equal(status.saveState.value,'');assert.match(status.error.value,/503/)}finally{clearFollowup(client.pluginId);app.unmount()}
});
function fixture(component,{get,post,extra={}}={}){const calls=[];const config=structuredClone(contract.defaults);const current={revision:4,digest:'a'.repeat(64),config};const health={generation:6,dry_run:true,ordinary_work_active:false,safety_active:true,errors:[],snapshot:{config_revision:4,runtime_generation:6}};const api={get:async(p,o)=>{calls.push(['get',p,o]);if(get)return get(p,o,current,health);if(p.endsWith('/configuration'))return current;if(p.endsWith('/diagnostics'))return health;if(p.endsWith('/configuration/categories'))return {revision:1,categories:[]};return {items:[],total:0,next_offset:null,truncated:false,snapshot:{config_revision:4,runtime_generation:6,high_watermark:'0'}}},post:async(p,b)=>{calls.push(['post',p,b]);return post?post(p,b):{valid:true,errors:[],config:{...config,configuration_receipt:'p'}}}};const saves=[];const root={children:[]};const app=renderer.createApp(component,{api,pluginId:'Clone',sourcePluginId:'SubscriBetter',initialConfig:{...config,password:'SENTINEL'},...extra,onSave:x=>saves.push(x)});app.config.warnHandler=()=>{};app.component('VBtn',{inheritAttrs:false,setup(_,ctx){return()=>h('button',ctx.attrs,ctx.slots.default?.())}});app.component('VDialog',{setup(_,ctx){return()=>h('div',{},ctx.slots.default?.())}});app.mount(root);return {root,app,calls,saves,current}}
test('actual Config mount and save omit private initial fields; number controls send numbers; emit once without PUT',async()=>{
 const f=fixture(Config);await settle();assert.equal(f.calls.filter(c=>c[1].endsWith('/configuration')).length,1);
 const lifetime=walk(f.root,n=>n.type==='label'&&text(n).includes('电影期限'))[0];const number=walk(lifetime,n=>n.type==='input'&&n.props.type==='number')[0];assert.ok(number);number.props.onInput({target:{value:'9'}});await settle();
 const save=walk(f.root,n=>n.type==='button'&&text(n)==='保存设置')[0];assert.ok(save);await save.props.onClick();await settle();assert.equal(f.saves.length,1);assert.ok(!JSON.stringify(f.saves).includes('SENTINEL'));assert.ok(!JSON.stringify(f.calls.filter(c=>c[0]==='post')).includes('SENTINEL'));assert.equal(f.calls.find(c=>c[0]==='post')[2].patch.lifecycle.movie_days,9);assert.ok(f.calls.filter(c=>c[0]==='post').every(c=>c[1].endsWith('/configuration/preview')));await save.props.onClick();assert.equal(f.saves.length,1);f.app.unmount();
});
test('Config navigation shows only the selected group and preserves unsaved edits across groups',async()=>{
 const f=fixture(Config);await settle();
 try{
  const nav=walk(f.root,n=>n.props?.['aria-label']==='配置分组')[0];
  const content=walk(f.root,n=>n.props?.class==='sb-config-content')[0];
  for(const button of walk(nav,n=>n.type==='button')){
   button.props.onClick();await settle();
   const visible=content.children.filter(n=>n.type==='div'&&!n.props.hidden&&n.style.display!=='none');
   assert.equal(visible.length,1,text(button));
   assert.equal(walk(visible[0],n=>n.type==='h3')[0].text,text(button));
   if(text(button)==='生命周期'){
    const lifetime=walk(visible[0],n=>n.type==='label'&&text(n).includes('电影期限'))[0];
    walk(lifetime,n=>n.type==='input'&&n.props.type==='number')[0].props.onInput({target:{value:'9'}});await settle();
   }
  }
  walk(nav,n=>n.type==='button'&&text(n)==='生命周期')[0].props.onClick();await settle();
  const lifetime=walk(content,n=>n.type==='label'&&text(n).includes('电影期限'))[0];
  assert.equal(walk(lifetime,n=>n.type==='input'&&n.props.type==='number')[0].props.value,9);
  assert.equal(f.calls.filter(c=>c[0]==='post').length,0);
 }finally{f.app.unmount()}
});
test('Page offers human workflow navigation and keeps all nine diagnostic domains reachable without writes',async()=>{
 const f=fixture(Page);await settle();for(const title of ['订阅','发现','传输与待处理','策略','设置'])assert.ok(walk(f.root,n=>n.type==='button'&&text(n)===title)[0],title);
 walk(f.root,n=>n.type==='button'&&text(n)==='高级诊断')[0].props.onClick();await settle();
 for(const title of ['候选决策','版本档案','交付队列','策略与预演','服务与健康','榜单发现','解析与 AI','整合迁移','目标与任务']){const button=walk(f.root,n=>n.type==='button'&&text(n)===title)[0];assert.ok(button,title);button.props.onClick();await settle();assert.ok(text(f.root).includes(title));}assert.ok(!text(f.root).includes('纳管 / 一次性目标'));assert.equal(f.calls.filter(c=>c[0]==='post').length,0);assert.ok(f.calls.every(c=>c[1].startsWith('plugin/Clone/')));f.app.unmount();
});
test('delayed Config preflight cannot emit after unmount',async()=>{
 let complete;const f=fixture(Config,{post:()=>new Promise(resolve=>complete=resolve)});await settle();const save=walk(f.root,n=>n.type==='button'&&text(n)==='保存设置')[0];save.props.onClick();await settle();f.app.unmount();complete({valid:true,errors:[],config:contract.defaults});await settle();assert.equal(f.saves.length,0);
});
test('configuration refresh cannot save an older draft using the newly fetched revision',async()=>{
 let release,refreshing=false;const f=fixture(Config,{get:async(p,o,c,h)=>{if(p.endsWith('/configuration'))return refreshing?{...c,revision:5,digest:'b'.repeat(64),config:{...c.config,configuration_receipt:'delayed'}}:c;if(p.endsWith('/diagnostics'))return refreshing?{...h,snapshot:{config_revision:5}}:h;if(p.endsWith('/migration/receipts/delayed'))return new Promise(r=>release=r);return {items:[],categories:[],result:{}}}});await settle();
 try{refreshing=true;const pending=walk(f.root,n=>n.type==='button'&&text(n).startsWith('刷新当前值'))[0].props.onClick();await settle();await walk(f.root,n=>n.type==='button'&&text(n)==='保存设置')[0].props.onClick();assert.equal(f.saves.length,0);assert.equal(f.calls.filter(c=>c[0]==='post').length,0);release({state:'APPLIED'});await pending;}finally{release?.({state:'APPLIED'});f.app.unmount()}
});

test('actual immutable preview confirmation sends only receipt/opid/confirm and honors blockers',async()=>{
 const calls=[];const preview={preview_id:'p',preview_digest:'d',kind:'history',objects:{record_ids:[1]},permissions:{},blockers:[],revisions:{},expires_at:'later'};
 const client={post:async(p,b)=>{calls.push([p,structuredClone(b)]);return p.endsWith('/preview')?preview:{state:'APPLIED',result:{}}},get:async()=>({})};
 const status={current:ref({revision:4}),health:ref({generation:6}),error:ref('')};
 const f=fixture(Action,{extra:{action:['隐藏历史','/discovery/history/cleanup/preview'],client,status,context:{record_ids:[1]}}});await settle();
 const prepare=walk(f.root,n=>n.type==='button'&&text(n)==='取得不可变预览')[0];await prepare.props.onClick();await settle();
 assert.deepEqual(calls[0][1],{config_revision:4,runtime_generation:6,record_ids:[1]});preview.objects.record_ids.push(2);
 const confirm=walk(f.root,n=>n.type==='button'&&text(n)==='确认此对象与范围')[0];await confirm.props.onClick();await settle();assert.equal(calls.length,2);assert.deepEqual(Object.keys(calls[1][1]).sort(),['confirm','operation_id','preview_digest','preview_id']);assert.equal(calls[1][1].preview_id,'p');f.app.unmount();
 const blocked=fixture(Action,{extra:{action:['隐藏历史','/discovery/history/cleanup/preview'],client:{...client,post:async()=>({...preview,blockers:['UNKNOWN']})},status,context:{record_ids:[1]}}});await settle();await walk(blocked.root,n=>n.type==='button'&&text(n)==='取得不可变预览')[0].props.onClick();await settle();assert.equal(walk(blocked.root,n=>n.type==='button'&&text(n)==='确认此对象与范围')[0].props.disabled,true);blocked.app.unmount();
});
test('actual resource sends server pagination and keeps errors distinct from empty',async()=>{
 const calls=[];let failure=false;const client={get:async(p,o)=>{calls.push([p,o]);if(failure)throw {status:503};return {items:[{id:1,state:'ACTIVE'}],total:30,next_offset:25,truncated:true,snapshot:{}}}};
 const f=fixture(Resource,{extra:{client,resource:['任务','/tasks']}});await settle();await walk(f.root,n=>n.type==='button'&&text(n)==='下一页')[0].props.onClick();await settle();assert.equal(calls.at(-1)[1].params.offset,25);assert.equal(calls.at(-1)[1].params.limit,25);failure=true;await walk(f.root,n=>n.type==='form')[0].props.onSubmit({preventDefault(){}});await settle();assert.ok(text(f.root).includes('HTTP 503'));assert.ok(!text(f.root).includes('当前筛选下没有记录'));f.app.unmount();
});

test('actual diagnostics uses worker revision for the typed local reconcile action',async()=>{
 const events=[];const rule={id:'规则/one',revision:'actual-worker-revision',enabled:true};
 const f=fixture(Resource,{extra:{client:{get:async()=>({services:{local_rules:[rule]}})},resource:['健康','/diagnostics'],onAction:(...args)=>events.push(args)}});
 await settle();walk(f.root,n=>n.type==='button'&&text(n)==='对账此规则')[0].props.onClick();
 assert.deepEqual(events[0],[['本地规则对账','/health/reconcile'],{component:'local',object_id:rule.id,revision:rule.revision}]);f.app.unmount();
});
test('actual scan rows navigate to bounded items and select the persisted mapping-test identity',async()=>{
 const opened=[],selected=[];const client={get:async()=>({items:[{id:'scan',data:{}}],total:1,next_offset:null})};
 const f=fixture(Resource,{extra:{client,resource:['扫描','/health/records/{section}',{section:'archive_scans'}],onOpen:r=>opened.push(r)}});await settle();
 walk(f.root,n=>n.type==='button'&&text(n)==='扫描条目 / 映射测试对象')[0].props.onClick();assert.deepEqual(opened[0],['扫描条目 / 映射测试对象','/archive/scans/{scan_id}/items',{scan_id:'scan'}]);f.app.unmount();
 const items=fixture(Resource,{extra:{client:{get:async()=>({items:[{id:'persisted-item',data:{service:'Emby test',library:'L'}}],total:1,next_offset:null})},resource:opened[0],onSelect:r=>selected.push(r)}});await settle();walk(items.root,n=>n.type==='button'&&text(n)==='选为操作对象')[0].props.onClick();assert.equal(selected[0].item_id,'persisted-item');assert.equal(selected[0].scan_id,'scan');assert.equal(selected[0].service,'Emby test');items.app.unmount();
});

test('subscription cards show titles; late detail cannot replace the selected work; failed reads retain marked stale data',async()=>{
 const tasks=[{id:1,title:'侠女内莉',year:'2026',media_type:'电视剧',season:1,state:'ACTIVE'},{id:2,title:'一瓯春',year:'2026',media_type:'电视剧',season:1,state:'ACTIVE'}];
 let old,fail=false;const detail=task=>({task,units:{items:[],total:0},snapshot:{}});
 const client={get:async path=>{if(path==='/tasks'){if(fail)throw {status:503};return {items:tasks,total:2,next_offset:null}}if(path.endsWith('/1'))return new Promise(r=>old=r);return detail(tasks[1])}};
 const status={current:ref({config:{}}),health:ref({}),error:ref('')};
 const f=fixture(Subscriptions,{extra:{client,status}});await settle();
 const cards=()=>walk(f.root,n=>n.type==='button'&&n.props.class==='sb-subscription-card');assert.equal(cards().length,2);
 cards()[0].props.onClick();await settle();cards()[1].props.onClick();await settle();old(detail(tasks[0]));await settle();
 const panel=walk(f.root,n=>n.props?.['aria-label']==='作品详情')[0];assert.ok(text(panel).includes('一瓯春'));assert.ok(!text(panel).includes('侠女内莉'));
 fail=true;await walk(f.root,n=>n.type==='form')[0].props.onSubmit({preventDefault(){}});await settle();assert.ok(text(f.root).includes('可能已过期'));assert.equal(cards().length,2);f.app.unmount();
});

test('adoption UI reads native subscriptions and confirms unchanged identity without creating a subscription',async()=>{
 const native={id:42,type:'电视剧',media_source:'douban',media_id:'37029663',tmdb_id:999,name:'侠女内莉',year:'2026',season:1,episode_group:'group'};const writes=[];
 const status={current:ref({config:{enabled:true,dry_run:false,destination_templates:[{id:'测试方案',downloader:'测试下载器'}]}}),error:ref('')};
 const f=fixture(AdoptSubscription,{get:async p=>p==='subscribe/'?[native]:native,extra:{status,client:{post:async(p,b)=>{writes.push([p,b]);return {id:1}}}}});await settle();
 assert.equal(writes.length,0);await walk(f.root,n=>n.type==='button'&&text(n).includes('侠女内莉'))[0].props.onClick();await settle();
 assert.ok(text(f.root).includes('原生订阅将暂停执行'));walk(f.root,n=>n.type==='select')[0].props['onUpdate:modelValue']('测试方案');await settle();
 const form=walk(f.root,n=>n.type==='form')[0];await form.props.onSubmit({preventDefault(){}});await form.props.onSubmit({preventDefault(){}});await settle();
 assert.equal(writes.length,1);assert.equal(writes[0][0],'/intents');assert.equal(writes[0][1].native_id,42);assert.equal(writes[0][1].adopt,true);assert.equal(writes[0][1].media_id,'37029663');assert.equal(writes[0][1].episode_group,'group');assert.deepEqual(f.calls.map(c=>c[1]),['subscribe/','subscribe/42']);f.app.unmount();
});

test('adoption blocks changed native identity and does not submit after the dialog closes',async()=>{
 const native={id:42,type:'电视剧',media_source:'douban',media_id:'37029663',name:'侠女内莉',year:'2026',season:1};
 for(const closed of [false,true]){let finish;const writes=[];const status={current:ref({config:{enabled:true,dry_run:false,destination_templates:[{id:'target'}]}}),error:ref('')};
 const f=fixture(AdoptSubscription,{get:async p=>p==='subscribe/'?[native]:new Promise(r=>finish=r),extra:{status,client:{post:async(...args)=>writes.push(args)}}});await settle();
 walk(f.root,n=>n.type==='button'&&text(n).includes('侠女内莉'))[0].props.onClick();await settle();walk(f.root,n=>n.type==='select')[0].props['onUpdate:modelValue']('target');await settle();
 const pending=walk(f.root,n=>n.type==='form')[0].props.onSubmit({preventDefault(){}});await settle();if(closed)f.app.unmount();finish({...native,season:closed?1:2});await pending;await settle();assert.equal(writes.length,0);if(!closed){assert.ok(text(f.root).includes('原生订阅已变化'));f.app.unmount()}}
});


test('business pages replace generic first screens and bind source actions to the chosen source',async()=>{
 const f=fixture(Page,{get:async(p,o,c,h)=>{
 if(p.endsWith('/configuration'))return c;if(p.endsWith('/diagnostics'))return h;
 if(p.endsWith('/discovery/sources'))return {items:[{source_id:'weekly',last_state:'OK',next_due:0,config:{kind:'rsshub',route_key:'tv_global_best_weekly'}}],total:1,next_offset:null};
 if(p.endsWith('/discovery/catalog'))return {result:{routes:[{key:'tv_global_best_weekly',label:'全球口碑剧集榜'}]}};
 return {items:[],total:0,next_offset:null,result:{},categories:[]};}});await settle();
 for(const [tab,label] of [['发现','豆瓣榜单'],['传输与待处理','传输与待处理'],['策略','质量策略'],['设置','服务与设置']]){walk(f.root,n=>n.type==='button'&&text(n)===tab)[0].props.onClick();await settle();assert.ok(walk(f.root,n=>n.props?.['aria-label']===label).length,label);assert.equal(walk(f.root,n=>n.props?.['aria-label']==='当前视图资源').length,0)}
 assert.equal(f.calls.filter(c=>c[0]==='post').length,0);f.app.unmount();
});


test('business action binds object IDs without editable internal keys and submits only the server receipt',async()=>{
 const calls=[];const status={current:ref({revision:4}),health:ref({generation:6}),error:ref('')};
 const f=fixture(Action,{extra:{action:['取消交付','/delivery/{bundle_id}/cancel/preview'],context:{_bound:true,_label:'GATE24 第九集',_description:'保留下载器任务与数据',bundle_id:'b',revision:7,reason:'ADMIN_CANCEL'},status,client:{post:async(p,b)=>{calls.push([p,b]);return {preview_id:'p',preview_digest:'d',blockers:[],objects:{bundle_id:'b'},permissions:{},revisions:{},expires_at:'later'}}}}});await settle();
 assert.ok(text(f.root).includes('GATE24 第九集'));assert.equal(walk(f.root,n=>n.type==='input').length,0);
 await walk(f.root,n=>n.type==='button'&&text(n).includes('核对操作'))[0].props.onClick();await settle();
 assert.equal(calls[0][0],'/delivery/b/cancel/preview');assert.equal(calls[0][1].revision,7);assert.ok(!JSON.stringify(calls).includes('_label'));f.app.unmount();
});


test('source editor accepts a private RSSHub route with type suffix',async()=>{
 const {default:Sources}=await import(pathToFileURL(path.join(out,'Sources.mjs')));const updates=[];
 const modelValue={...contract.defaults.discovery,enabled:false,rsshub_base_url:'http://192.168.50.6:1200',allowed_private_ranges:['192.168.50.6/32'],cron:'0 8 * * *',douban_interval_seconds:5,sources:[]};
 const f=fixture(Sources,{extra:{modelValue,routes:[],templates:[],'onUpdate:modelValue':v=>updates.push(v)}});await settle();
 walk(f.root,n=>n.type==='select')[0].props['onUpdate:modelValue']('custom');await settle();walk(f.root,n=>n.type==='input'&&n.props.placeholder==='完整地址或 /douban/list/...@@TV')[0].props['onUpdate:modelValue']('/douban/list/tv_american?limit=15@@TV');await settle();walk(f.root,n=>n.type==='button'&&text(n)==='加入来源列表')[0].props.onClick();await settle();
 const source=updates.at(-1).sources[0];assert.equal(source.url,'http://192.168.50.6:1200/douban/list/tv_american?limit=15');assert.equal(source.source_type_hint,'TV');assert.deepEqual(source.destination_templates,{});assert.equal(updates.at(-1).enabled,false);f.app.unmount();
});
test('configuration service failures stay inline without exposing server details',async()=>{
 const f=fixture(Config,{get:async(p,o,c,h)=>{if(p==='download/clients')return {success:false,message:'private server detail',data:null};if(p.endsWith('/configuration'))return c;if(p.endsWith('/diagnostics'))return h;return {items:[],categories:[],result:{}}}});await settle();assert.ok(text(f.root).includes('部分宿主服务或目录暂不可用'));assert.ok(!text(f.root).includes('private server detail'));f.app.unmount();
});


test('episode action carries only the selected unit and current opportunity',async()=>{
 const task={id:3,title:'GATE24',year:'2026',state:'ACTIVE',generation:2};const key='["电视剧","douban","24",1,"",9]';const events=[];
 const client={get:async p=>p==='/tasks'?{items:[task],total:1,next_offset:null}:p==='/tasks/3'?{task,units:{items:[{target_key:key,publish_phase:'NOT_SENT'}],total:1,next_offset:null},opportunities:{items:[{id:'round',state:'ACTIVE'}]}}:{items:[]}};
 const status={current:ref({config:{}}),health:ref({}),error:ref('')};const f=fixture(Subscriptions,{extra:{client,status,onAction:(...a)=>events.push(a)}});await settle();walk(f.root,n=>n.type==='button'&&n.props.class==='sb-subscription-card')[0].props.onClick();await settle();
 walk(f.root,n=>n.type==='button'&&text(n)==='立即检查此集')[0].props.onClick();assert.equal(events[0][0][1],'/tasks/{task_id}/immediate');assert.deepEqual(events[0][1].target_keys,[key]);assert.equal(events[0][1].generation,2);assert.equal(events[0][1].opportunity_id,'round');f.app.unmount();
});

test('retired media library scope can be removed without changing unrelated scopes',async()=>{
 const {default:DeliverySettings}=await import(pathToFileURL(path.join(out,'DeliverySettings.mjs')));const updates=[];
 const modelValue={cloud_scopes:{},libraries:{retired:['old'],active:['keep']},mappings:[],rules:[],policy_bindings:{}};
 const f=fixture(DeliverySettings,{get:async()=>[],extra:{api:{get:async()=>[]},modelValue,policies:{bindings:{}},categories:[],policyNames:[],'onUpdate:modelValue':v=>updates.push(v)}});
 try{await settle();walk(f.root,n=>n.props?.['aria-label']==='移除扫描范围 retired old')[0].props.onClick();assert.deepEqual(updates[0].libraries,{active:['keep']});assert.deepEqual(modelValue.libraries,{retired:['old'],active:['keep']})}finally{f.app.unmount()}
});
test('cleanup confirmation visibly names exact immutable locations and retained scope',async()=>{
 const preview={kind:'cleanup',preview_id:'p',preview_digest:'d',objects:{scope:'monitor',locations:['/local/media/video.mkv'],files:[{file_index:0,relative_path:'video.mkv'}]},permissions:{cleanup_success:true},blockers:[]};
 const f=fixture(Action,{extra:{action:['清理','/delivery/{bundle_id}/cleanup/preview'],context:{_bound:true,bundle_id:'b',revision:1,scope:'monitor',reason:'ADMIN_CLEANUP'},status:{current:ref({revision:4}),health:ref({generation:6}),error:ref('')},client:{post:async()=>preview}}});
 try{await settle();await walk(f.root,n=>n.type==='button'&&text(n)==='核对操作')[0].props.onClick();await settle();const shown=walk(f.root,n=>n.type==='section'&&n.props.class==='sb-confirm-scope')[0];assert.ok(shown);assert.ok(text(shown).includes('/local/media/video.mkv'));assert.ok(text(shown).includes('保留 115 文件与下载器任务'));assert.equal(walk(shown,n=>n.type==='details').length,0)}finally{f.app.unmount()}
});
