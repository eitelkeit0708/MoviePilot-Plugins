"""Safe, durable activity descriptions. Never include SDK exceptions or credentials."""
from datetime import datetime
from pathlib import PurePosixPath
import time
import math

from .ownership import SUBTITLES


# A normal serial handoff can wait for one predecessor tick. Half an hour of the
# same durable block is no longer routine and belongs in the attention bucket.
BLOCKED_ATTENTION_SECONDS = 30 * 60

BLOCKED_REASON_LABELS = {
    "prepared": "前序批次已准备，尚未发出移动",
    "requested": "前序批次移动已发出，等待到达",
    "arrived": "前序批次已到达，等待消费清理",
    "cleanup_pending": "前序批次目标目录清理未完成",
    "isolating": "前序批次仍在隔离重复副本",
    "retired": "前序批次目录已退役",
    "recycled": "前序批次目录已回收",
    "prepared_earlier": "前序批次已准备，按封存顺序移交",
    "requested_move_pending": "前序移动已发出，等待目录 ID 核实",
    "arrived_cleanup_pending": "前序批次已到达，等待目录消费和安全退役",
    "retired_pending_recycle": "前序目录已退出监控区，正在核对退役回执",
    "cleanup_disabled": "前序空目录清理已关闭，暂不能复用同名目录",
}

SCOPE_REASON_LABELS = {
    "same_name": "同名目标冲突",
    "named_conflict": "同名目标冲突",
    "duplicate": "同名重复内容",
    "unmatched": "未匹配到本轮交付范围",
    "outside_scope": "不在本轮交付范围",
    "orphan_subtitle": "没有对应视频的字幕",
    "supplementary": "附加内容不纳入正片",
    "local_only": "仅本地存在",
    "cloud_only": "仅云端存在",
}


def when(value):
    return datetime.fromtimestamp(value).strftime("%m-%d %H:%M:%S") if value else "—"


