import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';import {pathToFileURL,fileURLToPath} from 'node:url';
import {parse,compileScript} from '@vue/compiler-sfc';
import {createRenderer,h,nextTick,ref} from 'vue';
import {contract} from '../src/schema.mjs';
const source=fileURLToPath(new URL('../src/',import.meta.url)),out=fileURLToPath(new URL('../.test-build/',import.meta.url));fs.mkdirSync(out,{recursive:true});
for(const filename of fs.readdirSync(source).filter(f=>f.endsWith('.vue'))){const {descriptor}=parse(fs.readFileSync(path.join(source,filename),'utf8'),{filename});let code=compileScript(descriptor,{id:filename,inlineTemplate:true}).content;code=code.replace(/import\s+['"]\.\/style\.css['"];?/g,'').replace(/from\s+(['"])(\.\/[^'"]+)\1/g,(_,quote,p)=>'from '+JSON.stringify(p.endsWith('.vue')?'./'+path.basename(p,'.vue')+'.mjs':pathToFileURL(path.join(source,p)).href));fs.writeFileSync(path.join(out,filename.replace('.vue','.mjs')),code)}
const {default:Action}=await import(pathToFileURL(path.join(out,'Action.mjs')));
const {default:Resource}=await import(pathToFileURL(path.join(out,'Resource.mjs')));
const {default:Page}=await import(pathToFileURL(path.join(out,'Page.mjs')));const {default:Config}=await import(pathToFileURL(path.join(out,'Config.mjs')));
globalThis.document={activeElement:null};
function element(type){return {type,tagName:type.toUpperCase(),props:{},children:[],style:{},events:{},addEventListener(k,fn){this.events[k]=fn},removeEventListener(k){delete this.events[k]},getAttribute(k){return this.props[k]},get options(){return this.children.filter(n=>n.type==='option')}}}
const renderer=createRenderer({insertStaticContent(value,parent,anchor){const node={type:'#static',text:value,parent};const i=parent.children.indexOf(anchor);if(i<0)parent.children.push(node);else parent.children.splice(i,0,node);return [node,node]},createElement:element,createText:text=>({type:'#text',text}),createComment:text=>({type:'#comment',text}),setText:(n,t)=>n.text=t,setElementText:(n,t)=>n.text=t,patchProp:(n,k,_,v)=>{n.props[k]=v;if(k==='value')n.value=v},insert(n,p,anchor){p.children??=[];if(n.parent)n.parent.children=n.parent.children.filter(x=>x!==n);const i=p.children.indexOf(anchor);if(i<0)p.children.push(n);else p.children.splice(i,0,n);n.parent=p},remove(n){if(n.parent)n.parent.children=n.parent.children.filter(x=>x!==n)},parentNode:n=>n?.parent,nextSibling:n=>n?.parent?.children[n.parent.children.indexOf(n)+1]||null});
const walk=(node,predicate)=>[...(predicate(node)?[node]:[]),...(node.children||[]).flatMap(n=>walk(n,predicate))];const text=node=>[node.text||'',...(node.children||[]).map(text)].join('');
const settle=async()=>{for(let i=0;i<8;i++){await Promise.resolve();await nextTick()}};
function fixture(component,{get,post,extra={}}={}){const calls=[];const config=structuredClone(contract.defaults);const current={revision:4,digest:'a'.repeat(64),config};const health={generation:6,dry_run:true,ordinary_work_active:false,safety_active:true,errors:[],snapshot:{config_revision:4,runtime_generation:6}};const api={get:async(p,o)=>{calls.push(['get',p,o]);if(get)return get(p,o,current,health);if(p.endsWith('/configuration'))return current;if(p.endsWith('/diagnostics'))return health;if(p.endsWith('/configuration/categories'))return {revision:1,categories:[]};return {items:[],total:0,next_offset:null,truncated:false,snapshot:{config_revision:4,runtime_generation:6,high_watermark:'0'}}},post:async(p,b)=>{calls.push(['post',p,b]);return post?post(p,b):{valid:true,errors:[],config:{...config,configuration_receipt:'p'}}}};const saves=[];const root={children:[]};const app=renderer.createApp(component,{api,pluginId:'Clone',sourcePluginId:'SubscriBetter',initialConfig:{...config,password:'SENTINEL'},...extra,onSave:x=>saves.push(x)});app.config.warnHandler=()=>{};app.component('VBtn',{inheritAttrs:false,setup(_,ctx){return()=>h('button',ctx.attrs,ctx.slots.default?.())}});app.component('VDialog',{setup(_,ctx){return()=>h('div',{},ctx.slots.default?.())}});app.mount(root);return {root,app,calls,saves,current}}
test('actual Config mount and save omit private initial fields; number controls send numbers; emit once without PUT',async()=>{
 const f=fixture(Config);await settle();assert.equal(f.calls.filter(c=>c[1].endsWith('/configuration')).length,1);
 const number=walk(f.root,n=>n.type==='input'&&n.props.type==='number')[0];assert.ok(number);number.props.onInput({target:{value:'9'}});await settle();
 const save=walk(f.root,n=>n.type==='button'&&text(n).includes('预检并提交宿主'))[0];assert.ok(save);await save.props.onClick();await settle();assert.equal(f.saves.length,1);assert.ok(!JSON.stringify(f.saves).includes('SENTINEL'));assert.ok(!JSON.stringify(f.calls.filter(c=>c[0]==='post')).includes('SENTINEL'));assert.equal(f.calls.find(c=>c[0]==='post')[2].patch.policy.classification_revision,9);assert.ok(f.calls.filter(c=>c[0]==='post').every(c=>c[1].endsWith('/configuration/preview')));await save.props.onClick();assert.equal(f.saves.length,1);f.app.unmount();
});
test('actual Page traverses all nine domain views using read-only instance API, with empty states',async()=>{
 const f=fixture(Page);await settle();for(const title of ['候选决策','版本档案','交付队列','策略与预演','服务与健康','榜单发现','解析与 AI','整合迁移','目标与任务']){const button=walk(f.root,n=>n.type==='button'&&text(n)===title)[0];assert.ok(button,title);button.props.onClick();await settle();assert.ok(text(f.root).includes(title));}assert.equal(f.calls.filter(c=>c[0]==='post').length,0);assert.ok(f.calls.every(c=>c[1].startsWith('plugin/Clone/')));f.app.unmount();
});
test('delayed Config preflight cannot emit after unmount',async()=>{
 let complete;const f=fixture(Config,{post:()=>new Promise(resolve=>complete=resolve)});await settle();const save=walk(f.root,n=>n.type==='button'&&text(n).includes('预检并提交宿主'))[0];save.props.onClick();await settle();f.app.unmount();complete({valid:true,errors:[],config:contract.defaults});await settle();assert.equal(f.saves.length,0);
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
