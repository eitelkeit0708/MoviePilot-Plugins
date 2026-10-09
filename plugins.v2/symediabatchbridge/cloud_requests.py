"""Bounded request boundary around MP's existing 115 storage algorithms.

Only a private shallow provider copy receives the request override. MP's
singleton, class, session, token refresh and QPS limiters are never patched.
Directory traversal and OSS upload remain the host's implementations.
"""

from copy import copy
from datetime import timezone
from email.utils import parsedate_to_datetime
import inspect
import time
from types import CodeType

import httpx

from .cooldown import CloudCooldown, CloudRequestError, deadline
from .domain import check_stop


API_METHODS = {
    "/open/upload/init": {"POST"}, "/open/upload/get_token": {"GET"},
    "/open/upload/resume": {"POST"}, "/open/ufile/files": {"GET"},
    "/open/folder/get_info": {"POST", "GET"}, "/open/folder/add": {"POST"},
}


def code_names(code):
    names = set(code.co_names)
    for item in code.co_consts:
        if isinstance(item, CodeType):
            names.update(code_names(item))
    return names


class NativeOperations:
    def __init__(self, provider, cooldown, stop):
        self.provider, self.cooldown, self.stop = provider, cooldown, stop
        self.failure = None
        required = ((provider, "_check_session"), (getattr(provider, "session", None), "request"),
                    (getattr(provider, "_api_limiter", None), "acquire"),
                    (getattr(provider, "_rate_stats", None), "record"))
        if any(not callable(getattr(owner, name, None)) for owner, name in required):
            raise CloudRequestError("当前 MP 的 115 请求接口不兼容，请更新适配", kind="contract", review=True)
        self.native = copy(provider)
        if self.native is provider:
            raise CloudRequestError("无法隔离 MP 的 115 请求接口", kind="contract", review=True)
        self.native._request_api = self.request

    def _raise(self, error):
        # Native get_item catches Exception and returns None. Remember the first
        # failure so that it cannot become permission to create/overwrite a path.
        self.failure = error
        raise error

    def check(self):
        check_stop(self.stop)
        if self.failure:
            raise self.failure
        self.cooldown.before(self.provider)

    def call(self, name, *args, **kwargs):
        self.check()
        self._validate_operation(name)
        try:
            result = getattr(self.native, name)(*args, **kwargs)
        except Exception:
            if self.failure:
                raise self.failure from None
            raise
        self.check()
        return result

    def _validate_operation(self, name):
        """Fail closed if the native call graph no longer has our request seam."""
        expected = {
            "get_folder": (("self", "path"), {"get_item", "list", "create_folder"}),
            "upload": (("self", "target_dir", "local_path", "new_name"), {"_request_api", "_calc_sha1", "get_item"}),
            "get_item": (("self", "path"), {"_U115Pan__get_info_item"}),
            "_U115Pan__get_info_item": (("self", "path"), {"_request_api"}),
            "list": (("self", "fileitem"), {"_request_api", "get_item", "detail"}),
            "create_folder": (("self", "parent_item", "name"), {"_request_api", "get_item"}),
            "detail": (("self", "fileitem"), {"get_item"}),
        }
        if name not in ("get_folder", "upload"):
            self._raise(CloudRequestError("不支持的 115 存储操作", kind="contract", review=True))
        methods = ({name, "get_item", "_U115Pan__get_info_item", "list", "create_folder", "detail"}
                   if name == "get_folder" else {name, "get_item", "_U115Pan__get_info_item"})
        for member in methods:
            method = getattr(type(self.provider), member, None)
            try:
                signature = tuple(inspect.signature(method).parameters)
                names = code_names(method.__code__)
            except (TypeError, ValueError, AttributeError):
                signature, names = (), set()
            parameters, required = expected[member]
            if (signature != parameters or not required <= names
                    or names & {"session", "httpx", "requests", "_request", "urlopen"}):
                self._raise(CloudRequestError("当前 MP 的 115 存储方法不兼容，请更新适配", kind="contract", review=True))

    def _limited(self, response=None):
        now = time.time()
        delay = max(1, deadline(getattr(self.provider, "limit_sleep_seconds", 3600)) or 3600)
        raw = str(getattr(response, "headers", {}).get("Retry-After", "")).strip()
        retry_at = 0
        if raw:
            try:
                retry_at = now + max(0, float(raw))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(raw)
                    retry_at = parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
                except (ValueError, TypeError, OverflowError):
                    pass
        state = self.cooldown.defer(max(now + delay, deadline(retry_at)), "115 接口限流")
        self._raise(CloudCooldown(state["until"]))

    def request(self, method, endpoint, result_key=None, *, accepted_codes=(0, 20004), **kwargs):
        self.check()
        if method not in API_METHODS.get(endpoint, ()) or self.provider.base_url != "https://proapi.115.com":
            self._raise(CloudRequestError("当前 MP 的 115 请求协议不兼容，请更新适配", kind="contract", review=True))
        # The host still owns login and token refresh. Never copy tokens into
        # plugin state or include raw exceptions/responses in user-facing errors.
        try:
            self.provider._check_session()
        except httpx.RequestError:
            self._raise(CloudRequestError("115 登录连接暂不可用", kind="network"))
        except Exception as error:
            kind = "authentication" if type(error).__name__ == "NoCheckInException" else "unknown"
            self._raise(CloudRequestError("115 登录状态不可用，请检查 MP 的 115 授权", kind=kind))
        limiter = (getattr(self.provider, "_download_limiter", None)
                   if endpoint == getattr(self.provider, "download_endpoint", None)
                   else self.provider._api_limiter)
        if not callable(getattr(limiter, "acquire", None)):
            self._raise(CloudRequestError("当前 MP 的 115 限速接口不兼容", kind="contract", review=True))
        try:
            limiter.acquire()
        except Exception:
            self._raise(CloudRequestError("MP 的 115 请求限速器暂不可用", kind="contract", review=True))
        self.check()  # A host request may have started a cooldown while we queued.
        try:
            self.provider._rate_stats.record()
        except Exception:
            self._raise(CloudRequestError("MP 的 115 请求统计暂不可用", kind="contract", review=True))
        kwargs.pop("retry_limit", None)
        kwargs.pop("no_error_log", None)
        try:
            response = self.provider.session.request(method, f"{self.provider.base_url}{endpoint}", **kwargs)
        except httpx.RequestError:
            self._raise(CloudRequestError("115 网络连接中断，稍后重试", kind="network"))
        except Exception:
            self._raise(CloudRequestError("115 请求结果无法确认，稍后核对", kind="unknown"))
        if response is None:
            self._raise(CloudRequestError("115 未返回有效响应，稍后核对", kind="unknown"))
        status = getattr(response, "status_code", None)
        if not isinstance(status, int):
            self._raise(CloudRequestError("115 响应缺少状态，稍后核对", kind="unknown"))
        if status == 429:
            self._limited(response)
        if status in (401, 403):
            self._raise(CloudRequestError("115 授权失效或权限不足，请检查 MP 的 115 授权", kind="authentication"))
        if status >= 500:
            self._raise(CloudRequestError("115 服务暂不可用，稍后重试", kind="service"))
        if not 200 <= status < 300:
            self._raise(CloudRequestError("115 请求未被接受，稍后核对", kind="http"))
        try:
            data = response.json()
        except (ValueError, TypeError):
            self._raise(CloudRequestError("115 响应无法解析，稍后核对", kind="unknown"))
        if not isinstance(data, dict):
            self._raise(CloudRequestError("115 响应格式不完整，稍后核对", kind="unknown"))
        # This is the exact business-limit message recognized by MP itself.
        if "已达到当前访问上限" in str(data.get("message", "")):
            self._limited(response)
        if data.get("code") not in accepted_codes:
            self._raise(CloudRequestError("115 返回未识别的业务结果，稍后核对", kind="unknown"))
        return data.get(result_key) if result_key else data
