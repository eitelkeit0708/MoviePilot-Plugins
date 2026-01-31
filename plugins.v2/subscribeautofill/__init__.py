import json
import re
import time
from typing import Any, List, Dict, Tuple, Optional

from app.core.event import eventmanager, Event
from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.site_oper import SiteOper
from app.db.subscribe_oper import SubscribeOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, SystemConfigKey


# 默认站点-官组映射
DEFAULT_SITE_GROUP_MAPPINGS = """馒头:MWeb|MTeam|TPTV
观众:ADE|ADWeb|Audies
憨憨:HHWEB
彩虹岛:CHDWEB|CHDBits|CHDTV|CHDHKTV|SGNB
我堡:OurTV|OurBits
UBits:UBWEB|UBits|UBTV
高清杜比:Dream|DBTV|QHstudIo
HDSWEB:HDSWEB
FRDS:FRDS"""

# 默认源正则
DEFAULT_SOURCE_PATTERNS = """CR|Crunchyroll
Netflix|NF
friDay|Friday
AMZN|Amazon
B-Global|BG
IQ|iqiyi
Baha
LINETV
Disney\\+?|DSNP
HBO|HMAX
Hulu
Paramount\\+?
AppleTV\\+?|ATVP"""


class SubscribeAutofill(_PluginBase):
    # 插件名称
    plugin_name = "订阅自动填充"
    # 插件描述
    plugin_desc = "电视剧下载后自动拆分媒体信息填充到订阅，支持站点-官组智能匹配。"
    # 插件图标
    plugin_icon = "teamwork.png"
    # 插件版本
    plugin_version = "1.5"
    # 插件作者
    plugin_author = "Eitelkeit"
    # 作者主页
    author_url = "https://github.com/eitelkeit0708/MoviePilot-Plugins"
    # 插件配置项ID前缀
    plugin_config_prefix = "subscribeautofill_"
    # 加载顺序
    plugin_order = 26
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled: bool = False
    _clear = False
    _clear_handle = False
    _update_details = []
    _site_group_mappings = ""
    _source_patterns = ""
    _parsed_site_mappings = {}
    _parsed_sources = []
    _subscribeoper = None
    _downloadhistoryoper = None
    _siteoper = None

    def init_plugin(self, config: dict = None):
        self._downloadhistoryoper = DownloadHistoryOper()
        self._subscribeoper = SubscribeOper()
        self._siteoper = SiteOper()

        if config:
            self._enabled = config.get("enabled")
            self._clear = config.get("clear")
            self._clear_handle = config.get("clear_handle")
            self._update_details = config.get("update_details") or []

            # 解析站点-官组映射
            self._site_group_mappings = config.get("site_group_mappings") or DEFAULT_SITE_GROUP_MAPPINGS
            self._parsed_site_mappings = {}
            for line in self._site_group_mappings.strip().split('\n'):
                line = line.strip()
                if ':' in line:
                    parts = line.split(':', 1)
                    site_name = parts[0].strip()
                    group_pattern = parts[1].strip()
                    if site_name and group_pattern:
                        self._parsed_site_mappings[site_name] = group_pattern
            logger.info(f"解析到 {len(self._parsed_site_mappings)} 个站点-官组映射")

            # 解析源正则
            self._source_patterns = config.get("source_patterns") or DEFAULT_SOURCE_PATTERNS
            self._parsed_sources = [p.strip() for p in self._source_patterns.strip().split('\n') if p.strip()]
            logger.info(f"解析到 {len(self._parsed_sources)} 个源正则")

            # 清理已处理历史
            if self._clear_handle:
                self.del_data(key="history_handle")
                self._clear_handle = False
                self.__update_config()
                logger.info("已处理历史清理完成")

            # 清理历史记录
            if self._clear:
                self.del_data(key="history")
                self._clear = False
                self.__update_config()
                logger.info("历史记录清理完成")

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "clear": self._clear,
            "clear_handle": self._clear_handle,
            "update_details": self._update_details,
            "site_group_mappings": self._site_group_mappings,
            "source_patterns": self._source_patterns,
        })

    def __extract_visual_effects_from_title(self, title: str) -> List[str]:
        """从种子标题提取视觉特效元素，返回原始匹配字符串"""
        effects = []
        if not title:
            return effects

        # 视觉特效正则 - 按优先级排序，使用分组名标记类别避免重复
        visual_patterns = [
            # Dolby Vision 系列
            (r'Dolby[\s.]?Vision|DoVi|Dovi', 'DV'),
            (r'DV[\s.]?P\d', 'DV'),
            (r'(?<![A-Za-z])DV(?![A-Za-z])', 'DV'),
            
            # HDR 系列
            (r'HDR10\+', 'HDR10+'),
            (r'HDR10(?!\+)', 'HDR10'),
            (r'HDR[\s.]?Vivid|HDRVivid', 'HDRVivid'),
            (r'HLG', 'HLG'),
            (r'(?<![A-Za-z0-9])HDR(?![0-9A-Za-z])', 'HDR'),
            
            # 增强特性
            (r'IMAX[\s.]?Enhanced|IMAX', 'IMAX'),
            
            # 帧率
            (r'120[Ff]ps', 'fps'),
            (r'60[Ff]ps', 'fps'),
            (r'30[Ff]ps', 'fps'),
            (r'25[Ff]ps', 'fps'),
            (r'24[Ff]ps', 'fps'),
            
            # 常规
            (r'SDR', 'SDR'),
            
            # 高码
            (r'HQ|高码|EDR', 'HQ'),
            
            # 色深
            (r'12[\s.]?bit', 'bit'),
            (r'10[\s.]?bit', 'bit'),
            (r'8[\s.]?bit', 'bit'),
        ]

        matched_categories = set()
        for pattern, category in visual_patterns:
            if category not in matched_categories:
                match = re.search(pattern, title, re.IGNORECASE)
                if match:
                    # 返回原始匹配字符串
                    effects.append(match.group(0))
                    matched_categories.add(category)

        return effects

    def __extract_audio_effects_from_title(self, title: str) -> List[str]:
        """从种子标题提取音频特效元素，返回原始匹配字符串"""
        effects = []
        if not title:
            return effects

        # 音频特效正则 - 按优先级排序（复合格式优先）
        # 使用分组名标记类别避免重复匹配同类型
        audio_patterns = [
            # 次世代顶级音轨 - 复合格式优先（格式+声道+Atmos）
            (r'TrueHD[\s.]?[\d.]+[\s.]?Atmos', 'TrueHDAtmos'),      # TrueHD 7.1 Atmos
            (r'TrueHD[\s.]?Atmos', 'TrueHDAtmos'),                   # TrueHD Atmos
            (r'DTS[\s.]?X[\s.]?[\d.]+', 'DTSX'),                     # DTS:X 7.1
            (r'DTS[\s.]?X(?![A-Za-z])', 'DTSX'),                     # DTS:X
            (r'DDP[\s.]?[\d.]+[\s.]?Atmos', 'DDPAtmos'),             # DDP 5.1 Atmos, DDP 7.1 Atmos
            (r'E-AC3[\s.]?[\d.]+[\s.]?Atmos', 'DDPAtmos'),           # E-AC3 5.1 Atmos
            (r'DDP[\s.]?Atmos|E-AC3[\s.]?Atmos', 'DDPAtmos'),        # DDP Atmos
            (r'Dolby[\s.]?Atmos|(?<![A-Za-z])Atmos(?![\s.]?[\d])', 'Atmos'),  # 独立的 Atmos
            
            # 无损/高码率音轨 - 带声道格式优先
            (r'DTS-HD[\s.]?MA[\s.]?[\d.]+', 'DTSHDMA'),              # DTS-HD MA 5.1
            (r'DTS-HD[\s.]?MA', 'DTSHDMA'),                          # DTS-HD MA
            (r'DTS-HD[\s.]?HR[\s.]?[\d.]+', 'DTSHDHR'),              # DTS-HD HR 5.1
            (r'DTS-HD[\s.]?HR', 'DTSHDHR'),                          # DTS-HD HR
            (r'TrueHD[\s.]?[\d.]+', 'TrueHD'),                       # TrueHD 5.1/7.1
            (r'TrueHD', 'TrueHD'),                                    # TrueHD
            (r'LPCM[\s.]?[\d.]+', 'LPCM'),                           # LPCM 2.0
            (r'LPCM', 'LPCM'),                                        # LPCM
            (r'FLAC[\s.]?[\d.]+', 'FLAC'),                           # FLAC 2.0
            (r'FLAC', 'FLAC'),                                        # FLAC
            
            # 常用有损/流媒体音轨 - 带声道格式优先
            (r'DDP[\s.]?[\d.]+|E-AC3[\s.]?[\d.]+', 'DDP'),           # DDP 5.1, E-AC3 5.1
            (r'DDP|E-AC3|Dolby[\s.]?Digital[\s.]?Plus', 'DDP'),      # DDP, E-AC3
            (r'DD[\s.]?[\d.]+|AC3[\s.]?[\d.]+', 'DD'),               # DD 5.1, AC3 5.1
            (r'DD(?!P)|AC3|Dolby[\s.]?Digital(?![\s.]?Plus)', 'DD'), # DD, AC3
            (r'DTS[\s.]?[\d.]+', 'DTS'),                             # DTS 5.1
            (r'DTS(?![\s.-]?HD|[\s.]?X)', 'DTS'),                    # DTS (不匹配 DTS-HD, DTS:X)
            
            # 其他压缩格式
            (r'AAC[\s.]?[\d.]+', 'AAC'),                             # AAC 2.0
            (r'AAC', 'AAC'),                                          # AAC
            (r'Opus', 'Opus'),
            (r'MP3', 'MP3'),
            (r'VORBIS', 'Vorbis'),
        ]

        matched_categories = set()
        for pattern, category in audio_patterns:
            if category not in matched_categories:
                match = re.search(pattern, title, re.IGNORECASE)
                if match:
                    # 返回原始匹配字符串
                    effects.append(match.group(0))
                    matched_categories.add(category)

        return effects

    def __extract_group_from_title(self, title: str) -> str:
        """从种子标题提取制作组（保留@连接格式）"""
        if not title:
            return ""
        # 匹配末尾的制作组，支持@连接格式如 Nest@Audies, sh@CHDBits
        match = re.search(r'-([A-Za-z0-9]+(?:@[A-Za-z0-9]+)?)(?:\s*$|\.(?:mkv|mp4|avi|ts))', title, re.IGNORECASE)
        if match:
            return match.group(1)
        # 尝试匹配不带扩展名的情况
        match = re.search(r'-([A-Za-z0-9]+(?:@[A-Za-z0-9]+)?)\s*$', title)
        if match:
            return match.group(1)
        return ""

    def __extract_source_from_title(self, title: str) -> Optional[str]:
        """从种子标题提取源"""
        if not title:
            return None
        for source_pattern in self._parsed_sources:
            try:
                if re.search(source_pattern, title, re.IGNORECASE):
                    return source_pattern
            except re.error:
                logger.warning(f"无效的源正则表达式: {source_pattern}")
                continue
        return None

    def __get_site_by_group(self, resource_team: str, default_site: int) -> List[int]:
        """根据制作组匹配优先站点"""
        if not resource_team:
            return [default_site] if default_site else []

        active_sites = self._siteoper.list_active()

        for site_name, group_pattern in self._parsed_site_mappings.items():
            try:
                if re.search(group_pattern, resource_team, re.IGNORECASE):
                    # 找到匹配的站点
                    for site in active_sites:
                        if site.name == site_name:
                            logger.info(f"制作组 {resource_team} 匹配到站点 {site_name}")
                            return [site.id]
            except re.error:
                logger.warning(f"无效的官组正则表达式: {group_pattern}")
                continue

        # 无匹配，返回默认站点
        if default_site:
            logger.info(f"制作组 {resource_team} 无匹配站点，使用默认站点")
        return [default_site] if default_site else []

    def __parse_pix(self, resource_pix):
        """解析分辨率"""
        if not resource_pix:
            return None
        if re.match(r"1080[pi]|x1080", resource_pix, re.IGNORECASE):
            return "1080[pi]|x1080"
        if re.match(r"4K|2160p|x2160", resource_pix, re.IGNORECASE):
            return "4K|2160p|x2160"
        if re.match(r"720[pi]|x720", resource_pix, re.IGNORECASE):
            return "720[pi]|x720"
        return resource_pix

    def __parse_type(self, resource_type):
        """解析资源质量"""
        if not resource_type:
            return None
        if re.match(r"Blu-?Ray.+VC-?1|Blu-?Ray.+AVC|UHD.+blu-?ray.+HEVC|MiniBD", resource_type, re.IGNORECASE):
            return "Blu-?Ray.+VC-?1|Blu-?Ray.+AVC|UHD.+blu-?ray.+HEVC|MiniBD"
        if re.match(r"Remux", resource_type, re.IGNORECASE):
            return "Remux"
        if re.match(r"Blu-?Ray", resource_type, re.IGNORECASE):
            return "Blu-?Ray"
        if re.match(r"UHD|UltraHD", resource_type, re.IGNORECASE):
            return "UHD|UltraHD"
        if re.match(r"WEB-?DL|WEB-?RIP", resource_type, re.IGNORECASE):
            return "WEB-?DL|WEB-?RIP"
        if re.match(r"HDTV", resource_type, re.IGNORECASE):
            return "HDTV"
        if re.match(r"[Hx].?265|HEVC", resource_type, re.IGNORECASE):
            return "[Hx].?265|HEVC"
        if re.match(r"[Hx].?264|AVC", resource_type, re.IGNORECASE):
            return "[Hx].?264|AVC"
        return resource_type

    @eventmanager.register(EventType.DownloadAdded)
    def download_notice(self, event: Event = None):
        """
        添加下载填充订阅制作组等信息
        """
        if not event:
            logger.error("下载事件数据为空")
            return

        if not self._enabled:
            return

        if len(self._update_details) == 0:
            return

        if event:
            event_data = event.event_data
            if not event_data or not event_data.get("hash") or not event_data.get("context"):
                logger.error(f"下载事件数据不完整 {event_data}")
                return
            download_hash = event_data.get("hash")
            # 根据hash查询下载记录
            download_history = self._downloadhistoryoper.get_by_hash(download_hash)
            if not download_history:
                logger.warning(f"种子hash:{download_hash} 对应下载记录不存在")
                return

            history_handle: List[str] = self.get_data('history_handle') or []

            if f"{download_history.type}:{download_history.tmdbid}" in history_handle:
                logger.warning(f"下载历史:{download_history.title} 已处理过，不再重复处理")
                return

            if download_history.type != '电视剧':
                logger.warning(f"下载历史:{download_history.title} 不是电视剧，不进行官组填充")
                return

            # 根据下载历史查询订阅记录
            subscribes = self._subscribeoper.list_by_tmdbid(tmdbid=download_history.tmdbid,
                                                            season=int(download_history.seasons.replace('S', ''))
                                                            if download_history.seasons and
                                                               download_history.seasons.count('-') == 0 else None)
            if not subscribes or len(subscribes) == 0:
                logger.warning(f"下载历史:{download_history.title} tmdbid:{download_history.tmdbid} 对应订阅记录不存在")
                return

            logger.info(
                f"获取到tmdbid {download_history.tmdbid} season {int(download_history.seasons.replace('S', '')) if download_history.seasons and download_history.seasons.count('-') == 0 else None} 订阅记录:{len(subscribes)} 个")

            for subscribe in subscribes:
                if subscribe.type != '电视剧':
                    logger.warning(f"订阅记录:{subscribe.name} 不是电视剧，不进行官组填充")
                    continue

                # 开始填充官组和站点
                context = event_data.get("context")
                _torrent = context.torrent_info
                _meta = context.meta_info

                # 获取种子标题
                torrent_title = _torrent.title if _torrent else ""

                # 填充数据
                update_dict = {}
                skip_reasons = []  # 记录跳过原因

                # 分辨率
                if "分辨率" in self._update_details:
                    if subscribe.resolution:
                        skip_reasons.append(f"分辨率已存在:{subscribe.resolution}")
                    else:
                        resource_pix = _meta.resource_pix if _meta else None
                        if resource_pix:
                            resource_pix = self.__parse_pix(resource_pix)
                            if resource_pix:
                                update_dict['resolution'] = resource_pix
                            else:
                                skip_reasons.append("分辨率解析失败")
                        else:
                            skip_reasons.append("未获取到分辨率信息")

                # 资源质量
                if "资源质量" in self._update_details:
                    if subscribe.quality:
                        skip_reasons.append(f"资源质量已存在:{subscribe.quality}")
                    else:
                        resource_type = _meta.resource_type if _meta else None
                        if resource_type:
                            resource_type = self.__parse_type(resource_type)
                            if resource_type:
                                update_dict['quality'] = resource_type
                            else:
                                skip_reasons.append("资源质量解析失败")
                        else:
                            skip_reasons.append("未获取到资源质量信息")

                # 构建 include 正则表达式
                if subscribe.include:
                    skip_reasons.append(f"include已存在:{subscribe.include}")
                else:
                    include_parts = []

                    # 1. 视觉特效
                    if "视觉特效" in self._update_details:
                        visual_effects = self.__extract_visual_effects_from_title(torrent_title)
                        if visual_effects:
                            include_parts.extend(visual_effects)
                        else:
                            skip_reasons.append("未检测到视觉特效")

                    # 2. 音频特效
                    if "音频特效" in self._update_details:
                        audio_effects = self.__extract_audio_effects_from_title(torrent_title)
                        if audio_effects:
                            include_parts.extend(audio_effects)
                        else:
                            skip_reasons.append("未检测到音频特效")

                    # 3. 视频源
                    if "视频源" in self._update_details:
                        source = self.__extract_source_from_title(torrent_title)
                        if source:
                            include_parts.append(source)
                        else:
                            skip_reasons.append("未检测到视频源")

                    # 4. 制作组
                    if "制作组" in self._update_details:
                        resource_team = _meta.resource_team if _meta else None
                        if not resource_team:
                            resource_team = self.__extract_group_from_title(torrent_title)
                        if resource_team:
                            include_parts.append(resource_team)
                        else:
                            skip_reasons.append("未检测到制作组")

                    if include_parts:
                        # 使用正向先行断言要求同时包含所有元素
                        if len(include_parts) == 1:
                            update_dict['include'] = include_parts[0]
                        else:
                            # (?=.*元素1)(?=.*元素2).*元素N
                            lookaheads = ''.join([f'(?=.*{p})' for p in include_parts[:-1]])
                            update_dict['include'] = f"{lookaheads}.*{include_parts[-1]}"
                        logger.info(f"订阅记录:{subscribe.name} 生成include: {update_dict['include']}")

                # 站点
                if "站点" in self._update_details:
                    if subscribe.sites and len(subscribe.sites) > 0:
                        skip_reasons.append(f"站点已存在:{subscribe.sites}")
                    else:
                        rss_sites = self.systemconfig.get(SystemConfigKey.RssSites) or []
                        default_site = _torrent.site if _torrent and _torrent.site and int(_torrent.site) in rss_sites else None

                        # 获取制作组
                        resource_team = _meta.resource_team if _meta else None
                        if not resource_team:
                            resource_team = self.__extract_group_from_title(torrent_title)

                        # 根据制作组匹配站点
                        matched_sites = self.__get_site_by_group(resource_team, default_site)
                        if matched_sites:
                            update_dict['sites'] = matched_sites

                # 记录跳过原因
                if skip_reasons:
                    logger.info(f"订阅记录:{subscribe.name} 跳过填充: {', '.join(skip_reasons)}")

                if len(update_dict.keys()) == 0:
                    logger.info(f"订阅记录:{subscribe.name} 无需填充")
                    continue

                # 更新订阅记录
                self._subscribeoper.update(subscribe.id, update_dict)
                logger.info(f"订阅记录:{subscribe.name} 填充成功\n {update_dict}")

                # 读取历史记录
                history = self.get_data('history') or []
                history.append({
                    'name': subscribe.name,
                    'type': '种子下载自定义配置',
                    'content': json.dumps(update_dict),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
                })
                # 保存历史
                self.save_data(key="history", value=history)

                # 保存已处理历史
                history_handle.append(f"{download_history.type}:{download_history.tmdbid}")
                self.save_data('history_handle', history_handle)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'clear',
                                            'label': '清理历史记录',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'clear_handle',
                                            'label': '清理已处理记录',
                                        }
                                    }
                                ]
                            },
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True,
                                            'chips': True,
                                            'model': 'update_details',
                                            'label': '种子下载填充内容',
                                            'items': [
                                                {
                                                    "title": "资源质量",
                                                    "value": "资源质量"
                                                },
                                                {
                                                    "title": "分辨率",
                                                    "value": "分辨率"
                                                },
                                                {
                                                    "title": "视觉特效",
                                                    "value": "视觉特效"
                                                },
                                                {
                                                    "title": "音频特效",
                                                    "value": "音频特效"
                                                },
                                                {
                                                    "title": "视频源",
                                                    "value": "视频源"
                                                },
                                                {
                                                    "title": "制作组",
                                                    "value": "制作组"
                                                },
                                                {
                                                    "title": "站点",
                                                    "value": "站点"
                                                }
                                            ]
                                        }
                                    }
                                ]
                            },
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'site_group_mappings',
                                            'label': '站点-官组映射配置',
                                            'rows': 10,
                                            'placeholder': '馒头:MWeb|MTeam|TPTV\n'
                                                           '观众:ADE|ADWeb|Audies\n'
                                                           '憨憨:HHWEB\n'
                                                           '彩虹岛:CHDWEB|CHDBits|CHDTV|CHDHKTV|SGNB\n'
                                                           '我堡:OurTV|OurBits\n'
                                                           'UBits:UBWEB|UBits|UBTV\n'
                                                           '高清杜比:Dream|DBTV|QHstudIo'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'source_patterns',
                                            'label': '视频源正则配置',
                                            'rows': 10,
                                            'placeholder': 'CR|Crunchyroll\n'
                                                           'Netflix|NF\n'
                                                           'friDay|Friday\n'
                                                           'AMZN|Amazon\n'
                                                           'B-Global|BG\n'
                                                           'IQ|iqiyi\n'
                                                           'Baha\n'
                                                           'LINETV\n'
                                                           'Disney\\+?|DSNP\n'
                                                           'HBO|HMAX'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '填充内容说明：\n'
                                                    '• 视觉特效：DV、HDR、HDR10、HDR10+、HDRVivid、60fps、10bit、12bit等\n'
                                                    '• 音频特效：DTS-HD MA、TrueHD、Atmos、DDP、AAC、FLAC等\n'
                                                    '• 视频源：Netflix、CR、Amazon等流媒体平台\n'
                                                    '• 制作组：保留@连接格式（如Nest@Audies）\n'
                                                    '• 选中的内容将组合成正则表达式填充到订阅的include字段'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '站点-官组映射格式：站点名称:官组正则（多个用|分隔），每行一个。'
                                                    '制作组匹配到站点官组后，优先使用该站点订阅。站点名称需与MoviePilot中的站点名称一致。'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '视频源正则格式：每行一个正则表达式，用于匹配种子标题中的视频源信息（如Netflix、CR、Amazon等）。'
                                                    '匹配到的视频源会添加到include中。'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '电视剧订阅未配置包含关键词、订阅站点等配置时，订阅或搜索下载后，'
                                                    '将下载种子的制作组、站点等信息填充到订阅信息中，以保证后续订阅资源的统一性。'
                                                    '（订阅新出的电视剧效果更佳。）'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "clear": False,
            "clear_handle": False,
            "update_details": [],
            "site_group_mappings": DEFAULT_SITE_GROUP_MAPPINGS,
            "source_patterns": DEFAULT_SOURCE_PATTERNS,
        }

    def get_page(self) -> List[dict]:
        historys = self.get_data('history')
        if not historys:
            return [
                {
                    'component': 'div',
                    'text': '暂无数据',
                    'props': {
                        'class': 'text-center',
                    }
                }
            ]

        if not isinstance(historys, list):
            historys = [historys]

        # 按照时间倒序
        historys = sorted(historys, key=lambda x: x.get("time") or 0, reverse=True)

        contens = [
            {
                'component': 'tr',
                'props': {
                    'class': 'text-sm'
                },
                'content': [
                    {
                        'component': 'td',
                        'props': {
                            'class': 'whitespace-nowrap break-keep text-high-emphasis'
                        },
                        'text': history.get("time")
                    },
                    {
                        'component': 'td',
                        'text': history.get("name")
                    },
                    {
                        'component': 'td',
                        'text': history.get("type")
                    },
                    {
                        'component': 'td',
                        'text': history.get("content").encode('utf-8').decode('unicode_escape') if history.get(
                            "content") else ''
                    }
                ]
            } for history in historys
        ]

        # 拼装页面
        return [
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                        },
                        'content': [
                            {
                                'component': 'VTable',
                                'props': {
                                    'hover': True
                                },
                                'content': [
                                    {
                                        'component': 'thead',
                                        'content': [
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '执行时间'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '订阅名称'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '更新类型'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '更新内容'
                                            },
                                        ]
                                    },
                                    {
                                        'component': 'tbody',
                                        'content': contens
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

    def stop_service(self):
        """
        退出插件
        """
        pass