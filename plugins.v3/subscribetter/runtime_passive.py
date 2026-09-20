"""Independent bounded raw RSS and selected-library passive inventory."""
import json
import time
from datetime import timedelta
from .archive import digest
from .candidates import value
from .planner import encoded
from .repository import Target,utcnow
from .scheduler import instant,parse


class Passive:
    def __init__(self,runtime):
        self.r=runtime;self.repo=runtime.repository;self.config=runtime.config

    def scan(self,deadline):
        r=self.r;r.check()
        selected=sorted((s,str(lib)) for s,libs in self.config.passive_libraries.items() for lib in libs)
        if not selected:return dict(state='PASSIVE_DISABLED')
        cursor=self.repo.setting('passive-library-cursor') or 0
        service,library=selected[cursor%len(selected)];key='passive-library:'+digest([service,library])
        state=self.repo.setting(key) or {}
        if state.get('next_at') and instant()<parse(state['next_at']):
            self.repo.setting('passive-library-cursor',(cursor+1)%len(selected));return dict(state='SCAN_NOT_DUE')
        limits=self.config.recovery
        result=r.delivery.archive.reconcile(service,library,scan_id=state.get('scan_id'),
            limits=dict(pages=limits.pages,page_size=limits.entries,items=limits.entries),deadline=deadline)
        state.update(result=result,scan_id=result['scan_id'] if result['status']=='INCOMPLETE' else None)
        if result['status']!='INCOMPLETE':
            state['next_at']=(instant()+timedelta(seconds=limits.reconcile_seconds)).isoformat()
            self.repo.setting('passive-library-cursor',(cursor+1)%len(selected))
        self.repo.setting(key,state)
        # Scanning builds archive facts only; it never submits old-library tasks.
        return dict(state='PASSIVE_SCAN',result=result)

    def rss(self,deadline):
        r=self.r;r.check()
        if not self.config.passive_libraries and not self.repo.list_tasks(1):return dict(state='RSS_NO_SCOPE')
        sites=sorted((s for s in r.candidates.adapter.sites() if s.get('id') in self.config.candidates.site_ids),key=lambda s:s['id'])
        if not sites:return dict(state='RSS_NO_SELECTED_SITES')
        cursor=self.repo.setting('runtime-rss-site-cursor') or 0
        site=sites[cursor%len(sites)];key='runtime-rss-site:'+str(site['id']);state=self.repo.setting(key) or dict(seen={},pending=[],cursor=0)
        self.repo.setting('runtime-rss-site-cursor',(cursor+1)%len(sites))
        if state['cursor']>=len(state['pending']):
            if state.get('next_at') and instant()<parse(state['next_at']):return dict(state='RSS_NOT_DUE',site_id=site['id'])
            state['attempt_at']=utcnow();state['next_at']=(instant()+timedelta(seconds=self.config.candidates.refresh_seconds)).isoformat()
            try:
                r.check()
                rows=r.candidates.adapter.rss(site,self.config.safety.network_timeout)
                r.check();pending=[];seen={}
                for raw in rows:
                    try:record=r.candidates.observe(raw,source='rss')
                    except (ValueError,TypeError):continue
                    cid=record['candidate_key'];fingerprint=digest(record);seen[cid]=fingerprint
                    if state['seen'].get(cid)!=fingerprint or cid in state.get('deferred',[]):pending.append(cid)
                state.update(seen=seen,pending=list(dict.fromkeys(pending)),cursor=0,deferred=[],watermark=utcnow(),failure=None,count=len(rows))
            except Exception as error:
                state['failure']=r.reason(error);self.repo.setting(key,state)
                return dict(state='RSS_FAILED',site_id=site['id'],reason=state['failure'])
            self.repo.setting(key,state)
        processed=0
        while state['cursor']<len(state['pending']) and processed<self.config.candidates.supplement_limit and time.monotonic()<deadline:
            cid=state['pending'][state['cursor']]
            try:outcome=self.arrival(cid)
            except Exception as error:
                outcome=dict(state='DEFER',reason=r.reason(error));state['deferred'].append(cid)
            self.repo.setting('runtime-rss-result:'+cid,dict(outcome,at=utcnow()))
            state['cursor']+=1;processed+=1;self.repo.setting(key,state)
        return dict(state='RSS_CHECKED',site_id=site['id'],processed=processed,remaining=len(state['pending'])-state['cursor'],failure=state.get('failure'))

    def arrival(self,key):
        r=self.r;r.check()
        raw=r.candidates.refresh(key,r.budget(dict(effective=dict(candidates=self.config.candidates.model_dump()))))
        r.check()
        corrected=r.meta.parse('rss:'+digest(key),value(raw,'title') or '',value(raw,'description'))
        if corrected.status!='OK':raise ValueError('RSS_META_UNCONFIRMED')
        r.check()
        media=r.candidates.adapter.recognize(corrected.meta,None)
        r.check()
        identity=r.candidates.adapter.identity(media);kind=value(media,'type');kind=getattr(kind,'value',kind)
        if not identity or kind not in ('电影','电视剧'):raise ValueError('RSS_IDENTITY_UNCONFIRMED')
        season=value(corrected.meta,'begin_season') if kind=='电视剧' else None
        if kind=='电视剧' and (type(season)is not int or season<0):raise ValueError('RSS_SEASON_UNCONFIRMED')
        target=Target(kind,str(identity[0]),str(identity[1]),season,'')
        row=self.repo.by_target(target)
        if row and row['state'] in ('STOPPED','RELEASED_NATIVE','PAUSED'):return dict(state='USER_STOPPED')
        with self.repo.connection() as db:
            active=db.execute("SELECT id FROM opportunities WHERE task_id=? AND state='ACTIVE' ORDER BY created_at LIMIT 1",(row['id'],)).fetchone() if row else None
        if active:
            saved=self.repo.setting('runtime-input:'+active['id'])
            if not saved:raise ValueError('ORIGINAL_RUNTIME_INPUT_REQUIRED')
            result=r.evaluate(saved,key)
            if not result['plans'] and not result.get('enrichments'):return dict(state='NO_IMPROVEMENT')
            self.queue(active['id'],key);return dict(state='MERGED',opportunity_id=active['id'])
        scopes=[[s,str(lib)] for s,libs in self.config.passive_libraries.items() for lib in libs]
        # The existing expression identity index avoids walking the whole library
        # for each RSS item. Only current selected-scope presence can wake work.
        with self.repo.connection() as db:
            keys=[x[0] for x in db.execute("SELECT t.target_key FROM archive_targets t WHERE json_extract(t.target_key,'$[0]')=? AND json_extract(t.target_key,'$[1]')=? AND json_extract(t.target_key,'$[2]')=? AND json_extract(t.target_key,'$[3]') IS ? AND json_extract(t.target_key,'$[4]')='' AND t.state='PRESENT' AND EXISTS(SELECT 1 FROM archive_versions v JOIN json_each(?) s ON v.service=json_extract(s.value,'$[0]') AND v.library=json_extract(s.value,'$[1]') WHERE v.target_key=t.target_key AND v.active=1) ORDER BY t.target_key LIMIT 1001",(kind,str(identity[0]),str(identity[1]),season,encoded(scopes)))]
        if not keys:return dict(state='OUTSIDE_PASSIVE_SCOPE')
        if len(keys)>1000:raise ValueError('PASSIVE_SCOPE_LIMIT')
        scope=r.scope(target,fresh=True);template=r.destination(scope)
        eligible=sorted(set(keys)&set(scope['units']))
        if not eligible:return dict(state='OUTSIDE_PROVIDER_SCOPE')
        result=r.pipeline.evaluate(key,target,eligible,downloader=template['downloader'],save_path=template['save_path'],custom_words=template['custom_words'],mode='episode')
        approved=sorted({k for plan in result['plans'] for k,v in plan['targets'].items() if v['action'] in ('QUALITY_UPGRADE','EVIDENCE_UPGRADE','REPLACE_INVALID','SIDECAR_SUPPLEMENT')})
        approved=sorted(set(approved)|{e['target_key'] for e in result.get('enrichments',[])})
        if not approved:return dict(state='NO_IMPROVEMENT')
        r.check()
        # Include the old revision so a later genuinely changed archive may open
        # another opportunity while retries of this exact arrival remain stable.
        baseline=r.delivery.archive.current(approved)
        intent='rss:'+digest([target.key,approved,[(k,v['archive_revision']) for k,v in baseline.items()]])
        row=r.submit(intent,target,dict(name=scope['keywords'][0] if scope['keywords'] else str(identity[1])),'rss',template_id=template['id'],mode='ONESHOT',approved_units=approved)
        with self.repo.connection() as db:
            active=db.execute("SELECT id FROM opportunities WHERE task_id=? AND state='ACTIVE'",(row['id'],)).fetchone()
        if active:self.queue(active['id'],key)
        else:self.repo.setting('runtime-pending-arrivals:'+str(row['id']),[key])
        return dict(state='ADMITTED',task_id=row['id'],opportunity_id=active['id'] if active else None)

    def queue(self,opportunity,key):
        setting='runtime-arrivals:'+opportunity
        pending=self.repo.setting(setting) or []
        if key not in pending:
            if len(pending)>=1000:raise ValueError('RSS_ARRIVAL_LIMIT')
            self.repo.setting(setting,pending+[key])
