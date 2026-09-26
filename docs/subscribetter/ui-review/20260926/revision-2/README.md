# 原图复核后的 UI 整改复核包

状态：已实现、已部署隔离 V3，交付用户与外部 agent 复核；**没有自行判定视觉设计合格，也不替代下载到入库的端到端验收**。

## 版本与阅读入口

- 本轮前的 GitHub 基线：`4fae4957aeee042e9e7bd136b368c010d712926d`。
- 最终部署代码：`b4c7a3010c00d5d99ecc4b02f3cbab2b30d9ed7e`。代码、测试、生成的 dist 都在仓库中。
- [逐步操作说明](USER-JOURNEYS.md)、[原图索引](GALLERY.md)、[截图原始清单](capture-metadata.json)、[文件哈希清单](manifest.json)。
- [部署与配置前后摘要](deployment.json)、[真实跨页分集顺序](episode-order.json)、[真实映射检查结果](mapping-result.txt)。
- [前一版 50 张原图](../full-audit/)保持冻结，不能与本目录的新截图混作同一版本。
- [前端源码](../../../../../plugins.v3/subscribetter/frontend/src/)、[后端展示与管理接口](../../../../../plugins.v3/subscribetter/ui.py)、[可复现合成预览](../../../../../plugins.v3/subscribetter/frontend/dev/preview.mjs)。

所有材料可从本分支 GitHub 直接阅读或克隆，不需要 NAS、fnos.txt、本地绝对路径或已登录会话。截图中的测试目录与媒体名称用于判断布局；无登录密钥。GitHub 源码链接以本目录层级为准。

## 复核意见如何落实

| 复核重点 | 本次改变 | 可复核位置 |
|---|---|---|
| 列表挤压详情 | 订阅、传输切换全宽详情；返回保留原筛选和列表 DOM，恢复滚动与触发按钮焦点 | Subscriptions.vue、Transfers.vue、panel.mjs；host/02、19 |
| 首屏层级过多 | 紧凑固定标题、模式条、导航；正文独立滚动；分集/候选/记录分页签 | Page.vue、Config.vue、style.css；host/01、02 |
| 当前版本与目标混在一起 | 服务端只读投影最近在库事实及所属计划阶段/文件，UI 并列展示，不自行评分 | ui.py task/_unit；fixtures/02、04 |
| “已知”误充整季总数 | 继续使用已知目标数量；未知时明确显示未知；历史 due 不写成未来承诺 | media.mjs；fixtures/06；host/18 |
| 分集字典序 | SQL 在 LIMIT/OFFSET 前按数值季、集排序 | ui.py；episode-order.json；host/02、03 |
| 来源测试变对象编辑 | 已保存来源原位测试，仅传 source_id，显示抓取数量或失败原因 | Discovery.vue；host/29 |
| 站点 ID 与预算抢首屏 | 从 MP 读取站点名称，保留不可用的已保存选择；预算进入高级 | SitePicker.vue、OperationalSettings.vue；host/09、23 |
| 调度 null/0/开关混乱 | 下载前观察、在途替换、入库后冷却分组；0 禁止替换，空值仍为空；秒传时钟独立 | ScheduleSettings.vue、Quantity.vue；host/10 |
| AI 要用户搬运引用 | 同页私密写入端点/密钥、自动接入草稿；模型与连接状态可见；提示词全宽、备份只读恢复 | AISettings.vue；host/15、16 |
| 长路径与方案割裂 | 四类路径全宽；方案按选择显示关联；原稳定 ID 不可误改；真实档案原位检查映射 | DeliverySettings.vue、MappingCheck.vue；host/24、25、27 |
| 控件不一致 | 主提交用 primary，返回/关闭中性；复选框紧邻标签；时长容量带单位；保留键盘焦点 | Action.vue、Quantity.vue、style.css |
| 活跃态没有代表数据 | 独立本地合成场景，始终有横幅，不连接 NAS，不允许保存/执行 | fixtures/ 与 dev/preview.mjs |

原位抓取实测：当前保存的“一周口碑电影榜”返回 1 条，未识别或创建订阅；三个历史来源的测试按钮均禁用。见 [抓取结果](source-test.txt)。实机首次暴露历史来源误作当前来源的问题后已修正并回归。

