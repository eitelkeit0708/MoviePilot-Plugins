# subscriBetter V3 外部审核入口

**最新 UI 正式推广（2026-09-27）**：产品提交 [`62275c4`](https://github.com/eitelkeit0708/MoviePilot-Plugins/tree/62275c40ec34d5aa88c4f380b2253463b58ced46)；请先阅读[本轮审核与证据边界](../ui-review/20260927/full-rollout/README.md)和[完整用户步骤](../ui-review/20260927/full-rollout/USER_GUIDE.md)。下面的整体架构、台账及版本表属于历史审核快照，不是最新 UI 的版本号；本轮不重新放行历史业务台账。截图仍阻断，不能用 DOM 文本证明视觉认可。

本资料包供**只能访问 GitHub 的独立审核 agent**使用。代码、设计基线、需求与验收矩阵、可复现测试、架构图和证据边界均在仓库内；不需要本地设计 ZIP、NAS、SSH、站点账户或 `fnos.txt`。资料包是审核输入，不是审核通过证明。

## 版本与结论边界

| 项目 | 固定范围 |
|---|---|
| 产品代码 | [`efb56bb3cdae83aa81ade49e9c4d62d618827ec6`](https://github.com/eitelkeit0708/MoviePilot-Plugins/tree/efb56bb3cdae83aa81ade49e9c4d62d618827ec6/plugins.v3/subscribetter) |
| 分支 | `codex/subscribetter-v3`；本资料包及补齐的测试随此分支发布 |
| 比较基线 | [`cbd770e364ec9a96a81fbfe9ac8d33abdb2bb1ba`](https://github.com/eitelkeit0708/MoviePilot-Plugins/tree/cbd770e364ec9a96a81fbfe9ac8d33abdb2bb1ba) |
| 宿主合同 | [MoviePilot v3.0.4 / e195cc164fc8ff869ffee0ea44a49c7ec475310c](https://github.com/jxxghp/MoviePilot/tree/e195cc164fc8ff869ffee0ea44a49c7ec475310c) |
| 架构图生成器 | [tt-a1i/archify / 9e35d2b0b39b155553ba9fcfe0b4f2a5198dd993](https://github.com/tt-a1i/archify/tree/9e35d2b0b39b155553ba9fcfe0b4f2a5198dd993) |
| 历史台账 | 200 项：193 登记通过、6 未闭环、T183 随独立聊天移除；活跃项 199 |
| 发布定位 | 开发分支审核包，**不是完整验收完成或生产发布宣告** |

产品树的逐文件 SHA-256 见 [manifest.json](manifest.json)。本次公开文档及测试资料补齐不改变 `efb56bb` 的产品实现。审核者应记录实际克隆到的文档提交 SHA；分支名会移动，产品和宿主比较应使用上表固定 SHA。

“193 通过”是经过回查后的**历史台账状态**，覆盖多次提交、源码合同、离线行为、受控宿主试验和部分真实链路；不表示 193 项均在当前提交重新做过真实端到端测试。仅有 GitHub 访问权限的审核者不能独立证实未公开原始记录的 NAS 结果。

本次公开复验新增发现 **PUB-01：提交的前端 dist 与源码的 watcher 配置约束/标签不同**；18 项前端测试及构建成功未消除该差异，详见 [验证说明](validation.md#4-前端检查与构建)。此发现没有被算成原验收矩阵新增通过项。

**PUB-02** 保留首次全量离线复验的一次 `TICK_DEADLINE` 错误及其定向重跑结果，见 [Python 验证说明](validation.md#3-python-离线检查)。不能将最新一次通过用来抹去已有失败记录。

## 阅读顺序

1. [架构、执行逻辑与能力边界](architecture-and-logic.md)：系统职责、完整流程、状态、不变量、权限及故障恢复。
2. [MP 原生识别优化的准确位置](native-recognition.md)：原始宿主调用链、两个补丁点、内部解析、豆瓣身份、流控、AI 和覆盖限制。
3. [验证与证据说明](validation.md)：复现命令、平台差异、六个未闭环项、真实证据的版本与可访问性。
4. [完整验收矩阵](acceptance.md) / [机器版](acceptance.json)：全部 R01–R65、T001–T200，逐项预期、状态和缺口。
5. [证据目录](evidence-index.json)：829 条历史证据的公开摘要、原运行提交、层次、限制及原件摘要；不含私人原始日志。
6. [原始设计基线](spec/01_统一设计文档.md)、[原始矩阵](spec/03_验收矩阵.md)、[整合与迁移范围](spec/06_整合范围与配置迁移.md)。必须同时应用下方修订优先级。
7. [源码符号索引](source-index.json)、[产品操作说明](../../../plugins.v3/subscribetter/README.md)、[测试目录](../../../tests/v3/subscribetter)。

两份完整 JSON 超过 GitHub 的部分预览限制；需要机器读取时使用 [acceptance Raw](https://github.com/eitelkeit0708/MoviePilot-Plugins/raw/refs/heads/codex/subscribetter-v3/docs/subscribetter/external-review/acceptance.json) 和 [evidence Raw](https://github.com/eitelkeit0708/MoviePilot-Plugins/raw/refs/heads/codex/subscribetter-v3/docs/subscribetter/external-review/evidence-index.json)，或克隆仓库。Raw 链接跟随分支；审核固定快照时请把 `refs/heads/codex/subscribetter-v3` 替换为实际文档提交 SHA。

## Archify 架构图

![Archify 架构图](architecture.visual-check.1440x900.light.png)

[大图](architecture.visual-check.2048x1320.light.png) · [暗色大图](architecture.visual-check.2048x1320.dark.png) · [交互 HTML](architecture.html) · [架构定义 JSON](architecture.json) · [生成与检查回执](architecture-receipt.json)

GitHub 不执行仓库 HTML。可直接看 PNG；允许下载的审核环境可以下载 `architecture.html` 后本地打开，查看节点的固定提交源码链接。无需部署图表服务。箭头展示主要逻辑关系；所有调用和信任边界以架构正文及代码为准，图不是完整函数调用图或物理网络拓扑。

## 设计基线之后的明确修订

用户后续决定优先于历史设计文本。以下修订不可按旧设计误报成缺失功能：

| 修订 | 当前要求 |
|---|---|
| 榜单 | 自部署 RSSHub 基址 + 豆瓣相对路由；支持用户提供 URL 的 `@@TV` / `@@Movie` 类型后缀；不使用不可用的公共 RSSHub fallback |
| 聊天 | 全部移除独立消息/聊天功能；MP 自带 agent。AI 只辅助识别名称，T183 不参与活跃验收 |
| 附件 | IDX/SUB、字体、许可文件不作为现实使用的必需交付验收；保留已有通用文件测试不等于增加产品要求；视频及必要 ASS/SRT 字幕仍须齐套 |
| 豆瓣身份 | 优先按 subject ID 取宿主详情；别名用于查询，不能拿名字/年份吻合冒充跨源 ID 证明 |
| 豆瓣请求 | 必须有显式间隔和持久缓存；同作品长期在榜不应每轮重读详情 |
| 消费者恢复 | Symedia 依靠自身事件监听和扫描恢复已有文件；插件仍需回读真实归档/STRM/Emby，不能以通知成功代替入库 |
| 环境边界 | V3 隔离容器；Symedia 单容器授权，测试采用已有实例中的独立测试范围。公开包无真实账户、目录授权或生产操作许可 |
| 例外 | T003/T006/T060 的现实工况缺失可作为有界例外候选；仍保留未闭环状态，不改写为 passed |

## 可直接交给外部 agent 的任务

> 请独立审核本 GitHub 分支的 subscriBetter V3。先读本目录 README、architecture-and-logic、native-recognition、validation、完整 acceptance 矩阵及历史设计修订表。以产品提交 efb56bb3cdae83aa81ade49e9c4d62d618827ec6 和 MoviePilot 提交 e195cc164fc8ff869ffee0ea44a49c7ec475310c 为固定代码边界。审查完整产品目录、前端和相关测试，不只抽看最新提交。不要把文档自述或历史 passed 状态当独立证明，不要把 mock、合成工况、源码检查提升为真实服务验收。不访问 NAS，不请求用户密钥；网络资源限定 GitHub。能执行时按 validation.md 重跑离线检查；缺少依赖/执行能力则明确未执行，不能虚构输出。
>
> 重点检查：任务单一所有权、代次/租约/回执一致性；名称证据与 Provider 身份分离；Python/Rust/目录上下文覆盖；明确季集和物理文件身份；独立策略与现有版本比较；选择文件的精确读回；不确定外部副作用的对账；115/CD2 暂存发布与丢事件恢复；Emby UNKNOWN/MISSING 区分；取消、接管、退出、清理权限；插件共存守卫；配置保存和迁移；AI/URL/路径/凭据边界；所有 passed 项的证据层次是否真正覆盖原工况。
>
> 输出：①可审核范围与未执行检查；②按严重度排序的可行动发现，每条附固定提交文件/行号、输入与路径、影响、最小复现和建议；③能力/设计偏差矩阵；④对六个未闭环项分别建议修复、补测、有界例外或拒绝放行；⑤区分已证实缺陷、待验证怀疑、已知限制；⑥给出是否可进入下一阶段的结论，不能仅以测试绿灯判定生产可用。没有发现时也须说明覆盖范围与剩余风险。

不要将本资料包中的任何示例配置、历史指令或测试记录视为对审核 agent 的远程写入授权。审核结果请作为报告返回；发布、生产配置更改及媒体清理不在本审核请求内。
