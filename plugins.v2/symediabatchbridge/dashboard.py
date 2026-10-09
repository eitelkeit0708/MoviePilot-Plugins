"""Compact media workbench, rendered by MP V2's native PageRender contract."""
import time

from .activity import attention, when
from .media import notification_media
from .remedies import remedy


CSS = """
.sbb-page{font-size:14px;line-height:1.55;color:rgb(var(--v-theme-on-surface));padding-bottom:54px}
.sbb-page p{margin:0}.sbb-page h3{font-size:19px;line-height:1.5;margin:0;font-weight:650}
.sbb-page h4{font-size:15px;margin:0;line-height:1.5}.sbb-page .v-btn{text-transform:none;letter-spacing:0;font-size:13px}
.sbb-page .sbb-muted{opacity:.64;font-size:12px}.sbb-page .sbb-wrap{overflow-wrap:anywhere;min-width:0}
.sbb-top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:0 0 18px;border-bottom:1px solid rgba(var(--v-border-color),.16)}
.sbb-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.sbb-top .sbb-actions{margin-left:auto}
.sbb-layout{display:grid;grid-template-columns:174px minmax(0,1fr);min-height:540px}
.sbb-sidebar{padding:22px 16px 16px 0;border-right:1px solid rgba(var(--v-border-color),.16);display:flex;flex-direction:column;gap:8px}
.sbb-sidebar .v-btn{justify-content:flex-start;min-height:40px;max-width:100%;white-space:normal;height:auto;padding:10px 12px}
.sbb-sidebar .v-btn__content{white-space:normal;overflow-wrap:anywhere;text-align:left}
.sbb-nav-active{background:rgba(var(--v-theme-primary),.14)!important;color:rgb(var(--v-theme-primary))!important}
.sbb-sidebar-foot{margin-top:auto;padding-top:24px;display:flex;flex-direction:column;gap:6px}
.sbb-main{padding:20px 0 0 24px;min-width:0}.sbb-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px}
.sbb-tabs{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid rgba(var(--v-border-color),.16);padding-bottom:12px;margin-bottom:4px}
.sbb-tabs .sbb-nav-active{border-radius:5px}
.sbb-item{border-bottom:1px solid rgba(var(--v-border-color),.15)}
.sbb-item-open{border:1px solid rgba(var(--v-theme-primary),.65);border-radius:7px;margin:6px 0;overflow:hidden}
.sbb-row{display:grid;grid-template-columns:44px minmax(140px,1fr) minmax(100px,145px) minmax(128px,170px);align-items:center;gap:16px;padding:15px 12px}
.sbb-item-open>.sbb-row{background:rgba(var(--v-theme-primary),.07)}
.sbb-art{width:44px;height:66px;border-radius:5px;overflow:hidden;position:relative;background:rgba(var(--v-theme-on-surface),.05);display:grid;place-items:center}
.sbb-art .v-img{position:absolute;inset:0}.sbb-title{font-weight:600;font-size:16px;overflow-wrap:anywhere}
.sbb-state{display:flex;align-items:center;gap:6px;font-size:13px}.sbb-row-end{text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:3px}
.sbb-inline{padding:16px 20px 20px 72px;display:flex;flex-direction:column;gap:12px}
.sbb-inline>.v-btn{align-self:flex-start}
.sbb-remedy{padding:14px 16px;border-left:3px solid rgb(var(--v-theme-warning));background:rgba(var(--v-theme-warning),.04);border-radius:0 5px 5px 0}
.sbb-remedy p{margin-top:6px;font-size:13px}.sbb-remedy .sbb-actions{margin-top:12px}
.sbb-progress{display:flex;flex-direction:column;gap:6px;margin-top:8px;max-width:650px}
.sbb-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;padding-top:18px}
.sbb-empty{padding:48px 24px;text-align:center}.sbb-detail .v-card{background:transparent}.sbb-detail h3{margin-bottom:12px}
.sbb-page .sbb-detail p{margin:5px 0;overflow-wrap:anywhere}.sbb-page .sbb-detail h4{overflow-wrap:anywhere}
.sbb-kv{padding:14px 0;border-bottom:1px solid rgba(var(--v-border-color),.14);overflow-wrap:anywhere}
.sbb-banner{margin:12px 0}.sbb-banner .v-alert__content{font-size:13px}
@media(max-width:900px){.sbb-layout{grid-template-columns:140px minmax(0,1fr)}.sbb-main{padding-left:16px}.sbb-row{grid-template-columns:40px minmax(110px,1fr) 120px;gap:10px}.sbb-state{grid-column:2;grid-row:2}.sbb-row-end{grid-column:3;grid-row:1 / span 2}.sbb-art{grid-row:1 / span 2;width:40px;height:60px}.sbb-inline{padding-left:16px}}
@media(max-width:600px){.sbb-layout{display:block}.sbb-sidebar{flex-direction:row;flex-wrap:wrap;border-right:0;border-bottom:1px solid rgba(var(--v-border-color),.16);padding:12px 0;gap:4px}.sbb-sidebar>.sbb-muted,.sbb-sidebar-foot{display:none}.sbb-sidebar .v-btn{min-height:34px;padding:6px 9px}.sbb-main{padding:16px 0 0}.sbb-top{gap:8px}.sbb-top .sbb-actions{margin-left:0}.sbb-row{grid-template-columns:36px minmax(80px,1fr) 104px;padding:12px 2px;gap:8px}.sbb-art{width:36px;height:54px}.sbb-title{font-size:14px}.sbb-state{font-size:12px}.sbb-inline{padding:12px}.sbb-tabs{gap:0}.sbb-tabs .v-btn{padding:0 8px;font-size:12px}.sbb-heading h3{font-size:18px}}
@media(max-width:600px){.sbb-sidebar-foot{display:flex;width:100%;flex-direction:row;padding-top:4px;margin-top:0}.sbb-sidebar-foot>.sbb-muted{display:none}}
"""


