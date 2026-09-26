import fixtures from '../display-fixtures.json' with {type:'json'};
import {compactQuality,downloadPercent} from '../../src/media.mjs';

// Design fixtures only. No HTTP adapter, NAS paths or production credentials.
export const qualityLabel=q=>compactQuality(q).replace(/2160p/g,'4K');
export const phaseLabels={DOWNLOADING:'下载中',RAPID_WAIT:'等待秒传',WAITING_ASSETS:'等待字幕',WAIT_CONSUMER:'等待入库',UNKNOWN:'结果待核对',UPLOADING:'上传中'};
export const tone=phase=>['UNKNOWN','PUBLISH_OUTCOME_UNKNOWN'].includes(phase)?'attention':['DOWNLOADING','UPLOADING'].includes(phase)?'active':'waiting';
export const glyph=phase=>({DOWNLOADING:'↓',UPLOADING:'↑',UNKNOWN:'!',PUBLISH_OUTCOME_UNKNOWN:'!',WAIT_CONSUMER:'↳',RAPID_WAIT:'◷',WAITING_ASSETS:'◷'}[phase]||'·');
export const episodes=['downloading','rapid','assets','unknown','unknown','downloading'].map((scene,index)=>{
 const u=structuredClone(fixtures.scenes[scene].units.items[0]);
 const phases=['DOWNLOADING','RAPID_WAIT','WAITING_ASSETS','WAIT_CONSUMER','UNKNOWN','UPLOADING'];
 return {number:index+1,current:u.current_quality?.[0]?.quality,target:u.processing?.quality,phase:phases[index],download:u.processing?.download,
   transfer:u.processing?.transfer_files||[],files:u.processing?.files||[],
   // This sample's deciding dimension is supplied, never ranked in the view.
   improvements:['resolution'],reason:'分辨率符合此方案的升级优先级',
   progress:['已下载 512 MiB / 2 GiB','未命中 2 / 6 次','视频已就绪，字幕尚未齐备','已交给 Symedia','上次云端请求尚未确认','CD2 正在传输'][index],
   next:['','8 分钟后检查','','等待 Emby 确认','','尚未取得云端进度'][index],
   actions:index===4?[{kind:'reconcile',label:'核对结果'}]:[], percent:index===0?downloadPercent(u.processing?.download):null};
});
export const media=[
 {id:'gate24',title:'GATE24 大机场',year:2026,type:'电视剧',season:1,state:'ACTIVE',present:6,known:6,quality:'1080p',episodes},
 {id:'nell',title:'侠女内莉',year:2024,type:'电视剧',season:1,state:'PAUSED',present:2,known:6,quality:'4K',episodes:[]},
 {id:'spring',title:'一瓯春',year:null,type:'电视剧',season:1,state:'STOPPED',present:null,known:null,quality:null,episodes:[]},
 {id:'unconfirmed',title:'尚未确认集数的连载作品',year:null,type:'电视剧',season:1,state:'ACTIVE',present:null,known:null,quality:null,episodes:[]}
];
export function highlights(work){
 const units=work.episodes||[],priority={UNKNOWN:0,PUBLISH_OUTCOME_UNKNOWN:0,DOWNLOADING:1,UPLOADING:2,RAPID_WAIT:3,WAITING_ASSETS:4,WAIT_CONSUMER:5};
 return [...units].sort((a,b)=>(priority[a.phase]??9)-(priority[b.phase]??9)||a.number-b.number).slice(0,3);
}
export const blankScheme=()=>({id:'scheme-'+crypto.randomUUID(),name:'',category:'',downloader:'',download:'',policy:'',cd2:'',p115:'',root:'/115',allowed:'',server:'',library:'',emby:'',local:'',playback:'',remote:'',staging:'',incoming:'',enabled:false});
export const exampleScheme={id:'scheme-tv-01',name:'追更剧集',category:'电视剧',downloader:'qBittorrent（示例）',download:'/downloads/series',policy:'4K 优先 · 保留当前可播版本',cd2:'CD2（示例）',p115:'115（示例）',root:'/115',allowed:'/115/series',server:'Emby（示例）',library:'剧集（示例）',emby:'/strm/series',local:'/media/strm/series',playback:'http://cd2.example/115/series',remote:'/115/series',staging:'/115/series/staging',incoming:'/115/series/incoming',enabled:false};
const required=[['name','方案名称'],['category','作品分类'],['downloader','下载器'],['download','下载目录'],['policy','版本偏好']];
export function issues(s,step){
 const fields=[required,[['cd2','CD2 连接'],['p115','115 连接'],['root','云盘根目录'],['allowed','允许写入的目录']],[['server','Emby 连接'],['library','媒体库'],['emby','Emby 中的路径'],['local','MoviePilot 中的路径'],['playback','STRM 内的地址'],['remote','CD2 中的路径']],[['staging','暂存目录'],['incoming','Symedia 入口']]][step]||[];
 const result=fields.filter(([key])=>!s[key]?.trim()).map(([key,label])=>({key,message:'请填写'+label}));
 for(const [key,label] of fields.filter(([k])=>['download','root','allowed','emby','local','remote','staging','incoming'].includes(k))){if(s[key]&&(!s[key].startsWith('/')||s[key].split('/').includes('..')||/[\r\n\0]/.test(s[key])))result.push({key,message:label+'应为绝对路径，且不含 ..'})}
 const within=(p,root)=>p===root||p?.startsWith(root?.replace(/\/$/,'')+'/');
 if(step===1&&s.allowed&&s.root&&!within(s.allowed,s.root))result.push({key:'allowed',message:'写入目录需要位于所选云盘内'});
 if(step===3){for(const k of ['staging','incoming'])if(s[k]&&s.allowed&&!within(s[k],s.allowed))result.push({key:k,message:'请使用允许写入范围内的目录'});if(s.staging&&s.incoming&&within(s.staging,s.incoming))result.push({key:'staging',message:'暂存目录不能位于 Symedia 入口内'})}
 return result;
}
export const mappingKey=s=>JSON.stringify(['server','library','emby','local','playback','remote'].map(k=>s[k]));
export function simulatePathCheck(s,sample){
 const missing=issues(s,2);if(missing.length)return {ok:false,message:missing[0].message};
 if(!sample?.path?.startsWith(s.emby.replace(/\/$/,'')+'/'))return {ok:false,message:'样本路径不在所填 Emby 目录下，请检查第一段路径。'};
 if(!sample.content?.startsWith(s.playback.replace(/\/$/,'')+'/'))return {ok:false,message:'样本 STRM 地址不匹配，请检查第二段路径。'};
 return {ok:true,emby:sample.path,local:s.local.replace(/\/$/,'')+sample.path.slice(s.emby.replace(/\/$/,'').length),remote:s.remote.replace(/\/$/,'')+sample.content.slice(s.playback.replace(/\/$/,'').length)};
}
export const samples=[{id:'episode-1',name:'GATE24 大机场',episode:'第 1 集',path:'/strm/series/GATE24/S01E01.strm',content:'http://cd2.example/115/series/GATE24/S01E01.mkv'},{id:'episode-2',name:'GATE24 大机场',episode:'第 2 集',path:'/strm/series/GATE24/S01E02.strm',content:'http://cd2.example/115/series/GATE24/S01E02.mkv'}];
