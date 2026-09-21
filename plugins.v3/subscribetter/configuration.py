"""Strict safe configuration and durable native-Save preflight, without host patches."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from .ai import AIConfig, digest
from .discovery import DiscoveryConfig, SourceConfig, RequestBudget, _digest as discovery_digest
from .scheduler import ScheduleConfig
from .candidates import SearchBudget
from .policy import Policy, _lock_value
from .repository import utcnow

Text = Annotated[str, Field(min_length=1, max_length=256)]
PathText = Annotated[str, Field(min_length=1, max_length=2048)]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


def path(value):
    if (not isinstance(value,str) or not value.startswith('/') or '\\' in value or '\x00' in value
            or any(p in ('.','..','') for p in value.split('/')[1:])):
        raise ValueError('ABSOLUTE_HOST_PATH_REQUIRED')
    return str(PurePosixPath(value))


def within(value, root): return value == root or value.startswith(root.rstrip('/') + '/')


class Permissions(Strict):
    cleanup_success: bool = False
    cleanup_abandoned: bool = False
    cleanup_staging: bool = False
    remove_downloader_task_enabled: bool = False
    delete_downloader_data_enabled: bool = False


class Lifecycle(Strict):
    movie_days: int = Field(default=0, ge=0, le=36500)
    tv_days: int = Field(default=0, ge=0, le=36500)
    expiry_mode: Literal['COMPLETE_COLLECTED','LAST_INGEST'] = 'COMPLETE_COLLECTED'
    completed_mode: Literal['EPISODE','PACK'] = 'EPISODE'
    oneshot_seconds: int = Field(default=86400, ge=1, le=31536000)


class Candidates(Strict):
    site_ids: list[Annotated[int,Field(gt=0)]] = Field(default_factory=list, max_length=100)
    entry: Literal['raw_search'] = 'raw_search'
    keywords: int = Field(default=2,ge=1,le=8)
    pages: int = Field(default=1,ge=1,le=5)
    concurrency: int = Field(default=1,ge=1,le=4)
    results: int = Field(default=100,ge=1,le=1000)
    requests: int = Field(default=4,ge=1,le=64)
    interval: float = Field(default=1,ge=0,le=10)
    refresh_seconds: int = Field(default=300,ge=1,le=86400)
    supplement_limit: int = Field(default=10,ge=0,le=100)
    query_scope: Literal['trusted_aliases','original'] = 'trusted_aliases'
    exclusion_scope: Literal['resource','release','content'] = 'resource'
    exclusion_seconds: int | None = Field(default=None,ge=1,le=31536000)

    @model_validator(mode='after')
    def budget(self):
        SearchBudget(**{k:getattr(self,k) for k in ('keywords','pages','concurrency','results','requests','interval')})
        if len(self.site_ids)!=len(set(self.site_ids)):raise ValueError('DUPLICATE_SITE')
        return self


class Recovery(Strict):
    startup_reconcile: bool = True
    reconcile_seconds: int = Field(default=60,ge=1,le=86400)
    pages: int = Field(default=2,ge=1,le=20)
    entries: int = Field(default=100,ge=1,le=1000)
    seconds: float = Field(default=5,ge=.1,le=30)
    active_poll_seconds: int = Field(default=15,ge=1,le=3600)
    watcher: Literal[False] = False
    consumer_recovery_ref: str = Field(default='',max_length=256)


class Safety(Strict):
    test_roots: list[PathText] = Field(default_factory=list,max_length=30)
    worker_limit: int = Field(default=2,ge=1,le=8)
    network_timeout: float = Field(default=15,ge=1,le=120)
    minimum_free_bytes: int = Field(default=1073741824,ge=0,le=10**15)


class PolicyConfig(Strict):
    bindings: dict[str,str] = Field(default_factory=dict,max_length=100)
    classification_revision: int = Field(default=1,ge=1)
    overrides: dict = Field(default_factory=dict)
    admission: dict | None = None
    locks: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def policy(self):
        if self.bindings: Policy(self.bindings,self.classification_revision,overrides=self.overrides,admission=self.admission)
        elif self.overrides or self.admission: raise ValueError('POLICY_BINDINGS_REQUIRED')
        if set(self.locks)-{'resolution','season','group','platform'}:raise ValueError('UNKNOWN_POLICY_LOCK')
        for name,value in self.locks.items():
            if name=='season':
                if type(value)is not int or not 0<=value<=999:raise ValueError('INVALID_SEASON_LOCK')
            else:_lock_value(name,value)
        if len(json.dumps(self.model_dump(),allow_nan=False))>131072:raise ValueError('POLICY_TOO_LARGE')
        return self


class Destination(Strict):
    id: Text
    category_id: Text
    downloader: Text
    save_path: PathText
    organized_rule: Text | None = None
    sites: list[Annotated[int,Field(gt=0)]] = Field(default_factory=list,max_length=100)
    custom_words: list[Annotated[str,Field(max_length=2048)]] = Field(default_factory=list,max_length=100)


class Mapping(Strict):
    id: Text
    revision: Text
    emby_service: Text
    library_id: Text
    cloud_scope_id: Text
    local_strm_prefix: PathText
    emby_prefix: PathText
    playback_prefix: PathText
    cd2_prefix: PathText
    max_strm_bytes: int = Field(default=65536,ge=1,le=1048576)
    allow_direct: bool = False
    media_source: Text = 'themoviedb'
    episode_group: str = Field(default='',max_length=256)


class CloudScope(Strict):
    cd2_plugin: Text
    p115_plugin: Text
    root: PathText
    allowed_prefixes: list[PathText] = Field(min_length=1,max_length=30)
    p115_parents: dict[str,str] = Field(default_factory=dict,max_length=100)


class DeliveryRule(Permissions):
    id: Text
    enabled: bool = False
    local_root: PathText
    staging_root: PathText
    incoming_root: PathText
    cloud_scope_id: Text
    consumer_roots: list[PathText] = Field(min_length=1,max_length=30)
    excluded_local_roots: list[PathText] = Field(default_factory=list,max_length=30)
    read_roots: list[PathText] = Field(default_factory=list,max_length=30)
    scan_interval: int = Field(default=60,ge=1,le=2592000)
    stable_seconds: int = Field(default=10,ge=0,le=2592000)
    rapid_interval: int = Field(default=60,ge=0,le=2592000)
    rapid_misses: int = Field(default=3,ge=1,le=2592000)
    fallback: bool = False
    unlimited: bool = False
    fallback_gb: float | None = Field(default=None,ge=0,le=100000)
    watcher: Literal[False] = False
    notify_success: bool = False
    notify_error: bool = False

    @model_validator(mode='after')
    def roots(self):
        for value in [self.local_root,self.staging_root,self.incoming_root,*self.consumer_roots,*self.excluded_local_roots,*self.read_roots]:path(value)
        if any(within(self.staging_root,p) or within(p,self.staging_root) for p in [self.incoming_root,*self.consumer_roots]):
            raise ValueError('STAGING_NOT_ISOLATED')
        if not any(within(self.incoming_root,p) for p in self.consumer_roots):raise ValueError('CONSUMER_SCOPE_REQUIRED')
        if any(within(self.local_root,p) or within(p,self.local_root) for p in self.excluded_local_roots):raise ValueError('MONITOR_OUTPUT_OVERLAP')
        if not self.read_roots:self.read_roots=[self.local_root]
        if self.fallback and not self.unlimited and self.fallback_gb is None:raise ValueError('FALLBACK_LIMIT_REQUIRED')
        return self


class DeliveryConfig(Strict):
    cloud_scopes: dict[str,CloudScope] = Field(default_factory=dict,max_length=30)
    libraries: dict[str,list[Text]] = Field(default_factory=dict,max_length=30)
    policy_bindings: dict[str,str] = Field(default_factory=dict,max_length=100)
    classification_revision: int = Field(default=1,ge=1)
    mappings: list[Mapping] = Field(default_factory=list,max_length=100)
    rules: list[DeliveryRule] = Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def dependencies(self):
        if self.policy_bindings:Policy(self.policy_bindings,self.classification_revision)
        for scope in self.cloud_scopes.values():
            path(scope.root)
            for prefix in scope.allowed_prefixes:
                if not within(path(prefix),scope.root):raise ValueError('CLOUD_SCOPE_ESCAPE')
            for prefix,parent in scope.p115_parents.items():
                if not parent.isdecimal() or not any(within(path(prefix),p) for p in scope.allowed_prefixes):raise ValueError('P115_PARENT_UNBOUND')
        for m in self.mappings:
            for value in (m.local_strm_prefix,m.emby_prefix,m.playback_prefix,m.cd2_prefix):path(value)
            if m.library_id not in self.libraries.get(m.emby_service,[]) or m.cloud_scope_id not in self.cloud_scopes:raise ValueError('MAPPING_SERVICE_UNBOUND')
            if not any(within(m.cd2_prefix,p) for p in self.cloud_scopes[m.cloud_scope_id].allowed_prefixes):raise ValueError('MAPPING_CLOUD_ESCAPE')
        for r in self.rules:
            if r.cloud_scope_id not in self.cloud_scopes:raise ValueError('RULE_CLOUD_UNBOUND')
            scope=self.cloud_scopes[r.cloud_scope_id]
            if not all(any(within(x,p) for p in scope.allowed_prefixes) for x in (r.staging_root,r.incoming_root)):raise ValueError('RULE_CLOUD_ESCAPE')
            if r.enabled and (not self.mappings or not self.policy_bindings):raise ValueError('DELIVERY_ARCHIVE_UNBOUND')
            if any(within(r.local_root,m.local_strm_prefix) or within(m.local_strm_prefix,r.local_root) for m in self.mappings):raise ValueError('MONITOR_STRM_OVERLAP')
        for rows in (self.rules,self.mappings):
            if len({r.id for r in rows})!=len(rows):raise ValueError('DUPLICATE_CONFIG_ID')
        return self


class Config(Strict):
    enabled: bool = False
    dry_run: bool = True
    auto_types: list[Literal['电影','电视剧']] = Field(default_factory=list,max_length=2)
    enhance_host_meta: bool = False
    meta_protected_names: list[Annotated[str,Field(min_length=1,max_length=160)]] = Field(default_factory=list,max_length=100)
    ai_assist: dict = Field(default_factory=lambda:AIConfig().model_dump())
    discovery: dict = Field(default_factory=lambda:DiscoveryConfig().model_dump())
    delivery: dict = Field(default_factory=dict)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    lifecycle: Lifecycle = Field(default_factory=Lifecycle)
    candidates: Candidates = Field(default_factory=Candidates)
    schedule: dict = Field(default_factory=lambda:asdict(ScheduleConfig()))
    recovery: Recovery = Field(default_factory=Recovery)
    safety: Safety = Field(default_factory=Safety)
    permissions: Permissions = Field(default_factory=Permissions)
    passive_libraries: dict[str,list[Text]] = Field(default_factory=dict,max_length=30)
    destination_templates: list[Destination] = Field(default_factory=list,max_length=100)
    history_view: Literal['all','latest12','recognized','unrecognized','statistics'] = 'all'
    configuration_receipt: str = Field(default='',max_length=64)
    configuration_revision: int = Field(default=0,ge=0)

    @field_validator('ai_assist')
    @classmethod
    def ai(cls,value):return AIConfig.model_validate(value).model_dump()

    @field_validator('discovery')
    @classmethod
    def sources(cls,value):
        config=DiscoveryConfig.model_validate(value)
        if len(config.cron.split())!=5:raise ValueError('INVALID_CRON')
        if config.cron!='0 8 * * *':
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(config.cron)
        return config.model_dump()

    @field_validator('delivery')
    @classmethod
    def delivery_config(cls,value):return DeliveryConfig.model_validate(value).model_dump() if value else {}

    @field_validator('schedule')
    @classmethod
    def scheduling(cls,value):return asdict(ScheduleConfig(**value))

    @model_validator(mode='after')
    def bindings(self):
        # Validate JSON budgets and all optional durations even while disabled.
        if len(json.dumps(self.model_dump(),allow_nan=False))>524288:raise ValueError('CONFIG_TOO_LARGE')
        for key,value in self.schedule.items():
            if key.endswith('_seconds') and value is not None and (type(value) not in (int,float) or value<0 or value>31536000):raise ValueError('INVALID_SCHEDULE_DURATION')
        for key in ('supersession_limit','failure_limit','max_downloaded_bytes'):
            value=self.schedule[key]
            if value is not None and (type(value)is not int or not 0<=value<=10**15):raise ValueError('INVALID_SCHEDULE_LIMIT')
        for value in self.safety.test_roots:path(value)
        bindings=self.policy.bindings or self.delivery.get('policy_bindings',{})
        templates={t.id:t for t in self.destination_templates}
        if len(templates)!=len(self.destination_templates):raise ValueError('DUPLICATE_TEMPLATE')
        for t in templates.values():
            path(t.save_path)
            if t.category_id not in bindings:raise ValueError('TEMPLATE_CATEGORY_UNBOUND')
            if set(t.sites)-set(self.candidates.site_ids):raise ValueError('TEMPLATE_SITE_UNAUTHORIZED')
            if t.organized_rule and t.organized_rule not in {r['id'] for r in self.delivery.get('rules',[])}:raise ValueError('TEMPLATE_RULE_UNBOUND')
        for service,ids in self.passive_libraries.items():
            if set(ids)-set(self.delivery.get('libraries',{}).get(service,[])):raise ValueError('PASSIVE_LIBRARY_UNBOUND')
        for source in self.discovery['sources']:
            for category,destination in source['destination_category_bindings'].items():
                if category not in bindings:raise ValueError('SOURCE_CATEGORY_UNBOUND')
                template=source['destination_templates'].get(destination)
                if template not in templates or templates[template].category_id!=category:raise ValueError('SOURCE_TEMPLATE_UNBOUND')
            if self.discovery['enabled'] and source['enabled'] and any(v not in templates for v in source['destination_templates'].values()):raise ValueError('SOURCE_TEMPLATE_UNBOUND')
        for rule in self.delivery.get('rules',[]):
            if any(rule[p] and not getattr(self.permissions,p) for p in Permissions.model_fields):raise ValueError('DESTRUCTIVE_PERMISSION_NOT_CONFIRMED')
        ai=self.ai_assist
        if ai['enabled'] and (not ai['endpoint_ref'] or not ai['credential_refs'] or not ai['model']):raise ValueError('AI_PROVIDER_REQUIRED')
        return self


def category_catalog(plugin):
    from app.schemas.category import ClassificationPolicyState
    from app.schemas.types import SystemConfigKey
    if plugin is None or not getattr(plugin,'systemconfig',None):raise ValueError('CLASSIFICATION_UNAVAILABLE')
    active=ClassificationPolicyState.model_validate(plugin.systemconfig.get(SystemConfigKey.MediaClassificationPolicy)).active
    if active.revision<=0 or len(active.categories)>1000:raise ValueError('CLASSIFICATION_UNPUBLISHED_OR_LIMIT')
    if len({c.id for c in active.categories})!=len(active.categories):raise ValueError('CLASSIFICATION_DUPLICATE_ID')
    return dict(revision=active.revision,schema_version=active.schema_version,
        categories=[dict(id=c.id,media_type=c.media_type,name=c.name,path=c.path,enabled=c.enabled) for c in active.categories])


def validate_categories(config,catalog):
    categories={c['id']:c for c in catalog['categories'] if c['enabled']}
    for values,revision in ((config['policy']['bindings'],config['policy']['classification_revision']),
                            (config['delivery'].get('policy_bindings',{}),config['delivery'].get('classification_revision'))):
        if values and (revision!=catalog['revision'] or set(values)-set(categories)):raise ValueError('CLASSIFICATION_STALE_OR_UNBOUND')
        for key,policy in values.items():
            expected='电影' if policy in ('华语电影','外语电影','动画电影') else '电视剧'
            if categories[key]['media_type']!=expected:raise ValueError('CATEGORY_POLICY_TYPE_MISMATCH')


def host_references(config,plugin=None):
    """Validate existing public service references, without querying remote media."""
    delivery=config['delivery'];templates=config['destination_templates']
    bindings=config['policy']['bindings'];delivery_bindings=delivery.get('policy_bindings',{})
    if bindings or delivery_bindings:
        validate_categories(config,category_catalog(plugin))
    if templates or delivery.get('libraries'):
        from app.sdk.services import MediaServerHelper,DownloaderHelper
        for service in delivery.get('libraries',{}):
            if MediaServerHelper().get_service(service,type_filter='emby') is None:raise ValueError('EMBY_REFERENCE_UNAVAILABLE')
        for t in templates:
            if DownloaderHelper().get_service(t['downloader']) is None:raise ValueError('DOWNLOADER_REFERENCE_UNAVAILABLE')
    if delivery.get('cloud_scopes'):
        from app.sdk.plugin import PluginManager
        manager=PluginManager()
        for scope in delivery['cloud_scopes'].values():
            if any(pid not in manager.get_running_plugin_ids() for pid in (scope['cd2_plugin'],scope['p115_plugin'])):raise ValueError('CLOUD_REFERENCE_UNAVAILABLE')
    if config['candidates']['site_ids']:
        from .candidates import HostCandidateAdapter
        sites=HostCandidateAdapter.sites()
        actual={s.get('id') for s in sites if isinstance(s,dict)}
        if set(config['candidates']['site_ids'])-actual:raise ValueError('SITE_REFERENCE_UNAVAILABLE')


def merge(base, patch):
    result=deepcopy(base)
    for key,value in patch.items():
        result[key]=merge(result[key],value) if isinstance(value,dict) and isinstance(result.get(key),dict) else deepcopy(value)
    return result


def content(config):
    return {k:v for k,v in config.items() if k not in ('configuration_revision','configuration_receipt')}


def contains_private(value,secret):
    if isinstance(value,str):return bool(secret) and secret in value
    if value is None or type(value) in (bool,int,float):return contains_private(json.dumps(value),secret)
    if isinstance(value,dict):return any(contains_private(k,secret) or contains_private(v,secret) for k,v in value.items())
    if isinstance(value,list):return any(contains_private(v,secret) for v in value)
    return False


def validation_errors(error):
    # Field names from the schema are useful; attacker-supplied map keys and
    # extra field names must not be echoed as a disguised credential export.
    known=set()
    for model in (Config,AIConfig,DiscoveryConfig,SourceConfig,RequestBudget,
                  Permissions,Lifecycle,Candidates,Recovery,Safety,PolicyConfig,Destination,Mapping,CloudScope,DeliveryRule,DeliveryConfig):
        known.update(model.model_fields)
    return ['.'.join(str(x) if type(x)is int or x in known else '<field>' for x in e['loc'])+':'+e['type']
            for e in error.errors(include_input=False,include_context=False)]


class Configuration:
    def __init__(self,repository,instance_id,secrets,save,validate_references=None):
        self.repository,self.instance_id,self.secrets,self.save=repository,instance_id,secrets,save
        self.validate_references=validate_references
        self.key='configuration:'+instance_id
        self.ready=False;self.errors=[];self.before_apply=None

    def view(self):
        state=self.repository.setting(self.key)
        if not state:
            value=Config().model_dump();state=dict(revision=0,digest=digest(content(value)),config=value)
        return deepcopy(state)

    def validate(self,value):
        config=Config.model_validate(value).model_dump()
        for ref in [config['ai_assist']['endpoint_ref'],*config['ai_assist']['credential_refs']]:
            if ref:
                secret=self.secrets.resolve(ref)
                if contains_private(config,secret):raise ValueError('PRIVATE_VALUE_IN_CONFIGURATION')
        if self.validate_references:self.validate_references(config)
        return config

    def preview(self,patch,revision,expected,actor):
        current=self.view()
        if revision!=current['revision'] or expected!=current['digest']:raise ValueError('STALE_CONFIGURATION')
        try:value=self.validate(merge(current['config'],patch))
        except ValidationError as error:
            return dict(valid=False,errors=validation_errors(error),
                        config=None,receipt_id=None,digest=None,revision=revision,changed_fields=[])
        except Exception:
            # Never expose Pydantic input/ctx, import strings, URLs or credentials.
            return dict(valid=False,errors=['INVALID_CONFIGURATION'],config=None,receipt_id=None,digest=None,revision=revision,changed_fields=[])
        identity='config-'+uuid4().hex;new_digest=digest(content(value))
        value.update(configuration_revision=revision+1,configuration_receipt=identity)
        data=dict(base_revision=revision,base_digest=expected,config=value,actor=actor)
        with self.repository.connection(write=True) as db:
            db.execute('INSERT INTO migration_receipts VALUES(?,?,?,?,?,?,?)',(identity,'CONFIG',1,new_digest,'PREVIEW',json.dumps(data),utcnow()))
        return dict(valid=True,errors=[],config=value,receipt_id=identity,digest=new_digest,revision=revision,
                    feature_digests={'ai_assist':digest(value['ai_assist']),'discovery':discovery_digest(value['discovery'])},
                    changed_fields=sorted(k for k in content(value) if value[k]!=current['config'].get(k)))

    def initialize(self,raw,*,import_marker=None,activate=True):
        current=self.view();persisted_current=deepcopy(current);self.ready=False;self.errors=[]
        try:
            normalized=AIConfig.remove_legacy_chat(current['config']['ai_assist'])
            if normalized!=current['config']['ai_assist']:
                current['config']['ai_assist']=normalized
                current['digest']=digest(content(current['config']))
            initial=self.repository.setting(self.key) is None
            raw=deepcopy(raw or {})
            if initial and 'permissions' not in raw:
                # W07 already stored explicit per-rule permissions. Preserve
                # each identical permission, never infer downloader deletion
                # from a different monitor/staging cleanup switch.
                rules=raw.get('delivery',{}).get('rules',[]) if isinstance(raw.get('delivery',{}),dict) else []
                if isinstance(rules,list):raw['permissions']={p:any(isinstance(r,dict) and r.get(p) is True for r in rules) for p in Permissions.model_fields}
            value=self.validate(merge(current['config'],raw));new_digest=digest(content(value))
            bootstrap_fence=self.repository.setting(self.key+':bootstrap_fence') is True
            needs_preview=not initial and (new_digest!=current['digest'] or bootstrap_fence
                or value['configuration_receipt']!=current['config']['configuration_receipt'])
            if needs_preview:
                with self.repository.connection() as db:
                    row=db.execute("SELECT * FROM migration_receipts WHERE id=? AND kind='CONFIG'",(value['configuration_receipt'],)).fetchone()
                data=json.loads(row['data']) if row else {}
                if (not row or row['state']!='PREVIEW' or row['digest']!=new_digest
                        or data['base_revision']!=current['revision'] or data['base_digest']!=current['digest']
                        or value!=data['config']):raise ValueError('CONFIG_PREFLIGHT_REQUIRED')
            if self.before_apply:self.before_apply(value)
            revision=current['revision']+int(initial or needs_preview)
            value['configuration_revision']=revision
            state=dict(revision=revision,digest=new_digest,config=value)
            with self.repository.connection(write=True) as db:
                persisted=db.execute('SELECT value FROM settings WHERE key=?',(self.key,)).fetchone()
                if persisted and json.loads(persisted[0])!=persisted_current:raise ValueError('STALE_CONFIGURATION')
                if needs_preview:
                    changed=db.execute("UPDATE migration_receipts SET state='APPLIED' WHERE id=? AND kind='CONFIG' AND state='PREVIEW'",(value['configuration_receipt'],)).rowcount
                    if changed!=1:raise ValueError('CONFIG_PREFLIGHT_REQUIRED')
                db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(self.key,json.dumps(state)))
                if import_marker:
                    db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',(import_marker,json.dumps({'config_digest':new_digest})))
                db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (self.key+':bootstrap_fence',json.dumps(bool(initial and value['enabled'] and not value['dry_run']))))
            self.ready=activate
            if initial and (value['enabled'] and not value['dry_run']):
                self.ready=False;self.errors=['INITIAL_ACTIVE_CONFIG_REQUIRES_PREFLIGHT']
        except Exception:
            value=current['config'];self.errors=['INVALID_OR_STALE_CONFIG']
        # Native PUT already persisted its input. Replace it through the public
        # plugin persistence hook even on failure; never retain raw secret keys.
        try:
            if self.save(deepcopy(value)) is False:raise ValueError('SAFE_CONFIG_RESTORE_FAILED')
        except Exception:self.ready=False;self.errors.append('SAFE_CONFIG_RESTORE_FAILED')
        return Config.model_validate(value)

    def import_disabled(self,patch,base_digest,actor):
        marker='legacy-config-import:'+digest([patch,base_digest])
        # Written atomically with configuration state: a crash before history
        # projection cannot cause reimport to overwrite a subsequent user edit.
        if self.repository.setting(marker):return self.view()
        current=self.view()
        if current['digest']!=base_digest:raise ValueError('IMPORT_CONFIG_CHANGED')
        patch=merge(patch,{'enabled':False,'dry_run':True,'ai_assist':{'enabled':False,'name_recognize_bridge':False},'discovery':{'enabled':False}})
        preview=self.preview(patch,current['revision'],current['digest'],actor)
        if not preview['valid']:raise ValueError('IMPORT_CONFIG_INVALID')
        self.initialize(preview['config'],import_marker=marker,activate=False)
        if self.errors:raise ValueError('IMPORT_CONFIG_SAVE_FAILED')
        return self.view()
