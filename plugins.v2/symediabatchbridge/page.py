"""Native MP V2 page: exception-first list, explicit record view, no disclosure triangles."""
from .activity import attention, when
from pathlib import PurePosixPath
import time

STATES = {"waiting": "等待齐套", "hashing": "计算 HASH", "waiting_instant": "等待秒传", "uploading": "上传中",
          "verifying": "核对中", "moving": "核对移交", "review": "异常待确认",
          "retrying": "等待恢复", "handed_off": "已移交"}


def text(value, component="div", **props):
    return {"component": component, "text": str(value), "props": props}


def button(plugin, label, action="view", **params):
    return {"component": "VBtn", "text": label, "props": {"variant": "text", "size": "small", "class": "mr-2"},
            "events": {"click": {"api": f"plugin/{plugin.__class__.__name__}/{action}", "method": "post", "params": params}}}


def card(contents, **props):
    return {"component": "VCard", "props": {"variant": "outlined", "class": "pa-4 mb-3", **props}, "content": contents}


def size(value):
    return f"{value / 1024**3:.2f} GiB" if value >= 1024**3 else f"{value / 1024**2:.2f} MiB"


def state_label(job):
    if job["state"] == "handed_off" and job.get("completion_basis") == "archive_verified":
        return "归档文件已核实"
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
    store, view = plugin._store, plugin._view
    if not store:
        return [text(plugin._message, "VAlert", type="warning", variant="tonal")]
    counts = store.counts()
    last_check = store.meta("last_check")
    status = store.meta("last_check_status", "尚未完成首次检查")
    header = [text(plugin._message, "VChip", color="success" if plugin.get_state() else "warning", size="small"),
              button(plugin, "刷新", **view.model_dump()),
              text(f"最近检查 {when(last_check)} · {status}", "p", **{"class": "mt-3 text-body-2"}),
              text(f"累计 {sum(counts.values())} 批 · 处理中 {sum(v for k,v in counts.items() if k != 'handed_off')} 批 · 已完成 {counts.get('handed_off', 0)} 批", "p")]
    incoming = store.incoming_count()
    if incoming:
        header.append(text(f"已接收 {incoming} 条整理记录，等待纳入批次", "p", **{"class": "text-body-2"}))
    missing = store.missing_transfer_count()
    if missing:
        header.append(text(f"{missing} 条接收记录已在 MP 中删除，未建立批次；接收快照保留。", "p",
                           **{"class": "text-warning text-body-2"}))
    cooldown = store.meta("u115_cloud_cooldown", {})
    if cooldown.get("until", 0) > time.time():
        header.append(text("115 访问冷却 · 恢复时间 " + when(cooldown["until"]), "p", **{"class": "text-body-2"}))
    scan = store.meta("last_scan", {})
    if scan:
        header.append(text(f"本轮读取 {scan.get('read', 0)} 条整理记录 · 接收范围内 {scan.get('matched', 0)} 条 · 新增 {scan.get('new', 0)} 批", "p", **{"class": "text-body-2"}))
    activated = store.meta("activated_at", "未启用")
    header.append(text(f"自动接收 {activated} 之后的 MP 整理记录；目录中的历史文件不会自动入队。", "p", **{"class": "text-caption"}))
    if plugin.get_state():
        header.append(button(plugin, "检查现存文件", "scan"))
    inventory = store.meta("inventory_status", {})
    if inventory:
        header.append(text("存量处理 · " + when(inventory.get("at")) + " · " + inventory["message"], "p"))
    elif store.meta("inventory_pending", False):
        header.append(text("一次性存量处理已排队，将在下一轮检查中执行。", "p"))
    else:
        previous = store.meta("existing_scan", {})
        if previous.get("imported") is not None:
            header.append(text(f"存量处理 · {when(previous.get('at'))} · 已接管 {previous['imported']} 批", "p"))
    if plugin._runtime:
        for route in plugin._runtime.routes:
            header.append(text(f"{route.name} · {route.local_root}", "div", **{"class": "text-caption"}))
    content = [card(header)]
    if plugin._action_message:
        content.append(text(plugin._action_message, "VAlert", type="info", variant="tonal", **{"class": "mb-3"}))
    if view.key:
        job = store.get(view.key)
        content.append(button(plugin, "返回批次列表", page=view.page))
        if job is None:
            return content + [text("记录不存在", "p")]
        content.extend(detail(plugin, job))
        return content
    jobs = store.page_jobs(view.page)
    if not jobs:
        content.append(text("暂无批次。最近检查时间会持续更新；新的 MP 整理任务将自动入队。", "p"))
    else:
        content.append(text("处理记录 · 异常优先", "h3", **{"class": "mb-3"}))
    for job in jobs:
        done = sum(bool(e.get("uploaded")) for e in job.get("files", []))
        body = [text(job["title"], "h3"), text(("需要关注 · " if attention(job) else "") + state_label(job), "VChip",
                color="warning" if attention(job) else "success" if job["state"] == "handed_off" else "primary", size="small", **{"class": "my-2"}),
                text(job["message"], "p"), text(f"{job.get('route_name','默认路线')} · 文件 {done}/{len(job.get('files', []))} · 更新 {when(job['updated'])}", "p", **{"class": "text-caption"})]
        if job.get("next_check"):
            body.append(text("下次检查 " + when(job["next_check"]), "p", **{"class": "text-caption"}))
        body.extend(hash_status(job))
        body.append(button(plugin, "查看记录", key=job["id"], page=view.page))
        content.append(card(body, color="warning" if attention(job) else None))
    if view.page:
        content.append(button(plugin, "上一页", page=view.page - 1))
    if (view.page + 1) * 12 < sum(counts.values()):
        content.append(button(plugin, "下一页", page=view.page + 1))
    existing = store.meta("existing_scan", {})
    if existing:
        content.append(text("存量检查 · " + when(existing["at"]), "h3", **{"class": "my-3"}))
        for row in existing.get("routes", []):
            content.append(text(f"{row['name']}：{row['files']} 个文件 · 可关联 {row['matched']} 个 · 已接管 {row['known']} 个 · 无可用记录 {row['unmatched']} 个", "p"))
        if existing.get("imported") is not None:
            content.append(text(f"本次已接管 {existing['imported']} 批", "p"))
        content.append(text("以下文件尚未接管。确认需要交给 Symedia 的批次后，再选择接管。", "p"))
        candidates = existing.get("candidates", [])
        for row in candidates[view.page * 12:(view.page + 1) * 12]:
            content.append(card([text(row["title"], "h4"), text(f"{row['route']} · {row['files']} 个已有文件 · 整理于 {row['date']}", "p"),
                                 button(plugin, "接管此批次", "import", history_id=row["history_id"])]))
        if (view.page + 1) * 12 < len(candidates):
            content.append(button(plugin, "下一页存量", page=view.page + 1))
        unmatched = existing.get("unmatched", [])
        if unmatched:
            content.append(text("未接管文件", "h4", **{"class":"my-3"}))
            for row in unmatched[view.page*12:(view.page+1)*12]:
                content.append(text(f"{row['route']} · {row['file']} · {row['reason']}", "p", **{"class":"text-caption"}))
            if (view.page + 1)*12 < len(unmatched):
                content.append(button(plugin, "下一页未接管文件", page=view.page+1))
    return content


