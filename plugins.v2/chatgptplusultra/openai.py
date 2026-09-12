"""OpenAI-compatible HTTP transport, safe key health and per-configuration caches."""
import hashlib
import json
import math
import time
from collections import OrderedDict, Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import BoundedSemaphore, RLock
from urllib.parse import urlsplit

import httpx
from app.log import logger
from .cache import TTLCache
from .diagnostics import safe_text
from .recognition import SCHEMA_VERSION, PROTOCOL_GUARD, inspect_identity, resolve_prompt, usable_title


class ProviderError(Exception):
    """Sanitized error: never contains request bodies, provider messages or keys."""
    def __init__(self, code, key_index=None, http_status=None):
        self.code, self.key_index = code, key_index
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        suffix = f' (key #{key_index + 1})' if key_index is not None else ''
        http = f' http={self.http_status}' if self.http_status is not None else ''
        super().__init__(f'AI provider: {code}{suffix}{http}')


@dataclass(frozen=True)
class _Failure:
    code: str
    key_index: object = None
    http_status: object = None


@dataclass(frozen=True)
class _Recognition:
    identity: object
    reason: str


@dataclass(frozen=True)
class MediaResult:
    """Per-caller diagnostics, not mutable global 'last request' state."""
    identity: object
    reason: str
    source: str
    elapsed_ms: float
    attempts: int = 0
    usage: object = None
    error: object = None


def normalize_endpoint(api_url, compatible=False):
    """Accept root or an existing /v1 base; never duplicate /v1 or log credentials."""
    value = (api_url or '').strip().rstrip('/')
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path.endswith('/chat/completions')):
        raise ValueError('Invalid API base URL')
    return value if compatible or parsed.path.endswith('/v1') else value + '/v1'


