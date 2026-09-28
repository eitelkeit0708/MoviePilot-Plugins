# subscriBetter UI/UX 整改与宿主验收记录

- 日期：2026-09-28
- 任务基线：`c60fb00e86ad1fbe4a57b2dfee78762727ae8056`
- 截图对应产品提交：`ab50b66233306f11df98bb7ddc25dd20459fc394`
- 分支：`codex/subscribetter-v3`
- 环境：隔离 MoviePilot V3，`http://192.168.50.6:13000`

## 结论

本轮完成任务书要求的前端正确性修复、受支持交付文件投影、窄屏重排和正式宿主页面回归。51 张截图均来自同一隔离 MoviePilot 宿主及同一最终构建，没有使用独立样板或合成业务页面。

当前可以确认：正常入口、三级导航、设置保存范围提示、策略试算上下文、上传文件列表、版本历史布局、390px/320px 响应式和基础键盘路径均已达到本轮断言。真实下载、上传、清理、迁移、配置保存以及宿主故障注入没有获得本轮副作用授权，继续标记 `BLOCKED` 或 `NOT_RUN`，不把组件测试扩写成真实业务成功。

## 固定构建与宿主信息

| 项目 | 记录 |
| --- | --- |
| 浏览器 | Chrome 154.0.0.0，Windows 10 x64 UA |
| 桌面 CSS 视口 | 1280 × 672；外部窗口 1920 × 1032 |
| DPR / visualViewport scale | 2 / 1 |
| 宿主对话框 | client 1270 × 672；scroll 1270 × 672 |
| 插件 region | 1220 × 591；无横向溢出 |
| 主内容区 | client 1210 × 522；scroll 1210 × 1332；`overflow:auto` |
| 320px 主内容 | clientWidth = scrollWidth = 308；仅一级导航独立横向滚动 |
| `remoteEntry.js` SHA256 | `30080a75c2a312b196d18e1529315d3c86ad45b5d823793896d67604b74c08b6` |
| 部署包 SHA256 | `27dff1328724f7077c8536208c68fce3244ec69fee13a073f24652f456eb2e03` |
| 部署前备份标识 | `20260928-232353` |

宿主实际加载资产：

- `remoteEntry.js`
- `__federation_expose_Page-Cuqrmy2_.css`
- `__federation_expose_Page-BQU4NYCw.js`
- `__federation_fn_import-E6wRZccp.js`

运行范围：隔离插件保持“仅检查，不执行”；本轮部署并重启隔离容器，但没有保存插件配置，没有触发下载、上传、清理或迁移。

## UX ID 处理结果

| UX ID | 结果 | 本轮处理 |
| --- | --- | --- |
| UX-01 | PASS | 主壳增加首次加载、失败解释和重试；基础作品详情与辅助页签请求分离；刷新失败保留旧内容并标记过期。 |
| UX-02 | PASS | 状态重试使用三方合并保留本地草稿；重新读取状态不静默覆盖输入；保存结果未知仍沿原回执核对。 |
| UX-03 | PASS | 质量策略保存明确为模块范围；底部列出实际改动的策略、分类绑定和全局限制。 |
| UX-04 | PASS | 试算结果明确策略、分类、草稿/已保存模式和版本；未绑定策略不继承旧分类语义；迟到响应继续被门控。 |
| UX-05 | PASS | 正式宿主完成 390px 与 320px 取证；正文单列，保存和返回可达，主内容无横向裁切。 |
| UX-06 | PASS | 交付详情统一使用后端可见文件投影；只把受支持文本字幕计入必要项。 |
| UX-07 | PASS | 候选比较按结论、原因和决定性维度分组；摘要截断明确显示范围，完整证据仍保留。 |
| UX-08 | PASS | 上传批次动作列按内容布局，“查看”完整可读；长说明允许换行。 |
| UX-09 | PASS | 版本历史移到跨列区域；0、未知、404/503 状态分开；使用 `aria-expanded` / `aria-controls` 并在展开后移动焦点。 |
| UX-10 | PASS | IDX/SUB、字体、许可证从新记录展示、必要等待和阻塞分母排除；历史无 manifest 记录保持兼容，不删除旧证据。 |
| UX-11 | PASS | 诊断刷新只更新运行快照，不覆盖编辑草稿；草稿与已生效状态分别表达。 |
| UX-12 | PASS | 搜索输入与已应用查询分离；轮询复用已应用条件；条件变更归零页码并拦截旧响应。 |
| UX-13 | PASS | 分集提供需核对/处理中优先阅读和明确状态，完整列表仍可访问。 |
| UX-14 | PASS | 收紧普通行与关闭态密度；异常操作就近；保存区保持稳定。 |
| UX-15 | PASS | 排序保留非拖拽键盘路径；文案使用本模块、当前策略和明确返回目的地。 |

