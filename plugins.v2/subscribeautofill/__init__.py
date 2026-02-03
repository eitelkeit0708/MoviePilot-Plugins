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
高清杜比:Dream|DBTV|QHstudIo"""

# 默认源正则
DEFAULT_SOURCE_PATTERNS = """CR|Crunchyroll
Netflix|NF
friDay|Friday
AMZN|Amazon
B-Global|BG
IQ|iqiyi
Baha
LINETV
Disney[\\s.]*\\+?|DSNP
HBO[\\s.]*Max|HBO|HMAX
Hulu
Paramount[\\s.]*\\+?
Apple[\\s.]*TV[\\s.]*\\+?|ATVP"""


class SubscribeAutofill(_PluginBase):
    # 插件名称
    plugin_name = "订阅自动填充"
    # 插件描述
    plugin_desc = "电视剧下载后自动拆分媒体信息填充到订阅，支持站点-官组智能匹配。"
    # 插件图标
    plugin_icon = "teamwork.png"
    # 插件版本
    plugin_version = "3.0"
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
    _override_mode = False  # 覆盖模式：覆盖现有include并清空特效字段
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
            self._override_mode = config.get("override_mode", False)
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
            "override_mode": self._override_mode,
            "update_details": self._update_details,
            "site_group_mappings": self._site_group_mappings,
            "source_patterns": self._source_patterns,
        })

    def __extract_visual_effects_from_title(self, title: str) -> List[str]:
        """从种子标题提取视觉特效元素，返回原始匹配字符串"""
        effects = []
        if not title:
            return effects

        # 视觉特效正则 - 按优先级排序
        # 使用分组名标记类别避免重复，DV和HDR是不同类别可同时匹配
        # 分隔符支持：空格、点、连字符、下划线
        visual_patterns = [
            # Dolby Vision 系列
            (r'\bDolby[\s.\-_]?Vision\b|\bDoVi\b|\bDovi\b', 'DV'),
            (r'\bDV[\s.\-_]?P\d\b', 'DV'),
            # DV使用更严格边界，避免匹配DVD等
            (r'(?<![A-Za-z])DV(?![A-Za-z0-9])', 'DV'),
            
            # HDR 系列 - 与 DV 是不同类别，可同时匹配 (DoVi HDR)
            # HDR10+/HDR10/HDRVivid/HLG/HDR 同属一个类别，只匹配第一个
            (r'\bHDR10[\s.\-_]*(?:\+|Plus)\b', 'HDR'),         # HDR10+ / HDR10Plus / HDR10 Plus
            (r'\bHDR10\b(?![\s.\-_]*(?:\+|Plus))', 'HDR'),     # HDR10 (不带+)
            (r'\bHDR[\s.\-_]?Vivid\b|\bHDRVivid\b', 'HDR'),
            (r'\bHLG\b', 'HDR'),
            (r'\bHDR\b', 'HDR'),
            
            # 增强特性
            (r'\bIMAX[\s.\-_]?Enhanced\b|\bIMAX\b', 'IMAX'),
            
            # 帧率
            (r'\b120[\s.\-_]?[Ff]ps\b', 'fps'),
            (r'\b60[\s.\-_]?[Ff]ps\b', 'fps'),
            (r'\b30[\s.\-_]?[Ff]ps\b', 'fps'),
            (r'\b25[\s.\-_]?[Ff]ps\b', 'fps'),
            (r'\b24[\s.\-_]?[Ff]ps\b', 'fps'),
            
            # 常规
            (r'\bSDR\b', 'SDR'),
            
            # 高码 - 使用更严格边界避免误匹配
            (r'(?<![A-Za-z])HQ(?![A-Za-z0-9])|高码|\bEDR\b', 'HQ'),
            
            # 色深 - 支持 10bit / 10-bit / 10.bit / 10 bit
            (r'\b12[\s.\-_]?bit\b', 'bit'),
            (r'\b10[\s.\-_]?bit\b', 'bit'),
            (r'\b8[\s.\-_]?bit\b', 'bit'),
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
        # 同系列格式归同一类别，只匹配第一个
        # 返回原始匹配字符串，确保 include 和标题一致
        
        # 声道匹配模式：可选的声道数 + 可选的 ch/channel 后缀
        # ch/channel 后必须跟非字母，避免匹配到 -CHDWEB 等
        ch = r'(?:[\s._-]*\d+(?:\.\d+)?(?:[\s._-]*(?:ch|channel)(?![a-z]))?)?'
        
        audio_patterns = [
            # --- TrueHD / Atmos ---
            (rf'\bTrueHD{ch}[\s._-]*Atmos\b', 'TrueHD'),
            (rf'\bTrueHD{ch}', 'TrueHD'),
            
            # --- DTS 系列 ---
            (rf'\bDTS[\s._-]*:?[\s._-]*X{ch}', 'DTSX'),
            (rf'\bDTS[\s._-]*HD[\s._-]*MA{ch}', 'DTSHDMA'),
            (rf'\bDTS[\s._-]*HD[\s._-]*HR{ch}', 'DTSHDHR'),
            (rf'\bDTS[\s._-]*HD{ch}', 'DTSHD'),
            (rf'\bDTS[\s._-]*ES{ch}', 'DTSES'),
            
            # --- Dolby Digital Plus (DDP/EAC3) ---
            (rf'\b(?:DDP|E-?AC-?3|DD\+){ch}[\s._-]*Atmos\b', 'DDP'),
            (rf'\b(?:DDP|E-?AC-?3|DD\+){ch}', 'DDP'),
            (r'\bDolby[\s._-]*Digital[\s._-]*Plus\b', 'DDP'),
            
            # --- Atmos (独立) ---
            (r'\b(?:Dolby[\s._-]*)?Atmos\b', 'Atmos'),
            
            # --- Dolby Digital (AC3) ---
            (rf'\b(?:DD|AC-?3|Dolby[\s._-]*Digital){ch}(?![\s._-]*\+|[\s._-]*Plus)', 'DD'),
            
            # --- DTS 基础 ---
            (rf'\bDTS{ch}(?![\s._-]*:?X|[\s._-]*HD|[\s._-]*ES)', 'DTS'),
            
            # --- 无损 / PCM ---
            (rf'\bL?PCM{ch}', 'LPCM'),
            (rf'\bFLAC{ch}', 'FLAC'),
            (rf'\bWAV{ch}', 'WAV'),
            
            # --- AAC ---
            (rf'\bHE[\s._-]*AAC{ch}', 'AAC'),
            (rf'\bAAC{ch}', 'AAC'),
            
            # --- 其他 ---
            (r'\bOpus\b', 'Opus'),
            (r'\bMP3\b', 'MP3'),
            (r'\bVORBIS\b', 'Vorbis'),
            (r'\bOGG\b', 'OGG'),
        ]

        matched_categories = set()
        matched_contents = []  # 记录已匹配的内容
        for pattern, category in audio_patterns:
            if category not in matched_categories:
                # 如果是独立 Atmos 类别，检查之前是否已包含 Atmos
                if category == 'Atmos':
                    already_has_atmos = any('atmos' in c.lower() for c in matched_contents)
                    if already_has_atmos:
                        continue
                match = re.search(pattern, title, re.IGNORECASE)
                if match:
                    # 返回原始匹配字符串
                    effects.append(match.group(0))
                    matched_contents.append(match.group(0))
                    matched_categories.add(category)

        return effects

    def __extract_group_from_title(self, title: str) -> str:
        """从种子标题提取制作组（保留@连接格式，排除*Audios音轨标记）"""
        if not title:
            return ""
        # 匹配末尾的制作组，支持@连接格式如 Nest@Audies, sh@CHDBits
        # 排除 *Audios/*Audio 这样的音轨数量标记
        match = re.search(r'-([A-Za-z0-9]+(?:@[A-Za-z0-9]+)?)(?:\s*$|\.(?:mkv|mp4|avi|ts))', title, re.IGNORECASE)
        if match:
            group = match.group(1)
            # 排除音轨数量标记（如 6Audios, 3Audio）
            if re.match(r'^\d+Audios?$', group, re.IGNORECASE):
                return ""
            return group
        # 尝试匹配不带扩展名的情况
        match = re.search(r'-([A-Za-z0-9]+(?:@[A-Za-z0-9]+)?)\s*$', title)
        if match:
            group = match.group(1)
            if re.match(r'^\d+Audios?$', group, re.IGNORECASE):
                return ""
            return group
        return ""

    def __extract_source_from_title(self, title: str) -> Optional[str]:
        """从种子标题提取源，返回原始匹配字符串"""
        if not title:
            return None
        for source_pattern in self._parsed_sources:
            try:
                match = re.search(source_pattern, title, re.IGNORECASE)
                if match:
                    # 返回原始匹配字符串，而不是正则模式
                    return match.group(0)
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
                
                # 调试日志：记录原始种子标题
                logger.debug(f"订阅记录:{subscribe.name} 处理种子标题: {torrent_title}")

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
                # 覆盖模式下，即使include已存在也重新生成
                should_build_include = not subscribe.include or self._override_mode
                if not should_build_include:
                    skip_reasons.append(f"include已存在:{subscribe.include}")
                else:
                    if self._override_mode and subscribe.include:
                        logger.info(f"订阅记录:{subscribe.name} 覆盖模式：将覆盖现有include:{subscribe.include}")
                    
                    include_parts = []

                    # 1. 视觉特效
                    if "视觉特效" in self._update_details:
                        visual_effects = self.__extract_visual_effects_from_title(torrent_title)
                        if visual_effects:
                            include_parts.extend(visual_effects)
                            logger.debug(f"订阅记录:{subscribe.name} 提取到视觉特效: {visual_effects}")
                        else:
                            skip_reasons.append("未检测到视觉特效")

                    # 2. 音频特效
                    if "音频特效" in self._update_details:
                        audio_effects = self.__extract_audio_effects_from_title(torrent_title)
                        if audio_effects:
                            include_parts.extend(audio_effects)
                            logger.debug(f"订阅记录:{subscribe.name} 提取到音频特效: {audio_effects}")
                        else:
                            skip_reasons.append("未检测到音频特效")

                    # 3. 视频源
                    if "视频源" in self._update_details:
                        source = self.__extract_source_from_title(torrent_title)
                        if source:
                            include_parts.append(source)
                            logger.debug(f"订阅记录:{subscribe.name} 提取到视频源: {source}")
                        else:
                            skip_reasons.append("未检测到视频源")

                    # 4. 制作组
                    if "制作组" in self._update_details:
                        resource_team = _meta.resource_team if _meta else None
                        if not resource_team:
                            resource_team = self.__extract_group_from_title(torrent_title)
                        if resource_team:
                            include_parts.append(resource_team)
                            logger.debug(f"订阅记录:{subscribe.name} 提取到制作组: {resource_team}")
                        else:
                            skip_reasons.append("未检测到制作组")

                    # 调试日志：记录所有include组成部分
                    if include_parts:
                        logger.debug(f"订阅记录:{subscribe.name} include组成部分: {include_parts}")
                        
                    if include_parts:
                        # 使用正向先行断言要求同时包含所有元素
                        if len(include_parts) == 1:
                            update_dict['include'] = include_parts[0]
                        else:
                            # (?=.*元素1)(?=.*元素2).*元素N
                            lookaheads = ''.join([f'(?=.*{p})' for p in include_parts[:-1]])
                            update_dict['include'] = f"{lookaheads}.*{include_parts[-1]}"
                        logger.info(f"订阅记录:{subscribe.name} 生成include: {update_dict['include']}")
                        
                        # 覆盖模式下，清空特效字段（effect）以避免冲突
                        if self._override_mode:
                            # 特效字段名为 effect（对应UI中的"特效"下拉框）
                            if hasattr(subscribe, 'effect') and subscribe.effect:
                                update_dict['effect'] = None
                                logger.info(f"订阅记录:{subscribe.name} 覆盖模式：清空effect字段:{subscribe.effect}")

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
                logger.info(f"订阅记录:{subscribe.name} 填充成功\\n {update_dict}")

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
                                            'model': 'override_mode',
                                            'label': '覆盖模式',
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
                                                    '• 选中的内容将组合成正则表达式填充到订阅的include字段\n\n'
                                                    '覆盖模式说明：\n'
                                                    '• 开启后，即使订阅已有include也会重新生成并覆盖\n'
                                                    '• 同时会清空订阅的特效字段（如DV、HDR等），统一使用include匹配'
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