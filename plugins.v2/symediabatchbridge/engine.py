"""One owner per batch; every external side effect follows a durable state write."""

from pathlib import Path
from threading import Event
import time

from .domain import (Awaiting, BridgeError, Config, Stopped, check_stop, child_path,
                     freeze_file, refresh_signature, unchanged, verify_tree)
from .instant import MAX_INSTANT_ATTEMPTS, RETRY_SECONDS, range_sha1


class Engine:
    def __init__(self, store, config: Config, host, cloud, stop: Event):
        self.store, self.config, self.host, self.cloud, self.stop = store, config, host, cloud, stop

    def process(self, job):
        if job["state"] == "handed_off":
            return
        try:
            check_stop(self.stop)
            if job["routing"] != self.config.routing():
                if job["routing"].get("storage") == "115网盘Plus":
                    raise BridgeError("旧批次需确认 MP 内置 115 与 CD2 使用同一账号，再切换上传接口", review=True)
                raise BridgeError("目录映射已变化，请恢复本批次的原配置后重新检查", review=True)
            if job.get("move_requested"):
                self._reconcile_move(job)
                return
            if not Path(self.config.local_root).is_dir():
                raise Awaiting("等待本地整理目录恢复：" + self.config.local_root)
            candidates = self.host.collect(job)
            self._sync_candidates(job, candidates)

            remote_batch = child_path(self.config.staging, job["id"])
            source = child_path(self.config.cd2_staging, job["id"])
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
            operations = 0
            for entry in job["files"]:
                check_stop(self.stop)
                self.config.relative(entry["local"])
                refresh_signature(entry, self.stop)
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
                if time.time() >= entry.get("instant_next_at", 0):
                    if operations >= 4:
                        break
                    operations += 1
                    self._upload_entry(job, entry, remote_file)

            pending = [e for e in job["files"] if not e["uploaded"]]
            if pending:
                job.update(state="waiting_instant", attempts=0,
                           message=f"已完成 {len(job['files']) - len(pending)}/{len(job['files'])} 个文件，等待秒传重试",
                           next_check=max(time.time() + self.config.interval * 60,
                                          min(e.get("instant_next_at", 0) for e in pending)))
                self.store.save(job)
                return

            check_stop(self.stop)
            job["state"] = "verifying"
            job["message"] = "正在核对云端文件"
            self.store.save(job)
            verify_tree(job["files"], self.cloud.tree(source))
            # Check for new native transfer results before the irreversible handoff.
            latest = self.host.collect(job)
            if not self._same_candidates(job, latest):
                self._sync_candidates(job, latest)
                raise Awaiting("已纳入新增整理文件，下次检查继续上传")
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
            job["state"] = "moving" if job.get("move_requested") else "waiting"
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

    def _upload_entry(self, job, entry, remote_file):
        now = time.time()
        # Per-file deadlines survive restart, downtime and the manual retry action.
        # Never catch up missed hourly attempts in a tight loop.
        if now < entry.get("instant_next_at", 0):
            return
        misses = entry.get("instant_misses", 0)
        if misses >= MAX_INSTANT_ATTEMPTS:
            earliest = entry["instant_started_at"] + MAX_INSTANT_ATTEMPTS * RETRY_SECONDS
            if now < earliest:
                entry["instant_next_at"] = earliest
                self.store.save(job)
                return
            job.update(state="uploading", message="秒传等待结束，正在普通上传：" + entry["relative"])
            self.store.save(job)
            receipt = self.host.upload(Path(entry["local"]), remote_file)
            check_stop(self.stop)
            unchanged(entry)
            if not receipt or receipt.get("size") != entry["size"]:
                raise BridgeError("上传未确认完成：" + entry["relative"])
            self._uploaded(job, entry, {**receipt, "method": "normal"})
            return

        # v1.0.0 sealed records have no prefix digest. Add it without resetting
        # their manifest, upload receipts or identity; already uploaded files skip it.
        if "preid" not in entry:
            from hashlib import sha1
            entry["preid"] = (range_sha1(entry["local"], 0, min(entry["size"], 128 * 1024 * 1024) - 1,
                                        entry["size"], self.stop).lower() if entry["size"] else sha1(b"").hexdigest())
            unchanged(entry)
        entry.setdefault("instant_started_at", now)
        entry["instant_next_at"] = now + RETRY_SECONDS
        entry["instant_error"] = "上次秒传结果未确认，将先核对云端文件"
        job.update(state="uploading", message=f"正在尝试秒传 {misses + 1}/{MAX_INSTANT_ATTEMPTS}：{entry['relative']}")
        # Reserve the next time BEFORE the request. Lost responses are reconciled
        # through the fresh CD2 snapshot and cannot cause immediate repeat requests.
        self.store.save(job)
        try:
            receipt = self.host.try_instant(entry, remote_file, self.stop)
            check_stop(self.stop)
            unchanged(entry)
            if receipt is not None and receipt.get("size") != entry["size"]:
                raise BridgeError("115 秒传回执大小不一致", review=True)
        except Stopped:
            raise
        except BridgeError as error:
            if error.review:
                raise
            entry["instant_error"] = str(error)
            entry.setdefault("instant_error_since", time.time())
        except Exception:
            entry["instant_error"] = "秒传接口暂不可用，请检查登录和连接"
            entry.setdefault("instant_error_since", time.time())
        else:
            entry.pop("instant_error", None)
            entry.pop("instant_error_since", None)
            if receipt is not None:
                self._uploaded(job, entry, receipt)
                return
            # Only an explicit, successful non-hit response consumes the budget.
            # Authentication/network/unknown responses cannot authorize full upload.
            entry["instant_misses"] = misses + 1
        entry["instant_next_at"] = time.time() + RETRY_SECONDS
        self.store.save(job)

    def _uploaded(self, job, entry, receipt):
        entry.update(uploaded=True, receipt=receipt)
        entry.pop("instant_error", None)
        entry.pop("instant_error_since", None)
        job["message"] = f"已上传 {sum(e['uploaded'] for e in job['files'])}/{len(job['files'])}"
        self.store.save(job)

    def _sync_candidates(self, job, candidates):
        paths = [c["local"] for c in candidates]
        if not paths or len(paths) != len(set(paths)):
            raise BridgeError("批次文件清单为空或包含重复目标", review=True)
        existing = {e["local"]: e for e in job["files"]}
        if set(existing) - set(paths):
            raise BridgeError("已有整理文件从清单移除，等待清单恢复", review=True)
        hashed = 0
        for candidate in candidates:
            check_stop(self.stop)
            path = candidate["local"]
            relative = self.config.relative(path)
            if path in existing:
                refresh_signature(existing[path], self.stop)
                continue
            if hashed >= 4:
                raise Awaiting(f"已记录 {len(job['files'])}/{len(candidates)} 个文件 HASH，下次继续")
            entry = freeze_file(path, relative, self.stop)
            source = candidate.get("source")
            if source:
                try:
                    same_file = Path(source).samefile(path)
                except OSError:
                    raise Awaiting("等待下载源文件恢复：" + Path(source).name) from None
                if not same_file:
                    original = freeze_file(source, relative, self.stop)
                    if original["size"] != entry["size"] or original["sha1"] != entry["sha1"]:
                        raise BridgeError("整理文件与本次下载内容不同，请检查同名覆盖：" + relative, review=True)
                    unchanged(original)
                unchanged(entry)
            job["files"].append(entry)
            hashed += 1
            # Persist each completed hash: a restart in a large season does not
            # discard all earlier file hashes, and new subtitles retain old receipts.
            self.store.save(job)
        job["history_ids"] = sorted(c["history_id"] for c in candidates if c.get("history_id"))
        job.update(state="uploading", message="正在尝试秒传")
        self.store.save(job)

    @staticmethod
    def _same_candidates(job, candidates):
        return ({c["local"] for c in candidates} == {e["local"] for e in job["files"]}
                and sorted(c["history_id"] for c in candidates if c.get("history_id")) == job.get("history_ids", []))

    def _reconcile_move(self, job):
        source_exists = self.cloud.exists(job["source"])
        destination_exists = self.cloud.exists(job["destination"])
        if destination_exists and not source_exists:
            verify_tree(job["files"], self.cloud.tree(job["destination"]))
            job.update(state="handed_off", message="已核实目录移交完成",
                       handed_off_at=time.time(), next_check=0, attempts=0)
            self.store.save(job)
            return
        if source_exists and not destination_exists:
            # Intent may have been saved immediately before a crash. Retry ONLY
            # this immutable, complete source directory, never upload/recreate it.
            verify_tree(job["files"], self.cloud.tree(job["source"]))
            self.cloud.require_inbox(self.config.inbox)
            check_stop(self.stop)
            if self.cloud.move_directory(job["source"], self.config.inbox):
                job.update(state="handed_off", message="已恢复整目录移交", handed_off_at=time.time(),
                           next_check=0, attempts=0)
                self.store.save(job)
                return
            raise BridgeError("CD2 尚未确认移交，稍后继续核对")
        if source_exists:
            raise BridgeError("源和目标同时存在同名批次，已暂停移交，等待冲突消除", review=True)
        raise BridgeError("移动结果待确认；目录可能已被 Symedia 消费，将继续核对，不重复上传", review=True)

    def _failure(self, job, message, review):
        job["attempts"] = job.get("attempts", 0) + 1
        job["message"] = message
        if review:
            job["state"] = "review"
            job["next_check"] = time.time() + 3600
        else:
            job["state"] = "moving" if job.get("move_requested") else "retrying"
            job["next_check"] = time.time() + min(1800, 60 * 2 ** min(job["attempts"] - 1, 5))
        self.store.save(job)
