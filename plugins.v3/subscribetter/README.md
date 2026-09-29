# subscriBetter V3

这是独立社区插件，不是 MoviePilot 官方功能。仅面向 MoviePilot V3；固定适配基线 v3.0.4 / e195cc164fc8ff869ffee0ea44a49c7ec475310c。管理界面直接嵌入宿主 Vue/Vuetify 对话框，没有额外服务、路由器或浏览器凭据存储。

## 安装与初始配置

1. 使用宿主原生插件安装机制安装完整插件目录（包含 dist）；首次默认关闭、dry-run。市场 release 标志和真实环境验收属于独立发布门禁，本目录的构建成功不表示已发布。
2. 在宿主设置 `/setting` 配置下载器、Emby、CloudDrive 与 115 服务。本插件只引用其名称/实例，不重复收集服务 token。
3. 打开配置页，刷新当前有效配置。配置已发布分类 ID/版本、独立偏好与目录模板，选择授权站点和被动媒体库。不要把旧 episode_priority=100 当作质量。
4. 设置两段映射：Emby 路径→MP 可读 STRM；STRM 目标→CD2 内部路径。绑定真实服务/库/云盘作用域。监控、隔离暂存、交付及消费者目录不能冲突。
5. 明确观察/冷却/抢占预算、稳定窗口、秒传 MISS 次数/间隔、CD2 回退大小，以及扫描/恢复预算。空值、零和无限制有不同语义，数字输入保留数值类型。五项清理权限默认分别关闭，同时受规则同名权限约束。
6. 可选榜单使用配置的本地 RSSHub 与服务器目录；没有公共替代服务。配置来源、类型提示、评分来源、年份、多季策略与历史视图。抓取测试不会识别、提交任务或调用模型；显式运行是另一个动作。
7. AI 端点/密钥通过管理页私密写入获得引用，模型、profile、提示词及备份、并发/缓存预算进入普通安全配置。名称桥默认关闭，需唯一处理者切换。开启公共 Meta 包装会影响经过公共入口的未受管解析；不扩展到其他宿主函数。
8. 预检并提交宿主 Save：预检一次，向父对话框发送精确完整安全配置一次。宿主执行唯一 PUT 并可能关闭对话框。HTTP 200/绿色提示不能证明插件接受了配置。重新打开 Page/Config 或显式刷新，核对当前版本/摘要、回执和 INVALID_OR_STALE_CONFIG 等诊断；没有关联证据时只显示“当前有效配置”。
9. 保持关闭/dry-run 完成试验范围和服务验证后，再由管理员明确启用。关闭普通工作不会移除已有纳管安全保护；恢复原生控制使用任务解除纳管预览。

## 九个管理视图

任务页保留逐目标当前质量、生命周期、机会/观察起止、最后改善、冷却、预算、计划与测量进度；候选页保留原始文件、准入/排序/排除、模拟与执行证据；档案页独立展示版本、文件附件、位置、Hash 与两段映射；交付页逐组/文件展示秒传/CD2、原始操作、代次/取代、UNKNOWN、发布、清理与 Emby 确认。

策略页查看绑定、覆盖、锁和真实样例预演；服务页查看依赖、扫描水位、失败路径、运行游标、消费者恢复和当前配置备份；发现页分离识别、意图 ACK、下载接受、交付、全部所需目标入库等统计；解析/AI 页提供确定性差异与重放、兼容性、预算/脱敏用量、缓存维护和提示词恢复；迁移页展示逐字段/历史映射与每个切换步骤。

列表使用服务器分页，展开明细读取有界数据。配置引用可以含 Unicode、空格与斜杠，导航按实际值编码。只读打开/刷新不会扫描、搜索、调用模型。动作先核对精确范围；破坏性/范围修改使用服务器不可变预览，再只提交回执、摘要、操作 ID 与确认。503 是不可用，不是健康空列表。过期/陈旧预览必须重新审阅，不自动扩大范围或重发。

