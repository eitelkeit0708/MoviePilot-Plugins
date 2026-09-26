export const states={ACTIVE:'追踪中',PENDING:'等待接管',PAUSED:'已暂停',PASSIVE:'仅观察',STOPPED:'已停止',RELEASING:'正在退出管理',RELEASED_NATIVE:'已交还 MoviePilot',PREPARED:'准备就绪',TRANSFERRING:'正在传输',STAGING:'准备暂存',PUBLISHING:'正在发布',PUBLISHED:'已发布',COMPLETED:'处理完成',CONFIRMED:'已确认',UNKNOWN:'结果待核实',FAILED:'处理失败',ERROR:'处理失败',ABANDONED:'已取消',WAITING:'等待处理',READY:'准备就绪',NOT_SENT:'尚未发布',SENT:'已发送，等待确认',ALLOW:'符合策略',ACCEPT:'符合策略',REJECT:'不符合策略',DEFER:'等待条件满足',ENRICH:'等待补充信息',PRESENT:'已找到',MISSING:'未找到',INVALID:'已失效'};
export const stateLabel=value=>states[value]||'待确认状态';
export const dateText=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'尚无记录';
export function unitLabel(unit){try{const v=JSON.parse(unit.target_key);return v[0]==='电影'?'正片':Number.isInteger(v[5])?`第 ${v[5]} 集`:'待确认目标'}catch{return '待确认目标'}}
export function qualitySummary(facts){
 if(!facts)return '尚无已确认的质量信息';
 if(facts.state&&facts.state!=='PRESENT')return stateLabel(facts.state)+'，质量待核实';
 if(Array.isArray(facts.versions)){const versions=facts.versions.filter(v=>v.reliable===true);return [...new Set(versions.map(v=>qualitySummary(v.raw?.technical)))].join(' / ')||'版本质量待核实'}
 const f=facts.quality||facts;
 return [f.resolution?`${f.resolution}p`:null,{web:'WEB',remux:'REMUX',bluray:'Blu-ray'}[f.source],f.group,f.platform].filter(Boolean).join(' · ')||'质量信息待补充';
}
export function formatBytes(value){if(!Number.isFinite(value)||value<0)return '大小未知';const units=['B','KiB','MiB','GiB','TiB'];let i=0;while(value>=1024&&i<4){value/=1024;i++}return `${value.toFixed(i?1:0)} ${units[i]}`}
export function adoptionBody(row,template,operation){
 if(!row||!Number.isSafeInteger(row.id)||row.id<=0||!['电影','电视剧'].includes(row.type)||!row.media_source||!row.media_id||!row.name)throw Error('原生订阅身份不完整，请在 MoviePilot 中核实。');
 if(row.type==='电视剧'&&(!Number.isInteger(row.season)||row.season<0))throw Error('原生订阅缺少明确的季，请在 MoviePilot 中核实。');
 if(!template)throw Error('请选择下载与入库方案。');
 return {intent_key:operation,native_id:row.id,adopt:true,media_type:row.type,media_source:row.media_source,media_id:String(row.media_id),name:row.name,year:String(row.year||''),season:row.type==='电视剧'?row.season:null,episode_group:row.episode_group||'',destination_template:template,mode:'CONTINUOUS'};
}