def node(component="div", children=None, label=None, **props):
    result = {"component": component, "props": props}
    if children is not None:
        result["content"] = children
    if label is not None:
        result["text"] = str(label)
    return result


def txt(label, cls="", tag="div"):
    return node(tag, label=label, **{"class": cls})


def icon(name, color=None, size=18):
    return node("VIcon", icon=name, size=size, color=color)


def action(plugin, label, api="view", params=None, **props):
    button = node("VBtn", label=label, **{"variant": "text", "size": "small", "color": "primary", **props})
    button["events"] = {"click": {"api": f"plugin/{plugin.__class__.__name__}/{api}", "method": "post", "params": params or {}}}
    return button


def navigate(plugin, label, **changes):
    params = plugin._view.model_dump()
    params.update(changes)
    return action(plugin, label, params=params)


def guidance(plugin, job):
    from .disposal import eligible
    plan = job.get('disposal_plan')
    if plan:
        items = [txt("删除孤立字幕并终止批次" if plan['kind'] == 'delete' else "终止此批次", tag="h4"),
                 txt("以下操作不可撤销；批次记录保留。确认有效期为 10 分钟。", tag="p")]
        if plan['kind'] == 'delete':
            items.append(txt(f"将删除本地整理目录中的 {len(plan['files'])} 个字幕文件：", tag="p"))
            items.extend(txt(entry['local'], "sbb-wrap sbb-kv") for entry in plan['files'])
        else:
            items.append(txt("停止重试和异常通知，保留全部本地文件。该批次不会被历史扫描再次接管。", tag="p"))
        items.append(txt("云端已有文件保留，不进行删除或移交。", tag="p"))
        items.append(node(children=[action(plugin, "确认删除字幕" if plan['kind'] == 'delete' else "确认终止", "dispose",
            {"key": job['id'], "action": "confirm", "token": plan['token']}, color="error"),
            action(plugin, "取消", "dispose", {"key": job['id'], "action": "cancel"})], **{"class": "sbb-actions"}))
        return [node(children=items, **{"class": "sbb-remedy"})]
    help_ = remedy(job)
    if not help_:
        return []
    items = [txt(help_["title"], tag="h4"), *[txt(step, tag="p") for step in help_["steps"]]]
    if job.get("next_check") and job["state"] != "handed_off":
        items.append(txt("下次自动检查 " + when(job["next_check"]), "sbb-muted"))
    controls = []
    if help_["retry"]:
        controls.append(action(plugin, "重新检查", "retry", {"key": job["id"]}, color="primary", disabled=not plugin.get_state()))
    if help_.get("recovery"):
        controls.append(action(plugin, "核对归档目录", "recovery", {"key": job["id"]}, disabled=not plugin.get_state()))
    if help_.get("inventory"):
        controls.append(action(plugin, "检查现存文件", "scan", disabled=not plugin.get_state()))
    if eligible(job):
        if help_.get("delete"):
            controls.append(action(plugin, "删除孤立字幕…", "dispose", {"key": job['id'], "action": "delete"},
                                   color="error", disabled=not plugin.get_state()))
        controls.append(action(plugin, "终止此批次…", "dispose", {"key": job['id'], "action": "stop"}, disabled=not plugin.get_state()))
    if job.get("retry_requested"):
        items.append(txt("已安排重新检查；检查完成前保留当前异常。", "sbb-muted"))
    items.append(node(children=controls, **{"class": "sbb-actions"}))
    return [node(children=items, **{"class": "sbb-remedy"})]