class OpenAi:
    """Own clients for every key; closing drains active calls without force-closing sockets."""
    def __init__(self, api_key=None, api_url=None, proxy=None, model=None,
                 compatible=False, customize_prompt=None, api_keys=None,
                 timeout=20, max_attempts=2, positive_ttl=3600, negative_ttl=600,
                 cache_size=1000, max_concurrency=2, profile='auto'):
        self._keys = tuple(dict.fromkeys(k.strip() for k in (api_keys or [api_key or ''])
                                       if isinstance(k, str) and k.strip()))
        self._api_url = normalize_endpoint(api_url, compatible)
        self._model = model or 'deepseek-flash'
        self._prompt = resolve_prompt(customize_prompt) + PROTOCOL_GUARD
        self._proxy = proxy or {}
        self._timeout = max(1.0, min(120.0, float(timeout)))
        self._attempts = max(1, min(int(max_attempts), len(self._keys) or 1))
        self._positive_ttl = max(0, float(positive_ttl))
        self._negative_ttl = max(0, float(negative_ttl))
        self._deepseek = profile == 'deepseek' or (profile == 'auto' and
                           urlsplit(self._api_url).hostname == 'api.deepseek.com')
        self._profile = profile
        self._lock = RLock()
        self._chat_lock = RLock()
        self._slots = BoundedSemaphore(max(1, int(max_concurrency)))
        self._cache = TTLCache(maxsize=cache_size)
        self._clients = {}
        self._health = [{'disabled': False, 'cooldown': 0.0} for _ in self._keys]
        self._cursor = 0
        self._active = 0
        self._closing = False
        self._provider_cooldown = (0.0, None)
        self._sessions = OrderedDict()
        self._calls = self._abstentions = 0
        self._recognition_calls = self._chat_calls = 0
        self._usage = Counter()
        self._reasons = Counter()

    def get_state(self):
        with self._lock:
            return bool(self._keys) and not self._closing

    def _extract_cache_key(self, filename):
        """No title cleanup: bracket names, editions and S/E remain distinct."""
        payload = [SCHEMA_VERSION, filename, self._model, self._api_url,
                   self._prompt, self._profile, self._deepseek]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()

    def _client_for(self, index):
        """Reuse MP2's httpx; no SDK dependency upgrades or nested automatic retries."""
        with self._lock:
            if index not in self._clients:
                try:
                    self._clients[index] = httpx.Client(
                        base_url=self._api_url + '/',
                        headers={'Authorization': 'Bearer ' + self._keys[index]},
                        proxy=self._proxy.get('https') or self._proxy.get('http'),
                        timeout=self._timeout, follow_redirects=False)
                except Exception:
                    raise ProviderError('configuration', index) from None
            return self._clients[index]

    @staticmethod
    def _retry_after(exc):
        """Honor seconds or HTTP-date Retry-After; do not shorten a server cooldown."""
        text = getattr(getattr(exc, 'response', None), 'headers', {}).get('retry-after', '')
        try:
            seconds = float(text)
        except (ValueError, TypeError):
            try:
                when = parsedate_to_datetime(text)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                seconds = (when - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = 60.0
        return max(1.0, seconds) if math.isfinite(seconds) else 60.0

    def _classify(self, exc, index):
        """Only explicit 401 disables a key. Content failures never enter this path."""
        now = time.monotonic()
        with self._lock:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            if status == 401:
                self._health[index]['disabled'] = True
                self._cursor = (index + 1) % len(self._keys)
                return ProviderError('authentication', index, status)
            if status == 429:
                delay, code = self._retry_after(exc), 'rate_limit'
                self._health[index]['cooldown'] = max(self._health[index]['cooldown'], now + delay)
            elif isinstance(exc, httpx.TimeoutException) or status == 408:
                delay, code = 15, 'timeout'
            elif isinstance(exc, httpx.RequestError):
                delay, code = 15, 'connection'
            elif status == 403:
                delay, code = 300, 'permission'
            elif status is not None and 300 <= status < 500:
                delay, code = 300, 'configuration'
            else:
                delay, code = 30, 'service'
            if now + delay > self._provider_cooldown[0]:
                self._provider_cooldown = (now + delay, code)
            return ProviderError(code, index, status)

    def _request(self, messages, media=False, max_tokens=2048, diagnostics=None):
        """One admission budget; no automatic HTTP retries. Socket timeout is not a hard wall timer."""
        deadline = time.monotonic() + self._timeout
        acquired = False
        with self._lock:
            if self._closing or not self._keys:
                raise ProviderError('closed' if self._closing else 'configuration')
            self._active += 1
        try:
            acquired = self._slots.acquire(timeout=self._timeout)
            if not acquired:
                raise ProviderError('busy')
            attempted = set()
            last_error = ProviderError('no_available_key')
            for _ in range(self._attempts):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderError('timeout')
                with self._lock:
                    if self._closing:
                        raise ProviderError('closed')
                    until, reason = self._provider_cooldown
                    if until > time.monotonic():
                        raise ProviderError(reason)
                    eligible = [(self._cursor + step) % len(self._keys) for step in range(len(self._keys))]
                    index = next((i for i in eligible if i not in attempted
                        and not self._health[i]['disabled']
                        and self._health[i]['cooldown'] <= time.monotonic()), None)
                if index is None:
                    raise last_error
                attempted.add(index)
                params = {'model': self._model, 'messages': messages,
                          'max_tokens': max_tokens}
                if self._deepseek:
                    params['thinking'] = {'type': 'disabled'}
                    if media:
                        params.update(response_format={'type': 'json_object'}, temperature=0)
                try:
                    http_client = self._client_for(index)
                    with self._lock:
                        self._calls += 1
                        if media:
                            self._recognition_calls += 1
                        else:
                            self._chat_calls += 1
                    if diagnostics is not None:
                        diagnostics['attempts'] += 1
                    response = http_client.post('chat/completions', json=params, timeout=remaining)
                    response.raise_for_status()
                except ProviderError:
                    raise
                except httpx.HTTPError as exc:
                    last_error = self._classify(exc, index)
                    # Rotate only on authentication, not on missing titles or shared rate limits.
                    if last_error.code == 'authentication':
                        continue
                    raise last_error from None
                try:
                    body = response.json()
                    usage = self._record_usage(body)
                    if diagnostics is not None:
                        diagnostics['usage'] = usage
                    choices = body.get('choices') if isinstance(body, dict) else None
                    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                        raise ValueError('invalid choices')
                    choice = choices[0]
                    message = choice.get('message')
                    if not isinstance(message, dict):
                        raise ValueError('invalid message')
                except (ValueError, TypeError):
                    raise ProviderError('invalid_response', index) from None
                if choice.get('finish_reason') == 'length':
                    raise ProviderError('truncated_response', index)
                with self._lock:
                    self._cursor = index
                return message.get('content')
            raise last_error
        finally:
            if acquired:
                self._slots.release()
            with self._lock:
                self._active -= 1
                retired = list(self._clients.values()) if self._closing and self._active == 0 else []
                if retired:
                    self._clients.clear()
            self._close_clients(retired)

    def _record_usage(self, body):
        """Count only explicit nonnegative integer usage on actual HTTP responses."""
        data = body.get('usage') if isinstance(body, dict) else None
        fields = ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens',
                  'prompt_cache_miss_tokens')
        usage = {key: data[key] for key in fields if isinstance(data, dict)
                 and type(data.get(key)) is int and 0 <= data[key] <= 10**10}
        if usage:
            with self._lock:
                self._usage.update(usage)
                self._usage['usage_responses'] += 1
        return usage

    def log_text(self, value, limit=240):
        return safe_text(value, self._keys, limit=limit)

    def trace_id(self, title):
        return self._extract_cache_key(title)[:12]

    def get_media_result(self, filename):
        """Value and source are returned atomically by the cache; no counter-delta guessing."""
        started = time.monotonic()
        details = {'attempts': 0, 'usage': {}}
        def report(identity, reason, source, error=None):
            return MediaResult(identity, reason, source, round((time.monotonic()-started)*1000, 2),
                               details['attempts'], details['usage'], error)
        if not self.get_state():
            error = ProviderError('closed' if self._closing else 'configuration')
            return report(None, error.code, 'local', error)
        if not usable_title(filename):
            return report(None, 'invalid_title', 'local')
        def load():
            try:
                raw = self._request([
                    {'role': 'system', 'content': self._prompt},
                    {'role': 'user', 'content': json.dumps({'input_title': filename}, ensure_ascii=False)}
                ], media=True, max_tokens=512, diagnostics=details)
            except ProviderError as exc:
                return _Failure(exc.code, exc.key_index, exc.http_status), 30.0
            identity, reason = inspect_identity(raw, filename)
            with self._lock:
                self._reasons[reason] += 1
                if identity is None:
                    self._abstentions += 1
            return _Recognition(identity, reason), self._positive_ttl if identity else self._negative_ttl
        try:
            result, source = self._cache.get_or_load_with_source(
                self._extract_cache_key(filename), load, self._timeout)
        except TimeoutError:
            return report(None, 'busy', 'coalesced', ProviderError('busy'))
        if source == 'loader':
            source = 'api' if details['attempts'] else 'local'
        if isinstance(result, _Failure):
            return report(None, result.code, source, ProviderError(result.code, result.key_index, result.http_status))
        return report(result.identity, result.reason, source)

    def get_media_name(self, filename):
        """Existing consumers still get a two-field dict/None or a sanitized exception."""
        result = self.get_media_result(filename)
        if result.error:
            raise result.error
        return result.identity

    def get_response(self, text, userid):
        """Serialize chat histories separately from recognition; store the answer, not the question."""
        if not userid:
            return '用户信息错误'
        with self._chat_lock:
            if text == '#清除':
                self._sessions.pop(str(userid), None)
                return '会话已清除'
            history = list(self._sessions.get(str(userid), []))
            messages = [{'role': 'system', 'content': '请使用中文回复。'}] + history + [
                {'role': 'user', 'content': text}]
            result = self._request(messages)
            if not isinstance(result, str) or not result.strip():
                raise ProviderError('empty_response')
            history += [{'role': 'user', 'content': text}, {'role': 'assistant', 'content': result}]
            self._sessions[str(userid)] = history[-32:]
            self._sessions.move_to_end(str(userid))
            while len(self._sessions) > 100:
                self._sessions.popitem(last=False)
            return result

    def translate_to_zh(self, text):
        try:
            result = self._request([{'role':'system','content':'Translate to Chinese. Return only the translation.'},
                                    {'role':'user','content':text}])
            return bool(result), result
        except ProviderError as exc:
            return False, str(exc)

    def get_question_answer(self, question):
        try:
            return self._request([{'role':'system','content':'返回给定问题正确选项的序号，只输出序号。'},
                                  {'role':'user','content':question}])
        except ProviderError:
            return None

    def clear_media_cache(self):
        self._cache.clear()

    def key_health(self):
        """Only numeric slot identities are observable outside the transport."""
        with self._lock:
            return [{'index': i, 'disabled': h['disabled'],
                     'cooldown_remaining': max(0, h['cooldown'] - time.monotonic())}
                    for i, h in enumerate(self._health)]

    def stats(self):
        data = self._cache.stats()
        with self._lock:
            data.update(api_calls=self._calls, abstentions=self._abstentions,
                        recognition_api_calls=self._recognition_calls, other_api_calls=self._chat_calls,
                        validation_results=dict(self._reasons), **dict(self._usage))
            until, reason = self._provider_cooldown
            data['provider_cooldown_remaining'] = round(max(0, until - time.monotonic()), 1)
            data['provider_cooldown_reason'] = reason if data['provider_cooldown_remaining'] else None
        return data

    @staticmethod
    def _close_clients(clients):
        for http_client in clients:
            try:
                http_client.close()
            except Exception:
                logger.debug('ChatGPTPlusUltra: 客户端清理失败（详情已隐藏）')

    def close(self):
        with self._lock:
            self._closing = True
            retired = list(self._clients.values()) if self._active == 0 else []
            if retired:
                self._clients.clear()
        self._cache.clear()
        self._close_clients(retired)