export const dimensions={resolution:'分辨率',picture:'HDR / 画质',special:'特效字幕',source:'片源',hq:'高码率',audio:'音轨',anime:'动画偏好'};
Object.assign(states,{OK:'抓取正常',NEW:'等待处理',READY:'准备处理',DEFERRED:'等待重试',FILTERED:'未符合筛选',RECOGNIZED:'已识别',SUBMITTED:'已提交订阅',EXISTING:'已有记录',MANAGED:'已纳管',ADDED:'已提交订阅',BLOCKED:'需要处理',UPLOADING:'正在上传',REMOTE_VERIFIED:'暂存已验证',WAIT_CONSUMER:'等待整理 / 入库',PUBLISH_OUTCOME_UNKNOWN:'发布结果待核实',VERIFIED:'文件已验证',CD2_UPLOADING:'CD2 正在上传',CD2_PAUSED:'CD2 已暂停'});
export function reasonText(reason){const messages={WAITING_ASSETS:'等待视频或必要字幕齐备后继续',ADMITTED:'符合准入条件',QUALITY_UPGRADE:'候选版本质量更优',CURRENT_BETTER:'现有版本质量更优',EQUIVALENT:'与现有版本质量相当',SOURCE_CONFIG_CHANGED:'来源设置已变更，尚未重新检查',CD2_REMOTE_UNSETTLED:'CD2 上传结果尚未确认',CD2_PENDING:'等待 CD2 完成上传',CD2_PAUSED:'CD2 上传已暂停，请检查云盘任务',CONSUMER_SETTLEMENT_REQUIRED:'等待 Symedia 整理及媒体库确认',RAPID_EXHAUSTED:'秒传尝试已用尽，等待后续处理',FALLBACK_LIMIT:'已达到普通上传预算',EXTERNAL_OUTCOME_UNKNOWN:'外部结果未知，需要先核对实际状态',REPROCESS_REQUESTED:'已安排重新判定',CHINESE_LANGUAGE:'缺少符合策略的中文音轨或字幕依据',RSSHUB_BASE_REQUIRED:'尚未配置自部署 RSSHub 地址',WAIT_OWNER:'存在其他处理者，先解决纳管冲突'};return messages[reason]||(reason?'需要查看详情确认处理条件':'')}
export function taskProgress(task){const p=task.progress;if(!p?.targets)return '目标范围待确认';return `已知 ${p.targets} ${task.media_type==='电影'?'个目标':'集'} · 最近档案在库 ${p.present??0}`}
export function taskNext(task,health,now=Date.now()){if(task.state==='PAUSED')return '恢复追踪后继续';if(task.state==='PENDING')return '等待接管确认后开始追踪';if(['STOPPED','RELEASED_NATIVE','RELEASING'].includes(task.state))return stateLabel(task.state);if(!health?.ordinary_work_active)return health?.dry_run?'演练中，不执行下载交付':'等待追踪启用';const p=task.progress;const waiting=[['观察',p?.observation_until],['冷却',p?.cooldown_until]].filter(([,v])=>v).map(([name,value])=>new Date(value).getTime()>now?name+'至 '+dateText(value):name+'记录已到期，等待重新核对');if(waiting.length)return waiting.join('；');return p?.processing?'继续处理在途目标，具体阶段见详情':'等待下一轮候选检查'}
export function deliveryNext(bundle,health,now=Date.now()){
 if(['CONFIRMED','COMPLETED','ABANDONED','FAILED'].includes(bundle.state))return '已结束本次交付；时间见处理记录';
 if(['UNKNOWN','PUBLISH_OUTCOME_UNKNOWN','PUBLISHING'].includes(bundle.state))return '先核对发布结果，再决定后续动作';
 if(!health?.ordinary_work_active)return health?.dry_run?'演练中，普通交付不调度':'普通交付未运行';
 const due=new Date(bundle.due).getTime();return Number.isFinite(due)?due>now?'检查时间 '+dateText(bundle.due):'已到检查时间，等待下一轮处理':'尚无明确检查时间';
}
Object.assign(states,{RAPID_WAIT:'等待秒传重试',RAPID_IN_FLIGHT:'正在尝试秒传',WAITING_ASSETS:'等待视频或必要字幕齐备',READY_TO_PUBLISH:'等待发布',DOWNLOADING:'正在下载',SUPERSEDED:'已被新计划取代',CANCELLED:'已取消',ATTACHED:'已关联计划',ACQUIRE:'补充缺失版本',UPGRADE:'升级已有版本'});
export function sourceTitle(source,routes=[]){const s=source?.config||source||{};if(s.name?.trim())return s.name.trim();const route=routes.find(r=>r.key===s.route_key||s.url?.includes('/douban/list/'+r.key));return route?.label||(s.source_type_hint==='Movie'?'自定义电影榜单':s.source_type_hint==='TV'?'自定义剧集榜单':'自定义榜单')}
export function sourceNotice(source){if(source.last_reason==='SOURCE_CONFIG_CHANGED')return reasonText(source.last_reason);return ['OK','SUCCESS','NEVER'].includes(source.last_state)?'':reasonText(source.last_reason)}
