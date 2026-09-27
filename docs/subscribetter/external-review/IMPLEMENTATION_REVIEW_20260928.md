# subscriBetter 候选分页与共享草稿生命周期修正

本次修正对应外部复核包 `subscriBetter_fd05af1_实现复核_20260927.zip`。产品提交固定为 [`862cd527e2de94365a52b1b60b3e4c8f8cc954eb`](https://github.com/eitelkeit0708/MoviePilot-Plugins/tree/862cd527e2de94365a52b1b60b3e4c8f8cc954eb)。只修复复核中两个可复现的正确性边界；上一轮已经通过的明确候选入口、自动重搜分离和业务来源返回没有重做。

## 结论

| 项目 | 结论 | 证据边界 |
| --- | --- | --- |
| 替代候选在排除、去重后分页 | **PASS（源码／SQLite 回归／挂载组件）** | 后端按不同候选与计划摘要分页；前端使用 `next_offset`。真实宿主没有可执行换源的当前处理中候选，正向 apply 仍为 **BLOCKED / NOT_RUN**。 |
| 共享对象编辑标记在保存后结束 | **PASS（挂载组件连续三次保存）** | A 保存共享映射后，B 的新草稿不会夹带进 A 的下一次保存；B 随后仍可单独保存自己的草稿。未对隔离宿主配置做这组写入。 |
| 隔离 V3 安装与围栏 | **PASS（实际宿主）** | 安装文件哈希与产品提交一致；配置修订、摘要、禁用、演练及诊断状态前后不变。 |
| 真实候选选择、下载、上传及入库 | **BLOCKED / NOT_RUN** | 当前 64 个分集没有 `processing.candidate_key`；没有注入测试候选，也没有调用 apply。 |

## 1. 候选分页单位

[ui.py](../../../plugins.v3/subscribetter/ui.py) 现在在 SQL 中先完成以下处理，再计算 `total`、`limit` 和 `offset`：

1. 限定当前任务、目标、通过状态与活动机会；
2. 可选排除当前候选；
3. 按 `(candidate_key, plan_digest)` 分组，只保留最新记录；
4. 对上述可展示结果分页。

因此，一百条较新的当前资源历史不能再把下一页的替代资源遮住。同一候选的不同计划摘要仍分别保留，避免把实际影响范围不同的计划误合并。

[Subscriptions.vue](../../../plugins.v3/subscribetter/frontend/src/Subscriptions.vue) 每次读取 25 条，传入当前候选作为 `exclude_candidate_key`，并根据服务端 `next_offset` 继续加载。只有读完查询范围后才显示“确实没有其他可选资源”；分页读取失败与空结果仍分别展示。生成的 [contract.json](../../../plugins.v3/subscribetter/frontend/src/contract.json) 已包含新增查询参数。

回归覆盖 101 条历史记录：第一页原始历史全部属于当前资源，较早页存在替代资源；接口现在直接返回两个不同计划摘要的替代候选，分页总数和下一页偏移均以去重后的结果计算。挂载组件另验证空历史页仍有 `next_offset` 时可以继续读取，不会误报无候选。

## 2. 共享编辑归属生命周期

[PlanSetup.vue](../../../plugins.v3/subscribetter/frontend/src/PlanSetup.vue) 继续按方案记录本次明确编辑的共享对象，但新增精确消费操作：[ConfigEditor.vue](../../../plugins.v3/subscribetter/frontend/src/ConfigEditor.vue) 只在宿主保存成功并完成配置回读后，消费这次实际提交的方案 ID 与对象 ID。其他方案、其他对象的未保存归属不会被清空。

挂载真实 `ConfigEditor` 与 `PlanSetup` 的回归顺序是：

1. A 修改共享映射并保存；
2. B 修改同一映射但不保存；
3. A 只修改下载目录并再次保存，提交仍保留 A 上次已保存的映射；
4. B 随后保存，自己的映射草稿仍然存在并进入本次提交。

这条测试覆盖实际组件事件、方案切换、预检、宿主 `put`、保存回读和标记消费，不只是独立调用 `planDraft()`。它仍属于隔离自动化，没有被表述为 NAS 配置写入实测。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 新增后端分页回归 | **6 / 6 PASS**（所在测试文件） |
| 管理接口 `test_management*.py` | **47 / 47 PASS** |
| 前端 Node 测试 | **76 / 76 PASS** |
| 全量 Python | **753 PASS，5 skipped** |
| Python `compileall` | **PASS** |
| Vite 正式构建 | **PASS，113 modules transformed** |
| `git diff --check` | **PASS** |

全量测试中的 AI 限流与超时文字来自预期故障夹具；最终退出码为 0。跳过的 5 项没有被计为通过。

## 4. 隔离 V3 证据

机器可读证据见 [ACTUAL_HOST_EVIDENCE_20260928.json](./ACTUAL_HOST_EVIDENCE_20260928.json)。

- 目标仅为 `moviepilot-v3-subscribetter-test`（`192.168.50.6:13000`）。
- 暂存 195 个提交文件并逐一校验 SHA-256，本地插件安装接口返回 HTTP 200 / success。
- 安装前后配置均为修订 `273`，摘要均为 `4f8a5fade7f69100e7fa40062657d9e881095a8e1e66228619cb2234b7dbd4dc`。
- 安装后仍为 `enabled=false`、`dry_run=true`、`ordinary_work_active=false`、诊断错误 0。
- 实际加载目录中的 `ui.py`、`runtime.py` 和 `remoteEntry.js` 与产品提交哈希一致。

只读候选核验遍历 14 个任务、返回 64 个分集，并在 12 个有分集的任务上实际发送带 `exclude_candidate_key` 的请求；得到 3 条历史可查询记录。没有分集带当前处理中候选，因此界面没有合法的“当前资源 → 替代资源”执行入口。真实正向选择继续记为 **BLOCKED_NO_ACTIONABLE_CURRENT_CANDIDATE**，没有用伪造数据关闭验收项。