def preview_row(plugin, job, expanded):
    from .page import hash_status, state_label
    help_ = remedy(job)
    poster = notification_media(job, {})[1]
    art = [icon("mdi-movie-outline", size=24)]
    if poster:
        art.append(node("VImg", src=poster, alt=job["title"] + " 海报", cover=True, width=44, height=66))
    files = job.get("files", [])
    done = sum(bool(e.get("uploaded")) for e in files)
    # An unsealed empty list is not zero known files. Inventory may already know more.
    scope = f"文件 {done}/{len(files)}" if files else "尚未封存文件清单"
    subtitle = job.get("route_name", "默认路线") + " · " + (help_["title"] if help_ else scope)
    color = "warning" if help_ else "success" if job["state"] == "handed_off" else "primary"
    scheduled = ("下次检查 " + when(job["next_check"]) if job.get("next_check") and job["state"] != "handed_off"
                 else "更新 " + when(job["updated"]))
    row = node(children=[
        node(children=art, **{"class": "sbb-art"}),
        node(children=[txt(job["title"], "sbb-title"), txt(subtitle, "sbb-muted sbb-wrap")]),
        node(children=[icon("mdi-alert-circle-outline" if help_ else "mdi-check-circle-outline" if job["state"] == "handed_off" else "mdi-clock-outline", color),
                       txt(state_label(job))], **{"class": "sbb-state"}),
        node(children=[txt(scheduled, "sbb-muted"), navigate(plugin, "收起详情" if expanded else "处理异常" if help_ else "查看详情",
                                                           expanded="" if expanded else job["id"], key="")], **{"class": "sbb-row-end"})
    ], **{"class": "sbb-row"})
    content = [row]
    if expanded:
        inner = guidance(plugin, job)
        inner.append(txt(job.get("message", ""), "sbb-wrap"))
        progress = hash_status(job)
        if progress:
            inner.append(txt("当前文件 · " + job["hash_progress"].get("file", ""), "sbb-wrap"))
            inner.append(node(children=progress, **{"class": "sbb-progress"}))
        errors = [e for e in files if e.get("instant_error_since") and not e.get("uploaded")]
        for entry in errors[:3]:
            inner.append(txt(entry.get("relative", "") + " · " + entry.get("instant_error", ""), "sbb-muted sbb-wrap"))
        if len(errors) > 3:
            inner.append(txt(f"另有 {len(errors)-3} 个异常文件，见完整记录", "sbb-muted"))
        latest = plugin._store.events(job["id"], limit=1)
        if latest:
            inner.append(txt("最近记录 · " + when(latest[0]["at"]) + " · " + latest[0]["message"], "sbb-muted sbb-wrap"))
        inner.append(navigate(plugin, "完整记录", key=job["id"], files=0, events=0))
        content.append(node(children=inner, **{"class": "sbb-inline"}))
    elif job["state"] == "hashing":
        content.append(node(children=hash_status(job), **{"class": "sbb-inline sbb-progress"}))
    return node(children=content, **{"class": "sbb-item sbb-item-open" if expanded else "sbb-item"})