取消交付保留下载器任务/数据和监控副本。清理监控、未发布暂存、下载器任务、下载器源数据分别申请独立权限。UNKNOWN 保留原外部 ID 与发布屏障；回执查询不把超时变成成功。支持 SSA/VTT/SUP/ZIP 和路径安全；取消的范围仅 IDX/SUB、独立字体及许可证附件。

## 迁移、切换与回退

仅读取用户明确选择的离线导出或明确授权的旧榜单公开只读接口。离线文件最多 2 MiB；原始 bytes 存于私密快照，页面显示安全引用、逐字段映射、历史原值/理由及游标。导入每次最多 100 条，幂等继续；不重放 clear/onlyonce/restore，不恢复旧内存缓存/会话，不把旧已添加订阅当实际成功。导入后通过正常配置 Save 重载。

SOURCE/IMPORT 只说明来源读取/导入，CUTOVER ACTIVE 才说明**当前宿主**的所选新模块与响应路由已确认唯一。空旧停止回执不是外部 V2 已停止。切换页先预检所选新功能的完整配置并生成绑定回执，审阅精确旧功能，再逐步停止旧功能、实际读取核对、达到 READY_CONFIG。复制切换回执 ID，到配置页加载该不可变配置，通过宿主 Save 提交，再返回原切换回执核对/激活。

旧实例修改只使用短暂的原生完整 GET/PUT；无损保留数值 token/巨大整数和无关字段，按 Python 完整摘要验证前后状态，只修改回执中已选布尔值。完整摘要由 @noble/hashes 1.8.0 计算，HTTP 无需 Web Crypto subtle。需要浏览器支持 JSON.parse source context 与 JSON.rawJSON；不支持时明确拒绝。完整旧配置、密码和导入草稿不显示、不记录、不导出、不存储，使用后及关闭时清空。宿主 PUT 没有 CAS：并发管理员修改仍可能冲突，不能承诺原子保全；读回不符不标记成功。

部分旧 AI 实例没有公开运行 recognize 开关证明。即使保存 false，仍可能 WAIT_OWNER / LEGACY_RUNTIME_FLAGS_UNVERIFIED；没有 force/ack 绕过。回退先隔离新入口，再在配置页加载回退变更并 Save，核对新入口停止，最后只恢复原回执选择的旧开关并验证注册。数据库状态回退不能恢复已被外部覆盖/删除的文件。

提示词恢复也是独立预览/操作，只生成安全配置预览。复制操作 ID 到配置页“从提示词操作 / 切换回执继续”加载并 Save；不在 Page 偷发第二条配置 PUT。

## 备份与恢复

服务页可导出当前安全配置（含私密引用，不含密钥）。它不是完整运行备份。完整备份应在宿主停止插件、已派发调用收尾后，以宿主管理员工具一致备份插件数据目录中的 SQLite（含所需 WAL 状态）、私密凭据文件和迁移原始快照；保护 owner/权限，不上传公开问题单。私密存储仅支持 POSIX 0600/当前 owner/单链接/拒绝 symlink。恢复必须保留任务、代次、原始外部操作 ID、UNKNOWN、归属、观察/入库锚点、历史和回执；先保持关闭/dry-run，检查版本、服务与扫描事实，再进行原 ID 对账。不要清库、强解锁、重复上传或把重新通知消费者当已完成。

## 可复现构建与本地检查

构建环境：Node 24.16 / npm 11.17。`frontend/package-lock.json` 固定全部依赖；精确直接版本见 `frontend/package.json`。宿主提供 Vue 3.5.13 / Vuetify 3.7.3；generate:false，不打包框架 fallback，也不导入 Vuetify 基础 CSS。

```sh
cd plugins.v3/subscribetter/frontend
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run build
```

输出 `dist/assets-v<插件版本>/remoteEntry.js` 暴露且只暴露 ./Page 和 ./Config。插件根目录 `vite.config.mjs` 是标准构建入口，frontend/build.mjs 复用该目录依赖。宿主 local:// 原生安装复制完整 dist，无需开发服务器。版本化资源目录会使插件升级后的远程模块 URL 同步变化，避免继续复用旧界面。