原权限、预检回执、幂等、所有权、未知结果保护、计划取代及防降级判定继续由后端负责。普通新增订阅继续使用 MP 原生入口。本轮没有新增前端决策器、独立聊天入口或通用媒体订阅接口。

## 证据与限制

- 前端 `npm test --prefix plugins.v3/subscribetter/frontend`：45 项通过，0 跳过。
- Python：183 项完成，182 通过、1 跳过。命令见下；跳过项为 Windows 下不能执行的 POSIX 凭据文件系统权限检查。不是 Linux 权限验证通过。
- Vite build：97 modules，成功。沙箱中首次构建遇到 esbuild spawn EPERM，使用获批构建权限后成功；没有修改测试标准。
- 已部署隔离 V3；161 个暂存文件哈希验证通过，安装 HTTP 200。前后 revision=271，配置 digest 完全一致；enabled=false、dry_run=true、ordinary_work_active=false、generation=1、errors=[]。
- 实机《一瓯春》跨页确认 1–25、26–30，筛选“一瓯春”后进入并返回详情，查询值保留，焦点回到作品行；这是已知目标的顺序验证。
- 实机从已保存电影映射选择《求救信号》扫描样本，返回 MAPPING_VERIFIED 并显示三端路径。只检查实际 STRM 与路径转换，不宣称 CD2 云端存在性或播放成功。
- AI 在隔离实例关闭；真实模型连接按钮禁用。本轮没有新写密钥、启用 AI 或发真实模型请求。组件测试覆盖部分私密写入失败及旧引用保留，不能冒充私密存储实机写入验收。
- 合成活跃态不是 NAS 的真实下载任务，不证明秒传、缺字幕恢复、替换或发布对账本轮实际成功。
- 本轮不保存生产或隔离运行配置，不运行普通下载/交付/清理，不新增纳管。保存错误、并发版本与不可变回执主要由现有组件/Python检查覆盖。
- 配置导航逐一使用键盘激活并核对标题；浏览器自动化的 locator 鼠标点击存在坐标偏移，不能把这一路径记录为鼠标验收通过。窄屏原生选择器另有实际操作记录。
- 截图是浏览器接口直接返回的 JPEG 原字节，未裁剪、缩放、拼接或修图。CSS 视口与图片像素分别记录；四张后补原图的捕获元数据未写入，只记录文件修改时间并将 CSS 尺寸标为未知；不要用 JPEG 像素反推 CSS 字号。390px 测试是桌面浏览器窄视口，不是真实手机触摸测试。
- host/11 是发现摘要过长时的中间截图，最终布局请看 host/24；host/17 是首轮映射成功，host/27 补录最终扫描时间标签。其余未涉及的页面相同。

Python 复现（仓库根目录，Windows PowerShell）：

```powershell
$env:PYTHONPATH='tests/v3/subscribetter'
python -m unittest test_management test_management_spec_fix test_management_reload_fix test_management_quality_fix test_native_ui test_configuration test_ai test_archive -q
npm ci --prefix plugins.v3/subscribetter/frontend
npm test --prefix plugins.v3/subscribetter/frontend
npm run build --prefix plugins.v3/subscribetter/frontend
```

本地 UI 复现：

```powershell
npm run dev --prefix plugins.v3/subscribetter/frontend
```

打开 `http://127.0.0.1:4179/dev/index.html`。使用真实 Vue 组件和合成 API 数据；不得把 fixture 当作生产档案。

## 请外部审核者重点检查

1. 对照旧包与本包，判断全宽详情、头部、滚动、常用表单是否真的降低操作成本；不要以“测试通过”推定美观合格。
2. 检查未知状态是否仍被误包装成失败或成功；最近档案与在途目标是否分明。
3. 核查 `Quantity` 的无损单位切换与 null/0；AI 部分私密写入失败、卸载、配置版本变化时的保护。
4. 核查 mapping-test 的管理员、版本、扫描样本、映射、路径边界；精确路径只在这个已验证结果投影中返回，普通安全投影仍遮蔽路径。
5. 真实写入、实际 AI 调用、触屏以及完整业务链路单独安排验证。本包不为这些项目放行。

本轮启动的两个本地预览服务在截图后已停止；需要重现时运行上述 npm 命令。隔离 V3 的管理页保留为实际复核入口。
