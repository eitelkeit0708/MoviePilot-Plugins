"""Private legacy import and resumable selective cutover; never invokes host writes."""
from copy import deepcopy
import json
from uuid import uuid4
from urllib.parse import urlsplit, urlencode

from .ai import digest, migrate_legacy, legacy_preview, owner_projection, owner_receipt
from .configuration import merge, contains_private
from .discovery import DiscoveryConfig, import_legacy, _digest as discovery_digest
from .meta import _stored
from .repository import utcnow, snapshot_config

LEGACY_STATUSES=('未识别','已识别未分类','年份不符合','评分不符合','媒体库已存在','订阅已存在','已添加订阅')
AI_FIELDS=dict(enabled='ai_assist.enabled',recognize='ai_assist.name_assistance_enabled',
    openai_url='ai_assist.endpoint_ref',openai_key='ai_assist.credential_refs',model='ai_assist.model',
    request_profile='ai_assist.profile',compatible='ai_assist.compatible',proxy='ai_assist.proxy',
    customize_prompt='ai_assist.prompt',previous_customize_prompt='ai_assist.prompt_backup',
    restore_prompt='action.prompt_restore',clear_cache='action.cache_clear',timeout='ai_assist.timeout',
    max_attempts='ai_assist.max_attempts',max_concurrency='ai_assist.max_concurrency',positive_ttl='ai_assist.positive_ttl',
    negative_ttl='ai_assist.negative_ttl',cache_size='ai_assist.cache_size',notify='ai_assist.notifications',
    statistics='historical.statistics')
DISCOVERY_FIELDS=dict(enabled='discovery.enabled',ranks='discovery.sources',rss_addrs='discovery.sources',cron='discovery.cron',
    onlyonce='action.run_once',proxy='discovery.sources.proxy',sleep_time='discovery.request_budget',
    is_exit_ip_rate_limit='discovery.origin_cooldown',vote='discovery.minimum_rating',release_year='discovery.minimum_release_year',
    is_only_movies='discovery.media_type_allowlist',is_seasons_all='discovery.season_scope',history_type='history_view',
    clear='action.history_cleanup',clear_unrecognized='action.history_cleanup',delete_history='action.history_cleanup',
    migrate_from_url='migration.read_only_source_ref',migrate_api_token='migration.credential_ref',migrate_once='action.import')
HISTORY_FIELDS=('title','type','year','poster','overview','tmdbid','doubanid','unique','time','time_full','vote','status')
ACTIONS={'restore_prompt','clear_cache','onlyonce','clear','clear_unrecognized','delete_history','migrate_once'}
MODULES={'name_assistance','name_bridge','discovery'}


def capabilities(plugin):
    return digest({k:plugin.get(k) for k in ('source','version','prefix','api_paths','commands')})


LEGACY_READ_SECONDS=15


async def read_legacy_source(base,token,instance,ranges):
    """Own both fixed GET streams under one safety/dispatch/body deadline.

    Direct public transport avoids AsyncClient's query-bearing request log.
    Cancellation closes response and transport before this coroutine returns.
    """
    import asyncio
    from contextlib import aclosing
    import httpx
    from .discovery import HostRSSFetcher
    checker=HostRSSFetcher(base,allowed_private_ranges=ranges)
    deadline=asyncio.get_running_loop().time()+LEGACY_READ_SECONDS
    try:
        async with asyncio.timeout_at(deadline):
            await checker._safe(base,base)
            result=[]
            async with httpx.AsyncHTTPTransport(verify=True,trust_env=False,proxy=None,retries=0) as transport:
                for route in ('migrate-config','migrate-history'):
                    remaining=deadline-asyncio.get_running_loop().time()
                    if remaining<=0:raise ValueError('LEGACY_READ_TIMEOUT')
                    url=base+'/api/v1/plugin/'+instance+'/'+route+'?'+urlencode({'migrate_api_token':token})
                    request=httpx.Request('GET',url,headers={'Accept-Encoding':'identity'},
                        extensions={'timeout':{k:remaining for k in ('connect','read','write','pool')}})
                    response=await transport.handle_async_request(request)
                    async with aclosing(response):
                        if response.status_code!=200 or response.headers.get('Content-Encoding','identity').lower()!='identity':
                            raise ValueError('LEGACY_READ_HTTP')
                        body=bytearray()
                        async for block in response.aiter_raw():
                            body.extend(block)
                            if len(body)>2097152:raise ValueError('LEGACY_READ_LIMIT')
                            if asyncio.get_running_loop().time()>=deadline:raise ValueError('LEGACY_READ_TIMEOUT')
                        result.append(bytes(body))
                return result
    except Exception:raise ValueError('LEGACY_READ_FAILED') from None


def flag(value):return value.strip().lower() in {'true','1','yes','on'} if isinstance(value,str) else bool(value)


def safe(value,secrets=()):
    if isinstance(value,str):
        for secret in sorted((s for s in secrets if s),key=len,reverse=True):value=value.replace(secret,'[PRIVATE]')
        return _stored(value)
    if isinstance(value,list):return [safe(x,secrets) for x in value]
    if isinstance(value,dict):return {safe(str(k),secrets):safe(v,secrets) for k,v in value.items()}
    if any(contains_private(value,s) for s in secrets if s):return '[PRIVATE]'
    return value


def history_row(row,ordinal,reference,timezone,secrets):
    identities={};diagnostics=[]
    for field,source in (('tmdbid','themoviedb'),('doubanid','douban')):
        raw=row.get(field);value=str(raw).strip() if raw is not None else ''
        if (value.isascii() and value.isdecimal() and len(value)<=32 and int(value)>0
                and not any(contains_private(value,s) for s in secrets if s)):identities[source]=value
        else:diagnostics.append(field+':INVALID_OR_MISSING_ID')
    if not timezone:diagnostics.append('TIMEZONE_UNKNOWN')
    if row.get('year') in (0,'0','',None):diagnostics.append('YEAR_UNKNOWN')
    if row.get('vote') in (0,'0','',None):diagnostics.append('RATING_UNKNOWN')
    if row.get('status') not in LEGACY_STATUSES:diagnostics.append('UNKNOWN_LEGACY_STATUS')
    diagnostics.extend('UNMAPPED:'+str(k) for k in row if k not in HISTORY_FIELDS)
    return dict(ordinal=ordinal,digest=digest(row),snapshot_ref=reference,raw_pointer='/history/'+str(ordinal),
        raw=safe({k:row.get(k) for k in HISTORY_FIELDS},secrets),identities=identities,season=None,
        source_timezone=timezone,time_basis='LEGACY_REPORTED',state='LEGACY_UNVERIFIED',diagnostics=safe(diagnostics,secrets))


