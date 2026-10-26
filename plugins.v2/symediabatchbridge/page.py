"""Native MP V2 page: exception-first list, explicit record view, no disclosure triangles."""
from .activity import (attention, blocked_info, cleanup_reason,
                       cleanup_reason_is_retired, move_sent, when)
from pathlib import PurePosixPath

STATES = {"waiting": "等待齐套", "hashing": "计算 HASH", "waiting_instant": "等待秒传", "uploading": "上传中",
          "verifying": "核对中", "moving": "核对移交", "review": "异常待确认",
          "retrying": "等待恢复", "handed_off": "已移交", "cancelled": "已终止", "deleting": "删除字幕"}
STATES["isolating"] = "隔离重复副本"
STATES['waiting_upload'] = '等待首次上传'


def text(value, component="div", **props):
    return {"component": component, "text": str(value), "props": props}


def button(plugin, label, action="view", **params):
    if action == "view":
        params = {**plugin._view.model_dump(), **params}
    return {"component": "VBtn", "text": label, "props": {"variant": "text", "size": "small", "class": "mr-2"},
            "events": {"click": {"api": f"plugin/{plugin.__class__.__name__}/{action}", "method": "post", "params": params}}}


def card(contents, **props):
    return {"component": "VCard", "props": {"variant": "outlined", "class": "pa-4 mb-3", **props}, "content": contents}


def size(value):
    return f"{value / 1024**3:.2f} GiB" if value >= 1024**3 else f"{value / 1024**2:.2f} MiB"


def state_label(job):
    if job["state"] == "handed_off" and job.get("completion_basis") == "archive_verified":
        return "归档文件已核实"
    if job.get("state") not in ("handed_off", "cancelled") and blocked_info(job):
        # A saved move intent is not a sent move; never tell an operator that a
        # queued batch is being reconciled when it has not moved yet.
        return "核对已发送移动" if move_sent(job) else "等待前序批次"
    repair = job.get('scope_repair')
    if job.get('state') in ('waiting', 'isolating') and isinstance(repair, dict):
        label = {'planned': '核对字幕范围', 'isolating': '隔离错配字幕'}.get(repair.get('phase'))
        if label:
            return label
    return STATES.get(job["state"], job["state"])


def hash_status(job):
    progress = job.get("hash_progress")
    if job["state"] != "hashing" or not progress:
        return []
    done, total = progress["done"], progress["total"]
    percent = min(100, done * 100 / max(total, 1))
    return [text(f"已读取 {size(done)} / {size(total)} · {percent:.1f}% · 更新 {when(progress['at'])}", "p", **{"class": "text-caption"}),
            {"component": "VProgressLinear", "props": {"model-value": percent, "color": "primary", "height": 4}}]


def render_page(plugin):
    from .dashboard import render
    return render(plugin)


