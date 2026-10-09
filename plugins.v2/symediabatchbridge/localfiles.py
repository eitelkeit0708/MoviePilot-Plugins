"""Delete only an accepted local file, with a durable isolation intent."""
import os
import tempfile
from pathlib import Path

from .domain import BridgeError, check_stop, file_signature


def available(config):
    root = Path(config.local_root)
    if root.is_symlink() or not root.is_dir():
        raise BridgeError("本地整理目录暂不可用，稍后自动重试")
    with os.scandir(root) as entries:
        next(entries, None)


def remove_file(store, job, entry, config, stop, *, pending_key="cleanup_pending", done_key="local_removed"):
    """Persist rename destination before touching the file; never overwrite a replacement."""
    available(config)
    check_stop(stop)
    if entry.get(done_key):
        return
    path = Path(entry["local"])
    root = Path(config.local_root)
    try:
        relative = path.relative_to(root)
        path.parent.resolve(strict=True).relative_to(root)
    except (ValueError, OSError):
        raise BridgeError("删除路径或父目录不可用，保留文件", review=True) from None
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise BridgeError("删除路径包含符号链接，保留文件", review=True)
    if entry.get("download_source") and Path(entry["download_source"]) == path:
        raise BridgeError("该路径是下载源，不能作为整理副本删除", review=True)
    pending = Path(entry[pending_key]) if entry.get(pending_key) else None
    if pending and (pending.parent.parent != path.parent or pending.name != path.name
                    or not pending.parent.name.startswith(".115-helper-clean-")
                    or pending.parent.is_symlink() or pending.is_symlink()):
        raise BridgeError("清理暂存路径异常，保留文件", review=True)
    if pending and pending.is_file():
        config.relative(str(pending))
    elif not path.exists():
        # A persisted intent allows replay after unlink and before its receipt.
        entry[done_key] = True
        store.save(job)
        return
    else:
        config.relative(str(path))
        if file_signature(path) != entry["signature"]:
            raise BridgeError("删除前文件已变化，保留文件：" + path.name, review=True)
        if not pending:
            pending = Path(tempfile.mkdtemp(prefix=".115-helper-clean-", dir=path.parent)) / path.name
            entry[pending_key] = str(pending)
            store.save(job)
        os.rename(path, pending)
    actual, expected = file_signature(pending), entry["signature"]
    if any(actual[i] != expected[i] for i in (0, 1, 3, 4)):
        if not path.exists():
            try:
                os.link(pending, path)
                pending.unlink()
                pending.parent.rmdir()
                entry.pop(pending_key, None)
                store.save(job)
            except OSError:
                pass
        raise BridgeError("清理时文件发生变化，已保留副本：" + path.name, review=True)
    check_stop(stop)
    pending.unlink()
    try:
        pending.parent.rmdir()
    except OSError:
        pass
    entry[done_key] = True
    entry.pop(pending_key, None)
    store.save(job)