def normalize(raw,store,timezone=None,private_context=()):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('DUPLICATE_LEGACY_KEY')
            result[key]=value
        return result
    try:
        data=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError,RecursionError):raise ValueError('LEGACY_JSON_INVALID') from None
    if not isinstance(data,dict) or set(data)-{'ai','discovery','history','native','policy','statistics','chat_history'}:raise ValueError('LEGACY_EXPORT_SHAPE')
    ai=data.get('ai',{});old=data.get('discovery',{});rows=data.get('history',[])
    if not isinstance(ai,dict) or not isinstance(old,dict) or not isinstance(rows,list) or len(rows)>10000 or any(not isinstance(x,dict) for x in rows):raise ValueError('LEGACY_EXPORT_SHAPE')
    secrets=[str(ai.get(k) or '') for k in ('openai_url','openai_key')]+[str(old.get(k) or '') for k in ('migrate_from_url','migrate_api_token')]
    secrets.extend(k.strip() for k in str(ai.get('openai_key') or '').split(',') if k.strip())
    secrets.extend(store.resolve(ref) for ref in private_context)
    fields=[];diagnostics=[];patch={};private_refs={};policy_candidates={}
    for source,values,mapping in (('ai',ai,AI_FIELDS),('discovery',old,DISCOVERY_FIELDS)):
        for key in sorted(set(values)|set(mapping)):
            fields.append(dict(source=source,field=key,target=mapping.get(key,'protected_raw'),
                status='MISSING' if key not in values else 'ACTION_NOT_REPLAYED' if key in ACTIONS else 'UNMAPPED_RETAINED' if key not in mapping else 'MAPPED',
                raw_pointer='/'+source+'/'+key))
    if ai:
        patch['ai_assist']=migrate_legacy(ai,store.put).model_dump()
        if 'model' in ai:
            if not isinstance(ai['model'],str):raise ValueError('LEGACY_MODEL_INVALID')
            patch['ai_assist']['model']=ai['model']
        # Raw key slots and original endpoint/prompt bytes remain in private raw
        # snapshot; effective usable keys preserve order and duplicate diagnosis.
        slots=[k.strip() for k in str(ai.get('openai_key') or '').split(',')]
        if len(slots)!=len(set(slots)):diagnostics.append('DUPLICATE_KEY_SLOTS_RETAINED_RAW_EFFECTIVE_DEDUPLICATED')
        private_refs['ai_endpoint']=patch['ai_assist']['endpoint_ref']
        private_refs['ai_keys']=patch['ai_assist']['credential_refs']
    if old:
        imported=import_legacy(old)
        discovery=dict(imported['config']);discovery.pop('proxy',None);discovery.pop('rate_limit_scope',None)
        discovery['season_scope']='all_known' if flag(old.get('is_seasons_all',True)) else 'identified'
        discovery['media_type_allowlist']=['电影'] if flag(old.get('is_only_movies')) else []
        for key in ('minimum_release_year','minimum_rating'):
            value=discovery.get(key)
            try:discovery[key]=None if value in (None,'',0,'0') else int(value) if key.endswith('year') else float(value)
            except (TypeError,ValueError):discovery[key]=None;diagnostics.append(key+':INVALID_REQUIRES_REVIEW')
        interval=old.get('sleep_time')
        if isinstance(interval,str):
            try:
                parts=[float(x.strip()) for x in interval.split(',')]
                if len(parts)!=2:raise ValueError()
                discovery['request_budget']={'interval_min_seconds':parts[0],'interval_max_seconds':parts[1]}
            except ValueError:diagnostics.append('INTERVAL_INVALID_REQUIRES_REVIEW')
        sources=[]
        for source in imported['sources']:
            source['enabled']=False
            source['proxy']=flag(old.get('proxy'))
            if source['id'].startswith('legacy-') and source['id'][7:].isdigit():
                source['legacy_original_text']=str(old.get('rss_addrs','')).splitlines()[int(source['id'][7:])-1]
            # Old paths are retained as disabled source syntax, never silently
            # interpreted as authorized current destination template IDs.
            sources.append(source)
        discovery.update(sources=sources,enabled=False)
        patch['discovery']=DiscoveryConfig.model_validate(discovery).model_dump()
        patch['history_view']={'最新12条历史':'latest12','已识别历史':'recognized','未识别历史':'unrecognized','历史处理统计':'statistics','所有历史':'all'}.get(old.get('history_type'),'all')
        diagnostics.extend(imported['diagnostics'])
        if 'proxy' not in old:diagnostics.append('LEGACY_PROXY_NOT_EXPORTED_UNKNOWN')
        if not old.get('cron'):diagnostics.append('CRON_LEGACY_DEFAULT_0_8')
        for k in ('migrate_from_url','migrate_api_token'):
            if old.get(k):private_refs[k]=store.put(str(old[k]))
        diagnostics.append('RATE_LIMIT_CORRECTED_ORIGIN_COOLDOWN')
    if 'native' in data:
        if not isinstance(data['native'],list) or len(data['native'])>1000:raise ValueError('NATIVE_HISTORY_LIMIT')
        for index,row in enumerate(data['native']):
            if not isinstance(row,dict):raise ValueError('NATIVE_HISTORY_SHAPE')
            for key in row:
                fields.append(dict(source='native',field=key,target='protected_native_snapshot',raw_pointer=f'/native/{index}/{key}',
                    status='HISTORICAL_ONLY' if key in ('episode_priority','best_version') else 'READBACK_REQUIRED' if key in snapshot_config(row) else 'UNMAPPED_RETAINED'))
        diagnostics.append('NATIVE_ADOPTION_REQUIRES_SEPARATE_LIVE_READBACK')
    if 'policy' in data:
        from .policy import import_legacy_overrides
        if not isinstance(data['policy'],list) or len(data['policy'])>100:raise ValueError('LEGACY_POLICY_SHAPE')
        identities=[r.get('id') for r in data['policy'] if isinstance(r,dict) and isinstance(r.get('id'),str)]
        for index,row in enumerate(data['policy']):
            if not isinstance(row,dict):raise ValueError('LEGACY_POLICY_SHAPE')
            identity=row.get('id');status='SUPPORTED_REQUIRES_BINDING';reason='TEXT_OR_METADATA_SEMANTICS_RETAINED'
            try:
                candidate=import_legacy_overrides([row])
                if identities.count(identity)>1:raise ValueError('DUPLICATE_LEGACY_ID')
                if any(contains_private(candidate,s) for s in secrets if s):raise ValueError('PRIVATE_PREDICATE')
                policy_candidates.update(candidate)
            except (ValueError,TypeError):
                status='EXPLICIT_EQUIVALENT_REQUIRED';reason='UNSUPPORTED_FIELDS_MATCH_OR_PREDICATE'
            for key in row:
                fields.append(dict(source='policy',field=str(key),target='policy.overrides',status=status,
                    raw_pointer=f'/policy/{index}/{key}',diagnostic=reason))
        diagnostics.append('POLICY_CATEGORY_MAPPING_REQUIRED_NO_HOST_YAML_WRITE')
    for f in fields:
        values=ai if f['source']=='ai' else old if f['source']=='discovery' else {}
        effective=patch
        for component in f['target'].split('.'):
            effective=effective.get(component) if isinstance(effective,dict) else None
        if effective is not None and f['field'] in values and f['field'] not in ('openai_url','openai_key'):
            f['effective_preview']=json.dumps(safe(effective,secrets),ensure_ascii=False)[:4096]
            if values[f['field']]!=effective:f['diagnostic']='NORMALIZED_OR_DISABLED_SEE_RAW'
        if f['field'] in ('statistics','chat_history') and f['field'] in values:f['diagnostic']='HISTORICAL_ONLY_NO_MEMORY_RESTORE'
    for key in ('statistics','chat_history'):
        if key in data:
            fields.append(dict(source='historical',field=key,target='protected_raw',status='HISTORICAL_ONLY',
                raw_pointer='/'+key,diagnostic='NO_CURRENT_SESSION_OR_USAGE_RESTORE'))
    return data,dict(config=patch,fields=safe(fields,secrets),diagnostics=safe(diagnostics,secrets),private_refs=private_refs,
        policy_candidates=policy_candidates,
        requested_features={'ai':legacy_preview(ai),'discovery_enabled':flag(old.get('enabled'))},
        memory_cache_restored=False,history_count=len(rows)),secrets


