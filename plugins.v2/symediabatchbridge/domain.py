"""Batch contracts; no MoviePilot or network imports."""

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path, PurePosixPath
from threading import Event
from urllib.parse import urlsplit
import re


class BridgeError(Exception):
    def __init__(self, message: str, *, review: bool = False):
        super().__init__(message)
        self.review = review


class Stopped(Exception):
    pass


class Awaiting(BridgeError):
    """Normal unfinished work, not a failed network attempt."""


def check_stop(stop: Event):
    if stop.is_set():
        raise Stopped()


def cloud_path(value: str) -> str:
    value = str(value or "").strip()
    if not value.startswith("/") or "\\" in value or "\x00" in value:
        raise ValueError("云端目录必须使用 / 开头的绝对路径")
    if any(p in (".", "..") for p in value.split("/")):
        raise ValueError("目录不能包含 . 或 ..")
    return str(PurePosixPath(value))


def child_path(root: str, relative: str) -> str:
    parts = relative.split("/")
    if not relative or relative.startswith("/") or "\\" in relative or "\x00" in relative:
        raise BridgeError("文件相对路径无效", review=True)
    if any(p in ("", ".", "..") for p in parts):
        raise BridgeError("文件路径越出批次目录", review=True)
    return str(PurePosixPath(root).joinpath(*parts))


def nested(a: str, b: str) -> bool:
    return a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")


@dataclass(frozen=True)
class Config:
    local_root: str
    storage: str
    staging: str
    cd2_prefix: str
    inbox: str
    cd2_address: str
    cd2_token: str
    interval: int = 1
    name: str = "默认路线"

    @classmethod
    def parse(cls, values: dict):
        local = Path(str(values.get("local_root") or ""))
        if not values.get("local_root") or not local.is_absolute() or ".." in local.parts:
            raise ValueError("请填写 MP 容器内的本地整理目录绝对路径")
        # A temporarily unavailable mount must not disable the scheduler forever.
        # Availability is checked again before every batch, not only at startup.
        local = local.resolve()
        if local == Path(local.anchor):
            raise ValueError("本地整理目录不能是文件系统根目录")
        # v1.1 uses MP's native 115 only. Existing jobs retain their original
        # routing and need an explicit account confirmation before switching.
        storage = "u115"
        staging = cloud_path(values.get("staging", ""))
        prefix = cloud_path(values.get("cd2_prefix", ""))
        inbox = cloud_path(values.get("inbox", ""))
        if staging == "/" or inbox == prefix:
            raise ValueError("请使用独立暂存目录和 CD2 中的具体 115 挂载目录")
        mapped = prefix.rstrip("/") + staging
        if not inbox.startswith(prefix.rstrip("/") + "/") or nested(mapped, inbox):
            raise ValueError("暂存和待归档目录必须位于同一 115 挂载下，且互不包含")
        address = str(values.get("cd2_address") or "").strip()
        url = urlsplit(address if "://" in address else "http://" + address)
        if url.scheme not in ("http", "https") or not url.hostname or not url.port:
            raise ValueError("CD2 地址需包含主机和 gRPC 端口，例如 http://cd2:19798")
        if url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise ValueError("CD2 地址不能包含账号、查询参数或子路径")
        token = str(values.get("cd2_token") or "").strip()
        if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("请填写有效的 CD2 API 令牌")
        interval = int(values.get("interval", 1))
        if not 1 <= interval <= 60:
            raise ValueError("检查间隔应为 1–60 分钟")
        return cls(str(local), storage, staging, prefix, inbox, address, token, interval,
                   str(values.get("name") or values.get("route_name") or "默认路线").strip()[:80])

    @classmethod
    def routes(cls, values: dict):
        count = int(values.get("route_count", 1))
        if not 1 <= count <= 16:
            raise ValueError("目录映射数量应为 1–16")
        routes = []
        for index in range(1, count + 1):
            prefix = "" if index == 1 else f"route_{index}_"
            route_values = {**values, **{key: values.get(prefix + key, "")
                            for key in ("local_root", "staging", "inbox")},
                            "name": values.get(prefix + "route_name", "默认路线" if index == 1 else f"路线 {index}")}
            routes.append(cls.parse(route_values))
        for i, route in enumerate(routes):
            for other in routes[i + 1:]:
                if nested(Path(route.local_root).as_posix(), Path(other.local_root).as_posix()):
                    raise ValueError(f"{route.name} 与 {other.name} 的本地目录重叠，请分开设置")
            # Every staging tree must be outside EVERY destination tree, otherwise
            # another route's Symedia watcher could consume incomplete uploads.
            for other in routes:
                if nested(route.cd2_staging, other.inbox):
                    raise ValueError(f"{route.name} 的暂存目录与 {other.name} 的待归档目录重叠")
        return tuple(routes)

    def pinned(self, routing: dict):
        if (routing.get("cd2_address") != self.cd2_address
                or routing.get("cd2_prefix") != self.cd2_prefix):
            raise BridgeError("CD2 连接或挂载已变化，等待恢复本批次的原连接", review=True)
        if routing.get("storage") != "u115":
            raise BridgeError("旧批次需确认 MP 内置 115 与 CD2 使用同一账号，再切换上传接口", review=True)
        pinned = self.parse({**routing, "cd2_token": self.cd2_token,
                             "interval": self.interval, "name": self.name})
        if pinned.routing() != routing:
            raise BridgeError("原目录解析结果已变化，已暂停本批次", review=True)
        return pinned

    def routing(self) -> dict:
        # Credentials may rotate without changing an existing batch destination.
        return {key: getattr(self, key) for key in
                ("local_root", "storage", "staging", "cd2_prefix", "inbox", "cd2_address")}

    @property
    def cd2_staging(self):
        # API tokens rooted at the 115 drive expose that drive directly as '/'.
        return self.cd2_prefix.rstrip("/") + self.staging

    def relative(self, value: str) -> str:
        path = Path(value)
        root = Path(self.local_root)
        try:
            relative = path.relative_to(root)
            path.resolve(strict=True).relative_to(root)
        except (ValueError, OSError):
            raise BridgeError("整理文件不存在或不在配置的本地目录中", review=True)
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise BridgeError("整理文件包含符号链接，请使用实际本地目录", review=True)
        if not path.is_file():
            raise BridgeError("当前仅支持已整理的媒体文件及附件", review=True)
        rel = relative.as_posix()
        child_path("/", rel)
        return rel


