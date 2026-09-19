"""subscriBetter V3: durable ownership foundation; download/delivery workers are not enabled."""
from threading import RLock
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from app.sdk.plugin import _PluginBase
from app.sdk.events import eventmanager
from app.sdk.security import verify_token
from app.schemas.token import TokenPayload
from app.schemas.types import ChainEventType, EventType
from .mp_adapter import NativeAdapter, make_target, target_from_native
from .repository import Repository
from .ownership import Guard, Ownership, field


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    dry_run: bool = True
    auto_types: list[Literal["电影", "电视剧"]] = Field(default_factory=list, max_length=2)


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
    dry_run: bool
    generation: int
    errors: list[str]
    pending: int
    foundation_only: bool = True


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


class SubscriBetter(_PluginBase):
    plugin_name = "subscriBetter"
    plugin_desc = "V3 订阅接管、持久化回执与安全隔离；完整调度和交付正在实现。"
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
            self.generation = getattr(self, "generation", 0) + 1
            self.errors = []
            self.running = False
            try:
                self.config = Config.model_validate(config or {})
            except ValueError:
                self.config = Config()
                self.errors.append("INVALID_CONFIG")
            try:
                self.repository = Repository(self.get_data_path() / "subscribetter.sqlite3")
                self.adapter = NativeAdapter()
                self.ownership = Ownership(self.repository, self.adapter)
                self.guard = Guard(self.repository, self.adapter, self._auto_scope)
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
                self.errors.append("INITIALIZATION_FAILED")
            # Guard listeners stay installed when ordinary work is disabled.
            for event, callback in self._listeners():
                eventmanager.add_event_listener(event, callback, priority=1)

    def _listeners(self):
        return [(ChainEventType.ResourceSelection, self.resource_selection),
                (ChainEventType.ResourceDownload, self.resource_download),
                (ChainEventType.SubscribeCompletionCheck, self.completion_check),
                (EventType.SubscribeAdded, self.subscribe_added),
                (EventType.SubscribeDeleted, self.subscribe_deleted)]

    def _auto_scope(self, native: dict) -> bool:
        return bool(self.running and self.config.enabled and not self.config.dry_run
                    and native.get("type") in self.config.auto_types
                    and native.get("id") not in self.auto_baseline)

    def get_state(self) -> bool:
        return bool(getattr(self, "running", False) and self.config.enabled)

    def stop_service(self):
        if not hasattr(self, "runtime_lock"):
            return
        with self.runtime_lock:
            self.running = False
            self.generation += 1
            if hasattr(self, "ownership"):
                try:
                    self.errors.extend(f"SHELL_PAUSE_FAILED:{sid}" for sid in self.ownership.ensure_paused())
                except Exception:
                    self.errors.append("SAFE_STOP_PAUSE_FAILED")
            for event, callback in self._listeners()[3:]:
                eventmanager.remove_event_listener(event, callback)

    @staticmethod
    def get_command():
        return []

    def get_service(self):
        # The host owns scheduling; no private thread/client survives reload.
        return [{"id": "SubscriBetter_ownership", "name": "subscriBetter 订阅状态核对", "trigger": "interval",
                 "func": self.reconcile, "kwargs": {"seconds": 60, "kwargs": {"generation": self.generation}}}]

    def reconcile(self, generation: int | None = None):
        with self.runtime_lock:
            if generation is not None and generation != self.generation:
                return
            if not self.running:
                return
            try:
                self.ownership.ensure_paused()
                if self.config.enabled and not self.config.dry_run:
                    self.ownership.reconcile()
                    for native in self.adapter.list():
                        if self._auto_scope(native) and not self.repository.by_native_id(native["id"]):
                            self._adopt_new(native)
                self.guard.refresh_cache()
            except Exception:
                self.errors = list(dict.fromkeys(self.errors + ["RECONCILE_FAILED"]))

    def _adopt_new(self, native):
        self.ownership.submit(f"native:{native['id']}", target_from_native(native), native,
                              "automatic", native_id=native["id"], adopt=True)
        self.guard.refresh_cache()

    def subscribe_added(self, event):
        with self.runtime_lock:
            if not self.running or not self.config.enabled or self.config.dry_run:
                return
            try:
                sid = field(event.event_data, "subscribe_id")
                native = self.adapter.get(sid)
                if native and self._auto_scope(native):
                    self._adopt_new(native)
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
        if not self.running or self.errors or not self.config.enabled or self.config.dry_run:
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
        return Diagnostics(enabled=self.get_state(), dry_run=self.config.dry_run, generation=self.generation,
                           errors=list(dict.fromkeys(self.errors)), pending=pending)

    def tasks(self, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
              user: TokenPayload = Depends(verify_token)) -> TaskList:
        self._authorize(user)
        return TaskList(tasks=[TaskView.model_validate(row) for row in self.repository.list_tasks(limit, offset)])

    def submit_intent(self, request: IntentRequest, user: TokenPayload = Depends(verify_token)) -> TaskView:
        self._authorize(user)
        with self.runtime_lock:
            self._writes_enabled()
            try:
                target = make_target(request.media_type, request.media_source, request.media_id, request.season, request.episode_group)
                row = self.ownership.submit(request.intent_key, target, {"name": request.name, "year": request.year, "username": user.username},
                                            str(user.username), request.native_id, request.adopt)
                self.guard.refresh_cache()
                return TaskView.model_validate(row)
            except ValueError as error:
                raise HTTPException(409, str(error)) from None

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

    def get_api(self):
        definitions = [("/diagnostics", "GET", self.diagnostics, Diagnostics),
                       ("/tasks", "GET", self.tasks, TaskList),
                       ("/intents", "POST", self.submit_intent, TaskView),
                       ("/tasks/{task_id}/state", "POST", self.change_state, TaskView),
                       ("/tasks/{task_id}/release-preview", "GET", self.release_preview, ReleasePreview),
                       ("/tasks/{task_id}/release", "POST", self.release_native, TaskView),
                       ("/tasks/{task_id}/recover", "POST", self.recover_native, TaskView)]
        return [{"path": path, "methods": [method], "endpoint": endpoint, "response_model": model,
                 "auth": "bear", "summary": endpoint.__name__} for path, method, endpoint, model in definitions]

    def get_form(self):
        return [{"component": "VForm", "content": [
            {"component": "VAlert", "props": {"type": "info", "variant": "tonal"},
             "text": "当前完成订阅接管基础。下载、排序和交付尚未启用。停用不会恢复原生壳；返回原生需先检查差异。"},
            {"component": "VSwitch", "props": {"model": "enabled", "label": "启用订阅管理"}},
            {"component": "VSwitch", "props": {"model": "dry_run", "label": "只读 / dry-run（保留现有安全隔离）"}},
            {"component": "VSelect", "props": {"model": "auto_types", "label": "自动纳管启用后的新订阅", "multiple": True, "items": ["电影", "电视剧"]}},
        ]}], Config().model_dump()

    def get_page(self):
        return [{"component": "VAlert", "props": {"type": "info", "variant": "tonal"},
                 "text": "订阅基础接口已提供：tasks、intents、state、release-preview、release、recover。仅管理员可用；完整调度与交付仍在实现。"}]