def collect_host(plugin):
    """Fresh public SDK metadata only. Configuration values never leave backend."""
    from app.sdk.plugin import PluginManager
    from app.sdk.events import eventmanager
    from app.sdk.scheduler import list_scheduler_jobs
    from app.schemas.types import ChainEventType
    manager=PluginManager();generation=manager.get_plugin_runtime_generation();plugins=[];services=[]
    runtime=manager.running_plugins
    ids=set(manager.get_plugin_ids())|set(manager.get_plugin_instances())|set(manager.get_running_plugin_ids())
    if len(ids)>500:raise ValueError('OWNER_INVENTORY_LIMIT')
    for pid in sorted(ids):
        instance=runtime.get(pid);cls=type(instance) if instance is not None else None
        plugins.append(dict(id=pid,source=manager.get_plugin_source_id(pid),
            prefix=cls.__module__+'.'+cls.__qualname__ if cls else '',active=manager.get_plugin_state(pid) is True,
            version=str(getattr(instance,'plugin_version','')),config=manager.get_plugin_config(pid) or {},
            loaded=instance is not None,
            api_paths=sorted(a['path'] for a in manager.get_plugin_apis(pid)),
            commands=sorted(str(c.get('cmd','')) for c in manager.get_plugin_commands(pid))))
        for service in manager.get_plugin_services(pid):
            callback=service.get('func')
            services.append(dict(instance_id=pid,id=service.get('id'),callable=callable(callback),
                handler=(callback.__module__+'.'+callback.__qualname__) if callable(callback) else '',
                kwargs=safe(service.get('kwargs',{})),func_kwargs=safe(service.get('func_kwargs',{}))))
    jobs=[{'id':str(getattr(j,'id','')),'status':str(getattr(j,'status',''))} for j in list_scheduler_jobs()]
    handlers=eventmanager.visualize_handlers()
    if len(handlers)>5000 or len(jobs)>5000:raise ValueError('OWNER_INVENTORY_LIMIT')
    if generation!=manager.get_plugin_runtime_generation():raise ValueError('OWNER_RUNTIME_CHANGED')
    return dict(generation=generation,plugins=plugins,services=services,jobs=jobs,handlers=handlers,
                event_types={'name_bridge':ChainEventType.NameRecognize.value})


