"""Nine administrator views over existing domains, bounded SQL and exact receipts."""
from datetime import timedelta
from contextlib import nullcontext
import json
import sqlite3
import time
import inspect
from functools import wraps
from typing import Annotated, Generic, Literal, TypeVar
from uuid import uuid4
from fastapi import Depends, HTTPException, Query
from pydantic import Field, JsonValue
from app.sdk.security import verify_token
from app.schemas.token import TokenPayload
from .configuration import Strict, PolicyConfig
from .management import PrivateRoute, Id, Digest, ConfigPreview
from .evidence import public, append
from .ai import digest, DEFAULT_PROMPT
from .repository import Target, utcnow
from .planner import encoded
from .scheduler import instant, parse
from .execution import Exclusions, MUTATION_LOCK

Key=Annotated[str,Field(min_length=1,max_length=2048)]
Limit=Annotated[int,Query(ge=1,le=100)]
Offset=Annotated[int,Query(ge=0,le=10000000)]
T=TypeVar('T')


class Snapshot(Strict):
    config_revision:int
    runtime_generation:int
    high_watermark:str


class Page(Strict,Generic[T]):
    items:list[T]
    total:int
    next_offset:int|None
    truncated:bool
    snapshot:Snapshot


class Task(Strict):
    id:int
    media_type:str
    media_source:str
    media_id:str
    season:int|None
    episode_group:str
    state:str
    native_id:int|None
    generation:int
    actor:str
    created_at:str
    updated_at:str


class Unit(Strict):
    target_key:str
    task_id:int
    generation:int
    owner_plan_id:str|None
    publish_phase:str
    publish_action_id:str|None
    current_revision:int
    current_facts:JsonValue
    last_ingest_confirmed_at:str|None
    cooldown_until:str|None


class Row(Strict):
    id:str
    state:str
    revision:str
    data:dict[str,JsonValue]


class TaskDetail(Strict):
    task:Task
    lifecycle:dict[str,JsonValue]|None
    effective:dict[str,JsonValue]|None
    units:Page[Unit]
    opportunities:Page[Row]
    plans:Page[Row]
    snapshot:Snapshot


class Candidate(Strict):
    candidate_key:str
    site:int|None
    title:str|None
    source:str|None
    status:str|None
    first_seen:str
    updated_at:str
    evidence:dict[str,JsonValue]


class Decision(Strict):
    id:str
    candidate_key:str
    task_id:int|None
    opportunity_id:str|None
    status:str
    simulation:bool
    digest:str
    created_at:str
    evidence:dict[str,JsonValue]


class ArchiveTarget(Strict):
    target_key:str
    state:str
    revision:str
    updated_at:str
    task_id:int|None
    facts:dict[str,JsonValue]


class ArchiveDetail(Strict):
    target:ArchiveTarget
    versions:Page[Row]


class VersionDetail(Strict):
    version:Row
    assets:Page[Row]
    sources:Page[Row]


class Bundle(Strict):
    id:str
    plan_id:str
    rule_id:str
    state:str
    due:str
    revision:int
    reason:str
    file_count:int
    publication_action:str|None
    consumer_pending:bool|None


class BundleDetail(Strict):
    bundle:Bundle
    authority:dict[str,JsonValue]
    files:Page[Row]
    actions:Page[Row]
    receipts:Page[Row]
    shared_references:Page[Row]
    cleanup:dict[str,JsonValue]


class PolicyView(Strict):
    category_id:str
    binding:str
    publication_revision:int
    policy_revision:str|None
    dimensions:list[str]
    configuration:dict[str,JsonValue]


class Source(Strict):
    source_id:str
    config_revision:str
    last_state:str
    last_reason:str
    failures:int
    next_due:float
    last_success:str|None
    last_failure:str|None
    updated_at:str
    config:dict[str,JsonValue]


class DiscoveryRecord(Strict):
    id:int
    source_id:str
    state:str
    reason:str
    raw_revision:str
    filter_revision:str
    retry_count:int
    next_due:float
    visible:bool
    first_seen:str
    last_seen:str
    raw:dict[str,JsonValue]
    evidence:dict[str,JsonValue]


class AIView(Strict):
    state:str
    config_digest:str
    generation:int
    provider:dict[str,JsonValue]
    runtime:dict[str,JsonValue]|None
    prompt:str
    prompt_backup:str
    prompt_previous_backup:str
    meta:dict[str,JsonValue]
    bridge:bool
    chat:bool
    errors:list[str]


class Health(Strict):
    foundation_only:Literal[True]=True
    database:Literal['AVAILABLE']
    enabled:bool
    ordinary_work_active:bool
    safety_required:bool
    safety_active:bool
    dry_run:bool
    generation:int
    pending:int
    errors:list[str]
    services:dict[str,JsonValue]
    scans:Page[Row]
    actions:Page[Row]
    meta:dict[str,JsonValue]
    ai:dict[str,JsonValue]
    snapshot:Snapshot


class Fence(Strict):
    config_revision:int=Field(ge=0)
    runtime_generation:int=Field(ge=0)


class Apply(Strict):
    preview_id:Id
    preview_digest:Digest
    operation_id:Id
    confirm:Literal[True]


class Preview(Strict):
    preview_id:str
    preview_digest:str
    kind:str
    objects:dict[str,JsonValue]
    revisions:dict[str,JsonValue]
    permissions:dict[str,bool]
    blockers:list[str]
    expires_at:str


class ActionResult(Strict):
    state:str
    operation_id:str|None=None
    result:dict[str,JsonValue]=Field(default_factory=dict)


class ParseView(Strict):
    status:str
    reasons:list[str]
    native:dict[str,JsonValue]
    corrected:dict[str,JsonValue]
    diff:dict[str,JsonValue]
    revision:str


class ReplayView(Strict):
    results:list[ParseView]


class DiscoveryStage(Strict):
    state:str
    reason:str=''
    items:int|None=None


class DiscoveryRun(Strict):
    sources:dict[str,DiscoveryStage]=Field(default_factory=dict)
    state:str|None=None
    reason:str|None=None


class DiscoveryTest(Strict):
    source_id:str
    items:int
    fetch_only:Literal[True]
    state:str
    url:str|None=None
    reason:str|None=None


class Changed(Strict):
    changed:int


class HistoryPreview(Fence):
    record_ids:list[Annotated[int,Field(gt=0)]]=Field(min_length=1,max_length=100)


class BundlePreview(Fence):
    revision:int=Field(ge=0)
    reason:Annotated[str,Field(min_length=1,max_length=256)]='ADMIN_CANCEL'


class CleanupPreview(BundlePreview):
    scope:Literal['monitor','staging','downloader_task','downloader_data']


class InvalidatePreview(Fence):
    version_ids:list[Id]=Field(min_length=1,max_length=100)
    reason:Annotated[str,Field(min_length=1,max_length=256)]


class SettingsPreview(Fence):
    generation:int=Field(gt=0)
    opportunity_id:Id
    target_keys:list[Key]=Field(min_length=1,max_length=100)
    destination_template:Id
    locks:dict[str,JsonValue]=Field(default_factory=dict)


class Immediate(Fence):
    generation:int=Field(gt=0)
    opportunity_id:Id
    target_keys:list[Key]=Field(min_length=1,max_length=100)
    operation_id:Id


class Search(Fence):
    site_ids:list[Annotated[int,Field(gt=0)]]=Field(min_length=1,max_length=32)
    keywords:list[Annotated[str,Field(min_length=1,max_length=256)]]=Field(min_length=1,max_length=8)
    keywords_limit:int=Field(default=2,ge=1,le=8)
    pages:int=Field(default=1,ge=1,le=5)
    concurrency:int=Field(default=1,ge=1,le=4)
    results:int=Field(default=50,ge=1,le=100)
    requests:int=Field(default=4,ge=1,le=64)
    interval:float=Field(default=1,ge=0,le=10)


class Evaluate(Fence):
    task_id:int=Field(gt=0)
    opportunity_id:Id
    candidate_key:Id
    simulation:bool=True


class ExclusionPreview(Fence):
    candidate_key:Id
    task_id:int=Field(gt=0)
    generation:int=Field(gt=0)
    target_keys:list[Key]=Field(min_length=1,max_length=100)
    reason:Annotated[str,Field(min_length=1,max_length=256)]
    expires_at:Annotated[str,Field(max_length=64)]|None=None


class Retry(BundlePreview):
    phase:Literal['transfer','publish','consumer']
    expected_state:Id
    operation_id:Id


class Reconcile(Fence):
    component:Literal['delivery','consumer','local','ownership']
    object_id:Key
    revision:Key


class ArchiveRefresh(Fence):
    service:Id
    library:Id
    target_keys:list[Key]=Field(min_length=1,max_length=100)
    scan_id:Id|None=None


class MappingTest(Fence):
    scan_id:Id
    item_id:Id
    mapping_id:Id


class PlanAction(Fence):
    task_generation:int=Field(gt=0)
    decision_digest:Digest|None=None


class PolicySample(Strict):
    title:Annotated[str,Field(max_length=16384)]=''
    description:Annotated[str,Field(max_length=16384)]=''
    labels:list[Annotated[str,Field(max_length=256)]]=Field(default_factory=list,max_length=100)
    size:int|None=Field(default=None,ge=0)
    seeders:int|None=Field(default=None,ge=0)
    downloadvolumefactor:float|None=Field(default=None,ge=0)
    original_language:Annotated[str,Field(max_length=128)]|None=None
    production_countries:list[str]=Field(default_factory=list,max_length=100)
    origin_country:list[str]=Field(default_factory=list,max_length=100)
    genre_ids:list[int]=Field(default_factory=list,max_length=100)


class CurrentSample(Strict):
    version_id:Id
    raw:PolicySample
    active:bool=True
    reliable:bool=True


class Simulation(Fence):
    category_id:Id
    candidate:PolicySample
    current:list[CurrentSample]=Field(default_factory=list,max_length=100)
    sample_key:Id|None=None


