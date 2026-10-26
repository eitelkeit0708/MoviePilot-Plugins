"""One owner per batch; every external side effect follows a durable state write."""

from pathlib import Path
from threading import Event
from contextlib import nullcontext
import time
import traceback

from .domain import (Awaiting, BridgeError, Config, Stopped, check_stop, child_path,
                     freeze_file, refresh_signature, unchanged, verify_tree)
from .instant import MAX_INSTANT_ATTEMPTS, RETRY_SECONDS, range_sha1
from .activity import event
from .cooldown import CloudCooldown, CloudRequestError
from .recovery import recovery_path, verify_archive
from .handoff import destination as handoff_destination, stable_observation
from .contentproof import PieceValidator


class Engine:
    def __init__(self, store, config: Config, host, cloud, stop: Event, protected_routings=(), receive_pending=None,
                 maintenance=None):
        self.store, self.config, self.host, self.cloud, self.stop = store, config, host, cloud, stop
        self.protected_routings = protected_routings
        self.receive_pending = receive_pending
        self.maintenance = maintenance

    def process(self, job):
        if job["state"] in ("handed_off", "cancelled", "deleting"):
            return
        requested = job.get('retry_requested')
        if not requested or self.stop.is_set():
            return self._process(job)
        job['recheck_started_at'] = time.time()
        self.store.save(job)
        self.store.record(job, event('recheck_started', '开始重新检查文件与处理状态'))
        try:
            self._process(job)
        finally:
            # Stopping/reloading is not a completed check; leave its request durable.
            if not self.stop.is_set():
                job.pop('retry_requested', None)
                job['recheck_result'] = {'requested_at': requested, 'at': time.time(),
                                       'state': job['state'], 'message': job['message']}
                self.store.save(job)

    def _process(self, job):
        try:
            check_stop(self.stop)
            if job["routing"] != self.config.routing():
                if job["routing"].get("storage") == "115网盘Plus":
                    raise BridgeError("旧批次需确认 MP 内置 115 与 CD2 使用同一账号，再切换上传接口", review=True)
                raise BridgeError("目录映射已变化，请恢复本批次的原配置后重新检查", review=True)
            if job.get("move_requested"):
                if job.get('work_handoffs'):
                    from .workhandoff import reconcile
                    self._gate_cloud()
                    reconcile(job, self.config, self.cloud, self.store, self.stop)
                    return
                self._reconcile_move(job)
                return
            if not Path(self.config.local_root).is_dir():
                raise Awaiting("等待本地整理目录恢复：" + self.config.local_root)
            if job.get('owned_candidates'):
                from .scoperepair import repair
                # Repair legacy attachment scope before collect_owned checks the
                # local files: a sibling batch may already have unlinked one of
                # these unrelated subtitles. Its captured SHA/cloud ID still
                # suffices for preservation, never for delivering it alone.
                repair(job, list(job['owned_candidates']), self.config, self.host, self.cloud, self.store, self.stop)
            candidates = self.host.collect(job)
            self.store.save(job)  # Complete ownership evidence precedes slow HASH / cloud work.
            if job.get('owned_candidates') and not job.get('scope_repair'):
                from .scoperepair import repair
                candidates = repair(job, candidates, self.config, self.host, self.cloud, self.store, self.stop)
            self._remember_original(job, candidates)
            remote_batch = child_path(self.config.staging, job["id"])
            source = child_path(self.config.cd2_staging, job["id"])
            work_layout = job.get('handoff_layout') == 'work'
            destination, destination_parent = ((None, self.config.inbox) if work_layout else
                                               handoff_destination(job, self.config, self.store))
            previous_files = None
            operations = 0
            missing = False
            cloud_error = None
            initial_hashes = len(job["files"])

            def ready(entry):
                nonlocal previous_files, operations, missing, cloud_error
                if cloud_error is not None:
                    return
                if not entry['uploaded'] and time.time() < entry.get('first_upload_after', 0):
                    return  # Local delay needs no cloud queries or directory creation.
                stage = '核对待归档目录'
                try:
                    check_stop(self.stop)
                    if previous_files is None:
                        # Snapshot the complete candidate manifest, including files
                        # whose HASH is still pending. Never trust cached upload paths.
                        if destination and self._cloud_call("exists", destination):
                            raise BridgeError("待归档目录已存在同名批次，请核对移动记录", review=True)
                        stage = '检查 SA 输入目录'
                        self._cloud_call("require_inbox", self.config.inbox)
                        if destination_parent != self.config.inbox:
                            stage = '准备日期目录'
                            ident = self._cloud_call('ensure_directory', destination_parent)
                            stage = '登记日期目录'
                            self._remember_parent(job, destination_parent, ident)
                        stage = '读取暂存清单'
                        previous_files = self._cloud_call("tree", source) if self._cloud_call("exists", source) else {}
                        stage = '核对暂存清单'
                        previous_files = self._staging_snapshot(job, source, previous_files,
                            {self.config.relative(c["local"]) for c in candidates})
                    stage = '处理文件上传'
                    used, absent = self._upload_ready(job, entry, remote_batch, previous_files, operations < 4)
                    operations += used
                    missing = missing or absent
                except Stopped:
                    raise
                except BridgeError as error:
                    if error.review:
                        raise
                    cloud_error = error
                except Exception as error:
                    cloud_error = self._unexpected(job, error, stage, entry['relative'])

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
                eligible = lambda e: max(e.get('instant_next_at', 0), e.get('first_upload_after', 0))
                next_due = min(eligible(e) for e in pending)
                first_wait = any(e.get('first_upload_after', 0) == next_due > time.time()
                                 and not e.get('instant_requests') for e in pending)
                job.update(state="waiting_upload" if first_wait else "waiting_instant", attempts=0,
                           message=f"已完成 {len(job['files']) - len(pending)}/{len(job['files'])} 个文件，" +
                                   ("等待首次上传" if first_wait else "等待秒传重试"),
                           next_check=max(time.time() + self.config.interval * 60,
                                          next_due))
                self.store.save(job)
                return

            check_stop(self.stop)
            if self.receive_pending:
                try:
                    self.receive_pending()
                finally:
                    # Intake uses the same lock but loads a fresh job. Keep files
                    # it accepted even when a later queued record is unavailable.
                    job.update(self.store.get(job['id']))
            latest = self.host.collect(job)
            if not self._same_candidates(job, latest):
                self._sync_candidates(job, latest, hash_limit=max(0, 4 - (len(job['files']) - initial_hashes)))
                raise Awaiting("已纳入新增整理文件，下次检查继续上传")
            job["state"] = "verifying"
            job["message"] = "正在核对云端文件"
            self.store.save(job)
            directory_id = self._cloud_call("directory_id", source)
            snapshot = self._staging_snapshot(job, source, self._cloud_call("tree", source),
                                             {entry["relative"] for entry in job["files"]},
                                             force=True, identity=directory_id)
            verify_tree(job["files"], snapshot)
            # Check for new native transfer results before the irreversible handoff.
            latest = self.host.collect(job)
            if not self._same_candidates(job, latest):
                self._sync_candidates(job, latest, hash_limit=max(0, 4 - (len(job["files"]) - initial_hashes)))
                raise Awaiting("已纳入新增整理文件，下次检查继续上传")
            for entry in job["files"]:
                self.config.relative(entry["local"])
                unchanged(entry)
            check_stop(self.stop)
            if work_layout:
                from .workhandoff import plan, reconcile
                self._gate_cloud()
                plan(job, self.config, self.cloud, self.store)
                reconcile(job, self.config, self.cloud, self.store, self.stop)
                return
            if self._cloud_call("exists", destination):
                raise BridgeError("待归档目录出现同名批次，已停止移交", review=True)
            if directory_id and self._cloud_call("directory_id", source) != directory_id:
                raise BridgeError("核对期间批次目录身份发生变化，已暂停移交", review=True)
            job.update(state="moving", move_requested=True, source=source, destination=destination,
                       source_directory_id=directory_id, message="正在整目录移交", next_check=0)
            self.store.save(job)
            # Persist the intent BEFORE the RPC. Any lost response is reconciled, never
            # blindly replayed, even if Symedia has already consumed the entire folder.
            job['move_last_requested_at'] = time.time()
            self.store.save(job)
            self._cloud_call("move_directory", source, destination_parent)
            # A successful RPC or a result path alone does not prove arrival.
            self._reconcile_move(job)
        except Stopped:
            return
        except CloudCooldown as error:
            pending_isolation = any(item.get("state") != "done"
                                    for item in job.get("duplicate_isolation", {}).get("items", []))
            job.update(state="moving" if job.get("move_requested") else "isolating" if pending_isolation else "waiting",
                       message=str(error), next_check=max(time.time() + self.config.interval * 60, error.next_at))
            self.store.save(job)
        except Awaiting as error:
            pending_isolation = any(item.get("state") != "done"
                                    for item in job.get("duplicate_isolation", {}).get("items", []))
            job["state"] = "moving" if job.get("move_requested") else "isolating" if pending_isolation else "waiting"
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
        except Exception as error:
            # SDK exceptions may contain authenticated URLs/tokens. Never persist or
            # return the raw exception through the plugin UI or notifications.
            if not self.stop.is_set():
                failure = self._unexpected(job, error, '处理批次')
                self._failure(job, str(failure), False)

    def _unexpected(self, job, error, stage, file=''):
        # SDK error strings, locals and source lines can contain authenticated
        # URLs or credentials. Persist only type and plugin code locations.
        locations = dict.fromkeys((Path(item.filename).name, item.name, item.lineno)
                                 for item in traceback.extract_tb(error.__traceback__)
                                 if '/symediabatchbridge/' in item.filename.replace('\\', '/'))
        frames = [{'file': name, 'function': function, 'line': line}
                  for name, function, line in list(locations)[-6:]]
        diagnostic = {'stage': stage, 'type': type(error).__name__, 'frames': frames}
        if file:
            diagnostic['file'] = file
        message = f"处理步骤异常：{stage}（{diagnostic['type']}），已保留进度并安排重试"
        if diagnostic != job.get('processing_error'):
            self.store.record(job, {**event('processing_error', message, level='warning', file=file),
                                    'diagnostic': diagnostic})
        job['processing_error'] = diagnostic
        return BridgeError(message)

    def _gate_cloud(self):
        gate = getattr(self.host, "gate_cloud", None)
        if callable(gate):
            gate()

    def _cloud_call(self, method, *args):
        self._gate_cloud()
        return getattr(self.cloud, method)(*args)

    def _staging_snapshot(self, job, source, remote, allowed, *, force=False, identity=None):
        pending = any(item.get("state") != "done"
                      for item in job.get("duplicate_isolation", {}).get("items", []))
        extra = set(remote) - allowed
        if not pending and not extra and not force:
            return remote
        recover = getattr(self.host, "isolate_duplicates", None)
        if not callable(recover):
            if not pending and not extra:
                return remote  # Legacy adapter still uses the original HASH checks.
            raise BridgeError("暂存批次出现清单外文件，已停止上传和移交", review=True)
        from .duplicates import identifier
        if force and not pending and not extra:
            try:
                for item in remote.values():
                    identifier(item.get("id"))
            except BridgeError:
                return remote  # Old CD2 builds omit file IDs; never guess isolation.
        self._gate_cloud()
        if identity is None:
            identity = self._cloud_call("directory_id", source)
        if not identity:
            if force and not pending and not extra:
                return remote
            raise Awaiting("等待 CD2 提供稳定目录 ID，再恢复重复副本隔离")
        changed = recover(job, identity, remote, allowed, self.store)
        if not changed:
            if extra:
                raise Awaiting("115 原始清单已核对，等待 CD2 刷新文件名称")
            return remote
        current = self._cloud_call("tree", source)
        if self._cloud_call("directory_id", source) != identity:
            raise BridgeError("隔离核对期间批次目录身份发生变化", review=True)
        if set(current) - allowed:
            raise Awaiting("重复副本已核对，等待 CD2 刷新完整清单")
        return current

    def _upload_ready(self, job, entry, remote_batch, previous_files, can_upload):
        """Reconcile one sealed file; return (upload operations, awaiting visibility)."""
        previous = previous_files.get(entry["relative"])
        if entry["uploaded"]:
            if previous is not None:
                verify_tree([entry], {entry["relative"]: previous})
                entry.pop("remote_missing_at", None)
                entry.pop('native_missing_observations', None)
                for name in ("instant_error", "instant_error_kind", "instant_error_since"):
                    entry.pop(name, None)
                return 0, False
            # A successful upload can precede CD2 visibility. Require two fresh,
            # separated observations before invalidating its durable receipt.
            now = time.time()
            if "remote_missing_at" not in entry:
                entry["remote_missing_at"] = now
                self.store.save(job)
            if now - entry["remote_missing_at"] < max(60, self.config.interval * 60):
                return 0, True
            native_check = getattr(self.host, 'staged_file_present', None)
            if callable(native_check):
                try:
                    present = native_check(entry, child_path(remote_batch, entry['relative']))
                except BridgeError:
                    entry.pop('native_missing_observations', None)
                    self.store.save(job)
                    raise
                if present:
                    entry.pop('remote_missing_at', None)
                    entry.pop('native_missing_observations', None)
                    self.store.save(job)
                    return 0, True
                previous_check = entry.get('native_missing_observations', {})
                if now - previous_check.get('at', 0) >= 300:
                    previous_check = {'at': now, 'count': previous_check.get('count', 0) + 1}
                    entry['native_missing_observations'] = previous_check
                    self.store.save(job)
                if previous_check.get('count', 0) < 3:
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
                entry.setdefault("receipt", {"size": entry["size"], "fileid": str(previous.get("id") or ""),
                                             "method": "verified"})
                for name in ("instant_error", "instant_error_kind", "instant_error_since"):
                    entry.pop(name, None)
                self.store.save(job)
                return 0, False
            raise BridgeError("云端存在未确认的同名文件，已停止覆盖：" + entry["relative"], review=True)
        if can_upload and time.time() >= max(entry.get("instant_next_at", 0), entry.get('first_upload_after', 0)):
            if entry.get('normal_requests'):
                if time.time() < entry.get('normal_recheck_at', 0):
                    return 0, True
                if not self._normal_retry_ready(job, entry, remote_batch):
                    return 1, True  # A native query also consumes this round's work budget.
            self._upload_entry(job, entry, child_path(remote_batch, entry["relative"]))
            return 1, False
        return 0, False

    def _normal_retry_ready(self, job, entry, remote_batch):
        """A lost full-upload response must not authorize another blind upload."""
        now = time.time()
        if now < entry.get('normal_recheck_at', 0):
            return False
        check = getattr(self.host, 'staged_file_present', None)
        if not callable(check):
            raise Awaiting('等待原生 115 核对上次普通上传结果，不凭 CD2 空列表重新上传')
        try:
            present = check(entry, child_path(remote_batch, entry['relative']))
            if type(present) is not bool:
                raise Awaiting('原生 115 未明确确认普通上传结果，保留现场等待复核')
        except Stopped:
            raise
        except Exception:
            entry.pop('normal_missing_observations', None)
            self.store.save(job)
            raise
        entry['normal_recheck_at'] = now + 300
        if present:
            entry.pop('normal_missing_observations', None)
            self.store.save(job)
            return False  # Keep waiting for the complete, fresh CD2 manifest.
        old = entry.get('normal_missing_observations', {})
        entry['normal_missing_observations'] = {'count': old.get('count', 0) + 1, 'at': now}
        self.store.save(job)
        return entry['normal_missing_observations']['count'] >= 3

    def _upload_entry(self, job, entry, remote_file):
        now = time.time()
        # Per-file deadlines survive restart, downtime and the manual retry action.
        # Never catch up missed hourly attempts in a tight loop.
        if now < max(entry.get("instant_next_at", 0), entry.get('first_upload_after', 0)):
            return
        self._gate_cloud()
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
            entry['normal_last_requested_at'] = now
            entry['instant_next_at'] = now + RETRY_SECONDS
            entry.pop('normal_missing_observations', None)
            entry.pop('normal_recheck_at', None)
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
        previous_protocol = {(item["id"], item["stage"]) for item in entry.get("instant_protocol", [])}
        try:
            trace = getattr(self.host, "trace_instant", None)
            with trace(lambda: self.store.save(job)) if callable(trace) else nullcontext():
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
        finally:
            fresh_protocol = [item for item in entry.get("instant_protocol", [])
                              if (item["id"], item["stage"]) not in previous_protocol]
            if fresh_protocol:
                self.store.save(job)
                for item in fresh_protocol:
                    endpoint = item.get('endpoint', '/open/upload/init')
                    label = {'/open/upload/init': '初始化', '/open/folder/get_info': '查询暂存目录',
                             '/open/folder/add': '创建暂存目录', '/open/ufile/files': '读取暂存目录'}.get(endpoint, endpoint)
                    if item["stage"] == "result":
                        if endpoint == '/open/upload/init':
                            message = f"115 初始化响应：状态 {item.get('status')}，业务码 {item.get('business_code')}，结果码 {item.get('result_code')}"
                        else:
                            message = f"115 {label}响应：业务码 {item.get('business_code')}"
                            if endpoint == '/open/folder/get_info' and item.get('business_code') in (20004, 430004):
                                message += '，目标目录不存在，继续按原路径准备目录'
                        if item.get("fileid"):
                            message += "，文件 ID " + item["fileid"]
                        self.store.record(job, {**event("instant_protocol", message, file=entry["relative"]),
                                                "protocol": item})
                    elif item["stage"] == "error":
                        self.store.record(job, {**event("instant_protocol", "115 " + label + "请求结果未确认", file=entry["relative"]),
                                                "protocol": item})
                    elif item["stage"] == "http" and (item.get("status") or 0) >= 300:
                        self.store.record(job, {**event("instant_protocol",
                            f"115 {label} HTTP {item['status']}，已停止自动重发并保留核对记录", file=entry["relative"]),
                            "protocol": item})
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
            if self.maintenance:
                self.maintenance()
            before = entry['signature']
            refresh_signature(entry, self.stop, rehash=lambda: self._hash_file(
                job, entry['local'], entry['relative'], action='复核原 SHA'))
            if entry['signature'] != before:
                self.store.save(job)
            if on_ready:
                on_ready(entry)
        hashed = 0
        for candidate in candidates:
            check_stop(self.stop)
            if self.maintenance:
                self.maintenance()
            path = candidate["local"]
            relative = self.config.relative(path)
            if path in existing:
                continue
            if hashed >= hash_limit:
                raise Awaiting(f"已记录 {len(job['files'])}/{len(candidates)} 个文件 HASH，下次继续")
            validator = self._content_validator(job, candidate)
            try:
                entry = self._hash_file(job, path, relative, validator=validator)
            except BridgeError as error:
                if getattr(error, 'content_rejection', None):
                    candidate['content_rejection'] = error.content_rejection
                raise
            entry['hash_completed_at'] = time.time()
            entry['first_upload_after'] = entry['hash_completed_at'] + (
                self.config.first_upload_delay_minutes * 60 if self.config.first_upload_delay else 0)
            snapshot = candidate.get("accepted_signature") or candidate.get("inventory_signature")
            if snapshot and entry['signature'] != snapshot:
                if validator is None:
                    raise BridgeError('存量文件在接管后发生变化，等待原种子自动复核：' + relative, review=True)
            if validator is not None:
                entry['content_revalidation'] = {'basis': 'torrent_pieces', 'hash': job['download_hash'],
                    'signature': entry['signature'], 'previous_signature': snapshot,
                    'piece_count': validator.checked, 'sha1': entry['sha1']}
                candidate['accepted_signature'] = entry['signature']
                candidate.pop('content_rejection', None)
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
            if source and not job.get("owned_candidates"):
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
        job["history_ids"] = self._candidate_history_ids(job, candidates)
        job.pop("hash_progress", None)
        job.update(state="uploading", message="正在尝试秒传")
        self.store.save(job)

    def _remember_original(self, job, candidates):
        """Cache immutable evidence before seeding originals/tasks can expire."""
        from .domain import file_signature
        key = str(job.get('download_hash', '')).lower()
        if (job.get('content_evidence_attempted') or len(key) != 40
                or any(c not in '0123456789abcdef' for c in key)):
            return
        existing = {e['local'] for e in job['files']}
        pending = [c for c in candidates if c['local'] not in existing]
        if not pending or not all(c.get('source') and c.get('accepted_signature') for c in pending):
            return
        # A changed file acquires proof through the required validation path;
        # never make two downloader requests in the same check on failure.
        if any(file_signature(Path(c['local'])) != c['accepted_signature'] for c in pending):
            return
        acquire = getattr(self.host, 'torrent_evidence', None)
        if not callable(acquire):
            return
        if self.store.meta('content-proof:' + key) is None:
            try:
                proof = acquire(job)
                from .contentproof import manifest
                manifest(proof, key)
                self.store.set_meta('content-proof:' + key, proof)
            except BridgeError:
                # Unchanged completed outputs keep their prior behavior. A later
                # metadata change still MUST acquire valid proof before release.
                self.store.record(job, event('content_evidence_pending',
                    '原种子证据暂未缓存；文件变化时将重新取得证据，验证前不会放行'))
        job['content_evidence_attempted'] = True
        self.store.save(job)

    def _content_validator(self, job, candidate):
        from .domain import file_signature
        snapshot = candidate.get('accepted_signature') or candidate.get('inventory_signature')
        if not snapshot:
            return None
        current = file_signature(Path(candidate['local']))
        if current == snapshot:
            return None
        rejected = candidate.get('content_rejection', {})
        if current == rejected.get('signature'):
            try:
                unchanged_neighbors = all(file_signature(Path(p)) == s for p, s in rejected.get('neighbors', {}).items())
            except OSError:
                unchanged_neighbors = False
            if unchanged_neighbors:
                raise BridgeError('本地文件内容与原种子不一致，继续拦截：' + Path(candidate['local']).name, review=True)
        key = 'content-proof:' + str(job.get('download_hash', '')).lower()
        proof = self.store.meta(key)
        if proof is None:
            acquire = getattr(self.host, 'torrent_evidence', None)
            if not callable(acquire):
                raise BridgeError('无法取得原种子校验证据，保留文件并自动重试', review=True)
            proof = acquire(job)
            # Validate evidence before persisting it; cache only immutable info.
            from .contentproof import manifest
            manifest(proof, job.get('download_hash'))
            self.store.set_meta(key, proof)
        return PieceValidator(proof, job, candidate, self.stop, self.config)

    def _hash_file(self, job, path, relative, source=False, validator=None, action=None):
        last_saved = last_log = None
        action = action or ('复核原种子内容' if validator is not None else "核对下载源 HASH" if source else "计算 HASH")
        def progress(done, total):
            nonlocal last_saved, last_log
            # The same worker performs bounded control work between HASH blocks.
            # No concurrent media mutation or second writer owns this job.
            if self.maintenance:
                self.maintenance()
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
        if validator is not None:
            return freeze_file(path, relative, self.stop, progress=progress, validator=validator)
        return freeze_file(path, relative, self.stop, progress=progress)

    @staticmethod
    def _candidate_history_ids(job, candidates):
        identifiers = {c["history_id"] for c in candidates if c.get("history_id")}
        # Legacy sealed manifests retain aggregate IDs, not a per-file mapping.
        # Ownership is append-only; a newly received subtitle must not discard
        # the IDs belonging to the already accepted video and attachments.
        if job.get("owned_candidates"):
            identifiers.update(job.get("history_ids", []))
        return sorted(identifiers) or job.get("history_ids", [])

    @staticmethod
    def _same_candidates(job, candidates):
        return ({c["local"] for c in candidates} == {e["local"] for e in job["files"]}
                and Engine._candidate_history_ids(job, candidates) == job.get("history_ids", []))

    def _remember_parent(self, job, parent, ident):
        """A retried move must record the parent it will actually use, too."""
        created = getattr(self.cloud, 'created_directories', {})
        if created.get(parent) == ident:
            self.store.remember_inbox_date(parent, ident)
        job['delivery_parent_id'] = ident
        self.store.save(job)

    def _reconcile_move(self, job):
        target, parent = handoff_destination(job, self.config, self.store)
        if job.get('source') != child_path(self.config.cd2_staging, job['id']):
            raise BridgeError('移交源目录与本批次不一致', review=True)
        source_exists = self._cloud_call("exists", job["source"])
        destination_exists = self._cloud_call("exists", job["destination"])
        if source_exists != destination_exists:
            job.pop('move_observation', None)
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
            if time.time() - job.get('move_last_requested_at', 0) < 60:
                raise Awaiting('移动请求已保存，等待目录刷新后再决定是否重试')
            if parent != self.config.inbox:
                ident = self._cloud_call('ensure_directory', parent)
                self._remember_parent(job, parent, ident)
            check_stop(self.stop)
            if directory_id and self._cloud_call("directory_id", job["source"]) != directory_id:
                raise BridgeError("核对期间批次目录身份发生变化，已暂停移交", review=True)
            # Older jobs can gain identity evidence ONLY while their complete
            # source is still present, never from an already empty destination.
            if directory_id and not job.get("source_directory_id"):
                job["source_directory_id"] = directory_id
                self.store.save(job)
            job['move_last_requested_at'] = time.time()
            self.store.save(job)
            self._cloud_call("move_directory", job["source"], parent)
            raise Awaiting("CD2 移动请求已提交，下轮核对目标目录身份")
        if source_exists:
            stable_observation(job, 'both_present', self.store)
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
        stable_observation(job, 'both_missing', self.store)
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
