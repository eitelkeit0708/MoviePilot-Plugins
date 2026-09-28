# subscriBetter UIUX 二次复核修正

本次修正对应外部复核包 `subscriBetter_UIUX_二次复核包_ab50b662_20260929.zip`，产品提交为 [`f5b650f23e7d98ea5e219e987ef4072c8c41e083`](https://github.com/eitelkeit0708/MoviePilot-Plugins/tree/f5b650f23e7d98ea5e219e987ef4072c8c41e083)。复核包中的 18 项断言均已转为可运行回归；没有借本轮修正改写下载、上传或生产配置。

## 结论

| 范围 | 结论 | 证据边界 |
| --- | --- | --- |
| R2-01 配置冲突跨刷新保留 | **PASS** | 冲突字段合并保存至会话草稿；刷新、模块切换与恢复不会静默丢失。 |
| R2-02 策略已保存绑定与草稿绑定分离 | **PASS** | 试算基线读取已保存绑定；界面草稿仍可独立修改。 |
| R2-03 诊断只展示已确认状态 | **PASS** | 状态仓库保留最后一次成功快照；刷新失败时明确标记过期，不以草稿冒充运行状态。 |
| R2-04 作品基础详情与辅助请求解耦 | **PASS** | 基础详情先展示；候选、计划、档案、历史分别加载、报错和重试，同作品旧数据可保留并标记过期，切换作品立即失效。 |
| R2-05 版本历史可访问性 | **PASS** | 触发按钮的 `aria-controls` 指向真实内容节点；关闭后焦点返回原按钮，404 与 503 分开表达。 |
| R2-06 分集筛选先于分页 | **PASS** | `processing` / `attention` 在 SQL 分页前完成；总数、页码和翻页稳定。 |
| R2-07 旧附件安全边界 | **PASS** | 视频和受支持文本字幕使用同一共享判定；字体、IDX/SUB 等旧记录不能因依赖闭包重新进入交付，隐藏未确认附件形成明确阻断。 |
| R2-08 窄屏策略定位 | **PASS** | 只滚动策略选择条；实际 390px 宿主测试中页面 `scrollTop` 保持 0。 |

## 实现要点

- [ConfigEditor.vue](../../../plugins.v3/subscribetter/frontend/src/ConfigEditor.vue) 在读取远端配置前先保存会话草稿，随后恢复冲突集合；成功保存只消费本次已经提交的状态。
- [Policies.vue](../../../plugins.v3/subscribetter/frontend/src/Policies.vue) 与 [PolicySimulation.vue](../../../plugins.v3/subscribetter/frontend/src/PolicySimulation.vue) 分开保存态和编辑态，并把试算请求时的分类、策略与配置版本固定下来，迟到响应不能覆盖新选择。
- [Subscriptions.vue](../../../plugins.v3/subscribetter/frontend/src/Subscriptions.vue) 把作品主体与四类辅助数据拆成独立读取状态；同一作品允许保留旧辅助数据，跨作品不复用。
- [ui.py](../../../plugins.v3/subscribetter/ui.py) 在数据库中先按业务状态筛选分集，再统计和分页。
- [display.py](../../../plugins.v3/subscribetter/display.py) 提供唯一的附件支持判定；初选、依赖闭包与界面统计共用该结果。
- [VersionHistory.vue](../../../plugins.v3/subscribetter/frontend/src/VersionHistory.vue) 修正了真实 DOM 关联和焦点返回。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 外部复核 RT01–RT18 | **18 / 18 PASS** |
| 前端 Node 测试 | **108 / 108 PASS** |
| 全量 Python | **760 PASS，5 skipped** |
| 原生 UI 回归 | **8 / 8 PASS** |
| 管理与展示专项 | **12 / 12 PASS** |
| Python `compileall` | **PASS** |
| Vite 正式构建 | **PASS，110 modules transformed** |
| `git diff --check` | **PASS** |

跳过的 5 项没有计为通过。故障夹具产生的 AI 限流与超时日志属于预期输入，完整测试进程退出码为 0。

## 隔离 V3 实际宿主

机器可读记录见 [ACTUAL_HOST_EVIDENCE_20260929.json](./ACTUAL_HOST_EVIDENCE_20260929.json)。

- 仅更新并重启 `moviepilot-v3-subscribetter-test`（`192.168.50.6:13000`）；生产容器未修改。
- 运行目录与持久插件源均更新为同一份 182 文件构建，关键文件 SHA-256 与本地构建一致。
- 宿主诊断页显示订阅追踪未启用、执行方式为只检查计划、任务保护运行中、配置读取正常。
- 真实作品详情可加载当前版本和版本历史入口；真实 Emby 服务与媒体库名称可读取。
- 390px 宽度下，从首个策略切换到屏外的最后一个策略后，策略条 `scrollLeft=2137.60009765625`，页面 `scrollTop=0`。

浏览器截图接口在本轮实际宿主复核时持续超时，因此没有把失败的截图调用包装成图像证据。已有自动截图用于静态页面覆盖，本轮新增结论来自可访问树、DOM 状态和可重复数值检查。

## 未执行范围

| 项目 | 状态 | 原因 |
| --- | --- | --- |
| 真实配置冲突写入 | **NOT_RUN** | 会改变隔离宿主配置；使用挂载组件与会话缓存回归覆盖。 |
| 故意制造 Emby / 辅助接口失败 | **NOT_RUN** | 未中断共享服务；错误与恢复状态由组件回归覆盖。 |
| 真实下载、上传、清理、入库 | **NOT_RUN** | 本轮是 UI 状态与展示合同复核，隔离实例保持禁用和演练。 |
| 生产容器 | **NOT_RUN / UNTOUCHED** | 不属于本轮授权范围。 |
