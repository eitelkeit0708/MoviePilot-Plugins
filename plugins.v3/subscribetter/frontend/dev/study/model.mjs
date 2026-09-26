import fixtures from '../display-fixtures.json' with {type:'json'};
import {compactQuality,downloadPercent,qualitySummary} from '../../src/media.mjs';
import {id} from '../../src/client.mjs';
import policySnapshot from './policies.json' with {type:'json'};
export const {policies,categories,schedule,rules}=policySnapshot;
export const policyName=id=>policies.find(p=>p.id===id)?.name||'策略暂不可用';
export const categoryName=id=>categories.find(c=>c.id===id)?.name||'分类暂不可用';

// Design fixtures only. No HTTP adapter, NAS paths or production credentials.
export const qualityLabel=q=>compactQuality(q).replace(/2160p/g,'4K');
export const phaseLabels={DOWNLOADING:'下载中',RAPID_WAIT:'等待秒传',WAITING_ASSETS:'等待字幕',WAIT_CONSUMER:'等待入库',UNKNOWN:'结果待核对',UPLOADING:'上传中',QUEUED:'等待下载',PRESENT:'已收录'};
export const tone=phase=>['UNKNOWN','PUBLISH_OUTCOME_UNKNOWN'].includes(phase)?'attention':['DOWNLOADING','UPLOADING'].includes(phase)?'active':phase==='PRESENT'?'collected':'waiting';
export const glyph=phase=>({DOWNLOADING:'↓',UPLOADING:'↑',UNKNOWN:'!',PUBLISH_OUTCOME_UNKNOWN:'!',WAIT_CONSUMER:'↳',RAPID_WAIT:'◷',WAITING_ASSETS:'◷',PRESENT:'✓'}[phase]||'·');
export const episodes=['downloading','rapid','assets','unknown','unknown','downloading'].map((scene,index)=>{
 const u=structuredClone(fixtures.scenes[scene].units.items[0]);
 const phases=['DOWNLOADING','RAPID_WAIT','WAITING_ASSETS','WAIT_CONSUMER','UNKNOWN','UPLOADING'];
 return {number:index+1,current:u.current_quality?.[0]?.quality,target:u.processing?.quality,phase:phases[index],download:u.processing?.download,
   transfer:u.processing?.transfer_files||[],files:u.processing?.files||[],
   // This sample's deciding dimension is supplied, never ranked in the view.
   change:{kind:'quality',dimensions:['resolution'],reason:'分辨率符合此方案的升级优先级'},
   progress:['已下载 512 MiB / 2 GiB','未命中 2 / 6 次','视频已就绪，字幕尚未齐备','已交给 Symedia','上次云端请求尚未确认','CD2 正在传输'][index],
   next:['','8 分钟后检查','','等待 Emby 确认','','尚未取得云端进度'][index],
   actions:index===4?[{kind:'reconcile',label:'核对结果'}]:[], percent:index===0?downloadPercent(u.processing?.download):null};
});
// Explicit design facts stand in for the server decision. No ranking runs in this view.
const base={resolution:2160,picture:2,audio:1,special_zh_subtitles:true,evidence:'explicit',source:'web',basis:{resolution:'measured',picture:'measured',audio:'measured'}};
const release=values=>({...base,...values,basis:{resolution:'release',picture:'release',audio:'release'}});
Object.assign(episodes[1],{current:{...base,picture:0},target:release({}),change:{kind:'quality',dimensions:['picture'],reason:'同为 4K，画面维度决定此次升级'}});
Object.assign(episodes[2],{current:{...base},target:release({audio:3}),change:{kind:'quality',dimensions:['audio'],reason:'分辨率与画面相同，音频从 DDP 升为无损'}});
Object.assign(episodes[3],{current:{...base,evidence:'inferred_pgs'},target:release({}),change:{kind:'evidence',dimensions:['special'],reason:'质量等级相同；中文 PGS 推断更新为明确特效声明'}});
Object.assign(episodes[5],{current:{...base},target:release({audio:3}),change:{kind:'quality',dimensions:['audio'],reason:'同分辨率、同画面，升级音频'}});
episodes[4].followup={nextAt:null,blocked:[{label:'重新上传',reason:'上传结果未确认，避免重复提交'},{label:'清理暂存',reason:'尚未核实云端文件，暂不能清理'}]};
episodes.push(
 {number:7,current:null,currentState:'MISSING',target:release({}),phase:'QUEUED',change:{kind:'acquire',dimensions:[],reason:'已确认这一集尚未在库，首次下载'},progress:'已选定待下载资源',next:'尚未取得开始时间',percent:null,actions:[],files:[]},
 {number:8,current:{...base,audio:3},target:null,phase:'PRESENT',change:{kind:'none',dimensions:[],reason:'当前版本已达到所选偏好'},progress:'当前没有在途任务',next:'',percent:null,actions:[],files:[]}
);
for(const u of episodes)u.files=u.target?[`GATE24.S01E${String(u.number).padStart(2,'0')}.${u.target.resolution}p.WEB-DL.mkv`]:[];
export function qualityParts(q){
 if(!q)return [];
 const parts=qualitySummary(q,true).replace(/2160p/g,'4K').split(' · ');
 return [...['resolution','picture','audio'].map((dimension,i)=>({dimension,text:parts[i]})),
  {dimension:'special',text:q.evidence==='inferred_pgs'?'中文 PGS 推断':q.evidence==='explicit'&&q.special_zh_subtitles===true?'明确特效声明':'字幕依据未取得'}];
}
export function versionChange(u){
 const kind=u.change?.kind;
 if(kind==='none'&&!u.target)return {label:'已收录',before:qualityLabel(u.current),after:'',targetLabel:''};
 if(kind==='acquire')return {label:'首次下载',before:u.currentState==='MISSING'?'尚未在库':'在库情况待确认',after:qualityLabel(u.target),targetLabel:'待下载版本'};
 const dimensions=u.change?.dimensions||[];
 const select=q=>dimensions.map(d=>qualityParts(q).find(p=>p.dimension===d)?.text||'规格未知').join(' · ');
 return {label:kind==='evidence'?'字幕依据更新':'版本升级',before:dimensions.length?select(u.current):'变更依据未取得',after:dimensions.length?select(u.target):qualityLabel(u.target),targetLabel:kind==='evidence'?'声明更新目标':'升级目标'};
}
export function collectionText(work){
 if(work.present==null)return '在库情况待确认';
 if(work.totalReliable===true&&Number.isInteger(work.seasonTotal)&&work.seasonTotal>0&&work.present<=work.seasonTotal)return `${work.present} / ${work.seasonTotal} 集在库`;
 return `已收录 ${work.present} 集 · 总集数待确认`;
}
export const media=[
 {id:'gate24',title:'GATE24 大机场',year:2026,type:'电视剧',season:1,state:'ACTIVE',present:7,known:8,seasonTotal:null,quality:'1080p / 4K',episodes},
 {id:'nell',title:'侠女内莉',year:2024,type:'电视剧',season:1,state:'PAUSED',present:2,known:6,seasonTotal:6,totalReliable:true,quality:'4K',episodes:[]},
 {id:'spring',title:'一瓯春',year:null,type:'电视剧',season:1,state:'STOPPED',present:null,known:null,quality:null,episodes:[]},
 {id:'unconfirmed',title:'尚未确认集数的连载作品',year:null,type:'电视剧',season:1,state:'ACTIVE',present:6,known:6,seasonTotal:null,quality:null,episodes:[]}
];
export function highlights(work){
 const units=work.episodes||[],priority={UNKNOWN:0,PUBLISH_OUTCOME_UNKNOWN:0,DOWNLOADING:1,UPLOADING:2,RAPID_WAIT:3,WAITING_ASSETS:4,WAIT_CONSUMER:5};
 return units.filter(u=>u.phase!=='PRESENT').sort((a,b)=>(priority[a.phase]??9)-(priority[b.phase]??9)||a.number-b.number).slice(0,3);
}
export const blankScheme=()=>({id:'scheme-'+id(),name:'',category:'',downloader:'',download:'',policy:'',policyRevision:'',cd2:'',p115:'',root:'/115',allowed:'',server:'',library:'',emby:'',local:'',playback:'',remote:'',staging:'',incoming:'',enabled:false});
export const exampleScheme={id:'scheme-tv-01',name:'追更剧集',category:'tv-west',downloader:'qBittorrent（示例）',download:'/downloads/series',policy:policies[0].id,policyRevision:policies[0].revision,cd2:'CD2（示例）',p115:'115（示例）',root:'/115',allowed:'/115/series',server:'Emby（示例）',library:'剧集（示例）',emby:'/strm/series',local:'/media/strm/series',playback:'/vol3/1000/docker/clouddrive2/CloudNAS/CloudDrive/115',remote:'/115',staging:'/115/series/staging',incoming:'/115/series/incoming',enabled:false};
const required=[['name','方案名称'],['category','MP 分类'],['downloader','下载器'],['download','下载目录'],['policy','质量策略']];
export function issues(s,step){
 const fields=[required,[['cd2','CD2 连接'],['p115','115 连接'],['root','云盘根目录'],['allowed','允许写入的目录']],[['server','Emby 连接'],['library','媒体库'],['emby','Emby STRM 路径前缀'],['local','MP 可读 STRM 路径前缀'],['playback','STRM 内容路径前缀'],['remote','CD2 内部路径前缀']],[['staging','暂存目录'],['incoming','Symedia 入口']]][step]||[];
 const result=fields.filter(([key])=>!s[key]?.trim()).map(([key,label])=>({key,message:'请填写'+label}));
 for(const [key,label] of fields.filter(([k])=>['download','root','allowed','emby','local','playback','remote','staging','incoming'].includes(k))){if(s[key]&&(!s[key].startsWith('/')||s[key].split('/').includes('..')||/[\r\n\0]/.test(s[key])))result.push({key,message:label+'应为绝对路径，且不含 ..'})}
 if(step===0&&s.category&&!categories.some(c=>c.id===s.category))result.push({key:'category',message:'原分类暂不可用，请重新选择'});
 if(step===0&&s.policy&&!policies.some(p=>p.id===s.policy&&p.category_id===s.category&&p.revision===s.policyRevision))result.push({key:'policy',message:'策略不属于当前分类、版本已变更或暂不可用，请重新选择'});
 const within=(p,root)=>p===root||p?.startsWith(root?.replace(/\/$/,'')+'/');
 if(step===1&&s.allowed&&s.root&&!within(s.allowed,s.root))result.push({key:'allowed',message:'写入目录需要位于所选云盘内'});
 if(step===3){for(const k of ['staging','incoming'])if(s[k]&&s.allowed&&!within(s[k],s.allowed))result.push({key:k,message:'请使用允许写入范围内的目录'});if(s.staging&&s.incoming&&within(s.staging,s.incoming))result.push({key:'staging',message:'暂存目录不能位于 Symedia 入口内'})}
 return result;
}
export const mappingKey=s=>JSON.stringify(['server','library','emby','local','playback','remote'].map(k=>s[k]));
export function simulatePathCheck(s,sample){
 const missing=issues(s,2);if(missing.length)return {ok:false,message:missing[0].message};
 if(!sample?.path?.startsWith(s.emby.replace(/\/$/,'')+'/'))return {ok:false,message:'样本路径不在所填 Emby 目录下，请检查第一段路径。'};
 if(!sample.content?.startsWith(s.playback.replace(/\/$/,'')+'/'))return {ok:false,message:'样本 STRM 内容不匹配，请检查本地挂载路径前缀。'};
 return {ok:true,emby:sample.path,local:s.local.replace(/\/$/,'')+sample.path.slice(s.emby.replace(/\/$/,'').length),content:sample.content,remote:s.remote.replace(/\/$/,'')+sample.content.slice(s.playback.replace(/\/$/,'').length)};
}
export const samples=[1,2].map(n=>({id:'episode-'+n,name:'GATE24 大机场',episode:`第 ${n} 集`,path:`/strm/series/GATE24/S01E0${n}.strm`,content:`/vol3/1000/docker/clouddrive2/CloudNAS/CloudDrive/115/series/GATE24/S01E0${n}.mkv`}));
