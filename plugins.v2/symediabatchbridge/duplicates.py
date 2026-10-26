"""Recover identical staging duplicates by immutable 115 IDs, never by CD2 aliases.

The journal precedes every write. Isolation stays outside the batch/inbox, and
unknown contents never become permission to delete, overwrite or upload again.
"""
from pathlib import PurePosixPath
import re
import time

from .activity import event
from .domain import Awaiting, BridgeError, check_stop, child_path


def identifier(value):
    value = str(value or "")
    if not re.fullmatch(r"[1-9][0-9]{0,39}", value):
        raise BridgeError("115 文件身份不完整，无法安全隔离重复副本", review=True)
    return value


def matches(record, entry):
    return (record.get("size") == entry["size"] and
            record.get("sha1") == entry["sha1"] and
            bool(re.fullmatch(r"[0-9a-f]{40}", str(entry["sha1"]))))


def canonical(relative, allowed):
    if relative in allowed:
        return relative
    path = PurePosixPath(relative)
    names = [name for name in allowed if PurePosixPath(name).parent == path.parent and
             re.fullmatch(re.escape(PurePosixPath(name).stem) + r" ?\([1-9][0-9]*\)" +
                          re.escape(PurePosixPath(name).suffix), path.name)]
    return names[0] if len(names) == 1 else None


def plan(entries, records, remote, allowed):
    """Prove native/CD2 identity, scope, name and contents before any mutation."""
    native = {item["id"]: item for item in records}
    if len(native) != len(records):
        raise BridgeError("115 返回重复文件 ID，无法核实隔离范围", review=True)
    cloud = {identifier(item.get("id")): (name, item) for name, item in remote.items()}
    if len(cloud) != len(remote):
        raise BridgeError("CD2 返回重复文件 ID，无法核实隔离范围", review=True)
    if set(native) != set(cloud):
        raise Awaiting("115 与 CD2 的目录视图尚未一致，稍后重新核对重复副本")
    groups = {}
    for item in records:
        name, other = cloud[item["id"]]
        if (PurePosixPath(name).parent != PurePosixPath(item["relative"]).parent or
                other.get("size") != item["size"] or
                (other.get("sha1") and other["sha1"] != item["sha1"])):
            raise BridgeError("115 与 CD2 文件身份或内容不一致，已停止隔离", review=True)
        expected = canonical(item["relative"], allowed)
        if expected is None:
            raise BridgeError("暂存批次包含无法归属的清单外文件：" + item["relative"], review=True)
        groups.setdefault(expected, []).append(item)
    known = {entry["relative"]: entry for entry in entries}
    result = []
    for expected, group in groups.items():
        entry = known.get(expected)
        if len(group) == 1 and group[0]["relative"] == expected:
            if entry and not matches(group[0], entry):
                raise BridgeError("115 原始文件内容与本地 HASH 不一致：" + expected, review=True)
            continue
        if not entry:
            raise Awaiting("先完成本地 HASH，再核对同内容重复副本：" + expected)
        if not all(matches(item, entry) for item in group):
            raise BridgeError("同名云端文件内容不同，已停止自动隔离：" + expected, review=True)
        originals = [item for item in group if item["relative"] == expected]
        if not originals:
            raise BridgeError("原文件名缺失，不能自动推断应保留的副本：" + expected, review=True)
        receipt_id = str(entry.get("receipt", {}).get("fileid") or "")
        keeper = next((item for item in originals if item["id"] == receipt_id), None)
        # A known receipt must remain the original, not silently move to isolation.
        if receipt_id and keeper is None:
            raise BridgeError("上传回执对应的原件不在预期位置，已停止自动隔离", review=True)
        keeper = keeper or min(originals, key=lambda item: int(item["id"]))
        for item in group:
            if item["id"] != keeper["id"]:
                result.append({**item, "expected": expected, "keeper_id": keeper["id"], "state": "planned"})
    return result


