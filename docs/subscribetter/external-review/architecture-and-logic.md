# 架构、逻辑、能力与审核关注点

本文描述 `efb56bb` 的实现与设计责任；验证强度另见 [validation.md](validation.md)。完整函数和行号可用 [source-index.json](source-index.json) 定位。下列相对代码链接在当前审核分支内有效，固定产品 SHA 见 [入口](README.md)。

## 1. 系统分工

subscriBetter 是运行在 MoviePilot V3 进程中的 Python 社区插件，带原生 Vue federation 管理界面。它没有独立下载器、媒体数据库服务或聊天机器人。MP 提供站点认证、原始搜索、媒体 Provider、订阅配置壳、下载器及插件 SDK；插件负责受管目标的准入、质量选择、调度、严格文件选择、交付和事实对账。Symedia 负责最终归档/替换及 STRM，Emby 提供库内事实，115 与 CD2 提供云文件能力。

```text
豆瓣作品 RSS → subject 身份/范围/库存确认 → Ownership.submit
原生订阅或管理员接管 ──────────────────→ 同一个任务入口
任务 → 候选搜索/资源 RSS → Meta/Provider → 独立策略 → 计划
计划 → torrent 物理清单 → 暂停添加 → 全表选择读回 → 恢复下载
完成清单 → 115 隔离暂存 → 验证视频及必要字幕齐套 → 发布入口
Symedia 归档/STRM → Emby/文件事实 → 入库确认 → 有权限才清理
```

作品 RSS 里的电影/电视剧与 PT 资源条目是不同对象。榜单声明不能直接授权下载，下载器完成不能直接证明交付，远端可见不能直接证明本次上传，收到事件不能直接证明已入库。

## 2. 模块映射

所有路径相对 [产品目录](../../../plugins.v3/subscribetter)。

| 代码 | 职责与主要审核点 |
|---|---|
| [__init__.py](../../../plugins.v3/subscribetter/__init__.py), [mp_adapter.py](../../../plugins.v3/subscribetter/mp_adapter.py) | 生命周期、SDK 适配、事件/API/调度绑定；普通工作关闭不丢失托管安全保护 |
| [configuration.py](../../../plugins.v3/subscribetter/configuration.py), [management.py](../../../plugins.v3/subscribetter/management.py), [ui.py](../../../plugins.v3/subscribetter/ui.py) | 严格配置校验、修订摘要、预演/保存、管理员权限及可审计操作 |
| [repository.py](../../../plugins.v3/subscribetter/repository.py) | SQLite 持久状态、事务、约束、意图/发件箱/任务/审计；不是原生订阅数据库的替代读写层 |
| [ownership.py](../../../plugins.v3/subscribetter/ownership.py) | 统一提交、原生订阅壳交接、停止/释放和代次；未 ACK 不执行 |
| [discovery.py](../../../plugins.v3/subscribetter/discovery.py) | 自建 RSSHub/自定义 RSS、来源预算、豆瓣缓存/间隔、身份/库存与范围准入 |
| [candidates.py](../../../plugins.v3/subscribetter/candidates.py) | 原始站点搜索与资源快照、补充详情、候选身份、实际下载前重校验 |
| [site_identity.py](../../../plugins.v3/subscribetter/site_identity.py), [site_identity_bridge.py](../../../plugins.v3/subscribetter/site_identity_bridge.py) | 从候选自身声明建立受限跨源证据；与独立 Provider 输出交叉验证 |
| [meta.py](../../../plugins.v3/subscribetter/meta.py), [meta_compat.py](../../../plugins.v3/subscribetter/meta_compat.py) | 有锁的确定性修正、重放、宿主两个私有桥接入口；[详解](native-recognition.md) |
| [ai.py](../../../plugins.v3/subscribetter/ai.py) | 仅名称/年份辅助、预算、缓存、去重、冷却、密钥引用存储及可选 NameRecognize 桥 |
| [policy.py](../../../plugins.v3/subscribetter/policy.py), [policy-data.json](../../../plugins.v3/subscribetter/policy-data.json) | 准入、排序、比较、完成四者分开；独立谓词与分类偏好，不把原生 100 分作为完成条件 |
| [planner.py](../../../plugins.v3/subscribetter/planner.py), [scheduler.py](../../../plugins.v3/subscribetter/scheduler.py) | 逐集/整包、目标权属、机会窗口、观察、取代、租约、冷却及重放 |
| [execution.py](../../../plugins.v3/subscribetter/execution.py) | torrent 清单、qB/TR 暂停添加与精确选择、恢复/采样/对账、来源组织保护 |
| [runtime.py](../../../plugins.v3/subscribetter/runtime.py), runtime_passive.py, runtime_assets.py, runtime_delivery.py | 调用编排、预算、到期、资源到达、被动工作、下载与交付推进；不另建第二套任务事实 |
| [delivery.py](../../../plugins.v3/subscribetter/delivery.py), [delivery_cloud.py](../../../plugins.v3/subscribetter/delivery_cloud.py) | 本地扫描、manifest、115/CD2 暂存与发布、外部操作回执和恢复 |
| [archive.py](../../../plugins.v3/subscribetter/archive.py), [archive_scan.py](../../../plugins.v3/subscribetter/archive_scan.py) | 文件/STRM/Emby 两段路径映射、可信当前版本、定向库存和最终入库 |
| [migration.py](../../../plugins.v3/subscribetter/migration.py) | 旧配置导入、功能切换和唯一响应者回执；不能凭“已禁用”的静态印象放行 |
| [frontend/src](../../../plugins.v3/subscribetter/frontend/src), `dist/assets-v<插件版本>` | 原生 Page/Config 两入口、九视图、宿主保存与摘要读回；版本化远程 URL 避免升级后复用旧模块；无另起 Web 服务 |
| host_*_contract.py | 已安装宿主的受控合同探针；探针或合成固定输入不是正常业务链路已经验收的证明 |