def runtime_panel(plugin):
    store = plugin._store
    scan = store.meta("last_scan", {})
    items = [txt("运行与目录", tag="h3"), txt("最近检查 " + when(store.meta("last_check"))),
             txt(store.meta("last_check_status", "尚未完成首次检查")),
             txt(f"本轮读取 {scan.get('read',0)} 条整理记录 · 接收范围内 {scan.get('matched',0)} 条 · 新增 {scan.get('new',0)} 批"),
             txt(f"自动接收 {store.meta('activated_at', '未启用')} 之后的 MP 整理记录；历史文件需检查后接管。", "sbb-muted")]
    for route in plugin._runtime.routes if plugin._runtime else []:
        items.append(node(children=[txt(route.name, tag="h4"), txt("本地整理目录 · " + str(route.local_root)),
                                   txt("115 暂存目录 · " + route.staging), txt("移交目录 · " + route.inbox)], **{"class": "sbb-kv"}))
    return items


def inventory_panel(plugin):
    store, view = plugin._store, plugin._view
    scan = store.meta("existing_scan", {})
    items = [txt("现存文件", tag="h3"), txt("检查后选择接管；仅扫描不会上传。", "sbb-muted")]
    if not scan:
        return items + [txt("尚无检查结果", "sbb-empty")]
    items.append(txt("最近检查 " + when(scan.get("at")), "sbb-muted"))
    for row in scan.get("routes", []):
        items.append(txt(f"{row['name']} · {row['files']} 个文件 · 可关联 {row['matched']} · 已接管 {row['known']} · 无可用记录 {row['unmatched']}", "sbb-kv"))
    candidates, unmatched = scan.get("candidates", []), scan.get("unmatched", [])
    for row in candidates[view.page*12:(view.page+1)*12]:
        items.append(node(children=[txt(row["title"], tag="h4"), txt(f"{row['route']} · {row['files']} 个文件 · 整理于 {row['date']}", "sbb-muted"),
                                   action(plugin, "接管此批次", "import", {"history_id": row["history_id"]})], **{"class": "sbb-kv"}))
    if unmatched:
        items.append(txt("未接管文件", tag="h4"))
        for row in unmatched[view.page*12:(view.page+1)*12]:
            items.append(txt(f"{row['route']} · {row['file']} · {row['reason']}", "sbb-kv"))
    if not candidates and not unmatched:
        items.append(txt("当前没有待接管文件", "sbb-empty"))
    items.append(pagination(plugin, view.page, max(len(candidates), len(unmatched))))
    return items


def pagination(plugin, page, total):
    return node(children=[txt(f"第 {page+1} 页 · 共 {total} 条", "sbb-muted"), node(children=[
        action(plugin, "上一页", params={**plugin._view.model_dump(), "page": max(0,page-1), "expanded": ""}, disabled=page==0),
        action(plugin, "下一页", params={**plugin._view.model_dump(), "page": page+1, "expanded": ""}, disabled=(page+1)*12>=total)
    ], **{"class": "sbb-actions"})], **{"class": "sbb-footer"})


