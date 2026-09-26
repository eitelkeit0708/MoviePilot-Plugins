# 紧凑媒体管理界面

本轮基于 `5140831`，落实用户对空间、层级、常用配置与状态表达的整体修订。它是界面验收候选，不修改冻结的 `external-review/` 或历史业务验收结论。

## 可检查的变化

| 页面 | 当前呈现 | 数据依据与边界 |
|---|---|---|
| 订阅 | 作品、最近档案与版本、当前处理、下一步横向排列；桌面作品行最低 88px | 当前生命周期范围内的目标汇总。档案来自上次扫描或入库记录，处理前仍需核实，不承诺实时文件可用性；范围未知时不编造总集数 |
| 发现 | 默认显示作品和处理结果，来源作为筛选，状态折叠 | 来源名称取保存的名称或内置榜单名称；地址放详情。成功来源不重复展示旧错误 |
| 策略 | 左侧分类，右侧允许范围、实际比较顺序和预演 | 后端 `Policy.describe()` 使用保存的模板及同一个 `rank()` 生成展示，不在前端维护第二份排序；预演仍针对已保存策略 |
| 传输 | 作品与文件、当前状态、下一次检查分列 | 尚未确认与失败分别表达；原始原因及回执保留在详情 |
| 常用配置 | 运行开关置顶、电影/电视剧勾选、媒体库名称勾选、完整片名标签、紧凑下载方案 | 从宿主读取 Emby 服务与媒体库；失联保留选择并可重试，不要求编辑媒体库数字 ID |
| 清理权限 | 按成功、暂存、放弃、下载器分别组织 | 权限及实际执行守卫没有变化 |

正文使用插件范围内的实色宿主主题表面，沿用宿主字体和强调色。控件规格、焦点、按钮层级和窄屏重排统一。重复抬头和解释性面板缩减，复杂规则仍可在高级详情编辑。新增订阅仍使用 MoviePilot 原生入口。

## 实现定位

- [Config.vue](../../../../plugins.v3/subscribetter/frontend/src/Config.vue)、[BasicSettings.vue](../../../../plugins.v3/subscribetter/frontend/src/BasicSettings.vue)、[LibraryPicker.vue](../../../../plugins.v3/subscribetter/frontend/src/LibraryPicker.vue)：常用表单与媒体库读取失败保留。
- [Subscriptions.vue](../../../../plugins.v3/subscribetter/frontend/src/Subscriptions.vue)、[Discovery.vue](../../../../plugins.v3/subscribetter/frontend/src/Discovery.vue)、[PolicyOverview.vue](../../../../plugins.v3/subscribetter/frontend/src/PolicyOverview.vue)、[Transfers.vue](../../../../plugins.v3/subscribetter/frontend/src/Transfers.vue)：业务页面。
- [ui.py](../../../../plugins.v3/subscribetter/ui.py)：只读目标汇总；[policy.py](../../../../plugins.v3/subscribetter/policy.py)：真实比较规则说明；[style.css](../../../../plugins.v3/subscribetter/frontend/src/style.css)：仅作用于插件的样式。

`SourceConfig.name` 是新增的可选展示名称（最多 80 字符）。旧来源未命名时会补默认空值；不改变来源地址、媒体类型或操作权限。没有新增依赖，没有改变下载、删除、发布回执和执行权规则。

## 验证与限制

前端 38 项通过，生产构建 90 个模块成功。最终 Python 回归结果、浏览器布局数据与部署证据见 [compact-verification.json](compact-verification.json)。新增检查覆盖真实质量记录结构、当前生命周期范围、列表与详情一致、只读查询不写库、禁用或过期观察不作为下一步，以及媒体库失联保留和重试恢复。

一次完整 Python 回归运行 728 项，出现 1 项 `TICK_DEADLINE` 错误、5 项跳过：原有 `test_cleanup_deadline_blocks_files_and_remove_after_slow_task` 在模拟慢请求开始之前，已超过测试设定的 100 毫秒窗口。未修改测试或放宽阈值；随后该文件 7 项单独通过。完整复跑单独记录，保留这次失败，不用重跑覆盖历史结果。

本地合成数据预览在 1440、1024、390 像素宽度检查了五个页面，无横向溢出。390 像素检查全部 11 个配置分组，只显示选中组；键盘回车添加片名及分组切换保留草稿正常。深色表面为 `rgb(33,33,33)`，浅色为 `rgb(255,255,255)`，桌面订阅行实测 88px。

独立代码复核发现并修复：质量字段读取错误、历史范围混入进度、禁用/过期观察误作下一步、服务重试未更新目录、来源分页导致作品名称缺失。再次复核修正详情范围与历史记录文案，无已知遗留复核项。

**以上浏览器证据仅为 DOM、尺寸及交互检查。** 截图接口持续失败，MP 浏览器会话未登录，因此未通过 MP 实机明暗截图、完整焦点循环或视觉对比度验收。合成数据不能证明真实下载、云盘交付或媒体库入库成功。

## 复现

```powershell
python -m unittest discover -s tests/v3/subscribetter -q
npm --prefix plugins.v3/subscribetter/frontend test
npm --prefix plugins.v3/subscribetter/frontend run build
```

使用仓库已有 Python 测试环境与前端锁定依赖。部署仅针对既有隔离 V3；正式提交和资源哈希见验证记录，生产容器不在本轮更新范围内。
