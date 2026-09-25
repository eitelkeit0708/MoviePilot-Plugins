import copy
import json
import re
import threading
import time
from typing import Any, List, Dict, Tuple, Optional

from app.core.event import eventmanager, Event
from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.site_oper import SiteOper
from app.db.subscribe_oper import SubscribeOper
from app.helper.rule import RuleHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, SystemConfigKey


DEFAULT_SITE_GROUP_MAPPINGS = """馒头:MWeb|MTeam|TPTV
观众:ADE|ADWeb|Audies
憨憨:HHWEB
彩虹岛:CHDWEB|CHDBits|CHDTV|CHDHKTV|SGNB
我堡:OurTV|OurBits
UBits:UBWEB|UBits|UBTV
高清杜比:Dream|DBTV|QHstudIo"""

DEFAULT_SOURCE_PATTERNS = r"""\bCR\b|Crunchyroll
Netflix|\bNF\b
friDay|Friday
\bAMZN\b|Amazon
B-Global|\bBG\b
\bIQ\b|iqiyi
Baha
LINETV
Disney[\s.]*\+?|\bDSNP\b
HBO[\s.]*Max|\bHBO\b|\bHMAX\b
Hulu
Paramount[\s.]*\+?
Apple[\s.]*TV[\s.]*\+?|\bATVP\b"""


