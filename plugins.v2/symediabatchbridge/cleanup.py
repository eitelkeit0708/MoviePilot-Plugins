"""Optional cleanup of verified organized hardlinks, never downloader originals."""
from pathlib import Path
import os
import time
import tempfile

from .activity import event
from .domain import unchanged, check_stop, BridgeError, file_signature


def cleanup(store, job, config, stop):
    if job["state"] != "handed_off" or not job.get("cleanup_local") or job.get("cleanup_done"):
        return
    parents = set()
    try:
        for entry in job.get("files", []):
            check_stop(stop)
            if entry.get("local_removed"):
                continue
            path = Path(entry["local"])
            source = Path(entry.get("download_source") or "")
            pending = Path(entry["cleanup_pending"]) if entry.get("cleanup_pending") else None
            if pending:
                if pending.parent.parent != path.parent or not pending.parent.name.startswith(".115-helper-clean-"):
                    raise BridgeError("清理暂存路径异常，保留本地文件", review=True)
            if not path.exists() and not (pending and pending.exists()):
                # Resume after unlink committed but before its SQLite receipt.
                try:
                    path.relative_to(Path(config.local_root))
                    path.parent.resolve().relative_to(Path(config.local_root))
                except ValueError:
                    raise BridgeError("清理路径已不属于本地整理目录", review=True) from None
                if path.is_symlink():
                    raise BridgeError("清理路径为符号链接，保留文件", review=True)
                entry["local_removed"] = True
                store.save(job)
                continue
            current = pending if pending and pending.exists() else path
            config.relative(str(current))
            if (current.is_symlink() or source.is_symlink() or not source.is_file()
                    or source.resolve() == path.resolve() or not current.samefile(source)
                    or current.stat().st_nlink < 2):
                raise BridgeError("整理副本不是仍有下载源的硬链接，保留本地文件：" + entry["relative"], review=True)
            if current == path:
                unchanged(entry)
                if not pending:
                    folder = Path(tempfile.mkdtemp(prefix=".115-helper-clean-", dir=path.parent))
                    pending = folder / path.name
                    entry["cleanup_pending"] = str(pending)
                    store.save(job)
                os.rename(path, pending)
            # Check identity AFTER atomically isolating the link: a concurrent MP
            # replacement at the original name must never be unlinked by cleanup.
            actual = file_signature(pending)
            expected = entry["signature"]
            if any(actual[i] != expected[i] for i in (0, 1, 3, 4)) or not pending.samefile(source):
                if not path.exists():
                    try:
                        os.link(pending, path)  # exclusive creation, never overwrite
                        pending.unlink()
                        pending.parent.rmdir()
                        entry.pop("cleanup_pending", None)
                    except OSError:
                        pass
                raise BridgeError("清理时文件发生变化，已保留副本：" + entry["relative"], review=True)
            check_stop(stop)
            pending.unlink()
            try:
                pending.parent.rmdir()
            except OSError:
                pass
            entry["local_removed"] = True
            entry.pop("cleanup_pending", None)
            parents.add(path.parent)
            store.save(job)
            store.record(job, event("cleanup_file", "已删除本地整理副本，保留下载源", file=entry["relative"]))
        for parent in sorted(parents, key=lambda p:len(p.parts), reverse=True):
            while parent != Path(config.local_root):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        job.update(cleanup_done=True, cleanup_error="", cleanup_next=0)
        store.save(job)
        store.record(job, event("cleanup_done", "本地整理副本清理完成，下载源与做种任务保留"))
    except BridgeError as error:
        job.update(cleanup_error=str(error), cleanup_next=time.time()+3600)
        store.save(job)
        store.record(job, event("cleanup_error", str(error), level="warning", notice="清理未完成", scope="issue", next_at=job["cleanup_next"]))
    except OSError:
        job.update(cleanup_error="本地整理副本暂时无法清理，将自动重试", cleanup_next=time.time()+3600)
        store.save(job)
        store.record(job, event("cleanup_error", job["cleanup_error"], level="warning", notice="清理未完成", scope="issue", next_at=job["cleanup_next"]))