仓库根目录（使用本仓库测试 Python）可运行：

```sh
python -B -X utf8 plugins.v3/subscribetter/frontend/tools/export_contract.py
python -B -X utf8 -m unittest discover -s tests/v3/subscribetter -p test_native_ui.py
```

表单合同从实际挂载 FastAPI/Pydantic 导出，行为测试编译并挂载真实 Vue 组件，使用无网络渲染器/注入 API 检查九视图、数值、Save、错误、分页、回执和关闭。测试不代表实际 NAS、浏览器布局/键盘、Linux 文件权限、真实模型、旧生产实例切换、完整云盘/Emby 链路或 T171 验收。官方 CSS 检查应使用固定原始检查器；当前市场 release=true 留待发布授权，不能为了绿灯修改索引或检查器。

## 许可与来源

本插件遵循仓库 GPL-3.0，完整文本随 `LICENSE` 提供。源代码、测试、前端源码、构建锁文件和资产均随目录提供。名称辅助/迁移算法参考 eitelkeit0708 ChatGPTPlusUltra 1.4.2 与 DoubanRankPlusOptimized 1.0.7，固定历史源码 cbd770e364ec9a96a81fbfe9ac8d33abdb2bb1ba；V3 改动加入独立状态、权限、证据、幂等回执、预算与生命周期隔离，不把旧插件测试当新实现验收。

宿主合同来自上述固定 MoviePilot 提交；官方 federation/CSS 检查来源 jxxghp/MoviePilot-Plugins a0433b09f3b6c110dbf74af46be3758bea5027f8。云盘/STRM 适配按现有源码中的固定 DDS 来源归属，不引入遥测或第三方媒体内容上报。

Vue、Vuetify、Vite、plugin-vue、@noble/hashes 1.8.0 为 MIT；@originjs/vite-plugin-federation 为 MulanPSL-2.0。直接依赖的完整许可证与版本清单在 frontend/licenses。Node_modules 不随插件分发。以下保留底层 AI/发现合同说明；实现与真实环境验收状态以当前回执和验证报告为准。

---

# subscriBetter V3：统一订阅、作品发现与名称辅助

## 作品发现

`Config.discovery` 使用结构化 `DiscoveryConfig`。配置自建 RSSHub `rsshub_base_url` 后，可从 `/discovery/catalog` 查看 13 条固定豆瓣影视相对路由，并从 `/discovery/sources` 查看实际来源配置与预算；没有公共 RSSHub fallback。自定义 RSS 每行迁移为一个 `kind=custom` source。管理员可用 `/discovery/test` 仅取源/解析，`/discovery/run` 显式运行，`/discovery/records` 查看 `all/latest12/recognized/unrecognized` 历史及分阶段统计，另有 POST reprocess 和 history/cleanup。作品发现与 PT 下载资源是两个不同的数据域。

来源请求使用严格同源重定向、identity encoding、总时限、响应字节/项目/文本/XML 深度限制，以及固定 SDK `SecurityUtils.evaluate_url_safety`。字面私网 IP 只授权该地址；私网域名需要配置 `allowed_private_ranges`。RSS 标题、描述、排名、年份和分数只是原始声明；身份、年份、TMDB 分数及明确季号来自实际识别/provider 返回。多季只使用明确、已播、正数 season_number，不从 season 数量构造范围。

每个来源在取源前都必须通过 W10 的 `migration.unique_owner(...)` 和 `migration.owner_snapshot(...)` 精确回执。新电影在原生壳 ACK 后直接用共享 `Scheduler.open_opportunity` 建立连续机会。电视剧还要求 W10 绑定 `migration.discovery_scope(request) -> {'episodes': [明确正整数集号...]}`；未绑定或范围不完整时保持 DEFERRED，绝不由 episode_count 造集号。全新目标档案为 UNKNOWN 时可绑定 `migration.inventory_refresh(request)` 做选定媒体库内的有界定向刷新，返回带 `evidence_ref` 的 `UNKNOWN/MISSING/PRESENT/PARTIAL/INGESTED`。已有档案由 `Archive.discovery_inventory(Target)` 直接投影；部分季在默认 `record_only` 下只链接事实，不接管下载。

