# subscriBetter 实现复核修正与外部审计说明

本次修正对应外部复核包 `subscriBetter_实现复核_620ae51_20260927.zip`。产品实现提交为 `fd05af1`，只处理复核确认的四组缺口；没有重写后台业务，也没有把单元测试或合成数据表述为真实下载、上传、入库验收。

## 结论台账

| 项目 | 结论 | 证据边界 |
| --- | --- | --- |
| 明确选择替代候选与自动重搜分离 | **PASS（源码／自动化）** | 真实隔离宿主当前 14 个任务中没有可用替代候选，正向弹窗与提交在实际宿主 **BLOCKED**；没有伪造候选。 |
| 榜单 → 作品 → 原榜单 | **PASS（实际宿主）** | 返回后保留“一周口碑电影榜”来源筛选，并把焦点还给原“查看作品”按钮。 |
| 上传与入库 → 作品 → 原上传批次 | **PASS（实际宿主）** | 返回后保留“结果未知”筛选、展开作品和触发按钮焦点。 |
| 共用云盘范围的单方案保存／放弃 | **PASS（自动化）** | 当前方案只纳入独占对象或本次明确编辑的共享对象；另一方案的未保存映射不被夹带或恢复。 |
| 信息密度与普通入口展示 | **PASS（实际宿主截图）** | 空闲作品行、策略作用域、RSS/Cron 摘要、上传文件角色／状态／路径和 390px 页面均在真实隔离 V3 中复核。 |
| 明确候选真实执行、下载、上传、Symedia／Emby 入库 | **NOT_RUN** | 隔离 V3 保持禁用与演练模式；本轮没有制造业务任务或扩大写入。 |

## 1. 明确候选与自动重新寻找

作品分集的“更换资源”现在先读取服务端已经比较通过、仍适用于该任务和分集的候选。用户可以：

1. 选择一条明确候选，查看资源标题、影响集数、文件数以及是否为多集共享文件；
2. 取得不可变预览；
3. 由服务端重新核对配置修订、运行代次、任务 generation、候选决策、计划摘要、策略／解析修订、目标集范围和共享文件范围；
4. 确认后复用现有计划、下载和交付管线。

“排除当前资源并自动重搜”保留为独立动作，界面不再把它称为已经选好了替代资源。实现见 [Subscriptions.vue](../../../plugins.v3/subscribetter/frontend/src/Subscriptions.vue)、[ui.py](../../../plugins.v3/subscribetter/ui.py) 和 [runtime.py](../../../plugins.v3/subscribetter/runtime.py)。

隔离宿主已安装并暴露 `replacement-candidates`、`select-candidate/preview`、`select-candidate/apply` 三条路由。对当前 14 个任务的只读枚举得到 0 条合格替代候选，因此没有执行正向确认，也没有把测试候选注入真实宿主。

## 2. 跨业务入口返回

[Page.vue](../../../plugins.v3/subscribetter/frontend/src/Page.vue) 保存进入作品详情前的来源类型与恢复函数；[Discovery.vue](../../../plugins.v3/subscribetter/frontend/src/Discovery.vue) 和 [Transfers.vue](../../../plugins.v3/subscribetter/frontend/src/Transfers.vue) 分别保存：

- 来源／状态筛选；
- 分页偏移；
- 展开对象；
- 内容滚动位置；
- 触发控件焦点。

直接从订阅列表进入作品仍返回订阅列表。实际宿主连续操作见截图 `03` 和 `04`，不是根据源码推断。

## 3. 共用云盘范围的草稿隔离

[configuration-draft.mjs](../../../plugins.v3/subscribetter/frontend/src/configuration-draft.mjs) 的 `planDraft(base, draft, id, edited)` 不再因为两个方案共享 `cloud_scope` 就把整个范围内的映射都归入当前方案：

