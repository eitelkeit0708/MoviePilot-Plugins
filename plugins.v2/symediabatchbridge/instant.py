"""MP native 115 initialization only. Never sends file contents to OSS.

MP's ordinary upload method combines instant initialization and an OSS fallback.
Keep the small native initialization contract here; unknown responses
must raise instead of being counted as a miss or invoking ordinary upload.
"""

from hashlib import sha1
from pathlib import Path
import time

from .domain import BridgeError, check_stop


MAX_INSTANT_ATTEMPTS = 24
RETRY_SECONDS = 3600


def range_sha1(path, start, end, size, stop):
    if not 0 <= start <= end < size:
        raise BridgeError("115 返回的文件校验范围无效")
    digest = sha1()
    with Path(path).open("rb") as stream:
        stream.seek(start)
        remaining = end - start + 1
        while remaining:
            check_stop(stop)
            chunk = stream.read(min(4 * 1024 * 1024, remaining))
            if not chunk:
                raise BridgeError("文件校验读取不完整", review=True)
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest().upper()


def native_provider():
    from app.modules.filemanager.storages.u115 import U115Pan
    return U115Pan()


def try_instant(provider, parent_id, entry, stop):
    check_stop(stop)
    if not str(parent_id or "").isdigit():
        raise BridgeError("115 暂存目录缺少有效 ID", review=True)
    request = getattr(provider, "_request_api", None)
    if not callable(request):
        raise BridgeError("当前 MP 版本不支持此秒传接口，请更新 MP V2", review=True)
    # Avoid entering the host's one-hour blocking cooldown inside our worker.
    if getattr(provider, "_limit_until", 0) > time.time():
        raise BridgeError("MP 的 115 接口正在冷却，稍后重试")
    payload = {"file_name": Path(entry["local"]).name, "file_size": entry["size"],
               "target": "U_1_" + str(parent_id), "fileid": entry["sha1"], "preid": entry["preid"]}

    def initialize():
        check_stop(stop)
        response = request("POST", "/open/upload/init", data=payload, retry_limit=0)
        if not isinstance(response, dict) or not response.get("state") or not isinstance(response.get("data"), dict):
            raise BridgeError("115 秒传请求未成功，请检查 MP 的登录和连接")
        return response["data"]

    result = initialize()
    if result.get("code") in (700, 701):
        try:
            start, end = map(int, result["sign_check"].split("-"))
            key = result["sign_key"]
        except (KeyError, TypeError, ValueError):
            raise BridgeError("115 秒传校验响应不完整") from None
        payload.update(pick_code=result.get("pick_code"), sign_key=key,
                       sign_val=range_sha1(entry["local"], start, end, entry["size"], stop))
        result = initialize()
    if result.get("status") == 2:
        return {"size": entry["size"], "fileid": str(result.get("file_id") or ""), "method": "instant"}
    if result.get("status") == 1 and all(result.get(k) for k in ("bucket", "object", "callback")):
        return None
    raise BridgeError("115 秒传结果无法确认，稍后重试")
