"""ChatGPT Plus Ultra: conservative auxiliary recognition for MoviePilot V2."""
import json
import math
import time
from threading import RLock
from typing import Any, Dict, List, Tuple

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.core.metainfo import MetaInfo
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.types import EventType, ChainEventType
from .openai import OpenAi, ProviderError
from .recognition import DEFAULT_PROMPT, resolve_prompt, usable_title, has_name, build_event_result, is_release_group


def _bool(value):
    return value.strip().lower() in {'true', '1', 'yes', 'on'} if isinstance(value, str) else bool(value)


def _number(value, default, lower, upper):
    try:
        number = float(value)
        return min(upper, max(lower, number)) if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


class ChatGPTPlusUltra(_PluginBase):
    plugin_name = 'ChatGPT Plus Ultra'
    plugin_desc = '严格名称提取、季集安全适配、正负缓存与 DeepSeek Flash 支持。'
    plugin_icon = 'Chatgpt_A.png'
    plugin_version = '1.4.1'
    plugin_author = 'eitelkeit0708'
    author_url = 'https://github.com/eitelkeit0708'
    plugin_config_prefix = 'chatgptplusultra_'
    plugin_order = 15
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._lock = RLock()
        self.openai = None
        self._enabled = self._recognize = self._chat_enabled = self._notify = False
        self._customize_prompt = DEFAULT_PROMPT
        self._errors = {}

    def init_plugin(self, config: dict = None):
        """Swap a complete runtime; removed credentials never leave an old client active."""
        cfg = dict(config or {})
        original_prompt = cfg.get('customize_prompt')
        prompt = resolve_prompt(original_prompt)
        changed = False
        if _bool(cfg.get('restore_prompt')):
            prompt = DEFAULT_PROMPT
            cfg['restore_prompt'] = False
            changed = True
        if isinstance(original_prompt, str) and original_prompt.strip() and prompt != original_prompt.strip():
            cfg['previous_customize_prompt'] = original_prompt
        if cfg.get('customize_prompt') != prompt:
            cfg['customize_prompt'] = prompt
            cfg['prompt_schema_version'] = 2
            changed = True
        if _bool(cfg.get('clear_cache')):
            cfg['clear_cache'] = False
            changed = True
        enabled = _bool(cfg.get('enabled'))
        keys = [key.strip() for key in str(cfg.get('openai_key') or '').split(',') if key.strip()]
        runtime = None
        if enabled and keys and cfg.get('openai_url'):
            try:
                runtime = OpenAi(
                    api_keys=keys, api_url=cfg['openai_url'], model=cfg.get('model') or 'deepseek-flash',
                    proxy=settings.PROXY if _bool(cfg.get('proxy')) else None,
                    compatible=_bool(cfg.get('compatible')), customize_prompt=prompt,
                    timeout=_number(cfg.get('timeout'), 20, 1, 120),
                    max_attempts=int(_number(cfg.get('max_attempts'), 2, 1, 5)),
                    positive_ttl=_number(cfg.get('positive_ttl'), 3600, 0, 86400),
                    negative_ttl=_number(cfg.get('negative_ttl'), 600, 0, 86400),
                    cache_size=int(_number(cfg.get('cache_size'), 1000, 1, 10000)),
                    max_concurrency=int(_number(cfg.get('max_concurrency'), 2, 1, 8)),
                    profile=cfg.get('request_profile') if cfg.get('request_profile') in
                            {'auto','deepseek','generic'} else 'auto')
            except Exception:
                logger.error('ChatGPTPlusUltra 配置无效，请检查 API 基址和数值设置（敏感详情已隐藏）')
        with self._lock:
            previous = self.openai
            self.openai = runtime
            self._enabled = enabled
            self._recognize = _bool(cfg.get('recognize'))
            # Preserve old installations' chat behavior; new form defaults to recognition-only.
            self._chat_enabled = _bool(cfg.get('chat_enabled', enabled))
            self._notify = _bool(cfg.get('notify'))
            self._customize_prompt = prompt
            self._errors.clear()
        if previous:
            previous.close()
        if changed:
            # Preserve API settings and unknown config fields during one-shot actions/migration.
            self.update_config(cfg)

    def _report_error(self, runtime, error, rid=None):
        """Rate-limit sanitized diagnostics; cached failures must not flood notifications."""
        now = time.monotonic()
        key = (error.code, error.key_index)
        with self._lock:
            if runtime is not self.openai:
                return
            previous = self._errors.get(key)
            if previous is not None and now - previous < 300:
                return
            self._errors[key] = now
            notify = self._notify
        logger.warning(f'ChatGPTPlusUltra: id={rid or "-"} {error}')
        if notify:
            self.post_message(mtype=NotificationType.Plugin, title=self.plugin_name,
                              text=f'辅助服务暂不可用：{error}；未修改资源或订阅。')

    def _trace(self, runtime, title, status, reason, started, result=None, payload=None, meta=None):
        """One request-local summary/detail, all untrusted scalars escaped and redacted."""
        rid = runtime.trace_id(title)
        source = result.source if result else 'local'
        elapsed = round((time.monotonic() - started) * 1000, 2)
        prefix = (f'ChatGPTPlusUltra: id={rid} status={status} reason={reason} '
                  f'source={source} elapsed_ms={elapsed}')
        safe = runtime.log_text
        if payload:
            summary = (f'{prefix} name={safe(payload["name"])} year={safe(payload["year"])}'
                       '；已提交名称候选，最终匹配由 MP2 决定')
            # Cache replays remain inspectable at DEBUG without flooding five-minute RSS runs.
            if source == 'api':
                logger.info(summary)
        detail = f'{prefix} title={safe(title, limit=480)}'
        if result:
            identity = result.identity or {}
            detail += (f' ai_name={safe(identity.get("name"))} ai_year={safe(identity.get("year"))}'
                       f' api_attempts={result.attempts} usage={json.dumps(result.usage or {})}')
        if result and result.error and result.error.http_status is not None:
            detail += f' http={result.error.http_status}'
        if meta is not None:
            detail += (f' meta_name={safe(getattr(meta, "name", None))}'
                       f' meta_year={safe(getattr(meta, "year", None))}')
        if payload:
            detail += (f' name={safe(payload["name"])} year={safe(payload["year"])}'
                       f' season={safe(payload["season"])} episode={safe(payload["episode"])}'
                       ' season_episode_source=MetaInfo')
        logger.debug(detail)

    def _warn_validation(self, runtime, reason, title):
        """Structural/semantic rejection is not an API failure or a key-failure notification."""
        if reason == 'no_name':
            return
        now = time.monotonic()
        key = ('validation', reason)
        with self._lock:
            if runtime is not self.openai:
                return
            previous = self._errors.get(key)
            if previous is not None and now - previous < 300:
                return
            self._errors[key] = now
        logger.warning(f'ChatGPTPlusUltra: id={runtime.trace_id(title)} '
                       f'候选未通过校验 reason={reason}；未禁用密钥，详情见 DEBUG')

    @eventmanager.register(ChainEventType.NameRecognize)
    def recognize(self, event: Event):
        """Provide a candidate only; MP2 remains responsible for media lookup and selection."""
        started = time.monotonic()
        with self._lock:
            runtime = self.openai
            if not self._enabled or not self._recognize or not runtime:
                return
        data = getattr(event, 'event_data', None)
        title = data.get('title') if isinstance(data, dict) else None
        if not usable_title(title):
            self._trace(runtime, '', 'skipped', 'invalid_title', started)
            return
        if has_name(data):
            self._trace(runtime, title, 'skipped', 'existing_result', started)
            return
        meta = result = None
        try:
            meta = MetaInfo(title=title)
            if meta is None:
                self._trace(runtime, title, 'skipped', 'metainfo_empty', started)
                return
            result = runtime.get_media_result(filename=title)
            if result.error:
                self._trace(runtime, title, 'error', result.reason, started, result, meta=meta)
                self._report_error(runtime, result.error, runtime.trace_id(title))
                return
            if result.identity is None:
                self._trace(runtime, title, 'abstained' if result.reason == 'no_name' else 'rejected',
                            result.reason, started, result, meta=meta)
                if result.source == 'api':
                    self._warn_validation(runtime, result.reason, title)
                return
            if is_release_group(result.identity, meta):
                self._trace(runtime, title, 'rejected', 'name_is_release_group', started, result, meta=meta)
                return
            payload = build_event_result(title, result.identity, meta)
            if payload is None:
                self._trace(runtime, title, 'rejected', 'invalid_metainfo_number', started, result, meta=meta)
                return
        except ProviderError as error:
            self._trace(runtime, title, 'error', error.code, started, result, meta=meta)
            self._report_error(runtime, error, runtime.trace_id(title))
            return
        except Exception as error:
            # Type name is useful for programming errors; never stringify the exception payload.
            self._trace(runtime, title, 'error', 'adapter_error', started, result, meta=meta)
            logger.warning('ChatGPTPlusUltra: 元数据适配失败 exception_type=' +
                           runtime.log_text(type(error).__name__) + '；已放弃本次辅助识别')
            return
        skip_reason = None
        with self._lock:
            if runtime is not self.openai or not self._enabled or not self._recognize:
                skip_reason = 'stale_runtime'
            else:
                current = getattr(event, 'event_data', None)
                if not isinstance(current, dict) or current.get('title') != title or has_name(current):
                    skip_reason = 'event_changed'
                else:
                    event.event_data = {**current, **payload}
        if skip_reason:
            self._trace(runtime, title, 'skipped', skip_reason, started, result, meta=meta)
            return
        self._trace(runtime, title, 'submitted', 'accepted', started, result, payload, meta)

    @eventmanager.register(EventType.UserMessage)
    def talk(self, event: Event):
        with self._lock:
            runtime = self.openai
            if not self._enabled or not self._chat_enabled or not runtime:
                return
        data = getattr(event, 'event_data', None)
        if not isinstance(data, dict):
            return
        text, userid, channel = data.get('text'), data.get('userid'), data.get('channel')
        if not isinstance(text, str) or not text.strip() or userid is None or len(text) > 16000:
            return
        if text.startswith(('http', 'magnet', 'ftp')):
            return
        if not (text == '#清除' or text.startswith(('问', '帮', '你')) or
                text.endswith(('?', '？')) or len(text) > 10):
            return
        session = json.dumps([str(channel), str(userid)], ensure_ascii=False)
        try:
            response = runtime.get_response(text=text, userid=session)
        except ProviderError as error:
            self._report_error(runtime, error)
            return
        with self._lock:
            if runtime is not self.openai or not self._enabled or not self._chat_enabled:
                return
        self.post_message(channel=channel, title=response, userid=userid)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """Existing config IDs are unchanged; reset/clear run once when saving."""
        defaults = dict(enabled=False, recognize=False, chat_enabled=False, notify=False,
            proxy=False, compatible=False, openai_url='https://api.deepseek.com',
            openai_key='', model='deepseek-flash', customize_prompt=DEFAULT_PROMPT,
            request_profile='auto', timeout=20, max_attempts=2, max_concurrency=2,
            cache_size=1000, positive_ttl=3600, negative_ttl=600,
            clear_cache=False, restore_prompt=False)
        fields = []
        for name, label in [('enabled','启用插件'),('recognize','辅助识别'),('chat_enabled','消息聊天'),
                            ('proxy','使用代理'),('compatible','基址按填写值使用（不补 /v1）'),
                            ('notify','错误通知'),('clear_cache','保存时清空缓存'),
                            ('restore_prompt','保存时恢复新版默认提示词')]:
            fields.append({'component':'VCol','props':{'cols':12,'md':6}, 'content':[
                {'component':'VSwitch','props':{'model':name,'label':label}}]})
        for name,label in [('openai_url','API 基址'),('openai_key','API 密钥（多个用逗号分隔）'),
                           ('model','模型 ID（DeepSeek 官方：deepseek-flash）'),
                           ('timeout','请求预算/超时（秒）'),('max_attempts','鉴权失败最多尝试密钥数'),
                           ('max_concurrency','同时请求上限'),('cache_size','缓存条数上限'),
                           ('positive_ttl','有效候选缓存秒数'),('negative_ttl','放弃识别缓存秒数')]:
            props = {'model':name,'label':label}
            if name == 'openai_key':
                props['type'] = 'password'
            elif name in {'timeout','max_attempts','max_concurrency','cache_size','positive_ttl','negative_ttl'}:
                props['type'] = 'number'
            fields.append({'component':'VCol','props':{'cols':12,'md':6},'content':[
                {'component':'VTextField','props':props}]})
        fields += [
            {'component':'VCol','props':{'cols':12},'content':[{'component':'VSelect','props':{
                'model':'request_profile','label':'请求配置（转发 DeepSeek 时可手动选择）',
                'items':[{'title':'自动识别官方域名','value':'auto'},
                         {'title':'DeepSeek：非思考；辅助识别 JSON 模式','value':'deepseek'},
                         {'title':'通用 OpenAI 兼容：不发送 DeepSeek 扩展','value':'generic'}]}}]},
            {'component':'VCol','props':{'cols':12},'content':[{'component':'VTextarea','props':{
                'model':'customize_prompt','label':'名称提取提示词（严格 name/year 两字段）','rows':10}}]},
            {'component':'VCol','props':{'cols':12},'content':[{'component':'VAlert','props':{
                'type':'info','variant':'tonal','text':
                'AI 只提取名称和年份，季集来自 MP2 对当前标题的解析。未知结果不会封禁密钥。'
                '缓存保留完整标题，不跨集复用；重启或保存设置会重建缓存。建议只开启一个 AI 辅助识别插件。'
                '消息以问/帮/你开头、问号结尾或超过10字时可触发聊天；#清除仅清空当前渠道会话。'}}]}
        ]
        return [{'component':'VForm','content':[{'component':'VRow','content':fields}]}], defaults

    def get_page(self) -> List[dict]:
        runtime = self.openai
        stats = runtime.stats() if runtime else {}
        return [{'component':'VAlert','props':{'type':'info','variant':'tonal',
            'text':'本次配置会话统计（API 调用含聊天；候选不等于最终匹配成功）：' +
                   json.dumps(stats, ensure_ascii=False)}}]

    def stop_service(self):
        with self._lock:
            previous, self.openai = self.openai, None
            self._enabled = self._recognize = self._chat_enabled = False
        if previous:
            previous.close()
