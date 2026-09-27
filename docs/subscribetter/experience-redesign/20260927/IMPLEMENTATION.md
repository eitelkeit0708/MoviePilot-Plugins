# 完整前端实现 · 外部复核入口

2026-09-27。产品提交 **[620ae51](https://github.com/eitelkeit0708/MoviePilot-Plugins/commit/620ae51b15444c1008c2a1783c42439b40aa2067)**；设计依据 `13b3fda`，本轮补充“放弃外层草稿不撤销已保存子策略”。完整普通入口已接入正式组件并部署到隔离 MoviePilot V3。**这不是用户视觉认可，也不是全部真实业务链路重新验收通过。**

外部审核只需 GitHub：本文件、[实际操作步骤](USAGE.md)、[截图索引与原始文件](review/README.md)、[原图与证据 ZIP](subscriBetter-experience-review-620ae51.zip)及下面的固定源码链接。无需访问 NAS、账号、fnos.txt 或私人附件。八张 `concepts/` 图片仍是 `1941efe` 的 AI 概念图，不能作为本轮实现截图。

## 1. 使用范围与实现位置

| 用户任务 | 本轮界面 | 固定源码 |
|---|---|---|
| 看作品、收集情况和具体进展 | 有可信封面则显示；分集查看当前版本、目标、各项变化、进度和允许的操作；新增仍从 MP 原生订阅进入 | [Subscriptions.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/Subscriptions.vue)、[UnitProgress.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/UnitProgress.vue)、[VersionDifference.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/VersionDifference.vue) |
| 查候选、旧版与处理记录 | 有界候选摘要，展开读取单条依据；每集版本历史独立分页；处理计划先排序再分页 | [CandidateDecision.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/CandidateDecision.vue)、[VersionHistory.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/VersionHistory.vue) |
| 改当前作品采用的策略 | 保留分类、策略、作品入口；共享使用范围先展示；规则与草稿试算同页 | [Policies.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/Policies.vue)、[PolicySimulation.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/PolicySimulation.vue) |
| 连续设置一份下载与入库方案 | 五步围绕当前方案，显示名称与稳定身份分开；下载目录和整理后上传来源分开；关联策略可嵌套编辑 | [PlanSetup.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/PlanSetup.vue)、[plan-flow.mjs](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/plan-flow.mjs) |
| 检查本地 STRM 路径 | 当前步骤选择已授权媒体库的一页真实样本，检查当前草稿；四个完整转换结果；不启动自动追踪 | [MappingCheck.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/MappingCheck.vue)、[PathInput.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/PathInput.vue) |
| 看榜单是否已经加入管理 | 作品与收录结果优先；已管理、原生管理、媒体库已有、未加入、待识别分开；来源在同页进入维护 | [Discovery.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/Discovery.vue)、[Sources.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/Sources.vue) |
| 看上传与入库卡在哪里 | 按作品聚合，再展开批次、文件；可以返回作品的下载进度；每个操作沿用后台的独立许可 | [Transfers.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/Transfers.vue) |
| 全局设置和名称识别 | 五个直接分区；片名保护、AI 连接、提示词同页；私密写入、配置保存、测试分别反馈 | [ConfigEditor.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/ConfigEditor.vue)、[AISettings.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/AISettings.vue) |
| 维护与迁移 | 备份、旧榜单读取、功能切换、恢复入口保留；九域原始诊断需主动进入 | [Migration.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/Migration.vue)、[Page.vue](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/Page.vue) |

没有新增依赖。普通流程复用宿主按钮与选择器、已有权限预览、保存回读和质量比较。通用 `Field` / `Record` 保留在高级参数和诊断；常用方案、规则、来源、名称识别没有以通用对象编辑器作为主入口。

## 2. 三项交互合同怎样落实

### 目录职责

“下载保存到”对应 `Destination.save_path`；“从哪里上传”对应 `DeliveryRule.local_root`。后者是 MP 整理后的本地监控目录。接下来才是 115 暂存、Symedia 接收目录。没有自动互填，也没有绝对禁止有意相同；配置是否有效仍由后台检查。设置上传来源不会授予下载目录、保种目录清理权限。

### 嵌套编辑和分层放弃

`Page.vue` 使用最多八层返回记录。父编辑器保留草稿与基线，子策略保存回读后向父层更新相关配置；策略或路径变化会使相关旧试算/路径检查失效。父层其他输入和位置保留。

放弃外层方案仅放弃未保存方案变更；确认框列出本次已经保存的子策略。已生效共享策略不会假回滚。需要撤销时重新编辑该策略。本轮真实宿主已完成一次子策略保存、外层放弃、策略恢复，最终业务配置与开始前一致；配置修订号和保存回执正常变化，见 [nested-save-readback.json](review/evidence/nested-save-readback.json)。

### 分区保存、删除和并发

[configuration-draft.mjs](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/src/configuration-draft.mjs)对“编辑前、当前草稿、最新保存值”做三方比较。未编辑范围保留最新值；删除是实际修改，不能递归补回。稳定 ID 列表按对象合并；质量顺序等有序数组作为完整值核验冲突。当前方案保存只提取该方案与明确涉及的共享对象，其他方案草稿不随之提交。

冲突显示字段、已保存值与本地修改，保留输入，由用户明确解决。原生保存结果未知时不重复提交，只核对保存回执。普通保存仍经既有后台预检和 MoviePilot 原生插件保存入口，不直接改数据库。

## 3. 展示数据和后台边界

主要支持位于 [ui.py](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/ui.py) 与 [display.py](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/display.py)。

- 任务活动筛选和上传作品聚合在服务端分页前执行；当前代次与失效计划区别处理。上传批次和文件仍单独分页，没有把所有完整证据塞进列表。
- 榜单结果结合已关联任务状态，而不是以“识别成功”代替“已管理”；部分目标仍待处理明确表达。
- 封面只来自已有可信 MP 元数据，并检查媒体身份；没有为列表新增整库图片请求。
- 版本改善和降低读取已有比较结果。前端格式化多个变化维度，不重新判质量高低；PGS 推定到明确标注仍是既有条件下的同质量替换例外。
- 草稿试算使用后端实际 `Policy` 和当前完整草稿，不建立计划、不改变保存策略、不启动下载。
- 路径样本只读取当前已授权服务、媒体库的一页项目，再读取授权 MP 路径的 STRM。新范围未授权时不能仅凭草稿扩大读取；路径转换通过也不宣称云端文件存在。
- 新的名称识别界面只承载既有保护与 AI 辅助。MP 原生季集传递、严格名称提取及最终身份确认仍在既有 `meta.py` / `meta_compat.py` / `ai.py` 中；本轮没有新增聊天或绕过原生订阅入口。

旧审核指出的候选详情契约、服务端最近计划排序、数值季集排序等行为继续保留。末次实际点击发现处理记录中还有一个普通入口跳进全局档案诊断，已在 `620ae51` 移至明确的高级诊断下；普通入口现在返回当前作品的分集版本和候选比较，并为较早计划增加分页。

## 4. 部署与恢复状态

部署对象仅为授权隔离容器 `moviepilot-v3-subscribetter-test`，入口端口 13000。最终部署时间 **2026-09-27 11:20:02 UTC**。原生安装返回成功，无需容器重启；139 个安装文件逐项与本地提交前源文件哈希一致。详见 [deployment.json](review/evidence/deployment.json)。

| 项目 | 实际结果 |
|---|---|
| 最终构建入口 | `__federation_expose_Page-DfDfumY1.js`；样式 `__federation_expose_Page-DsjHUO01.css` |
| 原生保存修订号 | 部署前后均 273 |
| 普通追踪 | `enabled=false` |
| 演练模式 | `dry_run=true` |
| 普通后台工作 | `ordinary_work_active=false` |
| 诊断错误 | 空列表 |
| 部署前备份 | 隔离容器内源文件、安装文件与 SQLite；未将数据库或配置私密内容公开 |

页面读取既有真实记录；历史上的下载、入库、失败或未决状态不算本轮重新执行成功。大雄兔旧测试批次中仍有早期 IDX/SUB、字体、许可文件记录，这是历史数据展示，不代表重新纳入本次附件处理范围。

## 5. 连续任务证据

以下 PASS 都有范围，不能相加为整体验收。合成预览使用本轮正式组件，黄色提示常驻，网络调用由本地模拟接口承接。

| 场景 | 状态 | 实际验证与边界 |
|---|---|---|
| 作品 → 当前方案 → 当前策略 → 草稿试算 → 子策略保存回读 → 返回未保存方案 → 放弃外层 | **PASS（隔离宿主）** | 一瓯春 / 国产剧；目录草稿保留，放弃框提示子策略继续生效；原生保存回读至 272，恢复策略至 273；业务配置复核还原 |
| 正常配置菜单与全部业务入口 | **PASS（隔离宿主导航）** | 五个业务入口、五个设置分区、来源、迁移入口均实际打开；不推导每个高级开关都已运行 |
| 本地 STRM 当前草稿检查 | **PASS（隔离宿主只读）** | 一瓯春 S01E13，真实 Emby 样本、实际 STRM、本地挂载内容、CD2 内部路径；没有触发全局扫描，没有验证云端存在 |
| 同作品多个上传批次 | **PASS（隔离宿主读取与预览）** | 大雄兔两个批次分别未知/待 Emby；取消预览被后台阻止，未执行取消、删除或重新上传 |
| 候选完整依据、当前集历史 | **PASS（隔离宿主只读）** | 候选列表展开取得完整 16 个目标；S01E13 取得有效 4K 与停用 1080p；全部版本按钮没有离开作品 |
| 非零滚动返回与焦点 | **PASS（限定宿主场景）** | 稳定后鼠标、键盘分别为 1292.5 → 1292.5，焦点回一瓯春；滚轮尚在运动时一次为 1315.5 → 1292.5，未算精确恢复，原始记录完整保留 |
| 已选作品跨真实页面重载 | **PASS（隔离宿主）** | 重载后重新打开插件，仍为原作品；已确认浏览器 loader 改变 |
| 未保存嵌套草稿跨真实浏览器重载 | **NOT_RUN（完整证据不足）** | 曾观察重载操作后草稿仍在，但当时未核实 loader 是否改变，可能受离开保护影响；不把它算成独立重载验收 |
| 多项改善、改善与降低并存、首轮下载、字幕依据例外、正常已收录、未知总数 | **PASS（正式组件＋合成数据）** | 桌面与 390px CSS 视口；没有真实触发这些新计划 |
| 空白方案逐步校验，未知保存保留输入且不重复提交 | **PASS（合成界面）** | 缺分类阻止继续；分别填下载/上传路径；模拟 503 后草稿保留、核对仍未知；完整空配置真实成功保存 **NOT_RUN** |
| 删除、跨分区保存、其他方案不夹带、并发合并 | **PASS（自动检查）** | 实际组件和纯函数回归；真实宿主双客户端同时修改的完整流程 **NOT_RUN** |
| AI 私密写入部分成功 | **PASS（组件检查）** | 地址写入成功、密钥写入失败时保留成功引用与原密钥，不泄露新密钥；名称保护单独保存不发模型请求。私密写入全部成功后配置失败再恢复的完整流程及真实服务测试 **NOT_RUN**；本轮未启用 AI 或轮换密钥 |
| 迁移切换、冲突处理、恢复 | **NOT_RUN（本轮真实切换）** | 正式入口保留并实际打开；未改旧插件处理者或迁移数据 |
| 真机触屏、真实宿主浅色全部页面、用户视觉认可 | **NOT_RUN** | 390px 是桌面浏览器视口；浅色截图是本地正式组件合成数据；用户视觉认可由用户决定 |
| 本轮重新下载 → 115 → Symedia → Emby 全链路 | **NOT_RUN** | 本轮聚焦正式界面实现和有界验证；历史验收状态未被改为本轮通过 |

## 6. 可复跑的检查

Windows Python 3.12 本地管理检查 45 项通过；前端 Node 测试 69 项通过；Vite 构建 113 modules 通过。输出见 [evidence/](review/evidence/)。这些是工程回归，不能证明美观或真实外部交付。

```powershell
# 仓库根目录
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests/v3/subscribetter -p 'test_management*.py'
# plugins.v3/subscribetter/frontend
npm test -- --test-force-exit
npm run build
```

重点可核对 [configuration-draft.test.mjs](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/test/configuration-draft.test.mjs)、[components.test.mjs](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/plugins.v3/subscribetter/frontend/test/components.test.mjs)、[test_management_experience.py](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/620ae51b15444c1008c2a1783c42439b40aa2067/tests/v3/subscribetter/test_management_experience.py)。

## 7. 外部审核建议

先按 [USAGE.md](USAGE.md)核对普通人的任务路径，再对照截图与组件。尤其检查父子草稿的保存/放弃、共享作用域和删除语义，不只检查有无字段。图片不齐的状态请标 NOT_RUN；不要把黄色合成数据中的下载百分比、下一次检查时间或成功提示当成 NAS 执行记录。

本轮可供实际体验复核；仍需用户判断各页面的可读性、操作连贯性和视觉是否达到要求。报告不替用户填写“视觉定稿”。
