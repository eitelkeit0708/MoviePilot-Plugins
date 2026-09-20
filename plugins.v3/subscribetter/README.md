# subscriBetter V3：统一订阅、作品发现与名称辅助

## 作品发现

`Config.discovery` 使用结构化 `DiscoveryConfig`。配置自建 RSSHub `rsshub_base_url` 后，可从 `/discovery/sources` 查看 13 条固定豆瓣影视相对路由及完整 URL；没有公共 RSSHub fallback。自定义 RSS 每行迁移为一个 `kind=custom` source。管理员可用 `/discovery/test` 仅取源/解析，`/discovery/run` 显式运行，`/discovery/records` 查看 `all/latest12/recognized/unrecognized` 历史及分阶段统计，另有 POST reprocess 和 history/cleanup。作品发现与 PT 下载资源是两个不同的数据域。

来源请求使用严格同源重定向、identity encoding、总时限、响应字节/项目/文本/XML 深度限制，以及固定 SDK `SecurityUtils.evaluate_url_safety`。字面私网 IP 只授权该地址；私网域名需要配置 `allowed_private_ranges`。RSS 标题、描述、排名、年份和分数只是原始声明；身份、年份、TMDB 分数及明确季号来自实际识别/provider 返回。多季只使用明确、已播、正数 season_number，不从 season 数量构造范围。

每个来源在取源前都必须通过 W10 的 `migration.unique_owner(...)` 和 `migration.owner_snapshot(...)` 精确回执。新电影在原生壳 ACK 后直接用共享 `Scheduler.open_opportunity` 建立连续机会。电视剧还要求 W10 绑定 `migration.discovery_scope(request) -> {'episodes': [明确正整数集号...]}`；未绑定或范围不完整时保持 DEFERRED，绝不由 episode_count 造集号。全新目标档案为 UNKNOWN 时可绑定 `migration.inventory_refresh(request)` 做选定媒体库内的有界定向刷新，返回带 `evidence_ref` 的 `UNKNOWN/MISSING/PRESENT/PARTIAL/INGESTED`。已有档案由 `Archive.discovery_inventory(Target)` 直接投影；部分季在默认 `record_only` 下只链接事实，不接管下载。

自动提交还要求来源有对应电影/剧集保存目录、至少一个启用交付规则，以及同 provider 的已选 Emby service/library mapping。所有成功目标仍只调用共享 `Ownership.submit`，使用同一 `tasks/intents/outbox`、STOPPED/RELEASED 和排除边界；没有第二个下载队列。显示历史清理只隐藏记录，保留目标、任务和回执。

AI 默认关闭。内部候选流程先执行完整 Meta 修正，只有名称缺失或可解释的名称冲突才调用名称辅助；随后仍用真实媒体源返回的 ID 验证身份。模型只能提供两个字符串 `name`、`year`，不能提供季集、类型、媒体 ID、下载选择或订阅执行权。确定性解析已明确时不调用模型，范围冲突和用户规则也不会被模型覆盖。

可选 NameRecognize 桥默认关闭。同步事件只读取缓存并投递有界工作队列，第一次调用可能不提供结果；后续调用才可能获得缓存。该桥受宿主事件只提供标题、不能替换完整 Meta 的限制，不能保证修复宿主的同名同年份短路。插件不会自动修改宿主“优先使用插件识别”。内部候选流程直接调用低层媒体源，不触发这个辅助事件。

可选普通聊天默认关闭，必须配置精确的渠道、来源、用户、会话白名单。保留普通文字触发条件和 `#清除`，限制输入长度、会话数量及历史。没有工具、订阅、下载、规则修改或删除接口。所有模型调用和消息发送均由工作流程执行，事件回调不发送网络请求。宿主原生命令／Agent 可能提前消费消息。

V3 出站消息保留 `channel/source/userid/original_chat_id`，仅使用普通文本。宿主没有出站 `reply_to_message_id` 字段；`original_message_id` 用于编辑消息，因此不会填入收到的回复消息 ID。不承诺定点线程回复。

桥和聊天都需要 W10 迁移服务的有效唯一响应者回执，并重新读取当前宿主公开处理器、插件配置和运行代次。回执缺失、过期、不完整或存在未分类的共同响应者时阻断相应功能。其他未调用的辅助事件监听器不会阻断内部候选名称流程。

## W10 / W09 稳定 Python 接口