class Migration:
    def __init__(self,repository,configuration,secrets,inventory):
        self.repository,self.configuration,self.secrets,self.inventory=repository,configuration,secrets,inventory
        configuration.before_apply=self.guard_configuration

    def _load(self,identity,kind=None):
        with self.repository.connection() as db:row=db.execute('SELECT * FROM migration_receipts WHERE id=?',(identity,)).fetchone()
        if not row or kind and row['kind']!=kind:raise ValueError('RECEIPT_NOT_FOUND')
        return dict(row,data=json.loads(row['data']))

    def _save(self,row):
        with self.repository.connection(write=True) as db:
            changed=db.execute('UPDATE migration_receipts SET revision=revision+1,state=?,data=?,updated_at=? WHERE id=? AND revision=?',
                (row['state'],json.dumps(row['data']),utcnow(),row['id'],row['revision'])).rowcount
            if changed!=1:raise ValueError('STALE_RECEIPT')
        return self.receipt(row['id'])

    def receipt(self,identity):
        row=self._load(identity);data=row['data']
        result=dict(receipt_id=row['id'],kind=row['kind'],revision=row['revision'],digest=row['digest'],state=row['state'],
            **{k:data[k] for k in ('snapshot_ref','source_instance','source_version','fields','diagnostics','private_refs',
                'requested_features','memory_cache_restored','history_count','cursor','features','steps','next_changes',
                'read_scope','result_receipt_id','proposed_config','policy_candidates','configuration_receipt','base_digest','config_digest','owner_checks') if k in data})
        if any(contains_private(result,self.secrets.resolve(ref)) for ref in data.get('private_context',[])):
            raise ValueError('PRIVATE_VALUE_IN_MIGRATION')
        return result

    def _new(self,identity,kind,checksum,state,data):
        with self.repository.connection(write=True) as db:
            db.execute('INSERT OR IGNORE INTO migration_receipts VALUES(?,?,?,?,?,?,?)',(identity,kind,1,checksum,state,json.dumps(data),utcnow()))
        return self.receipt(identity)

    def preview_import(self,raw,source_instance,source_version,timezone,actor,*,private_context=()):
        reference=self.secrets.put_snapshot(raw)
        context=sorted(set(private_context))
        data,preview,secrets=normalize(raw,self.secrets,timezone,context)
        # Existing offline identities remain stable; the same bytes under a
        # different SOURCE privacy context must never reuse a weaker receipt.
        checksum=digest([reference,source_instance,source_version,timezone,1]+([context] if context else []));identity='import-'+checksum[:32]
        # Full raw, prompts, keys and unknown fields remain byte-exact privately.
        # Receipt only exposes mapped values after redaction; config is internal.
        preview.update(snapshot_ref=reference,source_instance=source_instance,source_version=source_version,timezone=timezone,
            base_digest=self.configuration.view()['digest'],cursor=0,config_applied=False,operations={},actor=actor,private_context=context)
        proposal=merge(self.configuration.view()['config'],preview['config'])
        proposal=merge(proposal,{'enabled':False,'dry_run':True,'ai_assist':{'enabled':False,'name_recognize_bridge':False},'discovery':{'enabled':False}})
        preview['proposed_config']=self.configuration.validate(proposal)
        # Check the entire ordinary projection, including internal config patch
        # and external provenance metadata, against every known imported secret.
        # Do not redact an operational prompt into different executable content.
        if any(contains_private(preview,s) for s in secrets if s):raise ValueError('PRIVATE_VALUE_IN_MIGRATION')
        return self._new(identity,'IMPORT',checksum,'PREVIEW',preview)

    def preview_source(self,endpoint_ref,credential_ref,instance,ranges,actor):
        import ipaddress
        base=self.secrets.resolve(endpoint_ref).rstrip('/');parts=urlsplit(base)
        if (parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password
                or parts.path or parts.query or parts.fragment or instance!='DoubanRankPlusOptimized'):
            raise ValueError('LEGACY_READ_SCOPE_UNSUPPORTED')
        self.secrets.resolve(credential_ref)
        if len(ranges)>8:raise ValueError('LEGACY_PRIVATE_SCOPE_LIMIT')
        ranges=[str(ipaddress.ip_network(r,strict=False)) for r in ranges]
        scope=dict(origin=parts.scheme+'://'+parts.netloc,instance_id=instance,
                   methods=['GET'],paths=['/api/v1/plugin/'+instance+'/migrate-config','/api/v1/plugin/'+instance+'/migrate-history'],
                   private_ranges=ranges,redirects=0,requests=2,response_bytes=2097152,timeout_seconds=15)
        checksum=digest([scope,endpoint_ref,credential_ref,self.configuration.view()['digest']])
        return self._new('source-'+uuid4().hex,'SOURCE',checksum,'PREVIEW',dict(read_scope=scope,endpoint_ref=endpoint_ref,
            credential_ref=credential_ref,operations={},actor=actor,config_digest=self.configuration.view()['digest']))

    async def read_source(self,identity,revision,checksum,operation,actor,*,read=read_legacy_source):
        row=self._load(identity,'SOURCE');data=row['data']
        if operation in data['operations']:
            if data['operations'][operation]!=checksum:raise ValueError('OPERATION_CONFLICT')
            self.receipt(data['result_receipt_id'])  # Resolve the derived privacy context on replay too.
            return self.receipt(identity)
        if row['revision']!=revision or row['digest']!=checksum or data['config_digest']!=self.configuration.view()['digest']:raise ValueError('STALE_SOURCE_PREVIEW')
        scope=data['read_scope'];bodies=await read(self.secrets.resolve(data['endpoint_ref']).rstrip('/'),
            self.secrets.resolve(data['credential_ref']),scope['instance_id'],scope['private_ranges'])
        if data['config_digest']!=self.configuration.view()['digest']:raise ValueError('SOURCE_CONFIG_CHANGED')
        if len(bodies)!=2:raise ValueError('LEGACY_READ_SHAPE')
        refs=[self.secrets.put_snapshot(b) for b in bodies]
        try:
            config,history=(json.loads(b) for b in bodies)
            if not isinstance(config,dict) or not isinstance(history,list) or config.get('success') is False:raise ValueError()
        except (ValueError,TypeError):raise ValueError('LEGACY_READ_SHAPE') from None
        preview=self.preview_import(json.dumps({'discovery':config,'history':history},ensure_ascii=False).encode(),
                                    scope['instance_id'],'unknown; adapter contract 1.0.7',None,actor,
                                    private_context=[data['endpoint_ref'],data['credential_ref']])
        data['private_refs']=dict(config_response=refs[0],history_response=refs[1])
        data['result_receipt_id']=preview['receipt_id'];data['operations'][operation]=checksum;row['state']='READ_DONE'
        return self._save(row)

    def import_page(self,identity,revision,checksum,cursor,limit,operation,actor,begin=None):
        row=self._load(identity,'IMPORT');data=row['data'];signature=digest([cursor,limit,checksum])
        if operation in data['operations']:
            if data['operations'][operation]!=signature:raise ValueError('OPERATION_CONFLICT')
            return self.receipt(identity)
        if len(data['operations'])>=1000:raise ValueError('OPERATION_LIMIT')
        if row['revision']!=revision or row['digest']!=checksum or cursor!=data['cursor']:raise ValueError('STALE_IMPORT')
        if type(limit)is not int or not 1<=limit<=100:raise ValueError('IMPORT_PAGE_LIMIT')
        raw=self.secrets.read_snapshot(data['snapshot_ref']);original,_,secrets=normalize(raw,self.secrets,data['timezone'],data.get('private_context',[]))
        marker='legacy-config-import:'+digest([data['config'],data['base_digest']])
        if (not data['config_applied'] and not self.repository.setting(marker)
                and self.configuration.view()['digest']!=data['base_digest']):raise ValueError('IMPORT_CONFIG_CHANGED')
        if begin:begin()
        self.configuration.ready=False
        if not data['config_applied']:
            self.configuration.import_disabled(data['config'],data['base_digest'],actor)
            data['config_applied']=True;data['imported_config_digest']=self.configuration.view()['digest']
        rows=original.get('history',[]);end=min(cursor+limit,len(rows))
        with self.repository.connection(write=True) as db:
            current=db.execute('SELECT revision FROM migration_receipts WHERE id=?',(identity,)).fetchone()
            if current[0]!=revision:raise ValueError('STALE_IMPORT')
            for ordinal in range(cursor,end):
                projected=history_row(rows[ordinal],ordinal,data['snapshot_ref'],data['timezone'],secrets)
                db.execute('INSERT OR IGNORE INTO migration_history VALUES(?,?,?,?)',(identity,ordinal,projected['digest'],json.dumps(projected)))
            data['cursor']=end;data['operations'][operation]=signature
            state='IMPORTED' if end==len(rows) else 'IMPORTING'
            db.execute('UPDATE migration_receipts SET revision=revision+1,state=?,data=?,updated_at=? WHERE id=? AND revision=?',
                (state,json.dumps(data),utcnow(),identity,revision))
            self.repository._audit(db,None,'LEGACY_IMPORT_PAGE:'+identity,str(actor)[:128])
        return self.receipt(identity)

    def history(self,identity,limit,offset):
        if not 1<=limit<=100 or offset<0:raise ValueError('HISTORY_PAGE_LIMIT')
        row=self._load(identity,'IMPORT')
        with self.repository.connection() as db:rows=[json.loads(r[0]) for r in db.execute('SELECT data FROM migration_history WHERE receipt_id=? ORDER BY ordinal LIMIT ? OFFSET ?',(identity,limit,offset))]
        if any(contains_private(rows,self.secrets.resolve(ref)) for ref in row['data'].get('private_context',[])):
            raise ValueError('PRIVATE_VALUE_IN_MIGRATION')
        return rows

    def link_history(self,identity,ordinal,expected_digest,task_id,version_id,archive,actor):
        """Attach a historical row to a fresh current task/library without granting work."""
        receipt=self._load(identity,'IMPORT')
        if type(ordinal)is not int or ordinal<0 or type(task_id)is not int or task_id<=0 or not isinstance(version_id,str) or not version_id:
            raise ValueError('LEGACY_LINK_SCOPE_INVALID')
        with self.repository.connection() as db:
            stored=db.execute('SELECT data FROM migration_history WHERE receipt_id=? AND ordinal=?',(identity,ordinal)).fetchone()
            task=db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            selected=db.execute('SELECT target_key,active FROM archive_versions WHERE id=?',(version_id,)).fetchone()
        if not stored or not task:raise ValueError('LEGACY_LINK_SCOPE_UNKNOWN')
        row=json.loads(stored[0]);task=dict(task)
        private=[self.secrets.resolve(ref) for ref in receipt['data'].get('private_context',[])]
        if any(contains_private(row,value) for value in private):
            raise ValueError('PRIVATE_VALUE_IN_MIGRATION')
        if row['digest']!=expected_digest:raise ValueError('LEGACY_DIGEST_CHANGED')
        prior=row.get('link')
        if prior:
            if (prior['task_id'],prior['version_id'])!=(task_id,version_id):raise ValueError('LEGACY_LINK_CONFLICT')
            return row
        raw=row['raw'];identities=row['identities'];snapshot=json.loads(task['snapshot'])
        if raw.get('type')!=task['media_type']:
            raise ValueError('LEGACY_IDENTITY_UNVERIFIED')
        if identities:
            if identities.get(task['media_source'])!=str(task['media_id']):raise ValueError('LEGACY_IDENTITY_UNVERIFIED')
        else:
            title=' '.join(str(raw.get('title') or '').split()).casefold()
            current_title=' '.join(str(snapshot.get('name') or '').split()).casefold()
            year=str(raw.get('year') or '')
            if not title or title!=current_title or not year.isdecimal() or int(year)<=0 or year!=str(snapshot.get('year')):
                raise ValueError('LEGACY_IDENTITY_UNVERIFIED')
        if not selected or selected['active']!=1 or json.loads(selected['target_key'])[:5]!=json.loads(task['target_key']):
            raise ValueError('CURRENT_LIBRARY_UNVERIFIED')
        unit_key=selected['target_key']
        observed=archive.current([unit_key])[unit_key]
        if observed['state']!='PRESENT' or version_id not in {v.version_id for v in observed['versions']}:
            raise ValueError('CURRENT_LIBRARY_UNVERIFIED')
        with self.repository.connection(write=True) as db:
            current=db.execute('SELECT data FROM migration_history WHERE receipt_id=? AND ordinal=?',(identity,ordinal)).fetchone()
            fresh_task=db.execute('SELECT target_key,snapshot,generation FROM tasks WHERE id=?',(task_id,)).fetchone()
            target=db.execute('SELECT state,revision FROM archive_targets WHERE target_key=?',(unit_key,)).fetchone()
            version=db.execute('SELECT target_key,service,library,active FROM archive_versions WHERE id=?',(version_id,)).fetchone()
            if not current or current[0]!=stored[0] or not fresh_task or (fresh_task['target_key'],fresh_task['snapshot'],fresh_task['generation'])!=(task['target_key'],task['snapshot'],task['generation']):
                raise ValueError('LEGACY_LINK_CHANGED')
            if not target or target['state']!='PRESENT' or target['revision']!=observed['archive_revision'] or not version or version['target_key']!=unit_key or version['active']!=1:
                raise ValueError('CURRENT_LIBRARY_UNVERIFIED')
            row['link']=dict(task_id=task_id,version_id=version_id,service=version['service'],library=version['library'],
                             archive_revision=target['revision'],linked_at=utcnow())
            if any(contains_private(row,value) for value in private):
                raise ValueError('PRIVATE_VALUE_IN_MIGRATION')
            db.execute('UPDATE migration_history SET data=? WHERE receipt_id=? AND ordinal=?',(json.dumps(row,ensure_ascii=False),identity,ordinal))
            self.repository._audit(db,task_id,'LEGACY_HISTORY_LINK:'+identity+':'+str(ordinal),str(actor)[:128])
        return row

    def feature(self,module,route_scope,config=None):
        if module not in MODULES:raise ValueError('UNKNOWN_FEATURE')
        config=config if config is not None else self.configuration.view()['config'];key='discovery' if module=='discovery' else 'ai_assist'
        if module=='discovery':
            if not isinstance(route_scope,str) or route_scope not in {s['id'] for s in config[key]['sources']}:raise ValueError('SOURCE_SCOPE_UNKNOWN')
        elif module=='name_bridge':
            if route_scope!={'event':'NameRecognize'}:raise ValueError('NAME_SCOPE_INVALID')
        elif route_scope!='internal':raise ValueError('ASSISTANCE_SCOPE_INVALID')
        checksum=discovery_digest(config[key]) if module=='discovery' else digest(config[key])
        return dict(module=module,instance_id=self.configuration.instance_id,config_digest=checksum,route_scope=route_scope)

    def _enabled(self,feature,config):
        if feature['module'] not in MODULES:return False
        if feature['module']=='discovery':return config.get('discovery',{}).get('enabled') is True
        ai=config.get('ai_assist',{})
        return ai.get('enabled') is True and ai.get({'name_bridge':'name_recognize_bridge','name_assistance':'name_assistance_enabled'}[feature['module']]) is True

    def owner_snapshot(self,module,instance_id,config_digest,route_scope,*,desired=None,require_new=True):
        expected=self.feature(module,route_scope,desired)
        if expected!=dict(module=module,instance_id=instance_id,config_digest=config_digest,route_scope=route_scope):raise ValueError('FEATURE_CONFIG_CHANGED')
        inventory=self.inventory();plugins=inventory['plugins'];own=next((p for p in plugins if p['id']==instance_id),None)
        if not own:raise ValueError('OWN_INSTANCE_MISSING')
        config=self.configuration.view()['config'];key='discovery' if module=='discovery' else 'ai_assist'
        overlaps=[];unknown=[]
        checksum=discovery_digest(own['config'].get(key,{})) if module=='discovery' else digest(own['config'].get(key,{}))
        if checksum!=config_digest:unknown.append('own_config_changed')
        if (not self.configuration.ready or not own['active'] or own['config'].get('enabled') is not True or own['config'].get('dry_run') is not False
                or not self._enabled(expected,own['config'])):unknown.append('own_feature_inactive')
        if module=='name_bridge':
            # Match the real enum values supplied in the public inventory.
            from_event='NameRecognize'
            event=inventory.get('event_types',{}).get(module,from_event)
            own_handler=own['prefix']+'.ai_name'
            projected=owner_projection(inventory['handlers'],plugins,module,event,own_handler,expected)
            overlaps.extend(projected['overlaps']);unknown.extend(projected['unclassified'])
            # Legacy partial flags have no public runtime readback. Saved false
            # plus an enabled responder cannot prove init applied the change.
            # Do not inspect private flags or disable an unselected feature.
            for p in plugins:
                if p['source']!='ChatGPTPlusUltra' or not p['active']:continue
                key='recognize'
                if not flag(p['config'].get(key,False)) and any(
                        h.get('event_type')==event and h.get('status')=='enabled' and
                        h.get('handler_identifier','').startswith(p['prefix']+'.') for h in inventory['handlers']):
                    unknown.append('LEGACY_RUNTIME_FLAGS_UNVERIFIED:'+p['id'])
        for p in plugins:
            if p['id']==instance_id:continue
            if p['source'] in ('ChatGPTPlusUltra','DoubanRankPlusOptimized') and any(
                    k in p['config'] and type(p['config'][k])is not bool for k in ('enabled','recognize')):
                unknown.append(p['id']+':invalid_switch')
            if p['source']=='SubscriBetter' and self._enabled(expected,p['config']):
                overlaps.append(p['id'])
            if module=='discovery' and p['source']=='DoubanRankPlusOptimized':
                services=[s for s in inventory['services'] if s['instance_id']==p['id']]
                jobs=[j for j in inventory['jobs'] if j['id'].startswith(p['id']+'_')]
                if flag(p['config'].get('enabled')) or services or jobs:overlaps.append(p['id'])
            if p.get('loaded') is False and flag(p['config'].get('enabled')) and p['source'] in ('SubscriBetter','ChatGPTPlusUltra','DoubanRankPlusOptimized'):unknown.append(p['id']+':unloaded')
        if module=='discovery':
            own_services=[s for s in inventory['services'] if s['instance_id']==instance_id and s['id']=='SubscriBetter_discovery' and s['callable']]
            if not own_services or not any(j['id']==instance_id+'_SubscriBetter_discovery' for j in inventory['jobs']):unknown.append('own_schedule_missing')
            # Unknown scheduler providers cannot be proved harmless by a name.
            classified={p['id'] for p in plugins}
            if any(s['instance_id'] not in classified for s in inventory['services']):unknown.append('unclassified_service')
            sources={p['id']:p['source'] for p in plugins}
            if any(s['instance_id']!=instance_id and sources.get(s['instance_id']) not in
                   {'SubscriBetter','DoubanRankPlusOptimized','CloudDriveDisk','P115Disk','ChatGPTPlusUltra'} for s in inventory['services']):
                unknown.append('unclassified_scheduled_plugin')
        if not require_new:
            unknown=[x for x in unknown if x not in {'own_config_changed','own_feature_inactive','own_handler_missing','own_schedule_missing'}]
        projected=[{k:v for k,v in p.items() if k!='config'}|{'config_digest':digest(p['config'])} for p in plugins]
        return dict(fingerprint=digest([expected,inventory['generation'],projected,inventory['handlers'],inventory['services'],inventory['jobs']]),
                    overlaps=sorted(set(overlaps)),unclassified=sorted(set(unknown)))

    def preview_cutover(self,features,selected,actor,configuration_receipt=None):
        if not features or len(features)>100 or len(selected)>30:raise ValueError('CUTOVER_SCOPE_LIMIT')
        if selected and not configuration_receipt:raise ValueError('CUTOVER_CONFIG_PREVIEW_REQUIRED')
        current=self.configuration.view();desired=current['config'];binding=None
        if configuration_receipt:
            binding=self._load(configuration_receipt,'CONFIG')
            if (binding['state']!='PREVIEW' or binding['data']['base_digest']!=current['digest']
                    or binding['data']['base_revision']!=current['revision']):raise ValueError('STALE_CONFIGURATION')
            desired=binding['data']['config']
            if current['config']['enabled'] and not current['config']['dry_run']:raise ValueError('CUTOVER_NEW_MUST_BE_DISABLED')
        for feature in features:
            if feature!=self.feature(feature['module'],feature['route_scope'],desired):raise ValueError('FEATURE_CONFIG_CHANGED')
        inventory=self.inventory();plugins={p['id']:p for p in inventory['plugins']};steps=[]
        for choice in selected:
            pid=choice['instance_id'];p=plugins.get(pid)
            if not p or choice['config_digest']!=digest(p['config']):raise ValueError('OLD_CONFIG_CHANGED')
            if any(k in p['config'] and type(p['config'][k])is not bool for k in ('enabled','recognize')):raise ValueError('OLD_SWITCH_INVALID')
            if any(flag(p['config'].get(k)) for k in ACTIONS):raise ValueError('OLD_ONE_SHOT_RELOAD_UNSAFE')
            module=choice['module'];changes={};before={}
            if p['source']=='ChatGPTPlusUltra' and p['version']=='1.4.2' and module=='name_bridge':
                key='recognize';changes[key]=False;before[key]=p['config'].get(key,False)
            elif p['source']=='DoubanRankPlusOptimized' and p['version']=='1.0.7' and module=='discovery' and choice.get('whole_instance') is True:
                # Fixed version's cron + manual discovery are its entire running
                # capability. Unknown version/partial sources cannot use this exception.
                if choice.get('all_capabilities')!=['discovery']:raise ValueError('SELECTIVE_DISABLE_UNSUPPORTED')
                if (not isinstance(p['config'].get('ranks',[]),list) or any(not isinstance(x,str) for x in p['config'].get('ranks',[]))
                        or not isinstance(p['config'].get('rss_addrs',''),str)):raise ValueError('OLD_SOURCE_SCOPE_INVALID')
                paths={x.rsplit('/',1)[-1] for x in p.get('api_paths',[])}
                own_handlers=[h for h in inventory['handlers'] if h['handler_identifier'].startswith(p['prefix']+'.')]
                services=[s for s in inventory['services'] if s['instance_id']==pid]
                if (paths!={'delete_history','migrate-config','migrate-history'} or p.get('commands')!=[] or own_handlers
                        or any(not s['callable'] or not s['handler'].endswith('.__start_task') for s in services)):
                    raise ValueError('SELECTIVE_DISABLE_UNSUPPORTED')
                changes={'enabled':False};before={'enabled':p['config'].get('enabled',False)}
            else:raise ValueError('SELECTIVE_DISABLE_UNSUPPORTED')
            if module not in {f['module'] for f in features}:raise ValueError('OLD_FEATURE_NOT_SELECTED')
            after=merge(p['config'],changes)
            baseline_ref=self.secrets.put_snapshot(json.dumps(p['config'],ensure_ascii=False,sort_keys=True).encode())
            existing=next((s for s in steps if s['instance_id']==pid),None)
            if existing:
                if module in existing['modules']:raise ValueError('DUPLICATE_SELECTED_FEATURE')
                existing['modules'].append(module);existing['changes'].update(changes);existing['before'].update(before)
                existing['after_digest']=digest(merge(p['config'],existing['changes']))
                existing['restore_digest']=digest(merge(p['config'],existing['before']))
            else:
                steps.append(dict(instance_id=pid,modules=[module],changes=changes,before=before,before_digest=digest(p['config']),
                    after_digest=digest(after),restore_digest=digest(merge(p['config'],before)),baseline_ref=baseline_ref,
                    capability_digest=capabilities(p),
                    scope=dict(all_discovery_sources=bool(choice.get('whole_instance')),
                        ranks=safe(p['config'].get('ranks',[])) if module=='discovery' else [],
                        rss_line_digests=[digest(line) for line in str(p['config'].get('rss_addrs','')).splitlines() if line.strip()] if module=='discovery' else []),
                    state='WAIT_HOST_SAVE',receipt_id=None,whole_instance=choice.get('whole_instance',False)))
        checks=[self.owner_snapshot(**f,desired=desired,require_new=False) for f in features]
        checksum=digest([features,steps,current['digest'],configuration_receipt]);identity='cutover-'+uuid4().hex
        return self._new(identity,'CUTOVER',checksum,'PREVIEW',dict(features=features,steps=steps,operations={},
            config_digest=binding['digest'] if binding else current['digest'],base_digest=current['digest'],
            configuration_receipt=configuration_receipt,proposed_config=desired,owner_checks=checks,actor=actor,next_changes=[dict(instance_id=s['instance_id'],changes=s['changes'],expected_digest=s['before_digest']) for s in steps]))

    def guard_configuration(self,value):
        if not value['enabled'] or value['dry_run']:return
        current=self.configuration.view()
        with self.repository.connection() as db:
            rows=list(db.execute("SELECT state,data FROM migration_receipts WHERE kind='CUTOVER' AND state NOT IN ('ACTIVE','ROLLED_BACK')"))
        for row in rows:
            data=json.loads(row['data'])
            if not any(f['module'] in MODULES for f in data['features']):continue
            if row['state'] in ('ROLLBACK_FENCED','ROLLBACK_RESTORE') and not any(self._enabled(f,value) for f in data['features']):continue
            binding=data.get('configuration_receipt')
            if not binding:continue
            # A different receipt cannot bypass an unfinished reviewed cutover.
            if value['configuration_receipt']!=binding:raise ValueError('CUTOVER_CONFIGURATION_CONFLICT')
            if row['state']!='READY_CONFIG' or current['digest'] not in (data['base_digest'],data['config_digest']):
                raise ValueError('CUTOVER_OLD_STOP_REQUIRED')
            if value!=data['proposed_config']:raise ValueError('CUTOVER_CONFIGURATION_CONFLICT')
            inventory=self.inventory();plugins={p['id']:p for p in inventory['plugins']}
            if any(s['instance_id'] not in plugins or digest(plugins[s['instance_id']]['config'])!=s['after_digest']
                    or capabilities(plugins[s['instance_id']])!=s['capability_digest'] for s in data['steps']):
                raise ValueError('CUTOVER_OLD_STOP_REQUIRED')
            for feature in data['features']:
                fresh=self.owner_snapshot(**feature,desired=value,require_new=False)
                if fresh['overlaps'] or fresh['unclassified']:raise ValueError('CUTOVER_OLD_STOP_REQUIRED')

    def advance(self,identity,revision,checksum,action,operation,actor):
        row=self._load(identity,'CUTOVER');data=row['data'];signature=digest([action,checksum])
        if any(f['module'] not in MODULES for f in data['features']):raise ValueError('REMOVED_FEATURE_REQUIRES_NEW_PREVIEW')
        if operation in data['operations']:
            if data['operations'][operation]!=signature:raise ValueError('OPERATION_CONFLICT')
            return self.receipt(identity)
        if len(data['operations'])>=1000:raise ValueError('OPERATION_LIMIT')
        if row['revision']!=revision or row['digest']!=checksum:raise ValueError('STALE_CUTOVER')
        if action=='rollback':
            # Durable fence FIRST, before suggesting any host Save or old restore.
            row['state']='ROLLBACK_FENCED';changes={}
            for f in data['features']:
                if f['module']=='discovery':changes=merge(changes,{'discovery':{'enabled':False}})
                else:changes=merge(changes,{'ai_assist':{{'name_bridge':'name_recognize_bridge','name_assistance':'name_assistance_enabled'}[f['module']]:False}})
            data['next_changes']=[dict(instance_id=self.configuration.instance_id,changes=changes,expected_digest=self.configuration.view()['digest'])]
        elif action=='rollback_readback':
            if row['state'] not in ('ROLLBACK_FENCED','ROLLBACK_RESTORE'):raise ValueError('ROLLBACK_NOT_FENCED')
            inventory=self.inventory();plugins={p['id']:p for p in inventory['plugins']};own=plugins.get(self.configuration.instance_id)
            if not own or any(self._enabled(f,own['config']) for f in data['features']):raise ValueError('NEW_FEATURE_STILL_ENABLED')
            patches=[];registration_pending=False;legacy_runtime_unknown=False
            for step in data['steps']:
                p=plugins.get(step['instance_id'])
                if not p:raise ValueError('OLD_INSTANCE_MISSING')
                if capabilities(p)!=step['capability_digest']:raise ValueError('OLD_CAPABILITIES_CHANGED')
                if digest(p['config'])==step['restore_digest']:
                    if flag(p['config'].get('enabled')):
                        for module in step['modules']:
                            if module=='discovery':
                                services=[s for s in inventory['services'] if s['instance_id']==p['id'] and s['callable']]
                                if not services or not any(j['id']==p['id']+'_'+s['id'] for j in inventory['jobs'] for s in services):registration_pending=True
                            elif flag(step['before'].get('recognize')):
                                # The same public flag gap applies to restoration:
                                # an enabled decorator is not proof init restored
                                # a selected internal runtime switch.
                                legacy_runtime_unknown=True;registration_pending=True
                                event=inventory.get('event_types',{}).get(module,'NameRecognize')
                                if not any(h['event_type']==event and h['status']=='enabled' and h['handler_identifier'].startswith(p['prefix']+'.') for h in inventory['handlers']):registration_pending=True
                    step['state']='WAIT_REGISTRATION' if registration_pending else 'RESTORED';continue
                if digest(p['config'])!=step['after_digest']:raise ValueError('ROLLBACK_CONFIG_CONFLICT')
                patches.append(dict(instance_id=p['id'],changes=step['before'],expected_digest=step['after_digest']))
            data['next_changes']=patches;row['state']='ROLLBACK_RESTORE' if patches or registration_pending else 'ROLLED_BACK'
            data['diagnostics']=['OLD_REGISTRATION_NOT_CONFIRMED'] if registration_pending else []
            if legacy_runtime_unknown:data['diagnostics'].append('LEGACY_RUNTIME_FLAGS_UNVERIFIED')
        elif action=='activate':
            if row['state'] not in ('PREVIEW','WAIT_HOST_SAVE','WAIT_OWNER','READY_CONFIG','ACTIVE'):raise ValueError('CUTOVER_NOT_ACTIVATABLE')
            current_digest=self.configuration.view()['digest']
            if current_digest not in (data['config_digest'],data.get('base_digest')):raise ValueError('CUTOVER_CONFIG_CHANGED')
            awaiting_config=bool(data.get('configuration_receipt') and current_digest!=data['config_digest'])
            inventory=self.inventory();plugins={p['id']:p for p in inventory['plugins']}
            pending=[]
            for step in data['steps']:
                p=plugins.get(step['instance_id'])
                if not p:raise ValueError('OLD_INSTANCE_MISSING')
                if capabilities(p)!=step['capability_digest']:raise ValueError('OLD_CAPABILITIES_CHANGED')
                observed=digest(p['config'])
                if observed!=step['after_digest']:
                    if observed==step['before_digest']:pending.append(step);continue
                    raise ValueError('OLD_CONFIG_CHANGED')
                step['state']='CONFIG_READBACK';step['receipt_id']=identity+':'+step['instance_id']
            data['next_changes']=[dict(instance_id=s['instance_id'],changes=s['changes'],expected_digest=s['before_digest']) for s in pending]
            row['state']='WAIT_HOST_SAVE'
            if not pending:
                fingerprints=[]
                for f in data['features']:
                    fresh=self.owner_snapshot(**f,desired=data.get('proposed_config'),require_new=not awaiting_config)
                    if fresh['overlaps'] or fresh['unclassified']:
                        row['state']='WAIT_OWNER';data['diagnostics']=['OWNER_NOT_UNIQUE',*fresh['unclassified']];break
                    fingerprints.append(fresh['fingerprint'])
                else:
                    row['state']='READY_CONFIG' if awaiting_config else 'ACTIVE';data['diagnostics']=[]
                    data['verified_at']=utcnow();data['verified_by']=actor
                    data['readback_fingerprints']=fingerprints
        else:raise ValueError('CUTOVER_ACTION_INVALID')
        data['operations'][operation]=signature
        return self._save(row)

    def unique_owner(self,module,instance_id,config_digest,route_scope):
        expected=dict(module=module,instance_id=instance_id,config_digest=config_digest,route_scope=route_scope)
        if not self.configuration.ready:return None
        with self.repository.connection() as db:
            rows=list(db.execute("SELECT id,state,data FROM migration_receipts WHERE kind='CUTOVER' ORDER BY updated_at DESC LIMIT 100"))
        for row in rows:
            data=json.loads(row['data'])
            if expected not in data['features']:continue
            if row['state']=='PREVIEW':continue
            if row['state']!='ACTIVE' or data['config_digest']!=self.configuration.view()['digest']:return None
            try:
                inventory=self.inventory();plugins={p['id']:p for p in inventory['plugins']}
                if any(s['instance_id'] not in plugins or digest(plugins[s['instance_id']]['config'])!=s['after_digest']
                    or capabilities(plugins[s['instance_id']])!=s['capability_digest'] for s in data['steps']):return None
                fresh=self.owner_snapshot(**expected)
                if fresh['overlaps'] or fresh['unclassified']:return None
                return owner_receipt(row['id'],**expected,fingerprint=fresh['fingerprint'],disabled=[s['receipt_id'] for s in data['steps']])
            except Exception:return None
        return None
