"""MoviePilot V2 adapter. Use public Chain/Oper methods, never patch the host."""

from pathlib import Path, PurePosixPath

from .domain import Awaiting, BridgeError, child_path


def value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def source_path(path):
    return str(PurePosixPath(str(path or "").replace("\\", "/")))


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
        return self.transfers.list_by_date(since) or []

    def collect(self, job):
        download_hash, downloader = job["download_hash"], job["downloader"]
        if not download_hash or not downloader:
            raise BridgeError("缺少下载任务标识，无法自动确认附件范围；此任务需人工处理", review=True)
        native_files = [row for row in self.downloads.get_files_by_hash(download_hash, state=1)
                        if value(row, "downloader") == downloader]
        if not native_files:
            raise Awaiting("等待 MP 下载文件清单")
        save_paths = {source_path(value(row, "savepath")) for row in native_files if value(row, "savepath")}
        if len(save_paths) != 1:
            raise BridgeError("下载文件的保存路径无法唯一确定", review=True)
        save_path = next(iter(save_paths))
        files = self.chain.torrent_files(tid=download_hash, downloader=downloader)
        if not files:
            raise Awaiting("无法读取下载器文件清单；请保留下载任务并检查下载器连接")
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
        if previous is not None and previous != expected and job.get("files"):
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
            target = str(value(row, "dest") or "")
            self.config.relative(target)
            if Path(target).stat().st_size != expected[source]:
                raise BridgeError("整理文件大小与下载清单不同：" + Path(target).name, review=True)
            result.append({"local": target, "history_id": int(value(row, "id"))})
        # Include explicitly associated native attachment transfers, even if they were
        # added locally after the torrent's original file list was created.
        for source, row in newest.items():
            if source in torrent_sources or not self.in_scope(row):
                continue
            if not value(row, "status"):
                raise Awaiting("同一任务还有整理失败的文件：" + PurePosixPath(source).name)
            target = str(value(row, "dest"))
            self.config.relative(target)
            result.append({"local": target, "history_id": int(value(row, "id"))})
        if not any(Path(r["local"]).suffix.lower() not in
                   {".srt", ".ass", ".ssa", ".sub", ".idx", ".sup", ".vtt"} for r in result):
            raise BridgeError("批次只有字幕，没有对应媒体文件", review=True)
        return result

    def upload(self, local: Path, remote_file: str):
        parent = self.storage.get_folder(storage=self.config.storage, path=Path(remote_file).parent)
        if not parent or value(parent, "type") != "dir":
            raise BridgeError("无法建立 115 暂存目录，请检查储存插件是否启用")
        if source_path(value(parent, "path")) != source_path(str(Path(remote_file).parent)):
            raise BridgeError("115 返回的上传目录与批次路径不一致", review=True)
        uploaded = self.storage.upload_file(fileitem=parent, path=local, new_name=local.name)
        if not uploaded:
            return None
        if source_path(value(uploaded, "path")) != source_path(remote_file):
            raise BridgeError("115 返回的文件路径与批次清单不一致", review=True)
        return {"size": value(uploaded, "size"), "fileid": str(value(uploaded, "fileid") or "")}
