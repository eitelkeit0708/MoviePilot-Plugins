"""Presentation-only recovery guidance; it cannot change a manifest or retry policy.

Older persisted jobs have human-readable errors rather than structured codes.
Recognize only known wording, retain the original evidence, and fall back to a
neutral instruction instead of guessing that a file is safe to delete or skip.
"""
from .activity import attention, blocked_info
from .ownership import SUBTITLES
from pathlib import Path


def remedy(job):
    if not attention(job):
        return None
    message = job.get('inbox_cleanup', {}).get('error') or job.get("cleanup_error") or job.get("message", "")
    file_errors = [entry.get("instant_error", "") for entry in job.get("files", []) if entry.get("instant_error_since")]
    evidence = " ".join([message, *file_errors])
    result = {"title": "处理暂未完成", "steps": ["查看文件清单和最近记录，修复提示的问题后重新检查。"], "retry": job.get("state") != "handed_off"}
    if job['state'] == 'deleting':
        result.update(title="字幕删除尚未完成", steps=[job.get('disposal_error') or '按已确认清单继续删除，逐文件保留进度。',
                      '下一轮到期后自动重试，不会重新上传本批次。'], retry=False)
    elif job.get('inbox_cleanup', {}).get('error'):
        result.update(title="已移交，空目录清理暂缓", steps=[message,
            "文件和原移交记录均保留；连接恢复或目录满足清理条件后自动重试，不会重复上传。"], retry=False)
    elif job.get("cleanup_error"):
        result.update(title="已移交，本地副本保留", steps=[
            "本地清理未完成，不影响已完成的移交。",
            "临时访问或权限问题会自动重试；已替换的文件保留，不会强行删除。做种源到期删除不影响清理。"], retry=False)
    elif blocked_info(job):
        info = blocked_info(job)
        steps = ["同一目标目录由前序批次占用，插件按顺序等待，不会覆盖或合并两侧目录。",
                 "前序批次：" + (info["batch"] or "未记录")]
        if info["destination"]:
            steps.append("目标目录：" + info["destination"])
        if info["reason_label"]:
            steps.append("原因：" + info["reason_label"])
        steps.append("等待超过 30 分钟会进入需关注；到达、清理或退役结束后自动继续。")
        result.update(title="等待前序批次腾出同名目录", steps=steps, retry=False)
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
            "可核对清单后删除孤立字幕，或终止批次并保留文件；删除前会检查本批次视频和字幕目录的直属视频。"], delete=True, retry=False)
    elif "整理目录可访问，但文件已缺失" in evidence:
        result.update(title="整理文件已缺失", steps=[
            "检查时整理根目录可访问，但下方文件已不在原路径；不能据此判断由谁删除或移动。",
            "不会搜索或接管其他位置的文件。确认已由其他任务处理时，可终止此批次，避免重复上传。"])
    elif any(word in evidence for word in ("本地整理目录暂不可用", "本地整理目录无法读取", "本地整理文件暂时无法读取", "等待本地整理文件恢复")):
        result.update(title="本地路径暂不可用", steps=[
            "按下方路径检查挂载、目录或文件访问；恢复后会自动重试。",
            "现有 HASH 和回执保留；不会搜索或接管其他位置的文件。"])
    elif any(word in evidence for word in ("限流", "冷却", "访问上限")):
        result.update(title="115 正在冷却", steps=["等待共享冷却结束，所有路线会自动恢复云端请求。", "重新检查不会跳过冷却，也不会增加秒传未命中次数。"])
    elif "无权读取本地整理文件" in evidence:
        result.update(title="本地文件没有读取权限", steps=["按下方文件路径核对 MP 容器的访问权限；恢复后自动重试。"])
    elif any(word in evidence for word in ("令牌", "登录", "鉴权", "认证", "权限")):
        result.update(title="连接或权限需要检查", steps=["按下方错误说明核对 MP 的 115 登录或 CD2 令牌、目录权限。", "保存正确配置后重新检查，已有上传回执会保留。"])
    elif any(word in evidence for word in ("映射", "原配置", "原连接", "解析结果", "未整理到配置")):
        result.update(title="目录配置与批次不一致", steps=["对照完整记录中的原本地目录、暂存目录和移交目录核对配置。", "恢复本批次的原目录映射后重新检查；修改路线只影响新批次。"])
    elif any(word in evidence for word in ('原种子校验证据', '跨文件分块')):
        result.update(title='文件变化，等待校验证据', steps=[
            '已有 SHA 时核对原 SHA；首次 SHA 尚未完成时，需要原下载任务的种子校验证据。',
            '恢复原下载器或跨文件分块所需的相邻文件后自动复核；证据不足时保留文件、HASH 和上传回执，不会按同名或大小直接放行。'])
    elif any(word in evidence for word in ("发生变化", "已变化", "被修改", "被替换", "被移除", "内容不同", "清单移除", "校验不一致", '内容与原种子不一致')):
        result.update(title="文件与原清单不一致", steps=[
            "原 HASH 和上传回执均保留；校验未通过的文件继续拦截，不会交付整批。",
            "恢复原内容后会自动复核；确认已永久删除或替换时可终止此批次，保留文件和处理记录。"])
    elif any(word in evidence for word in ("文件不存在", "没有可核实", "不在配置")):
        result.update(title="本地文件缺失或目录不可读", steps=["这条旧记录没有区分文件缺失与目录故障；下次检查会核实具体路径。不会搜索或接管其他位置的文件。", "确认文件已由其他任务处理或永久删除时，可终止批次；若仅剩字幕，可查看待删清单后删除。"])
        result['delete'] = any(Path(e.get('local', '')).suffix.lower() in SUBTITLES
                               for key in ('files', 'owned_candidates', 'inventory_files', 'orphan_files') for e in job.get(key, []))
    elif job.get("duplicate_isolation") and any(item.get("state") != "done" for item in job["duplicate_isolation"].get("items", [])):
        result.update(title="重复副本隔离等待恢复", steps=["原件和同内容副本均保留；按已保存的文件 ID 自动恢复隔离核对。", "连接或权限恢复后继续，不会重新上传或重置秒传次数。"])
    elif any(word in evidence for word in ("同名", "清单外", "重复目标", "云端清单尚未一致")):
        result.update(title="目标文件存在冲突", steps=["对照本批清单核对暂存或待归档目录中的同名文件。", "先确认冲突文件归属，再处理冲突并重新检查；插件不会自动覆盖。"])
    elif any(word in evidence for word in ("下载任务", "下载器", "下载清单")):
        result.update(title="下载记录需要核对", steps=["检查原下载器任务和文件选择状态，确认 MP 整理记录对应同一批文件。", "若仅剩整理副本，可检查现存文件；仅在范围可核实时接管，不绕过原批次校验。"], inventory=True)
    elif message.startswith('处理步骤异常：'):
        result.update(title='处理步骤发生异常', steps=[message,
            '具体步骤、异常类型及代码位置已记录在完整记录中；任务会自动重试，HASH、回执及上传等待时间保留。'])
    elif any(word in evidence for word in ("网络", "暂不可用", "超时", "服务端", "未确认完成")):
        result.update(title="外部服务暂不可用", steps=["检查 MP 的 115 连接和 CD2 服务，连接恢复后会自动重试。", "接口报错不计秒传未命中次数，也不会提前转普通上传。"])
    return result
