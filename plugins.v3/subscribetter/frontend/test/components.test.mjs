import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';import {pathToFileURL,fileURLToPath} from 'node:url';
import {parse,compileScript} from '@vue/compiler-sfc';
import {createRenderer,h,nextTick,ref} from 'vue';
import {contract,initial} from '../src/schema.mjs';
import {useStatus} from '../src/status.mjs';import {saveFollowup,clearFollowup} from '../src/client.mjs';
const source=fileURLToPath(new URL('../src/',import.meta.url)),out=fileURLToPath(new URL('../.test-build/',import.meta.url));fs.mkdirSync(out,{recursive:true});
for(const filename of fs.readdirSync(source).filter(f=>f.endsWith('.vue'))){const {descriptor}=parse(fs.readFileSync(path.join(source,filename),'utf8'),{filename});let code=compileScript(descriptor,{id:filename,inlineTemplate:true}).content;code=code.replace(/import\s+['"]\.\/style\.css['"];?/g,'').replace(/from\s+(['"])(\.\/[^'"]+)\1/g,(_,quote,p)=>'from '+JSON.stringify(p.endsWith('.vue')?'./'+path.basename(p,'.vue')+'.mjs':pathToFileURL(path.join(source,p)).href));fs.writeFileSync(path.join(out,filename.replace('.vue','.mjs')),code)}
const {default:Action}=await import(pathToFileURL(path.join(out,'Action.mjs')));
const {default:Resource}=await import(pathToFileURL(path.join(out,'Resource.mjs')));
const {default:Page}=await import(pathToFileURL(path.join(out,'Page.mjs')));const {default:Config}=await import(pathToFileURL(path.join(out,'ConfigEditor.mjs')));
const {default:Subscriptions}=await import(pathToFileURL(path.join(out,'Subscriptions.mjs')));
const {default:AdoptSubscription}=await import(pathToFileURL(path.join(out,'AdoptSubscription.mjs')));
const {default:DeliverySettings}=await import(pathToFileURL(path.join(out,'DeliverySettings.mjs')));
globalThis.document={activeElement:null};
function element(type){return {type,tagName:type.toUpperCase(),props:{},children:[],style:{},events:{},addEventListener(k,fn){this.events[k]=fn},removeEventListener(k){delete this.events[k]},getAttribute(k){return this.props[k]},get options(){return this.children.filter(n=>n.type==='option')}}}
const renderer=createRenderer({insertStaticContent(value,parent,anchor){const node={type:'#static',text:value,parent};const i=parent.children.indexOf(anchor);if(i<0)parent.children.push(node);else parent.children.splice(i,0,node);return [node,node]},createElement:element,createText:text=>({type:'#text',text}),createComment:text=>({type:'#comment',text}),setText:(n,t)=>n.text=t,setElementText:(n,t)=>n.text=t,patchProp:(n,k,_,v)=>{n.props[k]=v;if(k==='value')n.value=v},insert(n,p,anchor){p.children??=[];if(n.parent)n.parent.children=n.parent.children.filter(x=>x!==n);const i=p.children.indexOf(anchor);if(i<0)p.children.push(n);else p.children.splice(i,0,n);n.parent=p},remove(n){if(n.parent)n.parent.children=n.parent.children.filter(x=>x!==n)},parentNode:n=>n?.parent,nextSibling:n=>n?.parent?.children[n.parent.children.indexOf(n)+1]||null});
const walk=(node,predicate)=>[...(predicate(node)?[node]:[]),...(node.children||[]).flatMap(n=>walk(n,predicate))];const text=node=>[node.text||'',...(node.children||[]).map(text)].join('');
const settle=async()=>{for(let i=0;i<20;i++){await Promise.resolve();await nextTick()}};
test('saved confirmation requires applied readback and disappears when a later refresh fails',async()=>{
 let status,fail=false;const client={pluginId:'status-test',get:async p=>{if(fail)throw {status:503};if(p==='/configuration')return {revision:4,digest:'digest',config:{configuration_receipt:'receipt'}};if(p==='/diagnostics')return {snapshot:{config_revision:4}};return {state:'APPLIED'}}};
 const root={children:[]},app=renderer.createApp({setup(){status=useStatus(client);return()=>h('div')}});app.mount(root);
 try{saveFollowup(client.pluginId,{receipt:'receipt',digest:'other'});await status.refresh();assert.match(status.saveState.value,/尚未确认/);saveFollowup(client.pluginId,{receipt:'receipt',digest:'digest'});await status.refresh();assert.equal(status.saveState.value,'设置已保存并确认生效');fail=true;await status.refresh();assert.equal(status.saveState.value,'');assert.match(status.error.value,/503/)}finally{clearFollowup(client.pluginId);app.unmount()}
});
function fixture(component,{get,post,extra={},seed}={}){clearFollowup('Clone');const calls=[];const config=structuredClone(contract.defaults);const current={revision:4,digest:'a'.repeat(64),config};const health={generation:6,dry_run:true,ordinary_work_active:false,safety_active:true,errors:[],snapshot:{config_revision:4,runtime_generation:6}};seed?.(current,health);const api={get:async(p,o)=>{calls.push(['get',p,o]);if(get)return get(p,o,current,health);if(p.endsWith('/configuration'))return current;if(p.endsWith('/diagnostics'))return health;if(p.endsWith('/configuration/categories'))return {revision:1,categories:[]};return {items:[],total:0,next_offset:null,truncated:false,snapshot:{config_revision:4,runtime_generation:6,high_watermark:'0'}}},post:async(p,b)=>{calls.push(['post',p,b]);return post?post(p,b):{valid:true,errors:[],config:{...config,configuration_receipt:'p'}}}};const saves=[];const root={children:[]};const app=renderer.createApp(component,{api,pluginId:'Clone',sourcePluginId:'SubscriBetter',initialConfig:{...config,password:'SENTINEL'},...extra,onSave:x=>saves.push(x)});app.config.warnHandler=()=>{};app.component('VBtn',{inheritAttrs:false,setup(_,ctx){return()=>h('button',ctx.attrs,ctx.slots.default?.())}});app.component('VDialog',{setup(_,ctx){return()=>h('div',{},ctx.slots.default?.())}});app.mount(root);return {root,app,calls,saves,current,api}}
test('actual Config mount and save omit private initial fields; number controls send numbers; emit once without PUT',async()=>{
 const f=fixture(Config);await settle();assert.equal(f.calls.filter(c=>c[1].endsWith('/configuration')).length,1);
 walk(f.root,n=>n.type==='button'&&text(n)==='升级期限')[0].props.onClick();await settle();
 const lifetime=walk(f.root,n=>n.type==='label'&&text(n).includes('电影追踪期限'))[0];const number=walk(lifetime,n=>n.type==='input'&&n.props.type==='number')[0];assert.ok(number);number.props.onInput({target:{value:'9'}});await settle();
 const save=walk(f.root,n=>n.type==='button'&&text(n)==='保存运行管理设置')[0];assert.ok(save);await save.props.onClick();await settle();assert.equal(f.saves.length,1);assert.ok(!JSON.stringify(f.saves).includes('SENTINEL'));assert.ok(!JSON.stringify(f.calls.filter(c=>c[0]==='post')).includes('SENTINEL'));assert.equal(f.calls.find(c=>c[0]==='post')[2].patch.lifecycle.movie_days,9);assert.ok(f.calls.filter(c=>c[0]==='post').every(c=>c[1].endsWith('/configuration/preview')));await save.props.onClick();assert.equal(f.saves.length,1);f.app.unmount();
});
test('settings switch one level, preserve the abandoned-page draft and do not save it with another section',async()=>{
 const storage=new Map();globalThis.sessionStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
 const f=fixture(Page,{extra:{startSection:'settings'}});await settle();const btn=t=>walk(f.root,n=>n.type==='button'&&text(n)===t)[0];
 try{btn('升级期限').props.onClick();await settle();const lifetime=walk(f.root,n=>n.type==='label'&&text(n).includes('电影追踪期限'))[0];walk(lifetime,n=>n.type==='input'&&n.props.type==='number')[0].props.onInput({target:{value:'9'}});await settle();btn('搜索与调度').props.onClick();await settle();btn('保留草稿并返回').props.onClick();await settle();assert.equal(walk(f.root,n=>n.props?.class==='sb-editor').length,1);const search=walk(f.root,n=>n.type==='label'&&text(n).includes('搜索名称来源'))[0],select=walk(search,n=>n.type==='select')[0];select.props.onChange({target:{value:'original'}});await settle();await btn('保存搜索与调度设置').props.onClick();await settle();assert.notEqual(f.saves[0].lifecycle.movie_days,9);
 }finally{f.app.unmount();delete globalThis.sessionStorage;clearFollowup('Clone')}
});
test('Page offers complete business navigation and keeps diagnostics human and bounded',async()=>{
 const f=fixture(Page);await settle();const btn=t=>walk(f.root,n=>n.type==='button'&&text(n)===t)[0];
 try{for(const title of ['订阅','榜单','上传与入库','质量策略','下载方案','设置'])assert.ok(btn(title),title);btn('设置').props.onClick();await settle();btn('维护与安全').props.onClick();await settle();btn('诊断').props.onClick();await settle();assert.ok(text(f.root).includes('运行状态'));assert.equal(btn('打开高级诊断'),undefined);for(const title of ['候选决策','版本档案','交付队列','策略与预演','服务与健康','榜单发现','解析与 AI','整合迁移','目标与任务'])assert.equal(btn(title),undefined);assert.equal(f.calls.filter(c=>c[0]==='post').length,0);
 }finally{f.app.unmount()}
});
test('delayed Config preflight cannot emit after unmount',async()=>{
 let complete;const f=fixture(Config,{post:()=>new Promise(resolve=>complete=resolve)});await settle();const save=walk(f.root,n=>n.type==='button'&&text(n)==='保存运行管理设置')[0];save.props.onClick();await settle();f.app.unmount();complete({valid:true,errors:[],config:contract.defaults});await settle();assert.equal(f.saves.length,0);
});
test('a stale editor uses the latest baseline and preserves concurrent settings outside its edited section',async()=>{
 const f=fixture(Config);await settle();try{f.current.revision=5;f.current.digest='b'.repeat(64);f.current.config.candidates.site_ids=[42];await walk(f.root,n=>n.type==='button'&&text(n)==='保存运行管理设置')[0].props.onClick();await settle();const request=f.calls.find(c=>c[0]==='post')[2];assert.equal(request.revision,5);assert.deepEqual(request.patch.candidates.site_ids,[42]);}finally{f.app.unmount()}
});

test('actual immutable preview confirmation sends only receipt/opid/confirm and honors blockers',async()=>{
 const calls=[];const preview={preview_id:'p',preview_digest:'d',kind:'history',objects:{record_ids:[1]},permissions:{},blockers:[],revisions:{},expires_at:'later'};
 const client={post:async(p,b)=>{calls.push([p,structuredClone(b)]);return p.endsWith('/preview')?preview:{state:'APPLIED',result:{}}},get:async()=>({})};
 const status={current:ref({revision:4}),health:ref({generation:6}),error:ref('')};
 const context={record_ids:[1],_bound:true,_label:'当前历史记录',_description:'仅处理当前选中的历史记录。'};
 const f=fixture(Action,{extra:{action:['隐藏历史','/discovery/history/cleanup/preview'],client,status,context}});await settle();
 const prepare=walk(f.root,n=>n.type==='button'&&text(n)==='核对操作')[0];await prepare.props.onClick();await settle();
 assert.deepEqual(calls[0][1],{config_revision:4,runtime_generation:6,record_ids:[1]});preview.objects.record_ids.push(2);
 const confirm=walk(f.root,n=>n.type==='button'&&text(n)==='确认隐藏历史')[0];await confirm.props.onClick();await settle();assert.equal(calls.length,2);assert.deepEqual(Object.keys(calls[1][1]).sort(),['confirm','operation_id','preview_digest','preview_id']);assert.equal(calls[1][1].preview_id,'p');f.app.unmount();
 const blocked=fixture(Action,{extra:{action:['隐藏历史','/discovery/history/cleanup/preview'],client:{...client,post:async()=>({...preview,blockers:['UNKNOWN']})},status,context}});await settle();await walk(blocked.root,n=>n.type==='button'&&text(n)==='核对操作')[0].props.onClick();await settle();assert.equal(walk(blocked.root,n=>n.type==='button'&&text(n)==='确认隐藏历史')[0].props.disabled,true);blocked.app.unmount();
});
test('top-level product editors do not show a back button to an empty copy of the same page',async()=>{
 const f=fixture(Page);await settle();
 try{const button=label=>walk(f.root,n=>n.type==='button'&&text(n)===label)[0];button('下载方案').props.onClick();await settle();button('设置').props.onClick();await settle();assert.ok(walk(f.root,n=>n.props?.['aria-label']==='设置分区')[0]);assert.equal(walk(f.root,n=>n.type==='button'&&text(n).startsWith('‹')).length,0)}finally{f.app.unmount()}
});
test('restored top-level editors discard obsolete back buttons from older browser sessions',async()=>{
 const storage=new Map([['subscribetter:navigation:Clone:subscriptions',JSON.stringify({section:'settings',scroll:0,editors:[{focus:{group:'ai',tab:'prompt'},scroll:0}]})]]);globalThis.sessionStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
 const f=fixture(Page);await settle();
 try{assert.equal(walk(f.root,n=>n.type==='button'&&text(n).startsWith('‹')).length,0)}finally{f.app.unmount();delete globalThis.sessionStorage}
});
test('quality policy keeps one visible navigation system',async()=>{
 const f=fixture(Page);await settle();
 try{assert.equal(walk(f.root,n=>String(n.props?.class||'').includes('picker-mobile')).length,0);walk(f.root,n=>n.type==='button'&&text(n)==='质量策略')[0].props.onClick();await settle();assert.equal(walk(f.root,n=>n.props?.class==='sb-policy-selector').length,1);assert.ok(!text(f.root).includes('所有策略的限制'));walk(f.root,n=>n.type==='button'&&text(n)==='全局限制')[0].props.onClick();await settle();assert.ok(text(f.root).includes('以下条件影响所有已绑定分类'))}finally{f.app.unmount()}
});
test('settings use direct module and function navigation without duplicate selectors',async()=>{
 const f=fixture(Page);await settle();
 try{walk(f.root,n=>n.type==='button'&&text(n)==='设置')[0].props.onClick();await settle();walk(f.root,n=>n.type==='button'&&text(n)==='名称识别')[0].props.onClick();await settle();walk(f.root,n=>n.type==='button'&&text(n)==='提示词与版本')[0].props.onClick();await settle();assert.ok(text(f.root).includes('当前提示词'));assert.equal(walk(f.root,n=>String(n.props?.class||'').includes('picker-mobile')).length,0);assert.ok(!text(f.root).includes('当前页面'));assert.ok(!text(f.root).includes('设置模块'));assert.ok(!text(f.root).includes('当前功能'))}finally{f.app.unmount()}
});
test('explicit replacement confirmation shows the selected resource and full affected scope',async()=>{
 const preview={preview_id:'p',preview_digest:'d',kind:'select_candidate',objects:{selection:{title:'GATE24.S01.2160p',affected_targets:['["电视剧","douban","24",1,"",1]','["电视剧","douban","24",1,"",2]'],file_count:1,shared_files:true}},permissions:{},blockers:[],revisions:{},expires_at:'later'};
 const f=fixture(Action,{extra:{action:['选择这个候选资源','/tasks/{task_id}/select-candidate/preview'],client:{post:async()=>preview},status:{current:ref({revision:4}),health:ref({generation:6}),error:ref('')},context:{task_id:3,generation:1,opportunity_id:'round',decision_id:'decision',plan_digest:'a'.repeat(64),target_keys:['["电视剧","douban","24",1,"",1]'],_bound:true,_label:'GATE24'}}});await settle();
 try{await walk(f.root,n=>n.type==='button'&&text(n)==='核对操作')[0].props.onClick();await settle();assert.ok(text(f.root).includes('GATE24.S01.2160p'));assert.ok(text(f.root).includes('第 1 集、第 2 集'));assert.ok(text(f.root).includes('覆盖多集的共享文件'))}finally{f.app.unmount()}
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
 const cards=()=>walk(f.root,n=>n.type==='button'&&String(n.props.class||'').split(' ').includes('sb-work-row'));assert.equal(cards().length,2);
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
 for(const [tab,label] of [['榜单','豆瓣榜单'],['上传与入库','上传与入库'],['质量策略','质量策略'],['设置','运行管理']]){walk(f.root,n=>n.type==='button'&&text(n)===tab)[0].props.onClick();await settle();assert.ok(walk(f.root,n=>n.props?.['aria-label']===label).length,label);assert.equal(walk(f.root,n=>n.props?.['aria-label']==='当前视图资源').length,0)}
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
 walk(f.root,n=>n.type==='button'&&text(n).includes('添加榜单'))[0].props.onClick();await settle();const kind=walk(f.root,n=>n.type==='label'&&text(n).includes('来源方式'))[0];walk(kind,n=>n.type==='select')[0].props['onUpdate:modelValue']('custom');await settle();walk(f.root,n=>n.type==='input'&&n.props.placeholder==='完整地址或 /douban/list/...@@TV')[0].props['onUpdate:modelValue']('/douban/list/tv_american?limit=15@@TV');await settle();walk(f.root,n=>n.type==='button'&&text(n)==='加入来源列表')[0].props.onClick();await settle();
 const source=updates.at(-1).sources[0];assert.equal(source.url,'http://192.168.50.6:1200/douban/list/tv_american?limit=15');assert.equal(source.source_type_hint,'TV');assert.deepEqual(source.destination_templates,{});assert.equal(updates.at(-1).enabled,false);f.app.unmount();
});
test('plan path editor distinguishes multiple unavailable media library mappings',async()=>{
 const mapping={...initial(contract.schemas.Mapping),cloud_scope_id:'shared',emby_service:'Emby'},updates=[];const modelValue={cloud_scopes:{shared:initial(contract.schemas.CloudScope)},mappings:[{...mapping,id:'one',library_id:'1'},{...mapping,id:'two',library_id:'2'}],libraries:{Emby:['1','2']},rules:[],policy_bindings:{}};
 const f=fixture(DeliverySettings,{extra:{modelValue,api:{get:async()=>[]},flow:{step:2,scope:'shared',category:'movie'},readonly:true,'onUpdate:modelValue':value=>updates.push(value)}});await settle();try{const picker=walk(f.root,n=>n.props?.class==='sb-mapping-picker')[0],select=walk(picker,n=>n.type==='select')[0];assert.ok(text(picker).includes('媒体库名称读取中 · 1/2'));assert.ok(text(picker).includes('媒体库名称读取中 · 2/2'));assert.equal(select.props.value,'one');select.props.onChange({target:{value:'two'}});await settle();assert.equal(walk(f.root,n=>n.props?.class==='sb-mapping-picker')[0].children.find(n=>n.type==='select').props.value,'two');assert.equal(updates.length,0)}finally{f.app.unmount()}
});
test('configuration service failures stay inline without exposing server details',async()=>{
 const f=fixture(Config,{get:async(p,o,c,h)=>{if(p==='download/clients')return {success:false,message:'private server detail',data:null};if(p.endsWith('/configuration'))return c;if(p.endsWith('/diagnostics'))return h;return {items:[],categories:[],result:{}}}});await settle();assert.ok(text(f.root).includes('部分宿主服务或目录暂不可用'));assert.ok(!text(f.root).includes('private server detail'));f.app.unmount();
});


test('episode action carries only the selected unit and current opportunity',async()=>{
 const task={id:3,title:'GATE24',year:'2026',state:'ACTIVE',generation:2};const key='["电视剧","douban","24",1,"",9]';const events=[];
 const client={get:async p=>p==='/tasks'?{items:[task],total:1,next_offset:null}:p==='/tasks/3'?{task,units:{items:[{target_key:key,publish_phase:'NOT_SENT'}],total:1,next_offset:null},opportunities:{items:[{id:'round',state:'ACTIVE'}]}}:{items:[]}};
 const status={current:ref({config:{}}),health:ref({}),error:ref('')};const f=fixture(Subscriptions,{extra:{client,status,onAction:(...a)=>events.push(a)}});await settle();walk(f.root,n=>n.type==='button'&&String(n.props.class||'').split(' ').includes('sb-work-row'))[0].props.onClick();await settle();
 walk(f.root,n=>n.type==='button'&&text(n)==='立即检查此集')[0].props.onClick();assert.equal(events[0][0][1],'/tasks/{task_id}/immediate');assert.deepEqual(events[0][1].target_keys,[key]);assert.equal(events[0][1].generation,2);assert.equal(events[0][1].opportunity_id,'round');f.app.unmount();
});

test('work history keeps normal navigation in the selected work and pages only its plans',async()=>{
 const task={id:3,title:'GATE24',state:'ACTIVE',generation:2},calls=[],diagnostics=[];
 const client={get:async(p,o)=>{calls.push([p,o]);return p==='/tasks'?{items:[task],total:1,next_offset:null}:p==='/tasks/3'?{task,units:{items:[],total:0,next_offset:null}}:p.endsWith('/plans')?{items:[],total:7,next_offset:o.params.offset?null:5}:{items:[],total:0,next_offset:null}}};
 const f=fixture(Subscriptions,{extra:{client,status:{current:ref({config:{}}),health:ref({}),error:ref('')},onDiagnostic:(...args)=>diagnostics.push(args)}});await settle();const btn=t=>walk(f.root,n=>n.type==='button'&&text(n)===t)[0];
 try{walk(f.root,n=>n.type==='button'&&String(n.props.class||'').split(' ').includes('sb-work-row'))[0].props.onClick();await settle();btn('处理记录').props.onClick();await settle();await btn('更早计划').props.onClick();await settle();assert.deepEqual(calls.filter(([p])=>p.endsWith('/plans')).at(-1)[1].params,{limit:5,offset:5,sort:'newest'});btn('查看分集版本').props.onClick();await settle();assert.equal(walk(f.root,n=>n.type==='button'&&text(n)==='分集与版本')[0].props['aria-current'],true);btn('处理记录').props.onClick();await settle();btn('查看候选比较').props.onClick();await settle();assert.equal(walk(f.root,n=>n.type==='button'&&text(n)==='候选比较')[0].props['aria-current'],true);assert.equal(diagnostics.length,0);assert.equal(btn('查看全部版本档案'),undefined);}finally{f.app.unmount()}
});

test('resource replacement separates an explicit reviewed candidate from automatic re-search',async()=>{
 const task={id:3,title:'GATE24',media_type:'电视剧',state:'ACTIVE',generation:2};const key='["电视剧","douban","24",1,"",9]',events=[];
 const unit={target_key:key,publish_phase:'NOT_SENT',processing:{candidate_key:'old',phase:'DOWNLOADING',quality:null,files:[],transfer_files:[],started_at:'2026-09-27T00:00:00Z'}};
 const candidate={decision_id:'decision',candidate_key:'new',opportunity_id:'round',plan_digest:'a'.repeat(64),title:'GATE24.S01E09.2160p',affected_targets:[key],file_count:1,shared_files:false,change:{reason:'QUALITY_UPGRADE'}};
 const client={get:async(p)=>p==='/tasks'?{items:[task],total:1,next_offset:null}:p==='/tasks/3'?{task,units:{items:[unit],total:1,next_offset:null},opportunities:{items:[{id:'round',state:'ACTIVE'}]}}:p.includes('/replacement-candidates')?{items:[candidate],total:1,next_offset:null}:{items:[],total:0,next_offset:null}};
 const f=fixture(Subscriptions,{extra:{client,status:{current:ref({config:{}}),health:ref({ordinary_work_active:true}),error:ref('')},onAction:(...args)=>events.push(args)}});await settle();
 try{walk(f.root,n=>n.type==='button'&&String(n.props.class||'').split(' ').includes('sb-work-row'))[0].props.onClick();await settle();await walk(f.root,n=>n.type==='button'&&text(n)==='更换资源')[0].props.onClick();await settle();assert.ok(text(f.root).includes(candidate.title));walk(f.root,n=>n.type==='button'&&text(n).includes(candidate.title))[0].props.onClick();assert.equal(events[0][0][1],'/tasks/{task_id}/select-candidate/preview');assert.equal(events[0][1].decision_id,'decision');assert.deepEqual(events[0][1].target_keys,[key]);
 await walk(f.root,n=>n.type==='button'&&text(n)==='更换资源')[0].props.onClick();await settle();walk(f.root,n=>n.type==='button'&&text(n)==='排除当前资源并自动重搜')[0].props.onClick();assert.equal(events[1][0][1],'/candidates/{candidate_key}/change-source/preview');assert.match(events[1][1]._description,/系统按现有策略重新选择/)}finally{f.app.unmount()}
});

test('resource replacement continues after an empty history page instead of declaring no candidate',async()=>{
 const task={id:3,title:'GATE24',media_type:'电视剧',state:'ACTIVE',generation:2},key='["电视剧","douban","24",1,"",9]',reads=[];
 const unit={target_key:key,publish_phase:'NOT_SENT',processing:{candidate_key:'current',phase:'DOWNLOADING',quality:null,files:[],transfer_files:[],started_at:'2026-09-27T00:00:00Z'}};
 const candidate={decision_id:'decision-b',candidate_key:'alternative',opportunity_id:'round',plan_digest:'b'.repeat(64),title:'替代资源 B',affected_targets:[key],file_count:1,shared_files:false,change:{reason:'QUALITY_UPGRADE'}};
 const client={get:async(p,o)=>{
  if(p==='/tasks')return {items:[task],total:1,next_offset:null};
  if(p==='/tasks/3')return {task,units:{items:[unit],total:1,next_offset:null},opportunities:{items:[{id:'round',state:'ACTIVE'}]}};
  if(p.includes('/replacement-candidates')){reads.push(o.params);return o.params.offset?{items:[candidate],total:1,next_offset:null}:{items:[],total:1,next_offset:100};}
  return {items:[],total:0,next_offset:null};
 }};
 const f=fixture(Subscriptions,{extra:{client,status:{current:ref({config:{}}),health:ref({ordinary_work_active:true}),error:ref('')}}});await settle();
 try{walk(f.root,n=>n.type==='button'&&String(n.props.class||'').split(' ').includes('sb-work-row'))[0].props.onClick();await settle();await walk(f.root,n=>n.type==='button'&&text(n)==='更换资源')[0].props.onClick();await settle();assert.ok(!text(f.root).includes('确实没有其他可选资源'));await walk(f.root,n=>n.type==='button'&&text(n)==='继续查看候选')[0].props.onClick();await settle();assert.ok(text(f.root).includes(candidate.title));assert.deepEqual(reads.map(row=>row.offset),[0,100]);assert.ok(reads.every(row=>row.limit===25&&row.exclude_candidate_key==='current'))}finally{f.app.unmount()}
});

test('opening a work from discovery returns to its source filter and focus context',async()=>{
 const task={id:3,title:'GATE24',media_type:'电视剧',state:'ACTIVE',generation:2};const record={id:7,source_id:'weekly',raw:{title:'GATE24'},membership:'managed',targets:[{task_id:3,season:1}],target_count:1},reads=[];let focused=0;
 const f=fixture(Page,{get:async(p,o,c,h)=>{if(p.endsWith('/configuration')){c.config.discovery.sources=[{id:'weekly',name:'每周榜单',enabled:true,destination_templates:{}}];return c}if(p.endsWith('/diagnostics'))return h;if(p.endsWith('/discovery/sources'))return {items:[{source_id:'weekly',last_state:'OK',config:{name:'每周榜单'}}],total:1,next_offset:null};if(p.endsWith('/discovery/catalog'))return {result:{routes:[]}};if(p.endsWith('/discovery/records')){reads.push(o?.params);return {items:[record],total:1,next_offset:null}}if(p.endsWith('/discovery/statistics'))return {result:{}};if(p.endsWith('/tasks/3'))return {task,units:{items:[],total:0,next_offset:null},opportunities:{items:[]}};if(p.endsWith('/candidate-decisions')||p.endsWith('/plans')||p.endsWith('/observations')||p.endsWith('/exclusions'))return {items:[],total:0,next_offset:null};return {items:[],total:0,next_offset:null,result:{},categories:[]}}});await settle();
 try{walk(f.root,n=>n.type==='button'&&text(n)==='榜单')[0].props.onClick();await settle();const discovery=walk(f.root,n=>n.props?.['aria-label']==='豆瓣榜单')[0],source=walk(discovery,n=>n.type==='select')[0];source.props['onUpdate:modelValue']('weekly');source.props.onChange();await settle();walk(discovery,n=>n.type==='button'&&text(n)==='查看第 1 季')[0].props.onClick({currentTarget:{focus(){focused++}}});await settle();const back=walk(f.root,n=>n.type==='button'&&text(n)==='返回榜单')[0];assert.ok(back);await back.props.onClick();await settle();assert.ok(walk(f.root,n=>n.props?.['aria-label']==='豆瓣榜单')[0]);assert.equal(reads.at(-1).source_id,'weekly');assert.equal(focused,1)}finally{f.app.unmount()}
});

test('opening a work from uploads returns to its stage filter and expanded work',async()=>{
 const task={id:3,title:'GATE24',media_type:'电视剧',state:'ACTIVE',generation:2},work={id:'work-3',data:{title:'GATE24',task_id:3,batch_count:1,confirmed_count:0,attention_count:1}},reads=[];let focused=0;
 const f=fixture(Page,{get:async(p,o,c,h)=>{if(p.endsWith('/configuration'))return c;if(p.endsWith('/diagnostics'))return h;if(p.endsWith('/delivery/works')){reads.push(o?.params);return {items:[work],total:1,next_offset:null}}if(p.endsWith('/delivery/bundles'))return {items:[],total:0,next_offset:null};if(p.endsWith('/tasks/3'))return {task,units:{items:[],total:0,next_offset:null},opportunities:{items:[]}};if(p.endsWith('/candidate-decisions')||p.endsWith('/plans')||p.endsWith('/observations')||p.endsWith('/exclusions'))return {items:[],total:0,next_offset:null};return {items:[],total:0,next_offset:null,result:{},categories:[]}}});await settle();
 try{walk(f.root,n=>n.type==='button'&&text(n)==='上传与入库')[0].props.onClick();await settle();const transfer=walk(f.root,n=>n.props?.['aria-label']==='上传与入库')[0],stage=walk(transfer,n=>n.type==='select')[0];stage.props['onUpdate:modelValue']('UNKNOWN');stage.props.onChange();await settle();walk(transfer,n=>n.type==='button'&&text(n)==='查看批次')[0].props.onClick();await settle();walk(transfer,n=>n.type==='button'&&text(n)==='查看作品与下载进度')[0].props.onClick({currentTarget:{focus(){focused++}}});await settle();const back=walk(f.root,n=>n.type==='button'&&text(n)==='返回上传与入库')[0];assert.ok(back);await back.props.onClick();await settle();assert.ok(text(walk(f.root,n=>n.props?.['aria-label']==='上传与入库')[0]).includes('查看作品与下载进度'));assert.equal(reads.at(-1).state,'UNKNOWN');assert.equal(focused,1)}finally{f.app.unmount()}
});

test('library picker preserves unavailable choices and removes only the explicitly unchecked library',async()=>{
 const {default:LibraryPicker}=await import(pathToFileURL(path.join(out,'LibraryPicker.mjs')));const updates=[];
 const modelValue={retired:['533550'],active:['keep']};let fail=true;
 const f=fixture(LibraryPicker,{extra:{api:{get:async p=>{if(fail)throw Error('private');return p==='mediaserver/clients'?[{name:'retired',type:'emby'},{name:'新增服务',type:'emby'}]:[{id:'533550',name:'测试电影'}]}},modelValue,'onUpdate:modelValue':v=>updates.push(v)}});
 try{await settle();assert.equal(updates.length,0);assert.ok(text(f.root).includes('名称暂不可用'));assert.ok(!text(f.root).includes('533550'));const retry=walk(f.root,n=>n.type==='button'&&text(n)==='重试读取')[0];fail=false;await retry.props.onClick();await settle();assert.ok(text(f.root).includes('测试电影'));assert.ok(text(f.root).includes('新增服务'));assert.ok(!text(f.root).includes('暂时无法读取 Emby 服务'));walk(f.root,n=>n.type==='input'&&n.props.type==='checkbox')[0].props.onChange({target:{checked:false}});assert.deepEqual(updates[0],{active:['keep']});assert.deepEqual(modelValue,{retired:['533550'],active:['keep']})}finally{f.app.unmount()}
});

test('saved source test stays inline and sends only the selected source without an object editor',async()=>{
 const f=fixture(Page,{get:async(p,o,c,h)=>{
  if(p.endsWith('/configuration')){c.config.discovery.sources=[{id:'weekly',name:'每周剧集',url:'https://rss.invalid/douban/list/tv'}];return c;}if(p.endsWith('/diagnostics'))return h;
  if(p.endsWith('/discovery/sources'))return {items:[{source_id:'weekly',last_state:'OK',config:{name:'每周剧集',url:'https://rss.invalid/douban/list/tv'}},{source_id:'removed',config:{name:'已移除'}}],total:2,next_offset:null};
  return {items:[],total:0,next_offset:null,result:{},categories:[]};},post:async()=>({items:12,state:'SUCCESS'})});await settle();
 try{walk(f.root,n=>n.type==='button'&&text(n)==='榜单')[0].props.onClick();await settle();walk(f.root,n=>n.type==='button'&&text(n)==='运行记录')[0].props.onClick();await settle();
  await walk(f.root,n=>n.type==='button'&&text(n)==='试读榜单')[0].props.onClick();await settle();
  const old=walk(f.root,n=>n.type==='button'&&text(n)==='试读榜单')[1];assert.equal(old.props.disabled,true);await old.props.onClick();await settle();const writes=f.calls.filter(c=>c[0]==='post');assert.equal(writes.length,1);assert.deepEqual(writes[0].slice(1),['plugin/Clone/discovery/test',{source_id:'weekly'}]);
  assert.ok(text(f.root).includes('抓取 12 条'));assert.ok(!text(f.root).includes('条目名称（精确配置引用）'));
 }finally{f.app.unmount()}
});

test('opening a subscription uses a full-width detail and returning retains list filters',async()=>{
 const task={id:3,title:'GATE24',state:'ACTIVE',generation:2};const client={get:async p=>p==='/tasks'?{items:[task],total:1,next_offset:null}:p==='/tasks/3'?{task,units:{items:[],total:0,next_offset:null},opportunities:{items:[]}}:{items:[]}};
 const f=fixture(Subscriptions,{extra:{client,status:{current:ref({config:{}}),health:ref({}),error:ref('')}}});await settle();
 try{walk(f.root,n=>n.type==='input'&&n.props.placeholder==='输入作品名称')[0].props['onUpdate:modelValue']('GATE24');
  await walk(f.root,n=>n.type==='button'&&String(n.props.class||'').split(' ').includes('sb-work-row'))[0].props.onClick();await settle();
  const list=walk(f.root,n=>n.props?.['aria-label']==='作品列表')[0];assert.equal(list.style.display,'none');
  walk(f.root,n=>n.type==='button'&&text(n)==='返回列表')[0].props.onClick();await settle();
  assert.notEqual(list.style.display,'none');assert.equal(walk(f.root,n=>n.type==='input'&&n.props.placeholder==='输入作品名称')[0].value,'GATE24');
 }finally{f.app.unmount()}
});

test('runtime settings update typed choices without changing naming settings or cleanup permissions',async()=>{
 const f=fixture(Config);await settle();try{walk(f.root,n=>n.type==='button'&&text(n)==='订阅接管')[0].props.onClick();await settle();const basic=walk(f.root,n=>n.props?.['aria-label']==='基本设置与自动接管')[0];const movie=walk(basic,n=>n.type==='label'&&text(n)==='电影')[0];walk(movie,n=>n.type==='input')[0].props.onChange({target:{checked:true}});await settle();await walk(f.root,n=>n.type==='button'&&text(n)==='保存运行管理设置')[0].props.onClick();await settle();const patch=f.calls.find(c=>c[0]==='post')[2].patch;assert.deepEqual(patch.auto_types,['电影']);assert.deepEqual(patch.meta_protected_names,f.current.config.meta_protected_names);assert.deepEqual(patch.permissions,f.current.config.permissions);assert.equal(patch.dry_run,true)}finally{f.app.unmount()}
});
test('cleanup confirmation visibly names exact immutable locations and retained scope',async()=>{
 const preview={kind:'cleanup',preview_id:'p',preview_digest:'d',objects:{scope:'monitor',locations:['/local/media/video.mkv'],files:[{file_index:0,relative_path:'video.mkv'}]},permissions:{cleanup_success:true},blockers:[]};
 const f=fixture(Action,{extra:{action:['清理','/delivery/{bundle_id}/cleanup/preview'],context:{_bound:true,bundle_id:'b',revision:1,scope:'monitor',reason:'ADMIN_CLEANUP'},status:{current:ref({revision:4}),health:ref({generation:6}),error:ref('')},client:{post:async()=>preview}}});
 try{await settle();await walk(f.root,n=>n.type==='button'&&text(n)==='核对操作')[0].props.onClick();await settle();const shown=walk(f.root,n=>n.type==='section'&&n.props.class==='sb-confirm-scope')[0];assert.ok(shown);assert.ok(text(shown).includes('/local/media/video.mkv'));assert.ok(text(shown).includes('保留 115 文件与下载器任务'));assert.equal(walk(shown,n=>n.type==='details').length,0)}finally{f.app.unmount()}
});

test('duration units change presentation without rounding or converting null to zero',async()=>{
 const {default:Quantity}=await import(pathToFileURL(path.join(out,'Quantity.mjs')));const changes=[];
 const f=fixture(Quantity,{extra:{label:'等待',modelValue:61,'onUpdate:modelValue':v=>changes.push(v)}});await settle();
 try{const input=walk(f.root,n=>n.type==='input')[0],units=walk(f.root,n=>n.type==='select')[0];assert.equal(input.value,61);units.props['onUpdate:modelValue'](60);await settle();assert.equal(changes.length,0);assert.equal(input.value,61/60);input.props.onInput({target:{value:'1.5'}});assert.equal(changes.at(-1),90);input.props.onInput({target:{value:''}});assert.equal(changes.at(-1),null)}finally{f.app.unmount()}
});

test('site choices preserve unavailable IDs and never expose cookie fields',async()=>{
 const {default:SitePicker}=await import(pathToFileURL(path.join(out,'SitePicker.mjs')));const changes=[];const saved=[9,42];
 const f=fixture(SitePicker,{extra:{api:{get:async()=>[{id:9,name:'OurBits',cookie:'COOKIE_SENTINEL'}]},modelValue:saved,'onUpdate:modelValue':v=>changes.push(v)}});await settle();
 try{assert.ok(text(f.root).includes('OurBits'));assert.ok(text(f.root).includes('名称暂不可用'));assert.ok(!text(f.root).includes('COOKIE_SENTINEL'));assert.ok(!text(f.root).includes('42'));walk(f.root,n=>n.type==='input')[0].props.onChange({target:{checked:false}});assert.deepEqual(changes,[[42]]);assert.deepEqual(saved,[9,42])}finally{f.app.unmount()}
});

test('AI credentials enter the draft by reference while partial failure preserves existing keys',async()=>{
 const {default:AISettings}=await import(pathToFileURL(path.join(out,'AISettings.mjs')));const changes=[],writes=[];const old='secret:'+'b'.repeat(32),fresh='secret:'+'a'.repeat(32);
 const modelValue={...contract.defaults.ai_assist,credential_refs:[old]};const f=fixture(AISettings,{extra:{modelValue,base:{revision:4,digest:'d'},client:{post:async(p,b)=>{writes.push([p,b]);if(b.kind==='key')throw {status:503};return {reference:fresh}}},'onUpdate:modelValue':v=>changes.push(v)}});await settle();
 try{const inputs=walk(f.root,n=>n.type==='input'&&n.props.type==='password');inputs[0].props['onUpdate:modelValue']('https://ai.test/v1');inputs[1].props['onUpdate:modelValue']('KEY_SENTINEL');await settle();await walk(f.root,n=>n.type==='button'&&text(n)==='写入连接信息')[0].props.onClick();await settle();assert.equal(writes.length,2);assert.equal(writes[0][1].revision,4);assert.equal(changes.at(-1).endpoint_ref,fresh);assert.deepEqual(changes.at(-1).credential_refs,[old]);assert.ok(!JSON.stringify(changes).includes('KEY_SENTINEL'));assert.equal(inputs[1].value,'');assert.ok(text(f.root).includes('已成功写入的部分保留'))}finally{f.app.unmount()}
});

test('mapping checks send the selected persisted sample and refuse an unsaved mapping',async()=>{
 const {default:MappingCheck}=await import(pathToFileURL(path.join(out,'SavedMappingCheck.mjs')));const writes=[];
 const f=fixture(MappingCheck,{extra:{saved:true,mapping:{id:'m',emby_service:'Emby',library_id:'library'},status:{current:ref({revision:4}),health:ref({generation:6}),error:ref('')},client:{get:async p=>p.startsWith('/health/')?{items:[{id:'scan',state:'COMPLETE',data:{service:'Emby',library:'library',data:{started_at:'2026-09-26T08:00:00Z'}}}],next_offset:null}:{items:[{id:'item',data:{name:'测试媒体'}}],next_offset:null},post:async(p,b)=>{writes.push([p,b]);return {state:'MAPPING_VERIFIED',result:{locations:[]}}}}}});await settle();
 try{await walk(f.root,n=>n.type==='button'&&text(n)==='读取此媒体库的扫描样本')[0].props.onClick();await settle();assert.ok(text(f.root).includes('2026-09-26T08:00:00Z'));const scans=walk(f.root,n=>n.type==='select')[0];scans.props['onUpdate:modelValue']('scan');scans.props.onChange();await settle();walk(f.root,n=>n.type==='select')[1].props['onUpdate:modelValue']('item');await settle();await walk(f.root,n=>n.type==='button'&&text(n)==='检查播放路径')[0].props.onClick();assert.deepEqual(writes,[['/archive/mapping-test',{config_revision:4,runtime_generation:6,scan_id:'scan',item_id:'item',mapping_id:'m'}]])}finally{f.app.unmount()}
 const unavailable=fixture(MappingCheck,{extra:{saved:false,mapping:{id:'m'},status:{error:ref('')},client:{get:async()=>assert.fail('unsaved check')}}});await settle();try{assert.equal(walk(unavailable.root,n=>n.type==='button'&&text(n)==='读取此媒体库的扫描样本')[0].props.disabled,true)}finally{unavailable.app.unmount()}
});


test('candidate comparison uses actual SQLite list DTO and lazy detail; errors, empty and late responses are distinct',async()=>{
 const {spawnSync}=await import('node:child_process');
 const cwd=fileURLToPath(new URL('../../../../',import.meta.url));
 const run=spawnSync(path.join(cwd,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python'),['-X','utf8','tests/v3/subscribetter/test_management_display.py','--fixture'],{cwd,encoding:'utf8',maxBuffer:32*1024*1024});
 assert.equal(run.status,0,run.error?.message||run.stderr);const dto=JSON.parse(run.stdout);assert.equal(dto.list.items[0].evidence.evaluation,undefined);
 const {default:UnitProgress}=await import(pathToFileURL(path.join(out,'UnitProgress.mjs')));
 for(const [scene,expected] of [['rapid','2 / 6'],['downloading','512.0 MiB'],['assets','视频或必要字幕'],['unknown','外部结果尚未确认'],['superseded','已失效或待核实']]){
  const f=fixture(UnitProgress,{extra:{unit:dto.scenes[scene].units.items[0],health:{ordinary_work_active:true}}});await settle();try{assert.ok(text(f.root).includes(expected),scene+': '+text(f.root));assert.ok(!text(f.root).includes('视频 · 等待接管'));if(scene==='downloading')assert.ok(!text(f.root).includes('等待下载器更新文件进度'));if(scene==='rapid'){assert.deepEqual(walk(f.root,n=>n.type==='mark').map(text),['4K','Dolby Vision','DDP']);assert.equal(walk(f.root,n=>n.props?.['aria-expanded']!==undefined).length,0);assert.equal(walk(f.root,n=>n.props?.class==='sb-episode-expanded').length,0)}}finally{f.app.unmount()}
 }
 const multi=structuredClone(dto.scenes.rapid.units.items[0]);
 multi.current_quality.push({...multi.current_quality[0],version_id:'lossless',quality:{...multi.current_quality[0].quality,audio:3}});
 multi.processing.change.versions.push({version_id:'lossless',changes:[{dimension:'resolution',order:1},{dimension:'picture',order:1},{dimension:'audio',order:-1}]});
 for(const baseline_current of [true,false]){
  multi.processing.change.baseline_current=baseline_current;
  const f=fixture(UnitProgress,{extra:{unit:multi,health:{ordinary_work_active:true}}});await settle();
  try{assert.deepEqual(walk(f.root,n=>n.type==='mark').map(text),baseline_current?['4K','Dolby Vision','DDP','4K','Dolby Vision']:[]);if(!baseline_current)assert.match(text(f.root),/变化依据未取得/)}finally{f.app.unmount()}
 }
 const {default:CandidateDecision}=await import(pathToFileURL(path.join(out,'CandidateDecision.mjs')));
 let resolve,mode='wait';const calls=[];const decision=ref(dto.list.items[0]);
 const client={get:(p)=>{calls.push(p);if(mode==='fail')return Promise.reject({status:503});if(mode==='empty')return Promise.resolve({id:decision.value.id,evidence:{}});return new Promise(r=>resolve=r)}};
 const root={children:[]},app=renderer.createApp({setup:()=>()=>h(CandidateDecision,{decision:decision.value,client})});app.component('VBtn',{setup(_,ctx){return()=>h('button',ctx.attrs,ctx.slots.default?.())}});app.mount(root);
 const button=label=>walk(root,n=>n.type==='button'&&text(n)===label)[0];
 try{
  assert.match(text(root),/候选版本质量更优/);assert.match(text(root),/分辨率/);assert.equal(calls.length,0);
  button('查看本次比较依据').props.onClick();await settle();assert.match(text(root),/正在读取完整比较依据/);
  const detail=structuredClone(dto.detail);for(const episode of [10,2,1])detail.evidence.evaluation.decisions[JSON.stringify(['电视剧','tmdb','numeric',1,'',episode])]={status:'ALLOW',reason:'QUALITY_UPGRADE'};
  resolve(detail);await settle();assert.ok(!text(root).includes('QUALITY_UPGRADE'));assert.ok(text(root).includes('候选版本质量更优'));
  const outcomes=walk(root,n=>n.props?.class==='sb-comparison-outcome').map(text);const displayed=outcomes.join('|');assert.equal(outcomes.length,3,displayed);assert.match(displayed,/第 1–2、10 集/);
  decision.value={...dto.list.items[0],id:'second'};await settle();button('查看本次比较依据').props.onClick();await settle();const late=resolve;
  decision.value={...dto.list.items[0],id:'third'};await settle();late(dto.detail);await settle();assert.ok(!text(root).includes('QUALITY_UPGRADE'));
  mode='fail';button('查看本次比较依据').props.onClick();await settle();assert.match(text(root),/比较详情暂时无法加载/);assert.ok(!text(root).includes('HTTP 503'));assert.ok(!text(root).includes('没有保存完整比较依据'));
  mode='empty';await button('重新加载').props.onClick();await settle();assert.match(text(root),/没有保存完整比较依据/);
 }finally{app.unmount()}
});


test('blank configuration creates a linked plan, completes its steps and retains the draft after rejected save',async()=>{
 const f=fixture(Config,{get:async(p,o,c,h)=>{
  if(p.endsWith('/configuration'))return c;if(p.endsWith('/diagnostics'))return h;
  if(p.endsWith('/configuration/categories'))return {revision:1,categories:[{id:'tv',name:'剧集',media_type:'电视剧',enabled:true}]};
  if(p.endsWith('/policies/catalog'))return {result:{default_templates:{欧美剧:{}}}};
  if(p==='download/clients')return [{name:'qbt',type:'qbittorrent'}];if(p==='download/paths')return [];
  if(p==='plugin/')return [{id:'CloudDriveDisk'},{id:'P115Disk'}];if(p==='mediaserver/clients')return [{name:'Emby',type:'emby'}];if(p==='mediaserver/library')return [{id:'L',name:'剧集库'}];return {items:[],result:{}};
 },extra:{focus:{group:'plans'}},post:async()=>({valid:false,errors:['测试：服务暂不可用，未保存']})});await settle();
 const btn=(root,label)=>walk(root,n=>n.type==='button'&&text(n)===label)[0];
 try{
  const root=walk(f.root,n=>n.props?.['aria-label']==='按方案连续配置')[0];
  function visible(n){for(let p=n;p;p=p.parent)if(p.props?.hidden||p.style?.display==='none')return false;return true}
  async function field(label,value,type='input'){const parent=walk(root,n=>n.type==='label'&&text(n).startsWith(label)&&visible(n))[0];assert.ok(parent,label);const el=walk(parent,n=>n.type===type)[0];assert.ok(el,label+' '+type);if(el.props.onInput)el.props.onInput({target:{value}});else if(el.props.onChange)el.props.onChange({target:{value}});else el.props['onUpdate:modelValue'](value);await settle()}
  assert.equal(walk(root,n=>n.props?.class==='sb-plan-workspace').length,1);await field('方案名称','首次剧集方案');walk(root,n=>n.type==='form')[0].props.onSubmit({preventDefault(){}});await settle();
  await field('MoviePilot 分类','tv','select');await field('收录与升级策略','欧美剧','select');btn(root,'继续').props.onClick();await settle();await field('下载器','qbt','select');await field('下载保存到','/downloads');
  btn(root,'继续').props.onClick();await settle();
  await field('CD2 插件实例','CloudDriveDisk','select');await field('115 插件实例','P115Disk','select');await field('云盘根目录','/115');await field('允许写入的路径','/115/test','textarea');
  for(const [label,path] of [['从哪里上传','/organized'],['115 暂存','/115/test/staging'],['Symedia 接收','/115/test/incoming']])await field(label,path);await field('Symedia 监控目录','/115/test/incoming','textarea');
  btn(root,'继续').props.onClick();await settle();await field('Emby 服务','Emby','select');await field('媒体库','L','select');
  for(const [label,path] of [['Emby 中的 STRM','/strm'],['MP 可以读取','/strm'],['STRM 内容','/play'],['CD2 内部','/115']])await field(label,path);
  await field('在库版本的比较策略','欧美剧','select');
  btn(root,'继续').props.onClick();await settle();
  await btn(root,'保存方案').props.onClick();await settle();
  const request=f.calls.filter(c=>c[0]==='post').at(-1);assert.ok(request,'valid local form reaches backend: '+text(f.root));assert.ok(request[1].endsWith('/configuration/preview'));
  const draft=request[2].patch,t=draft.destination_templates[0],rule=draft.delivery.rules[0],mapping=draft.delivery.mappings[0];assert.equal(t.organized_rule,rule.id);assert.equal(mapping.cloud_scope_id,rule.cloud_scope_id);assert.deepEqual(draft.delivery.libraries,{Emby:['L']});assert.ok(draft.delivery.cloud_scopes[rule.cloud_scope_id]);assert.equal(draft.enabled,false);assert.equal(draft.dry_run,true);
  assert.match(text(root),/服务暂不可用/);assert.equal(f.saves.length,0);
  await btn(root,'保存方案').props.onClick();await settle();assert.equal(f.saves.length,0);assert.deepEqual(f.calls.filter(c=>c[0]==='post').at(-1)[2].patch,draft);
  btn(root,'上一步').props.onClick();await settle();btn(root,'上一步').props.onClick();await settle();assert.ok(text(root).includes('从哪里上传'));assert.equal(walk(root,n=>n.type==='input'&&n.props.value==='/115/test/staging').length,1);
 }finally{f.app.unmount()}
});
test('native save with lost reply locks duplicate submission until exact applied readback',async()=>{
 const f=fixture(Config,{get:async(p,o,c,h)=>{if(p.endsWith('/configuration'))return c;if(p.endsWith('/diagnostics'))return {...h,snapshot:{config_revision:c.revision}};if(p.includes('/migration/receipts/'))return {state:'APPLIED'};return {categories:[],items:[],result:{}}},post:async(p,b)=>({valid:true,digest:'b'.repeat(64),config:{...b.patch,configuration_receipt:'native-receipt'}})});
 let writes=0;f.api.put=async(p,config)=>{writes++;assert.equal(p,'plugin/Clone');f.current.config=config;f.current.revision++;f.current.digest='b'.repeat(64);throw {status:503}};
 await settle();try{const button=label=>walk(f.root,n=>n.type==='button'&&text(n)===label)[0];await button('保存运行管理设置').props.onClick();await settle();assert.equal(writes,1);assert.equal(button('保存运行管理设置'),undefined);await walk(f.root,n=>n.type==='button'&&text(n).startsWith('‹'))[0].props.onClick();assert.match(text(f.root),/先核对本次保存结果/);assert.equal(writes,1);await button('核对保存结果').props.onClick();await settle();assert.match(text(f.root),/设置已保存并确认生效/);assert.equal(writes,1);assert.equal(f.saves.length,0);const toggle=walk(f.root,n=>n.type==='input'&&n.props.type==='checkbox')[0];toggle.props.onChange({target:{checked:!toggle.props.checked}});await settle();assert.ok(!text(f.root).includes('设置已保存并确认生效'))}finally{f.app.unmount()}
});

test('draft path checks use the selected library while disabled, and discard stale successes',async()=>{
 const {default:MappingCheck}=await import(pathToFileURL(path.join(out,'MappingCheck.mjs'))),calls=[],mapping=ref({id:'draft',emby_service:'Emby',library_id:'tv',local_strm_prefix:'/strm',playback_prefix:'/cloud'});
 let late;const client={post:async(p,b)=>{calls.push([p,b]);return p.endsWith('library-samples')?{result:{items:[{id:'sample',name:'真实作品',episode:7}],next_offset:null}}:new Promise(r=>late=r)}};
 const root={children:[]},status={current:ref({revision:4}),health:ref({generation:6,dry_run:true}),error:ref('')};const app=renderer.createApp({setup:()=>()=>h(MappingCheck,{mapping:mapping.value,client,status,saved:false})});app.component('VBtn',{setup(_,ctx){return()=>h('button',ctx.attrs,ctx.slots.default?.())}});app.mount(root);await settle();
 const btn=label=>walk(root,n=>n.type==='button'&&text(n)===label)[0];
 try{await btn('选择样本').props.onClick();await settle();assert.deepEqual(calls[0],['/configuration/library-samples',{config_revision:4,runtime_generation:6,service:'Emby',library:'tv',offset:0,limit:25}]);walk(root,n=>n.type==='select')[0].props['onUpdate:modelValue']('sample');await settle();btn('检查当前路径').props.onClick();await settle();assert.equal(calls[1][1].mapping.local_strm_prefix,'/strm');mapping.value={...mapping.value,playback_prefix:'/new'};await settle();late({result:{state:'DRAFT_MAPPING_VERIFIED',locations:[]}});await settle();assert.ok(!text(root).includes('路径转换通过'));assert.ok(!text(root).includes('启用订阅追踪'));assert.equal(status.health.value.dry_run,true);assert.equal(btn('检查当前路径').props.disabled,false);}finally{app.unmount()}
});

test('version display renders all declared gains and losses, with no inferred purple marks',async()=>{
 const {default:VersionDifference}=await import(pathToFileURL(path.join(out,'VersionDifference.mjs')));
 const base={current:{resolution:1080,picture:0,audio:3,basis:{picture:'measured'}},target:{resolution:2160,picture:2,audio:1},change:{kind:'quality',changes:[{dimension:'resolution',order:1},{dimension:'picture',order:1},{dimension:'audio',order:-1}]}};
 for(const change of [base.change,{kind:'quality',changes:[]},null]){
  const f=fixture(VersionDifference,{extra:{...base,change}});await settle();
  try{assert.deepEqual(walk(f.root,n=>n.type==='mark').map(text),change===base.change?['4K','Dolby Vision']:[]);if(change===base.change){assert.ok(text(f.root).includes('无损音频'));assert.ok(text(f.root).includes('偏好降低'));}}finally{f.app.unmount()}
 }
});


test('renaming a scheme changes only its display name and preserves all references',async()=>{
 const {default:Destinations}=await import(pathToFileURL(path.join(out,'Destinations.mjs'))),updates=[];
 const original={id:'stable',display_name:'旧名称',category_id:'tv',downloader:'qbt',save_path:'/downloads',organized_rule:'rule',sites:[7],custom_words:['word']};
 const f=fixture(Destinations,{extra:{modelValue:[original],embedded:true,'onUpdate:modelValue':v=>updates.push(v)}});await settle();
 try{const label=walk(f.root,n=>n.type==='label'&&text(n)==='方案名称')[0],input=walk(label,n=>n.type==='input')[0];assert.equal(input.props.disabled,false);input.props.onInput({target:{value:'新名称'}});assert.deepEqual(updates[0],[{...original,display_name:'新名称'}]);}finally{f.app.unmount()}
});

test('migration choices bind exact current digests without editable IDs or implicit activation',async()=>{
 const {default:Migration}=await import(pathToFileURL(path.join(out,'Migration.mjs'))),calls=[],old={instance_id:'old-douban',source:'DoubanRankPlusOptimized',version:'1.0.7',active:true,config_digest:'d'.repeat(64)};
 const config=structuredClone(contract.defaults);config.discovery.sources=[{id:'weekly',name:'每周口碑',enabled:false}];
 const client={pluginId:'MigrationTest',get:async()=>({plugins:[old]}),post:async(p,b)=>{calls.push([p,b]);return p==='/configuration/preview'?{valid:true,receipt_id:'config',feature_digests:{discovery:'f'.repeat(64)}}:{receipt_id:'migration',kind:'CUTOVER',state:'PREVIEW',next_changes:[],steps:[]}}};
 const f=fixture(Migration,{extra:{client,status:{current:ref({revision:4,digest:'a'.repeat(64),config}),health:ref({}),error:ref('')},nativeApi:{put:()=>assert.fail('no native save during preview')}}});await settle();
 try{const btn=t=>walk(f.root,n=>n.type==='button'&&text(n)===t)[0];btn('切换功能').props.onClick();await settle();await btn('读取当前插件').props.onClick();await settle();const oldLabel=walk(f.root,n=>n.type==='label'&&text(n).includes('old-douban'))[0];walk(oldLabel,n=>n.type==='input')[0].props.onChange({target:{checked:true}});const featureLabel=walk(f.root,n=>n.type==='label'&&text(n).includes('每周口碑'))[0];walk(featureLabel,n=>n.type==='input')[0].props['onUpdate:modelValue'](['discovery:weekly']);await settle();await btn('预览切换范围').props.onClick();assert.deepEqual(calls.map(c=>c[0]),['/configuration/preview','/migration/cutover/preview']);assert.deepEqual(calls[1][1].selected,[{instance_id:'old-douban',config_digest:old.config_digest,module:'discovery',whole_instance:true,all_capabilities:['discovery']}]);assert.equal(config.enabled,false);assert.ok(text(f.root).includes('预览待确认'));}finally{f.app.unmount()}
});


test('nearby reconcile is one bounded request and keeps an unknown result unknown',async()=>{
 const {default:ReconcileButton}=await import(pathToFileURL(path.join(out,'ReconcileButton.mjs'))),calls=[],action={component:'delivery',object_id:'b',revision:'7'};
 const f=fixture(ReconcileButton,{extra:{action,unknown:true,client:{post:async(p,b)=>{calls.push([p,b]);return {state:'UNKNOWN',result:{checked_at:'2026-09-27T00:00:00Z'}}}},status:{current:ref({revision:4}),health:ref({generation:6}),error:ref('')}}});await settle();
 try{await walk(f.root,n=>n.type==='button'&&text(n)==='核对结果')[0].props.onClick();await settle();assert.deepEqual(calls,[['/health/reconcile',{...action,config_revision:4,runtime_generation:6}]]);assert.match(text(f.root),/外部结果仍待确认/);assert.ok(!text(f.root).includes('上传成功'));}finally{f.app.unmount()}
});

test('quick subscription filters query the server, keep page boundaries, and show each returned episode fact',async()=>{
 const task={id:1,title:'GATE24',state:'ACTIVE',progress:{targets:30,present:7,processing:27,stage_sample_count:25,highlights:[{target_key:'["电视剧","tmdb","42",1,"",5]',phase:'UNKNOWN'},{target_key:'["电视剧","tmdb","42",1,"",1]',phase:'DOWNLOADING'}]}};
 const reads=[],configured=[];const client={get:async(p,o)=>{reads.push([p,o?.params]);return {items:[task],total:30,next_offset:25}}};
 const f=fixture(Subscriptions,{extra:{client,status:{current:ref({config:{}}),health:ref({ordinary_work_active:true}),error:ref('')},onConfigure:k=>configured.push(k)}});await settle();
 try{const button=t=>walk(f.root,n=>n.type==='button'&&text(n)===t)[0];await button('下一页').props.onClick();await settle();await button('已暂停').props.onClick();await settle();assert.equal(reads.at(-1)[1].state,'PAUSED');assert.equal(reads.at(-1)[1].offset,0);assert.equal(reads.at(-1)[1].limit,25);assert.equal(walk(f.root,n=>n.props?.class==='sb-task-highlight').length,2);assert.match(text(f.root),/已读取 25 \/ 27/);assert.match(text(f.root),/总集数待确认/);button('下载与入库方案').props.onClick();assert.deepEqual(configured,['plans']);}finally{f.app.unmount()}
});

test('nested policy save survives outer discard, keeps scheme inputs, and never submits them with the child',async()=>{
 const storage=new Map();globalThis.sessionStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
 const rule={resolutions:[2160,1080],group:'official',source:'movie',dimensions:['resolution','audio']};
 const f=fixture(Page,{get:async(p,o,c,h)=>{
  if(p.endsWith('/configuration'))return structuredClone(c);if(p.endsWith('/diagnostics'))return {...h,snapshot:{config_revision:c.revision}};
  if(p.endsWith('/configuration/categories'))return {revision:1,categories:[{id:'tv',name:'欧美剧',enabled:true}]};
  if(p.endsWith('/policies/catalog'))return {result:{default_templates:{欧美剧:rule}}};if(p.includes('/migration/receipts/'))return {state:'APPLIED'};
  if(p==='download/clients')return [{name:'qbt'}];if(p==='download/paths')return [];return {items:[],categories:[],result:{}};
 },post:async(p,b)=>p.endsWith('/configuration/preview')?{valid:true,digest:'b'.repeat(64),config:{...b.patch,configuration_receipt:'nested-save'}}:{result:{summary:{}}}});
 f.current.config.policy.bindings={tv:'欧美剧'};f.current.config.destination_templates=[{id:'plan',display_name:'剧集方案',category_id:'tv',downloader:'qbt',save_path:'/old',organized_rule:null,sites:[],custom_words:[]}];
 let writes=0;f.api.put=async(p,c)=>{writes++;f.current.config=structuredClone(c);f.current.revision++;f.current.digest='b'.repeat(64);return {success:true}};
  const btn=(label,root=f.root)=>walk(root,n=>n.type==='button'&&text(n)===label)[0],tab=label=>walk(f.root,n=>n.type==='button'&&n.props?.['aria-label']===label)[0];
   try{await settle();btn('下载方案').props.onClick();await settle();assert.equal(walk(f.root,n=>n.props?.class==='sb-plan-workspace').length,1);assert.equal(walk(f.root,n=>n.props?.class==='sb-plan-library').length,0);tab('下载').props.onClick();await settle();const input=walk(f.root,n=>n.type==='label'&&text(n).startsWith('下载保存到'))[0].children.find(n=>n.type==='input');input.props.onInput({target:{value:'/unsaved'}});await settle();tab('概览').props.onClick();await settle();btn('调整所选策略').props.onClick();await settle();
 const editors=()=>walk(f.root,n=>n.type==='section'&&n.props?.class==='sb-editor');assert.equal(editors().length,2);
  const child=editors().at(-1);walk(child,n=>n.props?.['aria-label']==='上移音轨')[0].props.onClick();await settle();await btn('保存质量策略设置',child).props.onClick();await settle();assert.equal(writes,1);assert.equal(f.current.config.destination_templates[0].save_path,'/old');assert.deepEqual(f.current.config.policy.templates['欧美剧'].dimensions,['audio','resolution']);
  walk(child,n=>n.type==='button'&&text(n).startsWith('‹'))[0].props.onClick();await settle();assert.equal(editors().length,1);tab('下载').props.onClick();await settle();assert.ok(walk(f.root,n=>n.type==='input'&&n.props.value==='/unsaved').length);
 btn('订阅').props.onClick();await settle();assert.match(text(f.root),/本次已保存的欧美剧仍然生效/);btn('放弃本页修改').props.onClick();await settle();assert.equal(editors().length,0);assert.equal(writes,1);assert.equal(f.current.config.destination_templates[0].save_path,'/old');assert.deepEqual(f.current.config.policy.templates['欧美剧'].dimensions,['audio','resolution']);
 }finally{f.app.unmount();delete globalThis.sessionStorage;clearFollowup('Clone')}
});

test('Page keeps a readable shell when initial status fails and retries in place',async()=>{
 let fail=true;
 const f=fixture(Page,{get:async(p,o,c,h)=>{if(p.endsWith('/configuration')||p.endsWith('/diagnostics')){if(fail)throw {status:503};return p.endsWith('/configuration')?c:h}return {items:[],total:0,next_offset:null}}});await settle();
 try{assert.ok(text(f.root).includes('插件状态暂时无法读取'));const retry=walk(f.root,n=>n.type==='button'&&text(n)==='重新读取')[0];assert.ok(retry);fail=false;await retry.props.onClick();await settle();assert.ok(walk(f.root,n=>n.props?.['aria-label']==='订阅作品')[0])}finally{f.app.unmount()}
});

test('status retry in settings preserves unsaved input and does not become discard-and-reload',async()=>{
 let fail=false;
 const f=fixture(Config,{get:async(p,o,c,h)=>{if((p.endsWith('/configuration')||p.endsWith('/diagnostics'))&&fail)throw {status:503};if(p.endsWith('/configuration'))return c;if(p.endsWith('/diagnostics'))return h;if(p.endsWith('/configuration/categories'))return {revision:1,categories:[]};return {items:[],result:{}}},extra:{focus:{group:'safety',tab:'cleanup'}}});await settle();
 try{const checkbox=walk(f.root,n=>n.type==='input'&&n.props.type==='checkbox')[0],original=checkbox.props.checked;checkbox.props.onChange({target:{checked:!original}});await settle();walk(f.root,n=>n.type==='button'&&text(n)==='诊断')[0].props.onClick();await settle();fail=true;await walk(f.root,n=>n.type==='button'&&text(n)==='刷新运行状态')[0].props.onClick();await settle();fail=false;await walk(f.root,n=>n.type==='button'&&text(n)==='重新读取状态')[0].props.onClick();await settle();walk(f.root,n=>n.type==='button'&&text(n)==='清理权限')[0].props.onClick();await settle();assert.equal(walk(f.root,n=>n.type==='input'&&n.props.type==='checkbox')[0].props.checked,!original);assert.ok(text(f.root).includes('修改尚未保存'))}finally{f.app.unmount()}
});

test('subscription detail survives auxiliary history failures and reports each unavailable panel',async()=>{
 const task={id:1,title:'侠女内莉',year:'2026',media_type:'电视剧',season:1,state:'ACTIVE'};
 const client={get:async path=>{if(path==='/tasks')return {items:[task],total:1,next_offset:null};if(path==='/tasks/1')return {task,units:{items:[],total:0},snapshot:{}};throw {status:503}}};
 const f=fixture(Subscriptions,{extra:{client,status:{current:ref({config:{}}),health:ref({}),error:ref('')}}});await settle();
 try{walk(f.root,n=>n.type==='button'&&String(n.props.class||'').includes('sb-work-row'))[0].props.onClick();await settle();assert.ok(text(f.root).includes('侠女内莉'));assert.ok(text(f.root).includes('分集与版本'));walk(f.root,n=>n.type==='button'&&text(n)==='候选比较')[0].props.onClick();await settle();assert.ok(text(f.root).includes('候选比较暂时无法读取'))}finally{f.app.unmount()}
});

test('subscription polling keeps the applied query until the user submits the draft',async()=>{
 const calls=[];let component;const client={get:async(p,o={})=>{if(p==='/tasks'){calls.push(o.params?.query??'');return {items:[],total:0,next_offset:null}}return {items:[],total:0,next_offset:null}}};
 const root={children:[]},app=renderer.createApp({setup(){component=ref(null);return()=>h(Subscriptions,{ref:component,api:{},client,status:{current:ref({config:{}}),health:ref({}),error:ref('')}})}});app.component('VBtn',{inheritAttrs:false,setup(_,ctx){return()=>h('button',ctx.attrs,ctx.slots.default?.())}});app.component('VDialog',{setup(_,ctx){return()=>h('div',{},ctx.slots.default?.())}});app.mount(root);await settle();
 try{walk(root,n=>n.type==='input'&&n.props.type==='search')[0].props['onUpdate:modelValue']('GATE24');await component.value.load();await settle();assert.equal(calls.at(-1),'');walk(root,n=>n.type==='form')[0].props.onSubmit({preventDefault(){}});await settle();assert.equal(calls.at(-1),'GATE24')}finally{app.unmount()}
});

test('an unbound policy cannot masquerade as the previously selected category effective policy',async()=>{
 const {default:Policies}=await import(pathToFileURL(path.join(out,'Policies.mjs')));const model={...structuredClone(contract.defaults.policy),bindings:{tv:'策略 A'},templates:{'策略 A':{resolutions:[2160,1080],group:'any',source:'any',dimensions:['resolution']},'策略 B':{resolutions:[1080],group:'any',source:'any',dimensions:['audio']}}};
 const f=fixture(Policies,{extra:{modelValue:model,defaults:{},categories:[{id:'tv',name:'电视剧',enabled:true}],focus:{policy:'策略 A',category:'tv',tab:'trial'},client:{get:async()=>({items:[],total:0,next_offset:null})},status:{current:ref({revision:4}),health:ref({generation:6})}}});await settle();
 try{walk(f.root,n=>n.type==='button'&&text(n).includes('策略 B'))[0].props.onClick();await settle();assert.ok(text(f.root).includes('请选择试算分类'));const picker=walk(f.root,n=>n.props?.label==='试算分类')[0];picker.props['onUpdate:modelValue']('tv');await settle();assert.ok(text(f.root).includes('此分类当前生效的是“策略 A”'))}finally{f.app.unmount()}
});

test('policy editor names the full save scope and lists the changed policy object',async()=>{
 const template={resolutions:[2160,1080],group:'any',source:'any',dimensions:['resolution','audio']};const f=fixture(Config,{extra:{focus:{group:'policy',policy:'欧美剧',category:'tv',root:true}},seed:current=>{current.config.policy={...current.config.policy,bindings:{tv:'欧美剧'},templates:{欧美剧:template}}},get:async(p,o,c,h)=>{if(p.endsWith('/configuration'))return c;if(p.endsWith('/diagnostics'))return h;if(p.endsWith('/configuration/categories'))return {revision:1,categories:[{id:'tv',name:'电视剧',enabled:true}]};if(p.endsWith('/policies/catalog'))return {result:{default_templates:{}}};return {items:[],result:{}}}});await settle();
 try{const remove=walk(f.root,n=>n.type==='button'&&text(n)==='移除')[0];remove.props.onClick();await settle();assert.ok(text(f.root).includes('本次保存将更新：策略“欧美剧”'));assert.ok(walk(f.root,n=>n.type==='button'&&text(n)==='保存质量策略设置')[0])}finally{f.app.unmount()}
});

test('known zero version history is static while unknown history remains queryable',async()=>{
 const {default:VersionHistory}=await import(pathToFileURL(path.join(out,'VersionHistory.mjs'))),base={target:'target',client:{get:async()=>assert.fail('known zero must not query')},status:{error:ref('')},title:'测试'};
 const known=fixture(VersionHistory,{extra:{...base,count:0}});await settle();try{assert.ok(text(known.root).includes('暂无历史版本'));assert.equal(walk(known.root,n=>n.type==='button').length,0)}finally{known.app.unmount()}
 const unknown=fixture(VersionHistory,{extra:{...base,count:null,client:{get:async()=>({versions:{items:[],total:0,next_offset:null}})}}});await settle();try{assert.ok(walk(unknown.root,n=>n.type==='button'&&text(n)==='查看版本记录')[0])}finally{unknown.app.unmount()}
});

test('a successful scheme save consumes only its shared edit ownership',async()=>{
 const scope={...initial(contract.schemas.CloudScope),cd2_plugin:'cd2',p115_plugin:'p115',root:'/115',allowed_prefixes:['/115/test']};
 const rule=id=>({...initial(contract.schemas.DeliveryRule),id,enabled:true,cloud_scope_id:'shared',local_root:'/organized/'+id,staging_root:'/115/test/staging',incoming_root:'/115/test/incoming',consumer_roots:['/115/test/incoming']});
 const mapping={...initial(contract.schemas.Mapping),id:'m',revision:'saved',emby_service:'Emby',library_id:'L',cloud_scope_id:'shared',emby_prefix:'/strm',local_strm_prefix:'/strm',playback_prefix:'/play',cd2_prefix:'/115'};
 const configured=current=>Object.assign(current.config,{policy:{...current.config.policy,bindings:{tv:'欧美剧',movie:'欧美剧'}},destination_templates:[
  {id:'a',display_name:'方案 A',category_id:'tv',downloader:'qbt',save_path:'/downloads/a',organized_rule:'r-a',sites:[],custom_words:[]},
  {id:'b',display_name:'方案 B',category_id:'movie',downloader:'qbt',save_path:'/downloads/b',organized_rule:'r-b',sites:[],custom_words:[]}
 ],delivery:{classification_revision:1,cloud_scopes:{shared:scope},rules:[rule('r-a'),rule('r-b')],mappings:[mapping],libraries:{Emby:['L']},policy_bindings:{tv:'欧美剧',movie:'欧美剧'}}});
 const f=fixture(Config,{extra:{focus:{group:'plans',id:'a',step:3}},seed:configured,get:async(p,o,c,h)=>{
  if(p.endsWith('/configuration'))return structuredClone(c);if(p.endsWith('/diagnostics'))return {...h,snapshot:{config_revision:c.revision}};
  if(p.includes('/migration/receipts/'))return {state:'APPLIED'};
  if(p.endsWith('/configuration/categories'))return {revision:1,categories:[{id:'tv',name:'欧美剧',enabled:true},{id:'movie',name:'电影',enabled:true}]};
  if(p.endsWith('/policies/catalog'))return {result:{default_templates:{欧美剧:{resolutions:[2160,1080],group:'official',source:'movie',dimensions:['resolution']}}}};
  if(p==='download/clients')return [{name:'qbt'}];if(p==='download/paths'||p==='plugin/'||p==='mediaserver/clients'||p==='mediaserver/library')return [];return {items:[],categories:[],result:{}};
 },post:async(p,b)=>({valid:true,digest:'b'.repeat(64),config:{...b.patch,configuration_receipt:'scheme-save'}})});
 const writes=[];f.api.put=async(p,c)=>{writes.push(structuredClone(c));f.current.config=structuredClone(c);f.current.revision++;f.current.digest='b'.repeat(64);return {success:true}};
 const button=label=>walk(f.root,n=>n.type==='button'&&text(n)===label)[0],step=n=>walk(f.root,node=>node.type==='button'&&(node.props?.['aria-label']?.startsWith('第 '+n+' 步')||Number(node.props?.['data-plan-section'])===n-1))[0];
 const choose=name=>walk(f.root,n=>n.type==='button'&&text(n).includes(name)&&(n.props?.class==='sb-plan-choice'||n.parent?.props?.class==='sb-plan-selector'))[0];
 const click=async label=>{const node=button(label);assert.ok(node,'missing button '+label+' in '+text(f.root).slice(-500));node.props.onClick();await settle()},go=async n=>{const node=step(n);assert.ok(node,'missing step '+n);node.props.onClick();await settle()},select=async name=>{const node=choose(name);assert.ok(node,'missing scheme '+name);node.props.onClick();await settle()};
 const setPath=(label,value)=>{const field=walk(f.root,n=>n.type==='label'&&text(n).startsWith(label))[0],input=walk(field,n=>n.type==='input')[0];input.props.onInput({target:{value}})};
 try{await settle();setPath('Emby 中的 STRM','/a-saved-v2');await settle();await go(5);await click('保存方案');assert.equal(writes[0].delivery.mappings[0].emby_prefix,'/a-saved-v2');
  await select('方案 B');await go(4);setPath('Emby 中的 STRM','/b-uncommitted');await settle();await select('方案 A');await go(2);setPath('下载保存到','/a-next');await settle();await go(5);await click('保存方案');
  assert.equal(writes[1].destination_templates.find(row=>row.id==='a').save_path,'/a-next');assert.equal(writes[1].delivery.mappings[0].emby_prefix,'/a-saved-v2');
  await select('方案 B');await go(5);await click('保存方案');assert.equal(writes[2].delivery.mappings[0].emby_prefix,'/b-uncommitted');
 }finally{f.app.unmount();clearFollowup('Clone')}
});

test('name protection alone can save from the AI page without writing credentials or calling the model',async()=>{
 const f=fixture(Config,{extra:{focus:{group:'ai'}}});await settle();
 try{const checkbox=walk(f.root,n=>n.type==='label'&&text(n).includes('辅助 MoviePilot 识别含数字的片名'))[0].children.find(n=>n.type==='input');checkbox.props.onChange({target:{checked:true}});await settle();const button=walk(f.root,n=>n.type==='button'&&text(n)==='保存名称识别设置')[0];assert.equal(button.props.disabled,false);await button.props.onClick();await settle();const requests=f.calls.filter(c=>c[0]==='post');assert.equal(requests.length,1);assert.ok(requests[0][1].endsWith('/configuration/preview'));assert.equal(requests[0][2].patch.enhance_host_meta,true);assert.equal(f.saves.length,1);}finally{f.app.unmount();clearFollowup('Clone')}
});

test('name recognition tabs keep one draft and save prompts from the fixed footer without testing the model',async()=>{
 const f=fixture(Config,{extra:{focus:{group:'ai'}}});await settle();
 try{
  const button=label=>walk(f.root,n=>n.type==='button'&&text(n)===label)[0];
  for(const label of ['解析修正','AI 服务','提示词与版本','缓存与限流'])assert.ok(button(label),label);
  button('提示词与版本').props.onClick();await settle();
  assert.ok(!text(f.root).includes('服务地址'));
  assert.equal(walk(f.root,n=>n.props?.class==='sb-prompt-history').length,0);
  const prompt=walk(f.root,n=>n.type==='textarea')[0];prompt.props.onInput({target:{value:'只返回核实后的媒体名称'}});await settle();assert.equal(walk(f.root,n=>n.type==='textarea')[0].props.value,'只返回核实后的媒体名称');
  button('AI 服务').props.onClick();await settle();button('提示词与版本').props.onClick();await settle();
  assert.equal(walk(f.root,n=>n.type==='textarea')[0].props.value,'只返回核实后的媒体名称');
  const footer=walk(f.root,n=>n.type==='footer'&&n.props?.class==='sb-editor-footer')[0];
  const save=walk(footer,n=>n.type==='button'&&text(n)==='保存名称识别设置')[0];assert.ok(save);await save.props.onClick();await settle();
  assert.equal(f.calls.filter(c=>c[0]==='post'&&c[1].endsWith('/configuration/preview')).length,1);
  assert.equal(f.calls.filter(c=>c[0]==='post'&&c[1].endsWith('/ai/connection-test')).length,0);
  assert.equal(f.calls.find(c=>c[0]==='post'&&c[1].endsWith('/configuration/preview'))[2].patch.ai_assist.prompt,'只返回核实后的媒体名称');
 }finally{f.app.unmount();clearFollowup('Clone')}
});

test('search and scheduling exposes each function as a tab with stable parameter names',async()=>{
 const f=fixture(Config,{extra:{focus:{group:'candidates'}}});await settle();
 try{
  const button=label=>walk(f.root,n=>n.type==='button'&&text(n)===label)[0];
  for(const label of ['站点与搜索','候选观察','资源替换','升级冷却','失败恢复'])assert.ok(button(label),label);
  button('候选观察').props.onClick();await settle();
  const observation=walk(f.root,n=>n.type==='label'&&text(n).includes('候选观察期'))[0];walk(observation,n=>n.type==='input')[0].props.onChange({target:{checked:true}});await settle();assert.match(text(f.root),/候选观察期/);assert.match(text(f.root),/初始观察时长/);assert.ok(!text(f.root).includes('下载前等一等'));
  button('升级冷却').props.onClick();await settle();
  assert.match(text(f.root),/升级冷却/);assert.ok(!text(f.root).includes('搜索哪些站点'));
  button('资源替换').props.onClick();await settle();assert.match(text(f.root),/最大替换次数/);
  button('失败恢复').props.onClick();await settle();assert.match(text(f.root),/最大恢复次数/);
 }finally{f.app.unmount();clearFollowup('Clone')}
});

test('discovery statistics is a business summary and keeps the raw object out of the normal page',async()=>{
 const get=async p=>{
  if(p==='/discovery/sources')return {items:[],total:0,next_offset:null};
  if(p==='/discovery/catalog')return {result:{routes:[]}};
  if(p==='/discovery/records')return {items:[],total:2,next_offset:null};
  if(p==='/discovery/statistics')return {state:'AVAILABLE',result:{records:{SUBMITTED:2},targets:{SUBMITTED:3},record_denominator:2,target_denominator:3,stages:{recognition:{numerator:2,denominator:2,eligible_denominator:2},intent_ack:{numerator:3,denominator:3,eligible_denominator:3}},cohort:{source_id:null,first_seen:'2026-09-01T00:00:00Z',last_seen:'2026-09-28T00:00:00Z',includes_hidden:true}}};
  return {items:[],total:0,next_offset:null};
 };
 const {default:Discovery}=await import(pathToFileURL(path.join(out,'Discovery.mjs')));
 const f=fixture(Discovery,{extra:{client:{get,post:async()=>({})},status:{current:ref({config:{discovery:{sources:[]}}})}}});await settle();
 try{const button=label=>walk(f.root,n=>n.type==='button'&&text(n)===label)[0];for(const label of ['作品','来源设置','运行记录','统计'])assert.ok(button(label),label);button('统计').props.onClick();await settle();assert.match(text(f.root),/榜单条目\s*2/);assert.match(text(f.root),/处理目标\s*3/);assert.match(text(f.root),/识别完成\s*2 \/ 2/);assert.equal(walk(f.root,n=>n.props?.class==='sb-record').length,0);for(const internal of ['record_denominator','target_denominator','cohort'])assert.ok(!text(f.root).includes(internal));}finally{f.app.unmount()}
});
