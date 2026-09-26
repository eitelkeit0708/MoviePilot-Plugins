# subscriBetter UI 第三轮聚焦修正 · 待复核

本轮修复展示数据和配置流程，保留第二轮已改善的全宽详情、三个详情页签、原位来源测试、站点选择和路径控件。**功能检查通过不等于 UI 已获用户认可，也不等于真实下载、秒传和入库重新验收通过。**

## 基线与交付

- 输入材料：用户第二轮复核包，产品 `b4c7a30`、材料 `1802760`。
- 最终产品：[`164e496a531f54d18a7afccb51d4638706dda4bb`](https://github.com/eitelkeit0708/MoviePilot-Plugins/commit/164e496a531f54d18a7afccb51d4638706dda4bb)。主体实现 `937301e`，配置步骤滚动修正 `188ec2d`，历史文案修正 `0736735`，最终菜单可视高度修正 `164e496`。
- 分支：`codex/subscribetter-v3`。审核者只需 GitHub，不需 NAS、私密配置或本地路径。
- 本轮 37 张未改动原图：22 张隔离宿主、15 张合成预览。逐张 SHA-256 与实际构建已记录，可运行 `verify.py` 核验。
- [逐步使用说明](USER-JOURNEYS.md)、[统一展示契约](DISPLAY-CONTRACT.md)、[验证与限制](VALIDATION.md)、[原图索引](SCREENSHOTS.md)、[截图原始元数据](capture-metadata.json)、[真实宿主只读核验](live-readonly.json)。

## 复核问题与落点

| 问题 | 本轮修改 | 可独立检查的证据 |
| --- | --- | --- |
| 候选列表读取被删除的 evaluation | 列表保留最多 8 个目标的原因、状态、决定维度；展开再请求单条详情；加载中、失败、无记录分开 | `ui.py: decisions/decision`；`CandidateDecision.vue`；实际 SQLite → DTO → Vue 测试；真实宿主列表 8 项、详情 16 项 |
| 最近计划返回最早五条 | 业务页面显式 `sort=newest`；SQL 在分页前按创建时间与 ID 双降序 | `ui.py: task_plans`；`Subscriptions.vue`；七条记录含同时间戳的测试；宿主两条真实历史只证实该数据集 |
| 当前与目标规格、进展缺失 | 计划形成时冻结已比较的规格；只读投影同任务、同目标、同代次的进度、交付次数及下一次时间 | `planner.py`、`policy.py`、`display.py`、`UnitProgress.vue`；五种 SQLite 合成状态 |
| 摘要看不出画面/音频/字幕差异 | 分辨率、画面、音频为主行，片源/字幕等为次行；声明与实测分开 | `media.mjs`；SDR/DDP 与 DV/无损/明确特效字幕对照测试 |
| 首次设置需要跨页组装引用 | 一个方案内连续五步；自动连接内部配置；草稿检查、保存、生效映射检查分开 | `PlanSetup.vue` 复用现有控件；空配置完整步骤及失败保留测试；宿主现有方案截图 |
| AI 私密保存与启用/测试混淆 | 分开显示连接私密保存、页面配置一致性、本次模型响应 | `AISettings.vue`；真实环境保持关闭，没有调用模型 |
| 较矮窗口末项菜单被遮挡 | 导航高度跟随内容容器，保留帮助区空间，末项可滚动到可点击区域 | `style.css`；host/19 修复前、host/20 修复后及实际鼠标切换 |
| 历史状态文案歧义 | 历史明确“最后记录阶段”；入库确认使用独立可读文案 | `0736735` 已包含在最终产品中；host/17–18 |

## 审核源码入口

以下链接固定到最终产品提交：

- [后端展示 API](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/164e496a531f54d18a7afccb51d4638706dda4bb/plugins.v3/subscribetter/ui.py)
- [按目标汇总展示事实](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/164e496a531f54d18a7afccb51d4638706dda4bb/plugins.v3/subscribetter/display.py)
- [计划快照](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/164e496a531f54d18a7afccb51d4638706dda4bb/plugins.v3/subscribetter/planner.py) / [质量事实](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/164e496a531f54d18a7afccb51d4638706dda4bb/plugins.v3/subscribetter/policy.py)
- [前端源码目录](https://github.com/eitelkeit0708/MoviePilot-Plugins/tree/164e496a531f54d18a7afccb51d4638706dda4bb/plugins.v3/subscribetter/frontend/src)
- [真实 SQLite 契约测试和合成材料生成器](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/164e496a531f54d18a7afccb51d4638706dda4bb/tests/v3/subscribetter/test_management_display.py)
- [实际 Vue 组件测试](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/164e496a531f54d18a7afccb51d4638706dda4bb/plugins.v3/subscribetter/frontend/test/components.test.mjs)

## 仍需关注

1. 复核另外发现较矮窗口中最后一个菜单被保存栏遮挡，已限制菜单高度并允许其滚动。最终隔离宿主 1280×720 下鼠标切换 AI/方案、方案第 2–5 步通过，见 host/20 和重拍的 host/08–13。原窗口的自动化坐标偏移与真实触屏仍单独列出验证边界。
2. 旧计划没有记录完整目标规格及秒传上限时显示未知；不会通过猜文件名补造。新计划才冻结这些事实。
3. 现有方案共享云盘时，会显示同一云盘下已有映射，并提示影响其他方案；新建方案自动建立独立关联。共享设置不是自动复制隔离。
4. 首次映射实测仍要求保存生效且已有扫描样本，当前步骤说明前置条件并提供入口；草稿通过不能代替实测。
5. 本轮没有重新运行真实下载、115 上传、Symedia 整理或清理。视觉是否达到使用要求，仍请以原图和实际操作判断。
