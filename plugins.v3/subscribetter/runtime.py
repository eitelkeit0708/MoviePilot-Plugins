"""Shared bounded production composition over the existing durable state machines."""
from datetime import date
from contextlib import contextmanager
from threading import RLock, local
import asyncio
import json
import time
from uuid import uuid4

from .candidates import HostCandidateAdapter, CandidatePipeline, SearchBudget, value
from .planner import TargetUnit, encoded, active_snapshot
from .archive import digest
from .repository import Target, utcnow, snapshot_config
from .scheduler import ScheduleConfig, instant, parse


class ScopeProvider:
    """Explicit provider episode rows; count projections never define work units."""
    def __init__(self,*,recognize=None,season_detail=None,identity=None,classify=None):
        self.recognize=recognize or self._recognize
        self.season_detail=season_detail or self._season_detail
        self.identity=identity or HostCandidateAdapter.identity
        self.classify=classify or HostCandidateAdapter.classify

    @staticmethod
    def _recognize(target,group):
        from app.chain.media import MediaChain
        from app.sdk.media import normalize_media_source
        from app.schemas.types import MediaType
        return MediaChain().run_module('recognize_media',meta=None,mtype=MediaType(target.media_type),
            media_source=normalize_media_source(target.media_source),media_id=target.media_id,episode_group=group or None)

    @staticmethod
    def _season_detail(target):
        from app.chain.media import MediaChain
        from app.schemas.types import MediaType
        return MediaChain().run_module('tmdb_info',tmdbid=int(target.media_id),mtype=MediaType.TV,season=target.season)

    def _verified(self,media,target):
        kind=value(media,'type');kind=getattr(kind,'value',kind)
        if not media or self.identity(media)!=(target.media_source,target.media_id) or kind!=target.media_type:
            raise ValueError('PROVIDER_IDENTITY_UNVERIFIED')
        return media

    def resolve(self,target,*,today=None):
        if getattr(self,'checkpoint',None):self.checkpoint()
        media=self._verified(self.recognize(target,''),target)
        if getattr(self,'checkpoint',None):self.checkpoint()
        result=dict(target_key=target.key,provider_identity=[target.media_source,target.media_id],
            episode_group=target.episode_group,season=target.season,episodes=[],provider_rows=[],scope_closed=True,
            classification=self.classify(media),keywords=list(dict.fromkeys(str(value(media,k)) for k in ('title','original_title','original_name') if value(media,k)))[:3])
        if target.media_type=='电影':
            result['units']=[TargetUnit(target).key];return result
        if target.media_source!='themoviedb' or not target.media_id.isascii() or not target.media_id.isdecimal():
            raise ValueError('PROVIDER_EPISODE_ROWS_UNAVAILABLE')
        if target.episode_group:
            info=value(media,'tmdb_info',{}) or {};groups=info.get('episode_groups',{})
            listed=groups.get('results') if isinstance(groups,dict) else None
            if not isinstance(listed,list) or len(listed)>1000 or sum(isinstance(g,dict) and g.get('id')==target.episode_group for g in listed)!=1:
                raise ValueError('GROUP_MEMBERSHIP_UNVERIFIED')
            if getattr(self,'checkpoint',None):self.checkpoint()
            grouped=self._verified(self.recognize(target,target.episode_group),target)
            seasons=value(grouped,'season_info')
            if value(grouped,'episode_group')!=target.episode_group or not isinstance(seasons,list) or len(seasons)>1000:
                raise ValueError('GROUP_SCOPE_UNVERIFIED')
            selected=[s for s in seasons if isinstance(s,dict) and type(s.get('order')) is int and s['order']==target.season]
            if len(selected)!=1:raise ValueError('GROUP_SCOPE_UNVERIFIED')
            detail=selected[0]
        else:
            if getattr(self,'checkpoint',None):self.checkpoint()
            detail=self.season_detail(target)
            if not isinstance(detail,dict) or detail.get('season_number',target.season)!=target.season:
                raise ValueError('PROVIDER_SEASON_UNVERIFIED')
        rows=detail.get('episodes')
        if not isinstance(rows,list) or not 1<=len(rows)<=1000:raise ValueError('PROVIDER_EPISODE_ROWS_UNAVAILABLE')
        seen=set();ids=set();closed=True;today=today or date.today()
        for row in rows:
            if not isinstance(row,dict):raise ValueError('PROVIDER_EPISODE_ROWS_INVALID')
            episode=row.get('episode_number')
            if type(episode)is not int or episode<=0 or episode in seen:raise ValueError('PROVIDER_EPISODE_ROWS_INVALID')
            if not target.episode_group and row.get('season_number')!=target.season:raise ValueError('PROVIDER_SEASON_UNVERIFIED')
            if row.get('show_id') is not None and str(row['show_id'])!=target.media_id:raise ValueError('PROVIDER_IDENTITY_UNVERIFIED')
            if row.get('id') is not None:
                if type(row['id'])is not int or row['id']<=0 or row['id'] in ids:raise ValueError('PROVIDER_EPISODE_ROWS_INVALID')
                ids.add(row['id'])
            try:closed=closed and date.fromisoformat(row.get('air_date') or '')<=today
            except (ValueError,TypeError):closed=False
            seen.add(episode)
            result['provider_rows'].append({k:row[k] for k in ('id','show_id','season_number','episode_number','air_date','order') if k in row})
        # Counts can prove that explicit rows cover the declared season; they
        # never generate an episode. Aired-so-far alone is not season completion.
        declared=(value(media,'tmdb_info',{}) or {}).get('seasons',[])
        selected=[s for s in declared if isinstance(s,dict) and s.get('season_number')==target.season]
        complete_count=len(selected)==1 and type(selected[0].get('episode_count'))is int and selected[0]['episode_count']==len(rows)
        if target.episode_group:
            # Group ordering is independent of broadcast count. A real group
            # episode_count is an optional completeness witness, never a range.
            complete_count=type(detail.get('episode_count'))is int and detail['episode_count']==len(rows)
        result.update(episodes=sorted(seen),units=[TargetUnit(target,e).key for e in sorted(seen)],scope_closed=closed and complete_count)
        return result


class OwnedStages:
    """Reload fences immediately; the old generation closes only after its I/O."""
    def __init__(self,close):
        self.lock=RLock();self.users=0;self.retired=False;self.closed=False;self.close_resources=close

    @contextmanager
    def lease(self):
        with self.lock:
            if self.retired:raise ValueError('STALE_GENERATION')
            self.users+=1
        try:yield
        finally:
            with self.lock:
                self.users-=1
                self._close()

    def _close(self):
        if self.retired and not self.users and not self.closed:
            self.closed=True;self.close_resources()

    def retire(self):
        with self.lock:self.retired=True;self._close()

    async def run(self,function,*args,**kwargs):
        with self.lease():
            task=asyncio.create_task(asyncio.to_thread(function,*args,**kwargs))
            try:await asyncio.wait({task})
            except asyncio.CancelledError:
                while not task.done():
                    try:await asyncio.wait({task})
                    except asyncio.CancelledError:continue
                try:task.result()
                except BaseException:pass
                raise
            return task.result()


