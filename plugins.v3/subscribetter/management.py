"""Typed administrator configuration/migration routes for native Page/Config."""
import base64
import binascii
import json
from typing import Annotated, Literal, Any
from fastapi import Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import Field, SecretStr
from app.sdk.security import verify_token
from app.schemas.token import TokenPayload
from .configuration import Strict, Config, category_catalog
from .ai import normalize_endpoint, digest

Id=Annotated[str,Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9._:-]+$')]
Digest=Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]


class PrivateRoute(APIRoute):
    """Bound before JSON parsing; validation errors never echo submitted secrets."""
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def run(request):
            count=0
            async def receive():
                nonlocal count
                message=await request.receive()
                count+=len(message.get('body',b''))
                if count>3000000:raise HTTPException(413,'REQUEST_TOO_LARGE')
                return message
            try:return await handler(Request(request.scope,receive))
            except RequestValidationError:
                raise HTTPException(422,'INVALID_TYPED_REQUEST') from None
        return run


class ConfigView(Strict):
    revision: int
    digest: str
    config: Config


class Category(Strict):
    id: str
    media_type: Literal['电影','电视剧','音乐']
    name: str
    path: list[str]
    enabled: bool


class CategoryCatalog(Strict):
    revision: int
    schema_version: Literal[2]
    categories: list[Category]


class ConfigPreviewRequest(Strict):
    revision: int = Field(ge=0)
    digest: Digest
    patch: dict
    mode: Literal['merge','replace'] = 'merge'


class ConfigPreview(Strict):
    feature_digests: dict[Literal['ai_assist','discovery'],Digest] = Field(default_factory=dict)
    valid: bool
    errors: list[str]
    config: Config | None
    receipt_id: str | None
    digest: str | None
    revision: int
    changed_fields: list[str]


class CredentialRequest(Strict):
    revision: int = Field(ge=0)
    digest: Digest
    operation_id: Id
    kind: Literal['endpoint','key','legacy_source','legacy_token']
    value: SecretStr


class CredentialView(Strict):
    reference: str
    kind: str
    operation_id: str


class ImportPreviewRequest(Strict):
    source_instance: Id
    source_version: Annotated[str,Field(min_length=1,max_length=128)]
    source_timezone: Annotated[str,Field(max_length=128)] | None = None
    content_base64: SecretStr


class ReceiptRequest(Strict):
    receipt_id: Id
    revision: int = Field(ge=1)
    digest: Digest
    operation_id: Id
    confirm: Literal[True]


class ImportRequest(ReceiptRequest):
    cursor: int = Field(ge=0,le=10000)
    limit: int = Field(default=100,ge=1,le=100)


class HistoryLinkRequest(Strict):
    receipt_id: Id
    ordinal: int = Field(ge=0,le=10000)
    digest: Digest
    task_id: int = Field(gt=0)
    version_id: Id


class SourcePreviewRequest(Strict):
    endpoint_ref: Annotated[str,Field(pattern=r'^secret:[a-f0-9]{32}$')]
    credential_ref: Annotated[str,Field(pattern=r'^secret:[a-f0-9]{32}$')]
    instance_id: Literal['DoubanRankPlusOptimized']
    private_ranges: list[Annotated[str,Field(max_length=64)]] = Field(default_factory=list,max_length=8)


class Feature(Strict):
    module: Literal['name_assistance','name_bridge','discovery']
    instance_id: Id
    config_digest: Digest
    route_scope: str | dict[str,str]


class RecordedFeature(Feature):
    # Historical evidence may name a retired feature; write DTOs stay strict.
    module: str


class SelectedOld(Strict):
    instance_id: Id
    module: Literal['name_bridge','discovery']
    config_digest: Digest
    whole_instance: bool = False
    all_capabilities: list[Literal['discovery']] = Field(default_factory=list,max_length=1)


class CutoverPreviewRequest(Strict):
    configuration_receipt: Id | None = None
    features: list[Feature] = Field(min_length=1,max_length=100)
    selected: list[SelectedOld] = Field(default_factory=list,max_length=30)


class CutoverRequest(ReceiptRequest):
    action: Literal['activate','rollback','rollback_readback']