def _scalar_text(value):
    """Backend strings must never break rendering; never stringify dicts/lists."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _epoch(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        return 0
    return value


def duration_text(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时" + (f" {minutes} 分钟" if minutes else "")


def move_sent(job):
    """True only when this batch actually persisted a move request/receipt.

    ``move_requested`` is also set before the first RPC (a saved intent), so it
    cannot distinguish "waiting for a predecessor" from "checking a sent move".
    """
    if _epoch(job.get("move_last_requested_at")):
        return True
    if _epoch(job.get("move_requested_at")):
        return True
    for unit in job.get("work_handoffs") or []:
        if not isinstance(unit, dict):
            continue
        if _epoch(unit.get("requested_at")) or _epoch(unit.get("first_move_requested_at")):
            return True
        if unit.get("phase") in ("requested", "arrived"):
            return True
    return False


def blocked_info(job, now=None):
    """Tolerant view of ``job.blocked_by``; None for legacy jobs or no usable data."""
    blocked = job.get("blocked_by")
    if not isinstance(blocked, dict) or not blocked:
        return None
    batch = _scalar_text(blocked.get("batch"))
    destination = _scalar_text(blocked.get("destination"))
    raw_reason = _scalar_text(blocked.get("reason"))
    if not (batch or destination or raw_reason):
        return None
    since = _epoch(blocked.get("since"))
    current = time.time() if now is None else now
    wait = max(0, int(current - since)) if since else None
    label = BLOCKED_REASON_LABELS.get(raw_reason.casefold(), raw_reason) if raw_reason else ""
    sent = move_sent(job)
    return {"batch": batch, "destination": destination, "reason": raw_reason,
            "reason_label": label, "since": since, "wait": wait, "sent": sent,
            "phase": "checking" if sent else "waiting"}


def blocked_lines(job, now=None):
    """Truthful details for a serial handoff wait; never calls the network."""
    info = blocked_info(job, now)
    if not info:
        return []
    lines = ["等待前序批次：" + (info["batch"] or "未记录")]
    if info["destination"]:
        lines.append("等待目标目录：" + info["destination"])
    if info["reason"]:
        label = info["reason_label"]
        lines.append("原因：" + (f"{info['reason']} · {label}" if label and label != info["reason"]
                                 else info["reason"]))
    else:
        lines.append("原因：未记录")
    lines.append("移动状态：" + ("移动已发出，正在核对目标目录与到达回执" if info["sent"]
                                else "尚未发出移动，正在等待目标目录可用"))
    if info["wait"] is not None:
        lines.append(f"已等待 {duration_text(info['wait'])}（自 {when(info['since'])}）")
    else:
        lines.append("已等待时间：未记录")
    return lines


def cleanup_reason(job):
    """Optional cleanup reason from known shapes; absent in older jobs."""
    containers = [job]
    for key in ("cleanup", "inbox_cleanup", "handoff_cleanup", "cleanup_status"):
        value = job.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for index, container in enumerate(containers):
        keys = ("cleanup_reason", "cleanup_disabled_reason", "cleanup_skip_reason", "disabled_reason")
        if index:
            keys += ("reason",)  # A nested cleanup object owns its generic reason.
        for key in keys:
            value = _scalar_text(container.get(key))
            if value:
                return value
    return ""


def cleanup_reason_is_retired(reason):
    """Recycle/retire proves the old directory left SA, not that SA consumed it."""
    text = _scalar_text(reason).casefold()
    return any(token in text for token in ("recycle", "retire", "回收", "退役"))


def _is_subtitle(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            if PurePosixPath(value.strip()).suffix.casefold() in SUBTITLES:
                return True
    return False


def _path(mapping, keys):
    if not isinstance(mapping, dict):
        return ""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def scope_repair_info(job):
    """Tolerant view of the additive scope-repair journal; never mutates it."""
    repair = job.get("scope_repair")
    if not isinstance(repair, dict) or not repair:
        return None
    raw_items = repair.get("items")
    items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
    rows = []
    for item in items:
        entry = item.get("entry") if isinstance(item.get("entry"), dict) else {}
        candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
        original = _path(entry, ("local", "original", "source", "file", "relative", "path",
                                 "cloud", "remote", "remote_path"))
        state = _scalar_text(item.get("state")).casefold()
        quarantine = _path(item, ("destination", "quarantine", "quarantine_path", "isolated_to", "isolated_path"))
        if state in ('unuploaded', 'not_uploaded', 'kept', 'preserved', 'retained'):
            quarantine = _path(candidate, ('quarantine', 'quarantine_path', 'isolated_to', 'isolated_path')) or original
        if not quarantine:
            quarantine = _path(candidate, ("quarantine", "quarantine_path", "isolated_to",
                                       "isolated_path", "moved_to", "destination", "relative",
                                       "file", "path", "cloud", "remote", "remote_path", "local"))
        if not quarantine:
            quarantine = _path(item, ("quarantine", "quarantine_path", "isolated_to", "isolated_path"))
        if not quarantine:
            quarantine = _path(repair, ("quarantine", "quarantine_root"))
        state = _scalar_text(item.get("state")).casefold()
        raw_reason = _scalar_text(item.get("reason"))
        subtitle = (bool(item.get("subtitle")) or _is_subtitle(original, quarantine)
                    or _scalar_text(entry.get("type")).casefold() in ("subtitle", "字幕")
                    or _scalar_text(candidate.get("type")).casefold() in ("subtitle", "字幕"))
        if state in ("done", "complete", "completed", "isolated", "moved"):
            state_label = "已隔离（未交付）"
        elif state in ("unuploaded", "not_uploaded", "kept", "preserved", "retained"):
            state_label = "未上传，保留（未交付）"
        elif state in ("planned", "pending"):
            state_label = "计划隔离（未交付）"
        elif state in ("isolating", "moving"):
            state_label = "隔离中（未交付）"
        else:
            state_label = (state or "状态未记录") + "（未交付）"
        rows.append({"original": original, "quarantine": quarantine, "subtitle": subtitle,
                     "state": state, "state_label": state_label, "reason": raw_reason,
                     "reason_label": SCOPE_REASON_LABELS.get(raw_reason.casefold(), raw_reason)})
    total = len(rows)
    subtitles = sum(row["subtitle"] for row in rows)
    isolated = sum(row["state"] in ("done", "complete", "completed", "isolated", "moved")
                   for row in rows)
    unuploaded = sum(row["state"] in ("unuploaded", "not_uploaded", "kept", "preserved", "retained")
                     for row in rows)
    planned = sum(row["state"] in ("planned", "pending") for row in rows)
    phase = _scalar_text(repair.get("phase")).casefold()
    phase_label = {"planned": "已计划，尚未执行", "isolating": "正在隔离",
                   "complete": "已完成"}.get(phase, phase or "状态未记录")
    return {"phase": phase, "phase_label": phase_label, "rows": rows, "total": total,
            "subtitles": subtitles, "isolated": isolated, "unuploaded": unuploaded,
            "planned": planned, "other": total - isolated - unuploaded - planned}


def attention(job):
    if job.get("state") == "cancelled":
        return False
    blocked = blocked_info(job)
    return (job.get("state") in ("review", "retrying") or bool(job.get("late_history_ids"))
            or bool(job.get("disposal_error")) or bool(job.get("retry_requested"))
            or bool(job.get("cleanup_error"))
            or bool(job.get('inbox_cleanup', {}).get('error'))
            or bool(job.get("attempts"))
            or any(e.get("instant_error_since") for e in job.get("files", []))
            or (job.get("state") == "waiting" and _epoch(job.get("created"))
                and time.time() - job["created"] >= 86400)
            or (blocked is not None and job.get("state") not in ("handed_off", "cancelled")
                and blocked["wait"] is not None and blocked["wait"] >= BLOCKED_ATTENTION_SECONDS))


def event(kind, message, *, level="info", file="", notice="", scope="", next_at=0):
    return dict(kind=kind, message=message, level=level, file=file,
                notice=notice, scope=scope, next_at=next_at)


def notification_grace(job):
    errors = [e for e in job.get('files', []) if e.get('instant_error_since')]
    return (bool(errors) and job['state'] not in ('review', 'retrying')
            and not any(job.get(k) for k in ('attempts', 'cleanup_error', 'disposal_error', 'late_history_ids'))
            and not job.get('inbox_cleanup', {}).get('error')
            and all(e.get('instant_error_kind') in ('network', 'service', 'unknown', 'transient')
                    and time.time() - e['instant_error_since'] < 300 for e in errors))


def changes(old, job):
    if old is None:
        return [event("received", "已接收 MP 整理记录，等待视频与字幕齐套")]
    result = []
    if job.get('recheck_result') != old.get('recheck_result') and job.get('recheck_result'):
        result.append(event('recheck_completed', '重新检查完成：' + job['recheck_result']['message'],
                            next_at=job.get('next_check', 0)))
    if job.get('historical_transfers') != old.get('historical_transfers') and job.get('historical_transfers'):
        result.append(event('historical_scope',
                            f"{len(job['historical_transfers'])} 个文件已有其他下载任务的 MP 整理记录，不纳入本次上传；详见历史整理文件"))
    before_excluded = {r['history_id'] for r in old.get('excluded_transfers', [])}
    before_supplementary = {r['source'] for r in old.get('excluded_downloads', [])}
    for row in job.get('excluded_downloads', []):
        if row['source'] not in before_supplementary:
            result.append(event('supplementary_excluded',
                f"{row['directory']} 附加内容不纳入正片交付，保留本地文件；不代表已上传或归档",
                file=row['source']))
    for row in job.get('excluded_transfers', []):
        if row['history_id'] not in before_excluded:
            result.append(event('scope_excluded',
                                f"MP 整理记录 {row['history_id']} 不属于当前下载文件及附件范围，已排除；不代表已上传或归档",
                                file=row['source']))
    if job.get('owned_at') and not old.get('owned_at'):
        result.append(event('owned', '完整清单已接管；后续以整理副本为准，不再依赖做种源和旧整理记录'))
    previous = {e["relative"]: e for e in old.get("files", []) if "relative" in e}
    old_units = {u['source']:u for u in old.get('work_handoffs', [])}
    for unit in job.get('work_handoffs', []):
        before = old_units.get(unit['source'], {})
        if unit['phase'] != before.get('phase'):
            result.append(event('work_handoff', {'prepared':'作品目录已封存', 'requested':'已提交作品目录移动',
                'arrived':'已按目录 ID 核实作品到达 SA 输入目录'}[unit['phase']] + '：' + unit['destination']))
        cleanup = unit.get('cleanup', {})
        if cleanup.get('phase') != before.get('cleanup', {}).get('phase') and cleanup.get('phase'):
            result.append(event('work_cleanup', '作品输入目录清理：' + cleanup['phase'] + ' · ' + unit['destination']))
    for entry in job.get("files", []):
        name = entry.get("relative", "")
        before = previous.get(name, {})
        verified = entry.get('content_revalidation')
        if verified and verified != before.get('content_revalidation'):
            text = ('原 SHA 校验一致，已自动恢复文件属性' if verified['basis'] == 'sha1' else
                    f"原种子 {verified['piece_count']} 个分块全部通过，已自动恢复并复用本次 SHA")
            result.append(event('content_revalidated', text, file=name))
        if entry.get("sha1") and not before.get("sha1"):
            result.append(event("hash", "已记录 SHA1 和文件清单", file=name))
            if entry.get('first_upload_after', 0) > entry.get('hash_completed_at', 0):
                result.append(event('first_upload_wait', '首次上传等待至 ' + when(entry['first_upload_after']),
                                    file=name, next_at=entry['first_upload_after']))
        if entry.get("instant_requests", 0) > before.get("instant_requests", 0):
            result.append(event("instant_start", f"开始秒传；已确认未命中 {entry.get('instant_misses', 0)}/24 次", file=name))
        if entry.get("instant_result_at") != before.get("instant_result_at"):
            if entry.get("instant_error_since"):
                quiet = (entry.get('instant_error_kind') in ('network','service','unknown','transient')
                         and time.time() - entry['instant_error_since'] < 300)
                result.append(event("instant_error", entry["instant_error"], level="warning", file=name,
                                    notice="" if quiet else "处理异常", scope="issue", next_at=entry.get("instant_next_at", 0)))
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
    if (not attention(job) and attention({**old, 'retry_requested': None}) and job["state"] not in ("handed_off", "cancelled")
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
        targets = [u['destination'] for u in job.get('work_handoffs', [])] or [job.get('destination', job['routing']['inbox'])]
        lines.extend('移交目录：' + p for p in targets)
        lines.append('后续归档由 Symedia 处理；输入目录清空不代表归档或入库已完成。')
        repair = scope_repair_info(job)
        if repair and repair['total']:
            lines.append(f"另有 {repair['total']} 个不匹配附件保留，未纳入本次移交，详见范围修复记录。")
    if item["kind"] == "archive_verified":
        lines.append("核对目录：" + job["recovery_directory"])
    return "\n".join(lines)