自动提交还要求来源有对应电影/剧集保存目录、至少一个启用交付规则，以及同 provider 的已选 Emby service/library mapping。所有成功目标仍只调用共享 `Ownership.submit`，使用同一 `tasks/intents/outbox`、STOPPED/RELEASED 和排除边界；没有第二个下载队列。显示历史清理只隐藏记录，保留目标、任务和回执。

AI 默认关闭。内部候选流程先执行完整 Meta 修正，只有名称缺失或可解释的名称冲突才调用名称辅助；随后仍用真实媒体源返回的 ID 验证身份。模型只能提供两个字符串 `name`、`year`，不能提供季集、类型、媒体 ID、下载选择或订阅执行权。确定性解析已明确时不调用模型，范围冲突和用户规则也不会被模型覆盖。

可选 NameRecognize 桥默认关闭。同步事件只读取缓存并投递有界工作队列，第一次调用可能不提供结果；后续调用才可能获得缓存。该桥受宿主事件只提供标题、不能替换完整 Meta 的限制，不能保证修复宿主的同名同年份短路。插件不会自动修改宿主“优先使用插件识别”。内部候选流程直接调用低层媒体源，不触发这个辅助事件。

独立聊天已按用户要求移除；AI 仅用于名称辅助。旧聊天配置在加载时剥离，不恢复会话；历史迁移原始证据保留。

名称桥需要 W10 迁移服务的有效唯一响应者回执，并重新读取当前宿主公开处理器、插件配置和运行代次。回执缺失、过期、不完整或存在未分类的共同响应者时阻断相应功能。其他未调用的辅助事件监听器不会阻断内部候选名称流程。

## W10 / W09 稳定 Python 接口

- `AIConfig.model_validate(config['ai_assist'])`：严格校验，字段见下表。AI 配置错误只停用 AI；接管保护监听器继续工作。
- `AIService(repository, config, client_factory, credential_resolver, *, generation, current, instance_id='SubscriBetter', proxy=None, clock=time.time, owner_check=None, owner_snapshot=None, notify=None)`。`current()` 必须检查 ordinary-work 与捕获的插件代次。客户端工厂注入已安装的同步 `httpx.Client`，测试使用其 `MockTransport`。
- `assist(title, subtitle, correction, *, corrector, custom_words=None, locks=(), gate=None, started=None)` 返回 `(Correction, Result)`，保留原始 Meta 和技术字段。W09 必须随后进行实际媒体源查询，不能把名称接受当身份匹配。
- `extract(title, subtitle='', *, context=None, parser_revision='', team=None, gate=None, started=None)` 是同一有界底层服务。context 仅支持 `locks/native_name/native_year/reasons/custom_words/team`，不接受目标媒体 ID；名称证据仍仅来自标题／副标题。
- `Result` 提供 `identity/reason/source/attempts/usage/elapsed_ms/request_digest/generation/text`。身份接受、候选提交、实际 ID 匹配和后续下载／入库分别计数；缓存及合并等待者不重复计费。
- `name_event(event, meta_service)` 只处理同步本地工作；`drain(meta_service)` 由宿主调度调用，**不能持有插件 runtime_lock**。
- `stats()` 返回计数、真实已返回 token 用量、冷却、队列、在途和最多 100 条未决请求摘要；不包含凭据、原始响应。`clear_cache(actor)` 写审计并隔离旧请求，不清除持久重试／冷却／未决预算。`close()` 停止发布，实际在途 HTTP 完成后才关闭客户端。

### 唯一响应者回执

W10 绑定 `plugin.migration.unique_owner(module, instance_id, config_digest, route_scope)`。名称辅助使用 `name_assistance` / `internal`，名称桥使用 `name_bridge` / `{'event':'NameRecognize'}`。返回：

