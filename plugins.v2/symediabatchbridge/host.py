"""MoviePilot V2 adapter. Never patch the host or another plugin."""

from pathlib import Path, PurePosixPath
from copy import copy
from contextlib import contextmanager
import json

from .domain import Awaiting, BridgeError, child_path, file_signature
from .instant import native_provider, try_instant
from .media import history_media
from .cloud_requests import NativeOperations
from .ownership import accept, collect_owned, migrate_sealed, capture, SUBTITLES
from . import subtitle_scope


def value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def source_path(path):
    return str(PurePosixPath(str(path or "").replace("\\", "/")))


def current_attachment(source, selected_sources, media=None, videos=None):
    """External subtitle attributed to a selected video by the shared matcher.

    The legacy two-argument call still works: it builds video candidates from
    the selected paths and uses conservative filename/parent attribution.
    ``media`` may carry the subtitle's authenticated per-file MP metadata and
    ``videos`` may carry full candidate dicts (local/source/media/history_id).
    A torrent hash or a shared parent directory alone never proves attachment.
    """
    path = PurePosixPath(str(source).replace("\\", "/"))
    if path.suffix.lower() not in SUBTITLES or '..' in path.parts:
        return False
    # Identity refines the CURRENT download scope; it must not make an old
    # same-hash transfer elsewhere become a newly discovered attachment.
    anchors = [item.get('source') or item.get('local') if isinstance(item, dict) else item
               for item in selected_sources]
    if not any(path.is_relative_to(PurePosixPath(str(anchor).replace('\\', '/')).parent)
               for anchor in anchors if anchor and PurePosixPath(str(anchor)).suffix.lower() not in SUBTITLES):
        return False
    if videos is None:
        videos = []
        for item in selected_sources:
            if isinstance(item, dict):
                candidate = item
            else:
                candidate_path = PurePosixPath(str(item).replace("\\", "/"))
                if candidate_path.suffix.lower() in SUBTITLES:
                    continue
                candidate = {"local": str(item), "source": str(item)}
            if candidate.get("local") or candidate.get("source"):
                videos.append(candidate)
    return subtitle_scope.matches({"local": str(path), "source": str(path),
                                   "media": media or {}}, videos)


def supplementary_directory(source, anchors):
    """Explicit extras beneath a verified main file's directory, never name guesses."""
    path = PurePosixPath(source)
    if ('..' in path.parts or not any(part.casefold() in ('featurettes', 'extras')
                                     for part in path.parts[:-1])):
        return None
    for anchor in sorted(anchors, key=lambda p: (len(PurePosixPath(p).parts), p)):
        try:
            relative = path.relative_to(PurePosixPath(anchor).parent)
        except ValueError:
            continue
        for part in relative.parts[:-1]:
            if part.casefold() in ('featurettes', 'extras'):
                return {'basis':'supplementary_directory', 'directory':part, 'anchor_source':anchor}
    return None


def download_root(native_files, files, *, torrents=(), download_hash=""):
    """Resolve the current task root, falling back to exact original MP paths.

    MP records original torrent names when adding a download. The downloader can
    later rename them, including removing invisible formatting characters. Its
    current save_path is already mapped to MP's local paths by the host adapter.
    Never repair names or match basenames: collect still requires each complete
    current source path to have a successful transfer for this same task.
    """
    current = [t for t in torrents if value(t, "save_path")]
    if current:
        if (len(torrents) != 1 or len(current) != 1 or not download_hash
                or str(value(current[0], "hash") or "").lower() != str(download_hash).lower()):
            raise BridgeError("下载器当前任务无法唯一对应本批次", review=True)
        raw = str(value(current[0], "save_path")).replace("\\", "/")
        if (not (PurePosixPath(raw).is_absolute() or Path(raw).is_absolute())
                or "\x00" in raw or any(p in (".", "..") for p in raw.split("/"))):
            raise BridgeError("下载器当前保存路径无效", review=True)
        root = source_path(raw)
        for item in files:
            child_path(root, str(value(item, "name") or ""))
        return root
    # Older host adapters may not expose the current save_path. MP's recorded
    # savepath can already include the torrent's top-level directory.
    paths = {source_path(value(row, "savepath")) for row in native_files if value(row, "savepath")}
    if len(paths) != 1:
        raise BridgeError("下载文件的保存路径无法唯一确定", review=True)
    saved = next(iter(paths))
    names = [str(value(item, "name") or "") for item in files]
    for name in names:
        child_path(saved, name)  # Validate before considering either root.
    recorded = {source_path(value(row, "fullpath")) for row in native_files if value(row, "fullpath")}
    if not recorded:
        return saved
    # Compare complete paths, never basenames or a guessed existing file.
    roots = {saved, str(PurePosixPath(saved).parent)}
    matching = [root for root in roots if recorded <= {child_path(root, name) for name in names}]
    if len(matching) != 1:
        raise BridgeError("下载器文件路径与 MP 下载记录不一致，无法确定源目录", review=True)
    return matching[0]


