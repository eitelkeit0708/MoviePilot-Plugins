import datetime
import math
import re
import xml.dom.minidom
from copy import copy, deepcopy
from threading import Event, Lock
from urllib.parse import urlencode, urlsplit
from typing import Optional, Tuple, List, Dict, Any, TypedDict
import time
import random
import pytz
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from enum import Enum

from app.schemas import Response
from app.schemas.types import MediaType
from app.core.context import MediaInfo
from app.core.meta.metabase import MetaBase
from app.chain.media import MediaChain
from app.chain.subscribe import SubscribeChain
from app.core.config import settings
from app.core.metainfo import MetaInfo
from app.log import logger
from app.plugins import _PluginBase
from app.utils.dom import DomUtils
from app.utils.http import RequestUtils
from app.modules.douban.apiv2 import DoubanApi


class Status(Enum):
    UNRECOGNIZED = "未识别"
    UNCATEGORIZED = "已识别未分类"
    YEAR_NOT_MATCH = "年份不符合"
    RATING_NOT_MATCH = "评分不符合"
    MEDIA_EXISTS = "媒体库已存在"
    SUBSCRIPTION_EXISTS = "订阅已存在"
    SUBSCRIPTION_ADDED = "已添加订阅"
    SUBSCRIPTION_FAILED = "添加订阅失败"
    PARTIAL_SUCCESS = "部分订阅成功"
    PROCESS_FAILED = "处理失败"
    YEAR_UNKNOWN = "年份信息缺失"
    RATING_UNKNOWN = "评分信息缺失"
    SEASON_UNKNOWN = "季度信息缺失"
    IDENTITY_MISMATCH = "识别信息待核对"


class HistoryDataType(Enum):
    STATISTICS = "历史处理统计"
    RECOGNIZED = "已识别历史"
    UNRECOGNIZED = "未识别历史"
    ALL = "所有历史"
    LATEST = "最新12条历史"


class Icons(Enum):
    RECOGNIZED = "icon_recognized"
    STATISTICS = "icon_statistics"
    UNRECOGNIZED = "icon_unrecognized"
    RSS = "icon_rss"


class HistoryPayload(TypedDict):
    title: str
    type: str
    year: str
    poster: Optional[str]
    overview: str
    tmdbid: str
    doubanid: str
    unique: str
    time: str
    time_full: str
    vote: float
    status: str


class RssInfo(TypedDict):
    title: str
    link: str
    mtype: str
    doubanid: str | None
    year: str | None