```python
{
    'receipt_id': '非空回执标识',
    'status': 'ACTIVE',
    'selected_old_disable_receipts': [],
    'expected_new_feature_set': {
        'module': module, 'instance_id': instance_id,
        'config_digest': config_digest, 'route_scope': route_scope,
    },
    'fresh_handler_config_fingerprint': '64 位小写十六进制摘要',
}
```

使用 `host_owner_snapshot(plugin, module, instance_id, config_digest, route_scope)` 构建相同的当前公开投影；W08 独立重读并精确比较。空旧停用回执列表只适用于当前没有重叠响应者。`owner_receipt(...)` 只是 DTO 构造函数，不授予权限。W10 必须完成所选旧功能的真实停用、重载和读回，不修改无关功能。

### 配置与迁移

| 配置 | 默认值与范围 |
|---|---|
| enabled / name_assistance_enabled | false / true；总开关仍默认关闭 |
| endpoint_ref / credential_refs | 空；`secret:` 加 32 位十六进制，只接受有序、不重复的引用 |
| model / profile | 空（必须配置） / auto；auto 只对精确的 api.deepseek.com 域名启用 DeepSeek 扩展 |
| compatible / proxy | false / false；兼容模式保留 API 基址，否则补 /v1 |
| timeout / max_attempts / max_concurrency | 20 秒 / 2 / 2；范围 1–120 / 1–5 / 1–8 |
| queue_size | 32，范围 1–256；合并等待者也受准入限制 |
| positive_ttl / negative_ttl / cache_size | 3600 秒 / 600 秒 / 1000；TTL 0–86400，容量 1–10000 |
| prompt / prompt_backup / prompt_previous_backup | 完整当前模板及两个备份，每项最多 32768 字符 |
| notifications / name_recognize_bridge | 均 false |

`migrate_legacy(data, put_secret)` 保留已选模型、顺序密钥、完整自定义提示词和历史备份，升级已知旧模板；返回的配置不会自动启用 AI 或名称桥。`legacy_preview(data)` 提供旧启用状态及被忽略的一次性标志供 W10 审核。导入不重放清缓存／恢复提示词。最多 31 个密钥加一个基址；超出私有存储容量会明确拒绝，不能截断导入。

`SecretStore(plugin.get_data_path())` 的 `put(value)`／`resolve(reference)` 使用独立的 `ai-secrets.json`：POSIX 0600、当前用户、单链接、逐层拒绝符号链接、目录文件锁、原子替换与 fsync；最多 32 个引用、64 KiB。Windows 无法靠 chmod 保证 POSIX 权限，因此拒绝真实写入。W10 的管理员入口负责写入／轮换／导入，必须从导出中排除此文件。普通配置和 SQLite 仅存储引用、摘要、受限状态与计数。

HTTPX 禁止自动重定向、启用 TLS 校验、禁用环境代理继承，显式配置代理。DeepSeek 名称提取发送 thinking disabled、JSON 格式与零温度；generic 不发送这些扩展。401 才有限轮换并停用确认无效的密钥；429 尊重 Retry-After，其他错误不轮换。请求声明 Accept-Encoding: identity，读取正文前拒绝非 identity 编码；raw 响应 envelope 最多 64 KiB，不经过自动解压，输出文本最多 8192 字符。

当前 SQLite schema 12；AI 记录始于 schema 8，保存代次、请求预算、冷却与计数。进程重启不会盲目重试未决派发；换配置也不会越过同实例仍在途的请求。未决项显示为 `outcome_unknown` / `previous_runtime_draining`，需要 W10 管理恢复依据，不能靠清缓存消除。正结果缓存是内存数据；重启保留冷却但不声称迁移旧内存缓存。负结果期限跨重启保留。

协议／配置兼容算法来自 eitelkeit0708 的 ChatGPTPlusUltra 1.4.2（基线 cbd770e364ec9a96a81fbfe9ac8d33abdb2bb1ba），遵循仓库 GPL-3.0；V3 增加来源边界、重载隔离、持久预算和唯一响应者门。固定宿主合同来源为 MoviePilot e195cc164fc8ff869ffee0ea44a49c7ec475310c。
