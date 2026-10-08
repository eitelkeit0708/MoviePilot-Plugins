"""Inventory only existing files and their latest native transfer record."""
from pathlib import Path
import os
import time

from .domain import check_stop, file_signature
from .host import value


def scan(runtime):
    known = runtime.store.source_keys()
    latest = {}
    for row in runtime.host.histories_since("1970-01-01 00:00:00"):
        check_stop(runtime.stop)
        dest = str(value(row, "dest") or "")
        if dest not in latest or int(value(row,"id")) > int(value(latest[dest],"id")):
            latest[dest] = row
    groups, summaries, unmatched = {}, [], []
    for route in runtime.routes:
        summary = {"name": route.name, "root": route.local_root, "files": 0, "matched": 0, "unmatched": 0, "known": 0}
        root = Path(route.local_root)
        if not root.is_dir():
            raise OSError("Organized root unavailable")
        def fail(error):
            raise error
        for directory, _, filenames in os.walk(root, followlinks=False, onerror=fail):
            for name in filenames:
                check_stop(runtime.stop)
                path = Path(directory) / name
                if path.is_symlink() or not path.is_file():
                    continue
                summary["files"] += 1
                row = latest.get(str(path))
                identity = (str(value(row,"downloader") or ""), str(value(row,"download_hash") or ""))
                valid = row and value(row,"status") and all(identity) and runtime.host.for_config(route).in_scope(row)
                if not valid:
                    summary["unmatched"] += 1
                    unmatched.append({"route":route.name, "file":str(path.relative_to(root)), "reason":"无可用 MP 下载整理记录或不支持的文件类型"})
                    continue
                route.relative(str(path))
                if identity in known:
                    summary["known"] += 1
                    continue
                summary["matched"] += 1
                group = groups.setdefault(identity, {"history_id":int(value(row,"id")), "title":str(value(row,"title") or path.stem),
                                                     "route":route.name, "date":str(value(row,"date")), "files":0, "members":[]})
                group["files"] += 1
                group["members"].append({"local": str(path), "history_id": int(value(row, "id")),
                                         "signature": file_signature(path)})
        summaries.append(summary)
    return {"at":time.time(), "candidates":list(groups.values()), "routes":summaries, "unmatched":unmatched}
