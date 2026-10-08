"""One owner per batch; every external side effect follows a durable state write."""

from pathlib import Path
from threading import Event
import time

from .domain import (Awaiting, BridgeError, Config, Stopped, check_stop, child_path,
                     freeze_file, unchanged, verify_tree)


class Engine:
    def __init__(self, store, config: Config, host, cloud, stop: Event):
        self.store, self.config, self.host, self.cloud, self.stop = store, config, host, cloud, stop

    def process(self, job):
        if job["state"] in ("handed_off", "review"):
            return
        try:
            check_stop(self.stop)
            if job["routing"] != self.config.routing():
                raise BridgeError("目录映射已变化，请恢复本批次的原配置后重新检查", review=True)
            if job.get("move_requested"):
                self._reconcile_move(job)
                return
            candidates = self.host.collect(job)
            if not job["files"]:
                entries = []
                for candidate in candidates:
                    check_stop(self.stop)
                    relative = self.config.relative(candidate["local"])
                    entries.append(freeze_file(candidate["local"], relative, self.stop))
                if not entries or len({e["relative"] for e in entries}) != len(entries):
                    raise BridgeError("批次文件清单为空或包含重复目标", review=True)
                job["files"] = entries
                job["history_ids"] = sorted(c["history_id"] for c in candidates if c.get("history_id"))
                job["state"] = "uploading"
                job["message"] = "正在上传"
                self.store.save(job)
            elif not self._same_candidates(job, candidates):
                raise BridgeError("封存后的整理清单发生变化，需核对新增或替换的文件", review=True)

            remote_batch = child_path(self.config.staging, job["id"])
            source = child_path(self.config.cd2_prefix + self.config.staging, job["id"])
            destination = child_path(self.config.inbox, job["id"])
            # Never upload over an existing destination belonging to an uncertain handoff.
            if self.cloud.exists(destination):
                raise BridgeError("待归档目录已存在同名批次，请核对移动记录", review=True)
            self.cloud.require_inbox(self.config.inbox)
            # One fresh batch snapshot for resume checks. Re-listing every ancestor for
            # every file would multiply CD2/115 requests for an entire TV season.
            previous_files = self.cloud.tree(source) if self.cloud.exists(source) else {}
            if set(previous_files) - {e["relative"] for e in job["files"]}:
                raise BridgeError("暂存批次出现清单外文件，已停止上传和移交", review=True)
            for entry in job["files"]:
                check_stop(self.stop)
                self.config.relative(entry["local"])
                unchanged(entry)
                if entry["uploaded"]:
                    continue
                remote_file = child_path(remote_batch, entry["relative"])
                # A crash can occur after upload but before its receipt is saved. Check CD2
                # using a fresh listing; do not trust the uploader's positive path cache.
                previous = previous_files.get(entry["relative"])
                if previous is not None:
                    if previous.get("sha1") == entry["sha1"] and previous.get("size") == entry["size"]:
                        entry["uploaded"] = True
                        self.store.save(job)
                        continue
                    raise BridgeError("云端存在未确认的同名文件，已停止覆盖：" + entry["relative"], review=True)
                receipt = self.host.upload(Path(entry["local"]), remote_file)
                check_stop(self.stop)
                unchanged(entry)
                if not receipt or receipt.get("size") != entry["size"]:
                    raise BridgeError("上传未确认完成：" + entry["relative"])
                entry["uploaded"] = True
                entry["receipt"] = receipt
                job["message"] = f"已上传 {sum(e['uploaded'] for e in job['files'])}/{len(job['files'])}"
                self.store.save(job)

            check_stop(self.stop)
            job["state"] = "verifying"
            job["message"] = "正在核对云端文件"
            self.store.save(job)
            verify_tree(job["files"], self.cloud.tree(source))
            # Check for new native transfer results before the irreversible handoff.
            if not self._same_candidates(job, self.host.collect(job)):
                raise BridgeError("上传期间整理清单发生变化，已暂停移交", review=True)
            for entry in job["files"]:
                self.config.relative(entry["local"])
                unchanged(entry)
            check_stop(self.stop)
            if self.cloud.exists(destination):
                raise BridgeError("待归档目录出现同名批次，已停止移交", review=True)
            job.update(state="moving", move_requested=True, source=source, destination=destination,
                       message="正在整目录移交", next_check=0)
            self.store.save(job)
            # Persist the intent BEFORE the RPC. Any lost response is reconciled, never
            # blindly replayed, even if Symedia has already consumed the entire folder.
            result = self.cloud.move_directory(source, self.config.inbox)
            if result:
                job.update(state="handed_off", message="已移交 Symedia 待归档目录",
                           handed_off_at=time.time(), next_check=0, attempts=0)
                self.store.save(job)
            else:
                raise BridgeError("CD2 未确认移动成功，等待核对结果")
        except Stopped:
            return
        except Awaiting as error:
            job["message"] = str(error)
            job["next_check"] = time.time() + self.config.interval * 60
            self.store.save(job)
        except BridgeError as error:
            if not self.stop.is_set():
                self._failure(job, str(error), error.review)
        except Exception:
            # SDK exceptions may contain authenticated URLs/tokens. Never persist or
            # return the raw exception through the plugin UI or notifications.
            if not self.stop.is_set():
                self._failure(job, "外部服务暂不可用，请检查 MP 储存或 CD2 连接", False)

    @staticmethod
    def _same_candidates(job, candidates):
        return ({c["local"] for c in candidates} == {e["local"] for e in job["files"]}
                and sorted(c["history_id"] for c in candidates if c.get("history_id")) == job.get("history_ids", []))

    def _reconcile_move(self, job):
        if self.cloud.exists(job["destination"]) and not self.cloud.exists(job["source"]):
            verify_tree(job["files"], self.cloud.tree(job["destination"]))
            job.update(state="handed_off", message="已核实目录移交完成",
                       handed_off_at=time.time(), next_check=0, attempts=0)
            self.store.save(job)
            return
        raise BridgeError("移动结果待确认；源目录消失也可能已被 Symedia 处理，不会重复上传或移动", review=True)

    def _failure(self, job, message, review):
        job["attempts"] = job.get("attempts", 0) + 1
        job["message"] = message
        if review or job["attempts"] >= 10:
            job["state"] = "review"
            job["next_check"] = 0
        else:
            job["next_check"] = time.time() + min(1800, 60 * 2 ** min(job["attempts"] - 1, 5))
        self.store.save(job)