class DownloadObservation(Fence):
    plan_id:Id


class Views:
    def __init__(self,plugin):self.plugin=plugin;self.repository=plugin.repository

    def _auth(self,user):self.plugin._authorize(user)

    def snapshot(self,db,table='audit'):
        config=self.plugin.configuration.view()
        high=db.execute(f'SELECT coalesce(max(rowid),0) FROM {table}').fetchone()[0]
        return Snapshot(config_revision=config['revision'],runtime_generation=self.plugin.generation,high_watermark=str(high))

    def _page(self,table,model,project,*,where='1',args=(),order='rowid',limit=25,offset=0,select='*',source=None):
        if type(limit)is not int or not 1<=limit<=100 or type(offset)is not int or offset<0:raise HTTPException(422,'INVALID_PAGINATION')
        source=source or table
        try:
            with self.repository.connection() as db:
                db.execute('BEGIN')
                total=db.execute(f'SELECT count(*) FROM {source} WHERE {where}',args).fetchone()[0]
                rows=db.execute(f'SELECT {select} FROM {source} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
                snap=self.snapshot(db,table)
                items=[model.model_validate(project(dict(row))) for row in rows]
            end=offset+len(items)
            return Page[model](items=items,total=total,next_offset=end if end<total else None,truncated=end<total,snapshot=snap)
        except (sqlite3.Error,OSError,AttributeError):raise HTTPException(503,'MANAGEMENT_STORE_UNAVAILABLE') from None

    def _one(self,table,key,value):
        try:
            with self.repository.connection() as db:row=db.execute(f'SELECT * FROM {table} WHERE {key}=?',(value,)).fetchone()
        except (sqlite3.Error,OSError):raise HTTPException(503,'MANAGEMENT_STORE_UNAVAILABLE') from None
        if not row:raise HTTPException(404,table.upper()+'_NOT_FOUND')
        return dict(row)

    @staticmethod
    def row(row):
        data={k:json.loads(v) if k in ('data','snapshot','authorization','config','scope','sample','evidence','payload','targets','files','record','inputs','native','result','current_facts','identity','location','criteria') and isinstance(v,str) and v[:1] in ('[','{') else v for k,v in row.items()}
        return dict(id=str(row.get('id',row.get('target_key',row.get('sample_key',row.get('scope',row.get('file_index',row.get('source_id',''))))))),state=str(row.get('state',row.get('authorization','RECORDED'))),revision=str(row.get('revision',row.get('generation',row.get('updated_at','')))),data=public(data))

    def rows(self,table,*,where='1',args=(),order='rowid',limit=25,offset=0,source=None,select='*'):
        if select=='*' and source is None:
            if table=='plans':select="id,opportunity_id,task_id,authorization,transfer_phase,created_at,task_generation,json_remove(snapshot,'$.torrent_files','$.local_assets','$.targets','$.current') AS snapshot,json_array_length(json_extract(snapshot,'$.torrent_files')) AS file_count"
            elif table=='archive_versions':select="id,target_key,service,library,active,json_remove(data,'$.assets','$.source_assets','$.streams') AS data"
            elif table=='archive_sources':select="id,version_id,at,json_remove(data,'$.assets','$.source_assets','$.streams') AS data"
            elif table=='reconcile_checkpoints':select="scope,json_remove(data,'$.files','$.failed_paths','$.stack','$.directories') AS data,json_array_length(data,'$.failed_paths') AS failed_count,json_array_length(data,'$.stack') AS pending_directories,json_array_length(data,'$.directories') AS known_directories"
        return self._page(table,Row,self.row,where=where,args=args,order=order,limit=limit,offset=offset,source=source,select=select)

    def tasks(self,limit:Limit=25,offset:Offset=0,state:Literal['PENDING','ACTIVE','PASSIVE','PAUSED','STOPPED','RELEASING','RELEASED_NATIVE']|None=None,
              media_type:Literal['电影','电视剧']|None=None,sort:Literal['id','updated_at']='id',user:TokenPayload=Depends(verify_token))->Page[Task]:
        self._auth(user)
        return self._page('tasks',Task,lambda r:{k:public(r[k]) for k in Task.model_fields},where='(? IS NULL OR state=?) AND (? IS NULL OR media_type=?)',args=(state,state,media_type,media_type),order=sort+',id',limit=limit,offset=offset)

    def task(self,task_id:int,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->TaskDetail:
        self._auth(user);task=self._one('tasks','id',task_id)
        with self.repository.connection() as db:
            life=db.execute('SELECT * FROM task_lifecycle WHERE task_id=?',(task_id,)).fetchone();snap=self.snapshot(db)
        effective=self.repository.setting('runtime-task:'+str(task_id))
        units=self._page('target_units',Unit,lambda r:{k:public(json.loads(r[k]) if k=='current_facts' and r[k] else r[k]) for k in Unit.model_fields},where='task_id=?',args=(task_id,),order='target_key',limit=limit,offset=offset)
        return TaskDetail(task=Task(**{k:task[k] for k in Task.model_fields}),lifecycle=self.row(dict(life))['data'] if life else None,
            effective=public(effective) if effective else None,units=units,opportunities=self.rows('opportunities',where='task_id=?',args=(task_id,),order='created_at,id'),
            plans=self.rows('plans',where='task_id=?',args=(task_id,),order='created_at,id'),snapshot=snap)

    @staticmethod
    def _candidate(r):
        d=public(json.loads(r['data']))
        return dict(candidate_key=r['candidate_key'],site=d.get('site'),title=d.get('title'),source=d.get('source'),status=d.get('status'),first_seen=r['first_seen'],updated_at=r['updated_at'],evidence=d)

    def candidates(self,limit:Limit=25,offset:Offset=0,site:int|None=Query(None,gt=0),status:Literal['OBSERVED','DEFER']|None=None,user:TokenPayload=Depends(verify_token))->Page[Candidate]:
        self._auth(user);return self._page('candidates',Candidate,self._candidate,select="candidate_key,first_seen,updated_at,json_remove(data,'$.torrent_files','$.file_parse','$.last_decision') AS data",where="(? IS NULL OR json_extract(data,'$.site')=?) AND (? IS NULL OR json_extract(data,'$.status')=?)",args=(site,site,status,status),order='first_seen,candidate_key',limit=limit,offset=offset)

    def candidate(self,candidate_key:Id,user:TokenPayload=Depends(verify_token))->Candidate:
        self._auth(user);r=self._one('candidates','candidate_key',candidate_key)
        data=json.loads(r['data']);r['data']=encoded({k:v for k,v in data.items() if k not in ('torrent_files','file_parse','last_decision')})
        return Candidate(**self._candidate(r))

    def candidate_files(self,candidate_key:Id,section:Literal['torrent_files','file_parse'],limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user);self._one('candidates','candidate_key',candidate_key)
        return self._page('candidates',Row,lambda r:dict(id=str(r['key']),state='OBSERVED',revision='',data=public(json.loads(r['value']))),source='candidates c,json_each(c.data,?) f',select='f.key,f.value',where='c.candidate_key=?',args=('$.'+section,candidate_key),order='f.key',limit=limit,offset=offset)

    @staticmethod
    def _decision(r):return dict(**{k:r[k] for k in ('id','candidate_key','task_id','opportunity_id','status','digest','created_at')},simulation=bool(r['simulation']),evidence=public(json.loads(r['data'])))

    def decisions(self,limit:Limit=25,offset:Offset=0,candidate_key:Id|None=None,task_id:int|None=Query(None,gt=0),status:Literal['ACCEPT','REJECT','DEFER','ENRICH']|None=None,user:TokenPayload=Depends(verify_token))->Page[Decision]:
        self._auth(user);return self._page('candidate_decisions',Decision,self._decision,select="id,candidate_key,task_id,opportunity_id,status,simulation,digest,created_at,json_remove(data,'$.evaluation','$.observed.torrent_files','$.observed.file_parse','$.observed.last_decision') AS data",where='(? IS NULL OR candidate_key=?) AND (? IS NULL OR task_id=?) AND (? IS NULL OR status=?)',args=(candidate_key,candidate_key,task_id,task_id,status,status),order='created_at,id',limit=limit,offset=offset)

    def decision(self,decision_id:Id,user:TokenPayload=Depends(verify_token))->Decision:
        self._auth(user);return Decision(**self._decision(self._one('candidate_decisions','id',decision_id)))

    def decision_plans(self,decision_id:Id,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user);self._one('candidate_decisions','id',decision_id)
        return self.rows('plans',where="json_extract(snapshot,'$.decision_id')=?",args=(decision_id,),order='created_at,id',limit=limit,offset=offset)

    def exclusions(self,limit:Limit=25,offset:Offset=0,active:bool|None=None,candidate_key:Id|None=None,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user)
        return self.rows('exclusions',where="(? IS NULL OR active=?) AND (? IS NULL OR json_extract(criteria,'$.candidate_key')=?)",args=(active,active,candidate_key,candidate_key),order='id',limit=limit,offset=offset)

    @staticmethod
    def _archive(r):return dict(**{k:r[k] for k in ('target_key','state','revision','updated_at','task_id')},facts=public(json.loads(r['data'])))

    def archives(self,limit:Limit=25,offset:Offset=0,state:Literal['PRESENT','MISSING','UNKNOWN','INVALID','ERROR']|None=None,user:TokenPayload=Depends(verify_token))->Page[ArchiveTarget]:
        self._auth(user);return self._page('archive_targets',ArchiveTarget,self._archive,source='archive_targets a LEFT JOIN target_units u USING(target_key)',select='a.*,u.task_id',where='(? IS NULL OR a.state=?)',args=(state,state),order='a.target_key',limit=limit,offset=offset)

    def archive(self,target_key:Key,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->ArchiveDetail:
        self._auth(user);r=self._one('archive_targets','target_key',target_key)
        with self.repository.connection() as db:u=db.execute('SELECT task_id FROM target_units WHERE target_key=?',(target_key,)).fetchone()
        r['task_id']=u[0] if u else None
        return ArchiveDetail(target=ArchiveTarget(**self._archive(r)),versions=self.rows('archive_versions',where='target_key=?',args=(target_key,),order='id',limit=limit,offset=offset))

    def version(self,version_id:Id,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->VersionDetail:
        self._auth(user);r=self._one('archive_versions','id',version_id)
        data=json.loads(r['data']);r['data']=encoded({k:v for k,v in data.items() if k not in ('assets','source_assets','streams')})
        return VersionDetail(version=Row(**self.row(r)),assets=self.rows('archive_assets',source='archive_assets a JOIN archive_locations l ON l.id=a.location_id JOIN archive_contents c ON c.id=l.content_id',select='a.*,l.scope,l.state,l.content_id,c.sha1,c.size,l.data AS location',where='a.version_id=?',args=(version_id,),order='a.file_index,a.location_id',limit=limit,offset=offset),sources=self.rows('archive_sources',where='version_id=?',args=(version_id,),order='at,id',limit=limit,offset=offset))

    @staticmethod
    def _bundle(r):
        d=json.loads(r['data']);return dict(**{k:r[k] for k in ('id','plan_id','rule_id','state','due','revision')},reason=d.get('reason',''),file_count=len(d.get('files',[])),publication_action=d.get('publication_action'),consumer_pending=d.get('consumer_pending'))

    def bundles(self,limit:Limit=25,offset:Offset=0,state:Id|None=None,plan_id:Id|None=None,user:TokenPayload=Depends(verify_token))->Page[Bundle]:
        self._auth(user);return self._page('delivery_bundles',Bundle,self._bundle,where='(? IS NULL OR state=?) AND (? IS NULL OR plan_id=?)',args=(state,state,plan_id,plan_id),order='due,id',limit=limit,offset=offset)

    def bundle(self,bundle_id:Id,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->BundleDetail:
        self._auth(user);r=self._one('delivery_bundles','id',bundle_id);d=json.loads(r['data']);p=self._one('plans','id',r['plan_id']);s=json.loads(p['snapshot'])
        p['snapshot']=encoded({k:v for k,v in s.items() if k not in ('torrent_files','local_assets','targets','current')})
        files=self._page('delivery_bundles',Row,lambda x:dict(id=str(json.loads(x['value'])['file_index']),state=json.loads(x['value'])['state'],revision=str(r['revision']),data=public(json.loads(x['value']))),source="delivery_bundles b,json_each(b.data,'$.files') f",select='f.value',where='b.id=?',args=(bundle_id,),order="json_extract(f.value,'$.file_index')",limit=limit,offset=offset)
        return BundleDetail(bundle=Bundle(**self._bundle(r)),authority=public(dict(plan=self.row(p),target_count=len(d.get('vector',{})),manifest={k:v for k,v in d.get('manifest',{}).items() if k not in ('assets','targets','publication')},rule_revision=d.get('rule_revision'))),files=files,
            actions=self.rows('plan_actions',where='plan_id=?',args=(r['plan_id'],),order='created_at,id',limit=limit,offset=offset),
            receipts=self.rows('action_receipts',source='action_receipts r JOIN plan_actions a ON a.id=r.action_id',select='r.*',where='a.plan_id=?',args=(r['plan_id'],),order='r.id',limit=limit,offset=offset),
            shared_references=self.rows('plans',where="id!=? AND json_extract(snapshot,'$.downloader')=? AND json_extract(snapshot,'$.infohash')=?",args=(p['id'],s['downloader'],s['infohash']),order='id',limit=limit,offset=offset),cleanup=public({k:v for k,v in d.items() if 'clean' in k or k in ('cancel_intent','downloader_removed','downloader_remove_intent')}))

    def policies(self,limit:Limit=25,offset:Offset=0,category_id:Id|None=None,user:TokenPayload=Depends(verify_token))->Page[PolicyView]:
        self._auth(user)
        from .policy import CATEGORIES
        config=self.plugin.configuration.view()['config'];p=config['policy'];runtime=self.plugin.runtime
        # The config is one bounded row; JSON1 performs binding paging/counting.
        key=self.plugin.configuration.key
        return self._page('settings',PolicyView,lambda r:dict(category_id=r['category_id'],binding=r['binding'],publication_revision=p['classification_revision'],policy_revision=runtime.policy.semantic_hash if runtime and runtime.policy else None,dimensions=list(CATEGORIES[r['binding']][3]),configuration=public(dict(overrides=p['overrides'],admission=p['admission'],locks=p['locks'],lifecycle=config['lifecycle'],all_bindings=CATEGORIES))),source="settings s,json_each(s.value,'$.config.policy.bindings') j",select='j.key AS category_id,j.value AS binding',where='s.key=? AND (? IS NULL OR j.key=?)',args=(key,category_id,category_id),order='j.key',limit=limit,offset=offset)

    def bundle_records(self,bundle_id:Id,section:Literal['vector','assets','publication'],limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user);self._one('delivery_bundles','id',bundle_id)
        path={'vector':'$.vector','assets':'$.manifest.assets','publication':'$.manifest.publication'}[section]
        return self._page('delivery_bundles',Row,lambda r:dict(id=str(r['key']),state='FROZEN',revision='',data=public(json.loads(r['value']))),source='delivery_bundles b,json_each(b.data,?) f',select='f.key,f.value',where='b.id=?',args=(path,bundle_id),order='f.key',limit=limit,offset=offset)

    def policy(self,category_id:Id,user:TokenPayload=Depends(verify_token))->PolicyView:
        self._auth(user)
        page=self.policies(limit=1,offset=0,category_id=category_id,user=user)
        item=next((x for x in page.items if x.category_id==category_id),None)
        if not item:raise HTTPException(404,'POLICY_NOT_FOUND')
        return item

    @staticmethod
    def _source(r):return dict(**{k:r[k] for k in Source.model_fields if k!='config'},config=public(json.loads(r['config'])))

    def sources(self,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Source]:
        self._auth(user);return self._page('discovery_sources',Source,self._source,order='source_id',limit=limit,offset=offset)

    def catalog(self,user:TokenPayload=Depends(verify_token))->ActionResult:
        self._auth(user)
        from .discovery import ROUTES, ROUTE_PROVENANCE
        return ActionResult(state='AVAILABLE',result=dict(routes=[dict(key=k,label=v[0],media_type=v[1],provenance=ROUTE_PROVENANCE) for k,v in ROUTES.items()]))

    @staticmethod
    def _record(r):return dict(**{k:r[k] for k in DiscoveryRecord.model_fields if k not in ('evidence','raw','visible')},visible=bool(r['visible']),raw=public(json.loads(r['raw'])),evidence=public(json.loads(r['data'])))

    def records(self,limit:Limit=25,offset:Offset=0,state:Id|None=None,source_id:Id|None=None,view:Literal['all','latest12','recognized','unrecognized']='all',user:TokenPayload=Depends(verify_token))->Page[DiscoveryRecord]:
        self._auth(user);where="visible=1 AND (? IS NULL OR state=?) AND (? IS NULL OR source_id=?)"
        if view=='recognized':where+=" AND json_type(data,'$.identity')='object'"
        if view=='unrecognized':where+=" AND json_type(data,'$.identity') IS NULL"
        return self._page('discovery_records',DiscoveryRecord,self._record,where=where,args=(state,state,source_id,source_id),order='id DESC',limit=min(limit,12) if view=='latest12' else limit,offset=offset)

    def record_targets(self,record_id:int,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user);self._one('discovery_records','id',record_id)
        return self.rows('discovery_targets',where='record_id=?',args=(record_id,),order='target_key',limit=limit,offset=offset)

    def statistics(self,source_id:Id|None=None,user:TokenPayload=Depends(verify_token))->ActionResult:
        self._auth(user)
        from .discovery import DiscoveryService
        return ActionResult(state='AVAILABLE',result=DiscoveryService.statistics(self,source_id=source_id))

    def samples(self,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user);return self.rows('parse_samples',order='sample_key',limit=limit,offset=offset)

    def sample(self,sample_key:Key,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user);self._one('parse_samples','sample_key',sample_key)
        return self.rows('parse_history',where='sample_key=?',args=(sample_key,),order='created_at,digest',limit=limit,offset=offset)

    def task_observations(self,task_id:int,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user);self._one('tasks','id',task_id)
        return self.rows('observations',where='opportunity_id IN (SELECT id FROM opportunities WHERE task_id=?)',args=(task_id,),order='opportunity_id,target_key',limit=limit,offset=offset)

    def task_plans(self,task_id:int,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user);self._one('tasks','id',task_id)
        return self.rows('plans',where='task_id=?',args=(task_id,),order='created_at,id',limit=limit,offset=offset)

    def plan_rows(self,plan_id:Id,section:Literal['targets','actions','progress','organized','receipts'],limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user);self._one('plans','id',plan_id)
        table={'targets':'plan_targets','actions':'plan_actions','progress':'plan_progress','organized':'organized_assets','receipts':'ingest_receipts'}[section]
        return self.rows(table,where='plan_id=?',args=(plan_id,),order='rowid',limit=limit,offset=offset)

    def plan_files(self,plan_id:Id,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user);self._one('plans','id',plan_id)
        return self._page('plans',Row,lambda r:dict(id=str(json.loads(r['value'])['index']),state='FROZEN',revision='',data=public(json.loads(r['value']))),source="plans p,json_each(p.snapshot,'$.torrent_files') f",select='f.value',where='p.id=?',args=(plan_id,),order="json_extract(f.value,'$.index')",limit=limit,offset=offset)

    def health_rows(self,section:Literal['local_scans','archive_scans','downloads','operations','migration'],limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user)
        table={'local_scans':'reconcile_checkpoints','archive_scans':'archive_scans','downloads':'managed_downloads','operations':'management_operations','migration':'migration_receipts'}[section]
        if section=='migration':
            return self.rows(table,select='id,kind,revision,digest,state,updated_at',limit=limit,offset=offset)
        if section=='downloads':
            return self.rows(table,select='downloader,infohash,marker,add_action,client_id,state,updated_at,json_array_length(file_table) AS file_count',limit=limit,offset=offset)
        return self.rows(table,limit=limit,offset=offset)

    def policy_catalog(self,user:TokenPayload=Depends(verify_token))->ActionResult:
        self._auth(user)
        from .policy import CATEGORIES
        return ActionResult(state='AVAILABLE',result=dict(policies=[dict(binding=k,resolutions=list(v[0]),admission=v[1],source_order=v[2],dimensions=list(v[3])) for k,v in CATEGORIES.items()]))

    def local_scan(self,rule_id:Id,section:Literal['stack','directories','failed_paths'],limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user);scope='local:'+rule_id;self._one('reconcile_checkpoints','scope',scope)
        return self._page('reconcile_checkpoints',Row,lambda r:dict(id=str(r['key']),state='OBSERVED',revision='',data={'entry':public(json.loads(r['value']) if r['type'] in ('object','array') else r['value'])}),source='reconcile_checkpoints c,json_each(c.data,?) j',select='j.key,j.value,j.type',where='c.scope=?',args=('$.'+section,scope),order='j.key',limit=limit,offset=offset)

    def runtime_records(self,limit:Limit=25,offset:Offset=0,user:TokenPayload=Depends(verify_token))->Page[Row]:
        self._auth(user)
        return self.rows('settings',select="key AS id,json_remove(value,'$.plans','$.pending','$.seen','$.scope.provider_rows','$.scope.units') AS data",where="key LIKE 'runtime-result:%' OR key LIKE 'runtime-bundle-result:%' OR key LIKE 'runtime-search:%' OR key LIKE 'runtime-consumer:%' OR key LIKE 'passive-%' OR key IN ('runtime-lane','delivery_tick_cursor','delivery-maintenance')",order='key',limit=limit,offset=offset)

    def ai(self,user:TokenPayload=Depends(verify_token))->AIView:
        from .ai import AIConfig
        self._auth(user);p=self.plugin;c=AIConfig.model_validate(p.config.ai_assist);state=c.model_dump()
        return AIView(state='AVAILABLE' if p.ai else 'DISABLED',config_digest=digest(state),generation=p.generation,
            provider=public({k:v for k,v in state.items() if k not in ('prompt','prompt_backup','prompt_previous_backup')}),runtime=public(p.ai.stats()) if p.ai else None,
            prompt=c.prompt,prompt_backup=c.prompt_backup,prompt_previous_backup=c.prompt_previous_backup,
            meta=public(p.meta_patch.diagnostics()) if hasattr(p,'meta_patch') else {'state':'UNAVAILABLE'},bridge=c.name_recognize_bridge,chat=c.chat_enabled,errors=p.ai_errors)

    def diagnostics(self,user:TokenPayload=Depends(verify_token))->Health:
        self._auth(user);p=self.plugin
        try:
            with self.repository.connection() as db:
                pending=db.execute("SELECT count(*) FROM outbox WHERE state IN ('PENDING','UNKNOWN')").fetchone()[0]
                issued=db.execute("SELECT count(*) FROM plan_actions WHERE state IN ('UNKNOWN','IN_FLIGHT','PUBLISHING','PUBLISH_OUTCOME_UNKNOWN','HANDED_OFF')").fetchone()[0]
                snap=self.snapshot(db)
        except (sqlite3.Error,OSError):raise HTTPException(503,'MANAGEMENT_STORE_UNAVAILABLE') from None
        runtime=p.runtime;services={'runtime':'AVAILABLE' if runtime else 'UNAVAILABLE','selected_libraries':p.config.delivery.get('libraries',{}),'cloud_scopes':list(p.config.delivery.get('cloud_scopes',{})), 'configured_downloaders':[t.downloader for t in p.config.destination_templates]}
        if getattr(p,'adapter',None):
            errors=p.adapter.capabilities();services['native']={'state':'BLOCKED' if errors else 'AVAILABLE','errors':public(errors)}
        worker=getattr(p,'delivery_worker',None);archive=getattr(worker,'archive',None);cloud=getattr(worker,'cloud',None)
        services['emby']={'state':'AVAILABLE' if archive and callable(getattr(archive.sources,'emby_target_page',None)) else 'UNAVAILABLE','remote_health':'NOT_PROBED_BY_GET'}
        services['cloud']={'state':'AVAILABLE' if cloud else 'UNAVAILABLE','remote_health':'NOT_PROBED_BY_GET','methods':{name:callable(getattr(cloud,name,None)) for name in ('stat','rapid','start','pump','move','inventory')}}
        services['consumer']={'state':'AVAILABLE' if runtime and callable(getattr(runtime,'consumer',None)) and archive else 'UNAVAILABLE'}
        services['mapping_revision']=archive.mappings.revision if archive else None
        services['downloaders']={'state':'CONFIGURED' if p.config.destination_templates else 'UNCONFIGURED','remote_health':'NOT_PROBED_BY_GET'}
        services['owner_evidence']={'partial_legacy_ai_runtime_flags':'LEGACY_RUNTIME_FLAGS_UNVERIFIED','unverified_cutover_state':'WAIT_OWNER','force_approve':False}
        safety=bool(pending or issued)
        return Health(database='AVAILABLE',enabled=p.config.enabled,ordinary_work_active=p._ordinary_work_active(),safety_required=safety,safety_active=p.lifecycle_active and safety,dry_run=p.config.dry_run,generation=p.generation,pending=pending,errors=list(p.errors),services=services,scans=self.rows('archive_scans',order='id'),actions=self.rows('plan_actions',where="state IN ('UNKNOWN','IN_FLIGHT','PUBLISHING','PUBLISH_OUTCOME_UNKNOWN','HANDED_OFF')",order='created_at,id'),meta=public(p.meta_patch.diagnostics()) if hasattr(p,'meta_patch') else {'state':'UNAVAILABLE'},ai={'errors':p.ai_errors,'runtime':public(p.ai.stats()) if p.ai else None},snapshot=snap)

    def fence(self,request):
        c=self.plugin.configuration.view()
        if (request.config_revision,request.runtime_generation)!=(c['revision'],self.plugin.generation):raise HTTPException(409,'STALE_CONFIGURATION_OR_RUNTIME')
        return c

    def _facts(self,kind,objects,db):
        facts={};blockers=[];permissions={}
        def rows(table,where,args):
            result=[dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE {where} ORDER BY rowid LIMIT 1001',args)]
            if len(result)>1000:raise HTTPException(409,'EXACT_PREVIEW_SCOPE_LIMIT')
            return result
        if kind=='history':
            ids=objects['record_ids'];facts['records']=rows('discovery_records','id IN (SELECT value FROM json_each(?))',(encoded(ids),))
            if len(facts['records'])!=len(ids):raise HTTPException(404,'DISCOVERY_RECORD_NOT_FOUND')
        elif kind=='archive':
            ids=objects['version_ids'];facts['versions']=rows('archive_versions','id IN (SELECT value FROM json_each(?))',(encoded(ids),))
            if len(facts['versions'])!=len(ids):raise HTTPException(404,'ARCHIVE_VERSION_NOT_FOUND')
            keys=[r['target_key'] for r in facts['versions']]
            facts['targets']=rows('archive_targets','target_key IN (SELECT value FROM json_each(?))',(encoded(keys),))
            facts['units']=rows('target_units','target_key IN (SELECT value FROM json_each(?))',(encoded(keys),))
            if any(r['owner_plan_id'] or r['publish_phase'] in ('PUBLISHING','PUBLISH_OUTCOME_UNKNOWN','HANDED_OFF') for r in facts['units']):blockers.append('TARGET_AUTHORITY_UNSETTLED')
        elif kind in ('cancel','cleanup'):
            items=rows('delivery_bundles','id=?',(objects['bundle_id'],))
            if not items:raise HTTPException(404,'DELIVERY_BUNDLE_NOT_FOUND')
            facts['bundle']=items[0];b=json.loads(items[0]['data']);pid=b['plan_id']
            facts['plan']=rows('plans','id=?',(pid,))[0];s=json.loads(facts['plan']['snapshot'])
            facts['units']=rows('target_units','target_key IN (SELECT key FROM json_each(?))',(encoded(b['vector']),))
            facts['actions']=rows('plan_actions','plan_id=?',(pid,))
            facts['shared']=rows('plans',"id!=? AND authorization IN ('ACTIVE','PREPARED') AND json_extract(snapshot,'$.downloader')=? AND json_extract(snapshot,'$.infohash')=?",(pid,s['downloader'],s['infohash']))
            facts['other_bundles']=rows('delivery_bundles',"id!=? AND EXISTS(SELECT 1 FROM json_each(data,'$.files') f JOIN json_each(?,'$.files') own ON json_extract(f.value,'$.snapshot.path')=json_extract(own.value,'$.snapshot.path') WHERE coalesce(json_extract(f.value,'$.cleaned'),0)=0)",(b['id'],encoded(b)))
            if facts['shared'] or facts['other_bundles']:blockers.append('SHARED_REFERENCE')
            if 'UNKNOWN' in b['state'] or any('UNKNOWN' in f.get('state','') for f in b['files']):blockers.append('EXTERNAL_OUTCOME_UNKNOWN')
            if any(a['state'] in ('UNKNOWN','IN_FLIGHT','PUBLISHING','PUBLISH_OUTCOME_UNKNOWN') for a in facts['actions']):blockers.append('EXTERNAL_OUTCOME_UNKNOWN')
            if kind=='cleanup':
                success=b['state'] in ('WAIT_CONSUMER','CONFIRMED')
                permission={'monitor':'cleanup_success' if success else 'cleanup_abandoned','staging':'cleanup_staging','downloader_task':'remove_downloader_task_enabled','downloader_data':'delete_downloader_data_enabled'}[objects['scope']]
                rules=self.plugin.config.delivery.get('rules',[]);rule=next((r for r in rules if r['id']==b['rule_id']),{})
                permissions[permission]=bool(getattr(self.plugin.config.permissions,permission) and rule.get(permission,False))
                if not permissions[permission]:blockers.append('CLEANUP_PERMISSION_DISABLED')
                if not success and b['state']!='ABANDONED':blockers.append('CLEANUP_NOT_ELIGIBLE')
                if objects['scope']=='staging' and b.get('publication_action'):blockers.append('PUBLISHED_CLEANUP_FORBIDDEN')
        elif kind in ('settings','exclusion','change_source'):
            task=rows('tasks','id=?',(objects['task_id'],))
            if not task:raise HTTPException(404,'TASK_NOT_FOUND')
            facts['task']=task[0]
            if task[0]['state'] not in ('ACTIVE','PASSIVE','PAUSED'):blockers.append('TASK_LIFECYCLE_BLOCKED')
            facts['units']=rows('target_units','task_id=?',(objects['task_id'],))
            facts['plans']=rows('plans','task_id=? AND authorization IN (\'ACTIVE\',\'PREPARED\')',(objects['task_id'],))
            facts['actions']=rows('plan_actions','plan_id IN (SELECT id FROM plans WHERE task_id=?)',(objects['task_id'],))
            if any(a['state'] in ('UNKNOWN','IN_FLIGHT','PUBLISHING','PUBLISH_OUTCOME_UNKNOWN','HANDED_OFF') for a in facts['actions']):blockers.append('EXTERNAL_OUTCOME_UNKNOWN')
            if any(u['publish_phase'] in ('PUBLISHING','PUBLISH_OUTCOME_UNKNOWN','HANDED_OFF') for u in facts['units']):blockers.append('PUBLICATION_UNSETTLED')
            facts['opportunities']=rows('opportunities','task_id=? AND state=\'ACTIVE\'',(objects['task_id'],))
            facts['inputs']=rows('settings','key IN (SELECT \'runtime-input:\'||id FROM opportunities WHERE task_id=? AND state=\'ACTIVE\')',(objects['task_id'],))
            facts['shared']=rows('plans',"task_id!=? AND authorization IN ('ACTIVE','PREPARED') AND EXISTS(SELECT 1 FROM plans p WHERE p.task_id=? AND p.authorization IN ('ACTIVE','PREPARED') AND json_extract(p.snapshot,'$.downloader')=json_extract(plans.snapshot,'$.downloader') AND json_extract(p.snapshot,'$.infohash')=json_extract(plans.snapshot,'$.infohash'))",(objects['task_id'],objects['task_id']))
            if facts['shared']:blockers.append('SHARED_REFERENCE')
        elif kind=='revoke':
            facts['exclusion']=rows('exclusions','id=?',(objects['exclusion_id'],))
            if not facts['exclusion']:raise HTTPException(404,'EXCLUSION_NOT_FOUND')
        elif kind.startswith('ai_'):
            ai=self.plugin.ai
            with ai.lock if ai else nullcontext():
                facts['ai']={'generation':getattr(ai,'generation',None),'epoch':getattr(ai,'epoch',None),'sessions':dict(getattr(ai,'session_epochs',{}))}
                if ai:
                    facts['ai'].update(cache=sorted(ai.cache),bridge=sorted(ai.bridge_cache),queue=sorted(ai.queue),chat_queue=sorted(ai.chat_queue),pending=sorted(ai.pending),session_digest=digest(ai.sessions))
            if kind!='ai_prompt' and not ai:blockers.append('AI_SERVICE_UNAVAILABLE')
        facts['config']=self.plugin.configuration.view();facts['runtime_generation']=self.plugin.generation
        facts['exclusions']=rows('exclusions','1',())
        facts['planner_revisions']=rows('settings',"key='planner_revisions'",())
        return facts,permissions,blockers

    def preview(self,kind,objects,request,user):
        self._auth(user);self.fence(request);actor=str(user.username);objects=json.loads(encoded(objects))
        with self.repository.connection(write=True) as db:
            facts,permissions,blockers=self._facts(kind,objects,db)
            if 'revision' in objects and facts.get('bundle',{}).get('revision')!=objects['revision']:raise HTTPException(409,'STALE_BUNDLE')
            if 'generation' in objects and facts.get('task',{}).get('generation')!=objects['generation']:raise HTTPException(409,'STALE_TASK')
            identity='preview:'+uuid4().hex;expiry=(instant()+timedelta(minutes=10)).isoformat()
            data=dict(objects=objects,facts_digest=digest(facts),permissions=permissions,blockers=blockers)
            shown=dict(objects)
            if 'bundle' in facts:
                bundle=json.loads(facts['bundle']['data'])
                shown['files']=[{k:f.get(k) for k in ('file_index','relative_path','sha1','size','state','reader_stopped')} for f in bundle['files']]
                shown['authority']=bundle['vector']
            if 'versions' in facts:shown['versions']=[{k:v[k] for k in ('id','target_key','service','library','active')} for v in facts['versions']]
            if 'ai' in facts:shown['cache_and_session_references']=facts['ai']
            data['display']=public(shown)
            data['revisions']=dict(config_revision=request.config_revision,runtime_generation=request.runtime_generation,facts_digest=data['facts_digest'])
            signature=digest([identity,kind,actor,data,expiry]);db.execute('INSERT INTO management_previews VALUES(?,?,?,?,?,?)',(identity,kind,actor,signature,encoded(data),expiry))
        return Preview(preview_id=identity,preview_digest=signature,kind=kind,objects=data['display'],revisions=data['revisions'],permissions=permissions,blockers=blockers,expires_at=expiry)

    def history_preview(self,request:HistoryPreview,user:TokenPayload=Depends(verify_token))->Preview:
        return self.preview('history',dict(record_ids=sorted(set(request.record_ids))),request,user)

    def cancel_preview(self,bundle_id:Id,request:BundlePreview,user:TokenPayload=Depends(verify_token))->Preview:
        return self.preview('cancel',dict(bundle_id=bundle_id,revision=request.revision,reason=request.reason),request,user)

    def cleanup_preview(self,bundle_id:Id,request:CleanupPreview,user:TokenPayload=Depends(verify_token))->Preview:
        return self.preview('cleanup',dict(bundle_id=bundle_id,revision=request.revision,reason=request.reason,scope=request.scope),request,user)

    def invalidate_preview(self,request:InvalidatePreview,user:TokenPayload=Depends(verify_token))->Preview:
        return self.preview('archive',dict(version_ids=sorted(set(request.version_ids)),reason=request.reason),request,user)

    def settings_preview(self,task_id:int,request:SettingsPreview,user:TokenPayload=Depends(verify_token))->Preview:
        self._auth(user);PolicyConfig(locks=request.locks)
        objects=dict(task_id=task_id,**request.model_dump(exclude={'config_revision','runtime_generation'}))
        return self.preview('settings',objects,request,user)

    def exclusion_preview(self,request:ExclusionPreview,user:TokenPayload=Depends(verify_token))->Preview:
        self._auth(user)
        if request.expires_at:parse(request.expires_at)
        self._one('candidates','candidate_key',request.candidate_key)
        return self.preview('exclusion',request.model_dump(exclude={'config_revision','runtime_generation'}),request,user)

    def change_source_preview(self,candidate_key:Id,request:ExclusionPreview,user:TokenPayload=Depends(verify_token))->Preview:
        self._auth(user);self._one('candidates','candidate_key',candidate_key)
        if candidate_key!=request.candidate_key:raise HTTPException(422,'CANDIDATE_ID_MISMATCH')
        return self.preview('change_source',request.model_dump(exclude={'config_revision','runtime_generation'}),request,user)

    def revoke_preview(self,exclusion_id:Id,request:Fence,user:TokenPayload=Depends(verify_token))->Preview:
        return self.preview('revoke',dict(exclusion_id=exclusion_id),request,user)

    def cache_preview(self,request:Fence,user:TokenPayload=Depends(verify_token))->Preview:return self.preview('ai_cache',{},request,user)
    def sessions_preview(self,request:Fence,user:TokenPayload=Depends(verify_token))->Preview:return self.preview('ai_sessions',{},request,user)
    def prompt_preview(self,request:Fence,user:TokenPayload=Depends(verify_token))->Preview:return self.preview('ai_prompt',{},request,user)

    def operation(self,operation_id:Id,user:TokenPayload=Depends(verify_token))->ActionResult:
        self._auth(user);row=self._one('management_operations','operation_id',operation_id)
        if row['actor']!=str(user.username):raise HTTPException(403,'OPERATION_ACTOR_MISMATCH')
        return ActionResult(state=row['state'],operation_id=operation_id,result=json.loads(row['result']))

    def preview_receipt(self,preview_id:Id,user:TokenPayload=Depends(verify_token))->Preview:
        self._auth(user);row=self._one('management_previews','id',preview_id)
        if row['actor']!=str(user.username):raise HTTPException(403,'PREVIEW_ACTOR_MISMATCH')
        data=json.loads(row['data'])
        return Preview(preview_id=preview_id,preview_digest=row['digest'],kind=row['kind'],objects=data['display'],revisions=data['revisions'],permissions=data['permissions'],blockers=data['blockers'],expires_at=row['expires_at'])

    def apply(self,kind,request,user):
        self._auth(user)
        runtime=self.plugin.runtime
        if runtime:
            with runtime.lock:
                if runtime.busy:raise HTTPException(409,'RUNTIME_BUSY')
                runtime.busy=True
        ai=self.plugin.ai
        try:
            with ai.lock if ai and kind in ('ai_cache','ai_sessions') else nullcontext():return self._apply(kind,request,user)
        finally:
            if runtime:
                with runtime.lock:runtime.busy=False

    def _apply(self,kind,request,user):
        self._auth(user);actor=str(user.username)
        with MUTATION_LOCK:
            with self.repository.connection(write=True) as db:
                preview=db.execute('SELECT * FROM management_previews WHERE id=?',(request.preview_id,)).fetchone()
                if not preview:raise HTTPException(404,'PREVIEW_NOT_FOUND')
                if (preview['actor'],preview['kind'],preview['digest'])!=(actor,kind,request.preview_digest):raise HTTPException(409,'PREVIEW_SCOPE_CONFLICT')
                prior=db.execute('SELECT * FROM management_operations WHERE operation_id=?',(request.operation_id,)).fetchone()
                if prior:
                    if (prior['preview_id'],prior['actor'])!=(request.preview_id,actor):raise HTTPException(409,'OPERATION_CONFLICT')
                    return ActionResult(state=prior['state'],operation_id=request.operation_id,result=json.loads(prior['result']))
                if parse(preview['expires_at'])<=instant():raise HTTPException(409,'PREVIEW_EXPIRED')
                data=json.loads(preview['data']);objects=data['objects'];facts,permissions,blockers=self._facts(kind,objects,db)
                if digest(facts)!=data['facts_digest']:raise HTTPException(409,'STALE_PREVIEW')
                if blockers:raise HTTPException(409,blockers[0])
                db.execute('INSERT INTO management_operations VALUES(?,?,?,\'UNKNOWN\',\'{}\')',(request.operation_id,request.preview_id,actor))
                result=self.local_apply(kind,objects,facts,db,actor,request)
                if result is not None:
                    result=public(result);db.execute("UPDATE management_operations SET state='APPLIED',result=? WHERE operation_id=?",(encoded(result),request.operation_id))
                    return ActionResult(state='APPLIED',operation_id=request.operation_id,result=result)
            # Durable UNKNOWN precedes external operations. Replays never dispatch twice.
            try:
                result=self.external_apply(kind,objects,actor,request)
                state='UNKNOWN' if 'UNKNOWN' in str(result) else 'APPLIED'
            except Exception:
                result={'reason':'ACTION_OUTCOME_UNCONFIRMED'};state='UNKNOWN'
            result=result if kind=='ai_prompt' else public(result)
            with self.repository.connection(write=True) as db:db.execute('UPDATE management_operations SET state=?,result=? WHERE operation_id=?',(state,encoded(result),request.operation_id))
            return ActionResult(state=state,operation_id=request.operation_id,result=result)

    def local_apply(self,kind,o,facts,db,actor,request):
        if kind=='history':
            count=db.execute('UPDATE discovery_records SET visible=0 WHERE id IN (SELECT value FROM json_each(?))',(encoded(o['record_ids']),)).rowcount
            self.repository._audit(db,None,'DISCOVERY_HISTORY_HIDE:'+request.preview_id,actor);return dict(changed=count)
        if kind=='archive':
            archive=getattr(getattr(self.plugin,'delivery_worker',None),'archive',None)
            if not archive:raise HTTPException(409,'ARCHIVE_UNAVAILABLE')
            db.execute('UPDATE archive_versions SET active=0 WHERE id IN (SELECT value FROM json_each(?))',(encoded(o['version_ids']),))
            for key in sorted({v['target_key'] for v in facts['versions']}):archive._sync(db,key,'PRESENT' if archive._live_versions(db,key).fetchone() else 'INVALID','management:'+request.preview_id,diagnostics=['LOGICALLY_INVALIDATED'])
            self.repository._audit(db,None,'ARCHIVE_INVALIDATE:'+request.preview_id,actor);return dict(invalidated=o['version_ids'])
        if kind=='revoke':
            db.execute('UPDATE exclusions SET active=0 WHERE id=?',(o['exclusion_id'],));self.repository._audit(db,None,'EXCLUSION_REVOKE:'+o['exclusion_id'],actor);return dict(revoked=o['exclusion_id'])
        if kind in ('exclusion','change_source'):
            keys=set(o['target_keys']);owned={u['target_key'] for u in facts['units']}
            if not keys<=owned:raise HTTPException(409,'TARGET_SCOPE_CHANGED')
            Exclusions(self.repository).add('exclusion:'+request.preview_id,dict(candidate_key=o['candidate_key'],targets=sorted(keys)),reason=o['reason'],expires_at=o.get('expires_at'),db=db)
            if kind=='change_source':
                for plan in facts['plans']:
                    s=json.loads(plan['snapshot'])
                    if s['candidate_key']!=o['candidate_key']:continue
                    vector={u['target_key']:{k:u[k] for k in ('owner_plan_id','generation','publish_phase','current_revision')} for u in facts['units'] if u['target_key'] in keys and u['owner_plan_id']==plan['id']}
                    if vector:self.plugin.authority.cancel(plan['id'],vector,reason=o['reason'],db=db)
            self.repository._audit(db,o['task_id'],'RESOURCE_EXCLUDE:'+request.preview_id,actor)
            return dict(exclusion_id='exclusion:'+request.preview_id,replan='COMMON_NEXT_TICK' if kind=='change_source' else 'NOT_REQUESTED')
        if kind=='settings':
            if not self.plugin.runtime:raise HTTPException(409,'RUNTIME_UNAVAILABLE')
            return self.plugin.runtime.settings(o,db=db,actor=actor)
        return None

    def external_apply(self,kind,o,actor,request):
        p=self.plugin
        if kind in ('ai_cache','ai_sessions'):
            if not p.ai:raise ValueError('AI_SERVICE_UNAVAILABLE')
            if kind=='ai_cache':p.ai.clear_cache(actor)
            else:p.ai.clear_sessions(actor)
            return dict(cleared=kind)
        if kind=='ai_prompt':
            c=p.configuration.view();ai=c['config']['ai_assist']
            preview=p.configuration.preview({'ai_assist':{'prompt':DEFAULT_PROMPT,'prompt_backup':ai['prompt'],'prompt_previous_backup':ai['prompt_backup']}},c['revision'],c['digest'],actor)
            if not preview['valid']:raise ValueError('INVALID_CONFIGURATION')
            return dict(native_save_required=True,configuration=preview)
        runtime=p.runtime
        if not runtime:raise ValueError('RUNTIME_UNAVAILABLE')
        with runtime.stages.lease():
            worker=runtime.scope_worker(o['bundle_id'])
            if kind=='cancel':
                b=worker.bundle(o['bundle_id']);s=runtime.authority.plan(b['plan_id'])['snapshot']
                return worker.cancel(b['id'],reason=o['reason'],exclusion_id='cancel:'+request.preview_id,criteria=dict(candidate_key=s['candidate_key'],targets=sorted(b['vector'])))
            runtime.check();return worker.cleanup(o['bundle_id'],scope=o['scope'])

    async def run(self,request,user,function,*,ordinary=True):
        self._auth(user);self.fence(request);runtime=self.plugin.runtime
        if not runtime:raise HTTPException(503,'RUNTIME_UNAVAILABLE')
        with runtime.lock:
            if runtime.busy:raise HTTPException(409,'RUNTIME_BUSY')
            runtime.busy=True
        def invoke():
            runtime.deadline=time.monotonic()+runtime.config.recovery.seconds
            runtime.candidates.deadline=runtime.deadline
            runtime.management_no_ai=True
            try:
                self.fence(request)
                if ordinary:runtime.check()
                return function(runtime)
            finally:
                runtime.deadline=None;runtime.candidates.deadline=None;runtime.management_no_ai=False
        try:
            result=await runtime.stages.run(invoke)
            return ActionResult(state=str(result.get('state',result.get('status','OBSERVED'))),operation_id=getattr(request,'operation_id',None),result=public(result))
        except HTTPException:raise
        except (sqlite3.Error,OSError):raise HTTPException(503,'MANAGEMENT_DEPENDENCY_UNAVAILABLE') from None
        except (ValueError,KeyError,TypeError,AttributeError) as error:
            raise HTTPException(409,runtime.reason(error)) from None
        finally:
            with runtime.lock:runtime.busy=False

    async def immediate(self,task_id:int,request:Immediate,user:TokenPayload=Depends(verify_token))->ActionResult:
        def action(runtime):
            task=self._one('tasks','id',task_id);op=self._one('opportunities','id',request.opportunity_id)
            if task['generation']!=request.generation or task['state'] not in ('ACTIVE','PASSIVE'):raise ValueError('TASK_GENERATION_CHANGED')
            if op['task_id']!=task_id or op['state']!='ACTIVE' or sorted(json.loads(op['scope']))!=sorted(request.target_keys):raise ValueError('OPPORTUNITY_SCOPE_CHANGED')
            key='management-immediate:'+request.operation_id;prior=self.repository.setting(key)
            signature=digest([task_id,request.model_dump()])
            if prior:
                if prior['digest']!=signature:raise ValueError('OPERATION_CONFLICT')
                return prior['result']
            self.repository.setting(key,dict(digest=signature,result=dict(state='UNKNOWN',reason='IMMEDIATE_IN_PROGRESS')))
            result=runtime.work(runtime.scheduler.opportunity(op['id']),deadline=runtime.deadline,immediate=True)
            self.repository.setting(key,dict(digest=signature,result=result));return result
        return await self.run(request,user,action)

    async def search(self,request:Search,user:TokenPayload=Depends(verify_token))->ActionResult:
        from .candidates import SearchBudget
        def action(runtime):
            if not set(request.site_ids)<=set(runtime.config.candidates.site_ids):raise ValueError('SITE_NOT_AUTHORIZED')
            budget=SearchBudget(keywords=request.keywords_limit,**request.model_dump(include={'pages','concurrency','results','requests','interval'}))
            rows=runtime.candidates.search(request.site_ids,request.keywords,budget,deadline=runtime.deadline)
            return dict(state='SEARCHED',candidate_keys=[r['candidate_key'] for r in rows],errors=runtime.candidates.search_errors)
        return await self.run(request,user,action)

    async def refresh_candidate(self,candidate_key:Id,request:Fence,user:TokenPayload=Depends(verify_token))->ActionResult:
        from .candidates import SearchBudget
        def action(runtime):
            row=json.loads(self._one('candidates','candidate_key',candidate_key)['data'])
            if row['site'] not in runtime.config.candidates.site_ids:raise ValueError('SITE_NOT_AUTHORIZED')
            runtime.candidates.refresh(candidate_key,SearchBudget(**runtime.config.candidates.model_dump(include={'keywords','pages','concurrency','results','requests','interval'})))
            return dict(state='REFRESHED',candidate_key=candidate_key)
        return await self.run(request,user,action)

    async def evaluate(self,request:Evaluate,user:TokenPayload=Depends(verify_token))->ActionResult:
        def action(runtime):
            if not runtime.pipeline:raise ValueError('CANDIDATE_PIPELINE_UNAVAILABLE')
            saved=self.repository.setting('runtime-input:'+request.opportunity_id)
            if not saved or saved['task_id']!=request.task_id:raise ValueError('OPPORTUNITY_SCOPE_CHANGED')
            if request.candidate_key not in runtime.candidates.runtime:raise ValueError('RESOURCE_REFRESH_REQUIRED')
            result=runtime.evaluate(saved,request.candidate_key,simulation=request.simulation,assistance=False)
            return dict(state='EVALUATED',decision_id=result['decision_id'],decision_digest=result['decision_digest'])
        return await self.run(request,user,action)

    def simulate(self,request:Simulation,user:TokenPayload=Depends(verify_token))->Decision:
        self._auth(user);self.fence(request);runtime=self.plugin.runtime
        if not runtime or not runtime.policy:raise HTTPException(409,'POLICY_UNAVAILABLE')
        from .policy import Version
        raw=request.candidate.model_dump(exclude_none=True)
        if request.sample_key:
            sample=self._one('parse_samples','sample_key',request.sample_key);inputs=json.loads(sample['inputs'])
            raw.update(title=inputs['title'],description=inputs.get('subtitle') or '')
        policy=runtime.policy
        if request.category_id not in policy.bindings:raise HTTPException(404,'POLICY_NOT_FOUND')
        classification=dict(state='complete',policy_revision=policy.classification_revision,effective={'category_id':request.category_id})
        current=[Version(v.version_id,policy.normalize(v.raw.model_dump(exclude_none=True),current=True),active=v.active,reliable=v.reliable) for v in request.current]
        d=policy.compare(policy.normalize(raw),current,classification,identity_ok=True,scope_ok=True,locked=runtime.config.policy.locks)
        output=dict(plans=[],status=d.status,reason=d.reason,decisions={'simulation':dict(status=d.status,action=d.action,rank=list(d.rank),comparisons=list(d.comparisons),evidence_keys=list(d.evidence_keys))})
        ref=append(self.repository,'simulation:'+uuid4().hex,[],output,simulation=True,observed=raw,context=dict(policy=policy.semantic_hash,parse=runtime.meta.corrector.revision,config_revision=request.config_revision),sanitize=runtime.public_evidence)
        return self.decision(ref['decision_id'],user=user)

    async def retry(self,bundle_id:Id,request:Retry,user:TokenPayload=Depends(verify_token))->ActionResult:
        def action(runtime):
            key='management-retry:'+request.operation_id;signature=digest([bundle_id,request.model_dump()])
            prior=self.repository.setting(key)
            if prior:
                if prior['digest']!=signature:raise ValueError('OPERATION_CONFLICT')
                return prior['result']
            worker=runtime.scope_worker(bundle_id);b=worker.bundle(bundle_id)
            if (b['revision'],b['state'])!=(request.revision,request.expected_state):raise ValueError('STALE_BUNDLE')
            if 'UNKNOWN' in b['state'] or any(f['state']=='UNKNOWN' for f in b['files']):raise ValueError('EXTERNAL_OUTCOME_UNKNOWN')
            eligible={'transfer':{'PREPARED','UPLOADING','WAITING','BLOCKED'},'publish':{'REMOTE_VERIFIED'},'consumer':{'WAIT_CONSUMER'}}
            if b['state'] not in eligible[request.phase]:raise ValueError('RETRY_PHASE_NOT_ELIGIBLE')
            self.repository.setting(key,dict(digest=signature,result=dict(state='UNKNOWN',reason='RETRY_IN_PROGRESS')))
            if request.phase=='consumer':result=runtime.consumer(bundle_id)
            elif request.phase=='publish':result=worker.publish(bundle_id)
            else:result=worker.reconcile(bundle_id,limits=dict(seconds=runtime.config.recovery.seconds))
            self.repository.setting(key,dict(digest=signature,result=result));return result
        return await self.run(request,user,action)

    async def archive_refresh(self,request:ArchiveRefresh,user:TokenPayload=Depends(verify_token))->ActionResult:
        def action(runtime):
            if not runtime.delivery:raise ValueError('ARCHIVE_UNAVAILABLE')
            archive=runtime.delivery.archive;archive.mappings.scoped(request.service,request.library)
            # Keys are persisted identities, never arbitrary library/path selectors.
            for key in request.target_keys:self._one('archive_targets','target_key',key)
            return archive.reconcile(request.service,request.library,target_keys=request.target_keys,scan_id=request.scan_id,limits=dict(pages=runtime.config.recovery.pages,page_size=min(runtime.config.recovery.entries,100),items=runtime.config.recovery.entries),deadline=runtime.deadline)
        return await self.run(request,user,action)

    async def mapping_test(self,request:MappingTest,user:TokenPayload=Depends(verify_token))->ActionResult:
        def action(runtime):
            if not runtime.delivery:raise ValueError('ARCHIVE_UNAVAILABLE')
            archive=runtime.delivery.archive;scan=self._one('archive_scans','id',request.scan_id)
            with self.repository.connection() as db:item=db.execute('SELECT data FROM archive_scan_items WHERE scan_id=? AND item_id=?',(request.scan_id,request.item_id)).fetchone()
            if not item:raise HTTPException(404,'ARCHIVE_ITEM_NOT_FOUND')
            rules=archive.mappings.scoped(scan['service'],scan['library'])
            if request.mapping_id not in {r['id'] for r in rules}:raise ValueError('MAPPING_SCOPE_CHANGED')
            raw=json.loads(item[0]);raw=raw.get('item',raw);proof=[]
            for source in (raw.get('MediaSources') or [])[:100]:
                path=source.get('Path');item_path=path if str(path).lower().endswith('.strm') else raw.get('Path')
                rule,internal,check=archive.mappings.resolve(scan['service'],scan['library'],item_path,path)
                if rule['id']!=request.mapping_id:raise ValueError('MAPPING_SCOPE_CHANGED')
                proof.append(dict(mapping_id=rule['id'],mapping_revision=archive.mappings.revision,raw_hash=check,internal_path_ref=digest(internal)))
            if not proof:raise ValueError('MEDIA_SOURCES_INCOMPLETE')
            return dict(state='MAPPING_VERIFIED',steps=proof)
        return await self.run(request,user,action)

    async def health_reconcile(self,request:Reconcile,user:TokenPayload=Depends(verify_token))->ActionResult:
        def action(runtime):
            if request.component in ('delivery','consumer'):
                worker=runtime.scope_worker(request.object_id);bundle=worker.bundle(request.object_id)
                if str(bundle['revision'])!=request.revision:raise ValueError('STALE_BUNDLE')
                if request.component=='consumer':return runtime.consumer(request.object_id)
                with runtime.safety_reads():return worker.safety_reconcile(request.object_id,limits=dict(seconds=runtime.config.recovery.seconds))
            if request.component=='local':
                runtime.check()
                from .delivery import LocalReconciler
                rule=runtime.delivery.rules.get(request.object_id) if runtime.delivery else None
                if not rule or str(rule['revision'])!=request.revision:raise ValueError('STALE_RULE')
                return LocalReconciler(self.repository,list(runtime.delivery.rules.values())).scan(request.object_id,limits=dict(entries=runtime.config.recovery.entries,seconds=runtime.config.recovery.seconds),force=True)
            if request.component=='ownership':
                row=self._one('tasks','id',int(request.object_id))
                if str(row['generation'])!=request.revision:raise ValueError('STALE_TASK')
                runtime.owner.reconcile(task_id=row['id']);return dict(state='OBSERVED',task_id=row['id'])
            raise ValueError('USE_EXACT_ARCHIVE_REFRESH')
        return await self.run(request,user,action,ordinary=False)

    async def download_reconcile(self,downloader:Id,infohash:Annotated[str,Field(pattern=r'^[a-fA-F0-9]{40}$|^[a-fA-F0-9]{64}$')],request:DownloadObservation,user:TokenPayload=Depends(verify_token))->ActionResult:
        def action(runtime):
            from .execution import StrictExecutor
            plan=runtime.authority.plan(request.plan_id);s=plan['snapshot']
            if (s['downloader'],s['infohash'])!=(downloader,infohash.lower()):raise ValueError('DOWNLOAD_SCOPE_CHANGED')
            def no_dispatch(plan):raise ValueError('OBSERVATION_ONLY')
            return StrictExecutor(self.repository,runtime.clients,revalidate=no_dispatch).reconcile(request.plan_id)
        return await self.run(request,user,action,ordinary=False)

    async def resume(self,plan_id:Id,request:PlanAction,user:TokenPayload=Depends(verify_token))->ActionResult:
        def action(runtime):
            if not runtime.pipeline:raise ValueError('CANDIDATE_PIPELINE_UNAVAILABLE')
            plan=runtime.authority.plan(plan_id)
            if plan['task_generation']!=request.task_generation or plan['snapshot'].get('decision_digest')!=request.decision_digest:raise ValueError('STALE_PLAN')
            saved=self.repository.setting('runtime-input:'+plan['opportunity_id'])
            runtime.recover(plan,saved);runtime.pipeline.revalidate(plan)
            return runtime.pipeline.resume(plan_id)
        return await self.run(request,user,action)

    async def organize_reconcile(self,plan_id:Id,request:PlanAction,user:TokenPayload=Depends(verify_token))->ActionResult:
        def action(runtime):
            from .execution import StrictExecutor,Organizer,HostOrganization
            plan=runtime.authority.plan(plan_id)
            if plan['task_generation']!=request.task_generation:raise ValueError('STALE_PLAN')
            def no_dispatch(plan):raise ValueError('OBSERVATION_ONLY')
            executor=StrictExecutor(self.repository,runtime.clients,revalidate=no_dispatch)
            return Organizer(executor,HostOrganization(None,repository=self.repository)).reconcile(plan_id)
        return await self.run(request,user,action,ordinary=False)

    def apply_history(self,request:Apply,user:TokenPayload=Depends(verify_token))->ActionResult:return self.apply('history',request,user)
    def bound_apply(self,kind,request,user,field,value):
        self._auth(user);row=self._one('management_previews','id',request.preview_id)
        if json.loads(row['data'])['objects'].get(field)!=value:raise HTTPException(409,'PREVIEW_PATH_CONFLICT')
        return self.apply(kind,request,user)

    def apply_cancel(self,bundle_id:Id,request:Apply,user:TokenPayload=Depends(verify_token))->ActionResult:return self.bound_apply('cancel',request,user,'bundle_id',bundle_id)
    def apply_cleanup(self,bundle_id:Id,request:Apply,user:TokenPayload=Depends(verify_token))->ActionResult:return self.bound_apply('cleanup',request,user,'bundle_id',bundle_id)
    def apply_archive(self,request:Apply,user:TokenPayload=Depends(verify_token))->ActionResult:return self.apply('archive',request,user)
    def apply_settings(self,task_id:int,request:Apply,user:TokenPayload=Depends(verify_token))->ActionResult:return self.bound_apply('settings',request,user,'task_id',task_id)
    def apply_exclusion(self,request:Apply,user:TokenPayload=Depends(verify_token))->ActionResult:return self.apply('exclusion',request,user)
    def apply_change_source(self,candidate_key:Id,request:Apply,user:TokenPayload=Depends(verify_token))->ActionResult:return self.bound_apply('change_source',request,user,'candidate_key',candidate_key)
    def apply_revoke(self,exclusion_id:Id,request:Apply,user:TokenPayload=Depends(verify_token))->ActionResult:return self.bound_apply('revoke',request,user,'exclusion_id',exclusion_id)
    def apply_cache(self,request:Apply,user:TokenPayload=Depends(verify_token))->ActionResult:return self.apply('ai_cache',request,user)
    def apply_sessions(self,request:Apply,user:TokenPayload=Depends(verify_token))->ActionResult:return self.apply('ai_sessions',request,user)
    def apply_prompt(self,request:Apply,user:TokenPayload=Depends(verify_token))->ActionResult:return self.apply('ai_prompt',request,user)

    def boundary(self,fn):
        def safe_result(result):
            from .configuration import contains_private
            c=self.plugin.configuration.view()['config']['ai_assist']
            references=[c['endpoint_ref'],*c['credential_refs']]
            raw=result.model_dump(mode='json') if hasattr(result,'model_dump') else result
            for reference in references:
                if reference:
                    try:secret=self.plugin.configuration.secrets.resolve(reference)
                    except Exception:raise HTTPException(503,'PRIVATE_REFERENCE_UNAVAILABLE') from None
                    if contains_private(raw,secret):raise HTTPException(503,'PRIVATE_PROJECTION_BLOCKED')
            return result
        def failure(error):
            if isinstance(error,HTTPException):raise error
            if isinstance(error,(sqlite3.Error,OSError,AttributeError)):raise HTTPException(503,'MANAGEMENT_DEPENDENCY_UNAVAILABLE') from None
            from .runtime import Runtime
            raise HTTPException(409,Runtime.reason(error)) from None
        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def wrapped(*args,**kwargs):
                try:return safe_result(await fn(*args,**kwargs))
                except (HTTPException,ValueError,KeyError,TypeError,sqlite3.Error,OSError,AttributeError) as error:failure(error)
        else:
            @wraps(fn)
            def wrapped(*args,**kwargs):
                try:return safe_result(fn(*args,**kwargs))
                except (HTTPException,ValueError,KeyError,TypeError,sqlite3.Error,OSError,AttributeError) as error:failure(error)
        return wrapped

    def routes(self):
        definitions=[('/tasks',self.tasks,Page[Task]),('/tasks/{task_id}',self.task,TaskDetail),('/candidates',self.candidates,Page[Candidate]),('/candidates/{candidate_key}',self.candidate,Candidate),('/candidate-decisions',self.decisions,Page[Decision]),('/candidate-decisions/{decision_id}',self.decision,Decision),('/archive/targets',self.archives,Page[ArchiveTarget]),('/archive/targets/{target_key}',self.archive,ArchiveDetail),('/archive/versions/{version_id}',self.version,VersionDetail),('/delivery/bundles',self.bundles,Page[Bundle]),('/delivery/bundles/{bundle_id}',self.bundle,BundleDetail),('/policies',self.policies,Page[PolicyView]),('/policies/{category_id}',self.policy,PolicyView),('/diagnostics',self.diagnostics,Health),('/discovery/sources',self.sources,Page[Source]),('/discovery/catalog',self.catalog,ActionResult),('/discovery/records',self.records,Page[DiscoveryRecord]),('/discovery/records/{record_id}/targets',self.record_targets,Page[Row]),('/discovery/statistics',self.statistics,ActionResult),('/parse/samples',self.samples,Page[Row]),('/parse/samples/{sample_key}/history',self.sample,Page[Row]),('/ai',self.ai,AIView)]
        routes=[dict(path=p,methods=['GET'],endpoint=f,response_model=m,auth='bear',route_class_override=PrivateRoute) for p,f,m in definitions]
        for path,fn,model in [('/management/previews/{preview_id}',self.preview_receipt,Preview),('/management/operations/{operation_id}',self.operation,ActionResult)]:
            routes.append(dict(path=path,methods=['GET'],endpoint=fn,response_model=model,auth='bear',route_class_override=PrivateRoute))
        for path,fn in [('/health/local-scans/{rule_id}/{section}',self.local_scan),('/health/runtime-records',self.runtime_records),('/candidates/{candidate_key}/files/{section}',self.candidate_files),('/delivery/bundles/{bundle_id}/records/{section}',self.bundle_records),('/exclusions',self.exclusions),('/candidate-decisions/{decision_id}/plans',self.decision_plans)]:
            routes.append(dict(path=path,methods=['GET'],endpoint=fn,response_model=Page[Row],auth='bear',route_class_override=PrivateRoute))
        # Specific policy catalog precedes the category identifier route.
        routes.insert(0,dict(path='/policies/catalog',methods=['GET'],endpoint=self.policy_catalog,response_model=ActionResult,auth='bear',route_class_override=PrivateRoute))
        for path,fn in [('/tasks/{task_id}/observations',self.task_observations),('/tasks/{task_id}/plans',self.task_plans),('/plans/{plan_id}/files',self.plan_files),('/plans/{plan_id}/records/{section}',self.plan_rows),('/health/records/{section}',self.health_rows)]:
            routes.append(dict(path=path,methods=['GET'],endpoint=fn,response_model=Page[Row],auth='bear',route_class_override=PrivateRoute))
        actions=[('/discovery/history/cleanup',self.history_preview,self.apply_history),('/delivery/{bundle_id}/cancel',self.cancel_preview,self.apply_cancel),('/delivery/{bundle_id}/cleanup',self.cleanup_preview,self.apply_cleanup),('/archive/invalidate',self.invalidate_preview,self.apply_archive),('/tasks/{task_id}/settings',self.settings_preview,self.apply_settings),('/exclusions',self.exclusion_preview,self.apply_exclusion),('/exclusions/{exclusion_id}/revoke',self.revoke_preview,self.apply_revoke),('/candidates/{candidate_key}/change-source',self.change_source_preview,self.apply_change_source),('/ai/cache/clear',self.cache_preview,self.apply_cache),('/ai/sessions/clear',self.sessions_preview,self.apply_sessions),('/ai/prompt/restore',self.prompt_preview,self.apply_prompt)]
        for path,preview,apply in actions:
            routes.extend([dict(path=path+'/'+suffix,methods=['POST'],endpoint=fn,response_model=model,auth='bear',route_class_override=PrivateRoute) for suffix,fn,model in [('preview',preview,Preview),('apply',apply,ActionResult)]])
        for path,fn,model in [('/tasks/{task_id}/immediate',self.immediate,ActionResult),('/candidates/search',self.search,ActionResult),('/candidates/{candidate_key}/refresh',self.refresh_candidate,ActionResult),('/candidates/evaluate',self.evaluate,ActionResult),('/policies/simulate',self.simulate,Decision),('/delivery/{bundle_id}/retry',self.retry,ActionResult),('/archive/refresh',self.archive_refresh,ActionResult),('/archive/mapping-test',self.mapping_test,ActionResult),('/health/reconcile',self.health_reconcile,ActionResult),('/downloads/{downloader}/{infohash}/reconcile',self.download_reconcile,ActionResult),('/plans/{plan_id}/resume',self.resume,ActionResult),('/plans/{plan_id}/organize/reconcile',self.organize_reconcile,ActionResult)]:
            routes.append(dict(path=path,methods=['POST'],endpoint=fn,response_model=model,auth='bear',route_class_override=PrivateRoute))
        for route in routes:route['endpoint']=self.boundary(route['endpoint'])
        return routes
