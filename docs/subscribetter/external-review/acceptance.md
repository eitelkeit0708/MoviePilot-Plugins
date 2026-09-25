# 完整需求与验收矩阵

历史状态快照：2026-09-26；完整判据、输入覆盖、历史修订和证据 ID 见 [acceptance.json](acceptance.json)。
193 项登记通过不是当前版本 193 项真实全链路复验。6 项未闭环；T183 已按用户要求移出活跃范围。原始设计须结合 [修订表](README.md#设计基线之后的明确修订)。

## 全部需求

| ID | 需求 | 验收项 |
|---|---|---|
| R01 | 独立准入/排序/升级/完成，不依赖原生100分 | T001, T002 |
| R02 | 未受管质量/执行行为不变，Meta全局影响须显式开启 | T007, T008, T009, T010, T011, T081 |
| R03 | 复用MP站点、识别、下载器与配置 | T001, T002, T003, T004, T005, T006 |
| R04 | 新剧集从创建开始按集补齐与升级 | T012 |
| R05 | 完结后模式可选，保留逐集事实 | T013, T014, T186 |
| R06 | 分类偏好与语言/官组/原盘保护迁移 | T015, T016, T019, T020, T021, T027 |
| R07 | 四档音频、1080简化、WEB/EDR/HQ边界 | T015, T016, T017, T018, T019 |
| R08 | 两类特效质量＋PGS证据＋同质量一次升级 | T022, T023, T024, T025, T137 |
| R09 | 缺失、处理失败、未知字段与在途区分 | T026, T027, T028, T090, T091, T103, T108 |
| R10 | 旧库无订阅建档，多版本有效最高 | T026, T029, T030, T083, T121 |
| R11 | 原始发布声明绑定具体文件与资产 | T006, T040, T054, T061, T062 |
| R12 | 每文件SHA1/size与云对象、季集关联 | T024, T043, T048, T060, T061, T063, T134 |
| R13 | 本地STRM两段映射，不要求pickcode | T058, T059, T060, T062, T095 |
| R14 | 继续Symedia/CD2/cloudfs链路 | T061 |
| R15 | 监控已整理目录，通知加速、完整对账兜底且幂等 | T041, T042, T085 |
| R16 | 专用云端暂存、齐套目录交付 | T049, T050, T051, T052, T114 |
| R17 | 字幕逐集绑定、双集/共享依赖及迟到补传 | T034, T038, T040, T049, T053, T054, T118 |
| R18 | N次秒传与CD2体积受限回退 | T043, T044, T045, T046, T047, T048, T092, T142 |
| R19 | 成功清理先远端核验，明确放弃另按授权清理监控目录 | T042, T055, T056, T057, T066, T131 |
| R20 | 不自动管理保种/删正式库，下载器删除独立权限且默认关闭 | T056, T067, T132, T133 |
| R21 | 精确排除与自定义候选条件 | T003, T064, T065, T068, T069, T120 |
| R22 | 排除→取消→清理→恢复目标→换源 | T066, T067, T068, T069 |
| R23 | 唯一有效交付权、受控转交、发布串行与重启恢复 | T025, T036, T041, T047, T051, T057, T065, T075, T077, T078, T083, T089, T119, T123, T124 |
| R24 | 可配置期限，与创建/模式解耦 | T070, T071, T084 |
| R25 | 不误终止连载/仍缺集任务 | T012, T070, T071, T127 |
| R26 | Emby库选择划定被动范围 | T005, T011, T072, T188, T189 |
| R27 | RSS一次性唤醒、去重与完成 | T072, T073, T074, T075, T129 |
| R28 | 历史只作模板，不当当前事实 | T009, T076, T084, T196 |
| R29 | 搜索与识别名称分离、数字/S00/ID保护 | T004, T031, T032 |
| R30 | 内置迁移名称AI并保持单一响应者 | T031, T032 |
| R31 | 独立排序不用加权和/组合穷举 | T001, T015, T097 |
| R32 | 严格文件索引执行而非盲信episodes | T033, T034, T035, T036, T037, T038, T039, T040, T109, T154 |
| R33 | 凭据复用、脱敏、不外传Hash信息 | T059, T079, T181, T195 |
| R34 | 规则预演、解释、真实样例回归 | T080 |
| R35 | 增强助手洗版相关功能验证接管 | T081 |
| R36 | 全部功能统一交付，Codex负责授权实机验收 | T082, T096, T138, T199 |
| R37 | 范围/一致性锁可选，显式意图优先 | T013, T014, T021 |
| R38 | 单独的发布、入库和任务完成时点 | T023, T030, T052, T055, T061, T074, T095, T126 |
| R39 | 受管插件故障不悄悄回退原生 | T007, T008, T010, T028, T045, T077, T084, T094, T141, T171, T191 |
| R40 | 结构化策略可扩展，但不任意执行不可信代码 | T079 |
| R41 | inotify/广播全丢仍以本地完整对账及持久化任务恢复 | T041, T085, T086, T087, T088, T089, T090, T091, T096, T116, T138 |
| R42 | 云端主动核验与Symedia补处理能力，不依赖通知重放 | T052, T091, T092, T093, T094, T095, T096, T125 |
| R43 | 候选基础/稳定/最长观察窗口，跨重启去重与提前规则 | T097, T098, T099, T100, T101, T102, T103, T104, T128, T130 |
| R44 | 下载/逐文件进展感知及可选有界早期抢占 | T105, T106, T107, T108, T109, T110 |
| R45 | 下载前认领、逐目标generation与交付前授权转移 | T065, T075, T078, T100, T105, T106, T110, T111, T115, T118, T119, T122, T135, T139 |
| R46 | 被取代秒传/回退任务失效、迟到结果隔离且不复活 | T048, T111, T112, T113, T114, T116, T117, T120, T137, T138, T139, T141 |
| R47 | 发布前再比较、PUBLISHING/UNKNOWN屏障与迟到防降级 | T051, T077, T078, T102, T112, T118, T121, T122, T123, T124, T125, T136, T138 |
| R48 | 入库确认后的逐目标升级冷却，ONESHOT归档仍保留 | T074, T126, T127, T128, T129, T130, T137, T138, T175 |
| R49 | 取代/放弃清理与下载器数据删除默认关闭的分级授权 | T056, T110, T115, T117, T131, T132, T133, T134, T140, T141, T142 |
| R50 | 取代预算防无限追逐，独立时钟、失败预算及可解释调度 | T098, T099, T101, T104, T107, T130, T135, T136, T140 |
| R51 | subscriBetter命名与V3专用结构/索引/测试/依赖 | T143, T144, T150, T153, T156 |
| R52 | V3公开SDK、事件与宿主数据访问合同 | T144, T148, T149, T151, T152 |
| R53 | V3成对媒体身份与category_id/revision分类绑定 | T145, T146, T147, T185 |
| R54 | V3重新核实执行能力、不照搬V2缺陷或宿主事务 | T148, T154, T155, T156 |
| R55 | 确定性Meta纠错，保护数字/括号/形态/范围与规格 | T031, T032, T157, T158, T159, T160, T161, T162, T163, T164, T165, T177, T192, T199 |
| R56 | 仅两个Meta桥接热补丁例外、幂等/可撤销/版本门 | T144, T168, T169, T170, T171, T200 |
| R57 | Python/Rust/路径合并/显式词一致与覆盖诚实 | T159, T162, T165, T166, T167, T172, T173 |
| R58 | 原文差异/解析revision与定向缓存重放，不重开任务 | T174, T175, T190 |
| R59 | ChatGPTPlusUltra名称与可选聊天功能内置等价 | T031, T032, T161, T176, T177, T178, T183, T199, T200 |
| R60 | AI结构校验/精确缓存/合并请求/错误与凭据保护 | T153, T158, T160, T176, T178, T179, T180, T181, T182 |
| R61 | 榜单/自定义RSS/筛选/多季度/历史功能迁移 | T184, T185, T186, T191, T192, T193, T194, T199 |
| R62 | 发现统一提交、STOPPED优先、存在范围和失败重试 | T187, T188, T189, T190 |
| R63 | 两个旧插件配置/历史幂等迁移与唯一切换 | T081, T182, T184, T195, T196, T197, T198 |
| R64 | V3实例/热重载/API安全与补丁单所有者 | T149, T150, T152, T167, T169, T180, T183, T193, T197, T198 |
| R65 | 来源证据区分与全量功能回归，不借旧测试充数 | T173, T194, T200 |

R59 的聊天子要求已撤销，名称辅助保留。附件范围等后续修订同入口说明。

## 全部用例

| ID | 需求 | 原工况 | 原预期 | 当前登记 | 要求层次 | 未完成验证 |
|---|---|---|---|---|---|---|
| T001 | R01, R03, R31 | V3原生全局规则拒绝1080，受管独立策略允许 | 受管候选绕开原生质量规则；未受管质量行为不变，Meta全局开关影响另测。 | passed | integration, source_contract |  |
| T002 | R01, R03 | V3高层rule_groups=[]/None及candidate_filter真实时点 | 在V3实际签名证明区别；低层主入口不依赖全局默认；历史V2行为不直接当V3结论。 | passed | source_contract |  |
| T003 | R03, R21 | 同站同标题描述的两个不同种子ID | 都保留并能独立排除/选择，不被原生高层去重吞掉。 | not_executed | offline_behavior, real_site | Find a genuine same-site pair with identical nonempty title and description but distinct torrent IDs, then verify both persist and can be independently excluded/selected in the isolated V3. |
| T004 | R03, R29 | 首个中文词仅返回无关结果，原名存在有效资源 | 有界继续可信别名，不把任何原始结果当搜索完成；保留数字作品名。 | passed | offline_behavior, real_site |  |
| T005 | R03, R26 | 授权站点列表为空 | 不调用会回退到全局/所有站点的接口，不扩大范围。 | passed | offline_behavior, source_contract |  |
| T006 | R03, R11 | 纯RSS缺少description/labels而条件依赖特效声明 | 使用既有详情能力补取或DEFER；不假造字段、不当明确否定。 | not_executed | offline_behavior, real_site | Find a genuine RSS candidate with absent description and labels, then verify detail completion or DEFER under a special-subtitle condition without invented fields. |
| T007 | R02, R39 | V3新增广播/快照尚未处理，原生尝试受管目标 | 验证V3真实入口与同步保护，不靠V2的60秒窗口；不能异步消息后到就漏纳管。 | passed | actual_host, source_contract |  |
| T008 | R02, R39 | V3已委托壳被原生显式sid搜索 | 按V3实际行为验证：不提交未授权重复下载、不抢先完成；不只依赖S状态码。 | passed | actual_host |  |
| T009 | R02, R28 | 原生check更新全部记录含受管壳 | 可核验同步元数据，不覆盖独立任务质量事实或把旧分数当新画像。 | passed | actual_host |  |
| T010 | R02, R39 | 插件停用/启动能力检查失败 | 现有委托壳保持安全暂停，提示返回原生操作；不静默回退自动下载。 | passed | actual_host |  |
| T011 | R02, R26 | 同媒体存在用户手动未受管订阅/下载 | 不误拦截或接管；有物理文件共享冲突时只阻止受管计划。 | passed | actual_host |  |
| T012 | R04, R25 | 连载已有E01普通版，E02缺失，E03尚未播出 | 能升级E01和补E02；不制造未来E03下载或提前结束整季。 | passed | actual_host, offline_behavior |  |
| T013 | R05, R37 | 完结后选择继续分集或整季 | 只有对应执行形式改变，逐集档案保留；创建范围开关不隐含强制全集。 | passed | actual_host, offline_behavior |  |
| T014 | R05, R37 | 混合质量E01高/E02低的全集候选 | 逐集不下降且至少改善才接受整包；分集模式可只选真正改进集。 | passed | offline_behavior |  |
| T015 | R06, R07, R31 | 高音频1080与普通音频4K，分类允许两者 | 按先分辨率比较，音频不抵消前置维度；仅4K类别不放宽。 | passed | offline_behavior |  |
| T016 | R06, R07 | 同画面特效WEB与普通字幕REMUX | 只在启用特效维度的分类按已确定顺序选择；不跨画面/分辨率加分。 | passed | offline_behavior |  |
| T017 | R07 | TrueHD Atmos/TrueHD/DDP Atmos/DDP/AAC/PCM/FLAC | 四档正确，无损不再分空间、PCM无额外加分，FLAC保留。 | passed | offline_behavior |  |
| T018 | R07 | 1080 DV/HDR/HQ、仅EDR、4K修复描述 | 1080不细分画面高码；EDR不自动HDR/HQ；修复宣传不自动变2160源。 | passed | offline_behavior |  |
| T019 | R06, R07 | 技术段WEB、WEB-DL、WEBRip、CHDWEB及标题Web | 前者同来源，后者不误认；原盘/ISO/BDMV黑名单仍正确。 | passed | offline_behavior |  |
| T020 | R06 | 日番VCB1080与B-Global4K、综艺非HHWEB | 保留用户梯队与HHWEB硬边界，不强推全分类统一排序。 | passed | offline_behavior |  |
| T021 | R06, R37 | 显式季锁与更高质量候选冲突；分类路由未命中 | 锁不被高分绕过；未分类不走无条件通道，输出配置问题。 | passed | offline_behavior |  |
| T022 | R08 | 中文PGS推定、无描述ASS、明确中文特效 | 两个质量类＋独立证据；不出现第三个质量档或凭ASS自动加分。 | passed | offline_behavior |  |
| T023 | R08, R38 | 旧PGS推定与新explicit质量相同，原生历史分数100 | 能执行一次证据升级而不改伪分；最终Hash/附件绑定后才标explicit。 | passed | actual_host, offline_behavior, real_cloud, real_consumer, real_downloader, real_emby |  |
| T024 | R08, R12 | explicit候选的内容与当前视频及字幕完全相同 | 补充证据即可；不重复下载/上传，校验不能只看视频忽略外挂。 | passed | integration, offline_behavior |  |
| T025 | R08, R23 | 同分特效任务失败、重启、跨站重复 | 同一目标接续重试，不提前确认、不重复建多个证据升级。 | passed | offline_behavior |  |
| T026 | R09, R10 | 旧库有视频无下载历史，技术画像充分 | 可以建档比较，不要求先重下载；不伪造旧发布历史。 | passed | offline_behavior, real_emby |  |
| T027 | R09, R06 | 旧库缺组名但技术已知；日番组名决定梯队 | 区分不影响比较的缺失与决定性缺证据；不伪组/暗降/判缺集。 | passed | offline_behavior |  |
| T028 | R09, R39 | Emby/CD2查询超时、401、半页、临时空列表 | 保持原存在/基线，记录错误及重试；不批量清空或放行替换。 | passed | offline_behavior |  |
| T029 | R10 | 一个目标两个有效版本和一个已退休高版 | 仅当前有效最高参与比较；确认高版删除后按剩余版本收敛。 | passed | offline_behavior, real_emby |  |
| T030 | R10, R38 | 同路径换文件 vs 只改字幕Title | 分别记录真实版本变化和元数据修订，不把Hash/画像指纹混同。 | passed | offline_behavior, real_emby |  |
| T031 | R29, R30, R55, R59 | 电影剧场版、真实S00、数字片名及明确ID冲突 | 共享确定性纠错与内部AI边界一致；不猜ID/type/season，不删片名数字。 | passed | integration, offline_behavior |  |
| T032 | R29, R30, R55, R59 | 内置AI与旧名称插件同时响应、相同失败被重复重试 | 迁移切换后只有一个有效提供者；保留错误/冷却语义；不要把原生非空或unchanged诊断当最终匹配成功。 | passed | integration, offline_behavior |  |
| T033 | R32 | V3 qB请求E07但torrent所有文件无法识别为目标 | 复用V3已正确原生能力或窄适配，回读wanted；零有效选择不恢复全种，不预设V2缺陷仍存在。 | passed | real_downloader |  |
| T034 | R17, R32 | 视频带E07而外挂字幕/字体不带集号 | 绑定清单包含必要资产，回读实际文件选择，不依赖纯集号过滤。 | passed | real_downloader |  |
| T035 | R32 | V3客户端已有同名同大小但不同infohash任务 | 核验真实身份不错误领养；原生若已修复直接复用，不按V2旧代码重新制造风险。 | passed | real_downloader |  |
| T036 | R23, R32 | 已有相同infohash但仅下载E01，现在要E02 | 明确共享归属、路径及集合变更，验证E02选择；不能仅凭返回hash宣告成功。 | passed | real_downloader |  |
| T037 | R32 | 文件选择API失败或部分设置，回读不一致 | 保持暂停，记录真实失败；不启动，不写已下载事实。 | passed | real_downloader, source_contract |  |
| T038 | R17, R32 | 双集视频跨越允许与排除集，或多季包同集号 | 按整文件和季+集处理；无法安全裁剪不冒充单集下载。 | passed | offline_behavior, real_downloader |  |
| T039 | R32 | 空episodes、磁力元数据未取得、含路径穿越的torrent | 空范围不全下，元数据未核实不启动，危险文件路径拒绝。 | passed | offline_behavior, real_downloader |  |
| T040 | R11, R17, R32 | DownloadFiles没有无集号字幕、DownloadAdded先于字幕后处理 | 插件清单补齐并追踪未完成附件；不在视频首个事件时提前齐套。 | passed | actual_host |  |
| T041 | R15, R23, R41 | 启动补扫、inotify与定时同时发现同一文件 | 只有一个有效计划/上传；通知、完整对账和启动恢复共用队列，变动文件生成新记录而非沿用旧成功。 | passed | integration, offline_behavior |  |
| T042 | R15, R19 | 软链接目录/重叠监控规则/STRM输出落入监控范围 | 拒绝危险循环/穿越或确定单一归属；不把cloudfs整库当本地新下载。 | passed | offline_behavior |  |
| T043 | R12, R18 | 单视频首次秒传成功 | 保存本地完整SHA1/大小、实际远端对象与核验，不混同torrent infohash。 | passed | real_cloud |  |
| T044 | R18 | 有效未命中达到N次 | 只未成功文件递增，次数持久化；符合条件转唯一CD2任务。 | passed | offline_behavior, real_cloud |  |
| T045 | R18, R39 | 认证/网络/限流连续失败 | 不当未命中、不耗尽后批量普通上传；有界退避和明确健康状态。 | passed | offline_behavior |  |
| T046 | R18 | 回退关闭、单文件>X、等于X、整季合计>X但单集<X | 严格按开关和单文件十进制GB条件判断；不按整季拒绝。 | passed | offline_behavior, real_cloud |  |
| T047 | R18, R23 | 任务从队列消失但目标尚无正确远端Hash | 不宣告成功，不删源文件；查远端与等待重试。 | passed | real_cloud |  |
| T048 | R12, R18, R46 | 115实际成功但响应丢失/重复回调 | 按批次对象与Hash先核对，只认领一次；若所属计划已取代只记旧暂存回执供收尾，不激活/发布。 | passed | offline_behavior |  |
| T049 | R16, R17 | 视频先秒传，字幕后成功 | 暂存不被消费；全组核验后交付，旧正式版在此之前不动。 | passed | real_cloud, real_consumer |  |
| T050 | R16 | 暂存位于消费者监控树、或暂存/目标不同账户 | 校验不通过，不把点号目录或不发刷新当隔离保证。 | passed | actual_host |  |
| T051 | R16, R23, R47 | 目录移动超时但已实际移动、目标有同名批次 | 持久化发布意图并查询实际落点，UNKNOWN期间不交付后继；不重传、不混合批次，不因lease超时解锁。 | passed | offline_behavior |  |
| T052 | R16, R38, R42 | 目录齐套交付后CD2事件分批/缓存延迟 | 核验真实消费者齐套与已存在的恢复入口；移动RPC和CD2可见都不冒充Symedia已处理。 | passed | real_consumer |  |
| T053 | R17 | 没有外挂字幕；后来出现字幕；一份共享字幕未能唯一绑定 | 无外挂可交付；迟到只补传；歧义不串集，不等待整部连载。 | passed | real_consumer |  |
| T054 | R17, R11 | IDX/SUB配对、明确依赖字体、字幕压缩包 | 保持必要依赖；压缩包未验证解包不称可用特效，不执行路径穿越。 | passed | integration |  |
| T055 | R19, R38 | 远端齐套并交付，Emby尚未扫到 | 允许按配置清理监控副本，仍保留待入库记录；不能先标任务全部完成。 | passed | real_cloud |  |
| T056 | R19, R20, R49 | 复制/移动/软链接/硬链接进入监控目录 | 仅删除登记监控目录项；链接目标/其他硬链接不追删，下载器任务及数据删除默认关闭。 | passed | offline_behavior |  |
| T057 | R19, R23 | Hash后文件变化或旧路径被新版本占用 | 旧上传/清理结果失效，不能删除新对象。 | passed | offline_behavior |  |
| T058 | R13 | 用户已提供两段路径和mp4目标样本 | 映射得到/115/media/...；读取实际STRM，中文花括号保留，不猜扩展名。 | passed | actual_host |  |
| T059 | R13, R33 | 最长前缀、根边界、另一个服务同路径、CloudAPI=115open | 按显式服务映射，不误用/115open；不允许跨根查询/读取。 | passed | offline_behavior |  |
| T060 | R12, R13 | FindFileByPath/GetSubFiles返回SHA1或缺Hash | 保留原始fileHashes；缺时按授权115查询补，禁止远端视频全量sha1sum。 | not_executed | real_cloud | Observe an actual CD2 FindFileByPath/GetSubFiles response lacking SHA1 inside the authorized test root, then confirm real 115 parent fallback and zero full remote video reads. |
| T061 | R11, R12, R38, R14 | 上传→Symedia改名/移动→Emby当前STRM最终核验 | 按目标身份+当前Hash/大小/附件绑定explicit；不只按时间/同名猜来源。 | passed | actual_host, real_cloud, real_consumer, real_downloader, real_emby |  |
| T062 | R11, R13 | 读STRM与提交关联之间目标被替换 | 复核版本/内容变化，重试；不把旧查询结果发布为当前。 | passed | offline_behavior |  |
| T063 | R12 | 同内容跨目录副本、CD2 id与115 id形式不同 | 位置各自记录，不把Hash当唯一云对象，不把大ID存JS Number。 | passed | offline_behavior, real_cloud |  |
| T064 | R21 | 候选A排除，B可用；所有候选被明确排除 | 选B或本轮为空；不恢复A，且保持其他插件已否决集合。 | passed | actual_host, offline_behavior |  |
| T065 | R21, R23, R45 | 筛选后用户新增排除，执行器准备恢复任务 | 最终核验排除、owner/generation和实际范围，拒绝过期计划，不依赖早先候选快照。 | passed | offline_behavior, real_downloader |  |
| T066 | R22, R19 | 排除并换源操作清理一半重启 | 顺序接续，不重复删/传；失败在途被撤销，旧有效版保留。 | passed | offline_behavior |  |
| T067 | R22, R20 | 被排除任务仍可能被MP再次整理 | 整理/监控清单守卫阻止反复回流；不默认删下载器源数据。 | passed | actual_host, real_downloader |  |
| T068 | R21, R22 | 整包只有E07失败，E08已入库 | 只排除/恢复E07及其资产，保留E08与共享文件引用。 | passed | actual_host, offline_behavior |  |
| T069 | R21, R22 | 排除某发布来源 vs 用户明确标当前版本无效 | 前者不删当前库；后者调整有效版本并计划替换，物理删除须另授权。 | passed | actual_browser, offline_behavior |  |
| T070 | R24, R25 | 创建开关关闭但已有任务到期，0不限，固定/空闲起算 | 期限仍工作，起算透明；扫描/改名/重评分不重置升级时钟。 | passed | offline_behavior |  |
| T071 | R24, R25 | 到期时仍缺集或目标季未闭合 | 只停止积极升级而不误终止应补内容；到期不伪造最高质量。 | passed | actual_host, offline_behavior |  |
| T072 | R26, R27 | 选择库中从未订阅的已有媒体出现更优资源 | 可建一次性目标；未选库不纳入，用户STOPPED优先。 | passed | real_emby |  |
| T073 | R27 | 老剧早已完结，RSS触发新机会 | 本轮独立执行期限，不因旧日期立即过期，也不另开长观察期。 | passed | actual_host, offline_behavior, real_cloud, real_consumer, real_downloader, real_emby |  |
| T074 | R27, R38, R48 | 一次性计划达到本轮改善但未到理论最高质量 | 入库核验后归档本轮并在目标保留升级冷却，不依赖原生100或重新开启长期窗口。 | passed | actual_host, real_cloud, real_consumer, real_downloader, real_emby |  |
| T075 | R27, R23, R45 | 重复RSS、多站重复、多个worker并发唤醒 | 同目标最多一份有效交付权；重复不生成新业务机会/重置预算；旧隔离收尾不拥有发布权。 | passed | integration, offline_behavior |  |
| T076 | R28 | 历史订阅已清理或旧限制与新策略冲突 | 用明确默认模板或报配置问题，不猜路径/下载器、不继承旧分。 | passed | actual_host |  |
| T077 | R23, R39, R47 | DB写入失败、lease过期但上传仍在运行 | DB失败不发危险操作；lease过期不释放UNKNOWN/HANDED_OFF屏障，先核实原进程和远端结果。 | passed | offline_behavior |  |
| T078 | R23, R45, R47 | 同季不同集并发、整包与单集重叠 | 不同集可并行；重叠目标认领/取代/发布串行，交付前能受控取代，交付中不被抢占。 | passed | offline_behavior |  |
| T079 | R33, R40 | 恶意路径、昂贵正则、含Token/Passkey文本、匿名API写请求 | 限制执行和路径，日志脱敏，正确鉴权；不调用eval/Shell/P115Center。 | passed | offline_behavior |  |
| T080 | R34 | 更改排序/规则，比较预演与实际决策 | 可解释差异及影响目标；策略缓存失效，离线结果不冒充实机。 | passed | actual_browser, offline_behavior |  |
| T081 | R35, R02, R63 | 旧增强助手、ChatGPTPlusUltra及豆瓣插件仍安装 | 只迁移/切换明确重叠功能；受管调度单一、内置AI不双响应、内置榜单不双跑；非冲突功能保留。 | not_executed | integration | The installed SubscribeAssistantEnhanced 0.7.9 schedules meta/common checks even with all overlapping feature switches off. Active audit-mode coexistence reached WAIT_OWNER under the unclassified-scheduled-plugin guard. Need a genuine non-overlapping scheduler mode or a source-backed safe classification before repeating the original cutover; do not bypass the owner guard. |
| T082 | R36 | 开发完成声明全功能可用 | R01–R65全量追踪代码和T001–T200证据，未执行项保持可见；不借旧插件130测试或旧包校验数字。 | passed | source_contract, whole_acceptance |  |
| T083 | R10, R23 | 约五万集初扫、增量、规则批量重算 | 分页可续跑、内存并发有界，不每轮RSS全库扫描或远端视频读取；报告真实指标。 | passed | capacity_measurement |  |
| T084 | R24, R28, R39 | 原生归档事件触发旧助手自动创建，或用户删除与插件归档相邻 | 区分操作意图，避免复活/误停止；委托壳归档失败可恢复且不伪分。 | passed | actual_host |  |
| T085 | R15, R41 | 完全关闭inotify，同时丢弃MP整理通知，监控目录留下稳定视频/字幕 | 周期覆盖全范围扫描发现并只建一个文件组；未使用任何事件仍推进本插件负责的步骤。 | passed | offline_behavior |  |
| T086 | R41 | 事件队列IN_Q_OVERFLOW且深层目录部分文件没收到通知 | 健康页显示降级、保存对账待办并扫描缺失范围；不只记告警或仅扫已知脏目录。 | passed | offline_behavior |  |
| T087 | R41 | 添加监听失败或限额不足，部分目录没有watch | 明确报告未监控范围；由完整对账接管，并核验重新注册/恢复，不能显示全部监听正常。 | passed | offline_behavior |  |
| T088 | R41 | 新建/整棵移动目录后立即写入字幕，监听注册竞态 | 补目录快照和后续完整对账捕获所有持续存在文件；不只等CLOSE_WRITE。 | passed | offline_behavior |  |
| T089 | R41, R23 | 插件离线期间新添多级剧集目录，恢复时没有重放旧事件 | 启动扫描恢复文件清单与身份，重复发现合并，不重复下载/上传。 | passed | integration |  |
| T090 | R41, R09 | 范围扫描中途权限错误、半途退出或深层目录mtime变化但根未变 | 保留检查点/失败范围，不将半快照作删除证明；下轮继续覆盖未收到事件的子树。 | passed | offline_behavior |  |
| T091 | R41, R09, R42 | 挂载暂时消失、断连后恢复，目录查询一度为空 | 区分服务错误/未完成查询与缺失，不批量重下；重新核验监听、映射及已知目标。 | passed | real_cloud, real_emby |  |
| T092 | R42, R18 | 插件直接115上传，cloudfs不产生任何本地CREATE | 通过本次upload/outbox主动核验对象、刷新目标父目录并查询，不等待文件系统事件。 | not_executed | real_cloud | CloudFS emitted two real CREATE events during the current-HEAD CD2/115 test upload, so the original no-CREATE input is false in this environment. A literal pass needs an actual no-CREATE environment or an explicit scope revision; the product no-event-input recovery is separately evidenced. |
| T093 | R42 | Symedia停机时CD2已刷新记录新增，恢复后再次刷新不重发事件 | 使用已核验的实际补扫/周期扫描恢复并记录证据，不靠重复刷新/改名/重传。 | passed | real_consumer |  |
| T094 | R42, R39 | 未找到或未验证Symedia自动补处理入口 | 显示WAIT_CONSUMER/BLOCKED_CONSUMER及缺口，不虚构接口/成功，不声称全事件丢失自动闭环通过。 | passed | offline_behavior |  |
| T095 | R42, R13, R38 | 同路径视频换内容，STRM文本和Item未产生新增事件 | 主动定向或低频档案对账查当前Hash/大小并正确关联；不因STRM没改跳过核验。 | passed | real_cloud, real_emby |  |
| T096 | R41, R42, R36 | inotify/MP广播/Emby Webhook全丢，服务恢复且消费者具备已验证补扫 | 持续存在的文件由本地对账、云端待办和目标查询完成；记录各层耗时和消费者恢复证据。 | passed | actual_host, real_cloud, real_consumer, real_downloader, real_emby |  |
| T097 | R43, R31 | 观察期先来1080，后到4K，再到4K HDR，分类允许这些版本 | 观察期不提前建下载，到期只选择当时最优且合格可用者；质量偏好不被等待成本改变。 | passed | offline_behavior |  |
| T098 | R43, R50 | 同一候选重复RSS、同质量跨站转载和做种数变化 | first_seen/last_quality_improved与最大截止不被重复更新；仍可刷新资源可用性。 | passed | offline_behavior |  |
| T099 | R43, R50 | 持续出现更优候选且每次发生在稳定窗口结束前 | D_observe不晚于首次合格候选+W_max；达到硬截止必重评估，不无限推迟。 | passed | integration |  |
| T100 | R43, R45 | E07观察成熟，E08尚未成熟；整季包覆盖两者 | 不得借E07绕过E08等待；可安全选集则只处理成熟目标，否则等待或换候选。 | passed | offline_behavior |  |
| T101 | R43, R50 | 发布时间陈旧/未来、观察中重启、定时器重新登记 | 按本地首见及持久化截止继续，不用pubdate伪造已等待时间，不从重启重新起算。 | passed | offline_behavior |  |
| T102 | R43, R47 | 明确目标提前满足或用户点击立即下载，但存在排除/旧未知发布 | 只能跳过观察/冷却，不越过准入、文件范围、授权或PUBLISH_OUTCOME_UNKNOWN。 | passed | actual_browser, offline_behavior |  |
| T103 | R43, R09 | 窗口到期时原最优被排除/链接不可用，或者所有候选失效 | 复核并选择剩余合格者或延期报原因；不因计时结束强制放行/放宽底线。 | passed | offline_behavior |  |
| T104 | R43, R50 | 观察关闭、W_base=0、quiet=0、W_base>W_max、无限/缺失W_max | 合法边界按合同执行；非法配置拒绝/提示，不把空值作无限或0；计时公式确定可重放。 | passed | offline_behavior |  |
| T105 | R44, R45 | 正常下载中出现更优资源，早期抢占开关关闭 | 继续有效计划，仅记待评估候选；不并发开始同目标另一份下载。 | passed | real_downloader |  |
| T106 | R44, R45 | 旧任务排队尚未开始，新候选被正式选定 | 先撤销旧执行许可并确认安全停止/隔离，再切换；不把停止等同于删数据。 | passed | real_downloader |  |
| T107 | R44, R50 | 早期抢占开启，两个旧任务百分比相同但实际字节/剩余时间不同 | 依据配置的改善与成本条件决定，记录理由；不以单一百分比作为质量排序或无限切换。 | passed | offline_behavior, real_downloader |  |
| T108 | R44, R09 | 任务处于人工暂停/排队/校验/限速/客户端失联 | 不全部认定死种或自动删换；实际失败和服务问题分域恢复，保留用户意图。 | passed | offline_behavior |  |
| T109 | R44, R32 | 整季总体60%但目标E07为0%或已完成，客户端部分统计缺失 | 使用本次文件清单进度；缺失字段标明未知，不伪造0或把整包ETA当单集ETA。 | passed | real_downloader |  |
| T110 | R44, R45, R49 | 同infohash中仅E07计划被取代，E08有效且仍在下载 | 只调整有授权的集合，保留E08/共享依赖；不删除整任务或所有源数据。 | passed | real_downloader |  |
| T111 | R45, R46 | A等待秒传，B合格并满足观察/切换条件 | 事务转移目标交付权、递增代次，A记SUPERSEDED；旧定时重试/普通上传回退/发布待办均失效。 | passed | offline_behavior |  |
| T112 | R46, R47 | 低规格A的已发秒传请求隔一天成功，B已确认入库 | 登记A旧暂存回执供核验/清理；不得发布A、覆盖B、更新当前版本或重置冷却。 | passed | offline_behavior, real_cloud |  |
| T113 | R46 | A已取代后，旧miss=N结果触发CD2回退待办或worker重放 | 在副作用前核验owner/generation，不创建旧普通上传或新增秒传；过期结果只收尾。 | passed | offline_behavior |  |
| T114 | R46, R16 | A视频字幕已核验齐套但仍在暂存，B随后获有效交付权 | A齐套不等于可发布；撤销交付并按授权保留/清理，不要求A先进入Symedia。 | passed | real_cloud |  |
| T115 | R45, R49 | RSS只发现标题更优的B，但身份/文件范围/配置核验失败 | B停在候选，A未被正式取代，不删除A监控副本或取消其有效工作。 | passed | offline_behavior |  |
| T116 | R46, R41 | A取代后重启，旧文件仍在监控目录或被MP再次整理 | 通过计划/文件血缘识别SUPERSEDED，不新建旧上传、不复活旧计时器；可显示旧成果待处理。 | passed | integration |  |
| T117 | R46, R49 | A普通上传仍请求本地读取/Hash，B取代后尝试清理A | 先收尾/停止并确认无未来读请求后才清理；未确认不删，旧远端结果只留在隔离暂存。 | passed | real_cloud |  |
| T118 | R45, R47, R17 | 同交付目录含E07/E08，只有E07被取代 | 不得整夹发布；可分离资产另建核验批次，E08继续、共享字幕不删；不能安全拆则阻止该交付。 | passed | offline_behavior, real_cloud |  |
| T119 | R45, R23 | 两条RSS/两个worker在DownloadAdded前同时尝试认领E07 | 调用下载器前已唯一占用；只有一份当前有效执行/交付权，不出现两个先查空后各自提交。 | passed | offline_behavior, real_downloader |  |
| T120 | R46, R21 | B失败后A资源仍合格；另一个场景A已被用户永久排除 | SUPERSEDED不等于拉黑，可通过新规划重用合格A但不复活旧定时器；永久排除仍不可绕过。 | passed | integration, offline_behavior |  |
| T121 | R47, R10 | A下载时更优，但等待期间实际库已升级到高于A的版本 | 发布时按当前事实重新比较，阻止A；不凭当初ALLOW继续移动。 | passed | offline_behavior, real_cloud |  |
| T122 | R45, R47 | A准备发布与B取代事务同时竞争 | 只有一种串行结果：A先PUBLISHING则B等待；B先获得权则A不发移动，不存在检查后仍发旧RPC空窗。 | passed | offline_behavior |  |
| T123 | R47, R23 | 移动RPC超时、lease到期，但可能已移动或稍后移动 | 保持PUBLISH_OUTCOME_UNKNOWN屏障，核对旧进程/远端操作；不得释放给B、重传A或盲目重发。 | passed | offline_behavior |  |
| T124 | R47, R23 | 旧worker失联后仍可能发移动，恢复worker准备接管 | 代次不能撤回远端请求；确认旧worker及原操作已收敛前禁止新发布，保留审计。 | passed | offline_behavior |  |
| T125 | R47, R42 | A已HANDED_OFF但Symedia延迟，Emby恰好已有另一个高版 | 不把看见高版或旧入口消失当A不会迟到的证据；先解决A的外部交付再允许B发布。 | passed | real_consumer |  |
| T126 | R48, R38 | 已提交/下载完成/秒传成功/CD2可见/最终入库依次发生 | 只有最终有效目标入库确认启动升级冷却；前面事件不提前起算，重复确认不续时。 | passed | offline_behavior |  |
| T127 | R48, R25 | E07处于升级冷却，E08缺失，E07需补字幕或确认当前版损坏 | E08正常补齐；字幕/修复依对应策略处理，不被普通质量CD全局阻塞，发布屏障仍有效。 | passed | offline_behavior |  |
| T128 | R48, R43 | 冷却中连续出现几个更优版本 | 只积累最佳合格候选，期满一次择优；不排队依次下载每个中间规格。 | passed | offline_behavior |  |
| T129 | R48, R27 | ONESHOT成功归档后重启，同资源再次推送 | 冷却保存在目标，不随任务删除/重启清零；不会重复建立等价升级任务。 | passed | offline_behavior |  |
| T130 | R48, R43, R50 | 观察与冷却重叠，观察先结束/后结束两种情况 | 通常按max(D_observe,cooldown_until)执行，不到期后叠加一轮观察；到期重新核验候选。 | passed | offline_behavior |  |
| T131 | R49, R19 | 取代清理已开启，A未上传成功但正式SUPERSEDED且无读取/共享 | 可仅删除登记监控副本；不要求等待A秒传，不触碰正式库或下载器源数据。 | passed | offline_behavior |  |
| T132 | R49, R20 | 监控清理开关打开，但下载器移除/删数据仍默认关闭 | 仅清监控目录项；不调用下载器删除任务/数据，不把softlink目标追删。 | passed | offline_behavior |  |
| T133 | R49, R20 | 用户明确开启下载器数据删除，存在专属任务与共享任务 | 仅在专属、完整授权且安全停止时执行；共享/其他有效集不支持安全分离则跳过并说明。 | passed | real_downloader |  |
| T134 | R49, R12 | 取代后源路径被B换新内容，或还有CD2读租约/共享字幕引用 | 旧清理动作不得删除新文件或在用资产；记录具体阻止原因，收尾后才重评估。 | passed | offline_behavior |  |
| T135 | R50, R45 | 本轮连续改选、创建多个plan、反复RSS和重启 | 取代次数/时间预算沿机会血缘保留，不能通过新plan刷新；失败重试预算独立。 | passed | offline_behavior |  |
| T136 | R50, R47 | 取代预算耗尽，但现行计划已低于当前有效库或必要字幕缺失 | 不强制交付低版/不齐套；选择仍可安全完成者或有界失败恢复/待处理，不放宽底线。 | passed | offline_behavior |  |
| T137 | R46, R48, R08 | A被取代后收到旧Emby核验、旧字幕explicit确认或旧上传成功事件 | 仅保留旧操作证据；不得把现版标成A、重复消费特效例外、续冷却或改新目标owner。 | passed | offline_behavior |  |
| T138 | R41, R46, R47, R48, R36 | 低版A等24小时，新版B完成；期间丢事件、重启并重放A旧回退/成功回调 | B保持当前有效版本；A不再发布/回退或复活；本地/暂存清理按权限，冷却与档案仍正确。 | passed | actual_host, offline_behavior, real_cloud, real_consumer, real_downloader, real_emby |  |
| T139 | R45, R46 | A旧秒传RPC长期未决但仅指向其独立暂存，B正式取代 | B可在不共享危险资源时继续，不让A的隔离收尾永久占交付权；A迟到结果可追溯且不发布。 | passed | offline_behavior |  |
| T140 | R49, R50 | 旧配置仅有监控成功清理=true，新增调度/删除字段未配置 | 不推导下载器删除=true，不伪造首见/入库时间；数值需明确，既有任务先对账而非全部重启。 | passed | offline_behavior |  |
| T141 | R46, R49, R39 | 旧上传没有可核验的停止读取接口或取消返回不可靠 | 不虚构已取消，不删除源；保留旧收尾和隔离结果，明确能力边界并只在安全条件下推进B。 | passed | real_cloud |  |
| T142 | R49, R18 | 未命中次数耗尽但未取代/放弃，与正式取代清理开启的任务并存 | 前者保留成果待处理，后者按权限清理；不按年龄或统一FAILED状态误删。 | passed | offline_behavior |  |
| T143 | R51 | subscriBetter名称/主类/目录/索引/测试位置 | SubscriBetter、subscribetter、plugins.v3、package.v3.json一致；真实V3加载；不新建V2实现。 | passed | actual_host, source_contract |  |
| T144 | R51, R52, R56 | 扫描旧导入、宿主Model/Session和内部模块 | 公开SDK/Oper；仅Meta兼容层有两个明确内部访问例外，不能借例外扩到owner或全局download。 | passed | source_contract |  |
| T145 | R53 | 同数字来自TMDB、豆瓣和动态插件来源 | 身份键包含source+id，字符串保留；无效0/空/None不作合法来源ID。 | passed | actual_host, offline_behavior, source_contract |  |
| T146 | R53 | 分类改名、换路径但category_id不变 | 策略仍绑定正确类别，已冻结计划保留路径快照，不把新类别名称当新身份。 | passed | actual_host |  |
| T147 | R53 | 旧category.yaml迁入已存在V3发布策略 | 仅比对/映射，不覆盖当前revision或继续写YAML；保留旧路由意图和明确兜底。 | passed | offline_behavior |  |
| T148 | R52, R54 | 连续两个宿主Oper写入第二个失败 | 有持久化回执/幂等补偿，不假定一起回滚、不自行打开宿主Session拼事务。 | passed | offline_behavior |  |
| T149 | R52, R64 | 插件SQLite与宿主数据库/两运行实例并存 | 自有数据与宿主分离；实例共享外部范围不能双拥有，Meta补丁单所有者。 | passed | actual_host |  |
| T150 | R51, R64 | 重复保存配置、停用、重启加载 | 没有重复cron、未释放client或叠加补丁；旧runtime回调不得写新配置代次。 | passed | actual_host |  |
| T151 | R52 | input/output快照、可变event和同步否决差异 | 使用真实V3支持的输出路径；不因修改局部快照就假定宿主采纳。 | passed | actual_host, source_contract |  |
| T152 | R52, R64 | 普通JSON/文件响应与匿名修改请求 | 正确处理V3响应，修改需授权；UI不依赖旧V2响应包装，不匿名触发动作。 | passed | actual_browser, actual_host |  |
| T153 | R51, R60 | 旧httpx实现与V3共享httpx2/核心依赖 | pyproject声明必要依赖；无全局alias/降级/运行时pip，不把一种库异常当另一种。 | passed | offline_behavior |  |
| T154 | R54, R32 | V2选择附件/零集/同名复用风险重放 | 记录V3真实行为；已修复部分复用，缺口有窄适配；不把旧风险文字当当前bug证据。 | passed | actual_host, real_downloader |  |
| T155 | R54 | download_single有内部governance参数 | 公共门面可用且取消检查生效；内部类型不自动当SDK出口，不继承私有owner。 | passed | actual_host, source_contract |  |
| T156 | R51, R54 | DDS V3 p115disk/clouddrivedisk与缺同名helper目录 | 明确真实参考路径与API；未找到的V3 helper不臆造，旧算法移植按当前合同测。 | passed | offline_behavior |  |
| T157 | R55 | GATE24/CODE46被数字季集模式击中 | 对正反上下文纠正名称/type/范围；不将正则片段结果冒充完整原生输出。 | passed | actual_host, offline_behavior |  |
| T158 | R55, R60 | 1917.2019.1080p与只有数字片名 | 标题数字保留；年份必须有独立文本依据，不把1917变成发行年。 | passed | offline_behavior |  |
| T159 | R55, R57 | 两三位裸数字既可能为片名也可能是简写集号 | 有语境才推断，明确季集优先；不删除全部裸数字支持，不无据改变类型。 | passed | offline_behavior |  |
| T160 | R55, R60 | 前置括号分别为片名、组、字幕、规格 | 按内容角色提取，不统一剥括号、不把HHWEB当作品名。 | passed | offline_behavior |  |
| T161 | R55, R59 | 污点独立别名含The Movie vs真实Steins;Gate电影版 | 独立中文别名不强添英文标记，真实电影版不缩系列；保留所选名称来源证据。 | passed | offline_behavior |  |
| T162 | R55, R57 | 用户显式s/e范围、S00、None与自定义词偏移 | 显式优先，0不等于未知；更正不可重复应用偏移或覆盖用户ID。 | passed | actual_host, source_contract |  |
| T163 | R55 | 电影误解析TV后修正片名 | 关联type/begin/end/total等同步自洽；不能只改显示名称留下虚假集号。 | passed | offline_behavior |  |
| T164 | R55 | 名称纠错但分辨率/编码/HDR/发布组原来正确 | 未有证据需要修的技术属性逐字段保持，质量顺序不被纠错顺便改掉。 | passed | offline_behavior |  |
| T165 | R55, R57 | 同一正反样本经两个解析路径 | 稳定语义一致，差异解释；不能只运行Python就宣称Rust覆盖。 | passed | actual_host |  |
| T166 | R57 | Rust parsed缺custom_words/显式锁来源 | 不能臆造上下文；跳过潜在冲突修复并记录，受管高风险候选仍不放行。 | passed | offline_behavior, source_contract |  |
| T167 | R57, R64 | 音乐分支与影视外挂音轨force_video | 不污染音乐解析或扩音乐自动化；force_video正确路径保持。 | passed | actual_host, offline_behavior |  |
| T168 | R56 | 宿主模块预先from...import MetaInfo再启用包装 | 通过内部helper实际生效；不是只给外层符号重新赋值而误称覆盖。 | passed | offline_behavior |  |
| T169 | R56, R64 | 多次启用/第三方随后包装/第二分身安装 | 本包装不叠加，不撤销别人；必要时停自身逻辑并告警，只有一个所有者。 | passed | actual_host, offline_behavior |  |
| T170 | R56 | 纠错中途异常、共享原Meta被其他调用持有 | 副本失败不污染原对象；返回原结果，风险候选由独立准入拒绝/等待。 | passed | offline_behavior |  |
| T171 | R56, R39 | MP升级改函数签名或结果结构 | 停止该增强并清晰显示不兼容，不改宿主磁盘、不静默扩大补丁点。 | passed | offline_behavior |  |
| T172 | R57 | 文件/父目录/祖目录继承在纠错之后merge | 验证最终Meta而非中间对象；覆盖不足报告并保守处理，不称两helper解决一切。 | passed | actual_host |  |
| T173 | R57, R65 | 其他插件直接构造MetaVideo绕过公共入口 | 如实列出不覆盖路径；不扫描全进程强换所有类来扩大授权。 | passed | source_contract |  |
| T174 | R58 | 旧RSS已缓存错误身份，规则revision更新 | 从原文定向重放受影响项；宿主缓存仅公开授权入口；不清全历史。 | passed | integration, offline_behavior |  |
| T175 | R58, R48 | 重解析时目标在观察/冷却或发布UNKNOWN | 仅更新候选/诊断，不重置时钟和预算，不取消未知发布或重建下载。 | passed | offline_behavior |  |
| T176 | R59, R60 | JSON含多字段/null/数字year/重复键/指令 | 严格两字符串name/year校验，标题是数据，拒绝不禁key、不执行内容。 | passed | offline_behavior |  |
| T177 | R55, R59 | 普通清晰标题 vs原生非空但明显冲突 | 前者无额外AI；后者可诊断有界辅助；不因非空盲信，不把全部标题先送模型。 | passed | integration |  |
| T178 | R59, R60 | 同输入并发与不同画质/上下文请求 | 同语义精确键合并，不激进去词制造身份缓存碰撞；结果和统计分别正确。 | passed | offline_behavior |  |
| T179 | R60 | 401/429/Retry-After/超时/结构拒绝 | 仅明确401有限轮换；429不绕限，冷却不缩短；拒绝非服务认证错误。 | passed | offline_behavior |  |
| T180 | R60, R64 | HTTP未结束时换配置/清cache/停用 | 旧结果不能填新缓存、覆盖event或重复计量；结束后释放客户端。 | passed | actual_host, offline_behavior |  |
| T181 | R60, R33 | 标题含token URL/换行/伪system指令 | 链接与凭据脱敏、单行日志；保留必要普通片名用于诊断，不能泄露密钥或运行指令。 | passed | offline_behavior |  |
| T182 | R60, R63 | 旧默认prompt与用户自定义prompt/API/model不同 | 只迁已知模板，备份原文；不改自定义或服务配置，旧动作旗标不自动执行。 | passed | offline_behavior |  |
| T183 | R59, R64 | 新安装、旧启用聊天、多个渠道同用户与清会话 | 新默认关闭；迁移预览，隔离历史、单回复；聊天无订阅/下载/删除执行权。 | scope_removed | integration | 独立聊天已移除 |
| T184 | R61, R63 | 8个rank键、@@TYPE、分号/#保存目录组合 | 逐项映射保留；缺_douban_address映射不伪造可用地址，冲突配置报错。 | passed | offline_behavior |  |
| T185 | R61, R53 | 识别源评分不等于豆瓣分、年份/评分缺失 | 记录provider与阈值；未知不伪低分永久忽略，不悄悄更换评分来源。 | passed | offline_behavior |  |
| T186 | R61, R05 | all_seasons与full_pack、季元数据不连续/未播/S00 | 区分两个功能，按可信季/范围建立目标，不盲生成不存在季度。 | passed | integration |  |
| T187 | R62 | 统一入口失败或回执丢失，旧源add返回不检查 | 只明确回执标成功；失败保留可重试，回执丢失先核对幂等意图不重复建。 | passed | offline_behavior |  |
| T188 | R62, R26 | 相同作品多榜上榜且用户STOPPED | 合并可信同一目标，保留多来源关系；STOPPED不被榜单/历史清理绕过。 | passed | offline_behavior |  |
| T189 | R62, R26 | 榜单项已在未选被动库或已在受管库 | 只按明确existing_media_action与授权处理；不因上榜自动扩大升级范围。 | passed | real_emby |  |
| T190 | R62, R58 | 旧未识别/低评分历史后出现新证据或策略变化 | 保留原因且有界再评估，非永久成功去重；不一次全表重建。 | passed | offline_behavior |  |
| T191 | R61, R39 | XML破损、链接异常、无效类型/超时/限流 | 未成功不是空榜，单源失败隔离，限制资源消耗，无解析注入副作用。 | passed | offline_behavior |  |
| T192 | R55, R61 | @@TV的24/模范出租车3/电影死侍2 | 类型提示不证明末尾数字为季；候选别名需附加证据，电影续作数字保留。 | passed | offline_behavior |  |
| T193 | R61, R64 | cron/立即一次/随机间隔/代理/限流配置 | 测试行为真实生效，导入不重放onlyonce；未使用旧死配置需在迁移报告解释。 | passed | actual_host |  |
| T194 | R61, R65 | 识别通过、意图接受、下载接受、完成交付、入库五阶段 | 统计明确分母和状态，不把任一阶段冒充最终成功率。 | passed | actual_browser |  |
| T195 | R63, R33 | 重复导入两个旧插件配置历史/携带机密 | 回执去重、秘密安全引用、原备份保留；包/日志/普通导出不含明文key。 | passed | offline_behavior |  |
| T196 | R63, R28 | 旧unique、tmdbid=0/None与伪成功记录 | 不产生虚假身份，核对当前任务/库后链接；不全部忽略也不批量重下。 | passed | offline_behavior |  |
| T197 | R63, R64 | 新导入后停旧AI/榜单步骤失败或重载 | 单一所有者门阻止双跑，中断可续；不删旧配置，不用并行新旧作为fallback。 | passed | offline_behavior |  |
| T198 | R63, R64 | GET删除历史旧接口与query token迁移日志 | 新写操作授权且非GET；旧只读远端迁移允许协议适配但脱敏并限制目标主机。 | passed | offline_behavior |  |
| T199 | R36, R55, R59, R61 | 榜单→Meta纠错→可选AI→目标调度→下载/115→CD2/Symedia→Emby | 一套目标/版本/证据/回执串联，无重复原生best_version=0任务；保存真实各阶段证据。 | passed | actual_host, real_cloud, real_consumer, real_downloader, real_emby |  |
| T200 | R65, R56, R59 | 宣称整合完成时核查原50项和新模块边界 | 原142项保留，新增有真实结果；无全能身份引擎、无热路径LLM、无额外全局补丁，不用历史测试数冒充。 | not_executed | whole_acceptance | User approved bounded exceptions for T003/T006/T060 on 2026-09-25. Their original external triggers remain unobserved; keep them open and exclude them from any literal all-cases-passed claim.; Current commits fix independently reproduced cancellation, physical identity, synchronous search and passive RSS deadline defects. Exact-commit regression, isolated staging, selected real-service branches and synthetic Linux deadline probes are recorded; complete the remaining whole-branch review and current-commit end-to-end real-service revalidation before T200 closure. |