class Isolation:
    def __init__(self, request, folder, config, stop):
        self.request, self.folder, self.config, self.stop = request, folder, config, stop

    def files(self, parent_id):
        parent_id = identifier(parent_id)
        result, seen, offset = [], set(), 0
        while True:
            check_stop(self.stop)
            reply = self.request("GET", "/open/ufile/files", params={
                "cid": int(parent_id), "limit": 1000, "offset": offset, "cur": True, "show_dir": 1})
            rows = reply.get("data") if isinstance(reply, dict) and reply.get("state") else None
            if not isinstance(rows, list):
                raise Awaiting("115 目录暂不可读，隔离将在连接恢复后继续")
            for row in rows:
                if not isinstance(row, dict) or row.get("fc") not in ("0", "1", 0, 1):
                    raise BridgeError("115 文件类型不明确，无法核实隔离范围", review=True)
                ident = identifier(row.get("fid"))
                name = row.get("fn")
                child_path("/", name if isinstance(name, str) else "")
                if "/" in name or ident in seen or str(row.get("pid")) != parent_id:
                    raise BridgeError("115 子文件身份或父目录不一致，已停止隔离", review=True)
                seen.add(ident)
                is_dir = str(row["fc"]) == "0"
                size = row.get("fs", 0)
                if not is_dir and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
                    raise BridgeError("115 文件大小不完整，无法核实隔离范围", review=True)
                digest = str(row.get("sha1") or "").lower()
                result.append({"id": ident, "parent_id": parent_id, "name": name,
                               "directory": is_dir, "size": size, "sha1": digest})
                if len(result) > 10000:
                    raise BridgeError("隔离核对目录超过 10000 项，请缩小批次", review=True)
            if len(rows) < 1000:
                return result
            offset += len(rows)

    def inventory(self, root_id):
        pending, seen, records = [(identifier(root_id), "", 0)], set(), []
        while pending:
            parent_id, path, depth = pending.pop()
            if parent_id in seen or depth > 24 or len(seen) >= 1000:
                raise BridgeError("115 隔离核对目录层级或身份异常", review=True)
            seen.add(parent_id)
            rows = self.files(parent_id)
            if len({row["name"] for row in rows if row["directory"]}) != sum(row["directory"] for row in rows):
                raise BridgeError("115 同名目录无法唯一对应，已停止隔离", review=True)
            for row in rows:
                relative = child_path("/", path + "/" + row["name"] if path else row["name"])[1:]
                if row["directory"]:
                    pending.append((row["id"], relative, depth + 1))
                else:
                    records.append({**row, "relative": relative})
                    if len(records) > 10000:
                        raise BridgeError("单批隔离核对超过 10000 个文件", review=True)
        return records

    def recover(self, job, root_id, remote, allowed, store):
        root_id = identifier(root_id)
        journal = job.get("duplicate_isolation")
        if journal:
            if journal.get("source_directory_id") != root_id:
                raise BridgeError("重复副本隔离期间批次目录身份变化，已暂停", review=True)
            pending = any(item.get("state") != "done" for item in journal["items"])
            self.resume(job, journal, store)
            if pending:
                return True  # Caller must obtain a fresh CD2 snapshot after moves.
        records = self.inventory(root_id)
        items = plan(job["files"], records, remote, allowed)
        if not items:
            return False
        if not journal:
            journal = {"schema": 1, "source_directory_id": root_id, "items": [], "created_at": time.time()}
            job["duplicate_isolation"] = journal
        previous = {item["id"] for item in journal["items"]}
        if previous & {item["id"] for item in items}:
            raise BridgeError("已隔离副本重新出现在原批次，已停止循环处理", review=True)
        for item in items:
            item["destination"] = child_path(child_path(child_path(self.config.staging,
                "_重复文件隔离"), job["id"]), item["id"])
        journal["items"].extend(items)
        job.update(state="isolating", message=f"已核实 {len(items)} 个同内容重复副本，正在隔离保留")
        store.save(job)  # Entire immutable plan precedes folder creation and move.
        self.resume(job, journal, store)
        return True

    def resume(self, job, journal, store):
        operations = 0
        for item in journal["items"]:
            if item.get("state") == "done":
                continue
            check_stop(self.stop)
            if operations >= 4:
                raise Awaiting("重复副本隔离已保存进度，下轮继续")
            source = {row["id"]: row for row in self.files(item["parent_id"])}
            entry = next((entry for entry in job["files"] if entry["relative"] == item["expected"]), None)
            if not entry or not matches(item, entry):
                raise BridgeError("隔离记录与原文件清单不一致，已停止操作", review=True)
            keeper = source.get(item["keeper_id"])
            if not keeper or keeper["directory"] or not matches(keeper, entry):
                raise Awaiting("等待核实原件身份，重复副本隔离进度已保留")
            expected_target = child_path(child_path(child_path(self.config.staging,
                "_重复文件隔离"), job["id"]), item["id"])
            if item["destination"] != expected_target:
                raise BridgeError("隔离目标不在本批次专属暂存范围", review=True)
            if not item.get("destination_id"):
                folder = self.folder(item["destination"])
                item["destination_id"] = identifier(folder)
                if item["destination_id"] in (item["parent_id"], journal["source_directory_id"]):
                    raise BridgeError("隔离目标身份与原目录冲突", review=True)
                store.save(job)
            target = {row["id"]: row for row in self.files(item["destination_id"])}
            if set(target) - {item["id"]}:
                raise BridgeError("隔离目录出现其他文件，已停止覆盖", review=True)
            current = source.get(item["id"])
            moved = target.get(item["id"])
            if moved:
                if moved["directory"] or not matches(moved, entry):
                    raise BridgeError("隔离结果身份或内容冲突，已暂停核对", review=True)
                if current:
                    if current["directory"] or not matches(current, entry):
                        raise BridgeError("隔离两侧同一 ID 的内容不一致，已停止操作", review=True)
                    raise Awaiting("隔离两侧目录视图正在同步，稍后按文件 ID 自动核对")
            elif current:
                if current["directory"] or current["name"] != item["name"] or not matches(current, entry):
                    raise BridgeError("重复副本内容或身份发生变化，已停止隔离", review=True)
                item.setdefault("first_move_requested_at", time.time())
                item.update(state="moving", move_requested_at=time.time(),
                            move_requests=item.get("move_requests", 0) + 1)
                store.save(job)
                # Same-drive movement by ID is idempotent; a retry never creates a file.
                try:
                    self.request("POST", "/open/ufile/move", data={
                        "file_ids": int(item["id"]), "to_cid": int(item["destination_id"])})
                except BridgeError as error:
                    store.record(job, event("duplicate_move_pending", "隔离移动等待恢复：" + str(error),
                                            file=item["relative"], level="warning"))
                    raise
                operations += 1
                target = {row["id"]: row for row in self.files(item["destination_id"])}
                source = {row["id"]: row for row in self.files(item["parent_id"])}
                moved = target.get(item["id"])
                if (item["id"] in source or set(target) != {item["id"]} or not moved or
                        moved["directory"] or not matches(moved, entry)):
                    raise Awaiting("重复副本隔离回执尚未一致，稍后按文件 ID 恢复核对")
            else:
                raise Awaiting("重复副本移动结果未确认，保留隔离记录等待恢复")
            item.update(state="done", confirmed_at=time.time())
            store.save(job)
            store.record(job, event("duplicate_isolated", "同内容重复副本已按文件 ID 隔离保留",
                                    file=item["relative"]))
        journal["verified_at"] = time.time()
        store.save(job)