## 代码与数据合同变更

- `Page.vue`：首次加载、主壳失败与重试。
- `ConfigEditor.vue`：草稿安全重试、质量策略模块保存范围和具体改动清单。
- `Subscriptions.vue`：已应用搜索条件、辅助详情隔离、版本数量合同。
- `Policies.vue` / `PolicySimulation.vue`：当前对象、试算模式与结果版本一致。
- `Transfers.vue` / `UnitProgress.vue` / `VersionHistory.vue`：文件与路径阅读、必要字幕、批次动作、历史跨列和键盘焦点。
- `CandidateDecision.vue`：将逐目标重复审计压缩为有界业务分组。
- `display.py` / `ui.py`：必要交付集合、受支持文件投影和版本数量。
- `test_management_display.py`：覆盖忽略附件、历史兼容和版本计数。

## 26 项验收矩阵

状态说明：一项可以同时记录不同验证层，例如组件 `PASS`、真实宿主故障注入 `NOT_RUN`。这表示已验证的层通过，不代表未执行层已经通过。

| ID | 结果 | 证据与边界 |
| --- | --- | --- |
| T01 | PASS（组件） / NOT_RUN（宿主 503） | 首次配置失败有错误与重试断言；未在真实宿主注入 configuration 503。 |
| T02 | PASS（组件） / NOT_RUN（宿主超时） | diagnostics 首次失败仍有可恢复主区域；未对宿主制造超时。 |
| T03 | PASS（请求控制） / NOT_RUN（宿主故障注入） | 候选/排除失败与分集基础详情隔离；未修改宿主接口响应。 |
| T04 | PASS（组件） / NOT_RUN（宿主故障注入） | 刷新失败保留旧详情并标记过期；宿主未注入失败。 |
| T05 | PASS（草稿断言） / NOT_RUN（宿主故障注入） | dirty 后重试执行三方合并，输入保留；宿主未注入读取失败。 |
| T06 | PASS（载荷与回读合同） / NOT_RUN（真实保存） | 子策略保存和外层放弃分层处理已覆盖；未写隔离配置。 |
| T07 | PASS | A/B 策略修改的模块保存清单与 payload 范围一致。 |
| T08 | PASS | 全局锁、单策略修改分别出现在共享影响与提交范围。 |
| T09 | PASS | 两种试算请求携带准确策略、分类、模式与版本；未绑定策略不冒充已生效。 |
| T10 | PASS | 可控迟到响应被 read gate 丢弃，不进入新策略或新样本。 |
| T11 | PASS（假时钟） / NOT_RUN（宿主计时等待） | 第二页未提交输入不改变轮询的已应用查询；未用宿主等待 15 秒重复计数。 |
| T12 | PASS | 应用查询归零页码，旧请求不能覆盖新条件。 |
| T13 | PASS（模拟 API） / NOT_RUN（外部改配置） | 诊断使用同一生效快照且不覆盖草稿；未改宿主生效模式。 |
| T14 | PASS（组件） / NOT_RUN（宿主故障注入） | 有 current 时刷新失败显示陈旧状态；未对宿主注入失败。 |
| T15 | PASS | 后端 8 项测试覆盖视频、文本字幕、IDX/SUB、字体、许可证和 legacy manifest；截图 13–14 显示实际投影。 |
| T16 | PASS | 正式宿主截图 13–14 中说明可换行，“查看/返回”完整可读。 |
| T17 | PASS（组件） / NOT_RUN（宿主 404/503） | 0、未知、1 条、多条的组件状态已覆盖；非空宿主截图 22；宿主未注入 404/503。 |
| T18 | PASS（有界摘要） / NOT_RUN（真实多集候选） | 分组、截断范围和例外表达有断言；当前宿主没有满足该组合的真实候选记录。 |
| T19 | PASS（合同） / BLOCKED（副作用） | UNKNOWN、等待、权限和共享范围由后端事实决定且不自动重发；真实取消/重试/清理未授权。 |
| T20 | PASS | 390px 的订阅、详情、上传、方案、设置、榜单、策略见截图 43–47、49–50。 |
| T21 | PASS | 320 CSS px 见截图 48；正文 clientWidth = scrollWidth = 308。 |
| T22 | PASS | Tab、Shift+Tab、Enter、Space 已在正式宿主执行；版本展开与规则排序不依赖拖拽；截图 51 记录 dirty 范围。测试草稿未保存，重新打开后恢复已保存状态。 |
| T23 | PASS（beforeunload/组件） / NOT_RUN（完整宿主关闭矩阵） | dirty guard 存在并有断言；未逐个对 Esc、遮罩和浏览器刷新执行有草稿关闭。 |
| T24 | PASS（合同） / BLOCKED（真实丢响应） | UNKNOWN 后只查询原回执；真实保存响应丢失需要授权隔离故障注入。 |
| T25 | PASS | 榜单/上传进入作品后保留来源、筛选和焦点；正式宿主连续导航已检查。 |
| T26 | PASS（请求门控） / NOT_RUN（宿主长时请求计数） | 隐藏/重开使用请求门控且不让旧请求抢状态；未进行长时宿主网络计数。 |