class SubscribeAutofill(_PluginBase):
    plugin_name = "订阅自动填充"
    plugin_desc = "下载后填充同组同站点，默认尊重订阅优先级规则，避免锁死画质和音轨。"
    plugin_icon = "teamwork.png"
    plugin_version = "3.18"
    plugin_author = "Eitelkeit"
    author_url = "https://github.com/eitelkeit0708/MoviePilot-Plugins"
    plugin_config_prefix = "subscribeautofill_"
    plugin_order = 26
    auth_level = 2
    _enabled = False
    _respect_rules = True
    _override_mode = False
    _update_details = []
    _lock = threading.RLock()

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._downloadhistoryoper = DownloadHistoryOper()
        self._subscribeoper = SubscribeOper()
        self._siteoper = SiteOper()
        self._rulehelper = RuleHelper()
        self._enabled = bool(config.get("enabled", False))
        self._respect_rules = bool(config.get("respect_rules", True))
        self._override_mode = bool(config.get("override_mode", False))
        self._clear = bool(config.get("clear", False))
        self._clear_handle = bool(config.get("clear_handle", False))
        self._update_details = config.get("update_details") or []
        self._site_group_mappings = config.get("site_group_mappings") or DEFAULT_SITE_GROUP_MAPPINGS
        self._source_patterns = config.get("source_patterns") or DEFAULT_SOURCE_PATTERNS
        self._parsed_site_mappings = {}
        for line in self._site_group_mappings.splitlines():
            if ':' not in line:
                continue
            name, pattern = (p.strip() for p in line.split(':', 1))
            if name and pattern:
                try:
                    re.compile(pattern, re.I)
                    self._parsed_site_mappings[name] = pattern
                except re.error:
                    logger.warning(f"无效的官组正则，已跳过站点：{name}")
        self._parsed_sources = []
        for line in self._source_patterns.splitlines():
            if not line.strip():
                continue
            try:
                self._parsed_sources.append(self.__normalize_source_pattern(line.strip()))
            except re.error:
                logger.warning("无效的视频源正则，已跳过该配置行")
        if self._clear_handle:
            self.del_data(key="history_handle")
            self._clear_handle = False
            self.__update_config()
        if self._clear:
            self.del_data(key="history")
            self._clear = False
            self.__update_config()
        logger.info(f"订阅自动填充：尊重优先级规则={self._respect_rules}，"
                    f"站点映射={len(self._parsed_site_mappings)}，视频源={len(self._parsed_sources)}")

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled, "clear": self._clear,
            "clear_handle": self._clear_handle, "override_mode": self._override_mode,
            "respect_rules": self._respect_rules, "update_details": self._update_details,
            "site_group_mappings": self._site_group_mappings, "source_patterns": self._source_patterns,
        })

    @staticmethod
    def __escape_regex(text: str) -> str:
        """转义字面值，兼容 DDP5.1 / DDP 5.1 及发布名连接符。"""
        if not text:
            return ""
        parts = re.split(r'[\s._-]+|(?<=[A-Za-z])(?=\d)', text)
        return r'[\s._-]*'.join(re.escape(p) for p in parts if p)

    @staticmethod
    def __normalize_source_pattern(pattern: str) -> str:
        """所有分支都加 ASCII 词界，包括已有的 CR|Crunchyroll 等旧配置。"""
        if not pattern:
            return ""
        if not re.search(r'[\\\[\]{}()|?*+^$]', pattern):
            pattern = r'[\s._-]*'.join(re.escape(p) for p in re.split(r'[\s._-]+', pattern) if p)
        # 全局 flags 不能直接放进非捕获组，先转为作用域 flags。
        re.compile(pattern, re.I)
        flags = ''
        while True:
            match = re.match(r'^\(\?([aiLmsux]+)\)', pattern)
            if not match:
                break
            flags += match.group(1)
            pattern = pattern[match.end():]
        if flags:
            pattern = f"(?{''.join(dict.fromkeys(flags))}:{pattern})"
        bounded = rf'(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])'
        re.compile(bounded, re.I)
        return bounded

    def __extract_source_from_title(self, title: str) -> Optional[str]:
        best = None
        for pattern in self._parsed_sources:
            try:
                for match in re.finditer(pattern, title or '', re.I):
                    text = match.group(0)
                    if text and (best is None or len(text) > len(best)):
                        best = text
            except re.error:
                logger.warning("视频源匹配失败，已跳过无效配置")
        return best

    @staticmethod
    def __extract_group_from_title(title: str) -> str:
        if not title:
            return ""
        match = re.search(
            r'-((?:M-Team|VCB-Studio|[A-Za-z0-9]+)(?:@[A-Za-z0-9]+)*)'
            r'(?=\[|\s*$|\.(?:mkv|mp4|avi|ts)(?:\s|$))', title, re.I)
        if not match or re.fullmatch(r'\d+Audios?', match.group(1), re.I):
            return ""
        return match.group(1)

    def __extract_visual_effects_from_title(self, title: str) -> List[str]:
        """保留原有锁版模式的视觉提取；规则保护模式不调用此结果锁定画质。"""
        patterns = [
            (r'\bDolby[\s.\-_]?Vision\b|\bDoVi\b|\bDovi\b', 'DV'),
            (r'\bDV[\s.\-_]?P\d\b', 'DV'),
            (r'(?<![A-Za-z])DV(?![A-Za-z0-9])', 'DV'),
            (r'\bHDR10\s*\+|\bHDR10[\s.\-_]*Plus\b', 'HDR'),
            (r'\bHDR10\b(?!\s*\+)(?![\s.\-_]*Plus)', 'HDR'),
            (r'\bHDR[\s.\-_]?Vivid\b|\bHDRVivid\b', 'HDR'),
            (r'\bHLG\b', 'HDR'), (r'\bHDR\b', 'HDR'),
            (r'\bIMAX[\s.\-_]?Enhanced\b|\bIMAX\b', 'IMAX'),
            (r'\b120[\s.\-_]?[Ff]ps\b', 'fps'),
            (r'\b60[\s.\-_]?[Ff]ps\b', 'fps'),
            (r'\b30[\s.\-_]?[Ff]ps\b', 'fps'),
            (r'\b25[\s.\-_]?[Ff]ps\b', 'fps'),
            (r'\b24[\s.\-_]?[Ff]ps\b', 'fps'),
            (r'\bSDR\b', 'SDR'),
            (r'(?<![A-Za-z])HQ(?![A-Za-z0-9])|高码|\bEDR\b', 'HQ'),
            (r'\b12[\s.\-_]?bit\b', 'bit'),
            (r'\b10[\s.\-_]?bit\b', 'bit'),
            (r'\b8[\s.\-_]?bit\b', 'bit'),
        ]
        found, categories = [], set()
        for pattern, category in patterns:
            if category in categories:
                continue
            match = re.search(pattern, title or '', re.I)
            if match:
                found.append(match.group(0))
                categories.add(category)
        return found

    def __extract_audio_effects_from_title(self, title: str) -> List[str]:
        """保留原有音轨提取；只有显式锁版模式才将结果转成硬条件。"""
        ch = r'(?:[\s._-]*\d+(?:(?:\.\d+){0,2}|(?:\s\d))?(?!\d)(?![\s._-]*[Aa]udio)(?![\s._-]*bit)(?:[\s._-]*(?:ch|channel)(?![a-z]))?)?'
        patterns = [
            (rf'\bTrueHD[\s._-]*Atmos{ch}\b', 'TrueHD'),
            (rf'\bTrueHD{ch}[\s._-]*Atmos\b', 'TrueHD'),
            (rf'\bAtmos[\s._-]*TrueHD{ch}\b', 'TrueHD'),
            (rf'\bTrueHD{ch}', 'TrueHD'),
            (rf'\bDTS[\s._-]*:?[\s._-]*X{ch}', 'DTSX'),
            (rf'\bDTS[\s._-]*HD[\s._-]*MA{ch}', 'DTSHDMA'),
            (rf'\bDTS[\s._-]*HD[\s._-]*HR{ch}', 'DTSHDHR'),
            (rf'\bDTS[\s._-]*HD(?![\s._-]*MA|[\s._-]*HR){ch}', 'DTSHD'),
            (rf'\bDTS[\s._-]*ES{ch}', 'DTSES'),
            (rf'\b(?:DDP|E-?AC-?3|DD\+){ch}[\s._-]*Atmos\b', 'DDP'),
            (rf'\b(?:DDP|E-?AC-?3|DD\+){ch}', 'DDP'),
            (r'\bDolby[\s._-]*Digital[\s._-]*Plus\b', 'DDP'),
            (rf'\b(?:Dolby[\s._-]*)?Atmos{ch}\b', 'Atmos'),
            (rf'\bDD(?![P+])(?=(?:[\s._-]*\d|\b)){ch}|\bAC-?3{ch}|\bDolby[\s._-]*Digital{ch}(?![\s._-]*Plus)', 'DD'),
            (rf'\bDTS{ch}(?![\s._-]*:?X|[\s._-]*HD|[\s._-]*ES)', 'DTS'),
            (rf'\bL?PCM{ch}', 'LPCM'), (rf'\bFLAC{ch}', 'FLAC'),
            (rf'\bWAV{ch}', 'WAV'), (rf'\bHE[\s._-]*AAC{ch}', 'AAC'),
            (rf'\bAAC{ch}', 'AAC'), (rf'\bAV3A{ch}', 'AV3A'),
            (r'\bOpus\b', 'Opus'), (r'\bMP3\b', 'MP3'),
            (r'\bVORBIS\b', 'Vorbis'), (r'\bOGG\b', 'OGG'),
        ]
        found, categories = [], set()
        for pattern, category in patterns:
            if category in categories or (category == 'Atmos' and any('atmos' in s.lower() for s in found)):
                continue
            match = re.search(pattern, title or '', re.I)
            if match:
                found.append(match.group(0))
                categories.add(category)
        return found

    @staticmethod
    def __parse_pix(resource_pix):
        if not resource_pix:
            return None
        for pattern in [r'1080[pi]|x1080', r'4K|2160p|x2160', r'720[pi]|x720']:
            if re.match(pattern, resource_pix, re.I):
                return pattern
        return resource_pix

    @staticmethod
    def __parse_type(resource_type):
        if not resource_type:
            return None
        patterns = [r'Blu-?Ray.+VC-?1|Blu-?Ray.+AVC|UHD.+blu-?ray.+HEVC|MiniBD',
                    r'Remux', r'Blu-?Ray', r'UHD|UltraHD', r'WEB-?DL|WEB-?RIP',
                    r'HDTV', r'[Hx].?265|HEVC', r'[Hx].?264|AVC']
        for pattern in patterns:
            if re.match(pattern, resource_type, re.I):
                return pattern
        return resource_type

    @staticmethod
    def __list_value(value) -> list:
        if value is None or value == '':
            return []
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, list):
            raise ValueError('列表配置格式错误')
        return value

    def __rule_policy(self, subscribe, context) -> Tuple[bool, bool]:
        """返回 (允许回填, 规则接管画质)。仅复检已下载资源，不创建/取消下载。"""
        if not self._respect_rules:
            return True, False
        try:
            own = self.__list_value(getattr(subscribe, 'filter_groups', None))
            best = getattr(subscribe, 'best_version', 0) not in (None, False, 0, '', '0')
            key = SystemConfigKey.BestVersionFilterRuleGroups if best else SystemConfigKey.SubscribeFilterRuleGroups
            names = own or self.__list_value(self.systemconfig.get(key))
            if not names:
                return True, False
            if not all(isinstance(n, str) and n.strip() for n in names):
                raise ValueError('规则组名称格式错误')
            names = list(dict.fromkeys(names))
            definitions = self._rulehelper.get_rule_groups()
            by_name = {g.name: g for g in definitions}
            missing = [n for n in names if n not in by_name]
            if missing:
                logger.warning(f"订阅 {subscribe.id} 跳过回填：规则组已失效 {missing}；请重新选择，不回退放行")
                return False, True
            media = copy.deepcopy(getattr(context, 'media_info', None))
            torrent = getattr(context, 'torrent_info', None)
            if media is None or torrent is None:
                logger.warning(f"订阅 {subscribe.id} 跳过回填：缺少媒体/种子上下文，无法复检规则")
                return False, True
            if getattr(subscribe, 'media_category', None):
                media.category = subscribe.media_category
            applicable = self._rulehelper.get_rule_group_by_media(media=media, group_names=names)
            has_targeted = any(by_name[n].media_type or by_name[n].category for n in names)
            if not applicable or (has_targeted and not any(g.media_type or g.category for g in applicable)):
                logger.warning(f"订阅 {subscribe.id} 跳过回填：分类 {getattr(media, 'category', None)!r} "
                               f"没有适用分类组；选择={names}")
                return False, True
            if any(not g.rule_string for g in applicable):
                logger.warning(f"订阅 {subscribe.id} 跳过回填：适用规则组存在空规则串")
                return False, True
            # 原生过滤器会修改 pri_order，必须隔离事件中被其他监听器共享的对象。
            matched = self.chain.filter_torrents(
                rule_groups=names, torrent_list=[copy.deepcopy(torrent)], mediainfo=media)
            logger.info(f"订阅 {subscribe.id} 下载后规则复检：来源={'单条' if own else ('洗版全局' if best else '普通全局')}，"
                        f"分类={getattr(media, 'category', None)}，适用组={[g.name for g in applicable]}，"
                        f"通过={bool(matched)}")
            if not matched:
                logger.warning(f"订阅 {subscribe.id} 当前下载不符合所选规则，跳过全部回填；已下发的下载不会被撤回")
                return False, True
            return True, True
        except Exception as exc:
            logger.warning(f"订阅 {subscribe.id} 优先级复检失败（{type(exc).__name__}），跳过回填")
            return False, True

    def __get_site_by_group(self, resource_team: str, default_site: Optional[int]) -> List[int]:
        allowed = set()
        for item in self.__list_value(self.systemconfig.get(SystemConfigKey.RssSites)):
            try:
                allowed.add(int(item))
            except (TypeError, ValueError):
                continue
        active_sites = [s for s in self._siteoper.list_active() if int(s.id) in allowed]
        if resource_team:
            for name, pattern in self._parsed_site_mappings.items():
                try:
                    if any(re.fullmatch(pattern, p, re.I) for p in resource_team.split('@')):
                        for site in active_sites:
                            if site.name == name:
                                return [int(site.id)]
                except re.error:
                    logger.warning(f"站点 {name} 官组匹配失败，已跳过")
        return [int(default_site)] if default_site and any(int(s.id) == int(default_site) for s in active_sites) else []

    def __build_update(self, subscribe, context, protected: bool) -> dict:
        torrent = context.torrent_info
        meta = getattr(context, 'meta_info', None)
        title = torrent.title or ''
        details = set(self._update_details)
        if protected:
            removed = details - {'制作组', '站点'}
            if removed:
                logger.info(f"订阅 {subscribe.id} 画质由优先级规则管理，不回填：{sorted(removed)}")
            details &= {'制作组', '站点'}
        update = {}
        if '分辨率' in details and not subscribe.resolution:
            value = self.__parse_pix(getattr(meta, 'resource_pix', None))
            if value:
                update['resolution'] = value
        if '资源质量' in details and not subscribe.quality:
            value = self.__parse_type(getattr(meta, 'resource_type', None))
            if value:
                update['quality'] = value
        team = self.__extract_group_from_title(title) or getattr(meta, 'resource_team', None)
        override = self._override_mode and not protected
        if not subscribe.include or override:
            parts = []
            for option, extractor in [('视觉特效', self.__extract_visual_effects_from_title),
                                      ('音频特效', self.__extract_audio_effects_from_title)]:
                if option in details:
                    parts.extend(rf'(?<![A-Za-z0-9]){self.__escape_regex(x)}(?![A-Za-z0-9])'
                                 for x in extractor(title))
            if '视频源' in details:
                source = self.__extract_source_from_title(title)
                if source:
                    parts.append(rf'(?<![A-Za-z0-9]){self.__escape_regex(source)}(?![A-Za-z0-9])')
            if '制作组' in details and team:
                parts.append(rf'[-@]{re.escape(team)}(?![A-Za-z0-9])')
            if parts:
                expr = r'(?i)\A' + ''.join(rf'(?=[\s\S]*{p})' for p in dict.fromkeys(parts)) + r'[\s\S]*'
                if re.search(expr, title, re.I):
                    update['include'] = expr
                    if override and getattr(subscribe, 'effect', None):
                        update['effect'] = None
                else:
                    logger.warning(f"订阅 {subscribe.id} 自动生成条件不能匹配参考标题，未写入include")
        if '站点' in details and not subscribe.sites:
            sites = self.__get_site_by_group(team, getattr(torrent, 'site', None))
            if sites:
                update['sites'] = sites
        return update

    @eventmanager.register(EventType.DownloadAdded)
    def download_notice(self, event: Event = None):
        """这是下载后的监听器，不拦截、不触发、不取消首次下载。"""
        if not self._enabled or not self._update_details or not event:
            return
        data = getattr(event, 'event_data', None) or {}
        if not data.get('hash') or not data.get('context'):
            logger.warning('订阅自动填充：下载事件缺少hash或context')
            return
        with self._lock:
            try:
                self.__handle_download(data)
            except Exception as exc:
                logger.error(f"订阅自动填充处理失败（{type(exc).__name__}），未继续回填")

    def __handle_download(self, data: dict):
        history = self._downloadhistoryoper.get_by_hash(data['hash'])
        if not history or history.type != '电视剧' or not history.tmdbid:
            return
        season_text = str(history.seasons or '')
        match = re.fullmatch(r'S?(\d+)', season_text.strip(), re.I)
        if not match:
            logger.warning('订阅自动填充：下载历史没有唯一季号，跳过；不把多季资源回填到全部订阅')
            return
        season = int(match.group(1))
        subscribes = self._subscribeoper.list_by_tmdbid(tmdbid=history.tmdbid, season=season) or []
        handled = self.get_data('history_handle') or []
        if not isinstance(handled, list):
            handled = []
        for subscribe in subscribes:
            if subscribe.type != '电视剧' or subscribe.season != season:
                continue
            # 旧的“电视剧:tmdbid”条目无法区分季，不再用于阻止其他季的回填。
            key = f'subscribe:{subscribe.id}:{history.tmdbid}:S{season}'
            if key in handled:
                continue
            try:
                allowed, protected = self.__rule_policy(subscribe, data['context'])
                if not allowed:
                    continue
                update = self.__build_update(subscribe, data['context'], protected)
                if not update:
                    continue
                before = {field: getattr(subscribe, field, None) for field in update}
                self._subscribeoper.update(subscribe.id, update)
                records = self.get_data('history') or []
                if not isinstance(records, list):
                    records = [records]
                records.append({
                    'name': subscribe.name, 'subscribe_id': subscribe.id, 'season': season,
                    'type': '下载后自动填充', 'before': before,
                    'content': json.dumps(update),
                    'time': time.strftime('%Y-%m-%d %H:%M:%S'),
                })
                self.save_data(key='history', value=records)
                handled.append(key)
                self.save_data(key='history_handle', value=handled)
                logger.info(f"订阅 {subscribe.id} S{season:02d} 下载后填充完成：字段={list(update)}，"
                            f"规则保护={protected}；未修改优先级规则组")
            except Exception as exc:
                logger.error(f"订阅 {subscribe.id} 回填失败（{type(exc).__name__}），未继续处理该订阅")

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        def col(component, props, md=12):
            return {'component': 'VCol', 'props': {'cols': 12, 'md': md},
                    'content': [{'component': component, 'props': props}]}

        switches = [('enabled', '启用插件'), ('respect_rules', '尊重优先级规则（推荐）'),
                    ('override_mode', '覆盖已有包含条件（仅锁版模式）'),
                    ('clear', '清理历史记录'), ('clear_handle', '清理已处理记录')]
        detail_names = ['资源质量', '分辨率', '视觉特效', '音频特效', '视频源', '制作组', '站点']
        rows = [
            {'component': 'VRow', 'content': [col('VSwitch', {'model': key, 'label': label}, 4)
                                             for key, label in switches]},
            {'component': 'VRow', 'content': [col('VSelect', {
                'model': 'update_details', 'label': '下载后填充内容', 'multiple': True, 'chips': True,
                'items': [{'title': name, 'value': name} for name in detail_names]})]},
            {'component': 'VRow', 'content': [
                col('VTextarea', {'model': 'site_group_mappings', 'label': '站点-官组映射配置',
                                  'rows': 10, 'placeholder': DEFAULT_SITE_GROUP_MAPPINGS}, 6),
                col('VTextarea', {'model': 'source_patterns', 'label': '视频源正则配置',
                                  'rows': 10, 'placeholder': DEFAULT_SOURCE_PATTERNS}, 6)]},
            {'component': 'VRow', 'content': [col('VAlert', {
                'type': 'info', 'variant': 'tonal',
                'text': '保护模式默认开启：使用单条订阅选择的规则组，留空则按普通/洗版模式使用全局规则。'
                        '有规则组时，回填前先复检当前下载；失效组名、无适用分类或复检失败则跳过回填。'
                        '规则接管画质时只填选中的制作组/站点，不锁分辨率、来源、画面或音轨，也不覆盖已有include/effect。'
                        '本插件只在下载已经添加后运行，不能撤回不合规下载或修复首次选源。'})]},
            {'component': 'VRow', 'content': [col('VAlert', {
                'type': 'warning', 'variant': 'tonal',
                'text': '关闭保护模式或未配置任何规则组时，选中的画质/音轨/平台将成为后续订阅的硬条件，不是加分。'
                        '覆盖模式此时会替换已有include并清空effect。升级不会自动清理之前回填的字段，请人工检查。'})]},
            {'component': 'VRow', 'content': [col('VAlert', {
                'type': 'info', 'variant': 'tonal',
                'text': '站点映射每行“站点名:组名正则”；仅在启用且属于订阅站点范围的站点中选择。'
                        '视频源每行一个正则，所有分支自动加词界，CR不再匹配Crew。'
                        '制作组保留@组合。已处理记录按订阅与季隔离；清理记录不会清理订阅字段。'})]},
        ]
        return [{'component': 'VForm', 'content': rows}], {
            'enabled': False, 'respect_rules': True, 'override_mode': False,
            'clear': False, 'clear_handle': False, 'update_details': [],
            'site_group_mappings': DEFAULT_SITE_GROUP_MAPPINGS,
            'source_patterns': DEFAULT_SOURCE_PATTERNS,
        }

    def get_page(self) -> List[dict]:
        history = self.get_data('history') or []
        if not history:
            return [{'component': 'div', 'text': '暂无数据', 'props': {'class': 'text-center'}}]
        if not isinstance(history, list):
            history = [history]
        rows = []
        for item in sorted(history, key=lambda x: x.get('time') or '', reverse=True):
            content = item.get('content') or ''
            try:
                content = json.dumps(json.loads(content), ensure_ascii=False)
            except (ValueError, TypeError):
                pass
            rows.append({'component': 'tr', 'content': [
                {'component': 'td', 'text': text} for text in
                [item.get('time'), item.get('name'), item.get('type'), content]]})
        return [{'component': 'VTable', 'props': {'hover': True}, 'content': [
            {'component': 'thead', 'content': [{'component': 'tr', 'content': [
                {'component': 'th', 'text': text} for text in ['执行时间', '订阅名称', '更新类型', '更新内容']]}]},
            {'component': 'tbody', 'content': rows},
        ]}]

    def stop_service(self):
        pass