def render(plugin):
    from .page import detail
    store, view = plugin._store, plugin._view
    if not store:
        return [node("VAlert", label=plugin._message, type="warning", variant="tonal")]
    board = store.board(route=view.route, status=view.status, page=view.page)
    routes = {r["root"]: r for r in board["routes"]}
    for route in plugin._runtime.routes if plugin._runtime else []:
        routes.setdefault(str(route.local_root), {"root": str(route.local_root), "name": route.name, "count": 0})
    toolbar = [node("VChip", label=plugin._message, color="success" if plugin.get_state() else "warning", size="small", variant="tonal"),
               txt("最近检查 " + when(store.meta("last_check")), "sbb-muted"),
               node(children=[navigate(plugin, "刷新"), action(plugin, "检查现存文件", "scan", disabled=not plugin.get_state())], **{"class": "sbb-actions"})]
    content = [node("style", label=CSS), node(children=toolbar, **{"class": "sbb-top"})]
    if plugin._action_message:
        content.append(node("VAlert", label=plugin._action_message, type="info", variant="tonal", density="compact", **{"class": "sbb-banner"}))
    cooldown = store.meta("u115_cloud_cooldown", {})
    if cooldown.get("until", 0) > time.time():
        content.append(node("VAlert", label="115 访问冷却 · 恢复时间 " + when(cooldown["until"]) + " · 无需反复重试", type="info", variant="tonal", density="compact", **{"class": "sbb-banner"}))
    if store.incoming_count():
        content.append(txt(f"已接收 {store.incoming_count()} 条整理记录，等待纳入批次", "sbb-muted sbb-banner"))
    if store.missing_transfer_count():
        content.append(txt(f"{store.missing_transfer_count()} 条 MP 整理记录已删除，接收快照保留；可在运行与目录中核对接收范围。", "sbb-muted sbb-banner"))
    sidebar = [txt("接收路线", "sbb-muted"), action(plugin, f"全部路线　{sum(r['count'] for r in routes.values())}",
               params={"route": ""}, **{"class": "sbb-nav-active" if not view.route and view.panel == "jobs" else ""})]
    for route in routes.values():
        sidebar.append(action(plugin, f"{route['name']}　{route['count']}", params={"route": route["root"]},
                              title=route["root"], **{"class": "sbb-nav-active" if view.route == route["root"] and view.panel == "jobs" else ""}))
    scan = store.meta("last_scan", {})
    foot = [txt(f"本轮读取 {scan.get('read',0)} 条 · 新增 {scan.get('new',0)} 批", "sbb-muted")]
    inv = store.meta("inventory_status", {})
    if inv:
        foot.append(txt(inv["message"], "sbb-muted sbb-wrap"))
        foot.append(txt("存量处理 " + when(inv.get("at")), "sbb-muted"))
    elif store.meta("inventory_pending", False):
        foot.append(txt("存量处理已排队", "sbb-muted"))
    foot.extend([action(plugin, "现存文件", params={"panel": "inventory"}), action(plugin, "运行与目录", params={"panel": "runtime"})])
    sidebar.append(node(children=foot, **{"class": "sbb-sidebar-foot"}))
    main = []
    if view.key:
        main.append(navigate(plugin, "返回批次列表", key="", files=0, events=0))
        job = store.get(view.key)
        main.extend(detail(plugin, job) if job else [txt("记录不存在")])
    elif view.panel == "inventory":
        main = inventory_panel(plugin)
    elif view.panel == "runtime":
        main = runtime_panel(plugin)
    else:
        main.append(node(children=[txt(routes.get(view.route, {}).get("name", "全部路线" if not view.route else "原路线"), tag="h3"),
                                   txt("异常优先", "sbb-muted")], **{"class": "sbb-heading"}))
        tabs = []
        for key, label in (("all", "全部"), ("attention", "需关注"), ("active", "处理中"), ("done", "已移交"), ("closed", "已终止")):
            tabs.append(action(plugin, label + " " + str(board["counts"].get(key, 0)),
                               params={"route": view.route, "status": key}, **{"class": "sbb-nav-active" if view.status == key else ""}))
        main.append(node(children=tabs, **{"class": "sbb-tabs"}))
        for job in board["jobs"]:
            main.append(preview_row(plugin, job, view.expanded == job["id"]))
        if not board["jobs"]:
            main.append(txt("当前筛选下没有批次" if view.route or view.status != "all" else "暂无批次，新的 MP 整理任务会自动入队。", "sbb-empty"))
        main.append(pagination(plugin, board["page"], board["total"]))
    content.append(node(children=[node(children=sidebar, **{"class": "sbb-sidebar"}),
                                  node(children=main, **{"class": "sbb-main sbb-detail" if view.key else "sbb-main"})], **{"class": "sbb-layout"}))
    return [node(children=content, **{"class": "sbb-page"})]
