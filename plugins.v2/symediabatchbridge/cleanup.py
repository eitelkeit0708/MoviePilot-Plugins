"""Optional cleanup of verified organized copies, independent of seeding lifetime."""
from pathlib import Path
import os
import time

from .activity import event
from .domain import refresh_signature, BridgeError, file_signature
from .localfiles import available, remove_file


def cleanup_failure(store, job, message):
    job.update(cleanup_error=message, cleanup_next=time.time() + 3600)
    store.save(job)
    store.record(job, event("cleanup_error", message, level="warning", notice="清理未完成",
                            scope="issue", next_at=job["cleanup_next"]))


def cleanup(store, job, config, stop):
    if job["state"] != "handed_off" or not job.get("cleanup_local") or job.get("cleanup_done"):
        return
    parents = set()
    try:
        available(config)
        for entry in job.get("files", []):
            if entry.get("local_removed"):
                continue
            path = Path(entry["local"])
            if not entry.get("uploaded"):
                raise BridgeError("缺少文件上传回执，保留本地副本", review=True)
            if not entry.get("cleanup_pending") and path.exists():
                config.relative(str(path))
                current = file_signature(path)
                if current[3:] != entry["signature"][3:]:
                    raise BridgeError("整理副本已被替换，保留本地文件：" + entry["relative"], review=True)
                # Unlinking a different hardlink can change ctime. Revalidate once;
                # never require the downloader original to still exist.
                refresh_signature(entry, stop)
                store.save(job)
            remove_file(store, job, entry, config, stop)
            parents.add(path.parent)
            store.record(job, event("cleanup_file", "已删除本地整理副本；未操作下载目录", file=entry["relative"]))
        for parent in sorted(parents, key=lambda p: len(p.parts), reverse=True):
            while parent != Path(config.local_root):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        job.update(cleanup_done=True, cleanup_error="", cleanup_next=0)
        store.save(job)
        store.record(job, event("cleanup_done", "本地整理副本清理完成；未操作下载任务"))
    except BridgeError as error:
        cleanup_failure(store, job, str(error))
    except OSError:
        cleanup_failure(store, job, "本地整理副本暂时无法清理，将自动重试")