class FieldMapping(Strict):
    source: Literal['ai','discovery','native','policy','historical']
    field: str
    target: str
    status: Literal['MISSING','ACTION_NOT_REPLAYED','UNMAPPED_RETAINED','MAPPED','HISTORICAL_ONLY',
                    'READBACK_REQUIRED','SUPPORTED_REQUIRES_BINDING','EXPLICIT_EQUIVALENT_REQUIRED']
    raw_pointer: str
    diagnostic: str | None = None
    effective_preview: str | None = None


class SelectedScope(Strict):
    all_discovery_sources: bool
    ranks: list[str]
    rss_line_digests: list[str]


class CutoverStep(Strict):
    instance_id: str
    modules: list[str]
    changes: dict[str,bool]
    before: dict[str,bool]
    before_digest: str
    after_digest: str
    restore_digest: str
    baseline_ref: str
    capability_digest: str
    scope: SelectedScope
    state: Literal['WAIT_HOST_SAVE','CONFIG_READBACK','WAIT_REGISTRATION','RESTORED']
    receipt_id: str | None
    whole_instance: bool


class NextChange(Strict):
    instance_id: str
    changes: dict[str,bool | dict[str,bool]]
    expected_digest: str


class ReadScope(Strict):
    origin: str
    instance_id: str
    methods: list[Literal['GET']]
    paths: list[str]
    private_ranges: list[str]
    redirects: Literal[0]
    requests: Literal[2]
    response_bytes: int
    timeout_seconds: int


class OwnerSnapshot(Strict):
    fingerprint: str | None = None
    overlaps: list[str]
    unclassified: list[str]


class Receipt(Strict):
    configuration_receipt: str | None = None
    base_digest: Digest | None = None
    config_digest: Digest | None = None
    owner_checks: list[OwnerSnapshot] = Field(default_factory=list)
    receipt_id: str
    kind: str
    revision: int
    digest: str
    state: str
    snapshot_ref: str | None = None
    source_instance: str | None = None
    source_version: str | None = None
    fields: list[FieldMapping] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    private_refs: dict[str,str | list[str]] = Field(default_factory=dict)
    requested_features: dict = Field(default_factory=dict)
    memory_cache_restored: bool = False
    history_count: int = 0
    cursor: int = 0
    features: list[RecordedFeature] = Field(default_factory=list)
    steps: list[CutoverStep] = Field(default_factory=list)
    next_changes: list[NextChange] = Field(default_factory=list)
    read_scope: ReadScope | None = None
    result_receipt_id: str | None = None
    proposed_config: Config | None = None
    policy_candidates: dict[str,dict] = Field(default_factory=dict)


class LegacyHistory(Strict):
    title: Any = None
    type: Any = None
    year: Any = None
    poster: Any = None
    overview: Any = None
    tmdbid: Any = None
    doubanid: Any = None
    unique: Any = None
    time: Any = None
    time_full: Any = None
    vote: Any = None
    status: Any = None


class HistoryLink(Strict):
    task_id: int
    version_id: str
    service: str
    library: str
    archive_revision: str
    linked_at: str


class HistoryRow(Strict):
    ordinal: int
    digest: str
    snapshot_ref: str
    raw_pointer: str
    raw: LegacyHistory
    identities: dict[Literal['themoviedb','douban'],str]
    season: None
    source_timezone: str | None
    time_basis: Literal['LEGACY_REPORTED']
    state: Literal['LEGACY_UNVERIFIED']
    diagnostics: list[str]
    link: HistoryLink | None = None


class HistoryView(Strict):
    rows: list[HistoryRow]
    limit: int
    offset: int


class OwnerReceipt(Strict):
    receipt_id: str
    status: Literal['ACTIVE']
    selected_old_disable_receipts: list[str]
    expected_new_feature_set: Feature
    fresh_handler_config_fingerprint: str


class OwnerFeature(Strict):
    feature: Feature
    snapshot: OwnerSnapshot
    receipt: OwnerReceipt | None


class OwnerPlugin(Strict):
    instance_id: str
    source: str
    version: str
    active: bool
    config_digest: str