- `AIConfig.model_validate(config['ai_assist'])`：严格校验，字段见下表。AI 配置错误只停用 AI；接管保护监听器继续工作。
- `AIService(repository, config, client_factory, credential_resolver, *, generation, current, instance_id='SubscriBetter', proxy=None, clock=time.time, owner_check=None, owner_snapshot=None, notify=None)`。`current()` 必须检查 ordinary-work 与捕获的插件代次。客户端工厂注入已安装的同步 `httpx.Client`，测试使用其 `MockTransport`。
- `assist(title, subtitle, correction, *, corrector, custom_words=None, locks=(), gate=None, started=None)` 返回 `(Correction, Result)`，保留原始 Meta 和技术字段。W09 必须随后进行实际媒体源查询，不能把名称接受当身份匹配。
- `extract(title, subtitle='', *, context=None, parser_revision='', team=None, gate=None, started=None)` 是同一有界底层服务。context 仅支持 `locks/native_name/native_year/reasons/custom_words/team`，不接受目标媒体 ID；名称证据仍仅来自标题／副标题。
- `Result` 提供 `identity/reason/source/attempts/usage/elapsed_ms/request_digest/generation/text`。身份接受、候选提交、实际 ID 匹配和后续下载／入库分别计数；缓存及合并等待者不重复计费。
- `name_event(event, meta_service)`、`enqueue_chat(event, send)` 只处理同步本地工作；`drain(meta_service)` 由宿主调度调用，**不能持有插件 runtime_lock**。`chat(text, route, *, send, started=None)` 是同一授权检查下的同步内部接口，不提供管理动作。
- `stats()` 返回计数、真实已返回 token 用量、冷却、队列、在途和最多 100 条未决请求摘要；不包含凭据、原始响应或聊天记录。`clear_cache(actor)` 写审计并隔离旧请求，不清除持久重试／冷却／未决预算。`close()` 停止发布，实际在途 HTTP 完成后才关闭客户端。

### 唯一响应者回执

W10 绑定 `plugin.migration.unique_owner(module, instance_id, config_digest, route_scope)`。`module` 仅为 `name_bridge` 或 `chat`；桥 scope 为 `{'event':'NameRecognize'}`，聊天 scope 是精确的四字段路由。返回：

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
| notifications / name_recognize_bridge / chat_enabled | 均 false |
| chat_routes | 最多 100 个精确 channel/source/userid/chat_id 字符串对象 |
| chat_input_limit / chat_history_limit / chat_session_limit | 16000 字符 / 32 条历史 / 100 会话，可下调 |

`migrate_legacy(data, put_secret)` 保留已选模型、顺序密钥、完整自定义提示词和历史备份，升级已知旧模板；返回的配置不会自动启用 AI、桥或聊天。`legacy_preview(data)` 提供旧启用状态及被忽略的一次性标志供 W10 审核。导入不重放清缓存／恢复提示词。最多 31 个密钥加一个基址；超出私有存储容量会明确拒绝，不能截断导入。

`SecretStore(plugin.get_data_path())` 的 `put(value)`／`resolve(reference)` 使用独立的 `ai-secrets.json`：POSIX 0600、当前用户、单链接、逐层拒绝符号链接、目录文件锁、原子替换与 fsync；最多 32 个引用、64 KiB。Windows 无法靠 chmod 保证 POSIX 权限，因此拒绝真实写入。W10 的管理员入口负责写入／轮换／导入，必须从导出中排除此文件。普通配置和 SQLite 仅存储引用、摘要、受限状态与计数。

HTTPX 禁止自动重定向、启用 TLS 校验、禁用环境代理继承，显式配置代理。DeepSeek 配置发送 thinking disabled，仅名称提取额外发送 JSON 格式与零温度；generic 不发送这些扩展。401 才有限轮换并停用确认无效的密钥；429 尊重 Retry-After，其他错误不轮换。请求声明 Accept-Encoding: identity，读取正文前拒绝非 identity 编码；raw 响应 envelope 最多 64 KiB，不经过自动解压，输出文本最多 8192 字符。

SQLite schema 8 保存代次、请求预算、冷却与计数。进程重启不会盲目重试未决派发；换配置也不会越过同实例仍在途的请求。未决项显示为 `outcome_unknown` / `previous_runtime_draining`，需要 W10 管理恢复依据，不能靠清缓存消除。正结果缓存是内存数据；重启保留冷却但不声称迁移旧内存缓存。负结果期限跨重启保留。

协议／配置兼容算法来自 eitelkeit0708 的 ChatGPTPlusUltra 1.4.2（基线 cbd770e364ec9a96a81fbfe9ac8d33abdb2bb1ba），遵循仓库 GPL-3.0；V3 增加来源边界、重载隔离、持久预算和唯一响应者门。固定宿主合同来源为 MoviePilot e195cc164fc8ff869ffee0ea44a49c7ec475310c。