class DoubanRankPlusOptimized(_PluginBase):
    # 插件名称
    plugin_name = "豆瓣榜单订阅Plus优化版"
    # 插件描述
    plugin_desc = "豆瓣榜单自动洗版订阅：电影洗版、剧集分集洗版，支持自定义RSS、按季重试与历史去重"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/eitelkeit0708/MoviePilot-Plugins/main/icons/DouBanRankPlus.png"
    # 插件版本
    plugin_version = "1.0.11"
    # 插件作者
    plugin_author = "eitelkeit0708 (based on boeto's work)"
    # 作者主页
    author_url = "https://github.com/eitelkeit0708/MoviePilot-Plugins"
    # 插件配置项ID前缀
    plugin_config_prefix = "doubanrankplusoptimized_"
    # 加载顺序
    plugin_order = 7
    # 可使用的用户级别
    auth_level = 2

    _retry_delays = (15 * 60, 60 * 60, 6 * 60 * 60)
    _retryable_statuses = {
        Status.UNRECOGNIZED.value, Status.SUBSCRIPTION_FAILED.value,
        Status.PROCESS_FAILED.value, Status.YEAR_UNKNOWN.value, Status.RATING_UNKNOWN.value,
        Status.SEASON_UNKNOWN.value, Status.IDENTITY_MISMATCH.value,
    }
    _successful_statuses = {Status.SUBSCRIPTION_ADDED.value, Status.SUBSCRIPTION_EXISTS.value}

    subscribechain: SubscribeChain
    mediachain: MediaChain
    doubanapi: DoubanApi

    # 私有属性
    _msg_install = "适用于 MoviePilot V2；API 在安装或重载插件时自动注册"
    _msg_migrate_install = "请确保原MP已**安装并启用**此插件"

    _scheduler = None

    _enabled: bool = False
    _cron: str = ""
    _onlyonce: bool = False
    _rss_addrs: List[str] = []
    _ranks: List[str] = []
    _vote: float = 0.0
    _clear: bool = False
    _clearflag: bool = False
    _clear_unrecognized: bool = False
    _clearflag_unrecognized: bool = False
    _proxy: bool = False
    _is_seasons_all: bool = True
    _release_year: int = 0
    _min_sleep_time: int = 3
    _max_sleep_time: int = 10
    _history_type: str = HistoryDataType.LATEST.value
    _is_exit_ip_rate_limit: bool = False
    _is_only_movies: bool = False

    _migrate_from_url = ""
    _migrate_api_token = ""
    _migrate_once = False

    def __init__(self):
        """每个运行实例独立持有退出信号、任务锁及可变状态。"""
        super().__init__()
        self._event = Event()
        self._task_lock = Lock()
        self._scheduler = None
        self._rss_addrs = []
        self._ranks = []

    @property
    def _plugin_id(self):
        """使用宿主为原插件或分身分配的当前类名。"""
        return self.__class__.__name__

    def init_plugin(self, config: dict[str, Any] | None = None):
        self.stop_service()
        # 等旧任务退出后再更新配置，避免清除退出信号使旧任务继续运行。
        with self._task_lock:
            self._event.clear()
            self._clearflag = False
            self._clearflag_unrecognized = False
            self.__configure_plugin(config)

    @staticmethod
    def __sleep_range(value):
        try:
            minimum, maximum = map(int, re.split("[,，]", str(value)))
            if 0 <= minimum <= maximum <= 3600:
                return minimum, maximum
        except (ValueError, TypeError):
            pass
        logger.warn("处理间隔格式不正确，使用默认值 3,10（允许 0 至 3600 秒）")
        return 3, 10

    def __configure_plugin(self, config):
        self.subscribechain = SubscribeChain()
        self.mediachain = MediaChain()
        self.doubanapi = DoubanApi()

        config = config or {}
        self._enabled = config.get("enabled", False)
        self._proxy = config.get("proxy", False)
        self._onlyonce = config.get("onlyonce", False)
        self._is_seasons_all = config.get("is_seasons_all", True)
        self._is_only_movies = config.get("is_only_movies", False)

        self._migrate_from_url = config.get("migrate_from_url", "")
        self._migrate_api_token = config.get("migrate_api_token", "")
        self._migrate_once = config.get("migrate_once", False)

        self._cron = (
            config.get("cron", "").strip()
            if config.get("cron", "").strip()
            else ""
        )

        self._release_year = (
            int(str(config.get("release_year") or "").strip())
            if str(config.get("release_year") or "").strip()
            else 0
        )

        self._vote = (
            float(str(config.get("vote", "")).strip())
            if str(config.get("vote", "")).strip()
            else 0.0
        )

        self._min_sleep_time, self._max_sleep_time = self.__sleep_range(config.get("sleep_time", "3,10"))

        rss_addrs = config.get("rss_addrs")
        if rss_addrs and isinstance(rss_addrs, str):
            self._rss_addrs = rss_addrs.split("\n")
        else:
            self._rss_addrs = []

        self._ranks = config.get("ranks", [])
        self._clear = config.get("clear", False)
        self._clear_unrecognized = config.get("clear_unrecognized", False)
        self._history_type = config.get(
            "history_type", HistoryDataType.LATEST.value
        )
        self._is_exit_ip_rate_limit = config.get(
            "is_exit_ip_rate_limit", False
        )

        # 启动服务
        if self._enabled or self._onlyonce:
            save_config = self._onlyonce or self._clear or self._clear_unrecognized
            if self._onlyonce:
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                logger.info("豆瓣榜单Plus服务启动，立即运行一次")
                self._scheduler.add_job(
                    func=self.__start_task,
                    trigger="date",
                    run_date=datetime.datetime.now(
                        tz=pytz.timezone(settings.TZ)
                    )
                    + datetime.timedelta(seconds=3),
                )

                if self._scheduler.get_jobs():
                    # 启动服务
                    self._scheduler.print_jobs()
                    self._scheduler.start()

            if self._onlyonce or self._clear:
                # 记录缓存清理标志
                self._clearflag = self._clear
                # 关闭清理缓存
                self._clear = False

            if self._onlyonce or self._clear_unrecognized:
                # 记录未识别缓存清理标志
                self._clearflag_unrecognized = self._clear_unrecognized
                # 关闭未识别清理缓存
                self._clear_unrecognized = False

            if save_config:
                # 关闭一次性开关
                self._onlyonce = False
                # 保存配置
                self.__update_config()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        [{
            "path": "/xx",
            "endpoint": self.xxx,
            "methods": ["GET", "POST"],
            "summary": "API说明"
        }]
        """
        return [
            {
                "path": "/delete_history",
                "endpoint": self.delete_history,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "删除豆瓣榜单Plus历史记录",
            },
            {
                "path": "/retry_history",
                "endpoint": self.retry_history,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "重新处理失败的榜单条目",
            },
            {
                "path": "/migrate-history",
                "endpoint": self.get_migrate_history,
                "methods": ["GET"],
                "auth": "apikey",
                "summary": "获取豆瓣榜单Plus历史记录",
            },
            {
                "path": "/migrate-config",
                "endpoint": self.get_migrate_config,
                "methods": ["GET"],
                "auth": "apikey",
                "summary": "获取豆瓣榜单Plus配置",
            },
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self._enabled and self._cron:
            return [
                {
                    "id": f"{self._plugin_id}",
                    "name": "豆瓣榜单Plus服务",
                    "trigger": CronTrigger.from_crontab(self._cron),
                    "func": self.__start_task,
                    "kwargs": {},
                }
            ]
        elif self._enabled:
            return [
                {
                    "id": f"{self._plugin_id}",
                    "name": "豆瓣榜单Plus服务",
                    "trigger": CronTrigger.from_crontab("0 8 * * *"),
                    "func": self.__start_task,
                    "kwargs": {},
                }
            ]
        return []

    def get_form(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        return (
            [
                {
                    "component": "VForm",
                    "content": [
                        {
                            "component": "VRow",
                            "content": [
                                {
                                    "component": "VCol",
                                    "props": {"cols": 6, "md": 4},
                                    "content": [
                                        {
                                            "component": "VSwitch",
                                            "props": {
                                                "model": "enabled",
                                                "label": "启用插件",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "component": "VCol",
                                    "props": {"cols": 6, "md": 4},
                                    "content": [
                                        {
                                            "component": "VSwitch",
                                            "props": {
                                                "model": "onlyonce",
                                                "label": "立即运行一次",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "component": "VCol",
                                    "props": {"cols": 6, "md": 4},
                                    "content": [
                                        {
                                            "component": "VSwitch",
                                            "props": {
                                                "model": "proxy",
                                                "label": "使用代理服务器",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "component": "VCol",
                                    "props": {"cols": 6, "md": 4},
                                    "content": [
                                        {
                                            "component": "VSwitch",
                                            "props": {
                                                "model": "is_seasons_all",
                                                "label": "订阅剧集全季度",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "component": "VCol",
                                    "props": {"cols": 6, "md": 4},
                                    "content": [
                                        {
                                            "component": "VSwitch",
                                            "props": {
                                                "model": "is_only_movies",
                                                "label": "只订阅电影",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "component": "VCol",
                                    "props": {"cols": 6, "md": 4},
                                    "content": [
                                        {
                                            "component": "VSwitch",
                                            "props": {
                                                "model": "is_exit_ip_rate_limit",
                                                "label": "豆瓣限制时结束",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "component": "VCol",
                                    "props": {"cols": 6, "md": 4},
                                    "content": [
                                        {
                                            "component": "VSwitch",
                                            "props": {
                                                "model": "clear",
                                                "label": "清理历史记录",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "component": "VCol",
                                    "props": {"cols": 6, "md": 4},
                                    "content": [
                                        {
                                            "component": "VSwitch",
                                            "props": {
                                                "model": "clear_unrecognized",
                                                "label": "清理未识别历史",
                                            },
                                        }
                                    ],
                                },
                            ],
                        },
                        {
                            "component": "VRow",
                            "content": [
                                {
                                    "component": "VCol",
                                    "props": {"cols": 12, "md": 6},
                                    "content": [
                                        {
                                            "component": "VTextField",
                                            "props": {
                                                "model": "cron",
                                                "label": "执行周期",
                                                "placeholder": "5位cron表达式，留空自动",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "component": "VCol",
                                    "props": {"cols": 12, "md": 6},
                                    "content": [
                                        {
                                            "component": "VTextField",
                                            "props": {
                                                "model": "sleep_time",
                                                "label": "条目处理间隔（秒）",
                                                "placeholder": "默认 3,10；每处理一条后随机等待，可填 0,0；范围 0–3600。",
                                            },
                                        }
                                    ],
                                },
                            ],
                        },
                        {
                            "component": "VRow",
                            "content": [
                                {
                                    "component": "VCol",
                                    "props": {"cols": 12, "md": 6},
                                    "content": [
                                        {
                                            "component": "VTextField",
                                            "props": {
                                                "model": "vote",
                                                "label": "MP 识别评分下限",
                                                "placeholder": "0–10，留空不限；不保证是豆瓣评分",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "component": "VCol",
                                    "props": {"cols": 12, "md": 6},
                                    "content": [
                                        {
                                            "component": "VTextField",
                                            "props": {
                                                "model": "release_year",
                                                "label": "上映年份",
                                                "placeholder": "年份大于等于该值才订阅",
                                            },
                                        }
                                    ],
                                },
                            ],
                        },
                        {
                            "component": "VRow",
                            "props": {"cols": 12, "md": 6},
                            "content": [
                                {
                                    "component": "VCol",
                                    "content": [
                                        {
                                            "component": "VSelect",
                                            "props": {
                                                "model": "history_type",
                                                "label": "数据面板历史显示",
                                                "items": [
                                                    {
                                                        "title": f"{HistoryDataType.LATEST.value}",
                                                        "value": f"{HistoryDataType.LATEST.value}",
                                                    },
                                                    {
                                                        "title": f"{HistoryDataType.RECOGNIZED.value}",
                                                        "value": f"{HistoryDataType.RECOGNIZED.value}",
                                                    },
                                                    {
                                                        "title": f"{HistoryDataType.UNRECOGNIZED.value}",
                                                        "value": f"{HistoryDataType.UNRECOGNIZED.value}",
                                                    },
                                                    {
                                                        "title": f"{HistoryDataType.ALL.value}",
                                                        "value": f"{HistoryDataType.ALL.value}",
                                                    },
                                                ],
                                            },
                                        }
                                    ],
                                },
                                {
                                    "component": "VCol",
                                    "props": {"cols": 12, "md": 6},
                                    "content": [
                                        {
                                            "component": "VSelect",
                                            "props": {
                                                "chips": True,
                                                "multiple": True,
                                                "model": "ranks",
                                                "label": "热门榜单",
                                                "items": [
                                                    {
                                                        "title": "电影北美票房榜",
                                                        "value": "movie-ustop",
                                                    },
                                                    {
                                                        "title": "一周口碑电影榜",
                                                        "value": "movie-weekly",
                                                    },
                                                    {
                                                        "title": "实时热门电影",
                                                        "value": "movie-real-time",
                                                    },
                                                    {
                                                        "title": "热门综艺",
                                                        "value": "show-domestic",
                                                    },
                                                    {
                                                        "title": "热门电影",
                                                        "value": "movie-hot-gaia",
                                                    },
                                                    {
                                                        "title": "热门电视剧",
                                                        "value": "tv-hot",
                                                    },
                                                    {
                                                        "title": "电影TOP10",
                                                        "value": "movie-top250",
                                                    },
                                                    {
                                                        "title": "电影TOP250",
                                                        "value": "movie-top250-full",
                                                    },
                                                ],
                                            },
                                        }
                                    ],
                                },
                            ],
                        },
                        {
                            "component": "VRow",
                            "content": [
                                {
                                    "component": "VCol",
                                    "content": [
                                        {
                                            "component": "VTextarea",
                                            "props": {
                                                "model": "rss_addrs",
                                                "label": "自定义榜单地址",
                                                "placeholder": "",
                                            },
                                        },
                                        {
                                            "component": "VAlert",
                                            "props": {
                                                "type": "info",
                                                "variant": "tonal",
                                            },
                                            "content": [
                                                {
                                                    "component": "p",
                                                    "text": "电影和电视剧均创建洗版订阅，电视剧使用分集洗版。已成功处理的记录不会重复创建；失败项最多自动重试三次，分别在15分钟、1小时、6小时后的任务运行中处理，也可在历史卡片中重新处理。",
                                                },
                                                {
                                                    "component": "p",
                                                    "text": "评分采用 MP 识别结果，不保证来自豆瓣。洗版质量、分辨率和优先级由 MP 规则决定：订阅继承的默认过滤规则组优先，未设置时使用 MP 洗版规则组。本插件不单独指定 4K 等质量目标。",
                                                },
                                                {
                                                    "component": "p",
                                                    "text": "全部季度只处理 MP 元数据中实际存在的正式季；集数未提供的季度稍后重试。关闭全部季度时按标题中的明确季号处理，未指定则为第 1 季；特别篇需明确第 0 季。数字结尾片名不会被强制改成季号。RSS 类型、年份或季度与识别结果冲突时暂缓订阅，请在历史卡片查看原因。",
                                                },
                                                {
                                                    "component": "p",
                                                    "text": "每行一个地址。地址后可选加分号 `;`，第一个分号后是自定义地址的下载路径，用#按类型分割下载路径/电影#/电视剧#/动漫；第二个分号后以@开头并以@结尾，则按类型订阅，只订阅电影：@movies@，只订阅电视剧： @tv@。如果你只需要类型则以两个分号+@作为类型选择.。注意电影英文后面是带s的，tv没有s",
                                                },
                                                {
                                                    "component": "p",
                                                    "text": "https://rsshub.app/douban/movie/ustop",
                                                },
                                                {
                                                    "component": "p",
                                                    "text": "https://rsshub.app/douban/movie/ustop;/download_to_path",
                                                },
                                                {
                                                    "component": "p",
                                                    "text": "https://rsshub.app/douban/doulist/44852852;/download_to_movies#/download_to_tv#/download_to_anime",
                                                },
                                                {
                                                    "component": "p",
                                                    "text": "示例格式: URL@@TYPE",
                                                },
                                                {
                                                    "component": "p",
                                                    "text": "https://rsshub.app/douban/list/tv_real_time_hotest@@TV",
                                                },
                                                {
                                                    "component": "p",
                                                    "text": "https://rsshub.app/douban/movie/ustop@@Movie",
                                                },{
                                                    "component": "p",
                                                    "text": "http://192.168.50.6:1200/douban/list/ECFA5DI7Q/@@TV",
                                                },
                                            ],
                                        },
                                        {
                                            "component": "VAlert",
                                            "props": {
                                                "type": "info",
                                                "variant": "tonal",
                                            },
                                            "content": [
                                                {
                                                    "component": "span",
                                                    "text": "每行一个RSS地址，格式：URL@@TYPE（可选 TV 或 Movie）。类型用于辅助识别和校验；标题中的明确季号由 MP 解析。",
                                                },
                                            ],
                                        },
                                    ],
                                }
                            ],
                        },
                        {
                            "component": "VRow",
                            "content": [
                                {
                                    "component": "VCol",
                                    "props": {"cols": 12},
                                    "content": [
                                        {
                                            "component": "VAlert",
                                            "props": {
                                                "type": "info",
                                                "variant": "tonal",
                                            },
                                            "content": [
                                                {
                                                    "component": "span",
                                                    "text": f"{self._msg_install}",
                                                }
                                            ],
                                        },
                                        {
                                            "component": "VAlert",
                                            "props": {
                                                "type": "info",
                                                "variant": "tonal",
                                            },
                                            "content": [
                                                {
                                                    "component": "span",
                                                    "text": f"下面配置仅在需要迁移插件的历史记录和配置时，在新MP中填写，开启运行一次选项并立即运行一次。原MP不需要填写下面的配置或开启选项，{self._msg_migrate_install}",
                                                }
                                            ],
                                        },
                                    ],
                                },
                            ],
                        },
                        {
                            "component": "VRow",
                            "content": [
                                {
                                    "component": "VCol",
                                    "props": {"cols": 12},
                                    "content": [
                                        {
                                            "component": "VSwitch",
                                            "props": {
                                                "model": "migrate_once",
                                                "label": "迁移配置和历史一次",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "component": "VCol",
                                    "props": {"cols": 12, "md": 6},
                                    "content": [
                                        {
                                            "component": "VTextField",
                                            "props": {
                                                "model": "migrate_from_url",
                                                "label": "原MP地址: 例如 http://mp.com:3001",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "component": "VCol",
                                    "props": {"cols": 12, "md": 6},
                                    "content": [
                                        {
                                            "component": "VTextField",
                                            "props": {
                                                "model": "migrate_api_token",
                                                "label": "原MP API Token",
                                            },
                                        }
                                    ],
                                },
                            ],
                        },
                    ],
                }
            ],
            {
                "enabled": False,
                "cron": "",
                "proxy": False,
                "onlyonce": False,
                "vote": 0.0,
                "ranks": [],
                "rss_addrs": [],
                "clear": False,
                "clear_unrecognized": False,
                "release_year": "0",
                "sleep_time": "3,10",
                "is_seasons_all": True,
                "is_only_movies": False,
                "history_type": HistoryDataType.LATEST.value,
                "is_exit_ip_rate_limit": False,
                "migrate_from_url": "",
                "migrate_api_token": "",
                "migrate_once": False,
            },
        )

    @staticmethod
    def __get_svg_content(color: str, ds: List[str]):
        def __get_path_content(fill: str, d: str) -> dict[str, Any]:
            return {
                "component": "path",
                "props": {"fill": fill, "d": d},
            }

        path_content = [__get_path_content(color, d) for d in ds]
        component = {
            "component": "svg",
            "props": {
                "class": "icon",
                "viewBox": "0 0 1024 1024",
                "width": "40",
                "height": "40",
            },
            "content": path_content,
        }
        return component

    @classmethod
    def __get_icon_content(cls):
        color = "#8a8a8a"
        icon_content = {
            Icons.RECOGNIZED: cls.__get_svg_content(
                color,
                [
                    "M512 417.792c-53.248 0-94.208 40.96-94.208 94.208 0 53.248 40.96 94.208 94.208 94.208 53.248 0 94.208-40.96 94.208-94.208 0-53.248-40.96-94.208-94.208-94.208z",
                    "M512 229.376C245.76 229.376 36.864 475.136 28.672 487.424c-12.288 16.384-12.288 36.864 0 53.248 8.192 12.288 217.088 258.048 483.328 258.048 266.24 0 475.136-245.76 483.328-258.048 12.288-16.384 12.288-36.864 0-53.248-8.192-12.288-217.088-258.048-483.328-258.048z m0 479.232c-106.496 0-196.608-90.112-196.608-196.608 0-110.592 90.112-196.608 196.608-196.608 110.592 0 196.608 90.112 196.608 196.608 0 110.592-86.016 196.608-196.608 196.608zM61.44 741.376c-24.576 0-40.96 16.384-40.96 40.96v180.224c0 24.576 16.384 40.96 40.96 40.96h180.224c24.576 0 40.96-16.384 40.96-40.96s-16.384-40.96-40.96-40.96H102.4v-139.264c0-24.576-16.384-40.96-40.96-40.96zM61.44 282.624c24.576 0 40.96-16.384 40.96-40.96V102.4H245.76c24.576 0 40.96-16.384 40.96-40.96s-16.384-40.96-40.96-40.96H61.44c-24.576 0-40.96 16.384-40.96 40.96V245.76c0 20.48 16.384 36.864 40.96 36.864zM782.336 102.4h139.264v139.264c0 24.576 16.384 40.96 40.96 40.96s40.96-16.384 40.96-40.96V61.44c0-24.576-16.384-40.96-40.96-40.96h-180.224c-24.576 0-40.96 16.384-40.96 40.96s16.384 40.96 40.96 40.96zM962.56 741.376c-24.576 0-40.96 16.384-40.96 40.96v143.36h-139.264c-24.576 0-40.96 16.384-40.96 40.96s16.384 40.96 40.96 40.96h180.224c24.576 0 40.96-16.384 40.96-40.96v-184.32c0-24.576-16.384-40.96-40.96-40.96z",
                ],
            ),
            Icons.STATISTICS: cls.__get_svg_content(
                color,
                [
                    "M471.04 270.336V20.48c-249.856 20.48-450.56 233.472-450.56 491.52 0 274.432 225.28 491.52 491.52 491.52 118.784 0 229.376-40.96 315.392-114.688L655.36 708.608c-40.96 28.672-94.208 45.056-139.264 45.056-135.168 0-245.76-106.496-245.76-245.76 0-114.688 81.92-217.088 200.704-237.568z",
                    "M552.96 20.48v249.856C655.36 286.72 737.28 368.64 753.664 471.04h249.856C983.04 233.472 790.528 40.96 552.96 20.48zM712.704 651.264l176.128 176.128c65.536-77.824 106.496-172.032 114.688-274.432h-249.856c-8.192 36.864-20.48 69.632-40.96 98.304z",
                ],
            ),
            Icons.UNRECOGNIZED: cls.__get_svg_content(
                color,
                [
                    "M241.664 921.6H102.4v-139.264c0-24.576-16.384-40.96-40.96-40.96s-40.96 16.384-40.96 40.96v180.224c0 24.576 16.384 40.96 40.96 40.96h180.224c24.576 0 40.96-16.384 40.96-40.96s-16.384-40.96-40.96-40.96zM245.76 20.48H61.44c-24.576 0-40.96 16.384-40.96 40.96V245.76c0 24.576 16.384 40.96 40.96 40.96s40.96-16.384 40.96-40.96V102.4H245.76c24.576 0 40.96-16.384 40.96-40.96s-20.48-40.96-40.96-40.96zM962.56 20.48h-180.224c-24.576 0-40.96 16.384-40.96 40.96s16.384 40.96 40.96 40.96h139.264v139.264c0 24.576 16.384 40.96 40.96 40.96s40.96-16.384 40.96-40.96V61.44c0-24.576-16.384-40.96-40.96-40.96zM962.56 741.376c-24.576 0-40.96 16.384-40.96 40.96v143.36h-139.264c-24.576 0-40.96 16.384-40.96 40.96s16.384 40.96 40.96 40.96h180.224c24.576 0 40.96-16.384 40.96-40.96v-184.32c0-24.576-16.384-40.96-40.96-40.96zM696.32 401.408c0-102.4-81.92-184.32-184.32-184.32S327.68 299.008 327.68 401.408c0 57.344 24.576 110.592 69.632 143.36l-36.864 204.8c-4.096 12.288 0 28.672 8.192 36.864 8.192 12.288 20.48 16.384 36.864 16.384h212.992c12.288 0 28.672-4.096 36.864-16.384 8.192-12.288 12.288-24.576 8.192-36.864l-36.864-204.8c45.056-28.672 69.632-81.92 69.632-143.36z"
                ],
            ),
            Icons.RSS: cls.__get_svg_content(
                color,
                [
                    "M320.16155 831.918c0 70.738-57.344 128.082-128.082 128.082S63.99955 902.656 63.99955 831.918s57.344-128.082 128.082-128.082 128.08 57.346 128.08 128.082z m351.32 94.5c-16.708-309.2-264.37-557.174-573.9-573.9C79.31155 351.53 63.99955 366.21 63.99955 384.506v96.138c0 16.83 12.98 30.944 29.774 32.036 223.664 14.568 402.946 193.404 417.544 417.544 1.094 16.794 15.208 29.774 32.036 29.774h96.138c18.298 0.002 32.978-15.31 31.99-33.58z m288.498 0.576C943.19155 459.354 566.92955 80.89 97.00555 64.02 78.94555 63.372 63.99955 77.962 63.99955 96.032v96.136c0 17.25 13.67 31.29 30.906 31.998 382.358 15.678 689.254 322.632 704.93 704.93 0.706 17.236 14.746 30.906 31.998 30.906h96.136c18.068-0.002 32.658-14.948 32.01-33.008z"
                ],
            ),
        }
        return icon_content

    @classmethod
    def __get_historys_statistic_content(
        cls, title: str, value: str, icon_name: Icons
    ) -> dict[str, Any]:
        icon_content = cls.__get_icon_content().get(icon_name, "")
        total_elements = {
            "component": "VCol",
            "props": {"cols": 6, "md": 3},
            "content": [
                {
                    "component": "VCard",
                    "props": {
                        "variant": "tonal",
                    },
                    "content": [
                        {
                            "component": "VCardText",
                            "props": {
                                "class": "d-flex align-center",
                            },
                            "content": [
                                icon_content,
                                {
                                    "component": "div",
                                    "props": {
                                        "class": "ml-2",
                                    },
                                    "content": [
                                        {
                                            "component": "span",
                                            "props": {"class": "text-caption"},
                                            "text": f"{title}",
                                        },
                                        {
                                            "component": "div",
                                            "props": {
                                                "class": "d-flex align-center flex-wrap"
                                            },
                                            "content": [
                                                {
                                                    "component": "span",
                                                    "props": {
                                                        "class": "text-h6"
                                                    },
                                                    "text": f"{value}",
                                                }
                                            ],
                                        },
                                    ],
                                },
                            ],
                        }
                    ],
                },
            ],
        }
        return total_elements

    def __get_historys_statistics_content(
        self,
        historys_total,
        historys_recognized_total,
        historys_unrecognized_total,
    ):
        addr_list = self._rss_addrs + [
            self._douban_address.get(rank) for rank in self._ranks
        ]

        # 数据统计
        data_statistics = [
            {
                "title": "历史总计数量",
                "value": historys_total,
                "icon_name": Icons.STATISTICS,
            },
            {
                "title": "已识别数量",
                "value": historys_recognized_total,
                "icon_name": Icons.RECOGNIZED,
            },
            {
                "title": "未识别数量",
                "value": historys_unrecognized_total,
                "icon_name": Icons.UNRECOGNIZED,
            },
            {
                "title": "榜单数量",
                "value": len(addr_list),
                "icon_name": Icons.RSS,
            },
        ]

        content = list(
            map(
                lambda s: self.__get_historys_statistic_content(
                    title=s["title"],
                    value=s["value"],
                    icon_name=s["icon_name"],
                ),
                data_statistics,
            )
        )

        component = {"component": "VRow", "content": content}
        return component

    def __get_history_post_content(self, history: HistoryPayload):
        title = history.get("title", "")
        if len(title) > 8:
            title = title[:8] + "..."
        title = title.replace(" ", "")

        year = history.get("year")
        vote = history.get("vote")
        poster = history.get("poster")
        time_str = history.get("time")
        mtype = history.get("type")
        doubanid = history.get("doubanid")
        tmdbid = history.get("tmdbid")

        status = history.get("status")
        unique = history.get("unique")

        if (
            tmdbid
            and tmdbid != "0"
            and (mtype == MediaType.MOVIE.value or mtype == MediaType.TV.value)
        ):
            type_str = "movie" if mtype == MediaType.MOVIE.value else "tv"
            href = f"https://www.themoviedb.org/{type_str}/{tmdbid}"
        elif doubanid and doubanid != "0":
            href = f"https://movie.douban.com/subject/{doubanid}"
        else:
            href = "#"

        component = {
            "component": "VCard",
            "props": {
                "variant": "tonal",
            },
            "content": [
                {
                    "component": "VDialogCloseBtn",
                    "props": {
                        "innerClass": "absolute -top-4 right-0 scale-50 opacity-50",
                    },
                    "events": {
                        "click": {
                            "api": f"plugin/{self._plugin_id}/delete_history",
                            "method": "get",
                            "params": {
                                "key": f"{unique}",
                            },
                        }
                    },
                },
                {
                    "component": "div",
                    "props": {
                        "class": "d-flex justify-space-start flex-nowrap flex-row",
                    },
                    "content": [
                        {
                            "component": "div",
                            "content": [
                                {
                                    "component": "VImg",
                                    "props": {
                                        "src": poster,
                                        "height": 150,
                                        "width": 100,
                                        "aspect-ratio": "2/3",
                                        "class": "object-cover shadow ring-gray-500",
                                        "cover": True,
                                        "transition": True,
                                        "lazy-src": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAGQAAACWCAQAAACCseXNAAAAkklEQVR42u3PAREAAAQEMJ9cFFUVkMBtDZbpeiEiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIpcFcbGoK4SMl3wAAAAASUVORK5CYII=",  # 添加懒加载
                                    },
                                }
                            ],
                        },
                        {
                            "component": "div",
                            "content": [
                                {
                                    "component": "VCardTitle",
                                    "props": {
                                        "class": "py-1 pl-2 pr-4 text-lg whitespace-nowrap"
                                    },
                                    "content": [
                                        {
                                            "component": "a",
                                            "props": {
                                                "href": f"{href}",
                                                "target": "_blank",
                                            },
                                            "text": title,
                                        }
                                    ],
                                },
                                {
                                    "component": "VCardText",
                                    "props": {"class": "pa-0 px-2"},
                                    "text": f"类型: {mtype}",
                                },
                                {
                                    "component": "VCardText",
                                    "props": {"class": "pa-0 px-2"},
                                    "text": f"年份: {year}",
                                },
                                {
                                    "component": "VCardText",
                                    "props": {"class": "pa-0 px-2"},
                                    "text": f"MP 识别评分: {vote}",
                                },
                                {
                                    "component": "VCardText",
                                    "props": {"class": "pa-0 px-2"},
                                    "text": f"时间: {time_str}",
                                },
                                {
                                    "component": "VCardText",
                                    "props": {"class": "pa-0 px-2"},
                                    "text": f"状态: {status}",
                                },
                            ],
                        },
                    ],
                },
            ],
        }

        details = []
        if history.get("summary"):
            details.append(history["summary"])
        if history.get("error"):
            details.append(history["error"])
        results = list(history.get("season_results", {}).values())
        for result in results:
            label = f"第 {result['season']} 季" if result.get("season") is not None else "电影"
            details.append(f"{label}：{result['status']}" + (f"（{result['error']}）" if result.get("error") else ""))
        retries = [history["retry"]] if history.get("retry") else [r["retry"] for r in results if r.get("retry")]
        due = [r["next_retry_at"] for r in retries if r.get("next_retry_at") is not None]
        if history.get("manual_retry"):
            details.append("已安排重新处理")
        elif due:
            if min(due) <= time.time():
                details.append("已到重试时间，等待下次任务运行")
            else:
                when = datetime.datetime.fromtimestamp(min(due), tz=pytz.timezone(settings.TZ))
                details.append(f"下次重试：{when:%m-%d %H:%M} 后的任务运行中")
        if any(r.get("next_retry_at") is None for r in retries):
            details.append("部分失败项已达自动重试上限，可重新处理")
        component["content"].extend({
            "component": "VCardText", "props": {"class": "py-1 px-2"}, "text": detail,
        } for detail in details)
        if self.__can_retry(history):
            component["content"].append({
                "component": "VBtn", "props": {"variant": "text", "size": "small"},
                "text": "重新处理",
                "events": {"click": {
                    "api": f"plugin/{self._plugin_id}/retry_history", "method": "post",
                    "params": {"key": unique},
                }},
            })
        return component

    def __get_historys_posts_content(
        self, historys: List[HistoryPayload] | None
    ):
        posts_content = []
        if not historys:
            posts_content = [
                {
                    "component": "div",
                    "text": "暂无数据",
                    "props": {
                        "class": "text-start",
                    },
                }
            ]
        else:
            for history in historys:
                posts_content.append(self.__get_history_post_content(history))

        component = {
            "component": "div",
            "content": [
                {
                    "component": "VCardTitle",
                    "props": {
                        "class": "pt-6 pb-2 px-0 text-base whitespace-nowrap"
                    },
                    "content": [
                        {
                            "component": "span",
                            "text": f"{self._history_type}",
                        }
                    ],
                },
                {
                    "component": "div",
                    "props": {
                        "class": "grid gap-3 grid-info-card p-4",
                    },
                    "content": posts_content,
                },
            ],
        }

        return component

    def get_page(self) -> List[Dict[str, Any]]:
        """
        拼装插件详情页面，需要返回页面配置，同时附带数据
        """

        # 查询历史记录
        historys = self.get_data("history")
        if not historys:
            return [
                {
                    "component": "div",
                    "text": "暂无数据",
                    "props": {
                        "class": "text-center",
                    },
                }
            ]

        # 数据按时间降序排序
        historys = sorted(
            historys, key=lambda x: x.get("time_full"), reverse=True
        )

        history_recognized = []
        history_unrecognized = []

        for history in historys:
            if history.get("status") != Status.UNRECOGNIZED.value:
                history_recognized.append(history)
            else:
                history_unrecognized.append(history)

        history_recognized = sorted(
            history_recognized, key=lambda x: x.get("time_full"), reverse=True
        )
        history_unrecognized = sorted(
            history_unrecognized,
            key=lambda x: x.get("time_full"),
            reverse=True,
        )

        historys_total = len(historys)
        historys_recognized_total = len(history_recognized)
        historys_unrecognized_total = len(history_unrecognized)

        historys_in_type: list[HistoryPayload] | None = None
        if self._history_type == HistoryDataType.LATEST.value:
            historys_in_type = historys[:12]
        elif self._history_type == HistoryDataType.RECOGNIZED.value:
            historys_in_type = history_recognized
        elif self._history_type == HistoryDataType.UNRECOGNIZED.value:
            historys_in_type = history_unrecognized
        elif self._history_type == HistoryDataType.ALL.value:
            historys_in_type = historys

        historys_posts_content = self.__get_historys_posts_content(
            historys_in_type
        )
        historys_statistics_content = self.__get_historys_statistics_content(
            historys_total,
            historys_recognized_total,
            historys_unrecognized_total,
        )

        # 拼装页面
        return [
            {
                "component": "div",
                "content": [
                    historys_statistics_content,
                    historys_posts_content,
                ],
            }
        ]

    def stop_service(self):
        """
        停止服务
        """
        self._event.set()
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown(wait=False)
                self._scheduler = None
        except Exception as e:
            logger.error(f"停止插件服务失败：{type(e).__name__}")

    def __validate_token(self, api_token: str) -> Any:
        """
        验证 API 密钥
        """
        if api_token != settings.API_TOKEN:
            return Response(success=False, message="API密钥错误")
        return None

    def delete_history(self, key: str):
        """
        删除同步历史记录
        """
        logger.debug(f"删除同步历史记录:::{key}")
        # 身份认证由 get_api() 声明的宿主 bear 依赖执行。
        if not self._task_lock.acquire(blocking=False):
            return Response(success=False, message="榜单任务正在运行，请结束后再操作")
        try:
            historys = self.get_data("history")
            if not historys:
                return Response(success=False, message="未找到历史记录")
            historys = [h for h in historys if h.get("unique") != key]
            self.save_data("history", historys)
            return Response(success=True, message="删除成功")
        finally:
            self._task_lock.release()

    @classmethod
    def __can_retry(cls, history):
        return history.get("status") in cls._retryable_statuses | {
            Status.PARTIAL_SUCCESS.value, Status.YEAR_NOT_MATCH.value, Status.RATING_NOT_MATCH.value,
        }

    def retry_history(self, payload: Dict[str, str]):
        # MP PageRender 通过登录态调用，POST 参数是 JSON，请勿在页面下发 API Token。
        key = payload.get("key", "")
        if not self._task_lock.acquire(blocking=False):
            return Response(success=False, message="榜单任务正在运行，请结束后再操作")
        try:
            history = self.get_data("history") or []
            record = next((item for item in history if item.get("unique") == key), None)
            if record is None or not self.__can_retry(record):
                return Response(success=False, message="未找到可重试的失败或跳过记录")
            sources = self._rss_addrs + [self._douban_address.get(rank) for rank in self._ranks]
            if not sources or record.get("source") and record["source"] not in sources:
                return Response(success=False, message="请先恢复该条目所属的榜单来源")
            record["manual_retry"] = True
            if record.get("retry") is not None:
                record["retry"] = {"attempts": 0, "next_retry_at": 0}
            for result in record.get("season_results", {}).values():
                if result.get("status") in self._retryable_statuses:
                    result["retry"] = {"attempts": 0, "next_retry_at": 0}
            self.save_data("history", history)
            try:
                if self._scheduler is None:
                    self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                self._event.clear()
                self._scheduler.add_job(
                    self.__start_task, trigger="date", id="retry_history", replace_existing=True,
                    kwargs={"retry_only": True},
                    run_date=datetime.datetime.now(tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=1),
                )
                if not self._scheduler.running:
                    self._scheduler.start()
            except Exception as error:
                logger.error(f"安排榜单重试失败: {error}")
                return Response(success=True, message="重试标记已保存，请使用立即运行一次或等待下次定时任务")
            return Response(success=True, message="已安排重新处理；成功季度会保留，旧版记录需再次出现在榜单中")
        finally:
            self._task_lock.release()

    def get_migrate_history(self, migrate_api_token: str):
        """
        获取迁移l历史记录
        """
        logger.debug("获取迁移历史记录")
        validation_response = self.__validate_token(migrate_api_token)
        if validation_response:
            return validation_response

        return self.get_data("history")

    def get_migrate_config(self, migrate_api_token: str):
        """
        获取迁移配置
        """
        validation_response = self.__validate_token(migrate_api_token)
        if validation_response:
            return validation_response

        __config = self.__get_config()
        # 删除不需要的键
        for key in ["migrate_api_token", "migrate_from_url", "migrate_once"]:
            __config.pop(key, None)
        return __config

    def __get_config(self):
        """
        获取配置
        """
        return {
            "enabled": self._enabled,
            "proxy": self._proxy,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "vote": self._vote,
            "ranks": self._ranks,
            "rss_addrs": "\n".join(map(str, self._rss_addrs)),
            "clear": self._clear,
            "clear_unrecognized": self._clear_unrecognized,
            "is_seasons_all": self._is_seasons_all,
            "is_only_movies": self._is_only_movies,
            "release_year": str(self._release_year),
            "sleep_time": f"{self._min_sleep_time},{self._max_sleep_time}",
            "history_type": self._history_type,
            "is_exit_ip_rate_limit": self._is_exit_ip_rate_limit,
            "migrate_from_url": self._migrate_from_url.rstrip("/"),
            "migrate_api_token": self._migrate_api_token,
            "migrate_once": self._migrate_once,
        }

    def __update_config(self):
        """
        更新配置
        """
        __config = self.__get_config()
        logger.debug("保存豆瓣榜单插件配置")
        self.update_config(__config)

    def __start_task(self, retry_only=False):
        if not self._task_lock.acquire(blocking=False):
            logger.info("榜单任务正在运行，本次触发跳过")
            return
        try:
            if self._event.is_set():
                return
            self.__run_task(retry_only=retry_only)
        finally:
            self._task_lock.release()

    def __run_task(self, retry_only=False):
        """
        运行任务
        """
        if self._migrate_once:
            if self._migrate_from_url and self._migrate_api_token:
                logger.info("开始从原MP迁移配置...")
                __original_config = self.__get_migrate_config()
                if __original_config and isinstance(__original_config, dict):
                    self._enabled = __original_config.get(
                        "enabled", self._enabled
                    )
                    self._cron = __original_config.get("cron", self._cron)
                    self._onlyonce = __original_config.get(
                        "onlyonce", self._onlyonce
                    )
                    self._vote = __original_config.get("vote", self._vote)
                    self._ranks = __original_config.get("ranks", self._ranks)
                    self._rss_addrs = __original_config.get(
                        "rss_addrs", self._rss_addrs
                    ).split("\n")
                    self._clear = __original_config.get("clear", self._clear)
                    self._clear_unrecognized = __original_config.get(
                        "clear_unrecognized", self._clear_unrecognized
                    )
                    self._is_seasons_all = __original_config.get(
                        "is_seasons_all", self._is_seasons_all
                    )
                    self._is_only_movies = __original_config.get(
                        "_is_only_movies", self._is_only_movies
                    )

                    self._release_year = __original_config.get(
                        "release_year", self._release_year
                    )
                    self._min_sleep_time, self._max_sleep_time = self.__sleep_range(
                        __original_config.get("sleep_time", "3,10")
                    )
                    self._history_type = __original_config.get(
                        "history_type", self._history_type
                    )
                    self._is_exit_ip_rate_limit = __original_config.get(
                        "is_exit_ip_rate_limit", self._is_exit_ip_rate_limit
                    )
                else:
                    logger.warn("未获取到原MP配置，结束程序")
                    return

                __original_history = self.__get_migrate_history()
                if __original_history:
                    self.save_data("history", __original_history)
                else:
                    logger.warn("未获取到历史记录，结束程序")
                    return

                # 关闭一次性开关
                self._migrate_once = False
                self.__update_config()
                logger.info("迁移配置和历史完成")
            else:
                logger.error(
                    "迁移配置错误，请检查是否填写了原MP地址和原MP API Token"
                )
                return

        logger.info("开始刷新豆瓣榜单Plus ...")
        addr_list = self._rss_addrs + [
            self._douban_address.get(rank) for rank in self._ranks
        ]
        if not addr_list:
            logger.info("未设置榜单RSS地址")
            return
        else:
            logger.info(f"共 {len(addr_list)} 个榜单RSS地址需要刷新")

        # 读取历史记录
        if self._clearflag:
            history = []  # type: ignore
            self.save_data("history", history)
            # 历史只清理一次
            self._clearflag = False
            logger.info(f"已清理所有 {self.plugin_name} 的历史记录")
        else:
            history = self.get_data("history") or []
            if history and self._clearflag_unrecognized:
                original_length = len(history)
                history = [
                    h
                    for h in history
                    if h.get("status") != Status.UNRECOGNIZED.value
                ]
                deleted_count = original_length - len(history)
                self.save_data("history", history)
                # 未识别历史只清理一次
                self._clearflag_unrecognized = False
                logger.info(
                    f"已清理 {deleted_count} 条 {self.plugin_name} 未识别的历史记录"
                )

        history_by_key = {}
        self._successful_targets = set()
        self._next_item_at = 0
        for record in history:
            if not isinstance(record, dict):
                continue
            self.__remember_successes(record)
            for key in self.__history_keys(record):
                incumbent = history_by_key.get(key)
                if incumbent is None or self.__history_priority(record) > self.__history_priority(incumbent):
                    history_by_key[key] = record
        requested = {h["unique"] for h in history if isinstance(h, dict) and h.get("manual_retry")}
        # 新版历史已有原条目，单条重试直接复用；旧历史才需要从榜单重新查找。
        fetch_rss = not retry_only or any(not history_by_key[key].get("rss_info") for key in requested)
        processed = set()
        contexts = {}
        for addr_index, source in enumerate(addr_list):
            if not source or self._event.is_set():
                continue
            try:
                rss_url, type_hint = self.__parse_rss_config(source)
                if not rss_url:
                    continue
                rss_type = {"movie": MediaType.MOVIE, "tv": MediaType.TV}.get(
                    (type_hint or "").lower()
                )
                addr_info = self.__get_info_addr(rss_url)
                context = dict(addr_info, source=source, rss_type=rss_type)
                contexts[source] = context
                rss_infos = self.__get_rss_info(addr_info.get("addr")) if fetch_rss else []
                logger.info(f"榜单 {addr_index + 1}/{len(addr_list)} 获取到 {len(rss_infos)} 条数据")
                for rss_info in rss_infos:
                    if self._event.is_set():
                        return
                    rss_info = self.__apply_type_hint(rss_info, rss_type)
                    if retry_only:
                        record = self.__find_history(history_by_key, rss_info) if isinstance(rss_info, dict) else {}
                        if not record or record.get("unique") not in requested:
                            continue
                    self.__dispatch_item(rss_info, context, history, history_by_key, processed)
            except Exception as error:
                logger.error(f"处理RSS地址 {source} 失败: {error}")

        # 失败条目即使退出榜单仍可重试；来源移除后不再自动处理。
        for previous in list(history):
            if self._event.is_set():
                return
            if not isinstance(previous, dict):
                continue
            context = contexts.get(previous.get("source"))
            if context and previous.get("rss_info") and (not retry_only or previous.get("unique") in requested):
                self.__dispatch_item(previous["rss_info"], context, history, history_by_key, processed)
        logger.info("所有榜单RSS刷新完成")

    @classmethod
    def __legacy_unique(cls, rss_info):
        return (f"{cls.plugin_config_prefix}{rss_info.get('title')}_"
                f"{rss_info.get('year')}_(DB:{rss_info.get('doubanid')})")

    @staticmethod
    def __positive_id(value):
        value = str(value or "")
        return str(int(value)) if re.fullmatch(r"[0-9]+", value) and int(value) > 0 else None

    @classmethod
    def __unique(cls, rss_info):
        doubanid = cls.__positive_id(rss_info.get("doubanid"))
        if doubanid:
            return f"{cls.plugin_config_prefix}douban:{doubanid}"
        title = " ".join(str(rss_info.get("title") or "").split()).casefold()
        kind = {"movie": "movie", "电影": "movie", "tv": "tv", "电视剧": "tv"}.get(
            str(rss_info.get("mtype") or rss_info.get("type") or "").strip().lower(), "unknown"
        )
        return f"{cls.plugin_config_prefix}title:{title}|year:{rss_info.get('year') or ''}|type:{kind}"

    @staticmethod
    def __apply_type_hint(rss_info, type_hint):
        if isinstance(rss_info, dict) and type_hint in (MediaType.MOVIE, MediaType.TV):
            return dict(rss_info, mtype="movie" if type_hint == MediaType.MOVIE else "tv")
        return rss_info

    @classmethod
    def __history_keys(cls, record):
        # 原 unique 保留给历史卡片/API；别名仅用于索引，不重写旧历史。
        keys = {record.get("unique")}
        info = record.get("rss_info") or record
        keys.add(cls.__unique(info))
        match = re.search(r"\(DB:([0-9]+)\)$", str(record.get("unique") or ""))
        if match and cls.__positive_id(match[1]):
            keys.add(cls.__unique({"doubanid": match[1]}))
        return keys - {None, ""}

    @classmethod
    def __history_priority(cls, record):
        return (record.get("status") in cls._successful_statuses,
                bool(record.get("manual_retry")), bool(record.get("season_results")))

    @classmethod
    def __find_history(cls, index, rss_info):
        return index.get(cls.__unique(rss_info)) or index.get(cls.__legacy_unique(rss_info))

    @classmethod
    def __target_key(cls, identity, season):
        tmdbid = cls.__positive_id(identity.get("tmdbid"))
        mtype = identity.get("type")
        if tmdbid and mtype in (MediaType.MOVIE.value, MediaType.TV.value):
            return mtype, tmdbid, season if mtype == MediaType.TV.value else None
        return None

    def __remember_successes(self, record):
        identity = record.get("identity") or record
        results = record.get("season_results", {}).values()
        # 旧电影记录可安全还原目标；旧电视剧未保存季号，不猜测已完成的季度。
        if not results and identity.get("type") == MediaType.MOVIE.value:
            results = [{"season": None, "status": record.get("status")}]
        for result in results:
            target = self.__target_key(identity, result.get("season"))
            if target and result.get("status") in self._successful_statuses:
                self._successful_targets.add(target)

    @classmethod
    def __retry_due(cls, result, now):
        if result.get("status") not in cls._retryable_statuses:
            return False
        # 旧版失败记录没有重试信息，允许在再次遇到时补做一次。
        retry = result.get("retry")
        return retry is None or (retry.get("next_retry_at") is not None
                                 and retry["next_retry_at"] <= now)

    @classmethod
    def __history_due(cls, record, now):
        if not record or record.get("manual_retry"):
            return True
        if record.get("retry") is not None:
            return cls.__retry_due(record, now)
        if record.get("season_results"):
            return any(cls.__retry_due(result, now) for result in record["season_results"].values())
        return cls.__retry_due(record, now)

    @classmethod
    def __failure_retry(cls, previous):
        attempts = (previous.get("retry") or {}).get("attempts", 0) + 1
        delay = cls._retry_delays[attempts - 1] if attempts <= len(cls._retry_delays) else None
        return {"attempts": attempts, "next_retry_at": time.time() + delay if delay is not None else None}

    def __save_history_item(self, history, history_by_key, payload):
        key = payload["unique"]
        previous = history_by_key.get(key)
        if previous is None:
            saved = deepcopy(payload)
            history.append(saved)
        else:
            previous.clear()
            previous.update(deepcopy(payload))
            saved = previous
        for alias in self.__history_keys(saved):
            history_by_key[alias] = saved
        self.save_data("history", history)
        self.__remember_successes(saved)

    def __dispatch_item(self, rss_info, context, history, history_by_key, processed):
        rss_info = self.__apply_type_hint(rss_info, context.get("rss_type"))
        if not isinstance(rss_info, dict) or not isinstance(rss_info.get("title"), str) or not rss_info["title"].strip():
            logger.warn("RSS条目缺少标题，跳过")
            return
        key = self.__unique(rss_info)
        if key in processed:
            return
        previous = deepcopy(self.__find_history(history_by_key, rss_info) or {})
        if not self.__history_due(previous, time.time()):
            return
        delay = max(0, self._next_item_at - time.monotonic())
        if self._event.is_set() or (delay and self._event.wait(delay)):
            return
        processed.add(key)
        payload = deepcopy(previous)
        payload.update(self.__get_history_unrecognized_payload(
            rss_info["title"], previous.get("unique") or key, rss_info.get("year"), rss_info.get("doubanid")
        ))
        # 识别暂时失败时，保留上次的作品身份、展示信息与各季成功记录。
        for field in ("type", "year", "poster", "overview", "tmdbid", "vote"):
            if field in previous:
                payload[field] = previous[field]
        payload.update(rss_info=deepcopy(rss_info), source=context["source"])
        try:
            self.__process_item(rss_info, context, previous, payload, history, history_by_key)
        except Exception as error:
            logger.error(f"处理 {rss_info['title']} 失败: {error}")
            payload.update(status=Status.PROCESS_FAILED.value,
                           error=f"处理异常：{type(error).__name__}",
                           retry=self.__failure_retry(previous))
        payload.pop("manual_retry", None)
        self.__save_history_item(history, history_by_key, payload)
        self._next_item_at = time.monotonic() + random.uniform(self._min_sleep_time, self._max_sleep_time)

    def __process_item(self, rss_info, context, previous, payload, history, history_by_key):
        type_str = str(rss_info.get("mtype") or "").strip().lower()
        inferred_type = context.get("rss_type") or {
            "movie": MediaType.MOVIE, "电影": MediaType.MOVIE,
            "tv": MediaType.TV, "电视剧": MediaType.TV,
        }.get(type_str)
        meta = MetaInfo(self.__clean_title(rss_info["title"], inferred_type))
        meta.year = rss_info.get("year")
        if inferred_type:
            meta.type = inferred_type
        mediainfo = self.mediachain.recognize_media(meta=meta)
        if not mediainfo:
            payload.update(status=Status.UNRECOGNIZED.value, error="MP暂未识别到媒体信息",
                           retry=self.__failure_retry(previous))
            return
        seasons_info = self.__season_catalog(mediainfo)
        problem = self.__recognition_problem(rss_info, meta, inferred_type, mediainfo, seasons_info)
        if problem:
            status, message = problem
            payload.update(status=status.value, error=message, retry=self.__failure_retry(previous))
            return
        old_identity = previous.get("identity")
        identity = {"type": mediainfo.type.value, "tmdbid": str(mediainfo.tmdb_id or "0")}
        if old_identity and old_identity != identity:
            payload.update(status=Status.PROCESS_FAILED.value, error="识别结果与历史作品不一致，请检查标题和历史记录",
                           retry=self.__failure_retry(previous))
            return
        rating = mediainfo.vote_average
        display_rating = rating if isinstance(rating, (int, float)) and math.isfinite(rating) else None
        payload.update(identity=identity, type=mediainfo.type.value, year=mediainfo.year,
                       poster=mediainfo.get_poster_image(), overview=mediainfo.overview,
                       tmdbid=str(mediainfo.tmdb_id or "0"), vote=display_rating)
        payload.pop("retry", None)
        payload.pop("error", None)
        subscription_type = context.get("subscription_type")
        if ((self._is_only_movies or subscription_type == "movies") and mediainfo.type == MediaType.TV
                or subscription_type == "tv" and mediainfo.type == MediaType.MOVIE):
            payload.update(status="类型不符合", summary="按当前类型设置跳过")
            return
        paths = context.get("customize_save_paths") or {}
        is_tv = mediainfo.type == MediaType.TV
        save_path = paths.get("tv" if is_tv else "movie")
        if is_tv and 16 in (mediainfo.genre_ids or []):
            save_path = paths.get("anime") or save_path
        if self._is_seasons_all and is_tv:
            seasons = sorted(season for season in seasons_info if season > 0)
        else:
            seasons = [(meta.begin_season if meta.begin_season is not None else 1) if is_tv else None]
        # 有结果后冻结已尝试的季度集合，重试不会扩大本次订阅范围。
        results = payload.setdefault("season_results", {})
        if results:
            seasons = [result["season"] for result in results.values()]
        else:
            if not seasons:
                payload.update(status=Status.SEASON_UNKNOWN.value, error="MP尚未提供正式季度信息，稍后重试",
                               retry=self.__failure_retry(previous))
                return
            for season in seasons:
                results[str(season) if season is not None else "movie"] = {
                    "season": season, "status": Status.SUBSCRIPTION_FAILED.value,
                    "error": "尚未处理", "retry": {"attempts": 0, "next_retry_at": 0},
                }
        for season in seasons:
            if self._event.is_set():
                break
            key = str(season) if season is not None else "movie"
            old_result = deepcopy(results[key])
            if old_result.get("status") in self._successful_statuses:
                continue
            if not previous.get("manual_retry") and not self.__retry_due(old_result, time.time()):
                continue
            try:
                target = self.__target_key(identity, season)
                if target in self._successful_targets:
                    status, message = Status.SUBSCRIPTION_EXISTS, "相同媒体和季度已有成功处理记录"
                elif is_tv and not seasons_info.get(season, {}).get("episodes"):
                    status, message = Status.SEASON_UNKNOWN, "MP尚未提供该季度的有效集数，稍后重试"
                else:
                    status, message = self.__checke_and_add_subscribe(meta, mediainfo, season, save_path)
            except Exception as error:
                logger.error(f"{mediainfo.title_year} 季度 {season} 添加订阅异常: {error}")
                status, message = Status.SUBSCRIPTION_FAILED, f"添加订阅异常：{type(error).__name__}"
            result = {"season": season, "status": status.value, "error": message}
            if status.value in self._retryable_statuses:
                result["retry"] = self.__failure_retry(old_result)
            results[key] = result
            self.__summarize(payload)
            self.__save_history_item(history, history_by_key, payload)
        self.__summarize(payload)

    @staticmethod
    def __season_catalog(mediainfo):
        """采用 MP V2 的实际季度元数据，保留零集季度以便后续单独重试。"""
        catalog = {}
        for item in getattr(mediainfo, "season_info", None) or []:
            season = item.get("season_number")
            if isinstance(season, bool) or not str(season).isdigit():
                continue
            count = item.get("episode_count")
            catalog[int(season)] = {
                "episodes": count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 0,
                "year": str(item.get("air_date") or "")[:4],
            }
        for season, episodes in (getattr(mediainfo, "seasons", None) or {}).items():
            if isinstance(season, bool) or not str(season).isdigit():
                continue
            entry = catalog.setdefault(int(season), {"episodes": 0, "year": ""})
            if isinstance(episodes, (list, tuple)) and episodes:
                entry["episodes"] = len(episodes)
        for season, year in (getattr(mediainfo, "season_years", None) or {}).items():
            if not isinstance(season, bool) and str(season).isdigit() and int(season) in catalog:
                catalog[int(season)]["year"] = str(year or "")
        return catalog

    @classmethod
    def __recognition_problem(cls, rss_info, meta, inferred_type, mediainfo, seasons_info):
        if mediainfo.type not in (MediaType.MOVIE, MediaType.TV):
            return Status.IDENTITY_MISMATCH, "MP未返回明确的电影或电视剧类型"
        if not cls.__positive_id(mediainfo.tmdb_id):
            return Status.IDENTITY_MISMATCH, "MP未返回有效 TMDB ID，暂缓订阅"
        if inferred_type and inferred_type != mediainfo.type:
            return Status.IDENTITY_MISMATCH, f"RSS指定{inferred_type.value}，MP识别为{mediainfo.type.value}，请检查来源类型或标题"
        if meta.begin_season is not None:
            if mediainfo.type != MediaType.TV:
                return Status.IDENTITY_MISMATCH, "标题包含明确季号，但MP识别为电影"
            if meta.begin_season not in seasons_info:
                return Status.SEASON_UNKNOWN, f"MP元数据中尚无第 {meta.begin_season} 季，暂缓订阅"
        rss_year = str(rss_info.get("year") or "")
        media_year = str(mediainfo.year or "")
        if re.fullmatch(r"\d{4}", rss_year) and re.fullmatch(r"\d{4}", media_year):
            years = {media_year}
            if mediainfo.type == MediaType.TV:
                season = meta.begin_season if meta.begin_season is not None else 1
                if seasons_info.get(season, {}).get("year"):
                    years.add(seasons_info[season]["year"])
            if rss_year not in years:
                return Status.IDENTITY_MISMATCH, f"RSS年份 {rss_year} 与MP作品/目标季度年份 {'、'.join(sorted(years))} 不一致，请核对后重新处理"
        return None

    @classmethod
    def __summarize(cls, payload):
        results = list(payload.get("season_results", {}).values())
        if not results:
            return
        statuses = [result["status"] for result in results]
        success = sum(status in cls._successful_statuses for status in statuses)
        failed = sum(status in cls._retryable_statuses for status in statuses)
        skipped = len(results) - success - failed
        if success == len(results):
            status = (Status.SUBSCRIPTION_ADDED.value if Status.SUBSCRIPTION_ADDED.value in statuses
                      else Status.SUBSCRIPTION_EXISTS.value)
        elif success:
            status = Status.PARTIAL_SUCCESS.value
        elif len(set(statuses)) == 1:
            status = statuses[0]
        else:
            status = Status.SUBSCRIPTION_FAILED.value
        unit = "季" if payload.get("type") == MediaType.TV.value else "项"
        payload.update(status=status, summary=f"成功 {success} {unit}，失败 {failed} {unit}，跳过 {skipped} {unit}")

    def __checke_and_add_subscribe(
        self,
        meta: MetaBase,
        mediainfo: MediaInfo,
        season: int | None,
        save_path,
    ) -> Tuple[Status, str]:
        if save_path:
            logger.info(
                f"{mediainfo.title_year} 的自定义保存路径为: {save_path}"
            )

        # 判断上映年份是否符合要求
        if self._release_year and not re.fullmatch(r"\d{4}", str(mediainfo.year or "")):
            return Status.YEAR_UNKNOWN, "MP识别结果缺少有效年份，暂不判断年份条件"
        if self._release_year and int(mediainfo.year) < int(self._release_year):
            logger.info(
                f"{mediainfo.title_year} 上映年份: {mediainfo.year}, 不符合要求"
            )
            return Status.YEAR_NOT_MATCH, "上映年份低于设定值"
        # 判断评分是否符合要求
        rating = mediainfo.vote_average
        if self._vote and (isinstance(rating, bool) or not isinstance(rating, (int, float))
                           or not math.isfinite(rating) or not 0 < rating <= 10):
            return Status.RATING_UNKNOWN, "MP识别结果缺少有效评分，暂不判断评分条件"
        if self._vote and rating < self._vote:
            logger.info(
                f"{mediainfo.title_year} 评分: {mediainfo.vote_average}, 不符合要求"
            )
            return Status.RATING_NOT_MATCH, "评分低于设定值"

        # 与 MP V2 add() 的默认季度保持一致，并按本次目标季去重。
        # 全季循环不能复用 RSS 原始季度，也不能修改后续历史所用的 meta。
        if mediainfo.type == MediaType.TV:
            season = season if season is not None else 1
        else:
            season = None
        subscribe_meta = copy(meta)
        subscribe_meta.type = mediainfo.type
        subscribe_meta.begin_season = season
        if self.subscribechain.exists(mediainfo=mediainfo, meta=subscribe_meta):
            logger.info(f"{mediainfo.title_year} 订阅已存在")
            return Status.SUBSCRIPTION_EXISTS, ""

        # MP V2：best_version=1 开启洗版，best_version_full=0 使用分集洗版。
        # 两个字段显式传整数，避免继承全集洗版默认值及数据库布尔类型问题。
        subscribe_id, message = self.subscribechain.add(
            title=mediainfo.title,
            year=mediainfo.year,
            mtype=mediainfo.type,
            tmdbid=mediainfo.tmdb_id,
            season=season,
            exist_ok=True,
            username=self.plugin_name,
            save_path=save_path,
            best_version=1,
            best_version_full=0,
        )
        if not subscribe_id:
            logger.error(f"{mediainfo.title_year} 添加洗版订阅失败: {message}")
            return Status.SUBSCRIPTION_FAILED, str(message or "MP未返回订阅ID")
        if season is not None:
            logger.info(f"已添加分集洗版订阅: {mediainfo.title_year} 第 {season} 季")
        else:
            logger.info(f"已添加洗版订阅: {mediainfo.title_year}")
        return Status.SUBSCRIPTION_ADDED, ""

    def __request_rss(self, addr):
        # RequestUtils 不传 Session 时没有内部重试，最多三次、每次超时 20 秒。
        client = RequestUtils(timeout=20, proxies=settings.PROXY or {}) if self._proxy else RequestUtils(timeout=20)
        for attempt in range(3):
            if self._event.is_set():
                return None
            retry_after = None
            try:
                response = client.get_res(addr, raise_exception=True)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError):
                response = None
            except requests.exceptions.RequestException as error:
                logger.warn(f"RSS请求无法完成：{type(error).__name__}")
                return None
            if response is not None:
                status = response.status_code
                if 200 <= status < 300:
                    return response
                retry_after = response.headers.get("Retry-After")
                response.close()
                if status not in (408, 429) and not 500 <= status < 600:
                    logger.warn(f"RSS返回 HTTP {status}，本轮不重试")
                    return None
                logger.warn(f"RSS返回 HTTP {status}，第 {attempt + 1}/3 次请求失败")
            if attempt == 2:
                break
            delay = (2, 5)[attempt]
            if retry_after:
                # 长限流或日期形式留待下次刷新，避免提前重试和长时间占用任务。
                if not str(retry_after).isdigit() or int(retry_after) > 30:
                    logger.warn("RSS要求稍后再试，结束本轮请求，等待下次刷新")
                    return None
                delay = max(delay, int(retry_after))
            if self._event.wait(delay):
                return None
        logger.warn("RSS连续三次请求失败，等待下次刷新")
        return None

    @classmethod
    def __douban_subject_id(cls, link):
        try:
            url = urlsplit(str(link or ""))
            if url.scheme not in ("http", "https") or url.hostname not in ("movie.douban.com", "www.douban.com", "douban.com"):
                return None
            match = re.fullmatch(r"/(?:subject|doubanapp/dispatch/(?:movie|tv))/([0-9]+)/?", url.path)
            return cls.__positive_id(match[1]) if match else None
        except ValueError:
            return None

    def __get_rss_info(self, addr) -> List[RssInfo]:
        """
        获取RSS
        """
        try:
            ret = self.__request_rss(addr)
            if ret is None:
                return []
            try:
                ret_xml = ret.text
            finally:
                ret.close()
            ret_array: List[RssInfo] = []

            # 解析XML
            dom_tree = xml.dom.minidom.parseString(ret_xml)
            rootNode = dom_tree.documentElement
            if rootNode is None:
                return []
            items = rootNode.getElementsByTagName("item")
            for item in items:
                try:
                    # 标题
                    title = DomUtils.tag_value(item, "title", default="")
                    # 链接
                    link = DomUtils.tag_value(item, "link", default="")
                    if not title and not link:
                        logger.warn("条目标题和链接均为空，无法处理")
                        continue

                    # 豆瓣ID
                    doubanid = self.__douban_subject_id(link)

                    # 年份
                    year = DomUtils.tag_value(item, "year", default="")
                    if not year:
                        # 年份
                        description = DomUtils.tag_value(
                            item, "description", default=""
                        )
                        # 删除 '评价数' 到第一个 '<br>' 之间的字符串
                        description = re.sub(
                            r"评价数.*?<br>", "", str(description) or ""
                        )
                        # 删除所有 <img> 标签及其内容
                        description = re.sub(r"<img.*?>", "", description)
                        # 匹配4位独立数字1900-2099年
                        found_year = re.findall(
                            r"\b(19\d{2}|20\d{2})\b", description
                        )
                        year = found_year[0] if found_year else None

                    # 类型
                    mtype = DomUtils.tag_value(item, "type", default="")

                    rss_info: RssInfo = {
                        "title": str(title),
                        "link": str(link),
                        "mtype": str(mtype),
                        "year": str(year) if year else None,
                        "doubanid": str(doubanid) if doubanid else None,
                    }
                    # 返回对象
                    ret_array.append(rss_info)

                except Exception as e1:
                    logger.error("解析RSS条目失败：" + str(e1))
                    continue
            return ret_array
        except Exception as e:
            logger.error("获取RSS失败：" + str(e))
            return []


    @staticmethod
    def __parse_rss_config(config_line: str) -> tuple[str, str | None]:
        """
        解析RSS配置行,支持URL@@TYPE格式
        
        格式:
        - URL@@TV   -> (URL, 'TV')
        - URL@@Movie -> (URL, 'Movie')
        - URL       -> (URL, None)  # 向后兼容
        
        示例:
        - http://rss@@TV -> ('http://rss', 'TV')
        - http://rss     -> ('http://rss', None)
        """
        if not config_line:
            return '', None
        
        config_line = config_line.strip()
        
        # 检查是否包含@@分隔符
        if '@@' in config_line:
            parts = config_line.split('@@', 1)
            url = parts[0].strip()
            rss_type = parts[1].strip() if len(parts) > 1 else None
            return url, rss_type
        else:
            # 向后兼容,无类型标记
            return config_line, None

    @staticmethod
    def __get_info_addr(
        addr: str,
    ) -> Dict[str, Dict[str, str] | str | None]:
        # ) -> Dict[str, Dict[str, str] | str | None]:
        subscription_type = None

        # 提取分号分割的链接和保存地址
        if ";" not in addr:
            return {
                "addr": addr,
                "customize_save_paths": None,
                "subscription_type": None,
            }
        else:
            logger.debug("分割订阅地址")
            str_list: List[str] = addr.split(";")
            addr = str_list[0]
            customize_save_info = str_list[1] if len(str_list) > 1 else ""
            if len(str_list) > 2:
                subscription_type = str_list[2]

            logger.debug(f"addr: {addr}")
            logger.debug(f"customize_save_info: {customize_save_info}")
            logger.debug(f"subscription_type: {subscription_type}")

            if "#" in customize_save_info:
                customize_save_info_list = customize_save_info.split("#")

                logger.debug(
                    f"customize_save_info_list: {customize_save_info_list}"
                )

                customize_save_path_movie = customize_save_info_list[0]
                customize_save_path_tv = customize_save_info_list[1]
                customize_save_path_anime = (
                    customize_save_info_list[2]
                    if len(customize_save_info_list) > 2
                    else customize_save_path_tv
                )

                logger.debug(
                    f"订阅链接 {addr} 的自定义保存路径为: "
                    f"电影:{customize_save_path_movie}, "
                    f"电视剧: {customize_save_path_tv}, "
                    f"动漫: {customize_save_path_anime}"
                )

            else:
                customize_save_path_movie = customize_save_info
                customize_save_path_tv = customize_save_info
                customize_save_path_anime = customize_save_info

                logger.debug(
                    f"订阅链接 {addr} 的自定义保存路径为: {customize_save_info}"
                )

            if (
                subscription_type
                and subscription_type.startswith("@")
                and subscription_type.endswith("@")
            ):
                subscription_type = subscription_type.strip("@")
                logger.info(
                    f"订阅链接 {addr} 的订阅类型为: {subscription_type}"
                )

            customize_save_paths = {
                "movie": customize_save_path_movie,
                "tv": customize_save_path_tv,
                "anime": customize_save_path_anime,
            }
            return {
                "addr": addr,
                "customize_save_paths": customize_save_paths,
                "subscription_type": subscription_type,
            }

    @staticmethod
    def __clean_title(title: str, mtype=None) -> str:
        """仅整理空白；明确季号交给 MP 解析，数字片名不推断成续季。"""
        return " ".join(title.split()) if title else title

    @staticmethod
    def __get_history_unrecognized_payload(
        title: str,
        unique: str,
        year: str | None = None,
        doubanid: str | None = None,
    ) -> HistoryPayload:
        """
        获取历史记录
        """
        history_payload: HistoryPayload = {
            "title": title,
            "unique": unique,
            "status": Status.UNRECOGNIZED.value,
            "type": MediaType.UNKNOWN.value,
            "year": year or "0",
            "poster": "/assets/no-image-CweBJ8Ee.jpeg",
            "overview": "",
            "tmdbid": "0",
            "doubanid": doubanid or "0",
            "time": datetime.datetime.now(
                tz=pytz.timezone(settings.TZ)
            ).strftime("%m-%d %H:%M"),
            "time_full": datetime.datetime.now(
                tz=pytz.timezone(settings.TZ)
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "vote": 0.0,
        }
        return history_payload

    def __get_tmdbinfo_by_doubanid(
        self, doubanid: str, mtype: MediaType | None = None
    ) -> Tuple[dict[str, Any] | None, bool]:
        """
        根据豆瓣ID获取TMDB信息
        """
        doubaninfo, is_ip_rate_limit = self.__douban_info(
            doubanid=doubanid, mtype=mtype
        )
        if is_ip_rate_limit or not doubaninfo:
            return None, is_ip_rate_limit

        # 优先使用title匹配, original_title无法识别到季数
        title = doubaninfo.get("title", "")
        original_title = doubaninfo.get("original_title", "")
        # meta = MetaInfo(title=original_title if original_title else title)
        meta = MetaInfo(title=title if title else original_title)

        logger.debug(f"MetaInfo meta from original_title or title:::{meta}")

        # 年份
        meta.year = doubaninfo.get("year")

        # 处理类型
        media_type = doubaninfo.get("media_type")
        media_type = (
            media_type
            if isinstance(media_type, MediaType)
            else (
                MediaType.MOVIE
                if doubaninfo.get("type") == "movie"
                else MediaType.TV
            )
        )
        meta.type = media_type

        # 匹配TMDB信息
        if original_title:
            meta_names = list(
                dict.fromkeys(
                    [original_title, title, meta.cn_name, meta.en_name]
                )
            )
        else:
            meta_names = list(
                dict.fromkeys([title, meta.cn_name, meta.en_name])
            )

        # 移除空值
        meta_names = [name for name in meta_names if name]

        __mtype = mtype if mtype and mtype != MediaType.UNKNOWN else meta.type
        __begin_season = meta.begin_season if meta.begin_season else None
        __is_match_season_from_name = False

        for name in meta_names:
            if __is_match_season_from_name:
                # 如果已经从名字匹配到季数，则直接修正名字
                name = re.sub(
                    r"\d+$", "", name
                ).strip()  # 将匹配到的数字从 name 中移除，并去掉多余的空格
            elif __mtype == MediaType.TV and not __begin_season:
                # 如果季为空且是电视剧，则匹配获取 name 以数字结束的内容作为季数
                __matchSeason = re.search(r"\d+$", name)
                if __matchSeason:
                    __begin_season = int(
                        __matchSeason.group()
                    )  # 提取匹配内容并转换为 int
                    name = re.sub(
                        r"\d+$", "", name
                    ).strip()  # 将匹配到的数字从 name 中移除，并去掉多余的空格
                    __is_match_season_from_name = True
                    logger.debug("从名字匹配到季数：%s", __begin_season)

            logger.debug(f"match_tmdbinfo name:::{name}")
            logger.debug(f"match_tmdbinfo mtype:::{__mtype}")
            logger.debug(f"match_tmdbinfo meta.year:::{meta.year}")
            logger.debug(f"match_tmdbinfo begin_season:::{__begin_season}")
            tmdbinfo = self.mediachain.match_tmdbinfo(
                name=name,
                year=meta.year,
                mtype=__mtype,
                season=__begin_season,
            )
            # logger.debug(f"tmdbinfo:::{tmdbinfo}")

            if tmdbinfo:
                # 合季季后返回
                tmdbinfo["season"] = meta.begin_season
                return tmdbinfo, is_ip_rate_limit

        return None, is_ip_rate_limit

    def __douban_info(
        self, doubanid: str, mtype: MediaType | None = None
    ) -> Tuple[dict[str, Any] | None, bool]:
        """
        获取豆瓣信息
        :param doubanid: 豆瓣ID
        :param mtype:    媒体类型
        :return: 豆瓣信息
        """
        """
        豆瓣IP速率限制错误信息
        {'msg': 'subject_ip_rate_limit','code': 1309, 'request': 'GET /v2/movie/30483637','localized_message': '您所在的网络存在异常，请登录后重试。'}
        """

        def __douban_tv() -> Tuple[dict[str, Any] | None, bool]:
            """
            获取豆瓣剧集信息
            """
            info = self.doubanapi.tv_detail(doubanid)
            if info:
                if "subject_ip_rate_limit" in info.get("msg", ""):
                    logger.warn(f"触发豆瓣IP速率限制，错误信息：{info} ...")
                    return None, True
            return info, False

        def __douban_movie() -> Tuple[dict[str, Any] | None, bool]:
            """
            获取豆瓣电影信息
            """
            info = self.doubanapi.movie_detail(doubanid)
            if info:
                if "subject_ip_rate_limit" in info.get("msg", ""):
                    logger.warn(f"触发豆瓣IP速率限制，错误信息：{info} ...")
                    return None, True
            return info, False

        if not doubanid:
            return None, False
        logger.info(f"开始获取豆瓣信息：{doubanid} ...")
        if mtype == MediaType.TV:
            return __douban_tv()
        else:
            movie_info, is_ip_rate_limit = __douban_movie()
            if not movie_info and not is_ip_rate_limit:
                logger.debug("未从电影类型获取到信息，返回从剧集获取信息")
                return __douban_tv()
            else:
                return movie_info, is_ip_rate_limit

    def __get_migrate_info(self, migrate_url: str):
        """
        从原MP API URL获取信息
        """
        logger.info("开始从原MP获取插件数据")

        try:
            res = RequestUtils(headers={"X-API-KEY": self._migrate_api_token}).request(method="get", url=migrate_url)
            if not res:
                logger.error(
                    "没有获取到原MP信息，检查原MP地址和API Token是否正确，检查浏览器打开【请求URL】查看是能获取到数据"
                )
                if self._migrate_once:
                    logger.error(
                        f"{self._msg_migrate_install}。{self._msg_install}"
                    )
                return None
            res.raise_for_status()  # 检查响应状态码，如果不是 2xx，会抛出 HTTPError 异常
            resData = res.json()

            if isinstance(resData, dict):
                if resData.get("success", "") is False:
                    logger.error(
                        f"获取原MP信息失败：{resData.get('message', '')}"
                    )
                    return None

                if resData.get("detail", "") == "Not Found":
                    logger.error("请检查【请求URL】是否能获取到数据")
                    if self._migrate_once:
                        logger.error(
                            f"{self._msg_migrate_install}。{self._msg_install}"
                        )
                    return None

            if isinstance(resData, list) and len(resData) == 0:
                logger.info(f"没有需要添加的迁移信息：{resData}")
                return None

            return resData
        except requests.exceptions.RequestException as err:
            logger.error(f"迁移请求失败：{type(err).__name__}")
        return None

    def __get_migrate_plugin_api_url(self, endpoint: str) -> str:
        """
        获取插件API URL
        """
        # 旧版迁移接口仍校验此参数；宿主认证另外经 X-API-KEY 请求头传递。
        query = urlencode({"migrate_api_token": self._migrate_api_token})
        return f"{self._migrate_from_url.rstrip('/')}/api/v1/plugin/{self._plugin_id}/{endpoint}?{query}"

    def __get_migrate_history(self):
        """
        获取所有迁移历史记录
        """
        url = self.__get_migrate_plugin_api_url("migrate-history")
        return self.__get_migrate_info(url)

    def __get_migrate_config(self):
        """
        获取所有迁移配置
        """
        url = self.__get_migrate_plugin_api_url("migrate-config")
        return self.__get_migrate_info(url)
