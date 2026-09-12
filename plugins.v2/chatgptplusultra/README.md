# ChatGPT Plus Ultra 1.4.1

面向 MoviePilot V2 的保守型辅助名称提取器。AI 只返回 `name/year` 两个字符串；插件验证后，使用 MP2 对**当前标题**的原生解析结果补齐 NameRecognize 事件的季集。它只提供候选，不替代媒体库匹配，不调整字幕、画质、站点或洗版规则。

## 1.4.1：恢复可追踪日志，保留隐私边界

### 日志等级

- **INFO**：实际 API 调用产生、且成功提交给 MP2 的新候选，包含关联 ID、名称、年份、来源、耗时。不把“提供候选”说成“最终识别成功”。
- **DEBUG**：原始标题（脱敏并截断至 480 字符）、AI 的 name/year、当前标题原生解析的 name/year、最终回填的 name/year/season/episode、每次调用的来源、HTTP 请求次数、返回的 Token 用量、跳过或拒绝原因。季集明确标注 `season_episode_source=MetaInfo`。
- **WARNING**：服务错误显示分类、密钥编号和可用的 HTTP 状态码，不显示服务端响应正文；同类提示 300 秒去重。结构/语义校验拒绝单独提示，也做去重，但不触发“密钥失效”通知。模型明确放弃识别不是 API 故障。

示意日志（不是生产运行记录）：

```text
INFO ChatGPTPlusUltra: id=abc123 status=submitted reason=accepted source=api elapsed_ms=820.0 name="作品甲" year="2024"；已提交名称候选，最终匹配由 MP2 决定
DEBUG ChatGPTPlusUltra: id=abc123 status=submitted reason=accepted source=cache elapsed_ms=0.2 title="[作品甲] 2024 S02E03" ai_name="作品甲" ai_year="2024" api_attempts=0 usage={} meta_name="作品甲" meta_year="2024" name="作品甲" year="2024" season=2 episode=3 season_episode_source=MetaInfo
DEBUG ChatGPTPlusUltra: id=def456 status=rejected reason=field_set source=api ...
DEBUG ChatGPTPlusUltra: id=def456 status=rejected reason=field_set source=cache ...
```

同一标题及同一配置使用相同关联 ID，方便串起重复轮询；它不是每次调用唯一的请求编号。来源直接由缓存的当前调用结果返回，**不通过全局统计前后差值推断**，因此并发时不会把别人发出的请求算到当前标题上。

`source=api` 表示本次实际发出 HTTP 请求；`cache` 表示本地缓存复用（结合 status/reason 区分正负缓存）；`coalesced` 表示等待同标题正在进行的请求；`local` 表示本地跳过或冷却期间未发请求。缓存及并发复用的重复提交仅写 DEBUG，避免每 5 分钟的轮询重新刷满 INFO。

本插件自身的诊断日志不输出密钥、Authorization/Cookie 原文、原始模型响应、完整服务端错误正文或聊天回复。标题中的 URL 整段隐藏，配置的密钥和常见凭据格式会被脱敏，控制字符被清理，字符串单行 JSON 转义。**普通片名仍然可见，不是匿名化日志**；无法自动识别所有无标签的个人信息，分享 DEBUG 日志前仍应检查。HTTPX 等依赖自己的日志不由本插件全局修改。

### 常见 reason

| reason | 含义 |
| --- | --- |
| accepted | 名称候选通过插件校验，不代表媒体库匹配成功 |
| no_name | 模型明确放弃或未给出可用名称 |
| empty_response / invalid_json / not_object | 响应为空、坏 JSON、不是 JSON 对象 |
| duplicate_key / field_set / field_type | 重复字段、字段不是恰好 name/year、值不是字符串 |
| name_not_in_input / movie_marker_lost | 名称没有输入依据，或丢失了显式电影版标记 |
| name_is_metadata / name_is_release_group | 把技术标签或 MP2 当前解析出的制作组当成片名 |
| year_not_in_input / invalid_year_format | 年份没有独立数字依据或格式不合法 |
| year_in_name / year_is_date | 数字只出现在片名中，或只出现在完整日期中 |
| existing_result / event_changed | 之前已有名称，或事件在等待期间被其他处理器修改 |
| stale_runtime / invalid_metainfo_number | 配置已更换/停用，或原生季集值不合法 |
| adapter_error | 适配层异常；只记录异常类型，不转储异常正文 |

### 其他修复与统计

1. 修复空提示词或空白提示词恢复默认后，没有把完整默认文本保存到配置的问题。保留旧自定义提示词备份，不覆盖 API、模型或其他配置项。
2. 修复并发错误的较短冷却覆盖较长 Retry-After 的问题。已有的等待截止时间只延长，不缩短。
3. 年份同时检查数字片名、完整日期和原生年份回退；新增纯技术/字幕标签拒绝。制作组保护使用当前 `MetaInfo.resource_team` 的证据，不按全局组名黑名单禁用资源。
4. 缓存统计去除已过期条目；详情页增加识别 HTTP 次数、其他 HTTP 次数、模型校验结果分布及冷却状态。
5. 只累计真实 HTTP 响应明确返回的非负整数 Token 字段，缓存重放不重复累计。`prompt_cache_hit_tokens` 是服务商的提示词缓存，不是本地识别结果缓存。服务商不返回用量时不估算，不声称这些数字等于账单或费用。