- 当前方案独占对象自动属于当前编辑范围；
- 共享对象只有在本次编辑明确标记后才纳入保存或放弃；
- 其他方案的草稿保持原样；
- 真正修改共享对象时仍显示实际影响范围。

回归用例位于 [configuration-draft.test.mjs](../../../plugins.v3/subscribetter/frontend/test/configuration-draft.test.mjs)。没有通过复制共享配置来规避问题。

## 4. 展示调整

- 空闲作品使用内容驱动高度；已有可靠海报时显示小封面。
- 策略页常态显示分类／方案计数，完整作用域按需展开。
- 移动端把单集操作和详情放在同一就近操作区。
- RSSHub 与 Cron 显示为可读运行摘要，不直接铺内部对象。
- 上传文件显示业务角色、文件名和状态，完整路径按需展开。

390×844 复测时，`documentElement`、宿主弹窗、插件根容器、标题、筛选栏和作品详情头均未超过视口宽度。早期两张移动端图片受到浏览器残留缩放影响，已废弃并按真实 390px CSS 视口重拍；没有为错误截图追加 CSS 补丁。

## 5. 自动化验证

在 `fd05af1` 源码及其构建产物上完成以下复跑：

| 检查 | 结果 |
| --- | --- |
| 前端 Node 测试 | **74 / 74 PASS** |
| 管理接口测试 `test_management*.py` | **46 / 46 PASS** |
| 运行时测试 `test_runtime*.py` | **76 / 76 PASS** |
| 原生 UI 合同 `test_native_ui.py` | **8 / 8 PASS** |
| Python `compileall` | **PASS** |
| Vite 正式构建 | **PASS，113 modules transformed** |
| `git diff --check` | **PASS** |

前端测试与 Vite 在受限沙箱内首次因子进程 `EPERM` 失败；允许启动仓库内 Python／esbuild 子进程后原命令完整通过。该记录是执行环境限制，不计作产品测试失败，也没有通过删除测试绕过。

## 6. 隔离 V3 部署与状态围栏

机器可读摘要见 [ACTUAL_HOST_EVIDENCE_20260927.json](./ACTUAL_HOST_EVIDENCE_20260927.json)。

- 宿主：`http://192.168.50.6:13000`
- 容器：`moviepilot-v3-subscribetter-test`
- 安装方式：MoviePilot 本地插件安装接口，未要求宿主重启
- 安装产品提交：`fd05af1`
- 安装前后配置修订：`273`
- 安装前后配置摘要：`4f8a5fade7f69100e7fa40062657d9e881095a8e1e66228619cb2234b7dbd4dc`
- 安装前后状态：`enabled=false`、`dry_run=true`、`ordinary_work_active=false`、诊断错误 `0`

关键安装文件与提交内容一致：

| 文件 | SHA256 |
| --- | --- |
| `ui.py` | `1b7f8798fe0cbb61ab9d6ca81f29b88cec27bd7cfec7c3c0f586d224a570f919` |
| `runtime.py` | `b08f15828d4117cea5469da779f0237b983baad2d481c018c5fc80d65e0de168` |
| `dist/assets/remoteEntry.js` | `57b331ec519ebbf3c0383dcb89c9bb79e32f744b457592f43abfd797c7c4be7a` |

宿主的插件目录可能保留旧的哈希资源文件；当前 `remoteEntry.js` 引用的是本提交生成的资源，活动入口与提交哈希一致。部署前备份保留在隔离仓库的 `.deploy-backups/subscribetter-before-fd05af1.tar.gz`。

## 7. 真实截图

截图位于 [screenshots-20260927](./screenshots-20260927/README.md)，包含 6 张 2560×1440 桌面图和 2 张 390×844 移动图。完整 SHA256、尺寸与证据说明见同目录 `manifest.json`。

这些截图证明当前隔离宿主的页面和指定连续返回行为；它们不证明真实候选执行、下载、115 秒传、Symedia 处理或 Emby 入库。
