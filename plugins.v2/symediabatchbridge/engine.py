"""One owner per batch; every external side effect follows a durable state write."""

from pathlib import Path
from threading import Event
import time

from .domain import (Awaiting, BridgeError, Config, Stopped, check_stop, child_path,
                     freeze_file, refresh_signature, unchanged, verify_tree)
from .instant import MAX_INSTANT_ATTEMPTS, RETRY_SECONDS, range_sha1
from .activity import event
from .cooldown import CloudCooldown, CloudRequestError
from .recovery import recovery_path, verify_archive


class Engine:
    def __init__(self, store, config: Config, host, cloud, stop: Event, protected_routings=()):
        self.store, self.config, self.host, self.cloud, self.stop = store, config, host, cloud, stop
        self.protected_routings = protected_routings

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
            remote_batch = child_path(self.config.staging, job["id"])
            source = child_path(self.config.cd2_staging, job["id"])
            destination = child_path(self.config.inbox, job["id"])
            previous_files = None
            operations = 0
            missing = False
            cloud_error = None
            initial_hashes = len(job["files"])

            def ready(entry):
                nonlocal previous_files, operations, missing, cloud_error
                if cloud_error is not None:
                    return
                try:
                    check_stop(self.stop)
                    if previous_files is None:
                        # Snapshot the complete candidate manifest, including files
                        # whose HASH is still pending. Never trust cached upload paths.
                        if self._cloud_call("exists", destination):
                            raise BridgeError("待归档目录已存在同名批次，请核对移动记录", review=True)
                        self._cloud_call("require_inbox", self.config.inbox)
                        previous_files = self._cloud_call("tree", source) if self._cloud_call("exists", source) else {}
                        if set(previous_files) - {self.config.relative(c["local"]) for c in candidates}:
                            raise BridgeError("暂存批次出现清单外文件，已停止上传和移交", review=True)
                    used, absent = self._upload_ready(job, entry, remote_batch, previous_files, operations < 4)
                    operations += used
                    missing = missing or absent
                except Stopped:
                    raise
                except BridgeError as error:
                    if error.review:
                        raise
                    cloud_error = error
                except Exception:
                    cloud_error = BridgeError("外部服务暂不可用，请检查 MP 储存或 CD2 连接")

            # Cached HASH entries go first. Each newly sealed file can then upload
            # immediately, without waiting for every other file in the batch.
            # A cloud outage pauses cloud work only; bounded local hashing continues.
            self._sync_candidates(job, candidates, on_ready=ready)
            if cloud_error is not None:
                raise cloud_error

            if missing:
                raise Awaiting("等待再次核对云端暂存文件，尚未移交")
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
            directory_id = self._cloud_call("directory_id", source)
            verify_tree(job["files"], self._cloud_call("tree", source))
            # Check for new native transfer results before the irreversible handoff.
            latest = self.host.collect(job)
            if not self._same_candidates(job, latest):
                self._sync_candidates(job, latest, hash_limit=max(0, 4 - (len(job["files"]) - initial_hashes)))
                raise Awaiting("已纳入新增整理文件，下次检查继续上传")
            for entry in job["files"]:
                self.config.relative(entry["local"])
                unchanged(entry)
            check_stop(self.stop)
            if self._cloud_call("exists", destination):
                raise BridgeError("待归档目录出现同名批次，已停止移交", review=True)
            if directory_id and self._cloud_call("directory_id", source) != directory_id:
                raise BridgeError("核对期间批次目录身份发生变化，已暂停移交", review=True)
            job.update(state="moving", move_requested=True, source=source, destination=destination,
                       source_directory_id=directory_id, message="正在整目录移交", next_check=0)
            self.store.save(job)
            # Persist the intent BEFORE the RPC. Any lost response is reconciled, never
            # blindly replayed, even if Symedia has already consumed the entire folder.
            result = self._cloud_call("move_directory", source, self.config.inbox)
            if result:
                job.update(state="handed_off", message="已移交 Symedia 待归档目录",
                           handed_off_at=time.time(), next_check=0, attempts=0)
                self.store.save(job)
            else:
                raise BridgeError("CD2 未确认移动成功，等待核对结果")
        except Stopped:
            return
        except CloudCooldown as error:
            job.update(state="moving" if job.get("move_requested") else "waiting",
                       message=str(error), next_check=max(time.time() + self.config.interval * 60, error.next_at))
            self.store.save(job)
        except Awaiting as error:
            job["state"] = "moving" if job.get("move_requested") else "waiting"
            job["message"] = str(error)
            job["next_check"] = time.time() + self.config.interval * 60
            self.store.save(job)
        except CloudRequestError as error:
            if not self.stop.is_set():
                self._failure(job, str(error), error.review)
                if error.next_at > job["next_check"]:
                    job["next_check"] = error.next_at
                    self.store.save(job)
        except BridgeError as error:
            if not self.stop.is_set():
                self._failure(job, str(error), error.review)
        except Exception:
            # SDK exceptions may contain authenticated URLs/tokens. Never persist or
            # return the raw exception through the plugin UI or notifications.
            if not self.stop.is_set():
                self._failure(job, "外部服务暂不可用，请检查 MP 储存或 CD2 连接", False)

    def _gate_cloud(self):
        gate = getattr(self.host, "gate_cloud", None)
        if callable(gate):
            gate()

    def _cloud_call(self, method, *args):
        self._gate_cloud()
        return getattr(self.cloud, method)(*args)

    def _upload_ready(self, job, entry, remote_batch, previous_files, can_upload):
        """Reconcile one sealed file; return (upload operations, awaiting visibility)."""
        previous = previous_files.get(entry["relative"])
        if entry["uploaded"]:
            if previous is not None:
                verify_tree([entry], {entry["relative"]: previous})
                entry.pop("remote_missing_at", None)
                return 0, False
            # A successful upload can precede CD2 visibility. Require two fresh,
            # separated observations before invalidating its durable receipt.
            now = time.time()
            if "remote_missing_at" not in entry:
                entry["remote_missing_at"] = now
                self.store.save(job)
            if now - entry["remote_missing_at"] < max(60, self.config.interval * 60):
                return 0, True
            entry.update(uploaded=False, repair_count=entry.get("repair_count", 0) + 1)
            entry["previous_receipt"] = entry.pop("receipt", {})
            entry.pop("remote_missing_at", None)
            self.store.save(job)
            self.store.record(job, event("remote_missing", "云端暂存文件缺失，已安排补传",
                                        file=entry["relative"], level="warning"))
        # A crash after upload but before saving the receipt must not duplicate it.
        if previous is not None:
            if previous.get("sha1") == entry["sha1"] and previous.get("size") == entry["size"]:
                entry["uploaded"] = True
                self.store.save(job)
                return 0, False
            raise BridgeError("云端存在未确认的同名文件，已停止覆盖：" + entry["relative"], review=True)
        if can_upload and time.time() >= entry.get("instant_next_at", 0):
            self._upload_entry(job, entry, child_path(remote_batch, entry["relative"]))
            return 1, False
        return 0, False

    def _upload_entry(self, job, entry, remote_file):
        self._gate_cloud()
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
            entry.setdefault("normal_started_at", now)
            entry["normal_requests"] = entry.get("normal_requests", 0) + 1
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
        entry["instant_requests"] = entry.get("instant_requests", 0) + 1
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
        except (CloudCooldown, CloudRequestError) as error:
            entry["instant_error"] = str(error)
            entry["instant_error_kind"] = error.kind
            entry.setdefault("instant_error_since", time.time())
            entry["instant_next_at"] = max(time.time() + RETRY_SECONDS, error.next_at)
            error.next_at = entry["instant_next_at"]
            entry["instant_result_at"] = time.time()
            self.store.save(job)
            # A classified cloud failure stops this round's cloud work. HASH work
            # can still continue, and an unsuccessful request is never a non-hit.
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
            entry.pop("instant_error_kind", None)
            entry.pop("instant_error_since", None)
            if receipt is not None:
                self._uploaded(job, entry, receipt)
                return
            # Only an explicit, successful non-hit response consumes the budget.
            # Authentication/network/unknown responses cannot authorize full upload.
            entry["instant_misses"] = misses + 1
        entry["instant_next_at"] = time.time() + RETRY_SECONDS
        entry["instant_result_at"] = time.time()
        self.store.save(job)

    def _uploaded(self, job, entry, receipt):
        entry.update(uploaded=True, receipt=receipt)
        entry.pop("instant_error", None)
        entry.pop("instant_error_kind", None)
        entry.pop("instant_error_since", None)
        job["message"] = f"已上传 {sum(e['uploaded'] for e in job['files'])}/{len(job['files'])}"
        self.store.save(job)

    def _sync_candidates(self, job, candidates, on_ready=None, hash_limit=4):
        paths = [c["local"] for c in candidates]
        if not paths or len(paths) != len(set(paths)):
            raise BridgeError("批次文件清单为空或包含重复目标", review=True)
        existing = {e["local"]: e for e in job["files"]}
        if set(existing) - set(paths):
            raise BridgeError("已有整理文件从清单移除，等待清单恢复", review=True)
        # Snapshot all selected transfer metadata before hashing; resumed files gain
        # media context without losing HASH, receipts or retry deadlines.
        job["media_files"] = [{**c.get("media", {}), "file": self.config.relative(c["local"])}
                              for c in candidates]
        # Keep all mutations on the single batch worker. The callback does not own
        # another snapshot and cannot overwrite receipts from a concurrent writer.
        for entry in existing.values():
            check_stop(self.stop)
            refresh_signature(entry, self.stop)
            if on_ready:
                on_ready(entry)
        hashed = 0
        for candidate in candidates:
            check_stop(self.stop)
            path = candidate["local"]
            relative = self.config.relative(path)
            if path in existing:
                continue
            if hashed >= hash_limit:
                raise Awaiting(f"已记录 {len(job['files'])}/{len(candidates)} 个文件 HASH，下次继续")
            entry = self._hash_file(job, path, relative)
            snapshot = candidate.get("inventory_signature")
            if snapshot and any(entry["signature"][i] != snapshot[i] for i in (0, 1, 3, 4)):
                raise BridgeError("存量文件在接管后发生变化，已暂停处理：" + relative, review=True)
            cleanup_source = candidate.get("cleanup_source")
            if cleanup_source:
                try:
                    if Path(cleanup_source).samefile(path):
                        entry["download_source"] = cleanup_source
                except OSError:
                    pass  # Seeding originals may legitimately have expired.
            source = candidate.get("source")
            if source:
                entry["download_source"] = source
                try:
                    same_file = Path(source).samefile(path)
                except OSError:
                    raise Awaiting("等待下载源文件恢复：" + Path(source).name) from None
                if not same_file:
                    original = self._hash_file(job, source, relative, source=True)
                    if original["size"] != entry["size"] or original["sha1"] != entry["sha1"]:
                        raise BridgeError("整理文件与本次下载内容不同，请检查同名覆盖：" + relative, review=True)
                    unchanged(original)
                unchanged(entry)
            job["files"].append(entry)
            hashed += 1
            job.pop("hash_progress", None)
            # Persist each completed hash: a restart in a large season does not
            # discard all earlier file hashes, and new subtitles retain old receipts.
            self.store.save(job)
            if on_ready:
                on_ready(entry)
        job["history_ids"] = sorted(c["history_id"] for c in candidates if c.get("history_id"))
        job.pop("hash_progress", None)
        job.update(state="uploading", message="正在尝试秒传")
        self.store.save(job)

    def _hash_file(self, job, path, relative, source=False):
        last_saved = last_log = None
        action = "核对下载源 HASH" if source else "计算 HASH"
        def progress(done, total):
            nonlocal last_saved, last_log
            now = time.monotonic()
            if last_saved is not None and done != total and now - last_saved < 5:
                return
            job.update(state="hashing", message="正在" + action + "：" + relative, next_check=0,
                       hash_progress={"file": relative, "done": done, "total": total, "at": time.time()})
            self.store.save(job)
            last_saved = now
            if last_log is not None and now - last_log >= 60:
                self.store.record(job, event("hash_progress", f"{action} {done * 100 / max(total, 1):.1f}%", file=relative))
                last_log = now
            elif last_log is None:
                last_log = now
        return freeze_file(path, relative, self.stop, progress=progress)

    @staticmethod
    def _same_candidates(job, candidates):
        return ({c["local"] for c in candidates} == {e["local"] for e in job["files"]}
                and sorted(c["history_id"] for c in candidates if c.get("history_id")) == job.get("history_ids", []))

    def _reconcile_move(self, job):
        source_exists = self._cloud_call("exists", job["source"])
        destination_exists = self._cloud_call("exists", job["destination"])
        if destination_exists and not source_exists:
            directory_id = job.get("source_directory_id")
            if directory_id:
                if self._cloud_call("directory_id", job["destination"]) != directory_id:
                    raise BridgeError("目标目录身份与移交前不同，不能确认本批次已移交", review=True)
                # The entire verified source directory has arrived. Its consumer
                # may already be moving children away; do not require them to stay.
                check_stop(self.stop)
                job.update(state="handed_off", message="已通过目录 ID 核实整批移交完成",
                           completion_basis="directory_identity", handed_off_at=time.time(),
                           next_check=0, attempts=0)
                self.store.save(job)
                return
            verify_tree(job["files"], self._cloud_call("tree", job["destination"]))
            job.update(state="handed_off", message="已核实目录移交完成",
                       handed_off_at=time.time(), next_check=0, attempts=0)
            self.store.save(job)
            return
        if source_exists and not destination_exists:
            # Intent may have been saved immediately before a crash. Retry ONLY
            # this immutable, complete source directory, never upload/recreate it.
            directory_id = self._cloud_call("directory_id", job["source"])
            if job.get("source_directory_id") and directory_id != job["source_directory_id"]:
                raise BridgeError("源目录身份与移交前不同，已停止重试移动", review=True)
            verify_tree(job["files"], self._cloud_call("tree", job["source"]))
            self._cloud_call("require_inbox", self.config.inbox)
            check_stop(self.stop)
            if directory_id and self._cloud_call("directory_id", job["source"]) != directory_id:
                raise BridgeError("核对期间批次目录身份发生变化，已暂停移交", review=True)
            # Older jobs can gain identity evidence ONLY while their complete
            # source is still present, never from an already empty destination.
            if directory_id and not job.get("source_directory_id"):
                job["source_directory_id"] = directory_id
                self.store.save(job)
            if self._cloud_call("move_directory", job["source"], self.config.inbox):
                job.update(state="handed_off", message="已恢复整目录移交", handed_off_at=time.time(),
                           next_check=0, attempts=0)
                self.store.save(job)
                return
            raise BridgeError("CD2 尚未确认移交，稍后继续核对")
        if source_exists:
            raise BridgeError("源和目标同时存在同名批次，已暂停移交，等待冲突消除", review=True)
        if job.get("recovery_directory"):
            path = recovery_path(job["recovery_directory"], self.config,
                                 [*self.protected_routings, *self.store.pending_routings()])
            matches = verify_archive(job["files"], self._cloud_call("tree", path))
            check_stop(self.stop)
            job.update(state="handed_off", message="已核实归档目录中的全部视频与附件",
                       completion_basis="archive_verified", archive_matches=matches,
                       handed_off_at=time.time(), next_check=0, attempts=0)
            self.store.save(job)
            return
        raise BridgeError("移动回执缺失，源和待归档目录均已消失；请选择归档后的作品目录核对，不会重复上传", review=True)

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