class MPHost:
    def __init__(self, config, chain=None, storage=None, downloads=None, transfers=None,
                 extensions=None):
        # Imports are delayed so configuration and history still render if an optional
        # provider is missing. Tests inject only the host boundary, not business logic.
        if chain is None:
            from app.chain.download import DownloadChain
            from app.chain.storage import StorageChain
            from app.db.downloadhistory_oper import DownloadHistoryOper
            from app.db.transferhistory_oper import TransferHistoryOper
            from app.core.config import settings
            chain, storage = DownloadChain(), StorageChain()
            downloads, transfers = DownloadHistoryOper(), TransferHistoryOper()
            extensions = set(settings.RMT_MEDIAEXT + settings.RMT_SUBEXT + settings.RMT_AUDIOEXT)
            required = ((chain, ("torrent_files", "list_torrents")),
                        (storage, ("get_folder", "upload_file")),
                        (downloads, ("get_files_by_hash",)),
                        (transfers, ("get", "list_by_hash", "list_by_date")))
            if any(not callable(getattr(adapter, name, None)) for adapter, names in required for name in names):
                raise RuntimeError("MoviePilot V2 adapter contract unavailable")
        self.config, self.chain, self.storage = config, chain, storage
        self.downloads, self.transfers = downloads, transfers
        self.extensions = {str(e).lower() for e in extensions}
        # Include paired/external subtitles even if a host extension setting omitted one;
        # a missing native transfer result must hold the batch rather than drop subtitles.
        self.extensions.update({".srt", ".ass", ".ssa", ".sub", ".idx", ".sup", ".vtt"})
        self.cooldown = None
        self.stop = None
        self._instant_checkpoint = None

    def bind_cooldown(self, cooldown, stop):
        self.cooldown, self.stop = cooldown, stop
        return self

    def gate_cloud(self):
        if self.cooldown:
            self.cooldown.before()
            self.cooldown.before(native_provider())

    def cloud_status(self):
        return self.cooldown.status(native_provider()) if self.cooldown else {}

    def _native_operations(self):
        if self.cooldown:
            self.cooldown.before()
        return NativeOperations(native_provider(), self.cooldown, self.stop) if self.cooldown else None

    def for_config(self, config):
        host = copy(self)
        host.config = config
        return host

    def in_scope(self, history):
        if value(history, "dest_storage", "local") not in (None, "", "local"):
            return False
        try:
            path = Path(str(value(history, "dest") or ""))
            path.relative_to(Path(self.config.local_root))
            return path.suffix.lower() in self.extensions
        except ValueError:
            return False

    def histories_since(self, since):
        rows = self.transfers.list_by_date(since)
        if rows is None:
            raise Awaiting("MP 整理历史暂不可读，稍后重试")
        return rows

    def torrent_evidence(self, job):
        from .contentproof import export_native
        return export_native(job)

    def collect(self, job):
        migrate_sealed(job)
        if job.get('owned_candidates'):
            return collect_owned(job, self.config)
        if job.get("origin") == "inventory":
            return self._collect_inventory(job)
        download_hash, downloader = job["download_hash"], job["downloader"]
        if not download_hash or not downloader:
            raise BridgeError("缺少下载任务标识，无法自动确认附件范围；此任务需人工处理", review=True)
        native_files = [row for row in self.downloads.get_files_by_hash(download_hash, state=1)
                        if value(row, "downloader") == downloader]
        if not native_files:
            raise Awaiting("等待 MP 下载文件清单")
        files = self.chain.torrent_files(tid=download_hash, downloader=downloader)
        if not files:
            raise Awaiting("无法读取下载器文件清单；请保留下载任务并检查下载器连接")
        torrents = self.chain.list_torrents(hashs=download_hash, downloader=downloader) or []
        save_path = download_root(native_files, files, torrents=torrents, download_hash=download_hash)
        all_complete = bool(torrents) and all(float(value(t, "progress", 0) or 0) >= 100 for t in torrents)
        expected, torrent_sources, complete_sources = {}, set(), set()
        for item in files:
            name = str(value(item, "name") or "")
            if PurePosixPath(name).suffix.lower() not in self.extensions:
                continue
            source = child_path(save_path, name)
            if source in torrent_sources:
                raise BridgeError("下载器清单出现重复文件路径", review=True)
            torrent_sources.add(source)
            priority, selected = value(item, "priority"), value(item, "selected")
            if priority is not None:
                selected = int(priority) > 0
            if selected is None:
                raise BridgeError("下载器未返回文件选择状态，无法确定本批范围", review=True)
            if not selected:
                continue
            size = int(value(item, "size", 0) or 0)
            progress, completed = value(item, "progress"), value(item, "completed")
            complete = (float(progress) >= 1 if progress is not None else
                        int(completed) >= size if completed is not None else all_complete)
            if complete:
                complete_sources.add(source)
            expected[source] = size
        if not expected:
            raise BridgeError("本次选择中没有可交付的媒体或字幕文件", review=True)
        previous = job.get("download_manifest")
        if (previous is not None and job.get("files")
                and any(expected.get(path) != size for path, size in previous.items())):
            raise BridgeError("批次封存后下载文件选择发生变化", review=True)
        job["download_manifest"] = expected

        records = self.transfers.list_by_hash(download_hash) or []
        newest = {}
        for row in sorted(records, key=lambda r: int(value(r, "id", 0))):
            if value(row, "downloader") != downloader:
                continue
            if value(row, "src_storage", "local") not in (None, "", "local"):
                continue
            src = source_path(value(row, "src"))
            newest[src] = row
        # Only explicit extras folders beside a selected, successfully organized
        # main file are supplementary. Never drop subtitles or unmatched videos
        # elsewhere, or shrink a file whose identity/HASH was already accepted.
        anchors = [source for source in expected if source in newest
                   and value(newest[source], 'status') and self.in_scope(newest[source])
                   and PurePosixPath(source).suffix.lower() not in SUBTITLES]
        protected = {entry['local'] for entry in job.get('files', [])}
        excluded_downloads = []
        required = {}
        for source, size in expected.items():
            evidence = supplementary_directory(source, anchors)
            if evidence and value(newest.get(source), 'dest') not in protected:
                excluded_downloads.append({'source':source, 'size':size, **evidence})
            else:
                required[source] = size
        # Explicitly selected subtitles pass the same attribution check as
        # discovered externals. A conflicting selection is never delivered or
        # deleted; keep the local file and journal why it was excluded.
        video_candidates = []
        for source in sorted(required):
            if PurePosixPath(source).suffix.lower() in SUBTITLES:
                continue
            row = newest.get(source)
            target = str(value(row, 'dest')) if row and value(row, 'dest') else source
            video_candidates.append({
                'local': target, 'source': source,
                'history_id': int(value(row, 'id')) if row else None,
                'media': history_media(row) if row else {}})
        excluded_attachments = []
        for source in list(required):
            if PurePosixPath(source).suffix.lower() not in SUBTITLES:
                continue
            row = newest.get(source)
            candidate = {'local': source, 'source': source,
                         'history_id': int(value(row, 'id')) if row else None,
                         'media': history_media(row) if row else {}}
            if subtitle_scope.matches(candidate, video_candidates):
                continue
            detail = subtitle_scope.reason(candidate, video_candidates) or 'no_attribution_evidence'
            excluded_attachments.append({
                'local': str(value(row, 'dest')) if row and value(row, 'dest') else source,
                'source': source,
                'history_id': int(value(row, 'id')) if row else None,
                'media': candidate['media'],
                'date': str(value(row, 'date') or '') if row else '',
                'reason': detail})
            del required[source]
        job['excluded_attachments'] = excluded_attachments
        if any(source not in complete_sources for source in required):
            raise Awaiting("等待本次选择的视频与字幕下载完成")
        result = []
        historical = []
        first_history_id = min((int(value(row, 'id', 0)) for row in newest.values()), default=0)
        for source in sorted(required):
            row = newest.get(source)
            if row is None and not job.get('files'):
                prior = self._historical_transfer(source, expected[source], job, first_history_id)
                if prior:
                    historical.append(prior)
                    continue
            if row is None or not value(row, "status"):
                raise Awaiting("等待 MP 整理完成：" + PurePosixPath(source).name)
            if not self.in_scope(row):
                raise BridgeError("本批部分文件未整理到配置的本地目录，请核对 MP 整理规则", review=True)
            if not job.get("media"):
                job["media"] = history_media(row)
            target = str(value(row, "dest") or "")
            self.config.relative(target)
            if Path(target).stat().st_size != expected[source]:
                raise BridgeError("整理文件大小与下载清单不同：" + Path(target).name, review=True)
            result.append({"local": target, "source": source, "history_id": int(value(row, "id")),
                           "media": history_media(row)})
        # Native history spans every occurrence of this hash. Only current selected
        # torrent files and their external subtitles may become this batch's scope.
        # Never treat an earlier download's unlisted video/old subtitle path as a
        # newly added attachment, even when its missing output once organized well.
        excluded = []
        attachment_sources = [item['source'] for item in result
                              if PurePosixPath(item['local']).suffix.lower() not in SUBTITLES]
        video_candidates = [{"local": item['local'], "source": item['source'],
                             "history_id": item['history_id'], "media": item.get('media', {})}
                            for item in result
                            if PurePosixPath(item['local']).suffix.lower() not in SUBTITLES]
        for source, row in newest.items():
            if source in torrent_sources or not self.in_scope(row):
                continue
            supplementary = supplementary_directory(source, attachment_sources)
            if supplementary:
                excluded.append({'history_id': int(value(row, 'id')), 'source': source,
                                 'local': str(value(row, 'dest')), 'date': str(value(row, 'date') or ''),
                                 'basis': 'supplementary_directory'})
                continue
            media = history_media(row)
            candidate = {'local': source, 'source': source,
                         'history_id': int(value(row, 'id')), 'media': media}
            if not current_attachment(source, attachment_sources, media=media,
                                      videos=video_candidates):
                detail = subtitle_scope.reason(candidate, video_candidates)
                excluded.append({'history_id': int(value(row, 'id')), 'source': source,
                                 'local': str(value(row, 'dest')), 'date': str(value(row, 'date') or ''),
                                 'basis': 'outside_current_download_scope'})
                if PurePosixPath(source).suffix.lower() in SUBTITLES:
                    excluded_attachments.append({
                        'local': str(value(row, 'dest')), 'source': source,
                        'history_id': int(value(row, 'id')), 'media': media,
                        'date': str(value(row, 'date') or ''),
                        'reason': detail or 'no_attribution_evidence'})
                continue
            if not value(row, "status"):
                raise Awaiting("同一任务还有整理失败的文件：" + PurePosixPath(source).name)
            target = str(value(row, "dest"))
            self.config.relative(target)
            result.append({"local": target, "source": source, "history_id": int(value(row, 'id')),
                           "media": history_media(row)})
        if not any(Path(r["local"]).suffix.lower() not in
                   {".srt", ".ass", ".ssa", ".sub", ".idx", ".sup", ".vtt"} for r in result):
            job['orphan_files'] = capture(result)
            raise BridgeError("批次只有字幕，没有对应媒体文件", review=True)
        # These are MP's previous transfers, NOT upload receipts for this batch.
        # Persist the full partition only after all current transfers are verified.
        job['historical_transfers'] = historical
        job['excluded_transfers'] = excluded
        job['excluded_downloads'] = excluded_downloads
        return accept(job, result)

    def _historical_transfer(self, source, size, job, first_history_id):
        """MP deduplicates automatic transfers by exact source, across torrent hashes.

        A season torrent can reuse files previously organized as single episodes.
        Their old transfer belongs to that previous task, even after its local
        output was cleaned up. Never adopt it again or claim it was cloud archived.
        Missing/failed CURRENT records must still hold the batch.
        """
        lookup = getattr(self.transfers, 'get_by_src', None)
        if not callable(lookup) or not first_history_id:
            return None
        row = lookup(src=source, storage='local')
        if (not row or not value(row, 'status')
                or source_path(value(row, 'src')) != source
                or value(row, 'src_storage', 'local') not in (None, '', 'local')
                or value(row, 'downloader') != job['downloader']
                or not value(row, 'download_hash')
                or value(row, 'download_hash') == job['download_hash']
                or not 0 < int(value(row, 'id', 0)) < first_history_id
                or not self.in_scope(row)):
            return None
        item = value(row, 'src_fileitem')
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except (ValueError, TypeError):
                return None
        if (not isinstance(item, dict) or item.get('storage') != 'local'
                or source_path(item.get('path')) != source or item.get('size') != size):
            return None
        return {'source': source, 'size': size, 'history_id': int(value(row, 'id')),
                'download_hash': value(row, 'download_hash'), 'local': str(value(row, 'dest')),
                'date': str(value(row, 'date') or ''), 'basis': 'mp_source_history'}

    def _collect_inventory(self, job):
        # Explicitly adopted existing output is a snapshot, not an unfinished
        # download. Seeding originals and torrent tasks are allowed to expire.
        # New snapshots include media metadata. Older snapshots can enrich their
        # display once, best-effort; missing history never invalidates ownership.
        manifest = job.get("inventory_files")
        if manifest is None:
            latest = {}
            for row in self.histories_since("1970-01-01 00:00:00"):
                if self.in_scope(row):
                    dest = str(value(row, 'dest'))
                    if dest not in latest or int(value(row, 'id')) > int(value(latest[dest], 'id')):
                        latest[dest] = row
            # Upgrade only jobs whose persisted import event proves user adoption.
            manifest = []
            for dest, row in latest.items():
                if (value(row, "downloader") == job["downloader"]
                        and value(row, "download_hash") == job["download_hash"]
                        and value(row, "status") and Path(dest).is_file()):
                    self.config.relative(dest)
                    manifest.append({"local": dest, "history_id": int(value(row, "id")),
                                     "signature": file_signature(Path(dest))})
            job["inventory_files"] = manifest
        if not manifest:
            raise BridgeError("本次接管已没有可核实的整理文件", review=True)
        if any('media' not in member for member in manifest) and not job.get('snapshot_metadata_checked'):
            try:
                metadata = {int(value(row, 'id')): row for row in self.histories_since("1970-01-01 00:00:00")}
            except Exception:
                metadata = {}
            for member in manifest:
                row = metadata.get(member['history_id'])
                if (row and str(value(row, 'dest')) == member['local'] and value(row, 'status')
                        and value(row, 'download_hash') == job['download_hash']):
                    member['media'] = history_media(row)
                    if not job.get('media'):
                        job['media'] = member['media']
            job['snapshot_metadata_checked'] = True
        result = []
        for member in manifest:
            target = member["local"]
            self.config.relative(target)
            # Before the first hash, reject replacements since the scan. Unlinking
            # the original hardlink can change ctime without changing this copy.
            if not any(entry["local"] == target for entry in job.get("files", [])):
                current = file_signature(Path(target))
                if any(current[i] != member["signature"][i] for i in (0, 1, 3, 4)):
                    raise BridgeError("存量文件在接管后发生变化，已暂停处理：" + Path(target).name, review=True)
            candidate = {"local": target, "history_id": member["history_id"],
                         "inventory_signature": member["signature"], "media": member.get('media', job.get('media', {}))}
            result.append(candidate)
        subtitles = {".srt", ".ass", ".ssa", ".sub", ".idx", ".sup", ".vtt"}
        if not any(Path(row["local"]).suffix.lower() not in subtitles for row in result):
            job['orphan_files'] = capture(result)
            raise BridgeError("现存批次只有字幕，没有对应媒体文件", review=True)
        return accept(job, result)

    def _upload_parent(self, remote_file, operations=None):
        parent = (operations.call("get_folder", path=Path(remote_file).parent) if operations else
                  self.storage.get_folder(storage=self.config.storage, path=Path(remote_file).parent))
        if not parent or value(parent, "type") != "dir":
            raise BridgeError("无法建立 115 暂存目录，请检查 MP 内置 115 的登录和连接")
        if source_path(value(parent, "path")) != source_path(str(Path(remote_file).parent)):
            raise BridgeError("115 返回的上传目录与批次路径不一致", review=True)
        return parent

    def try_instant(self, entry, remote_file, stop):
        operations = self._native_operations()
        if operations:
            def audit(item):
                entry.setdefault("instant_protocol", []).append(item)
                entry["instant_protocol"] = entry["instant_protocol"][-48:]
                if self._instant_checkpoint:
                    self._instant_checkpoint()
            operations.audit = audit
        # Directory preparation can fail before upload init. Its request/result
        # must be checkpointed as well, including the exact failing endpoint.
        parent = self._upload_parent(remote_file, operations)
        entry['remote_parent_id'] = str(value(parent, 'fileid') or '')
        return try_instant(operations.provider if operations else native_provider(), value(parent, "fileid"), entry, stop,
                           request=operations.request if operations else None)

    def staged_file_present(self, entry, remote_file):
        """A missing CD2 view cannot invalidate a native 115 receipt by itself."""
        from .duplicates import Isolation, identifier, matches
        operations = self._native_operations()
        if not operations:
            raise Awaiting('等待原生 115 核实上传回执，不凭 CD2 空列表补传')
        parent_id = entry.get('remote_parent_id')
        if not parent_id:
            operations._validate_operation('get_folder')
            parent = operations.native.get_item(Path(remote_file).parent)
            operations.check()
            if (not parent or value(parent, 'type') != 'dir' or
                    source_path(value(parent, 'path')) != source_path(str(Path(remote_file).parent))):
                raise Awaiting('等待原生 115 暂存目录恢复，不撤销已有回执')
            parent_id = identifier(value(parent, 'fileid'))
            entry['remote_parent_id'] = parent_id
        rows = Isolation(operations.request, None, self.config, self.stop).files(parent_id)
        receipt_id = str(entry.get('receipt', {}).get('fileid') or '')
        candidates = [r for r in rows if r['id'] == receipt_id or r['name'] == Path(remote_file).name]
        if candidates and not all(not r['directory'] and matches(r, entry) for r in candidates):
            raise BridgeError('原生 115 回执对应文件内容不一致，已停止补传', review=True)
        return bool(candidates)

    def empty_directory(self, path, directory_id):
        from .duplicates import Isolation, identifier
        from .domain import cloud_path
        if cloud_path(path) != path or not path.startswith(self.config.cd2_prefix.rstrip('/') + '/'):
            raise BridgeError('空目录越出配置的挂载范围', review=True)
        operations = self._native_operations()
        if not operations:
            raise Awaiting('等待原生 115 核对空目录身份')
        native_path = path[len(self.config.cd2_prefix.rstrip('/')):]
        operations._validate_operation('get_folder')
        item = operations.native.get_item(Path(native_path))
        operations.check()
        if not item:
            raise Awaiting('原生 115 目录暂不可见，保留现场等待恢复')
        if (value(item, 'type') != 'dir' or str(value(item, 'fileid')) != identifier(directory_id)
                or source_path(value(item, 'path')) != source_path(native_path)):
            raise BridgeError('空目录身份与移交记录不一致，已停止清理', review=True)
        return not Isolation(operations.request, None, self.config, self.stop).inventory(directory_id)

    def delete_retired_empty(self, path, directory_id, batch_id):
        from .duplicates import identifier
        from .domain import cloud_path
        prefix = child_path(child_path(self.config.cd2_staging, '_空目录清理'), batch_id) + '/'
        if (cloud_path(path) != path or not path.startswith(prefix)
                or len(Path(path).relative_to(prefix).parts) != 2):
            raise BridgeError('仅允许清理本批次已退役的空目录', review=True)
        if not self.empty_directory(path, directory_id):
            raise Awaiting('退役目录出现文件，已停止删除并等待恢复到监控目录')
        result = self._native_operations().request('POST', '/open/ufile/delete',
                                                  data={'file_ids': int(identifier(directory_id))})
        if not result or result.get('state') is not True:
            raise Awaiting('空目录回收结果未确认，稍后按原 ID 核对')

    @contextmanager
    def trace_instant(self, checkpoint):
        previous, self._instant_checkpoint = self._instant_checkpoint, checkpoint
        try:
            yield
        finally:
            self._instant_checkpoint = previous

    def isolate_duplicates(self, job, root_id, remote, allowed, store):
        from .duplicates import Isolation
        from .cooldown import Cooldown
        operations = NativeOperations(native_provider(), self.cooldown or Cooldown(store), self.stop)
        def folder(path):
            item = operations.call("get_folder", path=Path(path))
            if (not item or value(item, "type") != "dir" or
                    source_path(value(item, "path")) != source_path(path)):
                raise BridgeError("无法核实重复副本的隔离目录，稍后自动重试")
            return value(item, "fileid")
        return Isolation(operations.request, folder, self.config, self.stop).recover(
            job, root_id, remote, allowed, store)

    def upload(self, local: Path, remote_file: str):
        operations = self._native_operations()
        parent = self._upload_parent(remote_file, operations)
        uploaded = (operations.call("upload", target_dir=parent, local_path=local, new_name=local.name) if operations else
                    self.storage.upload_file(fileitem=parent, path=local, new_name=local.name))
        if not uploaded:
            return None
        if source_path(value(uploaded, "path")) != source_path(remote_file):
            raise BridgeError("115 返回的文件路径与批次清单不一致", review=True)
        return {"size": value(uploaded, "size"), "fileid": str(value(uploaded, "fileid") or "")}
