"""Safe, durable activity descriptions. Never include SDK exceptions or credentials."""
from datetime import datetime
import time


def when(value):
    return datetime.fromtimestamp(value).strftime("%m-%d %H:%M:%S") if value else "—"


def attention(job):
    if job.get("state") == "cancelled":
        return False
    return (job.get("state") in ("review", "retrying") or bool(job.get("late_history_ids"))
            or bool(job.get("disposal_error")) or bool(job.get("retry_requested"))
            or bool(job.get("cleanup_error"))
            or bool(job.get("attempts"))
            or any(e.get("instant_error_since") for e in job.get("files", []))
            or (job.get("state") == "waiting" and time.time() - job["created"] >= 86400))


def event(kind, message, *, level="info", file="", notice="", scope="", next_at=0):
    return dict(kind=kind, message=message, level=level, file=file,
                notice=notice, scope=scope, next_at=next_at)


def changes(old, job):
    if old is None:
        return [event("received", "已接收 MP 整理记录，等待视频与字幕齐套")]
    result = []
    if job.get('owned_at') and not old.get('owned_at'):
        result.append(event('owned', '完整清单已接管；后续以整理副本为准，不再依赖做种源和旧整理记录'))
    previous = {e["relative"]: e for e in old.get("files", []) if "relative" in e}
    for entry in job.get("files", []):
        name = entry.get("relative", "")
        before = previous.get(name, {})
        if entry.get("sha1") and not before.get("sha1"):
            result.append(event("hash", "已记录 SHA1 和文件清单", file=name))
        if entry.get("instant_requests", 0) > before.get("instant_requests", 0):
            result.append(event("instant_start", f"开始秒传；已确认未命中 {entry.get('instant_misses', 0)}/24 次", file=name))
        if entry.get("instant_result_at") != before.get("instant_result_at"):
            if entry.get("instant_error_since"):
                result.append(event("instant_error", entry["instant_error"], level="warning", file=name,
                                    notice="处理异常", scope="issue", next_at=entry.get("instant_next_at", 0)))
            elif entry.get("instant_misses", 0) > before.get("instant_misses", 0):
                misses = entry["instant_misses"]
                action = "普通上传" if misses >= 24 else "再次秒传"
                result.append(event("instant_miss", f"秒传未命中 {misses}/24 次；{when(entry.get('instant_next_at'))} {action}", file=name))
        if entry.get("normal_requests", 0) > before.get("normal_requests", 0):
            result.append(event("normal_start", "秒传 24 次未命中且等待期已结束，开始普通上传", file=name,
                                notice="转普通上传", scope="normal:" + name))
        if entry.get("uploaded") and not before.get("uploaded"):
            method = entry.get("receipt", {}).get("method", "recovered")
            text = {"instant": "秒传成功", "normal": "普通上传完成", "recovered": "已核对并恢复云端文件回执"}.get(method, "上传完成")
            result.append(event("file_complete", text, file=name))
    if job.get("late_history_ids") != old.get("late_history_ids") and job.get("late_history_ids"):
        result.append(event("late_files", job["message"], level="warning", notice="处理异常", scope="issue"))
    if job["state"] == "cancelled" and old["state"] != "cancelled":
        result.append(event("cancelled", job["message"]))
    elif job["state"] == "handed_off" and old["state"] != "handed_off":
        if job.get("completion_basis") == "archive_verified":
            result.append(event("archive_verified", "全部视频与附件已通过云端 SHA1 和大小核对", notice="归档核对完成", scope="handoff"))
        elif job.get("completion_basis") == "directory_identity":
            result.append(event("handoff", "源目录已消失，目标目录 ID 与移交前一致，已恢复移交回执", notice="移交成功", scope="handoff"))
        else:
            result.append(event("handoff", "整目录已移交 Symedia 待归档目录", notice="移交成功", scope="handoff"))
    elif job.get("attempts", 0) > old.get("attempts", 0) or (job["state"] == "review" and old["state"] != "review"):
        result.append(event("failure", job["message"], level="warning", notice="处理异常", scope="issue", next_at=job.get("next_check", 0)))
    elif (job["state"], job["message"]) != (old["state"], old["message"]) and not result:
        result.append(event("state", job["message"], next_at=job.get("next_check", 0)))
    # An administrator only scheduled a check; no file or external service has
    # been verified yet. Do not report the request itself as recovery.
    if (not attention(job) and attention(old) and job["state"] not in ("handed_off", "cancelled")
            and job.get("message") != "已安排重新检查"):
        result.append(event("recovered", "异常已恢复，继续处理"))
    return result


def notice_text(job, item):
    from pathlib import PurePosixPath
    from .media import notification_media

    done = sum(bool(e.get("uploaded")) for e in job.get("files", []))
    lines = notification_media(job, item)[0] + [f"路线：{job.get('route_name', '默认路线')}", item["message"]]
    if item.get("file"):
        lines.append("文件：" + item["file"])
    files = job.get("files", [])
    lines.append(f"文件：{done}/{len(files)} 完成" if files else "文件清单尚未核对完成")
    if not item.get("file"):
        lines.extend(PurePosixPath(entry["relative"]).name for entry in files[:5] if entry.get("relative"))
        if len(files) > 5:
            lines.append(f"另 {len(files) - 5} 个文件，详见插件记录")
    lines.append("批次：" + job["id"])
    if item.get("next_at"):
        lines.append("下次检查：" + when(item["next_at"]))
    if item["kind"] == "handoff":
        lines.extend(["移交目录：" + job.get("destination", job["routing"]["inbox"]),
                      "后续归档由 Symedia 处理。"])
    if item["kind"] == "archive_verified":
        lines.append("核对目录：" + job["recovery_directory"])
    return "\n".join(lines)
