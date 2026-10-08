"""Content-proven recovery when a consumer has removed both handoff paths."""
from collections import defaultdict
import re

from .domain import BridgeError, cloud_path, nested


def recovery_path(path, config, routings=(), *, browse=False):
    path = cloud_path(path)
    prefix = config.cd2_prefix
    if path != prefix and not path.startswith(prefix.rstrip("/") + "/"):
        raise BridgeError("归档目录必须位于本批次的 115 挂载中", review=True)
    if not browse:
        for route in [config.routing(), *routings]:
            if route.get("cd2_address") != config.cd2_address or route.get("cd2_prefix") != prefix:
                continue
            staging = prefix.rstrip("/") + route["staging"]
            if nested(path, staging) or nested(path, route["inbox"]):
                raise BridgeError("请选择归档后的作品目录，不能使用暂存、待归档目录或其上级", review=True)
    return path


def verify_archive(entries, actual):
    """Names can change; every video and attachment needs its own SHA1/size match.

    This proves complete cloud content, not execution of Symedia's business logic.
    Missing digests never degrade to name/size-only confirmation.
    """
    if not entries:
        raise BridgeError("批次没有已封存的文件清单，无法核对归档结果", review=True)
    available = defaultdict(list)
    for path, item in actual.items():
        digest = str(item.get("sha1") or "").lower()
        if re.fullmatch(r"[0-9a-f]{40}", digest):
            available[(digest, item.get("size"))].append(path)
    matches = []
    for entry in entries:
        digest = str(entry.get("sha1") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{40}", digest):
            raise BridgeError("文件清单缺少有效 SHA1，无法核对归档结果", review=True)
        candidates = available.get((digest, entry["size"]))
        if not candidates:
            raise BridgeError("归档目录尚未核实此文件的 SHA1 和大小：" + entry["relative"], review=True)
        matches.append({"relative": entry["relative"], "archived_relative": candidates.pop(),
                        "sha1": digest, "size": entry["size"]})
    return matches
