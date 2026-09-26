# 多维变化：正式分集组件接入记录

基于 `4c2260c` 的 R2 设计基线，处理本轮追加问题：一次升级可同时改变多个规格，不能只突出第一个决定性维度。本轮开始接入正式代码，但没有宣告整个前端迁移完成。

## 展示与决策分开

- `Policy.compare()` 的逐维排序与首个差异决定结果的规则不变。
- 新增 `Policy.describe_change()`，为每个有效在库版本记录所有策略维度的关系：改善 `1`、相同 `0`、降低 `-1`、未知 `null`。字幕依据更新另用 `evidence` 表示，不创造新的画质档。
- 分辨率、画面和音频可同时改善；音频升级也可叠加字幕依据更新。分辨率提升、音频降低时，仅前者标紫，后者标黄并在手机摘要注明“偏好降低”。
- 画面/高码率的展示使用已知实际事实，不把排序为“不参与比较”设置的零值解释成实测 SDR 或普通码率。例如 1080p Dolby Vision → 4K HDR 仍须明确表现画面规格降低。
- 不在浏览器重新排名、解析文件名或补造未知规格。缺少声明不等于实测普通音轨。

链路：`Policy.describe_change → Planner 计划快照 → display.processing → UnitProgress → VersionDifference`。样板 `Episode` 复用同一个 `VersionDifference`，桌面标出全部改善项，手机逐项显示前后变化。

对多个在库版本分别比较，不能把相对不同旧版的改善合成一份结果。每计划最多保存 20 份对照，超过时保留计数和截断说明。只有版本 ID 匹配、库内修订号仍等于计划基线时才显示改善标记。旧计划没有这份合同，或库内记录后来变化时，保留已有规格并说明依据缺口，不推断紫色高亮。

## 其他小改动

- 特效字幕采用“PGS 推定 → 发布者明确标注”；样板规则文案使用“同质量替换例外”。
- 已关闭的观察/冷却只展示简短摘要，时间参数收进展开区；启用但缺值时保留明确提示。
- 正式 `PathInput` 与样板四段路径共用“展开完整路径”，展示当前输入全文。没有新增文件读取或云端访问能力。
- 默认策略快照重新由 `export_policies.py` 导出；仍是仓库默认值，不是 NAS 保存配置。

## 验证结果与证据边界

| 项目 | 结果 | 说明 |
|---|---|---|
| 后端相关回归 | PASS 129 / SKIPPED 1 | 共 130 项，0 失败。覆盖 policy、planner、management_display、acceptance_policy_boundaries、stale_plan_replan、candidates、execution |
| 前端 `npm test` | PASS 56 | 包括真实 SQLite 展示 DTO 挂载、多版本分别标注、库内修订变化后撤销标记、字幕和音频同时变化 |
| 正式 `npm run build` | PASS | 更新仓库内的 federation 构建产物，未部署到 NAS |
| 样板桌面与 390px 主行 | PASS，限 DOM | 本目录 `browser-desktop.json`、`browser-mobile.json`；包含多维改善、降低、首次下载、正常在库 |
| 正式组件桌面与 390px 主行 | PASS，限 DOM | `browser-formal-*.json`；正式 Vue 组件使用 SQLite 测试导出的合成接口数据，不是 MP 宿主 |
| 长路径展开 | PASS，限 DOM | `browser-path.txt`；所见完整路径为示例，不证明实际挂载可读 |
| 本轮截图与视觉复核 | BLOCKED | 浏览器截图接口多次返回 `Unable to capture screenshot`。读取、交互与视口调整可用。未把 DOM 检查或 R2 旧图当作新截图 |
| 正式 MP 嵌入 / NAS 业务 / 物理触屏 | NOT_RUN | 本轮未连接 NAS、未执行下载上传或改动配置 |

跳过项：`test_execution.ExecutionTests.test_source_hash_checkpoint_detects_same_size_change_with_restored_mtime`，原因是 `POSIX ctime invalidation requires POSIX filesystem`。不是通过，也不属于本轮 UI 视觉证据。

复核入口：在 `plugins.v3/subscribetter/frontend` 执行 `npm run dev`；样板为 `/dev/study-review.html`，正式组件合成预览为 `/dev/index.html`。前者 GATE24 第 1 集同时改善三个维度，第 5 集包含音频降低，第 6 集包含音频与字幕依据同时改善。顶部模式切换用于 390px 嵌入检查，不等同真实手机。

## 尚未完成的正式推广

R2 作为后续接入基线，不再重开整体样板设计。此次完成的是版本差异展示合同与正式分集组件接入；订阅列表的重点摘要、方案连续编辑的完整正式接入仍需继续。尤其不能把当前正式列表中的“另有几种进展”视为已按样板整改。

方案草稿的有界只读 Emby/STRM 检查、稳定 ID 与显示名称分离、当前规则具体名单/条件的同源解释仍待接入。正式策略必须读取实际锁定、覆盖、自定义准入与调度设置，不能直接采用离线样板 JSON。发现、传输、AI、迁移和全部高级设置继续保留在完整交付范围内，尚未统一推广这套组件；没有删除它们或将本轮当作全部前端验收。
