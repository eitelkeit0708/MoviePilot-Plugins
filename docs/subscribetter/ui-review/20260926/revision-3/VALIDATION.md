# 验证记录与边界

产品提交：`164e496a531f54d18a7afccb51d4638706dda4bb`。逻辑测试在 0736735 运行，随后仅补导航 CSS；最终构建和鼠标复核在 164e496 完成。2026-09-26，Windows 本地测试 + 已授权的隔离 MoviePilot V3。没有修改生产容器。

## 自动检查

| 范围 | 实际结果 | 不代表什么 |
| --- | --- | --- |
| 11 个后端相关模块 | 274 项运行，270 通过，4 跳过；[原始终端结果](python-checks.txt) | 不代表 Linux 文件权限或真实外部链路通过 |
| Vue/JS 测试 | 48/48 通过 | 不代表实机所有输入设备或视觉体验合格 |
| Vite 生产构建 | 成功，产物随产品提交 | 不代表服务自动启用 |
| diff whitespace 检查 | 通过 | 不是功能测试 |

四项跳过分别是 POSIX 凭据文件系统权限、POSIX ctime 身份、Linux mountinfo、Linux dirfd/nofollow。此轮没有在 Linux 重跑这些平台检查。

本机版本 Python 3.12.3、Node 24.16.0。外部审核可先建立仓库根目录 `.venv` 并安装 `tests/v3/subscribetter/requirements-review.txt`，前端使用已锁定的依赖；组件契约测试会直接调用根目录 `.venv` 的 Python，不需要 NAS 凭据。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tests/v3/subscribetter/requirements-review.txt
npm ci --prefix plugins.v3/subscribetter/frontend
```

执行命令（仓库根目录；激活上述项目测试环境）：

```powershell
$env:PYTHONPATH='tests/v3/subscribetter'
python -X utf8 -m unittest test_management_display test_management test_management_spec_fix test_management_reload_fix test_management_quality_fix test_native_ui test_configuration test_ai test_archive test_planner test_delivery -q
npm test --prefix plugins.v3/subscribetter/frontend
npm run build --prefix plugins.v3/subscribetter/frontend
git diff 1802760..164e496 --check
python docs/subscribetter/ui-review/20260926/revision-3/verify.py
```

重点覆盖：列表删大字段但摘要可读、详情按需请求/错误/空/过期响应；七条历史的最新五条和同时间戳稳定排序；Planner 实际形成目标规格；所选文件而非种子整体进度；跨集共用文件、未知计数和发布、旧代次和任务代次；完整空草稿流程、检查/保存失败保留；显示最低档不冒充已知音画规格。

组件测试从 Python 生成器读取真实 SQLite → Views DTO，再挂载实际 CandidateDecision 和 UnitProgress。前端预览使用同一生成器的持久化合成输出：

```powershell
python -X utf8 tests/v3/subscribetter/test_management_display.py --fixture --output plugins.v3/subscribetter/frontend/dev/display-fixtures.json
npm run dev --prefix plugins.v3/subscribetter/frontend -- --host 127.0.0.1
```

这不是外部链路：数据数据库是临时合成的，标题、时间、进度都属于测试输入，2099 年时间仅用于稳定的“未来检查”场景。

## 隔离 V3

最终产品 167 个文件经部署 helper 校验后安装成功（HTTP 200）；保留隔离环境原有 CloudDriveDisk、P115Disk 和测试事件探针条目。

配置 revision 始终 271，digest 始终 `b8fedbb7a390b13428692ae856abded034dde5cbdb29bb7ecbaf81cc4cff64b8`；enabled=false、dry_run=true、ordinary_work_active=false、AI=false、runtime generation=1、诊断 errors=[]。原始脱敏结果在 [live-readonly.json](live-readonly.json)。本轮未保存页面配置。

真实候选：一瓯春列表中 evaluation 不存在，摘要 8/16 个目标；展开实际单条详情取得 16 个目标，出现逐版本比较结果。真实计划样本只有两条，服务端最新优先与默认升序逆排一致；超过五条的边界由七条 SQLite 数据证明，不夸大成 NAS 七条测试。

## 交互与原图

- 桌面键盘：实际作品进入/返回、候选展开、配置分组、方案五步和 AI 页面均可操作。
- 非零滚动：进入前最后截图为 917.5 CSS px，返回为 917.5；焦点回到一瓯春作品按钮，筛选保持空字符串。此项不覆盖所有筛选和任意滚动场景。
- 鼠标：本地合成预览的按钮/页签/方案步骤可操作。隔离宿主 390px、DPR=1、重载后，设置入口和方案第 4 步点击正确。桌面 DPR=2 时，对 AI 菜单的自动点击仍落到前一项 RSS，键盘 Enter 正常。后续新标签 1280×720 进一步确认末项被保存栏遮挡：DOM 命中是保存栏。已用内容容器高度限制导航，最终同尺寸下末项命中 AI 自身、切换成功，且方案步骤 2–5 均鼠标点击通过。该修复对应真实遮挡；原窗口的自动化偏移另行观察，不把有限样本扩大成桌面全面验收。
- 窄屏：实际 390×844 CSS px 的宿主截图（0736735），内容宽度 354、根宽度 388；记录了当前样本没有越出该屏宽。之后只修改桌面导航高度，窄屏仍用原有分组选择器。没有真实手机触屏测试。
- 原图未重绘、拼接或美化。JPEG 是截图工具直接输出，不把后缀改成 PNG。文件像素尺寸与 CSS 视口独立记录。
- 每张图的实际产品提交见 capture-metadata.json。host/08–13（不含 10b）在最终菜单修复后重拍，host/20 是鼠标命中修复证据；host/19 是修复前末项被遮挡。历史阶段文案以 host/18 为准，host/06 是中间版本。
- 合成场景截图由 0736735 源码提供，位于 `synthetic/`；之后的差异仅是设置菜单高度。没有拿合成“正在运行”状态更改隔离服务。

## 未放行

UI 的美观性和易用性仍待用户/外部审核判断；桌面宿主鼠标坐标问题、真实触屏、真实新任务下载→115→Symedia→Emby 全流程、AI 实际请求不在本轮通过范围。旧计划未知规格和旧包未知上限是历史事实不足，不通过伪造补齐。
