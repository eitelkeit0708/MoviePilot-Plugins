"""MoviePilot V2: upload a complete native transfer batch, then hand it to Symedia."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, RLock, Lock
import json
import time

from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, Field

from app.core.event import eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import Response
from app.schemas.types import EventType, NotificationType

from .cd2 import CD2
from .domain import BridgeError, Config, Stopped, nested
from .engine import Engine
from .host import MPHost, value
from .instant import MAX_INSTANT_ATTEMPTS
from .store import Store
from .activity import attention, event, when
from .page import render_page
from .inventory import scan as scan_inventory
from .cleanup import cleanup, cleanup_failure
from .recovery import recovery_path


@dataclass
class Runtime:
    config: Config
    host: MPHost
    cloud: CD2
    stop: Event
    store: Store
    routes: tuple = ()


class RetryRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    switch_to_native: bool = False


class ViewRequest(BaseModel):
    key: str = Field(default="", max_length=120)
    page: int = Field(default=0, ge=0, le=100000)
    events: int = Field(default=0, ge=0, le=100000)
    files: int = Field(default=0, ge=0, le=100000)


class ImportRequest(BaseModel):
    history_id: int = Field(gt=0)


class RecoveryRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    path: str = Field(default="", max_length=4096)
    page: int = Field(default=0, ge=0, le=100000)
    verify: bool = False


class SymediaBatchBridge(_PluginBase):
    plugin_name = "115秒传助手"
    plugin_desc = "多目录秒传视频与字幕，按小时自动重试，齐套后通过 CD2 整目录交给 Symedia。"
    plugin_icon = "https://raw.githubusercontent.com/eitelkeit0708/MoviePilot-Plugins/main/icons/115InstantUpload.png"
    plugin_version = "1.4.1"
    plugin_author = "eitelkeit0708"
    author_url = "https://github.com/eitelkeit0708/MoviePilot-Plugins"
    plugin_config_prefix = "symediabatchbridge_"
    plugin_order = 30
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._lifecycle = RLock()
        self._runtime = None
        self._store = None
        self._listeners = []
        self._message = "未启用"
        self._action_message = ""
        self._notify = True
        self._notifying = Lock()
        self._view = ViewRequest()
        self._heartbeat = 0
        self._delete_local = False
        self._recovery_browser = None

    def init_plugin(self, config: dict = None):
        with self._lifecycle:
            self.stop_service()
            self._message = "未启用"
            self._action_message = ""
            self._recovery_browser = None
            self._notify = bool((config or {}).get("notify", True))
            self._delete_local = bool((config or {}).get("delete_local", False))
            try:
                self._store = Store(Path(self.get_data_path()), event_sink=self._on_activity)
            except Exception:
                self._message = "无法读取批次记录，请检查插件数据目录权限"
                logger.error(f"{self.plugin_name}：{self._message}")
                return
            if not config or not config.get("enabled"):
                logger.info(f"{self.plugin_name}：未启用，保留已有处理记录")
                return
            try:
                routes = Config.routes(config)
                parsed = routes[0]
                host = MPHost(parsed)
                stop = Event()
                cloud = CD2(parsed, stop)
                runtime = Runtime(parsed, host, cloud, stop, self._store, routes)
                self._runtime = runtime
                if config.get("scan_existing_once"):
                    # Persist intent before resetting the one-shot form flag. A restart
                    # resumes the request; replay observes the same source-key batches.
                    self._store.set_meta("inventory_pending", True)
                    self._store.set_meta("inventory_status", {"state": "queued", "at": time.time(),
                                         "message": "已接收存量处理请求，等待下一轮检查"})
                    self.update_config({**config, "scan_existing_once": False})
                    logger.info(f"{self.plugin_name}：已接收存量处理请求，已排队，每 {parsed.interval} 分钟检查；开关已复位")
                elif self._store.meta("inventory_pending", False):
                    self._store.set_meta("inventory_status", {"state": "queued", "at": time.time(),
                                         "message": "存量处理请求已恢复，等待继续检查"})
                if not self._store.meta("activated_at"):
                    self._store.set_meta("activated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                for name in ("TransferComplete", "SubtitleTransferComplete", "AudioTransferComplete"):
                    event_type = getattr(EventType, name, None)
                    if event_type is not None:
                        eventmanager.add_event_listener(event_type, self.on_transfer)
                        self._listeners.append(event_type)
                self._message = "运行中"
                logger.info(f"{self.plugin_name}：已启用 {len(routes)} 条路线，每 {parsed.interval} 分钟检查；通知{'开启' if self._notify else '关闭'}")
                for route in routes:
                    logger.info(f"{self.plugin_name}：{route.name} | {route.local_root} → {route.staging} → {route.inbox}")
            except ValueError as error:
                self.stop_service()
                self._message = str(error)
                logger.error(f"{self.plugin_name}：配置无效：{self._message}")
            except Exception:
                self.stop_service()
                self._message = "初始化失败，请检查插件依赖与 MP V2 版本"
                logger.error(f"{self.plugin_name}：{self._message}")

    def get_state(self) -> bool:
        return self._runtime is not None and not self._runtime.stop.is_set()

    def stop_service(self):
        with self._lifecycle:
            runtime, self._runtime = self._runtime, None
            if runtime:
                runtime.stop.set()
                runtime.cloud.close()
                logger.info(f"{self.plugin_name}：已停止调度，已记录的批次和上传进度保留")
            for event_type in self._listeners:
                eventmanager.remove_event_listener(event_type, self.on_transfer)
            self._listeners = []
            self._message = "已停止"
            # Uploads owned by the storage provider cannot be forcibly cancelled here.
            # Their worker retains the OS lock until it exits and cannot start a move.

    def get_service(self):
        runtime = self._runtime
        if not runtime:
            return []
        return [{"id": f"{self.__class__.__name__}.check_batches",
                 "name": "115 秒传与批次检查", "trigger": IntervalTrigger(minutes=runtime.config.interval),
                 "func": self.check_batches, "kwargs": {}}]

    @staticmethod
    def get_command():
        return []

    def get_api(self):
        from fastapi import Depends, HTTPException
        from app.db.user_oper import get_current_active_user

        def administrator(user=Depends(get_current_active_user)):
            # MP's bearer registration verifies a token, not the account's role.
            # Resolve the current active database user, including cloned plugins.
            if not user.is_superuser:
                raise HTTPException(status_code=403, detail="需要管理员权限")

        def retry(request: RetryRequest, _admin=Depends(administrator)):
            return self.retry_batch(request)

        def view(request: ViewRequest, _admin=Depends(administrator)):
            return self.view_records(request)

        def scan(_admin=Depends(administrator)):
            return self.scan_existing()

        def import_batch(request: ImportRequest, _admin=Depends(administrator)):
            return self.import_existing(request)

        def recovery(request: RecoveryRequest, _admin=Depends(administrator)):
            return self.recover_batch(request)

        return [{"path": "/retry", "endpoint": retry,
                 "methods": ["POST"], "summary": "重新检查批次", "auth": "bear"},
                {"path": "/view", "endpoint": view,
                 "methods": ["POST"], "summary": "查看处理记录", "auth": "bear"},
                {"path": "/scan", "endpoint": scan,
                 "methods": ["POST"], "summary": "检查现存文件", "auth": "bear"},
                {"path": "/import", "endpoint": import_batch,
                 "methods": ["POST"], "summary": "接管已有整理批次", "auth": "bear"},
                {"path": "/recovery", "endpoint": recovery,
                 "methods": ["POST"], "summary": "核对批次归档目录", "auth": "bear"}]

    def view_records(self, request: ViewRequest) -> Response:
        self._recovery_browser = None
        self._view = request
        return Response(success=True)

    def recover_batch(self, request: RecoveryRequest) -> Response:
        runtime = self._runtime
        if not runtime or runtime.stop.is_set():
            return Response(success=False, message="请先启用插件")
        try:
            with runtime.store.worker_lock() as acquired:
                if not acquired:
                    raise BridgeError("正在处理批次，请稍后再试")
                job = runtime.store.get(request.key)
                if not job or not job.get("move_requested") or job["state"] == "handed_off":
                    raise BridgeError("此批次无需核对归档目录")
                config = runtime.config.pinned(job["routing"])
                path = recovery_path(request.path or config.cd2_prefix, config,
                                     [r.routing() for r in runtime.routes] + runtime.store.pending_routings(),
                                     browse=not request.verify)
                self._view = ViewRequest(key=job["id"])
                if request.verify:
                    job.update(recovery_directory=path, next_check=0,
                               message="已安排核对归档目录中的视频与附件")
                    runtime.store.save(job)
                    runtime.store.record(job, event("archive_requested", "管理员指定归档核对目录：" + path))
                    self._recovery_browser = None
                    self._action_message = "已安排核对，全部文件的 SHA1 和大小一致后才恢复完成状态。"
                else:
                    # Read-only navigation; native V2 PageRender cannot submit
                    # editable field values. Each directory button has fixed params.
                    directories = runtime.cloud.directories(path)
                    self._recovery_browser = dict(key=job["id"], path=path, root=config.cd2_prefix,
                                                  directories=directories, page=request.page)
                    self._action_message = ""
                return Response(success=True)
        except (BridgeError, ValueError) as error:
            self._action_message = str(error)
        except Exception:
            self._action_message = "归档目录读取失败，请检查 CD2 连接后重试"
        return Response(success=False, message=self._action_message)

    def scan_existing(self) -> Response:
        runtime = self._runtime
        if not runtime or runtime.stop.is_set():
            self._action_message = "请先启用插件"
            return Response(success=False)
        try:
            result = scan_inventory(runtime)
            runtime.store.set_meta("existing_scan", result)
            count = len(result["candidates"])
            self._action_message = f"存量检查完成：发现 {count} 个未接管批次。选择接管后才开始上传。"
            logger.info(f"{self.plugin_name}：检查现存文件，发现 {count} 个存量批次，未发起上传")
            self._view = ViewRequest()
            return Response(success=True)
        except Exception:
            self._action_message = "存量检查未完成，请检查整理记录和本地目录后重试"
            logger.warning(f"{self.plugin_name}：存量检查失败，未接管文件")
            return Response(success=False)

    def import_existing(self, request: ImportRequest) -> Response:
        runtime = self._runtime
        if not runtime or runtime.stop.is_set():
            return Response(success=False, message="请先启用插件")
        with runtime.store.worker_lock() as acquired:
            if not acquired:
                self._action_message = "正在处理批次，请稍后再接管"
                return Response(success=False)
            scan = runtime.store.meta("existing_scan", {})
            candidate = next((r for r in scan.get("candidates", []) if r["history_id"] == request.history_id), None)
            if not candidate or not candidate.get("members"):
                self._action_message = "请先检查存量并选择批次"
                return Response(success=False)
            row = runtime.host.transfers.get(request.history_id)
            if not row or not value(row,"status") or not Path(str(value(row,"dest") or "")).is_file():
                self._action_message = "整理记录或文件已变化，请重新检查存量"
                return Response(success=False)
            job = self._observe(runtime, row, inventory_files=candidate["members"])
            if not job:
                self._action_message = "文件已不属于当前路线，请重新检查存量"
                return Response(success=False)
            runtime.store.record(job, event("imported", "已接管现存整理文件，核对本地副本后上传"))
            scan["candidates"] = [r for r in scan["candidates"] if r["history_id"] != request.history_id]
            runtime.store.set_meta("existing_scan", scan)
            self._view = ViewRequest(key=job["id"])
            self._action_message = "已接管，将在下一轮核对并上传"
            return Response(success=True)

    def _on_activity(self, job, item):
        log = logger.warning if item["level"] == "warning" else logger.info
        line = f"{self.plugin_name} [{job['id']}] {job.get('route_name', '默认路线')} · {job['title']} | {item['message']}"
        if item.get("file"):
            line += " | " + item["file"]
        log(line.replace("\n", " ").replace("\r", " "))
        self._flush_notifications()

    def _flush_notifications(self):
        runtime = self._runtime
        if not self._notify or not runtime or runtime.stop.is_set() or not self._notifying.acquire(blocking=False):
            return
        try:
            for notice in runtime.store.pending_notices():
                if runtime.stop.is_set():
                    break
                try:
                    # Native MP persists message history and routes Plugin messages
                    # to the user's configured channels. It gives no delivery receipt.
                    self.post_message(mtype=NotificationType.Plugin, title=notice["title"], text=notice["text"])
                except Exception:
                    runtime.store.notice_result(notice, False)
                    logger.warning(f"{self.plugin_name} [{notice['batch']}] 通知提交失败，将自动重试")
                else:
                    runtime.store.notice_result(notice, True)
                    logger.info(f"{self.plugin_name} [{notice['batch']}] {notice['title']}：已提交 MP 通知队列")
        finally:
            self._notifying.release()

    def _observe(self, runtime, row, inventory_files=None):
        if runtime.stop.is_set():
            return
        route = next((route for route in runtime.routes
                      if runtime.host.for_config(route).in_scope(row)), None)
        if route is None:
            return
        job = runtime.store.observe(
            instance=self.__class__.__name__,
            download_hash=str(value(row, "download_hash") or ""),
            downloader=str(value(row, "downloader") or ""),
            title=str(value(row, "title") or Path(str(value(row, "dest") or "")).stem),
            history_id=int(value(row, "id")), routing=route.routing(), route_name=route.name, cleanup_local=self._delete_local,
            inventory_files=inventory_files)
        self._notify_issue(runtime, job)
        return job

    def on_transfer(self, event):
        runtime = self._runtime
        if not runtime or runtime.stop.is_set():
            return
        try:
            identifier = (event.event_data or {}).get("transfer_history_id")
            if identifier:
                # Serialize observations with worker saves. A busy callback is
                # recovered by the history cursor, without blocking MP's event bus.
                with runtime.store.worker_lock() as acquired:
                    if acquired:
                        row = runtime.host.transfers.get(int(identifier))
                        if row is not None:
                            self._observe(runtime, row)
        except Exception:
            # A cursor-based history scan recovers missed callbacks without blocking MP.
            logger.warning(f"{self.plugin_name}：本次事件未入队，将在批次检查时补读整理记录")

    def _recover_history(self, runtime):
        activated = runtime.store.meta("activated_at")
        cursor = runtime.store.meta("history_cursor", activated)
        # Native list_by_date uses a strict > comparison with second precision.
        since = (datetime.strptime(cursor, "%Y-%m-%d %H:%M:%S") - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        scan_started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        read, matched = 0, 0
        before = sum(runtime.store.counts().values())
        for row in runtime.host.histories_since(since):
            read += 1
            if runtime.stop.is_set():
                raise Stopped()
            if str(value(row, "date") or "") >= activated:
                if self._observe(runtime, row):
                    matched += 1
        runtime.store.set_meta("history_cursor", scan_started)
        runtime.store.set_meta("last_scan", {"read": read, "matched": matched, "new": sum(runtime.store.counts().values()) - before})

    def check_batches(self):
        runtime = self._runtime
        if not runtime or runtime.stop.is_set():
            return
        with runtime.store.worker_lock() as acquired:
            if not acquired or runtime.stop.is_set():
                return
            try:
                self._flush_notifications()
                if runtime.store.meta("inventory_pending", False):
                    try:
                        runtime.store.set_meta("inventory_status", {"state": "running", "at": time.time(),
                                               "message": "正在检查现存整理文件"})
                        logger.info(f"{self.plugin_name}：开始检查 {len(runtime.routes)} 条路线的现存整理文件")
                        result = scan_inventory(runtime)
                        runtime.store.set_meta("existing_scan", result)
                        imported_count, skipped = 0, 0
                        for candidate in result["candidates"]:
                            check_row = runtime.host.transfers.get(candidate["history_id"])
                            imported = None
                            if check_row and value(check_row, "status") and Path(str(value(check_row, "dest") or "")).is_file():
                                imported = self._observe(runtime, check_row, inventory_files=candidate["members"])
                                if imported:
                                    imported_count += 1
                                    runtime.store.record(imported, event("imported", "一次性存量处理：已接管现存整理文件，核对本地副本后上传"))
                            if not imported:
                                skipped += 1
                        result["imported"], result["skipped"] = imported_count, skipped
                        result["candidates"] = []
                        runtime.store.set_meta("existing_scan", result)
                        message = f"存量检查完成：接管 {imported_count} 批，跳过 {skipped} 批，{len(result['unmatched'])} 个文件无可用记录"
                        runtime.store.set_meta("inventory_status", {"state": "done", "at": time.time(), "message": message})
                        runtime.store.set_meta("inventory_pending", False)
                        logger.info(f"{self.plugin_name}：{message}")
                    except Stopped:
                        raise
                    except Exception:
                        runtime.store.set_meta("inventory_status", {"state": "retrying", "at": time.time(),
                                               "message": "存量检查未完成，请求已保留，下轮自动重试"})
                        logger.warning(f"{self.plugin_name}：一次性存量检查未完成，保留请求，下次继续")
                history_ok = True
                try:
                    self._recover_history(runtime)
                except Stopped:
                    raise
                except Exception:
                    history_ok = False
                # Finite work per scheduler tick; old waiting tasks rotate behind others.
                routings = runtime.store.pending_routings()
                for job in runtime.store.due():
                    if runtime.stop.is_set():
                        break
                    engine = Engine(runtime.store, runtime.config, runtime.host, runtime.cloud, runtime.stop)
                    try:
                        runtime.store.restore_origin(job)
                        config = runtime.config.pinned(job["routing"])
                        for other in [r.routing() for r in runtime.routes] + routings:
                            if other.get("cd2_address") == config.cd2_address and other.get("cd2_prefix") == config.cd2_prefix:
                                if (nested(config.cd2_staging, other["inbox"])
                                        or nested(config.inbox, other["cd2_prefix"].rstrip("/") + other["staging"])):
                                    raise BridgeError("新旧路线的暂存与待归档目录重叠，等待修正配置", review=True)
                        engine = Engine(runtime.store, config, runtime.host.for_config(config), runtime.cloud, runtime.stop,
                                        protected_routings=[r.routing() for r in runtime.routes])
                    except (BridgeError, ValueError) as error:
                        engine._failure(job, str(error), True)
                        self._notify_issue(runtime, job)
                        continue
                    engine.process(job)
                    self._notify_issue(runtime, job)
                for job in runtime.store.cleanup_jobs():
                    if runtime.stop.is_set():
                        break
                    if not self._delete_local:
                        break
                    try:
                        cleanup(runtime.store, job, runtime.config.pinned(job["routing"]), runtime.stop)
                    except Stopped:
                        raise
                    except (BridgeError, ValueError) as error:
                        cleanup_failure(runtime.store, job, str(error))
                    except Exception:
                        cleanup_failure(runtime.store, job, "本批次清理暂时失败，保留本地副本，下次自动重试")
                if not runtime.stop.is_set():
                    self._message = "运行中" if history_ok else "整理记录暂不可读，已入队批次继续处理，下次自动补读"
                    runtime.store.set_meta("last_check", time.time())
                    runtime.store.set_meta("last_check_status", self._message)
                    if time.time() - self._heartbeat >= 3600 or not history_ok:
                        counts = runtime.store.counts()
                        logger.info(f"{self.plugin_name}：本轮检查完成，累计 {sum(counts.values())} 批，已移交 {counts.get('handed_off', 0)} 批；{self._message}")
                        self._heartbeat = time.time()
                    self._flush_notifications()
            except Stopped:
                pass
            except Exception:
                if not runtime.stop.is_set():
                    self._message = "本轮批次检查未完成，下次检查会重试"
                    logger.warning(f"{self.plugin_name}：{self._message}")

    def _notify_issue(self, runtime, job):
        if not self._notify or runtime.stop.is_set():
            return
        if not attention(job) or time.time() - job.get("notified_at", 0) < 86400:
            return
        job["notified_at"] = time.time()
        runtime.store.save(job)
        runtime.store.record(job, event("attention", job["message"], level="warning", notice="处理异常",
                                        scope="issue", next_at=job.get("next_check", 0)))

    def retry_batch(self, request: RetryRequest) -> Response:
        def respond(success, message):
            # Native V2 PageRender refreshes the page after POST; it does not display
            # Response.message. Keep the action result visible in that refreshed page.
            self._action_message = message
            return Response(success=success, message=message)

        runtime = self._runtime
        if not runtime or runtime.stop.is_set():
            return respond(False, "请先启用插件并完成配置")
        with runtime.store.worker_lock() as acquired:
            if not acquired:
                return respond(False, "正在处理批次，请稍后再试")
            job = runtime.store.get(request.key)
            if not job:
                return respond(False, "批次不存在")
            if job["state"] == "handed_off":
                return respond(False, "该批次已移交，不会重复发送")
            if job["routing"].get("storage") == "115网盘Plus":
                if not request.switch_to_native:
                    return respond(False, "请先确认 MP 内置 115 与 CD2 使用同一账号，并切换此旧批次")
                route = next((r for r in runtime.routes
                              if {**job["routing"], "storage": "u115"} == r.routing()), None)
                if route is None:
                    return respond(False, "目录映射也发生了变化，请先恢复本批次原目录配置")
                job["routing"] = route.routing()
            job.update(state="waiting", next_check=0, attempts=0, message="已安排重新检查")
            # Keep the sealed manifest and move intent. Retry never means start again.
            runtime.store.save(job)
        return respond(True, "已安排检查，将在下一次批次检查时执行")

    def get_form(self):
        def field(model, label, placeholder="", **props):
            return {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                {"component": "VTextField", "props": {"model": model, "label": label,
                 "placeholder": placeholder, **props}}]}

        defaults = {"enabled": False, "notify": True, "storage": "u115", "interval": 1,
                    "route_count": 1, "cd2_prefix": "", "cd2_address": "", "cd2_token": "",
                    "delete_local": False, "scan_existing_once": False}
        route_cards = []
        fields = ("route_name", "local_root", "staging", "inbox")
        for index in range(1, 17):
            prefix = "" if index == 1 else f"route_{index}_"
            initial = {"route_name": "默认路线" if index == 1 else f"路线 {index}",
                       "local_root": "", "staging": "/MP暂存" if index == 1 else f"/MP暂存/路线{index}", "inbox": ""}
            defaults.update({prefix + key: val for key, val in initial.items()})
            # FormRender models are flat keys; its onClick expressions support
            # normal JS. Predeclared cards use v-show, no custom frontend bundle.
            remove = ("() => { for (let i=" + str(index) + ";i<Number(model.route_count);i++) {"
                      "let to=i===1?'':'route_'+i+'_';let from='route_'+(i+1)+'_';"
                      "for (const key of " + json.dumps(fields) + ") model[to+key]=model[from+key];}"
                      "let last=Number(model.route_count);let p=last===1?'':'route_'+last+'_';"
                      "model[p+'route_name']='路线 '+last;model[p+'local_root']='';"
                      "model[p+'staging']='/MP暂存/路线'+last;model[p+'inbox']='';"
                      "model.route_count=Math.max(1,last-1); }")
            route_cards.append({"component": "VCard", "props": {"variant": "outlined", "class": "pa-4 mb-4",
                                "show": "{{ Number(model.route_count || 1) >= " + str(index) + " }}"}, "content": [
                {"component": "VRow", "content": [
                    field(prefix + "route_name", "路线名称", "入库 / 追更 / 涂佩"),
                    {"component": "VCol", "props": {"cols": 12, "md": 6, "class": "d-flex justify-end align-center"},
                     "content": [{"component": "VBtn", "text": "移除路线", "props": {"variant": "text", "size": "small",
                                  "disabled": "{{ Number(model.route_count || 1) <= 1 }}", "onClick": remove}}]},
                    field(prefix + "local_root", "MP 本地整理目录", "/media/organized/入库"),
                    field(prefix + "staging", "115 暂存目录", "/MP暂存/入库"),
                    {"component": "VCol", "props": {"cols": 12}, "content": [
                        {"component": "VTextField", "props": {"model": prefix + "inbox",
                         "label": "Symedia 待归档目录（CD2 路径）", "placeholder": "/115/Symedia待归档/入库"}}]},
                ]}]})
        return [{"component": "VForm", "content": [
            {"component": "VAlert", "props": {"type": "info", "variant": "tonal"},
             "text": "使用 MP 内置 115 账号上传，与 CD2 挂载保持同一账号。暂存目录不要加入 Symedia 监控。"},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [
                    {"component": "VSwitch", "props": {"model": "enabled", "label": "启用批次移交"}}]},
                field("cd2_prefix", "CD2 中的 115 根目录", "/115 或 /", hint="令牌根目录为 115 时填 /，下方待归档路径也省略 /115", persistentHint=True),
                field("cd2_address", "CD2 gRPC 地址", "http://cd2:19798"),
                field("cd2_token", "CD2 API 令牌", type="password", autocomplete="off"),
                field("interval", "检查间隔（分钟）", type="number", min=1, max=60),
                {"component": "VCol", "props": {"cols": 12}, "content": [
                    {"component": "VSwitch", "props": {"model": "notify", "label": "MP 通知：移交成功、处理异常、转普通上传"}}]},
                {"component": "VCol", "props": {"cols": 12}, "content": [
                    {"component": "VSwitch", "props": {"model": "delete_local", "label": "移交后删除本地整理副本",
                     "hint": "仅删除已移交批次中有原始下载文件的硬链接副本；保留下载器文件和做种任务。只应用于之后接管的批次。", "persistentHint": True}}]},
                {"component": "VCol", "props": {"cols": 12}, "content": [
                    {"component": "VSwitch", "props": {"model": "scan_existing_once", "label": "保存后处理一次现存文件",
                     "hint": "接管整理目录中有 MP 记录的现存文件；原下载源或做种任务已删除也可处理。保存后排队，开关自动复位，结果显示在数据页顶部。", "persistentHint": True}}]},
            ]},
            {"component": "h3", "props": {"class": "mb-3"}, "text": "目录映射"},
            *route_cards,
            {"component": "VBtn", "text": "添加路线", "props": {"variant": "outlined", "class": "mb-4",
             "prepend-icon": "mdi-plus", "disabled": "{{ Number(model.route_count || 1) >= 16 }}",
             "onClick": "() => { model.route_count = Math.min(16, Number(model.route_count || 1) + 1); }"}},
            {"component": "p", "text": "每个本地目录对应一条路线。修改或移除路线仅影响新批次，已有批次沿原路线完成。"},
            {"component": "VAlert", "props": {"type": "info", "variant": "tonal"},
             "text": "每个文件先尝试秒传 24 次，间隔 1 小时；最后一次未命中后再等 1 小时，才普通上传。接口报错不计次数；重启继续等待。"},
            {"component": "VAlert", "props": {"type": "warning", "variant": "tonal"},
             "text": "请保留下载器任务，使用 MP 复制或硬链接整理。相同目录不要再交给其他上传监控；首次启用仅接收之后的整理记录。"},
        ]}], defaults

    def get_page(self):
        try:
            return render_page(self)
        except Exception:
            logger.warning(f"{self.plugin_name}：读取处理记录失败，可刷新重试")
            return [{"component": "VAlert", "props": {"type": "warning", "variant": "tonal"},
                     "text": "暂时无法读取处理记录，请刷新重试。"}]
