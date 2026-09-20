"""subscriBetter V3: durable ownership foundation; download/delivery workers are not enabled."""
from threading import RLock
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.sdk.plugin import _PluginBase
from app.sdk.events import eventmanager
from app.sdk.security import verify_token
from app.schemas.token import TokenPayload
from app.schemas.types import ChainEventType, EventType
from .mp_adapter import NativeAdapter, make_target, target_from_native
from .repository import Repository
from .ownership import Guard, Ownership, field
from .meta import MetaCorrector, MetaService, _stored
from .meta_compat import MetaPatch
from .scheduler import Scheduler
from .planner import Authority
from .execution import TransferGuard
from .candidates import CandidateService, HostCandidateAdapter
from .ai import AIConfig, AIService, SecretStore
from .discovery import DiscoveryConfig, DiscoveryService, HostRSSFetcher, SourceConfig
from .configuration import Config, Configuration, host_references
from .migration import Migration, collect_host
from .management import Management
from .runtime import Runtime


class IntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    intent_key: str = Field(min_length=1, max_length=256)
    media_type: Literal["电影", "电视剧"]
    media_source: str = Field(min_length=1, max_length=128)
    media_id: str = Field(min_length=1, max_length=256)
    season: int | None = Field(default=None, ge=0)
    episode_group: str = Field(default="", max_length=256)
    name: str = Field(min_length=1, max_length=300)
    year: str = Field(default="", max_length=4, pattern=r"^(\d{4})?$")
    native_id: int | None = Field(default=None, gt=0)
    adopt: bool = False
    destination_template: str | None = Field(default=None,min_length=1,max_length=256)
    mode: Literal['CONTINUOUS','ONESHOT'] = 'CONTINUOUS'


class TaskView(BaseModel):
    id: int
    media_type: str
    media_source: str
    media_id: str
    season: int | None
    episode_group: str
    state: str
    native_id: int | None
    generation: int
    created_at: str
    updated_at: str


class TaskList(BaseModel):
    tasks: list[TaskView]


class Diagnostics(BaseModel):
    enabled: bool
    ordinary_work_active: bool
    safety_required: bool
    safety_active: bool
    dry_run: bool
    generation: int
    errors: list[str]
    pending: int
    foundation_only: bool = True
    meta: dict = Field(default_factory=dict)
    ai: dict = Field(default_factory=dict)


class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sample_key: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=8192)
    subtitle: str | None = Field(default=None, max_length=8192)
    custom_words: list[Annotated[str, Field(max_length=2048)]] | None = Field(default=None, max_length=100)
    locks: list[Literal["name", "year", "type", "season", "episode", "identity"]] = Field(default_factory=list, max_length=6)
    task_id: int | None = Field(default=None, gt=0)
    is_path: bool = False
    force_video: bool = False


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sample_keys: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(min_length=1, max_length=100)


class StateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["PAUSED", "PASSIVE", "STOPPED"]


class ReleasePreview(BaseModel):
    task_id: int
    revision: str
    changed_fields: list[str]
    resume_state: str
    restores_old_filters: bool


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    native_id: int = Field(gt=0)
    confirm_adoption: Literal[True]


class DiscoverySourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    source: dict | None = None

    @model_validator(mode="after")
    def one_source(self):
        if (self.source_id is None) == (self.source is None):
            raise ValueError("EXACTLY_ONE_SOURCE_REQUIRED")
        return self


class DiscoveryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_ids: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]] = Field(default_factory=list, max_length=100)


class DiscoveryRecordsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    record_ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=500)


class DiscoveryReprocessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    record_ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=100)


class DiscoveryRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_ids: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]] = Field(min_length=1, max_length=100)