def detail(plugin, job):
    store, view = plugin._store, plugin._view
    routing = job["routing"]
    from .dashboard import blocked_panel, guidance, scope_repair_panel
    body = [text(job["title"], "h3"), text(state_label(job), "VChip", size="small", color="warning" if attention(job) else "primary"),
            text(job["message"], "p"), text("路线：" + job.get("route_name", "默认路线"), "p"),
            text("接收：" + when(job["created"]) + " · 更新：" + when(job["updated"]), "p"),
            text("本地：" + routing["local_root"], "p"), text("115 暂存：" + routing["staging"] + "/" + job["id"], "p"),
            text("移交：" + job.get("destination", routing["inbox"] +
                 ("/MP 作品目录（含 TMDB ID）" if job.get('handoff_layout') == 'work' else "/" + job["id"])), "p"),
            text("批次：" + job["id"], "p", **{"class": "text-caption"}),
            text(f"MP 整理记录：{', '.join(str(i) for i in job.get('history_ids', [job['history_id']]))} · 下载器：{job.get('downloader') or '未关联'}", "p", **{"class": "text-caption"})]
    body.extend(guidance(plugin, job))
    body.extend(blocked_panel(job))
    # The board previews four items; the explicit complete-record view must
    # remain able to trace every retained item, including large legacy scopes.
    body.extend(scope_repair_panel(job, limit=10000))
    for unit in job.get('work_handoffs', []):
        phase = unit.get('phase')
        state = {'arrived': '已到达', 'requested': '移动已发出，等待核对',
                 'prepared': '已准备，尚未发出移动'}.get(phase, '等待核实')
        body.append(text('作品移交：' + unit['destination'] + ' · ID ' + unit['id'] + ' · ' + state, 'p'))
    if job.get("next_check"):
        body.append(text("下次检查：" + when(job["next_check"]), "p"))
    body.extend(hash_status(job))
    if job.get("cleanup_done"):
        local_copy = "已清理"
    elif job.get("cleanup_error"):
        local_copy = job["cleanup_error"]
    elif job.get("cleanup_local"):
        local_copy = "移交后清理"
    else:
        local_copy = "保留"
    reason = cleanup_reason(job)
    if not job.get("cleanup_done") and not job.get("cleanup_local") and reason:
        local_copy += " · 原因：" + reason
        if cleanup_reason_is_retired(reason):
            local_copy += "（目录已退役或回收，不能据此判断 SA 是否已消费）"
    body.append(text("本地副本：" + local_copy, "p"))
    if job.get('inbox_cleanup'):
        item = job['inbox_cleanup']
        body.append(text('输入目录清理：' + (item.get('error') or item.get('note') or
                         ('已完成' if item.get('done') else '已记录进度，定期自动核对')), 'p'))
        for name in ('batch','date'):
            if item.get(name, {}).get('holding'):
                body.append(text('空目录退役位置：' + item[name]['holding'], 'p', **{'class':'text-caption'}))
    notices, submitted = store.notice_counts(job["id"])
    body.append(text(f"通知：{submitted}/{notices} 已提交 MP · {'通知开启' if plugin._notify else '通知关闭'}", "p"))
    if job["state"] not in ("handed_off", "cancelled", "deleting"):
        from .remedies import remedy
        legacy = routing.get("storage") == "115网盘Plus"
        if legacy or not remedy(job):
            body.append(button(plugin, "确认同一账号，改用 MP 内置 115" if legacy else "重新检查", "retry", key=job["id"], switch_to_native=legacy))
    if job.get("recovery_directory"):
        body.append(text("归档核对：" + job["recovery_directory"], "p"))
    if job.get("source_directory_id"):
        body.append(text("移交目录 ID：" + job["source_directory_id"], "p", **{"class": "text-caption"}))
    if job.get("origin") == "inventory":
        body.append(text("接管方式：现存整理文件", "p"))
    if job.get("owned_at"):
        body.append(text("完整清单接管于 " + when(job['owned_at']) + "；后续以整理副本和本地清单为准", "p"))
    isolated = job.get("duplicate_isolation", {}).get("items", [])
    legacy_isolated = job.get("cloud_duplicate_isolation", {}).get("completed", [])
    if isolated or legacy_isolated:
        complete = sum(item.get("state") == "done" for item in isolated) + len(legacy_isolated)
        body.append(text(f"重复副本：{complete}/{len(isolated) + len(legacy_isolated)} 已隔离保留", "p"))
        for item in (isolated + legacy_isolated)[-8:]:
            body.append(text("隔离：" + item["destination"] + " · 文件 ID " + item["id"], "p", **{"class": "text-caption"}))
    if job.get('recheck_result'):
        checked = job['recheck_result']
        body.append(text('最近重新检查：' + when(checked['at']) + ' · ' + checked['message'], 'p'))
    historical = job.get('historical_transfers', [])
    supplementary = job.get('excluded_downloads', [])
    if supplementary:
        body.append(text(f"本次排除 {len(supplementary)} 个花絮/附加文件；保留本地文件，不上传、不清理。", 'p'))
        for entry in supplementary[:8]:
            body.append(text('保留：' + entry['source'], 'p', **{'class':'text-caption'}))
    if historical:
        body.append(text(f"本次接管 {len(job.get('owned_candidates', []))} 个文件；另 {len(historical)} 个文件沿用 MP 历史整理记录，不重复上传。", 'p'))
    for entry in job.get("deletion_files", []):
        body.append(text(("已删除 · " if entry.get('deleted') else "待删除 · ") + entry['relative'], "p"))
    browser = plugin._recovery_browser
    if browser and browser["key"] == job["id"]:
        path, page = browser["path"], browser["page"]
        body.extend([text("选择归档后的作品目录", "h4"), text(path, "p"),
                     text("支持文件改名；视频和字幕均需通过 SHA1 与大小核对。", "p")])
        if path != browser["root"]:
            body.append(button(plugin, "上一级", "recovery", key=job["id"], path=str(PurePosixPath(path).parent)))
            body.append(button(plugin, "核对此目录", "recovery", key=job["id"], path=path, verify=True))
        for directory in browser["directories"][page * 20:(page + 1) * 20]:
            body.append(button(plugin, PurePosixPath(directory).name, "recovery", key=job["id"], path=directory))
        if page:
            body.append(button(plugin, "上一页目录", "recovery", key=job["id"], path=path, page=page - 1))
        if (page + 1) * 20 < len(browser["directories"]):
            body.append(button(plugin, "下一页目录", "recovery", key=job["id"], path=path, page=page + 1))
    result = [card(body), text("文件清单", "h3", **{"class": "my-3"})]
    files = job.get("files", [])
    for entry in files[view.files * 20:(view.files + 1) * 20]:
        method = entry.get("receipt", {}).get("method")
        status = {"instant": "秒传完成", "normal": "普通上传完成"}.get(method, "已核对云端文件") if entry.get("uploaded") else "待上传"
        block = [text(entry.get("relative", "待记录文件"), "h4"), text(f"{status} · {size(entry.get('size', 0))}", "p"),
                 text(f"秒传未命中 {entry.get('instant_misses', 0)}/24 · 接口尝试 {entry.get('instant_requests', 0)} 次", "p", **{"class": "text-caption"}),
                 text("SHA1：" + entry.get("sha1", "尚未计算"), "p", **{"class": "text-caption"})]
        if entry.get("local"):
            block.append(text("本地文件：" + entry["local"], "p", **{"class": "text-caption"}))
        if not entry.get('uploaded') and not entry.get('instant_requests') and entry.get('first_upload_after'):
            block.append(text('首次可上传：' + when(entry['first_upload_after']), 'p'))
        if not entry.get("uploaded") and entry.get("instant_next_at"):
            action = "普通上传" if entry.get("instant_misses", 0) >= 24 else "再次秒传"
            block.append(text(when(entry["instant_next_at"]) + " " + action, "p"))
        if entry.get("instant_error"):
            block.append(text(entry["instant_error"], "p", **{"class": "text-warning"}))
        archived = next((m for m in job.get("archive_matches", []) if m["relative"] == entry.get("relative")), None)
        if archived:
            block.append(text("已核实：" + job["recovery_directory"].rstrip("/") + "/" + archived["archived_relative"], "p"))
        result.append(card(block))
    if not files:
        for member in job.get("inventory_files", [])[view.files * 20:(view.files + 1) * 20]:
            result.append(text(member.get("local", ""), "p", **{"class": "text-caption"}))
        result.append(text("尚未封存文件清单，请按上方提示核对整理文件。", "p"))
    if view.files:
        result.append(button(plugin, "上一页文件", key=job["id"], page=view.page, files=view.files - 1, events=view.events))
    if (view.files + 1) * 20 < len(files or job.get("inventory_files", [])):
        result.append(button(plugin, "下一页文件", key=job["id"], page=view.page, files=view.files + 1, events=view.events))
    if historical:
        result.extend([text(f'历史整理文件 · {len(historical)} 个', 'h3', **{'class': 'my-3'}),
                       text('这些文件由其他下载任务整理，本批次不接管。MP 整理成功不代表已上传或已归档。', 'p')])
        for entry in historical[view.files * 20:(view.files + 1) * 20]:
            result.append(card([text(PurePosixPath(entry['source']).name, 'h4'),
                                text(f"MP 整理记录 {entry['history_id']} · {entry['date']}", 'p'),
                                text('原整理位置：' + entry['local'], 'p', **{'class': 'text-caption'})]))
        if view.files:
            result.append(button(plugin, '上一页历史文件', key=job['id'], files=view.files - 1))
        if (view.files + 1) * 20 < len(historical):
            result.append(button(plugin, '下一页历史文件', key=job['id'], files=view.files + 1))
    result.append(text("处理时间线", "h3", **{"class": "my-3"}))
    events = store.events(job["id"], view.events)
    if not events:
        result.append(text("此记录建立于日志升级前；后续处理过程会记录在这里。", "p"))
    for item in events:
        block = [text(when(item["at"]) + " · " + item["message"], "div", **{"class": "text-warning" if item["level"] == "warning" else "text-body-2"})]
        if item.get("file"):
            block.append(text(item["file"], "div", **{"class": "text-caption"}))
        result.append({"component": "div", "props": {"class": "py-2 border-b"}, "content": block})
    if view.events:
        result.append(button(plugin, "较新记录", key=job["id"], page=view.page, files=view.files, events=view.events - 1))
    if len(events) == 30:
        result.append(button(plugin, "更早记录", key=job["id"], page=view.page, files=view.files, events=view.events + 1))
    return result