def detail(plugin, job):
    store, view = plugin._store, plugin._view
    routing = job["routing"]
    body = [text(job["title"], "h3"), text(state_label(job), "VChip", size="small", color="warning" if attention(job) else "primary"),
            text(job["message"], "p"), text("路线：" + job.get("route_name", "默认路线"), "p"),
            text("接收：" + when(job["created"]) + " · 更新：" + when(job["updated"]), "p"),
            text("本地：" + routing["local_root"], "p"), text("115 暂存：" + routing["staging"] + "/" + job["id"], "p"),
            text("移交：" + job.get("destination", routing["inbox"] + "/" + job["id"]), "p"),
            text("批次：" + job["id"], "p", **{"class": "text-caption"}),
            text(f"MP 整理记录：{', '.join(str(i) for i in job.get('history_ids', [job['history_id']]))} · 下载器：{job.get('downloader') or '未关联'}", "p", **{"class": "text-caption"})]
    if job.get("next_check"):
        body.append(text("下次检查：" + when(job["next_check"]), "p"))
    body.extend(hash_status(job))
    body.append(text("本地副本：" + ("已清理" if job.get("cleanup_done") else job.get("cleanup_error") or "移交后清理" if job.get("cleanup_local") else "保留"), "p"))
    notices, submitted = store.notice_counts(job["id"])
    body.append(text(f"通知：{submitted}/{notices} 已提交 MP · {'通知开启' if plugin._notify else '通知关闭'}", "p"))
    if job["state"] != "handed_off":
        legacy = routing.get("storage") == "115网盘Plus"
        body.append(button(plugin, "确认同一账号，改用 MP 内置 115" if legacy else "重新检查", "retry", key=job["id"], switch_to_native=legacy))
        if job.get("move_requested"):
            body.append(button(plugin, "核对归档目录", "recovery", key=job["id"]))
    if job.get("recovery_directory"):
        body.append(text("归档核对：" + job["recovery_directory"], "p"))
    if job.get("source_directory_id"):
        body.append(text("移交目录 ID：" + job["source_directory_id"], "p", **{"class": "text-caption"}))
    if job.get("origin") == "inventory":
        body.append(text("接管方式：现存整理文件", "p"))
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
        result.append(text("尚未封存文件清单；等待下载与整理记录齐套。", "p"))
    if view.files:
        result.append(button(plugin, "上一页文件", key=job["id"], page=view.page, files=view.files - 1, events=view.events))
    if (view.files + 1) * 20 < len(files):
        result.append(button(plugin, "下一页文件", key=job["id"], page=view.page, files=view.files + 1, events=view.events))
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