class OwnerView(Strict):
    configuration: ConfigView
    features: list[OwnerFeature]
    plugins: list[OwnerPlugin]
    diagnostics: list[str]


class Management:
    def __init__(self,plugin):self.plugin=plugin

    def _auth(self,user):self.plugin._authorize(user)

    @staticmethod
    def _call(function,*args):
        try:return function(*args)
        except (ValueError,KeyError,TypeError,OSError,ImportError,AttributeError):raise HTTPException(409,'CONFIGURATION_OR_MIGRATION_CONFLICT') from None

    def configuration(self,user:TokenPayload=Depends(verify_token))->ConfigView:
        self._auth(user);return self.plugin.configuration.view()

    def categories(self,user:TokenPayload=Depends(verify_token))->CategoryCatalog:
        self._auth(user);return self._call(category_catalog,self.plugin)

    def validate(self,request:ConfigPreviewRequest,user:TokenPayload=Depends(verify_token))->ConfigPreview:
        self._auth(user)
        with self.plugin.runtime_lock:return self._call(self.plugin.configuration.preview,request.patch,request.revision,request.digest,str(user.username),request.mode)

    def credential(self,request:CredentialRequest,user:TokenPayload=Depends(verify_token))->CredentialView:
        self._auth(user)
        with self.plugin.runtime_lock:
            state=self.plugin.configuration.view()
            if (state['revision'],state['digest'])!=(request.revision,request.digest):raise HTTPException(409,'STALE_CONFIGURATION')
            value=request.value.get_secret_value()
            if not 1<=len(value)<=4096 or '\x00' in value:raise HTTPException(422,'INVALID_CREDENTIAL_VALUE')
            if request.kind in ('endpoint','legacy_source'):self._call(normalize_endpoint,value,True)
            key='credential:'+self.plugin.__class__.__name__+':'+request.operation_id
            signature=digest([request.kind,value]);prior=self.plugin.repository.setting(key)
            if prior:
                if prior['digest']!=signature:raise HTTPException(409,'OPERATION_CONFLICT')
                return prior['result']
            reference=self._call(self.plugin.secret_store.put,value)
            result=dict(reference=reference,kind=request.kind,operation_id=request.operation_id)
            self.plugin.repository.setting(key,dict(digest=signature,result=result))
            return result

    def import_preview(self,request:ImportPreviewRequest,user:TokenPayload=Depends(verify_token))->Receipt:
        self._auth(user)
        try:raw=base64.b64decode(request.content_base64.get_secret_value(),validate=True)
        except (ValueError,binascii.Error):raise HTTPException(422,'INVALID_OFFLINE_BYTES') from None
        with self.plugin.runtime_lock:
            return self._call(self.plugin.migration.preview_import,raw,request.source_instance,request.source_version,request.source_timezone,str(user.username))

    def import_page(self,request:ImportRequest,user:TokenPayload=Depends(verify_token))->Receipt:
        self._auth(user)
        with self.plugin.runtime_lock:
            def begin():
                self.plugin.configuration.ready=False
                self.plugin.errors=list(dict.fromkeys(self.plugin.errors+['IMPORTED_CONFIG_RELOAD_REQUIRED']))
            return self._call(self.plugin.migration.import_page,request.receipt_id,request.revision,request.digest,
                request.cursor,request.limit,request.operation_id,str(user.username),begin)

    def source_preview(self,request:SourcePreviewRequest,user:TokenPayload=Depends(verify_token))->Receipt:
        self._auth(user)
        return self._call(self.plugin.migration.preview_source,request.endpoint_ref,request.credential_ref,
                          request.instance_id,request.private_ranges,str(user.username))

    async def source_read(self,request:ReceiptRequest,user:TokenPayload=Depends(verify_token))->Receipt:
        self._auth(user)
        try:return await self.plugin.migration.read_source(request.receipt_id,request.revision,request.digest,request.operation_id,str(user.username))
        except (ValueError,KeyError,TypeError,OSError):raise HTTPException(409,'LEGACY_READ_NOT_CONFIRMED') from None

    def receipt(self,receipt_id:Id,user:TokenPayload=Depends(verify_token))->Receipt:
        self._auth(user);return self._call(self.plugin.migration.receipt,receipt_id)

    def history(self,receipt_id:Id,limit:int=Query(100,ge=1,le=100),offset:int=Query(0,ge=0,le=10000),
                user:TokenPayload=Depends(verify_token))->HistoryView:
        self._auth(user)
        return dict(rows=self._call(self.plugin.migration.history,receipt_id,limit,offset),limit=limit,offset=offset)

    def link_history(self,request:HistoryLinkRequest,user:TokenPayload=Depends(verify_token))->HistoryRow:
        self._auth(user)
        with self.plugin.runtime_lock:
            worker=getattr(self.plugin,'delivery_worker',None)
            if not worker:raise HTTPException(409,'ARCHIVE_UNAVAILABLE')
            return self._call(self.plugin.migration.link_history,request.receipt_id,request.ordinal,
                request.digest,request.task_id,request.version_id,worker.archive,str(user.username))

    def cutover_preview(self,request:CutoverPreviewRequest,user:TokenPayload=Depends(verify_token))->Receipt:
        self._auth(user)
        with self.plugin.runtime_lock:
            return self._call(self.plugin.migration.preview_cutover,[f.model_dump() for f in request.features],
                [s.model_dump() for s in request.selected],str(user.username),request.configuration_receipt)

    def cutover(self,request:CutoverRequest,user:TokenPayload=Depends(verify_token))->Receipt:
        self._auth(user)
        with self.plugin.runtime_lock:
            return self._call(self.plugin.migration.advance,request.receipt_id,request.revision,request.digest,request.action,request.operation_id,str(user.username))

    def owners(self,user:TokenPayload=Depends(verify_token))->OwnerView:
        self._auth(user);migration=self.plugin.migration;state=self.plugin.configuration.view();features=[];diagnostics=[];plugins=[]
        config=state['config'];routes=[]
        if config['ai_assist']['name_assistance_enabled']:routes.append(('name_assistance','internal'))
        if config['ai_assist']['name_recognize_bridge']:routes.append(('name_bridge',{'event':'NameRecognize'}))
        routes.extend(('discovery',s['id']) for s in config['discovery']['sources'] if s['enabled'])
        for module,route in routes:
            f=migration.feature(module,route)
            try:fresh=migration.owner_snapshot(**f)
            except Exception:fresh={'overlaps':[],'unclassified':['PUBLIC_INVENTORY_UNAVAILABLE']}
            features.append(dict(feature=f,snapshot=fresh,receipt=migration.unique_owner(**f)))
        try:
            for p in migration.inventory()['plugins']:
                plugins.append(dict(instance_id=p['id'],source=p['source'],version=p['version'],active=p['active'],config_digest=digest(p['config'])))
        except Exception:diagnostics.append('PUBLIC_INVENTORY_UNAVAILABLE')
        return dict(configuration=state,features=features,plugins=plugins,diagnostics=diagnostics)

    def routes(self):
        definitions=[('/configuration','GET',self.configuration,ConfigView),('/configuration/categories','GET',self.categories,CategoryCatalog),('/configuration/preview','POST',self.validate,ConfigPreview),
            ('/credentials','POST',self.credential,CredentialView),('/migration/preview','POST',self.import_preview,Receipt),
            ('/migration/import','POST',self.import_page,Receipt),('/migration/receipts/{receipt_id}','GET',self.receipt,Receipt),
            ('/migration/source/preview','POST',self.source_preview,Receipt),('/migration/source/read','POST',self.source_read,Receipt),
            ('/migration/receipts/{receipt_id}/history','GET',self.history,HistoryView),
            ('/migration/history/link','POST',self.link_history,HistoryRow),
            ('/migration/cutover/preview','POST',self.cutover_preview,Receipt),('/migration/cutover','POST',self.cutover,Receipt),
            ('/migration/owners','GET',self.owners,OwnerView)]
        return [dict(path=p,methods=[m],endpoint=f,response_model=t,auth='bear',summary=f.__name__,route_class_override=PrivateRoute) for p,m,f,t in definitions]
