"""MoviePilot V2: upload a complete native transfer batch, then hand it to Symedia."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, RLock
import time

from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, Field

from app.core.event import eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import Response
from app.schemas.types import EventType

from .cd2 import CD2
from .domain import Config, Stopped
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


class RetryRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    switch_to_native: bool = False


class SymediaBatchBridge(_PluginBase):
    plugin_name = "Symedia 批次移交"
    plugin_desc = "将 MP 整理的视频与字幕整批上传到 115，再通过 CD2 整目录交给 Symedia。"
    plugin_icon = "https://raw.githubusercontent.com/eitelkeit0708/MoviePilot-Plugins/main/icons/upload.png"
    plugin_version = "1.1.0"
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

    def init_plugin(self, config: dict = None):
        with self._lifecycle:
            self.stop_service()
            self._message = "未启用"
            self._action_message = ""
            try:
                self._store = Store(Path(self.get_data_path()))
            except Exception:
                self._message = "无法读取批次记录，请检查插件数据目录权限"
                logger.error(f"{self.plugin_name}：{self._message}")
                return
            if not config or not config.get("enabled"):
                return
            try:
                parsed = Config.parse(config)
                host = MPHost(parsed)
                stop = Event()
                cloud = CD2(parsed, stop)
                runtime = Runtime(parsed, host, cloud, stop, self._store)
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
                 "name": "Symedia 批次检查", "trigger": IntervalTrigger(minutes=runtime.config.interval),
                 "func": self.check_batches, "kwargs": {}}]

    @staticmethod
    def get_command():
        return []

    def get_api(self):
        return [{"path": "/retry", "endpoint": self.retry_batch,
                 "methods": ["POST"], "summary": "重新检查批次", "auth": "bear"}]

    def _observe(self, runtime, row):
        if runtime.stop.is_set() or not runtime.host.in_scope(row):
            return
        runtime.store.observe(
            instance=self.__class__.__name__,
            download_hash=str(value(row, "download_hash") or ""),
            downloader=str(value(row, "downloader") or ""),
            title=str(value(row, "title") or Path(str(value(row, "dest") or "")).stem),
            history_id=int(value(row, "id")), routing=runtime.config.routing())

    def on_transfer(self, event):
        runtime = self._runtime
        if not runtime or runtime.stop.is_set():
            return
        try:
            identifier = (event.event_data or {}).get("transfer_history_id")
            if identifier:
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
                self._recover_history(runtime)
                engine = Engine(runtime.store, runtime.config, runtime.host, runtime.cloud, runtime.stop)
                # Finite work per scheduler tick; old waiting tasks rotate behind others.
                due = [job for job in runtime.store.jobs()
                       if job["state"] not in ("handed_off", "review")
                       and job.get("next_check", 0) <= time.time()]
                for job in due[:3]:
                    if runtime.stop.is_set():
                        break
                    engine.process(job)
                if not runtime.stop.is_set():
                    self._message = "运行中"
            except Stopped:
                pass
            except Exception:
                if not runtime.stop.is_set():
                    self._message = "暂时无法读取整理记录，下次检查会重试"
                    logger.warning(f"{self.plugin_name}：{self._message}")

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
                if {**job["routing"], "storage": "u115"} != runtime.config.routing():
                    return respond(False, "目录映射也发生了变化，请先恢复本批次原目录配置")
                job["routing"] = runtime.config.routing()
            job.update(state="waiting", next_check=0, attempts=0, message="已安排重新检查")
            # Keep the sealed manifest and move intent. Retry never means start again.
            runtime.store.save(job)
        return respond(True, "已安排检查，将在下一次批次检查时执行")

    def get_form(self):
        def field(model, label, placeholder="", **props):
            return {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                {"component": "VTextField", "props": {"model": model, "label": label,
                 "placeholder": placeholder, **props}}]}

        return [{"component": "VForm", "content": [
            {"component": "VAlert", "props": {"type": "info", "variant": "tonal"},
             "text": "使用 MP 内置 115 账号上传，与 CD2 挂载保持同一账号。暂存目录不要加入 Symedia 监控。"},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [
                    {"component": "VSwitch", "props": {"model": "enabled", "label": "启用批次移交"}}]},
                field("local_root", "MP 本地整理目录", "/media/organized"),
                field("staging", "115 暂存目录", "/MP暂存"),
                field("cd2_prefix", "CD2 中的 115 挂载目录", "/115"),
                field("inbox", "Symedia 待归档目录（CD2 路径）", "/115/Symedia待归档"),
                field("cd2_address", "CD2 gRPC 地址", "http://cd2:19798"),
                field("cd2_token", "CD2 API 令牌", type="password", autocomplete="off"),
                field("interval", "检查间隔（分钟）", type="number", min=1, max=60),
            ]},
            {"component": "VAlert", "props": {"type": "info", "variant": "tonal"},
             "text": "每个文件先尝试秒传 24 次，间隔 1 小时；最后一次未命中后再等 1 小时，才普通上传。接口报错不计次数；重启继续等待。"},
            {"component": "VAlert", "props": {"type": "warning", "variant": "tonal"},
             "text": "请保留下载器任务，使用 MP 复制或硬链接整理。相同目录不要再交给其他上传监控；首次启用仅接收之后的整理记录。"},
        ]}], {"enabled": False, "storage": "u115", "interval": 1,
               "local_root": "", "staging": "/MP暂存", "cd2_prefix": "",
               "inbox": "", "cd2_address": "", "cd2_token": ""}

    def get_page(self):
        def text_node(text, component="div", **props):
            return {"component": component, "props": props, "text": text}

        states = {"waiting": "等待齐套", "waiting_instant": "等待秒传", "uploading": "上传中", "verifying": "核对中",
                  "moving": "核对移交", "review": "需要处理", "handed_off": "已移交"}
        content = [text_node(self.plugin_name, "h3"), text_node(self._message, "p")]
        if self._action_message:
            content.append(text_node(self._action_message, "VAlert", type="info", variant="tonal", **{"class": "mb-3"}))
        try:
            jobs = self._store.jobs() if self._store else []
        except Exception:
            return content + [text_node("暂时无法读取批次记录", "p")]
        if not jobs:
            content.append(text_node("暂无批次。新的 MP 整理任务会自动出现在这里。", "p"))
        for job in sorted(jobs, key=lambda j: j["updated"], reverse=True)[:50]:
            body = [text_node(job["title"], "VCardTitle"),
                    text_node("已移交 · 后续变更" if job.get("late_history_ids") else states.get(job["state"], job["state"]), "VCardSubtitle"),
                    text_node(job["message"], "p"), text_node(job["id"], "small")]
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