class SubscriBetter(_PluginBase):
    plugin_name = "subscriBetter"
    plugin_desc = "V3 统一订阅、榜单发现、调度、交付档案与安全隔离。"
    plugin_icon = "mdi-shield-check"
    plugin_version = "1.0.0"
    plugin_author = "eitelkeit0708"
    author_url = "https://github.com/eitelkeit0708"
    plugin_config_prefix = "subscribetter_"
    plugin_order = 30
    auth_level = 1

    def init_plugin(self, config: dict | None = None):
        if not hasattr(self, "runtime_lock"):
            self.runtime_lock = RLock()
        with self.runtime_lock:
            old_runtime=getattr(self,'runtime',None)
            if old_runtime:old_runtime.stages.retire()
            elif getattr(self,'ai',None):self.ai.close()
            self.runtime=None
            self.ai=None;self.ai_errors=[]
            self.discovery=None;self.discovery_errors=[]
            for event,callback in getattr(self,'ai_listeners',[]):
                eventmanager.remove_event_listener(event,callback)
            self.ai_listeners=[]
            if not old_runtime and getattr(self,'delivery_worker',None):
                from .host_delivery_contract import close_delivery
                try:close_delivery(self.delivery_worker)
                except Exception:pass  # Readers are synchronous and already stopped.
            self.delivery_worker=None
            if hasattr(self, "meta_patch"):
                self.meta_patch.uninstall()
            self.generation = getattr(self, "generation", 0) + 1
            self.lifecycle_active = True
            self.errors = []
            self.running = False
            try:
                self.repository = Repository(self.get_data_path() / "subscribetter.sqlite3")
                self.secret_store=SecretStore(self.get_data_path())
                self.configuration=Configuration(self.repository,self.__class__.__name__,self.secret_store,
                    self.update_config,lambda config:host_references(config,self))
                self.config=Config.model_validate(self.configuration.view()['config'])
                self.migration=Migration(self.repository,self.configuration,self.secret_store,lambda:collect_host(self))
                self.config=self.configuration.initialize(config or {})
                self.errors.extend(self.configuration.errors)
                self.management=Management(self)
                self.scheduler = Scheduler(self.repository)
                self.authority = Authority(self.repository)
                self.candidates = CandidateService(self.repository, HostCandidateAdapter())
                self.transfer_guard = TransferGuard(self.repository)
                self.meta_corrector = MetaCorrector(self.config.meta_protected_names)
                self.meta_service = MetaService(self.repository, self.meta_corrector)
                self.meta_patch = MetaPatch(self.meta_corrector)
                if self.configuration.ready and self.config.enabled and not self.config.dry_run and self.config.enhance_host_meta:
                    try:
                        from app.chain.system import SystemChain
                        self.meta_patch.install(SystemChain.get_server_local_version())
                    except Exception:
                        self.meta_patch.state = "INITIALIZATION_FAILED"
                self.adapter = NativeAdapter()
                self.ownership = Ownership(self.repository, self.adapter)
                self.guard = Guard(self.repository, self.adapter, self._auto_scope)
                try:
                    discovery_config = DiscoveryConfig.model_validate(self.config.discovery)
                    generation = self.generation
                    async def fetch(url, source, budget):
                        configured = discovery_config.rsshub_base_url if source.kind == "rsshub" else source.url
                        return await HostRSSFetcher(configured, proxy=source.proxy,
                                                    allowed_private_ranges=discovery_config.allowed_private_ranges)(url, source, budget)
                    self.discovery = DiscoveryService(
                        self.repository, self.ownership, self.meta_service, HostCandidateAdapter(), discovery_config,
                        fetch=fetch, inventory=self._discovery_inventory,
                        inventory_refresh=self._discovery_inventory_refresh,
                        authorized=self._discovery_authorized,
                        excluded=self._discovery_excluded,
                        accepted=self._discovery_accept,
                        current=lambda: self._ordinary_work_active() and self.generation == generation,
                        owner_check=self._ai_owner, owner_snapshot=self._discovery_snapshot,
                        instance_id=self.__class__.__name__, ai=self.ai)
                except Exception:
                    self.discovery = None
                    self.discovery_errors.append("INVALID_DISCOVERY_CONFIG")
                try:
                    ai_config=AIConfig.model_validate(self.config.ai_assist)
                    if ai_config.enabled:
                        import httpx
                        proxy=None
                        if ai_config.proxy:
                            from app.sdk.config import settings
                            proxy=settings.PROXY.get('https') or settings.PROXY.get('http')
                        generation=self.generation
                        self.ai=AIService(self.repository,ai_config,httpx.Client,
                            self.secret_store.resolve,generation=generation,
                            current=lambda:self._ordinary_work_active() and self.generation==generation,
                            instance_id=self.__class__.__name__,proxy=proxy,
                            owner_check=self._ai_owner,owner_snapshot=self._ai_snapshot,notify=self._ai_notify,
                            assistance_gate=lambda:self._ai_owner('name_assistance',self.__class__.__name__,
                                self.ai.config_digest,'internal') is not None)
                        self.candidates.ai=self.ai
                        if ai_config.name_recognize_bridge:self.ai_listeners.append((ChainEventType.NameRecognize,self.ai_name))
                        if ai_config.chat_enabled:self.ai_listeners.append((EventType.UserMessage,self.ai_message))
                        for event,callback in self.ai_listeners:eventmanager.add_event_listener(event,callback,priority=30)
                except Exception:
                    if self.ai:self.ai.close()
                    for event,callback in self.ai_listeners:eventmanager.remove_event_listener(event,callback)
                    self.ai_listeners=[]
                    self.ai=None;self.candidates.ai=None;self.ai_errors.append('INVALID_AI_CONFIG')
                if self.config.delivery:
                    try:
                        from .host_delivery_contract import build_delivery
                        self.delivery_worker=build_delivery(self,self.config.delivery)
                    except Exception:self.errors.append('DELIVERY_CONFIGURATION_FAILED')
                if self.discovery:
                    self.discovery.authorized = self._discovery_authorized
                    self.discovery.ai = self.ai
                self.runtime=Runtime(self)
                if self.discovery:self.discovery.owner=self.runtime
                self.errors.extend(self.adapter.capabilities())
                self.auto_baseline = set(self.repository.setting("auto_baseline") or [])
                auto_types = sorted(self.config.auto_types) if self.config.enabled and not self.config.dry_run else []
                if auto_types and self.repository.setting("auto_active_types") != auto_types:
                    self.auto_baseline.update(row["id"] for row in self.adapter.list())
                    self.repository.setting("auto_baseline", sorted(self.auto_baseline))
                self.repository.setting("auto_active_types", auto_types)
                self.running = not self.errors
                self.errors.extend(f"SHELL_PAUSE_FAILED:{sid}" for sid in self.ownership.ensure_paused())
            except Exception:
                if not hasattr(self,'config'):self.config=Config()
                self.errors.append("INITIALIZATION_FAILED")
                try:
                    if self.update_config(self.config.model_dump()) is False:self.errors.append('SAFE_CONFIG_RESTORE_FAILED')
                except Exception:self.errors.append('SAFE_CONFIG_RESTORE_FAILED')
            # Guard listeners stay installed when ordinary work is disabled.
            for event, callback in self._listeners():
                eventmanager.add_event_listener(event, callback, priority=1)

    def _listeners(self):
        return [(ChainEventType.ResourceSelection, self.resource_selection),
                (ChainEventType.ResourceDownload, self.resource_download),
                (ChainEventType.SubscribeCompletionCheck, self.completion_check),
                (ChainEventType.TransferIntercept, self.transfer_intercept),
                (EventType.SubscribeAdded, self.subscribe_added),
                (EventType.SubscribeDeleted, self.subscribe_deleted)]

    def _auto_scope(self, native: dict) -> bool:
        return bool(self._ordinary_work_active()
                    and native.get("type") in self.config.auto_types
                    and native.get("id") not in self.auto_baseline)

    def get_state(self) -> bool:
        # V3 gates this plugin's entire event-owner class through this public hook.
        return bool(getattr(self, "lifecycle_active", False)
                    and (self._safety_required() or (self.running and self.config.enabled)))

    def _safety_required(self) -> bool:
        return bool(getattr(getattr(self, "guard", None), "known_ids", ())
                    or (getattr(self, 'transfer_guard', None) and self.transfer_guard.required()))

    def transfer_intercept(self, event):
        if getattr(self, 'transfer_guard', None):
            self.transfer_guard.intercept(event)

    def _ordinary_work_active(self) -> bool:
        return bool(getattr(self, "lifecycle_active", False) and self.running and not self.errors
                    and getattr(getattr(self,'configuration',None),'ready',False)
                    and self.config.enabled and not self.config.dry_run)

    def stop_service(self):
        if not hasattr(self, "runtime_lock"):
            return
        with self.runtime_lock:
            self.lifecycle_active = False
            self.running = False
            self.generation += 1
            runtime=getattr(self,'runtime',None)
            if runtime:runtime.stages.retire()
            elif self.ai:self.ai.close()
            for event,callback in self.ai_listeners:eventmanager.remove_event_listener(event,callback)
            self.ai_listeners=[]
            if not runtime and getattr(self,'delivery_worker',None):
                from .host_delivery_contract import close_delivery
                try:close_delivery(self.delivery_worker)
                except Exception:self.errors.append('DELIVERY_CLOSE_FAILED')
                self.delivery_worker=None
            if hasattr(self, "meta_patch"):
                self.meta_patch.uninstall()
            if hasattr(self, "ownership"):
                try:
                    self.errors.extend(f"SHELL_PAUSE_FAILED:{sid}" for sid in self.ownership.ensure_paused())
                except Exception:
                    self.errors.append("SAFE_STOP_PAUSE_FAILED")
            for event, callback in self._listeners()[4:]:
                eventmanager.remove_event_listener(event, callback)

    @staticmethod
    def get_command():
        return []

    def get_service(self):
        # The host owns scheduling; old clients close only after real I/O drains.
        services = [{"id": "SubscriBetter_ownership", "name": "subscriBetter 订阅状态核对", "trigger": "interval",
                 "func": self.reconcile, "kwargs": {"seconds": self.config.recovery.active_poll_seconds,"max_instances":1}, "func_kwargs": {"generation": self.generation}},
                {"id":"SubscriBetter_ai","name":"subscriBetter AI 有界队列","trigger":"interval",
                 "func":self.ai_tick,"kwargs":{"seconds":1,"max_instances":1},"func_kwargs":{"generation":self.generation}}]
        if self.discovery and self.discovery.config.enabled and self.discovery.config.sources:
            from apscheduler.triggers.cron import CronTrigger
            services.append({"id":"SubscriBetter_discovery","name":"subscriBetter 榜单发现",
                             "trigger":CronTrigger.from_crontab(self.discovery.config.cron),
                             "func":self.discovery_tick,"kwargs":{"max_instances":1},
                             "func_kwargs":{"generation":self.generation}})
        return services

    def _ai_owner(self,*args):
        provider=getattr(getattr(self,'migration',None),'unique_owner',None)
        return provider(*args) if callable(provider) else None

    def _ai_snapshot(self,module,instance_id,config_digest,route_scope):
        return self._discovery_snapshot(module,instance_id,config_digest,route_scope)

    def _discovery_snapshot(self,module,instance_id,config_digest,route_scope):
        provider=getattr(getattr(self,'migration',None),'owner_snapshot',None)
        if callable(provider):return provider(module,instance_id,config_digest,route_scope)
        return {"fingerprint":"","overlaps":[],"unclassified":["owner_snapshot_unbound"]}

    def _discovery_inventory(self,target):
        runtime=getattr(self,'runtime',None)
        if runtime is None:
            return {"state":"UNKNOWN","evidence_ref":None,"diagnostics":["ARCHIVE_UNAVAILABLE"]}
        return runtime.inventory_view(target)

    def _discovery_inventory_refresh(self,target,source):
        runtime=getattr(self,'runtime',None)
        if not runtime:return {"state":"UNKNOWN","evidence_ref":None,"diagnostics":["ARCHIVE_UNAVAILABLE"]}
        scope=runtime.scope(target)
        category=scope['classification'].get('effective',{}).get('category_id')
        destination=source.destination_category_bindings.get(category)
        template=source.destination_templates.get(destination)
        if not template:raise ValueError('EXACT_DESTINATION_TEMPLATE_REQUIRED')
        return runtime.inventory(target,template_id=template)

    def _discovery_authorized(self,target,source,destination):
        runtime=getattr(self,'runtime',None);template=source.destination_templates.get(destination)
        if not runtime or not template:return False
        try:runtime.check();runtime.destination(runtime.scope(target),template);return True
        except ValueError:return False

    def _discovery_accept(self,row,target,source,snapshot):
        with self.repository.connection() as db:
            opportunity=db.execute("SELECT id FROM opportunities WHERE task_id=? AND state='ACTIVE' ORDER BY created_at LIMIT 1",(row['id'],)).fetchone()
        saved=self.repository.setting('runtime-input:'+opportunity['id']) if opportunity else None
        if not saved:raise ValueError('RUNTIME_ADMISSION_REQUIRED')
        return dict(opportunity_id=opportunity['id'],target_units=saved['scope']['units'])

    def _discovery_excluded(self,target):
        from .execution import Exclusions
        return Exclusions(self.repository).matches_target(target)

    def _ai_notify(self,message):
        from app.schemas.types import MessageType
        self.post_message(mtype=MessageType.Plugin,title=self.plugin_name,text=message)

    def ai_name(self,event):
        with self.runtime_lock:runtime,meta,owner=self.ai,self.meta_service,self.runtime
        if runtime and owner:
            with owner.stages.lease():runtime.name_event(event,meta)

    def ai_message(self,event):
        with self.runtime_lock:runtime,owner=self.ai,self.runtime
        if runtime and owner:
            with owner.stages.lease():runtime.enqueue_chat(event,self.post_message)

    def ai_tick(self,generation=None):
        # HTTP and message sends never hold the plugin ownership/lifecycle lock.
        with self.runtime_lock:
            if generation is not None and generation!=self.generation:return
            runtime,meta,owner=self.ai,self.meta_service,self.runtime
        if runtime and owner:
            with owner.stages.lease():runtime.drain(meta)

    async def discovery_tick(self,generation=None,source_ids=None):
        with self.runtime_lock:
            runtime=self.discovery
            if generation is not None and generation!=self.generation:return {"sources":{},"reason":"STALE_GENERATION"}
            if not runtime or not runtime.config.enabled or not self._ordinary_work_active():return {"sources":{},"reason":"DISCOVERY_DISABLED"}
            owner=self.runtime
        with owner.stages.lease():return await runtime.run(source_ids)

    async def reconcile(self, generation: int | None = None):
        with self.runtime_lock:
            if generation is not None and generation != self.generation:
                return
            runtime=getattr(self,'runtime',None)
            if not runtime:
                return
        result=await runtime.tick()
        self.guard.refresh_cache()
        return result

    def _adopt_new(self, native):
        self.runtime.submit(f"native:{native['id']}", target_from_native(native), native,
                              "automatic", native_id=native["id"], adopt=True)
        self.guard.refresh_cache()

    def subscribe_added(self, event):
        with self.runtime_lock:
            if not self._ordinary_work_active():
                return
            runtime=self.runtime;adapter=self.adapter
        try:
            with runtime.stages.lease():
                sid = field(event.event_data, "subscribe_id")
                native = adapter.get(sid)
                if native and self._auto_scope(native):
                    runtime.submit(f"native:{native['id']}",target_from_native(native),native,'automatic',native_id=native['id'],adopt=True)
                    self.guard.refresh_cache()
        except Exception:
            self.errors = list(dict.fromkeys(self.errors + ["AUTOMATIC_HANDOFF_FAILED"]))

    def subscribe_deleted(self, event):
        with self.runtime_lock:
            try:
                sid = field(event.event_data, "subscribe_id")
                task = self.repository.by_native_id(sid)
                if task and task["state"] != "RELEASED_NATIVE" and self.adapter.get(sid) is None:
                    self.repository.set_state(task["id"], "STOPPED", "native-deleted")
            except Exception:
                self.errors = list(dict.fromkeys(self.errors + ["DELETION_RECONCILE_FAILED"]))

    def resource_selection(self, event):
        if hasattr(self, "guard"):
            self.guard.selection(event)

    def resource_download(self, event):
        if hasattr(self, "guard"):
            self.guard.download(event)

    def completion_check(self, event):
        if hasattr(self, "guard"):
            self.guard.completion(event)

    @staticmethod
    def _authorize(user: TokenPayload):
        if not isinstance(user, TokenPayload) or user.super_user is not True:
            raise HTTPException(403, "Administrator permission required")

    def _writes_enabled(self):
        if not self._ordinary_work_active():
            raise HTTPException(409, "Enable plugin and disable dry-run before changing native ownership")

    def diagnostics(self, user: TokenPayload = Depends(verify_token)) -> Diagnostics:
        self._authorize(user)
        try:
            pending = len(self.repository.pending_actions(1000))
        except Exception:
            pending = 0
            self.errors = list(dict.fromkeys(self.errors + ["OWNERSHIP_STORE_UNAVAILABLE"]))
        if getattr(getattr(self, "guard", None), "unhealthy", False):
            self.errors = list(dict.fromkeys(self.errors + ["GUARD_INPUT_OR_STORE_UNHEALTHY"]))
        safety_required = self._safety_required()
        return Diagnostics(enabled=self.config.enabled, ordinary_work_active=self._ordinary_work_active(),
                           safety_required=safety_required, safety_active=self.lifecycle_active and safety_required,
                           dry_run=self.config.dry_run, generation=self.generation,
                           errors=list(dict.fromkeys(self.errors)), pending=pending,
                           meta=self.meta_patch.diagnostics() if hasattr(self, "meta_patch") else {"state": "UNAVAILABLE"},
                           ai={"errors":self.ai_errors,"runtime":self.ai.stats() if self.ai else None})

    def parse_sample(self, request: ParseRequest, user: TokenPayload = Depends(verify_token)) -> dict:
        self._authorize(user)
        with self.runtime_lock:
            try:
                fields = request.model_dump()
                key = fields.pop("sample_key")
                return _stored(self.meta_service.parse(key, **fields).record())
            except ValueError as error:
                raise HTTPException(409, str(error)) from None

    def parse_samples(self, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0),
                      user: TokenPayload = Depends(verify_token)) -> dict:
        self._authorize(user)
        return {"samples": self.repository.parse_samples(limit=limit, offset=offset)}

    def replay_samples(self, request: ReplayRequest, user: TokenPayload = Depends(verify_token)) -> dict:
        self._authorize(user)
        with self.runtime_lock:
            try:
                return _stored({"results": self.meta_service.replay(request.sample_keys)})
            except ValueError as error:
                raise HTTPException(409, str(error)) from None

    def tasks(self, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
              user: TokenPayload = Depends(verify_token)) -> TaskList:
        self._authorize(user)
        return TaskList(tasks=[TaskView.model_validate(row) for row in self.repository.list_tasks(limit, offset)])

    def submit_intent(self, request: IntentRequest, user: TokenPayload = Depends(verify_token)) -> TaskView:
        self._authorize(user)
        with self.runtime_lock:
            self._writes_enabled()
            runtime=self.runtime;guard=self.guard
        try:
            with runtime.stages.lease():
                target = make_target(request.media_type, request.media_source, request.media_id, request.season, request.episode_group)
                row = runtime.submit(request.intent_key, target, {"name": request.name, "year": request.year, "username": user.username},
                                            str(user.username), request.native_id, request.adopt,template_id=request.destination_template,mode=request.mode)
                guard.refresh_cache()
                return TaskView.model_validate(row)
        except ValueError as error:
            raise HTTPException(409,Runtime.reason(error)) from None

    def change_state(self, task_id: int, request: StateRequest, user: TokenPayload = Depends(verify_token)) -> TaskView:
        self._authorize(user)
        with self.runtime_lock:
            try:
                row = self.repository.set_state(task_id, request.state, str(user.username))
                self.ownership.ensure_paused()
                return TaskView.model_validate(row)
            except ValueError as error:
                raise HTTPException(409, str(error)) from None

    def release_preview(self, task_id: int, user: TokenPayload = Depends(verify_token)) -> ReleasePreview:
        self._authorize(user)
        try:
            return ReleasePreview.model_validate(self.ownership.release_preview(task_id))
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    def release_native(self, task_id: int, request: ReleaseRequest, user: TokenPayload = Depends(verify_token)) -> TaskView:
        self._authorize(user)
        with self.runtime_lock:
            self._writes_enabled()
            try:
                row = self.ownership.release(task_id, request.revision, str(user.username))
                self.guard.refresh_cache()
                return TaskView.model_validate(row)
            except ValueError as error:
                raise HTTPException(409, str(error)) from None

    def recover_native(self, task_id: int, request: RecoveryRequest, user: TokenPayload = Depends(verify_token)) -> TaskView:
        self._authorize(user)
        with self.runtime_lock:
            self._writes_enabled()
            try:
                row = self.ownership.recover_native(task_id, request.native_id, str(user.username))
                self.guard.refresh_cache()
                return TaskView.model_validate(row)
            except ValueError as error:
                raise HTTPException(409, str(error)) from None

    def discovery_sources(self, user: TokenPayload = Depends(verify_token)) -> dict:
        self._authorize(user)
        return {"catalog": self.discovery.catalog() if self.discovery else [], "errors": self.discovery_errors}

    async def discovery_test(self, request: DiscoverySourceRequest, user: TokenPayload = Depends(verify_token)) -> dict:
        self._authorize(user)
        if not self.discovery: raise HTTPException(409,"Discovery configuration unavailable")
        try:
            proposed = SourceConfig.model_validate(request.source) if request.source is not None else None
            return await self.discovery.test_source(request.source_id, proposed=proposed)
        except ValueError as error:raise HTTPException(409,str(error)) from None

    def discovery_retry_sources(self, request: DiscoveryRetryRequest, user: TokenPayload = Depends(verify_token)) -> dict:
        self._authorize(user)
        with self.runtime_lock:self._writes_enabled()
        if not self.discovery:raise HTTPException(409,"Discovery configuration unavailable")
        try:return {"changed":self.discovery.retry_sources(request.source_ids)}
        except ValueError as error:raise HTTPException(409,str(error)) from None

    async def discovery_run(self, request: DiscoveryRunRequest, user: TokenPayload = Depends(verify_token)) -> dict:
        self._authorize(user)
        with self.runtime_lock:
            self._writes_enabled()
            generation=self.generation
        return await self.discovery_tick(generation,request.source_ids or None)

    def discovery_records(self, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                          state: str | None = None, source_id: str | None = None,
                          view: Literal["all","latest12","recognized","unrecognized"] = "all",
                          user: TokenPayload = Depends(verify_token)) -> dict:
        self._authorize(user)
        if not self.discovery:return {"records":[],"statistics":{"records":{},"targets":{}}}
        return {"records":self.discovery.records(limit=limit,offset=offset,state=state,source_id=source_id,view=view),
                "statistics":self.discovery.statistics()}

    def discovery_reprocess(self, request: DiscoveryReprocessRequest, user: TokenPayload = Depends(verify_token)) -> dict:
        self._authorize(user)
        with self.runtime_lock:self._writes_enabled()
        if not self.discovery:raise HTTPException(409,"Discovery configuration unavailable")
        return {"changed":self.discovery.reprocess(request.record_ids)}

    def discovery_cleanup(self, request: DiscoveryRecordsRequest, user: TokenPayload = Depends(verify_token)) -> dict:
        self._authorize(user)
        raise HTTPException(409,'PREVIEW_REQUIRED')

    def get_api(self):
        definitions = [("/diagnostics", "GET", self.diagnostics, Diagnostics),
                       ("/parse", "POST", self.parse_sample, dict),
                       ("/parse/samples", "GET", self.parse_samples, dict),
                       ("/parse/replay", "POST", self.replay_samples, dict),
                       ("/tasks", "GET", self.tasks, TaskList),
                       ("/intents", "POST", self.submit_intent, TaskView),
                       ("/tasks/{task_id}/state", "POST", self.change_state, TaskView),
                       ("/tasks/{task_id}/release-preview", "GET", self.release_preview, ReleasePreview),
                       ("/tasks/{task_id}/release", "POST", self.release_native, TaskView),
                       ("/tasks/{task_id}/recover", "POST", self.recover_native, TaskView)]
        definitions.extend([
            ("/discovery/sources","GET",self.discovery_sources,dict),
            ("/discovery/sources/retry","POST",self.discovery_retry_sources,dict),
            ("/discovery/test","POST",self.discovery_test,dict),
            ("/discovery/run","POST",self.discovery_run,dict),
            ("/discovery/records","GET",self.discovery_records,dict),
            ("/discovery/reprocess","POST",self.discovery_reprocess,dict),
            ("/discovery/history/cleanup","POST",self.discovery_cleanup,dict)])
        routes=[{"path": path, "methods": [method], "endpoint": endpoint, "response_model": model,
                 "auth": "bear", "summary": endpoint.__name__} for path, method, endpoint, model in definitions]
        if getattr(self,'management',None):routes.extend(self.management.routes())
        from .ui import Views, ParseView, ReplayView, DiscoveryRun, DiscoveryTest, Changed
        views=Views(self);managed=views.routes();replaced={r['path'] for r in managed}
        routes=[r for r in routes if r['path'] not in replaced]
        models={'/parse':ParseView,'/parse/replay':ReplayView,'/discovery/test':DiscoveryTest,'/discovery/run':DiscoveryRun,'/discovery/sources/retry':Changed,'/discovery/reprocess':Changed}
        from .management import PrivateRoute
        for route in routes:
            if route['path'] in models:
                route.update(response_model=models[route['path']],endpoint=views.boundary(route['endpoint']),route_class_override=PrivateRoute)
        routes.extend(managed)
        return routes

    def get_form(self):
        return [{"component": "VForm", "content": [
            {"component": "VAlert", "props": {"type": "info", "variant": "tonal"},
             "text": "关闭普通工作后，已有受管壳仍保持暂停与安全保护；解除保护须显式返回原生控制。"},
            {"component": "VSwitch", "props": {"model": "enabled", "label": "启用普通订阅管理工作"}},
            {"component": "VSwitch", "props": {"model": "dry_run", "label": "只读 / dry-run（保留现有安全隔离）"}},
            {"component": "VSwitch", "props": {"model": "enhance_host_meta", "label": "增强宿主公共解析（普通工作启用且非 dry-run 时生效，影响未受管解析）"}},
            {"component": "VCombobox", "props": {"model": "meta_protected_names", "label": "明确保护的完整片名", "multiple": True, "chips": True}},
            {"component": "VSelect", "props": {"model": "auto_types", "label": "自动纳管启用后的新订阅", "multiple": True, "items": ["电影", "电视剧"]}},
            {"component":"VAlert","props":{"type":"info"},"text":"AI 名称辅助与普通聊天独立配置，默认关闭；凭据仅用私密引用。可选名称事件桥接只读缓存并排队，首次可不返回结果。聊天须明确路由和唯一响应者切换回执，不提供订阅或删除能力；当前宿主不支持定点线程回复。"},
            {"component":"VAlert","props":{"type":"info"},"text":"榜单作品发现使用 /discovery API 与结构化来源配置；PT 下载资源仍走候选管线。自动提交还要求唯一 owner 回执、档案范围和交付规则同时有效。"},
        ]}], Config().model_dump()

    def get_page(self):
        return [{"component": "VAlert", "props": {"type": "info", "variant": "tonal"},
                 "text": f"普通工作：{'运行' if self._ordinary_work_active() else '关闭或受阻'}；已有任务安全保护：{'运行' if self.lifecycle_active and self._safety_required() else '未运行'}。作品发现、PT候选、交付与入库分别保留状态和回执；管理接口仅管理员可用。"}]
