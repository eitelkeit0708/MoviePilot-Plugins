"""MoviePilot V2 adapter. Never patch the host or another plugin."""

from pathlib import Path, PurePosixPath
from copy import copy

from .domain import Awaiting, BridgeError, child_path, file_signature
from .instant import native_provider, try_instant
from .media import history_media
from .cloud_requests import NativeOperations
from .ownership import accept, collect_owned, migrate_sealed, capture


def value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def source_path(path):
    return str(PurePosixPath(str(path or "").replace("\\", "/")))


def download_root(native_files, files):
    """MP savepath can already include the torrent's top-level directory."""
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
        save_path = download_root(native_files, files)
        torrents = self.chain.list_torrents(hashs=download_hash, downloader=downloader) or []
        all_complete = bool(torrents) and all(float(value(t, "progress", 0) or 0) >= 100 for t in torrents)
        expected, torrent_sources = {}, set()
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
            if not complete:
                raise Awaiting("等待本次选择的视频与字幕下载完成")
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
        result = []
        for source in sorted(expected):
            row = newest.get(source)
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
        # Include explicitly associated native attachment transfers, even if they were
        # added locally after the torrent's original file list was created.
        for source, row in newest.items():
            if source in torrent_sources or not self.in_scope(row):
                continue
            if not value(row, "status"):
                raise Awaiting("同一任务还有整理失败的文件：" + PurePosixPath(source).name)
            target = str(value(row, "dest"))
            self.config.relative(target)
            result.append({"local": target, "source": source, "history_id": int(value(row, "id")),
                           "media": history_media(row)})
        if not any(Path(r["local"]).suffix.lower() not in
                   {".srt", ".ass", ".ssa", ".sub", ".idx", ".sup", ".vtt"} for r in result):
            job['orphan_files'] = capture(result)
            raise BridgeError("批次只有字幕，没有对应媒体文件", review=True)
        return accept(job, result)

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
        parent = self._upload_parent(remote_file, operations)
        return try_instant(operations.provider if operations else native_provider(), value(parent, "fileid"), entry, stop,
                           request=operations.request if operations else None)

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