def file_signature(path: Path) -> list:
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino]


def freeze_file(path: str, relative: str, stop: Event, progress=None) -> dict:
    source = Path(path)
    before = file_signature(source)
    digest, prefix = sha1(), sha1()
    prefix_remaining = 128 * 1024 * 1024
    processed = 0
    if progress:
        progress(0, before[0])
    with source.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            check_stop(stop)
            digest.update(block)
            if prefix_remaining:
                prefix.update(block[:prefix_remaining])
                prefix_remaining = max(0, prefix_remaining - len(block))
            processed += len(block)
            if progress:
                progress(processed, before[0])
    if before != file_signature(source):
        raise Awaiting("文件仍在变化，稍后重新核对")
    return {"local": path, "relative": relative, "size": before[0],
            "signature": before, "sha1": digest.hexdigest(), "preid": prefix.hexdigest(), "uploaded": False}


def unchanged(entry: dict):
    try:
        same = file_signature(Path(entry["local"])) == entry["signature"]
    except OSError:
        same = False
    if not same:
        raise BridgeError("批次封存后本地文件被修改或移除，已停止交付", review=True)


def refresh_signature(entry: dict, stop: Event):
    """Recover identical restored files / hardlink metadata changes, never new content."""
    try:
        signature = file_signature(Path(entry["local"]))
    except OSError:
        raise Awaiting("等待本地整理文件恢复：" + entry["relative"]) from None
    if signature == entry["signature"]:
        return
    if signature != entry.get("rejected_signature") and signature[0] == entry["size"]:
        verified = freeze_file(entry["local"], entry["relative"], stop)
        if verified["sha1"] == entry["sha1"]:
            entry["signature"] = verified["signature"]
            entry.pop("rejected_signature", None)
            return
    entry["rejected_signature"] = signature
    raise BridgeError("本地文件内容被替换，等待原文件恢复：" + entry["relative"], review=True)


def verify_tree(entries: list, remote: dict):
    expected = {e["relative"]: e for e in entries}
    if set(remote) != set(expected):
        missing = len(set(expected) - set(remote))
        extra = len(set(remote) - set(expected))
        raise BridgeError(f"云端清单尚未一致：缺少 {missing} 个，多出 {extra} 个", review=bool(extra))
    for relative, entry in expected.items():
        item = remote[relative]
        if item.get("size") != entry["size"]:
            raise BridgeError(f"云端文件大小不一致：{relative}")
        remote_hash = str(item.get("sha1") or "").lower()
        if remote_hash and remote_hash != entry["sha1"]:
            raise BridgeError(f"云端文件校验不一致：{relative}", review=True)


def batch_name(instance: str, unique_id: str) -> str:
    prefix = re.sub(r"[^a-z0-9_-]", "", instance.lower())[:40] or "bridge"
    return f"{prefix}-{unique_id}"
