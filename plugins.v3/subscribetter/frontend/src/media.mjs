export const states={ACTIVE:'追踪中',PENDING:'等待接管',PAUSED:'已暂停',PASSIVE:'仅观察',STOPPED:'已停止',RELEASING:'正在退出管理',RELEASED_NATIVE:'已交还 MoviePilot',PREPARED:'准备就绪',TRANSFERRING:'正在传输',STAGING:'准备暂存',PUBLISHING:'正在发布',PUBLISHED:'已发布',COMPLETED:'处理完成',CONFIRMED:'已确认',UNKNOWN:'结果待核实',FAILED:'处理失败',ERROR:'处理失败',ABANDONED:'已取消',WAITING:'等待处理',READY:'准备就绪',NOT_SENT:'尚未发布',SENT:'已发送，等待确认',ALLOW:'符合策略',ACCEPT:'符合策略',REJECT:'不符合策略',DEFER:'等待条件满足',ENRICH:'等待补充信息',PRESENT:'已找到',MISSING:'未找到',INVALID:'已失效'};
export const stateLabel=value=>states[value]||'待确认状态';
export const dateText=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'尚无记录';
export function unitLabel(unit){try{const v=JSON.parse(unit.target_key);return v[0]==='电影'?'正片':Number.isInteger(v[5])?`第 ${v[5]} 集`:'待确认目标'}catch{return '待确认目标'}}
export function qualitySummary(facts){
 if(!facts)return '尚无已确认的质量信息';
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
export function reasonText(reason){const messages={ADMITTED:'符合准入条件',QUALITY_UPGRADE:'候选版本质量更优',CURRENT_BETTER:'现有版本质量更优',EQUIVALENT:'与现有版本质量相当',CD2_PENDING:'等待 CD2 完成上传',CD2_PAUSED:'CD2 上传已暂停，请检查云盘任务',CONSUMER_SETTLEMENT_REQUIRED:'等待消费者整理与入库核实',RAPID_EXHAUSTED:'秒传尝试已用尽，等待后续处理',FALLBACK_LIMIT:'已达到普通上传预算',EXTERNAL_OUTCOME_UNKNOWN:'外部结果未知，需要先核对实际状态',REPROCESS_REQUESTED:'已安排重新判定',CHINESE_LANGUAGE:'缺少符合策略的中文音轨或字幕依据',RSSHUB_BASE_REQUIRED:'尚未配置自部署 RSSHub 地址',WAIT_OWNER:'存在其他处理者，先解决纳管冲突'};return messages[reason]||(reason?'需要核对处理依据：'+reason:'暂无需要处理的问题')}
