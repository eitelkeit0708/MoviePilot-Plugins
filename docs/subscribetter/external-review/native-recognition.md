# MP 原生识别：改动位置、数据流与覆盖限制

本文针对产品 `efb56bb` 与 MP `e195cc1`（v3.0.4）。不根据“V3”名称推断未来版本兼容性。[源码符号索引](source-index.json) 收录插件函数及固定提交链接。

## 1. 先区分三个层次

1. **Meta 解析**：从原始标题、副标题、文件名及目录提取名称、年份、类型、季集和技术规格。
2. **媒体身份识别**：使用 MP Provider 返回真实 `media_source/media_id` 与详情，将名称关联到作品。Meta 看起来正确不等于 ID 已确认。
3. **下载准入与文件身份**：候选和 torrent 物理文件必须对应受管目标与明确范围，再由独立策略决定是否下载。AI 名称和 RSS 排名没有执行权。

插件主要在第一层做确定性修正，在第二层调用宿主已有能力并约束证据，在第三层做自己的受管流程。没有把整个 MP 识别器替换为模型，也没有全局改写 `recognize_media`。

## 2. 宿主原始路径（GitHub 可核对）

| 宿主位置 | 原有行为 |
|---|---|
| [metainfo.py:284](https://github.com/jxxghp/MoviePilot/blob/e195cc164fc8ff869ffee0ea44a49c7ec475310c/app/domain/metainfo.py#L284) | `_prepare_meta_input` 应用用户识别词、副标题处理、显式标签和文件后缀 |
| [metainfo.py:339](https://github.com/jxxghp/MoviePilot/blob/e195cc164fc8ff869ffee0ea44a49c7ec475310c/app/domain/metainfo.py#L339) | `_build_meta_info` 选择 MetaAnime/MetaVideo，应用显式字段；`_build_python_meta_info` 完成 original_name |
| [metainfo.py:428](https://github.com/jxxghp/MoviePilot/blob/e195cc164fc8ff869ffee0ea44a49c7ec475310c/app/domain/metainfo.py#L428) | `_meta_from_rust` 把加速器字典转换成兼容的 Python Meta 对象 |
| [metainfo.py:504](https://github.com/jxxghp/MoviePilot/blob/e195cc164fc8ff869ffee0ea44a49c7ec475310c/app/domain/metainfo.py#L504) | `MetaInfo` 区分音乐/影视，优先可用 Rust，否则 Python |
| [metainfo.py:552](https://github.com/jxxghp/MoviePilot/blob/e195cc164fc8ff869ffee0ea44a49c7ec475310c/app/domain/metainfo.py#L552) | `MetaInfoPath` 处理文件及父目录；Python 路径在各次解析之后继续 merge |
| [media/recognition.py:201](https://github.com/jxxghp/MoviePilot/blob/e195cc164fc8ff869ffee0ea44a49c7ec475310c/app/chain/media/recognition.py#L201) | `recognize_by_meta` 进入原生/插件识别选择，受宿主识别顺序控制 |
| [media/plugin.py](https://github.com/jxxghp/MoviePilot/blob/e195cc164fc8ff869ffee0ea44a49c7ec475310c/app/chain/media/plugin.py) | 名称帮助事件与辅助结果消费；事件不是完整原始 Meta 上下文 |

审核原生后续流程时应检查目录 merge 和同名/同年快速路径，不能因为某个中间 Meta 已修正就断言最终 Provider 请求一定采用修正值。

## 3. 两个全局补丁入口

实现集中在 [meta_compat.py](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/efb56bb3cdae83aa81ade49e9c4d62d618827ec6/plugins.v3/subscribetter/meta_compat.py#L32)。默认须由配置显式启用；它可能影响未受管的宿主解析调用，因此与插件内部解析分开说明。

| 补丁函数 | 包装顺序 | 限制 |
|---|---|---|
| `_build_python_meta_info(title, subtitle=None, custom_words=None)` | 先调用原生函数，再 `MetaCorrector.correct` | 原生路径随后仍可能 merge 父目录；这不是一个“最终全路径结果”的 hook |
| `_meta_from_rust(parsed)` | 先原生转换，再以 `context_known=False` 调用 corrector | Rust 输入不能证明完整用户锁；只要修正会改变字段，就返回原对象并记 `RUST_LOCK_CONTEXT_UNKNOWN` |

安装检查精确版本 3.0.4、函数模块/名称、参数顺序与种类/默认值、返回类型 MetaVideo/MetaAnime，以及是否已有包装者。重复安装检查自身所有权；冲突、签名不符或结果类型不符不会强行覆盖。卸载仅还原仍由本实例占有的包装函数，避免破坏别人后装的处理器。

补丁内只做有界本地解析与保护，不进行 HTTP、LLM 或 SQLite 热路径查询。异常保留原生结果。线程局部 `bypass` 让插件内部先拿到未经全局修正的原生结果，再带完整锁上下文执行一次修正。

**不覆盖**直接构造 `MetaVideo` 的调用、音乐解析、已经缓存的旧对象、未经过这两个入口的代码。**不承诺**Rust 全局纠错、任意未来 MP 版本、宿主所有目录合并后的正确性。应把诊断的 `coverage` 与真实调用路径一起评审，不能只看到 `active=true`。

## 4. 受管流程的 MetaService

[MetaCorrector.correct](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/efb56bb3cdae83aa81ade49e9c4d62d618827ec6/plugins.v3/subscribetter/meta.py#L78) 深拷贝原生对象并保留 before/after/diff。修正范围包括数字片名、受保护的括号名称、明确的中英文季集、范围和形态；技术规格不是名称改写的附带牺牲品。

优先级是用户显式意图和锁 > 有证据的确定性解析 > 有界名称辅助。`apply_words` 表明已应用用户识别词时保留其意图；显式标签与调用方锁限制对应字段。互相冲突的季集、不连续且无法准确表达的多集、无证据裸数字等返回 DEFER；不能取最大数字当集数。

[MetaService.parse](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/efb56bb3cdae83aa81ade49e9c4d62d618827ec6/plugins.v3/subscribetter/meta.py#L297) 在全局补丁 bypass 下调用原生解析，再做带锁修正及审计记录。`parse_path` 在原生 `MetaInfoPath` 完成后调用 `correct_path`：文件名优先，最近明确季目录辅助；必要时借父目录解释字幕名称。它是内部受管流程能力，**没有增加第三个全局热补丁点**。

解析记录带 parser revision、原文的安全存档、差异和原因。重放作用于指定记录；不能因重解析而重新开启 STOPPED/RELEASED 任务、重发上传或解开原生锁。

## 5. GATE24 示例应怎样判断

输入主标题：

```text
GATE24 The Border S01 2026 1080p NF WEB-DL H.264 AAC2.0-HHWEB
```

副标题包含 `大机场～GATE24 ～ | 第09集 | 1080p | 类型: 电视剧`。

受管确定性修正利用前缀和明确技术字段边界保护完整 `GATE24 The Border` 名称；`24` 是片名的一部分，`S01` 是季证据，副标题的 `第09集` 才是本次集号证据。缺少第09集证据时，不能凭片名创造第24集；也不能抹掉一个有独立证据的别的集号。用户锁、识别词和未知上下文仍优先。

见 [test_meta_numeric_prefix.py](../../../tests/v3/subscribetter/test_meta_numeric_prefix.py)：名称截断、技术尾部、单独年份、错误第24集、无集证据、其他已有集号、锁以及长连字符输入均有断言。该测试注入原生对象，是纠错器的**离线回归**；不证明某一已安装 Rust 构建对所有真实资源都产生同样初始错误，也不证明该作品已完成下载入库。

## 6. Provider 身份与豆瓣无 IMDb 情况

[HostCandidateAdapter.recognize](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/efb56bb3cdae83aa81ade49e9c4d62d618827ec6/plugins.v3/subscribetter/candidates.py#L157) 调用 MP `MediaChain().run_module('recognize_media', ...)`。候选自己的成对来源 ID 可以作为声明输入；不存在候选声明时可限定目标 Provider，**不把目标的 ID 填进去制造命中**。返回结果再用宿主身份 API 和实际 payload 检查来源 ID、类型及跨源冲突。

豆瓣榜单若有 `/subject/<id>/` 链接，`DiscoveryService._recognize_douban` 使用该 ID 请求宿主详情，不依赖重新搜索榜单显示名。所以《内格力》与豆瓣《侠女内莉》这类别名问题，应先用同一个 subject 的详情/别名解决查询词，不能把搜索失败当成不存在作品。

详情存在与跨源映射是两回事。对于《一瓯春》这类没有 IMDb 的条目，不应宣称“补一次名称搜索就完全解决”：

- **TV 名称/年份候选桥**：使用确认的豆瓣标题、原名及别名（最多 8 个），要求 TMDb 返回真实 ID、类型/年份/名称一致，并且返回结果带可验证的同一豆瓣 ID。只有名称年份相同而没有链接身份证据，仍为 `CROSS_SOURCE_ID_REQUIRED`。
- **电影站点桥**：从选定站点的候选详情读取该候选自身豆瓣/IMDb 声明，再独立调用 Provider；Provider 的 IMDb 必须与声明一致，豆瓣、TMDb、类型、已知年份不能冲突。不能把站点声明无条件提升为事实。
- 多重映射、冲突、缺证据、预算不足均返回 UNKNOWN/CONFLICT/DEFER。别名只帮助找候选，不直接授权身份。

实现与规则版本见 [site_identity_bridge.py](https://github.com/eitelkeit0708/MoviePilot-Plugins/blob/efb56bb3cdae83aa81ade49e9c4d62d618827ec6/plugins.v3/subscribetter/site_identity_bridge.py)。未来若增加可信的映射来源，需另行设计和验收；当前不伪造 IMDb，也不拿合成 TV 桥当真实无 IMDb 作品已打通。

## 7. MP 如何取豆瓣详情，插件如何限流

在固定 MP 源码中，[DoubanModule 的详情路径](https://github.com/jxxghp/MoviePilot/blob/e195cc164fc8ff869ffee0ea44a49c7ec475310c/app/modules/douban/__init__.py#L1116) 调用 DoubanApi。[apiv2.py:155](https://github.com/jxxghp/MoviePilot/blob/e195cc164fc8ff869ffee0ea44a49c7ec475310c/app/modules/douban/apiv2.py#L155) 指向豆瓣 Frodo/API 域名；[GET 包装:232](https://github.com/jxxghp/MoviePilot/blob/e195cc164fc8ff869ffee0ea44a49c7ec475310c/app/modules/douban/apiv2.py#L232) 使用宿主 `cached`，未命中时发真实请求。不能描述为“只请求 MP 官方中央详情缓存”。不一定每次查询都联网，也不能因为日常手动搜索没遇限流就推断批量榜单没有风险。

插件 [discovery.py](../../../plugins.v3/subscribetter/discovery.py) 另加：

| 项目 | 当前实现 |
|---|---|
| 显式间隔 | `douban_interval_seconds` 默认 5 秒，允许 1–60 秒；持久 gate 记录下一次启动时间，调用结束后仍留间隔 |
| 并发 | 进程内共用豆瓣 lock；不是跨任意 MP 容器的分布式限流器 |
| 正缓存 | 按 subject/上下文保存序列化结果，24 小时；读取后重校身份及类型 |
| 失败缓存 | `None` 单条延后约 60 秒；未知原因不当作全豆瓣封禁信号 |
| 存储上限 | 单记录 256 KiB、最多 512 条并清理到期记录 |
| 中止 | 等待中检查当前代次；缓存命中也不能绕过后续库存/策略/所有权准入 |

本间隔作用于插件这条详情调用链；不保证一次宿主 Provider 调用内部只有一次 HTTP，也不限制所有其他插件/MP 原生功能。宿主自己的 HTTP 退避仍属于宿主职责。用户要求的“直接加流控”和“长期在榜缓存”均在此处实现；其有效性应由 `test_discovery_cache.py` 和真实计时证据分开判断。

## 8. AI 辅助位置与 NameRecognize 桥

内部候选先做完整 Meta；仅名称缺失或可解释的名称冲突才进入 [AIService.assist](../../../plugins.v3/subscribetter/ai.py)。模型 JSON 输出仅 `name`、`year` 两个字符串，后续仍须实际 Provider ID 和范围校验。季集、类型、技术规格、下载/策略决定不交给模型。独立聊天和消息路由已移除。

AI 有请求合并、正/负缓存、重试与并发预算、401 有界密钥轮换、429 冷却、响应字节限制和配置代次保护。模型收到的输入及日志脱敏需独立审查；“不把凭据存普通配置”不等于网络/日志层可以不审。

可选 NameRecognize 桥默认关闭。事件只提供受限输入，同步处理读取缓存并可投递有界后台队列，首调用可能拿不到模型结果；确定性本地修正可单独产生证据。它不能替换宿主整套 Meta 对象，也不能保证改变原生已经命中的同名/年份快速路径。插件不自动开启宿主“优先使用插件识别”。内部受管 Provider 调用不依赖这个事件桥。

## 9. 建议审核用例

| 维度 | 公开离线入口 |
|---|---|
| 数字/括号/类型/明确范围/用户锁 | `test_meta.py`, `test_meta_numeric_prefix.py`, `test_season_locks.py` |
| 两桥版本门/冲突/撤销/未知上下文 | `test_meta.py` |
| 物理文件与目标不一致 | `test_physical_identity.py`, `test_candidates.py` |
| 豆瓣缓存/间隔/重启 | `test_discovery_cache.py`, `test_discovery.py` |
| 来源桥冲突/无关联 ID | `test_site_identity.py`, `test_site_identity_bridge.py` |
| AI 不越权/回执/错误 | `test_ai.py`, `test_native_ui.py` |

离线复现命令与依赖见 [validation.md](validation.md)。代码可审核性、离线测试通过、已安装宿主行为、真实资源识别成功和完整交付成功是五个不同结论，请分别报告。