class Runtime:
    """One composition of the existing scheduler, authority, archive and workers."""
    def __init__(self,plugin,*,provider=None,clients=None):
        from .execution import ConfiguredDownloader
        from .policy import Policy
        self.plugin=plugin;self.repository=plugin.repository;self.config=plugin.config.model_copy(deep=True)
        self.generation=plugin.generation;self.owner=plugin.ownership;self.scheduler=plugin.scheduler
        self.authority=plugin.authority;self.candidates=plugin.candidates;self.meta=plugin.meta_service
        self.delivery=plugin.delivery_worker;self.provider=provider or ScopeProvider();self.clients=clients or ConfiguredDownloader.named
        self.scopes={};self.scope_workers={};self.busy=False;self.lock=RLock();self.ai=plugin.ai
        self.stages=OwnedStages(self.close);self._io=local()
        if isinstance(self.provider,ScopeProvider):self.provider.checkpoint=self.read_check
        self.candidates.checkpoint=self.check
        self.owner.dispatch_gate=self.check
        config=self.config.policy
        self.policy=Policy(config.bindings or self.config.delivery.get('policy_bindings',{}),
            config.classification_revision if config.bindings else self.config.delivery.get('classification_revision',1),
            overrides=config.overrides,admission=config.admission) if config.bindings or self.config.delivery.get('policy_bindings') else None
        self.pipeline=CandidatePipeline(self.candidates,self.meta,self.policy,self.delivery.archive.current,self.clients,
            current=self.check,locked=config.locks) if self.policy and self.delivery else None
        if self.pipeline:
            self.pipeline.context=lambda:dict(config_revision=self.config.configuration_revision,runtime_generation=self.generation)
            self.pipeline.sanitize=self.public_evidence
        if self.delivery and self.policy:self.delivery.archive.policy=self.policy
        if self.delivery:
            self.delivery.archive.scope_provider=lambda target:self.scope(target,fresh=True)
            self.delivery.dispatch_gate=self.check
            if hasattr(self.delivery.archive,'sources'):
                self.delivery.archive.sources.checkpoint=self.read_check
                self.delivery.archive.sources.ordinary_checkpoint=self.check
                self.delivery.archive.sources.safety_checkpoint=lambda:self.checkpoint(getattr(self,'deadline',None))
        if self.policy:self.authority.set_revisions(self.policy.semantic_hash,self.meta.corrector.revision)

    def public_evidence(self,value):
        from .migration import safe
        config=self.config.ai_assist
        refs=[config['endpoint_ref'],*config['credential_refs']]
        private=[self.plugin.configuration.secrets.resolve(ref) for ref in refs if ref]
        return safe(value,private)

    def close(self):
        from .host_delivery_contract import close_delivery
        closing=([self.ai.close] if self.ai else [])+([lambda:close_delivery(self.delivery)] if self.delivery else [])
        closing += [lambda worker=worker:close_delivery(worker) for worker in self.scope_workers.values()]
        self.close_errors=[]
        for close in closing:
            try:close()
            except Exception:self.close_errors.append('RUNTIME_CLOSE_FAILED')
        if self.close_errors:
            try:self.repository.setting('runtime-close:'+str(self.generation),self.close_errors)
            except Exception:pass

    def check(self):
        if self.stages.retired or self.plugin.generation!=self.generation or not self.plugin._ordinary_work_active():
            raise ValueError('STALE_OR_DISABLED_RUNTIME')
        self.checkpoint(getattr(self,'deadline',None))

    def scope(self,target,*,fresh=False):
        self.read_check()
        old=self.scopes.get(target.key)
        if fresh or old is None or time.monotonic()-old[0]>60:
            old=(time.monotonic(),self.provider.resolve(target));self.scopes[target.key]=old
            while len(self.scopes)>1000:self.scopes.pop(next(iter(self.scopes)))
        return json.loads(encoded(old[1]))

    def read_check(self):
        if getattr(self._io,'safety',False):self.checkpoint(getattr(self,'deadline',None))
        else:self.check()

    @contextmanager
    def safety_reads(self):
        previous=getattr(self._io,'safety',False);self._io.safety=True
        try:yield
        finally:self._io.safety=previous

    def destination(self,scope,template_id=None):
        classification=scope['classification']
        if classification.get('state')!='complete' or not self.policy or classification.get('policy_revision')!=self.policy.classification_revision:
            raise ValueError('CLASSIFICATION_UNVERIFIED')
        category=classification.get('effective',{}).get('category_id')
        templates=[t for t in self.config.destination_templates if t.category_id==category and (template_id is None or t.id==template_id)]
        if len(templates)!=1:raise ValueError('EXACT_DESTINATION_TEMPLATE_REQUIRED')
        template=templates[0].model_dump()
        if not self.delivery or template['organized_rule'] not in self.delivery.rules or not self.delivery.rules[template['organized_rule']]['enabled']:
            raise ValueError('DELIVERY_RULE_UNAVAILABLE')
        if not template['sites'] or self.clients(template['downloader']) is None:raise ValueError('SEARCH_DOWNLOAD_CONFIG_REQUIRED')
        return template

    def effective(self,template,mode,*,completed_mode=None):
        lifecycle=self.config.lifecycle.model_dump()
        if completed_mode is not None:lifecycle['completed_mode']=completed_mode
        return dict(template=template,mode=mode,schedule=self.config.schedule,lifecycle=lifecycle,
            candidates=self.config.candidates.model_dump(),policy_revision=self.policy.semantic_hash,
            parse_revision=self.meta.corrector.revision)

    def submit(self,intent,target,snapshot,actor,native_id=None,adopt=False,*,template_id=None,mode='CONTINUOUS',approved_units=None):
        self.check()
        prior=self.repository.by_target(target)
        if prior and prior['state'] in ('STOPPED','RELEASED_NATIVE','PAUSED'):return self.owner.submit(intent,target,snapshot,actor,native_id,adopt)
        scope=self.scope(target)
        if approved_units is not None:
            if mode!='ONESHOT' or not approved_units or not set(approved_units)<=set(scope['units']):raise ValueError('INVALID_ONESHOT_SCOPE')
            scope=dict(scope,provider_units=scope['units'],units=sorted(approved_units),episodes=[json.loads(k)[5] for k in sorted(approved_units)] if target.media_type=='电视剧' else [])
        if actor=='discovery':template_id=snapshot.get('save_path')
        template=self.destination(scope,template_id)
        self.check()
        native=dict(snapshot) if adopt else dict(snapshot,save_path=template['save_path'],downloader=template['downloader'],sites=template['sites'],custom_words=template['custom_words'])
        pending=self.repository.setting('runtime-task:'+str(prior['id'])) if prior else None
        override=(pending['effective']['lifecycle']['completed_mode'] if pending and pending.get('completed_mode_override') else None)
        if override is not None and 'provider_units' in pending['scope']:
            scope=dict(scope,provider_units=pending['scope']['provider_units'])
        frozen=dict(scope=scope,effective=self.effective(template,mode,completed_mode=override),native=snapshot_config(native))
        intent_setting='runtime-intent:'+digest(intent)
        previous=self.repository.setting(intent_setting)
        if previous and previous!=frozen:
            original=dict(frozen,effective=dict(frozen['effective'],lifecycle=dict(frozen['effective']['lifecycle'],
                completed_mode=previous['effective']['lifecycle']['completed_mode'])))
            if 'provider_units' not in previous['scope']:
                original['scope']={k:v for k,v in original['scope'].items() if k!='provider_units'}
            if override is None or original!=previous:raise ValueError('INTENT_CONFIG_CHANGED')
        if not previous:self.repository.setting(intent_setting,frozen)
        row=self.owner.submit(intent,target,native,actor,native_id,adopt)
        with self.repository.connection() as db:
            active=db.execute("SELECT 1 FROM opportunities WHERE task_id=? AND state='ACTIVE'",(row['id'],)).fetchone()
            handled=db.execute("SELECT 1 FROM opportunities WHERE task_id=? AND state IN ('COMPLETED','ARCHIVED')",(row['id'],)).fetchone()
        if previous and handled and not active:return row
        pending_key='runtime-task:'+str(row['id'])
        previous=self.repository.setting(pending_key)
        if previous and (previous['effective']!=frozen['effective'] or previous['scope']!=frozen['scope']):
            with self.repository.connection() as db:active=db.execute("SELECT 1 FROM opportunities WHERE task_id=? AND state='ACTIVE'",(row['id'],)).fetchone()
            if active or mode!='ONESHOT':raise ValueError('TASK_CONFIG_CHANGED')
            previous=None
        if mode=='ONESHOT' and handled and not active:previous=None
        if not previous:self.repository.setting(pending_key,dict(frozen,admission_id=digest(intent)))
        if row['state'] in ('ACTIVE','PASSIVE') and row.get('native_id'):
            self.attach(row,scope,template,mode=mode)
        return row

    def attach(self,row,scope,template,*,mode='CONTINUOUS'):
        self.check()
        if mode not in ('CONTINUOUS','ONESHOT'):raise ValueError('INVALID_OPPORTUNITY_MODE')
        with self.repository.connection() as db:
            existing=db.execute("SELECT id FROM opportunities WHERE task_id=? AND state='ACTIVE' ORDER BY created_at LIMIT 1",(row['id'],)).fetchone()
        saved=self.repository.setting('runtime-input:'+existing['id']) if existing else None
        pending=self.repository.setting('runtime-task:'+str(row['id'])) if existing else None
        prior_input=saved or pending or {}
        override=prior_input if prior_input.get('completed_mode_override') else None
        effective=self.effective(template,mode,completed_mode=override['effective']['lifecycle']['completed_mode'] if override else None)
        config_digest=digest(effective)
        if existing:
            if saved:
                if saved['config_digest']!=config_digest or saved['scope']['units']!=scope['units']:raise ValueError('RUNTIME_SCOPE_OR_CONFIG_CHANGED')
                return saved
            if not pending or digest(pending['effective'])!=config_digest or pending['scope']['units']!=scope['units']:
                raise ValueError('ORIGINAL_RUNTIME_INPUT_REQUIRED')
        target=Target.from_task(row);units=[TargetUnit(target,n) for n in scope['episodes']] if scope['episodes'] else [TargetUnit(target)]
        opportunity=self.scheduler.open_opportunity(existing['id'] if existing else 'runtime:'+uuid4().hex,row['id'],units,mode=mode,config=ScheduleConfig(**effective['schedule']))
        lifecycle=effective['lifecycle']
        if mode=='CONTINUOUS':
            self.scheduler.configure_lifecycle(row['id'],scope['units'],movie_days=lifecycle['movie_days'],tv_days=lifecycle['tv_days'],anchor=lifecycle['expiry_mode'])
            self.scheduler.update_completion(row['id'],scope['units'],scope_closed=scope['scope_closed'],collected=False)
        saved=dict(task_id=row['id'],task_generation=row['generation'],opportunity_id=opportunity['id'],scope=scope,
            effective=effective,config_digest=config_digest,configuration_revision=self.config.configuration_revision,created_at=opportunity['created_at'],
            planner_mode='season' if scope['scope_closed'] and mode=='CONTINUOUS' and lifecycle['completed_mode']=='PACK' else 'episode')
        saved['admission_id']=(self.repository.setting('runtime-task:'+str(row['id'])) or {}).get('admission_id')
        self.repository.setting('runtime-input:'+opportunity['id'],saved)
        return saved

    @staticmethod
    def checkpoint(deadline=None):
        if deadline is not None and time.monotonic()>=deadline:raise ValueError('TICK_DEADLINE')

    def inventory(self,target,*,template_id=None,deadline=None):
        self.checkpoint(deadline)
        if not self.delivery:return dict(state='UNKNOWN',diagnostics=['ARCHIVE_UNAVAILABLE'],evidence_ref=None)
        scope=self.scope(target,fresh=True);archive=self.delivery.archive
        self.checkpoint(deadline);template=self.destination(scope,template_id)
        cloud_scope=self.delivery.rules[template['organized_rule']]['cloud_scope_id']
        selected=sorted({(r['emby_service'],str(r['library_id'])) for r in archive.mappings.rules
            if r.get('media_source','themoviedb')==target.media_source and r.get('episode_group','')==target.episode_group and r['cloud_scope_id']==cloud_scope})
        if not selected:raise ValueError('SELECTED_LIBRARY_SCOPE_REQUIRED')
        key='runtime-inventory:'+digest([scope,template]);state=self.repository.setting(key) or dict(cursor=0,scans={},complete=[])
        service,library=selected[state['cursor']%len(selected)];pair=encoded([service,library])
        limits=self.config.recovery
        result=archive.reconcile(service,library,target_keys=scope['units'],scope=scope,scan_id=state['scans'].get(pair),
            limits=dict(pages=limits.pages,page_size=limits.entries,items=limits.entries),deadline=min(deadline or float('inf'),time.monotonic()+limits.seconds))
        if result['status']=='INCOMPLETE':state['scans'][pair]=result['scan_id']
        else:
            state['scans'].pop(pair,None);state['cursor']=(state['cursor']+1)%len(selected)
            if result['status']=='COMPLETE':state['complete']=list(set(state['complete'])|{pair})
            elif pair in state['complete']:state['complete'].remove(pair)
        self.repository.setting(key,state)
        if len(state['complete'])!=len(selected) or result['status']!='COMPLETE':
            return dict(state='UNKNOWN',diagnostics=result.get('diagnostics') or ['INVENTORY_INCOMPLETE'],evidence_ref=result['scan_id'])
        return self.inventory_view(target,scope=scope)

    def inventory_view(self,target,*,scope=None):
        scope=scope or (self.scopes.get(target.key) or (None,None))[1]
        if not self.delivery or not scope:return dict(state='UNKNOWN',diagnostics=['UNOBSERVED_SCOPE'],evidence_ref=None)
        facts=self.delivery.archive.current(scope['units']);states={v['state'] for v in facts.values()}
        state='MISSING' if states=={'MISSING'} else 'INVALID' if states=={'INVALID'} else 'PRESENT' if states=={'PRESENT'} and target.media_type=='电影' else 'PARTIAL' if 'PRESENT' in states else 'UNKNOWN'
        if target.media_type=='电视剧' and states=={'PRESENT'} and scope['scope_closed']:
            with self.repository.connection() as db:
                missing=db.execute('SELECT 1 FROM json_each(?) k WHERE NOT EXISTS(SELECT 1 FROM ingest_receipts r JOIN target_units u USING(target_key) WHERE r.target_key=k.value AND r.generation=u.generation AND u.last_ingest_confirmed_at IS NOT NULL) LIMIT 1',(encoded(scope['units']),)).fetchone()
            if not missing:state='INGESTED'
        return dict(state=state,evidence_ref='archive:'+digest([(k,v['archive_revision']) for k,v in facts.items()]),
            diagnostics=sorted({d for v in facts.values() for d in v.get('diagnostics',[])}))

    @staticmethod
    def budget(saved):
        values=saved['effective']['candidates']
        return SearchBudget(**{k:values[k] for k in ('keywords','pages','concurrency','results','requests','interval')})

    @staticmethod
    def aired_units(scope,target):
        if target.media_type=='电影':return scope['units']
        today=date.today();aired=set()
        for row in scope['provider_rows']:
            try:
                if date.fromisoformat(row.get('air_date') or '')<=today:
                    aired.add(TargetUnit(target,row['episode_number']).key)
            except (ValueError,TypeError,KeyError):continue
        return sorted(aired&set(scope['units']))

    def verify_input(self,saved):
        self.check();row=self.repository.get_task(saved['task_id'])
        if not row or row['state'] not in ('ACTIVE','PASSIVE') or row['generation']!=saved['task_generation']:
            raise ValueError('TASK_GENERATION_CHANGED')
        template=self.destination(self.scope(Target.from_task(row),fresh=True),saved['effective']['template']['id'])
        if digest(self.effective(template,saved['effective']['mode'],
                                 completed_mode=saved['effective']['lifecycle']['completed_mode'] if saved.get('completed_mode_override') else None))!=saved['config_digest']:
            raise ValueError('RUNTIME_CONFIG_CHANGED')
        return row

    def evaluate(self,saved,key,*,simulation=False,assistance=True):
        assistance=assistance and not getattr(self,'management_no_ai',False)
        row=self.verify_input(saved);target=Target.from_task(row);scope=saved['scope'];template=saved['effective']['template']
        fresh=self.scope(target)
        if fresh['units']!=scope.get('provider_units',scope['units']):raise ValueError('PROVIDER_SCOPE_CHANGED')
        mode=saved['planner_mode']
        eligible=self.aired_units(fresh,target)
        eligible=sorted(set(eligible)&set(scope['units']))
        if not eligible:return dict(plans=[],reason='NO_AIRED_UNITS')
        return self.pipeline.evaluate(key,target,eligible,downloader=template['downloader'],save_path=template['save_path'],
            custom_words=template['custom_words'],task_id=row['id'],mode=mode,opportunity_id=saved['opportunity_id'],simulation=simulation,assistance=assistance,locks=saved.get('locks'))

    def settings(self,request,*,db,actor):
        """Rebind a reviewed shrinking opportunity, preserving all historical work."""
        from .configuration import PolicyConfig
        task=db.execute('SELECT * FROM tasks WHERE id=?',(request['task_id'],)).fetchone()
        if not task or task['state'] not in ('ACTIVE','PASSIVE','PAUSED') or task['generation']!=request['generation']:raise ValueError('TASK_GENERATION_CHANGED')
        opportunity=db.execute("SELECT * FROM opportunities WHERE id=? AND task_id=? AND state='ACTIVE'",(request['opportunity_id'],task['id'])).fetchone()
        if not opportunity:raise ValueError('ACTIVE_OPPORTUNITY_REQUIRED')
        raw=db.execute('SELECT value FROM settings WHERE key=?',('runtime-input:'+opportunity['id'],)).fetchone()
        if not raw:raise ValueError('ORIGINAL_RUNTIME_INPUT_REQUIRED')
        saved=json.loads(raw[0]);keys=sorted(set(request['target_keys']))
        if saved['task_generation']!=task['generation'] or saved['task_id']!=task['id']:raise ValueError('ORIGINAL_RUNTIME_INPUT_STALE')
        revisions=(self.policy.semantic_hash,self.meta.corrector.revision)
        if (request.get('policy_revision',saved['effective']['policy_revision']),
            request.get('parse_revision',saved['effective']['parse_revision']))!=revisions:raise ValueError('REVIEWED_REVISIONS_CHANGED')
        if not keys or not set(keys)<=set(json.loads(opportunity['scope'])):raise ValueError('TARGET_EXPANSION_FORBIDDEN')
        templates=[t.model_dump() for t in self.config.destination_templates if t.id==request['destination_template']]
        if len(templates)!=1 or templates[0]['category_id']!=saved['effective']['template']['category_id']:raise ValueError('DESTINATION_SCOPE_CHANGED')
        template=templates[0]
        if not self.delivery or template['organized_rule'] not in self.delivery.rules:raise ValueError('DELIVERY_RULE_UNAVAILABLE')
        PolicyConfig(locks=request['locks'])
        plans=[dict(r) for r in db.execute("SELECT * FROM plans WHERE task_id=? AND authorization IN ('ACTIVE','PREPARED') LIMIT 1001",(task['id'],))]
        if len(plans)>1000:raise ValueError('EXACT_PREVIEW_SCOPE_LIMIT')
        if db.execute("SELECT 1 FROM plans other JOIN plans own ON json_extract(own.snapshot,'$.downloader')=json_extract(other.snapshot,'$.downloader') AND json_extract(own.snapshot,'$.infohash')=json_extract(other.snapshot,'$.infohash') WHERE own.task_id=? AND other.task_id!=? AND own.authorization IN ('ACTIVE','PREPARED') AND other.authorization IN ('ACTIVE','PREPARED') LIMIT 1",(task['id'],task['id'])).fetchone():raise ValueError('SHARED_REFERENCE')
        if db.execute("SELECT 1 FROM target_units WHERE task_id=? AND publish_phase IN ('PUBLISHING','PUBLISH_OUTCOME_UNKNOWN','HANDED_OFF') LIMIT 1",(task['id'],)).fetchone():raise ValueError('PUBLICATION_UNSETTLED')
        if db.execute("SELECT 1 FROM plan_actions WHERE plan_id IN (SELECT id FROM plans WHERE task_id=?) AND state IN ('UNKNOWN','IN_FLIGHT','PUBLISHING','PUBLISH_OUTCOME_UNKNOWN','HANDED_OFF') LIMIT 1",(task['id'],)).fetchone():raise ValueError('EXTERNAL_OUTCOME_UNKNOWN')
        for plan in plans:
            units=[dict(r) for r in db.execute('SELECT * FROM target_units WHERE owner_plan_id=?',(plan['id'],))]
            if units:self.authority.cancel(plan['id'],{u['target_key']:{k:u[k] for k in ('owner_plan_id','generation','publish_phase','current_revision')} for u in units},reason='TASK_SETTINGS_CHANGED',db=db)
        db.execute("UPDATE plans SET authorization='CANCELLED' WHERE task_id=? AND authorization='PREPARED'",(task['id'],))
        db.execute("UPDATE plan_actions SET state='CANCELLED' WHERE state='PENDING' AND plan_id IN (SELECT id FROM plans WHERE task_id=?)",(task['id'],))
        db.execute('UPDATE tasks SET generation=generation+1,updated_at=? WHERE id=?',(utcnow(),task['id']))
        # Keep all observation rows/clocks and consumed budgets as history.
        db.execute('UPDATE opportunities SET scope=?,updated_at=? WHERE id=?',(encoded(keys),utcnow(),opportunity['id']))
        db.execute('DELETE FROM opportunity_targets WHERE opportunity_id=? AND target_key NOT IN (SELECT value FROM json_each(?))',(opportunity['id'],encoded(keys)))
        # The reviewed subset is also the exact scope for future completion evidence.
        # Existing anchors/expiry remain immutable; no historical unit is removed.
        db.execute('UPDATE task_lifecycle SET scope=? WHERE task_id=?',(json.dumps(keys),task['id']))
        scope=dict(saved['scope'],units=keys,provider_units=saved['scope'].get('provider_units',saved['scope']['units']))
        effective=dict(saved['effective'],template=template,policy_revision=revisions[0],parse_revision=revisions[1])
        completed_mode=request.get('completed_mode')
        if completed_mode is not None:
            if (task['media_type']!='电视剧' or effective['mode']!='CONTINUOUS' or not scope['scope_closed']
                    or completed_mode not in ('EPISODE','PACK')):raise ValueError('COMPLETED_MODE_SCOPE_REQUIRED')
            if completed_mode=='PACK' and set(keys)!=set(scope['provider_units']):raise ValueError('PACK_REQUIRES_FULL_PROVIDER_SCOPE')
            effective['lifecycle']=dict(effective['lifecycle'],completed_mode=completed_mode)
            saved['completed_mode_override']=True
        saved.update(scope=scope,effective=effective,config_digest=digest(effective),task_generation=task['generation']+1,
                     planner_mode='season' if scope['scope_closed'] and effective['mode']=='CONTINUOUS' and effective['lifecycle']['completed_mode']=='PACK' else 'episode',
                     locks=request['locks'])
        db.execute('UPDATE settings SET value=? WHERE key=?',(encoded(saved),'runtime-input:'+opportunity['id']))
        db.execute('UPDATE settings SET value=json_set(value,\'$.scope\',json(?),\'$.effective\',json(?),\'$.completed_mode_override\',json(?)) WHERE key=?',
                   (encoded(scope),encoded(effective),encoded(bool(saved.get('completed_mode_override'))),'runtime-task:'+str(task['id'])))
        db.execute('DELETE FROM settings WHERE key IN (?,?)',('runtime-round:'+opportunity['id'],'runtime-search:'+opportunity['id']))
        self.repository._audit(db,task['id'],'TASK_SETTINGS_CHANGED',actor)
        return dict(task_id=task['id'],generation=task['generation']+1,opportunity_id=opportunity['id'],target_keys=keys)

    def recover(self,plan,saved):
        self.verify_input(saved)
        key=plan['snapshot']['candidate_key']
        self.candidates.refresh(key,self.budget(saved))
        output=self.evaluate(saved,key)
        try:self.pipeline.revalidate(plan)
        except ValueError as error:raise ValueError('COLD_PLAN_CHANGED') from error
        return output

    def refresh_plan(self,plan,saved):
        cached=self.pipeline.rounds.get(plan['snapshot']['candidate_key'])
        if cached is None or time.monotonic()-cached.get('acquired',0)>300:
            if not saved:raise ValueError('ORIGINAL_RUNTIME_INPUT_REQUIRED')
            self.recover(plan,saved)

    def replan_stale_paused(self,plan):
        """Release a stale plan only before any bytes or downstream action escape."""
        if plan['authorization']!='ACTIVE' or plan['snapshot'].get('local_assets'):return False
        from .execution import MUTATION_LOCK
        snapshot=plan['snapshot']
        with MUTATION_LOCK:
            executor=self.pipeline.executor();owned=executor._owned(snapshot)
            try:
                if owned:
                    if owned['state']!='PAUSED_VERIFIED' or not owned['client_id'] or not owned['marker']:return False
                    client=executor.clients(snapshot['downloader'])
                    task=executor._task(snapshot,executor._read(client.task,snapshot['infohash']))
                    if task['state']!='PAUSED' or str(task['id'])!=owned['client_id'] or owned['marker'] not in task.get('markers',[]):return False
                    files=executor._table(snapshot,executor._read(client.files,snapshot['infohash']))
                    if any(type(f.get('completed')) is not int or f['completed']!=0 for f in files.values()):return False
                    if {i for i,f in files.items() if f['wanted']}!=set(snapshot['selected_indices']):return False
            except Exception:
                return False
            with self.repository.connection(write=True) as db:
                actions=[dict(r) for r in db.execute('SELECT kind,state FROM plan_actions WHERE plan_id=?',(plan['id'],))]
                if any(r['state'] not in ('PENDING','SUCCEEDED') or
                       (r['state']=='SUCCEEDED' and r['kind'] not in ('ADD','SET_WANTED')) for r in actions):return False
                if db.execute('SELECT 1 FROM organized_assets WHERE plan_id=? LIMIT 1',(plan['id'],)).fetchone():return False
                if db.execute('SELECT 1 FROM delivery_bundles WHERE plan_id=? LIMIT 1',(plan['id'],)).fetchone():return False
                current=db.execute('SELECT * FROM managed_downloads WHERE downloader=? AND infohash=?',
                                   (snapshot['downloader'],snapshot['infohash'])).fetchone()
                if (dict(current) if current else None)!=owned:return False
                if not owned and any(r['kind']=='ADD' and r['state']=='SUCCEEDED' for r in actions):return False
                references=self.authority.download_references(snapshot['downloader'],snapshot['infohash'],snapshot['save_path'],db=db)
                if len(references)!=1 or references[0]['plan_id']!=plan['id']:return False
                vector=self.authority._vector(db,list(snapshot['targets']))
                if any(v['owner_plan_id']!=plan['id'] or v['publish_phase']!='NOT_SENT' for v in vector.values()):return False
                self.authority.cancel(plan['id'],vector,reason='STALE_PAUSED_REPLAN',db=db)
            return True

    def search(self,saved,words):
        """Rotate configured sites within one shared request budget per round."""
        key='runtime-search:'+saved['opportunity_id'];state=self.repository.setting(key) or dict(cursor=0)
        if state.get('next_at') and instant()<parse(state['next_at']):return []
        sites=saved['effective']['template']['sites'];budget=self.budget(saved)
        count=min(len(sites),32,max(1,budget.requests//2));start=state['cursor']%len(sites)
        selected=(sites+sites)[start:start+count]
        rows=self.candidates.search(selected,words,budget)
        from datetime import timedelta
        state.update(cursor=(start+count)%len(sites),next_at=(instant()+timedelta(seconds=saved['effective']['candidates']['refresh_seconds'])).isoformat())
        self.repository.setting(key,state)
        return rows

    def advance(self,plan,saved,*,deadline=None):
        self.checkpoint(deadline)
        self.verify_input(saved)
        self.checkpoint(deadline)
        try:
            self.refresh_plan(plan,saved)
            self.checkpoint(deadline)
            self.pipeline.revalidate(plan)
        except ValueError as error:
            if str(error) in ('COLD_PLAN_CHANGED','CANDIDATE_POLICY_OR_CURRENT_CHANGED') and self.replan_stale_paused(plan):
                return dict(state='WAIT_REPLAN',reason='STALE_PAUSED_REPLAN')
            raise
        executor=self.pipeline.executor();owned=executor._owned(plan['snapshot'])
        if owned and owned['state'] in ('ADD_INTENT','UNKNOWN'):
            return executor.reconcile(plan['id'])
        result=self.pipeline.execute(plan['id'],resume=True)
        if result['state']!='RUNNING':return result
        self.checkpoint(deadline)
        progress=executor.sample(plan['id'])
        if progress['status']!='COMPLETED':return dict(state='DOWNLOADING',progress=progress)
        self.checkpoint(deadline)
        if result.get('subtitles',{}).get('assets_state')=='UNVERIFIED':
            if any(w['state']=='UNKNOWN' for w in result['subtitles'].get('workflows',[])):
                return dict(state='WAITING_ASSETS',reason='SUBTITLE_DISPATCH_UNKNOWN')
            if not hasattr(self.pipeline,'service'):return dict(state='WAITING_ASSETS',reason='SUBTITLE_ASSETS_UNVERIFIED')
            from .runtime_assets import observe
            rule=self.delivery.rules[saved['effective']['template']['organized_rule']]
            assets=observe(self.pipeline,plan,seconds=rule.get('stable_seconds',10),entries=self.config.recovery.entries)
            if assets['state']!='VERIFIED':return assets
            self.checkpoint(deadline)
            if assets['assets']:
                snapshot=dict(plan['snapshot'],local_assets=assets['assets'],source_plan=plan['id'])
                snapshot['selected_indices']=snapshot['selected_indices']+[a['file']['index'] for a in assets['assets']]
                self.pipeline.revalidate(plan)
                plan=self.authority.extend_assets(plan['id'],'asset-plan:'+digest(snapshot),snapshot,self.authority.vector(list(snapshot['targets'])))
                return dict(state='ASSET_PLAN_READY',plan_id=plan['id'])
            self.repository.setting('subtitle:'+plan['id'],dict(result['subtitles'],assets_state='VERIFIED_EMPTY',observation=assets['observation']))
        rule_id=saved['effective']['template']['organized_rule'];rule=self.delivery.rules[rule_id]
        selected_indices=active_snapshot(plan)['selected_indices']
        with self.repository.connection() as db:
            completed=all((row:=db.execute('SELECT state FROM organized_assets WHERE plan_id=? AND file_index=?',
                (plan['id'],index)).fetchone()) and row['state']=='COMPLETE' for index in selected_indices)
        # Delivery.prepare still verifies each ORGANIZE receipt and fully hashes the destination.
        if not completed:
            result=self.pipeline.organize(plan['id'],rule['local_root'])
            if result['state']!='COMPLETE':return result
        self.checkpoint(deadline)
        self.verify_input(saved)
        self.checkpoint(deadline)
        plan=self.authority.plan(plan['id']);candidate=self.pipeline.revalidate(plan)
        selected=active_snapshot(plan)
        publication={k:dict(raw=dict(candidate['facts'][k].raw),classification=candidate['classification']) for k in selected['targets']}
        self.delivery.validate_publication(plan,publication)
        self.checkpoint(deadline)
        result=self.delivery.prepare(plan['id'],rule_id,publication=publication,indices=selected['selected_indices'])
        self.persist_scope(result['bundle_id'])
        return result

    @staticmethod
    def ranked(plans):
        plans=sorted(plans,key=lambda p:p['candidate_key'])
        return sorted(plans,key=lambda p:max(tuple(v['quality']) for v in p['targets'].values()),reverse=True)

    def candidate_round(self,saved,rows,deadline=None):
        """Finish one bounded round before choosing; persist progress across ticks."""
        setting='runtime-round:'+saved['opportunity_id'];state=self.repository.setting(setting)
        if not state or state.get('complete'):
            with self.repository.connection() as db:
                previous=[r[0] for r in db.execute('SELECT DISTINCT best_key FROM observations WHERE opportunity_id=?',(saved['opportunity_id'],))]
            keys=list(dict.fromkeys([r['candidate_key'] for r in rows]+previous+(state or {}).get('keys',[])))[:1000]
            state=dict(keys=keys,cursor=0,plans=[],complete=False)
            self.repository.setting(setting,state)
        # A restart/expired acquisition redoes only the affected key, never uses
        # an unavailable saved preference to authorize a weaker/unknown resource.
        stale={p['candidate_key'] for p in state['plans'] if p['candidate_key'] not in self.pipeline.rounds or time.monotonic()-self.pipeline.rounds[p['candidate_key']]['acquired']>300}
        if stale:
            state['keys']=list(dict.fromkeys(state['keys'][state['cursor']:]+sorted(stale)));state['cursor']=0
            state['plans']=[p for p in state['plans'] if p['candidate_key'] not in stale]
        while state['cursor']<len(state['keys']):
            self.checkpoint(deadline);key=state['keys'][state['cursor']]
            try:
                if key not in self.candidates.runtime:self.candidates.refresh(key,self.budget(saved))
                self.checkpoint(deadline);result=self.evaluate(saved,key)
            except ValueError as error:
                if str(error)=='TICK_DEADLINE':raise
                result=dict(plans=[])
            if result.get('enrichments'):
                self.checkpoint(deadline)
                self.delivery.archive.enrich_evidence(saved['opportunity_id'],key,result['enrichments'],result['evidence_manifest'])
                self.repository.setting(setting,dict(complete=True))
                return []
            state['plans']+=result['plans'];state['cursor']+=1
            self.repository.setting(setting,state)
            self.checkpoint(deadline)
        state['complete']=True;self.repository.setting(setting,state)
        return self.ranked(state['plans'])

    def candidate_plan(self,opportunity,saved,snapshot,*,failure_id=None,round_plans=None,immediate=False):
        """Claim/replacement share one exact vector and physical isolation gate."""
        if round_plans is None or snapshot not in round_plans:raise ValueError('COMPLETE_CANDIDATE_ROUND_REQUIRED')
        ranked=self.ranked(round_plans)
        if any(set(p['targets'])&set(snapshot['targets']) and all(
                self.scheduler.ready(opportunity['id'],key,value['action'],immediate=immediate)['ready']
                for key,value in p['targets'].items()) for p in ranked[:ranked.index(snapshot)]):return None
        for candidate in round_plans:
            for key,value in candidate['targets'].items():self.scheduler.observe(opportunity['id'],key,candidate['candidate_key'],value['quality'],eligible=True)
        with self.repository.connection(write=True) as db:
            for key in snapshot['targets']:
                preferred=next(p for p in ranked if key in p['targets'])
                db.execute('UPDATE observations SET best_key=?,best_quality=? WHERE opportunity_id=? AND target_key=?',(preferred['candidate_key'],encoded(preferred['targets'][key]['quality']),opportunity['id'],key))
        if not all(self.scheduler.ready(opportunity['id'],key,value['action'],immediate=immediate)['ready'] for key,value in snapshot['targets'].items()):return None
        if getattr(self,'pipeline',None):self.pipeline.revalidate(dict(snapshot=snapshot))
        self.verify_input(saved);pid='runtime-plan:'+digest([opportunity['id'],snapshot])
        self.authority.prepare(pid,opportunity['id'],snapshot)
        prepared=self.authority.plan(pid)
        if prepared['authorization']!='PREPARED':return None
        vector=self.authority.vector(list(snapshot['targets']));owners={v['owner_plan_id'] for v in vector.values() if v['owner_plan_id']}
        if not owners:self.authority.claim(pid,vector,immediate=immediate)
        else:
            progress={};now=instant()
            for old_id in owners:
                old=self.authority.plan(old_id);before=old['snapshot']
                if before['downloader']==snapshot['downloader'] and before['save_path']==snapshot['save_path']:
                    if before['infohash']==snapshot['infohash']:
                        if before['torrent_files']!=snapshot['torrent_files']:raise ValueError('SHARED_TABLE_CHANGED')
                    elif {f['path'] for f in before['torrent_files']}&{f['path'] for f in snapshot['torrent_files']}:
                        raise ValueError('REPLACEMENT_LAYOUT_COLLISION')
                affected=sorted(k for k,v in vector.items() if v['owner_plan_id']==old_id)
                downloading=any(t['target_key'] in affected and t['state']=='ACTIVE' and t['transfer_phase']=='DOWNLOADING' for t in old['targets'])
                if downloading:
                    self.refresh_plan(old,self.repository.setting('runtime-input:'+old['opportunity_id']))
                    measured=self.pipeline.executor().sample(old_id,targets=affected)
                    now=parse(measured['sampled_at']);progress[old_id]=measured
            self.verify_input(saved)
            if failure_id:self.authority.recover(pid,vector,failure_id=failure_id,safe_isolation=True,now=now)
            else:self.authority.supersede(pid,vector,reason='VERIFIED_BETTER_CANDIDATE',safe_isolation=True,now=now,progress=progress,immediate=immediate)
        return self.authority.plan(pid)

    def work(self,opportunity,*,deadline=None,immediate=False):
        self.checkpoint(deadline)
        saved=self.repository.setting('runtime-input:'+opportunity['id'])
        if not saved:raise ValueError('ORIGINAL_RUNTIME_INPUT_REQUIRED')
        row=self.verify_input(saved)
        self.checkpoint(deadline)
        if opportunity['mode']=='ONESHOT' and (instant()-parse(saved['created_at'])).total_seconds()>saved['effective']['lifecycle']['oneshot_seconds']:
            with self.repository.connection(write=True) as db:
                for p in db.execute("SELECT id FROM plans WHERE opportunity_id=? AND authorization='ACTIVE'",(opportunity['id'],)).fetchall():
                    plan=self.authority._plan(db.execute('SELECT * FROM plans WHERE id=?',(p['id'],)).fetchone())
                    vector=self.authority._vector(db,list(plan['snapshot']['targets']))
                    self.authority.cancel(plan['id'],vector,reason='ONESHOT_EXPIRED',db=db)
                db.execute("UPDATE opportunities SET state='ARCHIVED',updated_at=? WHERE id=?",(utcnow(),opportunity['id']))
            return dict(state='ARCHIVED',reason='ONESHOT_EXPIRED')
        with self.repository.connection() as db:
            active=db.execute("SELECT id FROM plans WHERE opportunity_id=? AND authorization IN ('ACTIVE','PREPARED') ORDER BY authorization!='ACTIVE',created_at,id LIMIT 1",(opportunity['id'],)).fetchone()
        if active:
            plan=self.authority.plan(active['id'])
            if plan['authorization']=='PREPARED':
                self.recover(plan,saved)
                self.authority.claim(plan['id'],self.authority.vector(list(plan['snapshot']['targets'])),immediate=immediate)
                plan=self.authority.plan(plan['id'])
            if any(t['publish_phase']!='NOT_SENT' for t in self.authority.vector(list(plan['snapshot']['targets'])).values()):
                return dict(state='DELIVERY_PENDING',plan_id=plan['id'])
            if self.inventory(Target.from_task(row),template_id=saved['effective']['template']['id'],deadline=deadline)['state']=='UNKNOWN':return dict(state='WAIT_INVENTORY')
            self.checkpoint(deadline)
            failure_id=None
            executor=self.pipeline.executor();owned=executor._owned(plan['snapshot'])
            if owned and owned.get('client_id'):
                try:self.refresh_plan(plan,saved)
                except ValueError as error:
                    if str(error) in ('COLD_PLAN_CHANGED','CANDIDATE_POLICY_OR_CURRENT_CHANGED') and self.replan_stale_paused(plan):
                        return dict(state='WAIT_REPLAN',reason='STALE_PAUSED_REPLAN')
                    raise
                self.checkpoint(deadline)
                progress=executor.sample(plan['id'])
                if progress['status']=='FAILED':
                    failure_id='runtime-failure:'+digest([plan['id'],'EXECUTION_FAILED'])
                    self.scheduler.record_failure(opportunity['id'],failure_id,'EXECUTION_FAILED')
            schedule=saved['effective']['schedule']
            if failure_id or schedule['supersession_limit']:
                words=saved['scope']['keywords'][:saved['effective']['candidates']['keywords']]
                plans=self.candidate_round(saved,self.search(saved,words),deadline)
                if failure_id:plans=[p for p in plans if p['candidate_key']!=plan['snapshot']['candidate_key']]
                for snapshot in plans:
                    if snapshot['candidate_key']==plan['snapshot']['candidate_key']:continue
                    self.checkpoint(deadline)
                    try:replacement=self.candidate_plan(opportunity,saved,snapshot,failure_id=failure_id,round_plans=plans,immediate=immediate)
                    except ValueError as error:
                        self.repository.setting('runtime-replacement:'+opportunity['id'],dict(reason=self.reason(error),at=utcnow()));continue
                    if replacement:return self.advance(replacement,saved,deadline=deadline)
            if failure_id:return dict(state='WAIT_REPLACEMENT',reason='EXECUTION_FAILED')
            return self.advance(plan,saved,deadline=deadline)
        inventory=self.inventory(Target.from_task(row),template_id=saved['effective']['template']['id'],deadline=deadline)
        self.checkpoint(deadline)
        if inventory['state']=='UNKNOWN':return dict(state='WAIT_INVENTORY',reason=inventory['diagnostics'])
        scope=dict(self.scope(Target.from_task(row)),units=saved['scope']['units']);facts=self.delivery.archive.current(scope['units'])
        if opportunity['mode']=='CONTINUOUS':self.scheduler.update_completion(row['id'],scope['units'],scope_closed=scope['scope_closed'],collected=all(v['state']=='PRESENT' for v in facts.values()))
        words=scope['keywords'] if saved['effective']['candidates']['query_scope']=='trusted_aliases' else scope['keywords'][:1]
        if not words:raise ValueError('PROVIDER_SEARCH_NAME_UNAVAILABLE')
        arrivals=self.repository.setting('runtime-arrivals:'+opportunity['id']) or []
        rows=[]
        for key in arrivals[:saved['effective']['candidates']['supplement_limit']]:
            try:self.candidates.refresh(key,self.budget(saved));rows.append(dict(candidate_key=key))
            except ValueError:continue
        lifecycle=self.scheduler.lifecycle(row['id'])
        expired=lifecycle and lifecycle['expires_at'] and instant()>=parse(lifecycle['expires_at'])
        if not (expired and opportunity['mode']=='CONTINUOUS' and all(v['state']=='PRESENT' for v in facts.values())):
            rows+=self.search(saved,words)
        plans=self.candidate_round(saved,rows,deadline)
        if self.scheduler.opportunity(opportunity['id'])['state']!='ACTIVE':return dict(state='EVIDENCE_CONFIRMED')
        for snapshot in plans:
            self.checkpoint(deadline)
            claimed=self.candidate_plan(opportunity,saved,snapshot,round_plans=plans,immediate=immediate)
            if claimed:return self.advance(claimed,saved,deadline=deadline)
        return dict(state='WAITING',reason='SITE_SEARCH_FAILED' if self.candidates.search_errors else 'NO_READY_CANDIDATE')

    async def tick(self):
        with self.lock:
            if self.busy:return dict(state='BUSY')
            self.busy=True
        try:return await self.stages.run(self._tick)
        finally:
            with self.lock:self.busy=False

    def _tick(self):
        self.deadline=time.monotonic()+self.config.recovery.seconds
        self.candidates.deadline=self.deadline
        try:return self._run_tick()
        finally:self.deadline=None;self.candidates.deadline=None

    def _run_tick(self):
        deadline=time.monotonic()+self.config.recovery.seconds;results=[]
        self.owner.ensure_paused(limit=self.config.recovery.entries);self.scheduler.tick(limit=self.config.recovery.entries)
        try:self.check()
        except ValueError:return dict(state='SAFETY_ONLY',results=self.safety(deadline))
        lane=self.repository.setting('runtime-lane') or 0
        self.repository.setting('runtime-lane',(lane+1)%7)
        if lane==0:return dict(state='SAFETY',results=self.safety(deadline))
        if lane in (2,3) and self.pipeline:
            from .runtime_passive import Passive
            passive=Passive(self)
            try:result=(passive.scan if lane==2 else passive.rss)(deadline)
            except Exception as error:result=dict(state='DEFER',reason=self.reason(error))
            return dict(state='PASSIVE',results=[result])
        if lane==4 and self.policy:return dict(state='REPROFILE',results=[self.reprofile()])
        if lane==5 and self.delivery:
            return dict(state='MAINTENANCE',results=[self.delivery.local_maintenance(entries=self.config.recovery.entries,deadline=deadline)])
        if lane==6 and self.pipeline:
            try:result=self.late_assets(deadline)
            except Exception as error:result=dict(state='DEFER',reason=self.reason(error))
            return dict(state='SUBTITLES',results=[result])
        self.owner.reconcile()
        self.bootstrap(deadline)
        cursor=self.repository.setting('runtime-opportunity-cursor') or ''
        with self.repository.connection() as db:
            rows=[dict(r) for r in db.execute("SELECT o.* FROM opportunities o JOIN tasks t ON t.id=o.task_id WHERE o.state='ACTIVE' AND t.state IN ('ACTIVE','PASSIVE') AND o.id>? ORDER BY o.id LIMIT ?",(cursor,self.config.recovery.entries))]
        if not rows:self.repository.setting('runtime-opportunity-cursor','')
        for opportunity in rows:
            if time.monotonic()>=deadline:break
            try:result=self.work(opportunity,deadline=deadline)
            except Exception as error:result=dict(state='DEFER',reason=self.reason(error))
            results.append(dict(opportunity_id=opportunity['id'],**result))
            self.repository.setting('runtime-result:'+opportunity['id'],dict(result,at=utcnow()))
            self.repository.setting('runtime-opportunity-cursor',opportunity['id'])
        return dict(state='CHECKED',results=results)

    def late_assets(self,deadline):
        """One issued resource per pass; late sidecars use the common admission."""
        from .runtime_assets import observe
        cursor=self.repository.setting('runtime-subtitle-cursor') or ''
        with self.repository.connection() as db:
            row=db.execute("SELECT p.id FROM plans p JOIN tasks t ON t.id=p.task_id WHERE p.authorization='COMPLETED' AND t.state IN ('ACTIVE','PASSIVE') AND p.id>? ORDER BY p.id LIMIT 1",(cursor,)).fetchone()
        if not row:self.repository.setting('runtime-subtitle-cursor','');return dict(state='SUBTITLE_IDLE')
        plan=self.authority.plan(row['id']);s=plan['snapshot'];saved=self.repository.setting('runtime-input:'+plan['opportunity_id'])
        if not saved:raise ValueError('ORIGINAL_RUNTIME_INPUT_REQUIRED')
        self.verify_input(saved);self.checkpoint(deadline)
        rule=self.delivery.rules[saved['effective']['template']['organized_rule']]
        observed=observe(self.pipeline,plan,seconds=rule.get('stable_seconds',10),entries=self.config.recovery.entries)
        self.repository.setting('runtime-subtitle-cursor',row['id'])
        if observed['state']!='VERIFIED':return observed
        with self.repository.connection() as db:
            versions=[json.loads(v['data']) for key in s['targets'] for v in self.delivery.archive._live_versions(db,key)]
        pending=[a for a in observed['assets'] if not all(any(old['role']=='subtitle' and old['content']==dict(sha1=a['sha1'],size=a['file']['size']) for old in v.get('assets',[])) for v in versions if v['target_key'] in a['file']['targets'])]
        if not pending:return dict(state='SUBTITLE_CURRENT')
        for index,asset in enumerate(pending,len(s['torrent_files'])):asset['file']['index']=index
        self.repository.setting('subtitle-candidate:'+s['candidate_key'],dict(source_plan=plan['id'],assets=pending,source_digest=digest([s[k] for k in ('infohash','torrent_files','downloader','save_path')])))
        self.checkpoint(deadline);self.candidates.refresh(s['candidate_key'],self.budget(saved));self.checkpoint(deadline)
        result=self.evaluate(saved,s['candidate_key'])
        approved=sorted({k for p in result['plans'] for k,v in p['targets'].items() if v['action']=='SIDECAR_SUPPLEMENT'})
        if not approved:return dict(state='NO_SUBTITLE_IMPROVEMENT')
        task=self.repository.get_task(plan['task_id'])
        with self.repository.connection() as db:active=db.execute("SELECT id FROM opportunities WHERE task_id=? AND state='ACTIVE'",(task['id'],)).fetchone()
        if active:opportunity=active['id']
        else:
            baseline=self.delivery.archive.current(approved)
            intent='subtitle:'+digest([s['candidate_key'],pending,[(k,v['archive_revision']) for k,v in baseline.items()]])
            row=self.submit(intent,Target.from_task(task),saved.get('native',{}),'subtitle',template_id=saved['effective']['template']['id'],mode='ONESHOT',approved_units=approved)
            with self.repository.connection() as db:active=db.execute("SELECT id FROM opportunities WHERE task_id=? AND state='ACTIVE'",(row['id'],)).fetchone()
            if not active:return dict(state='SUBTITLE_ADMISSION_PENDING')
            opportunity=active['id']
        from .runtime_passive import Passive
        Passive(self).queue(opportunity,s['candidate_key'])
        return dict(state='SUBTITLE_QUEUED',opportunity_id=opportunity)

    def reprofile(self):
        """Bounded stored-fact re-evaluation; no remote media or lifecycle writes."""
        from .planner import quality_locks_for_scope
        revision=digest([self.policy.semantic_hash,self.policy.classification_revision,self.config.policy.locks])
        state=self.repository.setting('runtime-reprofile') or {}
        if state.get('revision')!=revision:state=dict(revision=revision,phase='ARCHIVE',cursor='')
        if state['phase']=='COMPLETE':return dict(state='POLICY_CURRENT')
        table,column=('archive_versions','id') if state['phase']=='ARCHIVE' else ('candidates','candidate_key')
        with self.repository.connection() as db:
            rows=[dict(r) for r in db.execute(f'SELECT {column},data FROM {table} WHERE {column}>? ORDER BY {column} LIMIT ?',(state['cursor'],self.config.recovery.entries))]
        writes=[]
        for row in rows:
            self.check();data=json.loads(row['data']);raw=data['raw'] if table=='archive_versions' else data
            facts=self.policy.normalize(raw,current=table=='archive_versions');classification=data.get('classification',{})
            # Raw reprofile rows do not prove physical episode scope. Leave a
            # season-constrained decision to the candidate/plan scope gate.
            try:locks=quality_locks_for_scope(self.config.policy.locks,[])
            except ValueError as error:
                decision=self.policy._decision('DEFER' if str(error)=='SEASON_SCOPE_UNCONFIRMED' else 'ERROR',str(error))
            else:decision=self.policy.admit(facts,classification,locked=locks,identity_ok=True,scope_ok=True)
            writes.append(('runtime-policy:'+table+':'+row[column],json.dumps(dict(revision=revision,status=decision.status,reason=decision.reason,rank=list(decision.rank),admission_only=True))))
            state['cursor']=row[column]
        if len(rows)<self.config.recovery.entries:state.update(phase='CANDIDATES' if state['phase']=='ARCHIVE' else 'COMPLETE',cursor='')
        writes.append(('runtime-reprofile',json.dumps(state)))
        with self.repository.connection(write=True) as db:
            db.executemany('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',writes)
        return dict(state='POLICY_REPROFILE',phase=state['phase'],checked=len(rows))

    @staticmethod
    def reason(error):
        import re
        return str(error) if isinstance(error,ValueError) and re.fullmatch('[A-Z_0-9]{1,100}',str(error)) else 'RUNTIME_STAGE_FAILED'

    def bootstrap(self,deadline):
        cursor=self.repository.setting('runtime-task-cursor') or 0
        rows=self.repository.list_tasks(self.config.recovery.entries,cursor)
        for row in rows:
            if time.monotonic()>=deadline:break
            if row['state'] not in ('ACTIVE','PASSIVE'):continue
            frozen=self.repository.setting('runtime-task:'+str(row['id']))
            if not frozen:continue
            with self.repository.connection() as db:
                active=db.execute("SELECT id FROM opportunities WHERE task_id=? AND state='ACTIVE'",(row['id'],)).fetchone()
                finished=db.execute("SELECT id FROM opportunities WHERE task_id=? AND state IN ('COMPLETED','ARCHIVED') ORDER BY created_at DESC LIMIT 1",(row['id'],)).fetchone()
            finished_input=self.repository.setting('runtime-input:'+finished['id']) if finished else None
            if not active and finished and finished_input and finished_input.get('admission_id')==frozen.get('admission_id'):
                # Existing clocks and opportunity budgets survive settlement.
                # A new ONESHOT is admitted only by a newly valuable arrival.
                if frozen['effective']['mode']=='CONTINUOUS':
                    previous=self.repository.setting('runtime-input:'+finished['id'])
                    if previous and previous['config_digest']==digest(frozen['effective']):
                        with self.repository.connection(write=True) as db:
                            db.execute("UPDATE opportunities SET state='ACTIVE' WHERE id=? AND state='COMPLETED'",(finished['id'],))
                continue
            if not active or not self.repository.setting('runtime-input:'+active['id']):
                scope=self.scope(Target.from_task(row),fresh=True);template=self.destination(scope,frozen['effective']['template']['id'])
                if frozen['effective']['mode']=='ONESHOT':
                    if scope['units']!=frozen['scope'].get('provider_units',frozen['scope']['units']):continue
                    scope=frozen['scope']
                if digest(self.effective(template,frozen['effective']['mode'],
                                         completed_mode=frozen['effective']['lifecycle']['completed_mode'] if frozen.get('completed_mode_override') else None))!=digest(frozen['effective']):continue
                attached=self.attach(row,scope,template,mode=frozen['effective']['mode'])
                arrivals=self.repository.setting('runtime-pending-arrivals:'+str(row['id']))
                if arrivals:self.repository.setting('runtime-arrivals:'+attached['opportunity_id'],arrivals)
        self.repository.setting('runtime-task-cursor',cursor+len(rows) if len(rows)==self.config.recovery.entries else 0)
        if self.config.auto_types and time.monotonic()<deadline:
            adapter=self.owner.adapter
            after=self.repository.setting('runtime-native-cursor') or 0
            natives=sorted(adapter.list(),key=lambda x:x['id'])
            selected=[r for r in natives if r['id']>after][:self.config.recovery.entries]
            for native in selected:
                if time.monotonic()>=deadline:break
                if self.plugin._auto_scope(native) and not self.repository.by_native_id(native['id']):
                    target=Target(native['type'],native['media_source'],str(native['media_id']),native.get('season'),native.get('episode_group') or '')
                    self.submit('native:'+str(native['id']),target,native,'automatic',native['id'],True)
                self.repository.setting('runtime-native-cursor',native['id'])
            if not selected:self.repository.setting('runtime-native-cursor',0)

    def persist_scope(self,bundle_id):
        bundle=self.delivery.bundle(bundle_id)
        if not self.repository.setting('delivery-scope:'+bundle_id):
            self.bind_scope(bundle_id,self.config.delivery,bundle['revision'],'runtime',ordinary=True)

    @staticmethod
    def scope_config(config,rule):
        mappings=[r for r in config['mappings'] if r['cloud_scope_id']==rule['cloud_scope_id']]
        if not mappings:raise ValueError('ORIGINAL_SCOPE_REQUIRED')
        return dict(cloud_scopes={rule['cloud_scope_id']:config['cloud_scopes'][rule['cloud_scope_id']]},
            mappings=mappings,rules=[next(r for r in config['rules'] if r['id']==rule['id'])],
            libraries={s:sorted({str(r['library_id']) for r in mappings if r['emby_service']==s}) for s in {r['emby_service'] for r in mappings}},
            policy_bindings=config['policy_bindings'],classification_revision=config['classification_revision'])

    def bind_scope(self,bundle_id,config,revision,actor,*,ordinary=False):
        """Exact historical scope recovery; no file paths or host writes accepted."""
        from .configuration import DeliveryConfig
        from .delivery import validate_rules
        from .archive import within
        if ordinary:self.check()
        elif self.plugin._ordinary_work_active():raise ValueError('DISABLE_ORDINARY_WORK_FIRST')
        if len(encoded(config))>524288:raise ValueError('SCOPE_LIMIT')
        validation=json.loads(encoded(config))
        # Only this historical scope import accepts a bounded decimal string;
        # ordinary configuration remains strict and the original hash is retained.
        import re
        for rule in validation.get('rules',[]):
            scalar=rule.get('fallback_gb')
            if isinstance(scalar,str) and re.fullmatch(r'[0-9]{1,6}(?:\.[0-9]{1,12})?',scalar):rule['fallback_gb']=float(scalar)
        DeliveryConfig.model_validate(validation)
        with self.repository.connection() as db:
            row=db.execute('SELECT * FROM delivery_bundles WHERE id=?',(bundle_id,)).fetchone()
        if not row or row['revision']!=revision:raise ValueError('STALE_BUNDLE')
        bundle=json.loads(row['data']);rule=validate_rules(config['rules']).get(bundle['rule_id'])
        if not rule or rule['transfer_revision']!=bundle['rule_transfer_revision']:raise ValueError('ORIGINAL_SCOPE_REQUIRED')
        if bundle['staging']!=rule['staging_root']+'/'+bundle_id or bundle['incoming']!=rule['incoming_root']+'/'+bundle_id:
            raise ValueError('ORIGINAL_SCOPE_CONFLICT')
        for f in bundle['files']:
            from pathlib import Path
            if not Path(f['snapshot']['path']).is_relative_to(Path(rule['local_root'])):raise ValueError('ORIGINAL_SCOPE_CONFLICT')
        frozen=self.scope_config(config,rule)
        identity=digest([bundle['plan_id'],bundle['manifest'],bundle['rule_transfer_revision'],bundle['staging'],bundle['incoming']])
        saved=dict(identity=identity,config=frozen,digest=digest(frozen))
        key='delivery-scope:'+bundle_id;old=self.repository.setting(key)
        if old and old!=saved:raise ValueError('ORIGINAL_SCOPE_ALREADY_BOUND')
        with self.repository.connection(write=True) as db:
            actual=db.execute('SELECT revision FROM delivery_bundles WHERE id=?',(bundle_id,)).fetchone()
            if not actual or actual[0]!=revision:raise ValueError('STALE_BUNDLE')
            db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',(key,encoded(saved)))
            if not old:self.repository._audit(db,None,'DELIVERY_SCOPE_BOUND:'+bundle_id,actor)
        return dict(bundle_id=bundle_id,scope_digest=saved['digest'],state='BOUND',ordinary_dispatch=False)

    def scope_worker(self,bundle_id):
        from .host_delivery_contract import build_delivery
        with self.repository.connection() as db:
            row=db.execute('SELECT data FROM delivery_bundles WHERE id=?',(bundle_id,)).fetchone()
        if not row:raise ValueError('BUNDLE_UNKNOWN')
        bundle=json.loads(row[0]);saved=self.repository.setting('delivery-scope:'+bundle_id)
        if not saved and self.delivery:
            rule=self.delivery.rules.get(bundle['rule_id'])
            if rule and rule['transfer_revision']==bundle['rule_transfer_revision']:
                # Current exact scope is evidence; unrelated old root fixtures are not.
                self.bind_scope(bundle_id,self.config.delivery,self.delivery.bundle(bundle_id)['revision'],'scope-recovery',ordinary=self.plugin._ordinary_work_active())
                saved=self.repository.setting('delivery-scope:'+bundle_id)
        if not saved:raise ValueError('ORIGINAL_SCOPE_REQUIRED')
        if saved['identity']!=digest([bundle['plan_id'],bundle['manifest'],bundle['rule_transfer_revision'],bundle['staging'],bundle['incoming']]) or saved['digest']!=digest(saved['config']):
            raise ValueError('ORIGINAL_SCOPE_CONFLICT')
        if self.delivery:
            rule=self.delivery.rules.get(bundle['rule_id'])
            if rule and rule['transfer_revision']==bundle['rule_transfer_revision'] and digest(self.scope_config(self.config.delivery,rule))==saved['digest']:return self.delivery
        if saved['digest'] not in self.scope_workers:
            worker=build_delivery(self.plugin,saved['config'])
            worker.archive.scope_provider=lambda target:self.scope(target,fresh=True)
            worker.archive.sources.checkpoint=lambda:self.checkpoint(getattr(self,'deadline',None))
            worker.archive.sources.safety_checkpoint=worker.archive.sources.checkpoint
            def safety_only():raise ValueError('ORIGINAL_SCOPE_SAFETY_ONLY')
            worker.archive.sources.ordinary_checkpoint=safety_only
            worker.dispatch_gate=safety_only;self.scope_workers[saved['digest']]=worker
        return self.scope_workers[saved['digest']]

    def consumer(self,bundle_id):
        with self.safety_reads():return self._consumer(bundle_id)

    def _consumer(self,bundle_id):
        from .runtime_delivery import observe_consumer
        worker=self.scope_worker(bundle_id);bundle=worker.bundle(bundle_id)
        if not bundle.get('publication_action'):raise ValueError('PUBLICATION_REQUIRED')
        receipt=observe_consumer(worker,bundle,lambda target:self.scope(target,fresh=True),entries=self.config.recovery.entries,
            pages=self.config.recovery.pages,seconds=self.config.recovery.seconds,deadline=getattr(self,'deadline',None))
        if receipt is None:return dict(state='WAIT_CONSUMER',bundle_id=bundle_id,reason='CONSUMER_OBSERVATION_INCOMPLETE')
        self.checkpoint(getattr(self,'deadline',None))
        return worker.confirm(bundle_id,receipt)

    def safety(self,deadline):
        deadline=min(deadline,getattr(self,'deadline',None) or deadline)
        results=[];cursor=self.repository.setting('runtime-bundle-cursor') or ''
        plan_cursor=self.repository.setting('runtime-safety-plan-cursor') or ''
        with self.repository.connection() as db:
            bundles=[dict(r) for r in db.execute("SELECT id FROM delivery_bundles WHERE state NOT IN ('CONFIRMED','CANCELLED','CLEANED') AND id>? ORDER BY id LIMIT ?",(cursor,self.config.recovery.entries))]
            plans=[dict(r) for r in db.execute("SELECT DISTINCT p.id FROM plans p JOIN plan_actions a ON a.plan_id=p.id WHERE p.id>? AND a.state IN ('UNKNOWN','IN_FLIGHT') AND a.kind IN ('ADD','SET_WANTED','RESUME','ORGANIZE') ORDER BY p.id LIMIT ?",(plan_cursor,self.config.recovery.entries))]
        if not bundles:self.repository.setting('runtime-bundle-cursor','')
        if not plans:self.repository.setting('runtime-safety-plan-cursor','')
        for row in bundles:
            if time.monotonic()>=deadline:break
            try:
                worker=self.scope_worker(row['id'])
                bundle=worker.bundle(row['id'])
                try:self.check();ordinary=worker is self.delivery and worker._rule(bundle)['enabled']
                except ValueError:ordinary=False
                self.checkpoint(deadline)
                limits=dict(seconds=min(self.config.recovery.seconds,deadline-time.monotonic()))
                # An authorized reconcile already observes the original ID and
                # serves its requests. A preceding observe-only stream can use
                # the entire tick while the provider waits for those bytes.
                if ordinary and not bundle.get('publication_action'):
                    result=worker.reconcile(row['id'],limits=limits)
                    if result['state']=='REMOTE_VERIFIED':result=worker.publish(row['id'])
                else:
                    with self.safety_reads():result=worker.safety_reconcile(row['id'],limits=limits)
                    if (ordinary and bundle.get('sidecar_destinations')
                            and result['state'] in ('PUBLISHING','PUBLISH_OUTCOME_UNKNOWN')):
                        self.checkpoint(deadline);result=worker.publish(row['id'])
                if bundle.get('publication_action'):result=self.consumer(row['id'])
            except Exception as error:result=dict(state='DEFER',reason=self.reason(error),bundle_id=row['id'])
            results.append(result);self.repository.setting('runtime-bundle-result:'+row['id'],dict(result,at=utcnow()))
            self.repository.setting('runtime-bundle-cursor',row['id'])
        from .execution import StrictExecutor,Organizer,HostOrganization
        for row in plans:
            if time.monotonic()>=deadline:break
            try:
                # This path only observes a previously issued exact client ID.
                def no_dispatch(plan):raise ValueError('SAFETY_RECONCILIATION_ONLY')
                executor=StrictExecutor(self.repository,self.clients,revalidate=no_dispatch)
                with self.repository.connection() as db:
                    kinds={r[0] for r in db.execute("SELECT kind FROM plan_actions WHERE plan_id=? AND state IN ('UNKNOWN','IN_FLIGHT')",(row['id'],))}
                if kinds&{'ADD','SET_WANTED','RESUME'}:result=executor.reconcile(row['id'])
                else:
                    # History/content readbacks do not require a media object;
                    # the rejecting validator also prohibits history repair.
                    result=Organizer(executor,HostOrganization(None,repository=self.repository)).reconcile(row['id'])
            except Exception as error:result=dict(state='DEFER',reason=self.reason(error))
            results.append(dict(plan_id=row['id'],**result))
            self.repository.setting('runtime-safety-plan-cursor',row['id'])
        return results
