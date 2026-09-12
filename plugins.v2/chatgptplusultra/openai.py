"""OpenAI-compatible HTTP transport, safe key health and per-configuration caches."""
import hashlib
import json
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import BoundedSemaphore, RLock
from urllib.parse import urlsplit

import httpx
from app.log import logger
from .cache import TTLCache
from .recognition import SCHEMA_VERSION, PROTOCOL_GUARD, parse_identity, resolve_prompt, usable_title


class ProviderError(Exception):
    """Sanitized error: never contains request bodies, provider messages or keys."""
    def __init__(self, code, key_index=None):
        self.code, self.key_index = code, key_index
        suffix = f' (key #{key_index + 1})' if key_index is not None else ''
        super().__init__(f'AI provider: {code}{suffix}')


@dataclass(frozen=True)
class _Failure:
    code: str
    key_index: object = None


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

    def get_state(self):
        with self._lock:
            return bool(self._keys) and not self._closing

    def _extract_cache_key(self, filename):
        """No title cleanup: bracket names, editions and S/E remain distinct."""
        payload = [SCHEMA_VERSION, filename, self._model, self._api_url,
                   self._prompt, self._profile, self._deepseek]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()

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
                return ProviderError('authentication', index)
            if status == 429:
                delay, code = self._retry_after(exc), 'rate_limit'
                self._health[index]['cooldown'] = now + delay
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
            self._provider_cooldown = (now + delay, code)
            return ProviderError(code, index)

    def _request(self, messages, media=False, max_tokens=2048):
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

    def get_media_name(self, filename):
        """Return a validated two-field identity or None; raise only sanitized provider errors."""
        if not self.get_state():
            raise ProviderError('closed' if self._closing else 'configuration')
        if not usable_title(filename):
            return None
        def load():
            try:
                raw = self._request([
                    {'role': 'system', 'content': self._prompt},
                    {'role': 'user', 'content': json.dumps({'input_title': filename}, ensure_ascii=False)}
                ], media=True, max_tokens=512)
            except ProviderError as exc:
                return _Failure(exc.code, exc.key_index), 30.0
            parsed = parse_identity(raw, filename)
            if parsed is None:
                with self._lock:
                    self._abstentions += 1
            return parsed, self._positive_ttl if parsed else self._negative_ttl
        try:
            result = self._cache.get_or_load(self._extract_cache_key(filename), load, self._timeout)
        except TimeoutError:
            raise ProviderError('busy') from None
        if isinstance(result, _Failure):
            raise ProviderError(result.code, result.key_index)
        return result

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
            data.update(api_calls=self._calls, abstentions=self._abstentions)
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
