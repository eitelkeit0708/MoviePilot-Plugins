# ChatGPT Plus Ultra 1.4.0

面向 MoviePilot V2 的保守型辅助名称提取器。模型只提取 `name/year`；插件校验后，以 MP2 当前标题解析结果补齐事件需要的季集。它提供候选，不替代 TMDB 等媒体数据源，也不调整订阅、洗版、字幕或画质规则。

## 对照的主程序

2026-09-13 核对：V2 最新发布版为 `v2.15.6`；V2 分支 HEAD 为 `6a02e7de21c110d758e3fc44e79150ac1d4d7949`。两者 `app/chain/media.py` 的 blob 相同：`2c17871a2f42454685c7069ad2bdb6f63f658682`。未按 V3 的拆分接口实现。

- [同步与异步辅助识别](https://github.com/jxxghp/MoviePilot/blob/6a02e7de21c110d758e3fc44e79150ac1d4d7949/app/chain/media.py)：`recognize_help`、`async_recognize_help`。
- [原生 MetaInfo](https://github.com/jxxghp/MoviePilot/blob/6a02e7de21c110d758e3fc44e79150ac1d4d7949/app/core/metainfo.py)：读取当前标题的 `begin_season/begin_episode`，不调用会补默认季号的便利方法。
- [事件管理器](https://github.com/jxxghp/MoviePilot/blob/6a02e7de21c110d758e3fc44e79150ac1d4d7949/app/core/event.py)：同一个事件可能经过多个插件；本插件尊重已有有效名称并保留不相关字段。
- [主程序依赖](https://github.com/jxxghp/MoviePilot/blob/6a02e7de21c110d758e3fc44e79150ac1d4d7949/requirements.in)：沿用 `httpx~=0.28.1`；不安装或升级共享 OpenAI SDK。本插件不再依赖 `cacheout`，但不卸载其他插件需要的库。

## DeepSeek V4.1 Flash 设置

| 设置 | 官方服务建议值 |
| --- | --- |
| API 基址 | `https://api.deepseek.com` 或 `https://api.deepseek.com/v1` |
| 模型 ID | `deepseek-flash` |
| 请求配置 | 自动，或 DeepSeek |
| 兼容模式 | 上述官方基址均可关闭；已包含 `/v1` 时不会重复追加 |
| 辅助识别 | 开启 |
| 消息聊天 | 仅有需要时开启；新增安装默认关闭，旧安装保留原行为 |
| 使用代理 | 按实际网络需要，不替用户更改 |

模型 ID 来自 [DeepSeek 2026-09-10 公告](https://www.deepseek.com/en/news/deepseek-v4-1-flash/)。DeepSeek 请求使用 `thinking: {type: disabled}`；识别另外使用 `response_format: {type: json_object}`、`temperature: 0`、`max_tokens: 512`。不传入 tools、网络搜索工具或 function calling。参考 [JSON 模式](https://api-docs.deepseek.com/guides/json_mode/) 与 [思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。HTTP JSON 请求不是 SDK 参数：`thinking` 位于顶层，不套 `extra_body`。

使用第三方转发服务时，保留其实际基址和模型别名，手动选 DeepSeek 才会发送相应扩展。通用模式不发送 DeepSeek 专用参数，也不强制服务端 JSON 模式，但本地仍严格检查两个字段。不要把完整 `/chat/completions` 地址填成基址。

## 提示词与校验

新版完整提示词位于 `recognition.py:DEFAULT_PROMPT`，配置界面可查看、复制或修改。

- 方括号优先仅适用于真实片名，不把制作组、分辨率、字幕或集号标签当片名。
- 优先保留输入中的中文名；没有可信中文名则使用原文，不凭模型记忆翻译、补名或补年份。
- 保留续作编号、副标题、剧场版、电影版和 The Movie 等身份信息。
- 年份未知使用空字符串，不强制编造四位数字。不能识别时输出 `{"name":"","year":""}`。
- 输出必须恰好两个字符串字段 `name/year`。数组、额外字段、重复键、坏 JSON、代码围栏、null、数字类型均不交给 MP2。
- 名称必须能在当前输入中找到文字依据，允许大小写、宽度及标点变化，不允许无依据翻译。显式电影版标记被丢失时保守拒绝。因此部分自由译名、简繁转换和标题缩写可能被拒绝，这是精确提取模式的取舍。
- 年份必须在标题中有独立数字依据；这不是对发行年份真实性的数据库验证。模型候选和媒体库最终匹配成功是两件事。

仅自动迁移空提示词、仓库旧默认提示词，以及本次修复对应的“STRICT 7 FIELDS 但只列 name/year”的已知两字段提示词。任意其他自定义内容保留。替换非空旧提示词时会保存 `previous_customize_prompt`，不覆盖模型、API 密钥及未知配置项。保存时勾选“恢复新版默认提示词”可手动恢复；运行时仍追加简短的两字段协议约束。

## 缓存、错误与生命周期

完整标题 + 模型 + 基址 + 有效提示词 + 协议版本构成指纹；不删除方括号或季集，不跨资源复用季集。缓存为配置实例内存缓存，重启、停用或保存配置会重建，不写入用户数据库。

默认有效候选缓存 3600 秒，放弃识别缓存 600 秒，容量 1000 条；可配置。相同标题并发访问合并为一次调用，不同标题最多同时请求 2 个。缓存返回副本，清理过程中旧请求不能重新填入已经清空的缓存。

没有名称、格式不合格或不能可靠提取：负缓存并退出，不轮换或禁用密钥。401：仅禁用实际出错的密钥槽位，最多尝试 2 个不同密钥（可配置）。429：按 Retry-After 冷却，不用换密钥绕过共享限流。超时、网络错误、403、配置错误或服务端错误分别短暂冷却；不永久封禁有效密钥。失败结果另短缓存 30 秒，同类错误通知 5 分钟去重。重新保存配置会重置密钥健康状态。

默认请求预算/超时 20 秒；排队与鉴权轮换共同消耗这个预算。HTTPX 的 connect/read/write/pool 超时不是严格总墙钟计时，缓慢持续传输仍可能超过该时间；不声称“20 秒必定结束”。没有 SDK 隐式重试，只有明确鉴权失败才在预算内轮换。日志和通知只包含错误类别与密钥编号，不输出密钥、原始模型响应或服务端错误正文。

配置重载或停用会立即使旧结果失去回填资格；已开始的 HTTP 请求可结束后释放客户端，不中途强关正在使用的连接。聊天按渠道和用户隔离上下文，保存真实助手回复，并限制历史长度；`#清除` 清空当前会话。

## MP2 接口的已知边界

MP2 当前只向 NameRecognize 事件传入 `title`，读取返回的 `name/year/season/episode`。模型 JSON 仍只有两字段；插件内部事件包含 `title` 和原生解析出的季集，这是不同层的协议。

1. 名称或年份改变后，主程序会覆盖起始季集；只要季或集非 None（包括 0），主程序会设为 TV。本插件不从模型读取季集；当前标题原生解析为电影无季集时传 None，明确 S00 则保留 0。
2. 主程序不会因为两个 None 把已经是 TV 的类型改回电影。不能宣称本插件能强制修正所有电影/电视剧类型。
3. 名称、年份都不变时，主程序提前退出，季集单独修正不能生效。
4. 原始路径、副标题继承的季集、类型和年份不在事件输入内。本插件无法完整恢复这些不可见信息；原生当前标题年份仅在有输入依据时作为空年份回退。主程序仍可能清空不可见的继承字段。
5. 无最终匹配反馈接口，插件负缓存只减少模型调用，不修改主程序订阅重试计数或保证消除所有重复媒体查询。

未动态替换 MP2 方法，未强制媒体类型、放宽候选过滤或更改其他插件。建议实际只开启一个 AI 名称识别提供者；本插件不覆盖之前的有效结果，但无法阻止后续其他插件覆盖它。

## 升级与验证

通过原插件仓库更新到 1.4.0，保存一次插件配置。既有 API 和模型配置不会被静默替换；核对模型 ID/请求配置及辅助识别开关，必要时勾选恢复新版提示词。已有原插件外的错误缓存或主程序历史状态不由本插件清除。

仓库根目录运行：

```sh
python -m pytest -q tests/chatgptplusultra
python -m compileall -q plugins.v2/chatgptplusultra tests/chatgptplusultra
```

测试使用真实 HTTPX Client + MockTransport 替代外部网络；MP2 服务、MetaInfo/Rust/数据库为桩。`mp2_contract.py` 是经源码对照的同步/异步处理契约摘录，保留分支赋值顺序并移除了注释、类型注解及日志，并不是整个主程序。本测试可检查字段交付、缓存/并发、错误处理、配置迁移与主程序已知边界；不能替代真实 DeepSeek 或 NAS 上完整 MP2 的集成验证。
