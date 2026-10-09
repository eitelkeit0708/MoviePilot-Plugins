"""Presentation-only recovery guidance; it cannot change a manifest or retry policy.

Older persisted jobs have human-readable errors rather than structured codes.
Recognize only known wording, retain the original evidence, and fall back to a
neutral instruction instead of guessing that a file is safe to delete or skip.
"""
from .activity import attention
from .ownership import SUBTITLES
from pathlib import Path


def remedy(job):
    if not attention(job):
        return None
    message = job.get("cleanup_error") or job.get("message", "")
    file_errors = [entry.get("instant_error", "") for entry in job.get("files", []) if entry.get("instant_error_since")]
    evidence = " ".join([message, *file_errors])
    result = {"title": "处理暂未完成", "steps": ["查看文件清单和最近记录，修复提示的问题后重新检查。"], "retry": job.get("state") != "handed_off"}
    if job['state'] == 'deleting':
        result.update(title="字幕删除尚未完成", steps=[job.get('disposal_error') or '按已确认清单继续删除，逐文件保留进度。',
                      '下一轮到期后自动重试，不会重新上传本批次。'], retry=False)
    elif job.get("cleanup_error"):
        result.update(title="已移交，本地副本保留", steps=[
            "本地清理未完成，不影响已完成的移交。",
            "临时访问或权限问题会自动重试；已替换的文件保留，不会强行删除。做种源到期删除不影响清理。"], retry=False)
    elif job.get("late_history_ids"):
        result.update(title="移交后又收到文件", steps=[
            "先在 Symedia 归档结果中核对新增文件是否齐全。",
            "不会单独补送字幕或重复移交；缺失附件需要在最终归档目录人工核对。"], retry=False)
    elif job.get("routing", {}).get("storage") == "115网盘Plus":
        result.update(title="旧批次需要切换上传接口", steps=[
            "确认 MP 内置 115 与 CD2 登录的是同一个账号。",
            "在完整记录中切换此批次，保留原文件清单和上传回执。"], retry=False)
    elif job.get("move_requested"):
        result.update(title="移交结果待核实", steps=[
            "先重新检查暂存目录与待归档目录，插件会核对原目录身份。",
            "若 Symedia 已归档，选择最终作品目录；全部视频和附件的 SHA1、大小一致后恢复完成状态。"], recovery=True)
    elif "只有字幕" in evidence:
        result.update(title="缺少对应视频", steps=[
            "该批次只有字幕，不能单独交给 Symedia 归档。",
            "可核对清单后删除孤立字幕，或终止批次并保留文件；删除前会检查同目录是否仍有媒体。"], delete=True, retry=False)
    elif any(word in evidence for word in ("限流", "冷却", "访问上限")):
        result.update(title="115 正在冷却", steps=["等待共享冷却结束，所有路线会自动恢复云端请求。", "重新检查不会跳过冷却，也不会增加秒传未命中次数。"])
    elif any(word in evidence for word in ("令牌", "登录", "鉴权", "认证", "权限")):
        result.update(title="连接或权限需要检查", steps=["按下方错误说明核对 MP 的 115 登录或 CD2 令牌、目录权限。", "保存正确配置后重新检查，已有上传回执会保留。"])
    elif any(word in evidence for word in ("映射", "原配置", "原连接", "解析结果", "未整理到配置")):
        result.update(title="目录配置与批次不一致", steps=["对照完整记录中的原本地目录、暂存目录和移交目录核对配置。", "恢复本批次的原目录映射后重新检查；修改路线只影响新批次。"])
    elif any(word in evidence for word in ("发生变化", "被修改", "被替换", "被移除", "内容不同", "清单移除", "校验不一致")):
        result.update(title="文件与原清单不一致", steps=["已停止使用旧 HASH 和回执，避免交付错误版本。", "若原文件已永久删除或替换，可终止此批次；保留已有文件和处理记录。"])
    elif any(word in evidence for word in ("文件不存在", "没有可核实", "不在配置")):
        result.update(title="本地文件缺失或目录不可读", steps=["挂载恢复后会自动重试；不会搜索或接管其他位置的文件。", "确认文件已永久删除时可终止批次；若仅剩字幕，可查看待删清单后删除。"])
        result['delete'] = any(Path(e.get('local', '')).suffix.lower() in SUBTITLES
                               for key in ('files', 'owned_candidates', 'inventory_files', 'orphan_files') for e in job.get(key, []))
    elif any(word in evidence for word in ("同名", "清单外", "重复目标")):
        result.update(title="目标文件存在冲突", steps=["对照本批清单核对暂存或待归档目录中的同名文件。", "先确认冲突文件归属，再处理冲突并重新检查；插件不会自动覆盖。"])
    elif any(word in evidence for word in ("下载任务", "下载器", "下载清单")):
        result.update(title="下载记录需要核对", steps=["检查原下载器任务和文件选择状态，确认 MP 整理记录对应同一批文件。", "若仅剩整理副本，可检查现存文件；仅在范围可核实时接管，不绕过原批次校验。"], inventory=True)
    elif any(word in evidence for word in ("网络", "暂不可用", "超时", "服务端", "未确认完成")):
        result.update(title="外部服务暂不可用", steps=["检查 MP 的 115 连接和 CD2 服务，连接恢复后会自动重试。", "接口报错不计秒传未命中次数，也不会提前转普通上传。"])
    return result