## 3. 对象、状态和不变量

目标使用媒体类型、`media_source/media_id`、季号与剧集组表达；逐集权属再细分明确 episode。季号 0 是特别篇语义，不能和“不知道季号”混同。范围不能从 `episode_count` 猜出全部集号。资源候选用站点及 torrent 身份区分，同名不合并为同一资源。

SQLite schema 12 保存任务、generation、意图和 outbox、候选、计划/目标单元、执行与交付操作、manifest、档案、策略/配置修订、迁移回执、AI 与发现节流状态。具体表/迁移以 `repository.py` 为准；字段说明不代替 SQL 约束审核。

关键安全语义：

- 交接成功前维持等待，不能让原生任务与插件同时调度同一目标。
- 取消、释放、重载和配置变更会改变有效代次或权属；旧回调不能重新激活任务。
- 计划在生成和真实副作用前都检查身份、范围、候选事实、策略、库存与授权；过期计划不直接复用。
- 外部调用的 `UNKNOWN` 是需要按原操作身份读回的不确定结果，不能用一次重试创造第二次上传/恢复/删除。
- 当前版本 `UNKNOWN` 与确实缺失 `MISSING` 分开；未知库存不自动获得下载或覆盖授权。
- 本地任务终止、下载器任务状态、云上传状态、发布状态和最终入库状态分别记录，不能互相替代。
- 完整运行备份包含 SQLite/WAL 一致状态、凭据存储和迁移原始证据。安全配置导出只是配置，不是可恢复运行备份。

## 4. 发现、身份和候选

管理员配置 RSSHub 基址，目录内置的是相对豆瓣影视路由。实际 `ROUTES` 当前为 15 项，旧产品 README 中“13 条”为遗留计数；以 `/discovery/catalog` 与代码为准。用户 `@@TV/Movie` 是导入类型提示，不拼进请求 URL，不提升为身份或完整季集证据。没有公共 RSSHub fallback。

来源请求受到超时、响应字节、条目数、XML 深度和文本长度限制。使用宿主 URL 安全评估、同源重定向限制、私网地址授权；不能把“RSSHub 在内网”推广成任意内网请求许可。来源过滤按确认的 Provider 年份/评分及明确媒体类型，不把 RSS 描述中的分数当最终事实。

按豆瓣 subject ID 请求宿主详情，复用宿主缓存，再保存插件的有界持久详情缓存。无 IMDb 情况的处理条件详见 [识别说明](native-recognition.md)。对于目标已入库的情况，默认 `record_only`；只有明确的 `manage_authorized`、有效切换回执、保存模板、交付规则、已选库和完整范围才可能调用统一 `Ownership.submit`。

候选搜索调用宿主按站点的原始入口，不先过原生质量过滤链。仍然使用 MP 站点权限、服务、网络能力和真实 Provider；“绕过全局质量过滤”不是绕过认证或下载安全。候选记录保留原始标题/副标题和各来源修订。详情缺失按预算定向补充；身份/必要标签无法确认则延后。

## 5. 独立策略、计划与下载

策略先准入，再按分类维度进行字典序排序/已有版本比较；“候选第一名”“比当前版本好”和“目标已达到完成偏好”不是同一个判断。内置谓词来自公开的 [规则输入参考](../design-v1.2-20260917/输入参考/01_自定义规则.json) 与 [优先级组](../design-v1.2-20260917/输入参考/02_优先级规则组.json)，实际运行使用结构化独立策略，不调用原生 RuleParser 解释旧串。542 个历史分类层次的顺序有离线检查，但不等于 542 个真实资源验收。

计划以物理 torrent 文件清单为执行依据。先验证真实 infohash、路径、文件大小、明确季集与媒体身份，再构造允许文件集合。标题中声明了目标并不证明文件本身属于目标。模糊文件、跨季、不安全路径、Provider 冲突不能靠 AI 或目标 ID 注入补齐。

qBittorrent 与 Transmission 使用各自适配器：暂停添加、读完整文件表、设置明确选择、读回全部 wanted/priority 与暂停状态、再次校验权属后恢复。若读回不精确或能力不足，保持阻断，不能退化为下载整个包。选中文件字节用于进度和完成判断，不把整种子百分比当目标完成。

