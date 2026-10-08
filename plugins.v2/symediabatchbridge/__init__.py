"""MoviePilot V2: upload a complete native transfer batch, then hand it to Symedia."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, RLock
import json
import time

from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, Field

from app.core.event import eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import Response
from app.schemas.types import EventType

from .cd2 import CD2
from .domain import BridgeError, Config, Stopped, nested
from .engine import Engine
from .host import MPHost, value
from .instant import MAX_INSTANT_ATTEMPTS
from .store import Store


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


class SymediaBatchBridge(_PluginBase):
    plugin_name = "115秒传助手"
    plugin_desc = "多目录秒传视频与字幕，按小时自动重试，齐套后通过 CD2 整目录交给 Symedia。"
    plugin_icon = "https://raw.githubusercontent.com/eitelkeit0708/MoviePilot-Plugins/main/icons/115InstantUpload.png"
    plugin_version = "1.2.0"
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

    def init_plugin(self, config: dict = None):
        with self._lifecycle:
            self.stop_service()
            self._message = "未启用"
            self._action_message = ""
            self._notify = bool((config or {}).get("notify", True))
            try:
                self._store = Store(Path(self.get_data_path()))
            except Exception:
                self._message = "无法读取批次记录，请检查插件数据目录权限"
                logger.error(f"{self.plugin_name}：{self._message}")
                return
            if not config or not config.get("enabled"):
                return
            try:
                routes = Config.routes(config)
                parsed = routes[0]
                host = MPHost(parsed)
                stop = Event()
                cloud = CD2(parsed, stop)
                runtime = Runtime(parsed, host, cloud, stop, self._store, routes)
                self._runtime = runtime
                if not self._store.meta("activated_at"):
                    self._store.set_meta("activated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                for name in ("TransferComplete", "SubtitleTransferComplete", "AudioTransferComplete"):
                    event_type = getattr(EventType, name, None)
                    if event_type is not None:
                        eventmanager.add_event_listener(event_type, self.on_transfer)
                        self._listeners.append(event_type)
                self._message = "运行中"
            except ValueError as error:
                self.stop_service()
                self._message = str(error)
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
        return [{"path": "/retry", "endpoint": self.retry_batch,
                 "methods": ["POST"], "summary": "重新检查批次", "auth": "bear"}]

    def _observe(self, runtime, row):
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
            history_id=int(value(row, "id")), routing=route.routing(), route_name=route.name)
        self._notify_issue(runtime, job)

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
        for row in runtime.host.histories_since(since):
            if runtime.stop.is_set():
                raise Stopped()
            if str(value(row, "date") or "") >= activated:
                self._observe(runtime, row)
        runtime.store.set_meta("history_cursor", scan_started)

    def check_batches(self):
        runtime = self._runtime
        if not runtime or runtime.stop.is_set():
            return
        with runtime.store.worker_lock() as acquired:
            if not acquired or runtime.stop.is_set():
                return
            try:
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
                        config = runtime.config.pinned(job["routing"])
                        for other in [r.routing() for r in runtime.routes] + routings:
                            if other.get("cd2_address") == config.cd2_address and other.get("cd2_prefix") == config.cd2_prefix:
                                if (nested(config.cd2_staging, other["inbox"])
                                        or nested(config.inbox, other["cd2_prefix"].rstrip("/") + other["staging"])):
                                    raise BridgeError("新旧路线的暂存与待归档目录重叠，等待修正配置", review=True)
                        engine = Engine(runtime.store, config, runtime.host.for_config(config), runtime.cloud, runtime.stop)
                    except (BridgeError, ValueError) as error:
                        engine._failure(job, str(error), True)
                        self._notify_issue(runtime, job)
                        continue
                    engine.process(job)
                    self._notify_issue(runtime, job)
                if not runtime.stop.is_set():
                    self._message = "运行中" if history_ok else "整理记录暂不可读，已入队批次继续处理，下次自动补读"
            except Stopped:
                pass
            except Exception:
                if not runtime.stop.is_set():
                    self._message = "暂时无法读取整理记录，下次检查会重试"
                    logger.warning(f"{self.plugin_name}：{self._message}")

    def _notify_issue(self, runtime, job):
        if not self._notify or runtime.stop.is_set():
            return
        attention = (job["state"] == "review" or job.get("attempts", 0) >= 10
                     or bool(job.get("late_history_ids"))
                     or (job["state"] == "waiting" and time.time() - job["created"] >= 86400)
                     or any(e.get("instant_error_since") and time.time() - e["instant_error_since"] >= 86400
                            for e in job.get("files", [])))
        if not attention or time.time() - job.get("notified_at", 0) < 86400:
            return
        try:
            if job["state"] == "handed_off":
                followup = "已移交批次不会自动重发；请在插件详情查看后续变更。"
            else:
                next_check = datetime.fromtimestamp(job.get("next_check") or time.time()).strftime("%m-%d %H:%M")
                followup = f"下次核对：{next_check}。也可在插件详情点击重新检查。"
            self.post_message(title=f"115秒传助手 · {job.get('route_name', '默认路线')}",
                              text=f"{job['title']}\n{job['message']}\n{followup}")
            job["notified_at"] = time.time()
            runtime.store.save(job)
        except Exception:
            logger.warning(f"{self.plugin_name}：异常通知暂未发送，不影响批次恢复")

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
                    "route_count": 1, "cd2_prefix": "", "cd2_address": "", "cd2_token": ""}
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
                    {"component": "VSwitch", "props": {"model": "notify", "label": "异常通知"}}]},
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
        def text_node(text, component="div", **props):
            return {"component": component, "props": props, "text": text}

        states = {"waiting": "等待齐套", "waiting_instant": "等待秒传", "uploading": "上传中", "verifying": "核对中",
                  "moving": "核对移交", "review": "异常待确认", "retrying": "等待恢复", "handed_off": "已移交"}
        content = [text_node(self.plugin_name, "h3"), text_node(self._message, "p")]
        if self._action_message:
            content.append(text_node(self._action_message, "VAlert", type="info", variant="tonal", **{"class": "mb-3"}))
        try:
            jobs = self._store.jobs(limit=51) if self._store else []
        except Exception:
            return content + [text_node("暂时无法读取批次记录", "p")]
        if not jobs:
            content.append(text_node("暂无批次。新的 MP 整理任务会自动出现在这里。", "p"))
        for job in sorted(jobs, key=lambda j: j["updated"], reverse=True)[:50]:
            body = [text_node(job["title"], "VCardTitle"),
                    text_node("已移交 · 后续变更" if job.get("late_history_ids") else states.get(job["state"], job["state"]), "VCardSubtitle"),
                    text_node(job["message"], "p"), text_node(job["id"], "small")]
            body.append(text_node(job.get("route_name", "默认路线") + " · " + job["routing"]["inbox"], "p"))
            if job.get("next_check"):
                body.append(text_node("下次检查 " + datetime.fromtimestamp(job["next_check"]).strftime("%m-%d %H:%M"), "p"))
            if job.get("destination"):
                body.append(text_node(job["destination"], "p"))
            for entry in job.get("files", []):
                if entry.get("uploaded") or not entry.get("instant_next_at"):
                    continue
                misses = entry.get("instant_misses", 0)
                next_time = datetime.fromtimestamp(entry["instant_next_at"]).strftime("%m-%d %H:%M")
                action = "普通上传" if misses >= MAX_INSTANT_ATTEMPTS else "再次秒传"
                status = f"秒传未命中 {misses}/{MAX_INSTANT_ATTEMPTS} · {next_time} {action}"
                if entry.get("instant_error"):
                    status += " · " + entry["instant_error"]
                body.extend([text_node(entry["relative"], "div", **{"class": "mt-2 text-body-2"}),
                             text_node(status, "div", **{"class": "text-caption"})])
            if job["state"] != "handed_off":
                legacy = job["routing"].get("storage") == "115网盘Plus"
                if legacy:
                    body.append(text_node("此旧批次使用 DDSRem 接口。确认 MP 内置 115 与 CD2 为同一账号后，点击下方切换；已上传文件会保留。", "p"))
                body.append({"component": "VBtn", "props": {"variant": "text", "size": "small"},
                             "text": "确认同一账号，改用 MP 内置 115" if legacy else "重新检查", "events": {"click": {
                                 "api": f"plugin/{self.__class__.__name__}/retry", "method": "post",
                                 "params": {"key": job["id"], "switch_to_native": legacy}}}})
            content.append({"component": "VCard", "props": {"variant": "outlined", "class": "mb-3 pa-3"},
                            "content": body})
        if len(jobs) > 50:
            content.append(text_node("显示最近 50 个批次，历史回执仍完整保留。", "small"))
        return content