年份、技术标签和名称依据检查仍是保守规则，不是数据库真值验证。仅有完整日期而没有独立发行年份的标题也可能被保守拒绝；真实发行日期与上传日期不能靠这条接口完全区分。现有文字依据检查不自由翻译、不自动简繁转换；剧场版标记保护可能拒绝一些合法别名。没有放宽这些规则去追求表面的识别成功率。

## MP2 兼容性

本次重新核对的 V2 分支 HEAD 为 `6a02e7de21c110d758e3fc44e79150ac1d4d7949`。1.4.0 对照的发布版为 `v2.15.6`，其 `app/chain/media.py` 与该 V2 HEAD 的 blob 均为 `2c17871a2f42454685c7069ad2bdb6f63f658682`。

- 同步及异步辅助识别：`app/chain/media.py` 的 `recognize_help`、`async_recognize_help`。
- 当前标题解析：`app/core/metainfo.py` 的 `MetaInfo`；仅读取 `begin_season/begin_episode`，不调用会补默认季号的便利方法。
- 事件：`app/core/event.py`；尊重已有有效名称并保留不相关事件字段。
- 日志：`app/log.py`；继续使用 MP2 的 `logger.info/debug/warning` 单消息调用。

源代码基准：[MoviePilot V2 固定提交](https://github.com/jxxghp/MoviePilot/tree/6a02e7de21c110d758e3fc44e79150ac1d4d7949)。未按 V3 拆分接口重写，未动态替换主程序方法。

### 仍然存在的主程序边界

NameRecognize 请求只有 `title`，返回字段读取 `name/year/season/episode`。**AI JSON 与插件事件是两层不同协议**，AI 不输出季集或 title。

- 主程序名称/年份变化后会覆盖起始季集；任一季集非 None（包括 0）会设为 TV。本插件不让 AI 猜季集，明确的原生 S00 保留 0，原生无季集则传 None。
- 两个 None 不能把主程序已经判为 TV 的类型强行改回电影。
- 名称和年份都不变时，主程序提前返回，季集单独修正不能生效。
- 原路径、副标题和父目录继承信息没有完整传入事件。当前标题的 MetaInfo 不是原调用者元数据的完整副本，无法保证保留不可见信息。
- 没有最终匹配反馈。负缓存只减少模型请求，不更改主程序的订阅识别重试计数，也不保证消除重复媒体库查询。

建议只开启一个 AI 名称识别提供者。本插件不覆盖之前的有效名称，但不能阻止后续其他插件覆盖结果。

## 配置与升级

更新到 **1.4.1** 后保存一次插件配置即可。1.4.0 的提示词无需重新复制；本次继续只接受 `name/year`，并保留现有模型和接口设置。需要完整识别详情时将 MP2 日志级别设为 **DEBUG**；INFO 也会显示 API 新候选。

DeepSeek 请求配置沿用 1.4.0：官方域名可自动识别，第三方转发按其实际基址和模型别名填写；选择 DeepSeek 配置后使用非思考模式及辅助识别 JSON 模式。不发送联网工具。API 基址可有 `/v1`，不会重复追加；不要填完整 `/chat/completions` 地址。没有在本次测试中验证真实服务商调用。

默认有效候选缓存 3600 秒、负缓存 600 秒、容量 1000 条、同时请求 2 个，仍可配置。完整标题、接口、模型、有效提示词及校验协议版本参与缓存标识，不删除括号或季集。缓存为内存缓存；重启或保存配置会重建。仍只有明确 401 才禁用对应密钥并有限轮换；429/网络/超时/配置错误分类冷却，不用切换密钥规避限流。

默认请求预算 20 秒包含排队和鉴权轮换；HTTPX 的连接/读写/连接池超时不是严格总墙钟计时，缓慢持续传输仍可能超出。已发出的请求停用后可结束，但旧结果不能回填；客户端在请求结束后释放。聊天保持按渠道/用户隔离及有限历史，本次不扩展聊天功能。

## 测试

```sh
python -m pytest -q tests/chatgptplusultra
python -m compileall -q plugins.v2/chatgptplusultra tests/chatgptplusultra
```

本次插件测试共 94 项通过（原有 56 项，加 38 项日志、缓存来源、校验、并发冷却和配置边界回归）。HTTPX 为真实 0.28.1 Client，使用 MockTransport 替代外部网络；MP2 服务/数据库/原生 MetaInfo 为测试替身。`mp2_contract.py` 是同步/异步字段应用契约摘录，不是完整 MP2。未使用真实 API 密钥，也未在 NAS 进行下载、整理集成测试；未运行无关插件的测试套件。