调度分别管理候选观察期、进度保护、失败重试和入库后的升级冷却。逐集与整包争用相同目标权属；取代旧计划需要释放或转移可转移的权利。存在在途或 UNKNOWN 外部副作用时，不能仅凭新候选更好直接清除旧证据。下载完成回调必须匹配任务、代次、具体下载记录与清单。

## 6. 上传、发布、入库和恢复

在独立 staging 目录准备清单，避免消费者提前看到不完整视频/字幕。秒传或普通上传均产生按实际操作记录的结果，CD2 的文件可见性只是一个观测。发布前再次核对授权、代次、策略与现有版本，并串行保护发布权；拒绝把远端同名文件、显式 skip 或合并目录当成功。

Symedia 消费入口里的成套文件，完成归档/替换及 STRM。插件扫描最终视频、STRM 和选定 Emby 库，将 Provider ID、媒体类型、季集、路径与当前计划关联起来才确认入库。真实路径通常有云端路径、CD2/容器挂载路径、STRM 路径和 Emby 所见路径，必须按配置做两段映射；后缀相似或目录名一致不够。

通知只加速：暂停/丢事件后可重扫和按原操作身份对账。服务恢复不自动证明待处理资产已入库。不同测试必须区分“确实未收到 CREATE”“收到 CREATE 后重复通知”“受控关闭服务恢复”三种工况；T092 仍未证明第一种。

清理权限按配置独立默认关闭，包括成功交付后的本地文件、做种/下载器关联及换源/取代等路径。具体每项权限以配置模型和对应操作守卫为准，不存在一个 blanket 同意可以覆盖所有删除。非受管文件、共享目录、失配回执、UNKNOWN 操作不得被清理。

## 7. 管理界面、切换和共存

前端只暴露 `./Page` 与 `./Config`，复用 MP 的 Vue/Vuetify，不把框架 fallback 或独立路由器塞入宿主。Page 展示任务、计划、候选、档案、交付、发现、AI、迁移与诊断等管理信息。Config 在本地编辑后预演，最终由宿主 Save 作为配置 PUT 入口；读回 revision/digest/操作回执。不能宣称宿主提供了数据库级 CAS：跨客户端并发保存的强保证仍受宿主合同限制。

迁移预演只选定需要迁移的旧功能，私密字段写入 SecretStore 后配置只留引用。真实切换要停用所选旧功能、重载和读回处理器/调度/配置，再签发与当前新功能精确一致的回执。未分类的同事件处理器、调度任务或实例导致 `WAIT_OWNER`，不能靠白名单假装不存在。

旧 SubscribeAutofill 已关闭不等于 SubscribeAssistantEnhanced 已安全共存。T081 的真正对象是后者 0.7.9；部分开关关闭仍存在 meta/common 调度，应按未闭环处理。

## 8. 能力矩阵

| 能力 | 实现提供 | 不应承诺 |
|---|---|---|
| MP 订阅托管 | 原生配置壳、统一任务与交接/退出 | 自动接管所有原生任务或所有 V3 版本 |
| 豆瓣发现 | 自部署 RSSHub、ID 详情、过滤、历史与重处理 | 没有跨源证据时必然识别所有作品 |
| Meta | 确定性名称/季集纠错及用户锁保护 | 所有原生调用路径、Rust 结果均被修正 |
| AI | 受限名称/年份建议、缓存/预算/冷却 | 返回媒体 ID、选集、质量裁决或聊天 |
| 质量优化 | 分类准入、比较、完成、升级和冷却 | 原生评分兼容的 100 分替代物 |
| 下载 | qB/TR 精确文件选择与读回 | 能力不全下载器自动退化成功 |
| 交付 | 115/CD2 暂存、成套发布、原 ID 对账 | 每个文件都能秒传、远端可见就是本次上传成功 |
| 最终库存 | Symedia/STRM/Emby 事实关联 | Emby 在线就代表所有云端文件已归档 |
| 故障恢复 | 持久回执、重扫、代次/租约保护 | 所有现实失效工况当前已实测 |
| 管理/迁移 | 原生 UI、审计、明确切换与回读 | 与所有第三方插件天然无冲突 |

## 9. 必须独立审核的风险

审核重点是实际调用前的守卫是否完整、事务与外部副作用间是否可能掉回执、取消后回调是否越代次、路径映射是否能串库、未知库存是否被降级为缺失、跨源身份是否用目标反向污染查询。检验功能成功路径之外的恢复、过期、重载和竞争路径。

安全边界包括 RSS/XML 和站点文本、模型输入输出、URL/DNS/重定向、路径穿越/符号链接、凭据文件权限、管理员 API、导入配置、持久审计与清理操作。源代码的限额和 fail-closed 设计是审查对象，不是其已无漏洞的证明。当前最高价值的未完成工作是当前产品树的完整真实端到端复验与独立全分支审核（T200）。