汇总：`PASS 26（至少一个验证层） / FAIL 0 / BLOCKED 2 个副作用层 / NOT_RUN 16 个宿主注入或长时层`。该汇总不把同一项中未执行的验证层抹掉。

## 截图清单

全部截图的 SHA256 位于 [screenshots.sha256](screenshots/screenshots.sha256)。

- 01–02：订阅列表、作品详情。
- 03–07：下载方案五个页签。
- 08–11：质量策略四个页签。
- 12–14：上传列表、作品展开、批次详情。
- 15–18：榜单作品、来源、运行记录、统计。
- 19–22：候选、完整比较、处理记录、版本历史。
- 23–42：设置五个模块共 20 个三级页签。
- 43–47、49–50：390px 正常入口与详情。
- 48：320 CSS px 订阅列表。
- 51：键盘排序后的未保存范围；仅用于交互证据，未写入配置。

总览：

- [桌面与交互总览](screenshots/contact-desktop.png)
- [移动端总览](screenshots/contact-mobile.png)

代表截图：

- [订阅列表](screenshots/01-subscriptions-desktop.png)
- [上传批次详情](screenshots/14-upload-batch-detail.png)
- [提示词与版本](screenshots/33-settings-prompt-versions.png)
- [390px 下载方案](screenshots/46-mobile-plan.png)
- [320px 订阅列表](screenshots/48-narrow-320-subscriptions.png)

## 自动验证

- 前端：`npm test`，92/92 PASS。
- 前端构建：`npm run build`，PASS。
- 后端：`PYTHONPATH=.tmp-pydeps python -m unittest discover -s tests/v3/subscribetter -p test_management_display.py`，8/8 PASS。
- Git：`git diff --check`，PASS。
- 宿主：最终 `remoteEntry.js` 的本地、local-repo 和运行目录哈希一致。

## 保留的验收边界

以下项目仍需专门授权或可控故障条件，不能由本轮截图和单元测试代替：

- configuration / diagnostics / 作品辅助接口在正式宿主中的 503、超时与 404 注入。
- 真正的策略、方案或设置保存及响应丢失恢复。
- 有合格替代候选时的真实换源执行。
- 下载、115 秒传、CD2 上传、Symedia 整理、Emby 入库、取消交付和清理。
- 实体触屏设备及软键盘弹出后的布局。
