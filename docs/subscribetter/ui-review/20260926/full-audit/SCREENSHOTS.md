# 全部真实宿主截图

[返回审计结论](README.md) · [用户操作步骤](USER-WALKTHROUGH.md)

共 50 张，来自本轮真实隔离 MP V3。所有图片都是实际视口，未裁剪、拼接或修改；长页通过不同位置取证，不能据一张图推定整页。配置和业务数据均为既有历史测试状态。未展示的提交结果不能算已验证。

每张原图均已保存后读取检查。浏览器 CSS 视口与实际 JPEG 尺寸分开记在 [清单](capture-manifest.json)；没有对图片重采样来统一尺寸。截图接口实际返回 JPEG，初始临时扩展名为 .png，归档时按文件头纠正为 .jpg，文件内容未改变。

| 图号 | 内容 |
|---|---|
| 01 | [订阅列表：默认状态](screenshots/01-subscriptions.jpg) |
| 02 | [订阅详情：一瓯春及分集上段](screenshots/02-subscription-detail-top.jpg) |
| 03 | [订阅详情：长页底部与候选依据](screenshots/03-subscription-detail-bottom.jpg) |
| 04 | [暂停追踪确认框（未提交）](screenshots/04-pause-confirmation.jpg) |
| 05 | [从 MP 纳管：原生订阅分页列表](screenshots/05-adopt-existing-list.jpg) |
| 06 | [纳管确认：方案选择与演练限制](screenshots/06-adopt-confirm.jpg) |
| 07 | [发现：作品结果优先显示](screenshots/07-discovery.jpg) |
| 08 | [发现：展开来源状态](screenshots/08-discovery-sources.jpg) |
| 09 | [发现：已选来源的测试抓取表单](screenshots/09-discovery-test-dialog.jpg) |
| 10 | [传输与待处理：全部交付](screenshots/10-transfers.jpg) |
| 11 | [交付详情：历史未知结果与文件列表](screenshots/11-transfer-detail.jpg) |
| 12 | [交付核对：准备操作界面](screenshots/12-transfer-review.jpg) |
| 13 | [策略：分类与真实规则摘要](screenshots/13-policy.jpg) |
| 14 | [策略：已有候选的确定性预演](screenshots/14-policy-preview.jpg) |
| 15 | [设置概览：八个入口与服务摘要](screenshots/15-settings-overview.jpg) |
| 16 | [基本设置：总开关、演练、自动类型](screenshots/16-config-basic-top.jpg) |
| 17 | [基本设置：实际媒体库名称与保护片名](screenshots/17-config-library-names.jpg) |
| 18 | [下载与入库方案编辑器](screenshots/18-config-destination-editor.jpg) |
| 19 | [独立策略：分类绑定](screenshots/19-config-policy-bindings.jpg) |
| 20 | [独立策略：模板编辑](screenshots/20-config-policy-editor.jpg) |
| 21 | [生命周期](screenshots/21-config-lifecycle.jpg) |
| 22 | [站点与搜索：默认字段](screenshots/22-config-candidates.jpg) |
| 23 | [站点与搜索：高级站点 ID 列表](screenshots/23-config-site-ids.jpg) |
| 24 | [观察、冷却、抢占：继承控件](screenshots/24-config-schedule.jpg) |
| 25 | [交付与映射：分步配置入口](screenshots/25-config-delivery.jpg) |
| 26 | [云盘范围与服务选择](screenshots/26-config-cloud-scope.jpg) |
| 27 | [媒体库与两段路径映射编辑器](screenshots/27-config-mapping-editor.jpg) |
| 28 | [本地到云端与整理入口的交付规则](screenshots/28-config-delivery-rule.jpg) |
| 29 | [扫描与恢复](screenshots/29-config-recovery.jpg) |
| 30 | [清理权限：五类授权开关](screenshots/30-config-cleanup.jpg) |
| 31 | [安全与容量](screenshots/31-config-safety.jpg) |
| 32 | [榜单与 RSS：自部署 RSSHub 与内置来源](screenshots/32-config-rsshub.jpg) |
| 33 | [具体来源：名称、过滤与方案绑定](screenshots/33-config-source-editor.jpg) |
| 34 | [AI 名称辅助：启用、模型和端点引用](screenshots/34-config-ai-top.jpg) |
| 35 | [AI 名称辅助：提示词与备用配置](screenshots/35-config-ai-prompt.jpg) |
| 36 | [高级诊断：目标与任务](screenshots/36-advanced-tasks.jpg) |
| 37 | [高级诊断：候选决策](screenshots/37-advanced-candidates.jpg) |
| 38 | [高级诊断：版本档案](screenshots/38-advanced-archive.jpg) |
| 39 | [高级诊断：交付队列](screenshots/39-advanced-delivery.jpg) |
| 40 | [高级诊断：策略与预演](screenshots/40-advanced-policy.jpg) |
| 41 | [高级诊断：服务与健康](screenshots/41-advanced-health.jpg) |
| 42 | [高级诊断：榜单发现](screenshots/42-advanced-discovery.jpg) |
| 43 | [高级诊断：解析与 AI](screenshots/43-advanced-ai.jpg) |
| 44 | [私密凭据写入：空白表单](screenshots/44-private-input.jpg) |
| 45 | [高级诊断：整合迁移](screenshots/45-advanced-migration.jpg) |
| 46 | [迁移：离线导入与唯一处理者表单](screenshots/46-migration-forms.jpg) |
| 47 | [MoviePilot 原生目录选择器](screenshots/47-native-directory-picker.jpg) |
| 48 | [AI 名称辅助：密钥引用列表](screenshots/48-config-ai-references.jpg) |
| 49 | [榜单与 RSS：自定义地址/路由入口](screenshots/49-config-custom-route.jpg) |
| 50 | [MoviePilot 宿主插件卡片菜单](screenshots/50-host-entry.jpg) |

## 01 · 订阅列表：默认状态

实际页面展示；本轮未保存配置或提交业务变更。

![01 订阅列表：默认状态](screenshots/01-subscriptions.jpg)


## 02 · 订阅详情：一瓯春及分集上段

实际页面展示；本轮未保存配置或提交业务变更。

![02 订阅详情：一瓯春及分集上段](screenshots/02-subscription-detail-top.jpg)


## 03 · 订阅详情：长页底部与候选依据

最终保存的是重新滚动后截取的候选依据下段；清单只保留该文件的最后一次采集时间。左侧大面积留白属于实际页面。完整分集次序另见 episode-order-observation.json。

![03 订阅详情：长页底部与候选依据](screenshots/03-subscription-detail-bottom.jpg)


## 04 · 暂停追踪确认框（未提交）

仅打开确认框；没有暂停任务。

![04 暂停追踪确认框（未提交）](screenshots/04-pause-confirmation.jpg)


## 05 · 从 MP 纳管：原生订阅分页列表

当前实际原生订阅列表；没有提交纳管。

![05 从 MP 纳管：原生订阅分页列表](screenshots/05-adopt-existing-list.jpg)


## 06 · 纳管确认：方案选择与演练限制

演练模式下按钮不可提交；不是成功纳管截图。

![06 纳管确认：方案选择与演练限制](screenshots/06-adopt-confirm.jpg)


## 07 · 发现：作品结果优先显示

实际页面展示；本轮未保存配置或提交业务变更。

![07 发现：作品结果优先显示](screenshots/07-discovery.jpg)


## 08 · 发现：展开来源状态

实际页面展示；本轮未保存配置或提交业务变更。

![08 发现：展开来源状态](screenshots/08-discovery-sources.jpg)


## 09 · 发现：已选来源的测试抓取表单

来源已选定后仍出现 source 对象编辑。没有发送测试抓取请求。

![09 发现：已选来源的测试抓取表单](screenshots/09-discovery-test-dialog.jpg)


## 10 · 传输与待处理：全部交付

显示历史测试数据；“下一次检查”的旧日期不证明本轮产生了调度故障。

![10 传输与待处理：全部交付](screenshots/10-transfers.jpg)


## 11 · 交付详情：历史未知结果与文件列表

历史测试对象；旧 IDX/SUB、字体、许可附件不重新计入处理要求。

![11 交付详情：历史未知结果与文件列表](screenshots/11-transfer-detail.jpg)


## 12 · 交付核对：准备操作界面

仅打开操作核对，没有发送最终对账请求。

![12 交付核对：准备操作界面](screenshots/12-transfer-review.jpg)


## 13 · 策略：分类与真实规则摘要

实际页面展示；本轮未保存配置或提交业务变更。

![13 策略：分类与真实规则摘要](screenshots/13-policy.jpg)


## 14 · 策略：已有候选的确定性预演

本轮一次真实确定性策略预演，结果为拒绝；截图只覆盖当前可视部分，没有执行下载。

![14 策略：已有候选的确定性预演](screenshots/14-policy-preview.jpg)


## 15 · 设置概览：八个入口与服务摘要

实际页面展示；本轮未保存配置或提交业务变更。

![15 设置概览：八个入口与服务摘要](screenshots/15-settings-overview.jpg)


## 16 · 基本设置：总开关、演练、自动类型

实际页面展示；本轮未保存配置或提交业务变更。

![16 基本设置：总开关、演练、自动类型](screenshots/16-config-basic-top.jpg)


## 17 · 基本设置：实际媒体库名称与保护片名

实际页面展示；本轮未保存配置或提交业务变更。

![17 基本设置：实际媒体库名称与保护片名](screenshots/17-config-library-names.jpg)


## 18 · 下载与入库方案编辑器

实际页面展示；本轮未保存配置或提交业务变更。

![18 下载与入库方案编辑器](screenshots/18-config-destination-editor.jpg)


## 19 · 独立策略：分类绑定

实际页面展示；本轮未保存配置或提交业务变更。

![19 独立策略：分类绑定](screenshots/19-config-policy-bindings.jpg)


## 20 · 独立策略：模板编辑

实际页面展示；本轮未保存配置或提交业务变更。

![20 独立策略：模板编辑](screenshots/20-config-policy-editor.jpg)


## 21 · 生命周期

实际页面展示；本轮未保存配置或提交业务变更。

![21 生命周期](screenshots/21-config-lifecycle.jpg)


## 22 · 站点与搜索：默认字段

实际页面展示；本轮未保存配置或提交业务变更。

![22 站点与搜索：默认字段](screenshots/22-config-candidates.jpg)


## 23 · 站点与搜索：高级站点 ID 列表

实际页面展示；本轮未保存配置或提交业务变更。

![23 站点与搜索：高级站点 ID 列表](screenshots/23-config-site-ids.jpg)


## 24 · 观察、冷却、抢占：继承控件

实际页面展示；本轮未保存配置或提交业务变更。

![24 观察、冷却、抢占：继承控件](screenshots/24-config-schedule.jpg)


## 25 · 交付与映射：分步配置入口

实际页面展示；本轮未保存配置或提交业务变更。

![25 交付与映射：分步配置入口](screenshots/25-config-delivery.jpg)


## 26 · 云盘范围与服务选择

实际云盘服务选择器；列表包含其他已安装插件，不代表它们具备所需能力。

![26 云盘范围与服务选择](screenshots/26-config-cloud-scope.jpg)


## 27 · 媒体库与两段路径映射编辑器

实际页面展示；本轮未保存配置或提交业务变更。

![27 媒体库与两段路径映射编辑器](screenshots/27-config-mapping-editor.jpg)


## 28 · 本地到云端与整理入口的交付规则

实际页面展示；本轮未保存配置或提交业务变更。

![28 本地到云端与整理入口的交付规则](screenshots/28-config-delivery-rule.jpg)


## 29 · 扫描与恢复

实际页面展示；本轮未保存配置或提交业务变更。

![29 扫描与恢复](screenshots/29-config-recovery.jpg)


## 30 · 清理权限：五类授权开关

实际页面展示；本轮未保存配置或提交业务变更。

![30 清理权限：五类授权开关](screenshots/30-config-cleanup.jpg)


## 31 · 安全与容量

实际页面展示；本轮未保存配置或提交业务变更。

![31 安全与容量](screenshots/31-config-safety.jpg)


## 32 · 榜单与 RSS：自部署 RSSHub 与内置来源

实际页面展示；本轮未保存配置或提交业务变更。

![32 榜单与 RSS：自部署 RSSHub 与内置来源](screenshots/32-config-rsshub.jpg)


## 33 · 具体来源：名称、过滤与方案绑定

实际页面展示；本轮未保存配置或提交业务变更。

![33 具体来源：名称、过滤与方案绑定](screenshots/33-config-source-editor.jpg)


## 34 · AI 名称辅助：启用、模型和端点引用

私密端点引用不是端点凭据本身；截图没有 API key。

![34 AI 名称辅助：启用、模型和端点引用](screenshots/34-config-ai-top.jpg)


## 35 · AI 名称辅助：提示词与备用配置

当前已保存提示词和配置的展示；本轮没有编辑或调用模型。

![35 AI 名称辅助：提示词与备用配置](screenshots/35-config-ai-prompt.jpg)


## 36 · 高级诊断：目标与任务

实际页面展示；本轮未保存配置或提交业务变更。

![36 高级诊断：目标与任务](screenshots/36-advanced-tasks.jpg)


## 37 · 高级诊断：候选决策

实际页面展示；本轮未保存配置或提交业务变更。

![37 高级诊断：候选决策](screenshots/37-advanced-candidates.jpg)


## 38 · 高级诊断：版本档案

实际页面展示；本轮未保存配置或提交业务变更。

![38 高级诊断：版本档案](screenshots/38-advanced-archive.jpg)


## 39 · 高级诊断：交付队列

实际页面展示；本轮未保存配置或提交业务变更。

![39 高级诊断：交付队列](screenshots/39-advanced-delivery.jpg)


## 40 · 高级诊断：策略与预演

实际页面展示；本轮未保存配置或提交业务变更。

![40 高级诊断：策略与预演](screenshots/40-advanced-policy.jpg)


## 41 · 高级诊断：服务与健康

实际页面展示；本轮未保存配置或提交业务变更。

![41 高级诊断：服务与健康](screenshots/41-advanced-health.jpg)


## 42 · 高级诊断：榜单发现

实际页面展示；本轮未保存配置或提交业务变更。

![42 高级诊断：榜单发现](screenshots/42-advanced-discovery.jpg)


## 43 · 高级诊断：解析与 AI

显示预算和私密引用等诊断字段；不表示本轮模型连通测试成功。

![43 高级诊断：解析与 AI](screenshots/43-advanced-ai.jpg)


## 44 · 私密凭据写入：空白表单

空白私密输入表单；没有写入或轮换任何凭据。

![44 私密凭据写入：空白表单](screenshots/44-private-input.jpg)


## 45 · 高级诊断：整合迁移

实际页面展示；本轮未保存配置或提交业务变更。

![45 高级诊断：整合迁移](screenshots/45-advanced-migration.jpg)


## 46 · 迁移：离线导入与唯一处理者表单

仅展开迁移表单，没有导入、停旧实例、激活或回退。

![46 迁移：离线导入与唯一处理者表单](screenshots/46-migration-forms.jpg)


## 47 · MoviePilot 原生目录选择器

只读浏览 MP 容器目录；没有选择目录或创建目录，不是云盘文件树。

![47 MoviePilot 原生目录选择器](screenshots/47-native-directory-picker.jpg)


## 48 · AI 名称辅助：密钥引用列表

显示服务端私密引用，不是密钥值。

![48 AI 名称辅助：密钥引用列表](screenshots/48-config-ai-references.jpg)


## 49 · 榜单与 RSS：自定义地址/路由入口

只切换表单显示方式，没有添加来源或保存。

![49 榜单与 RSS：自定义地址/路由入口](screenshots/49-config-custom-route.jpg)


## 50 · MoviePilot 宿主插件卡片菜单

实际卡片菜单；可从“查看数据”进工作台、“设置”进配置。

![50 MoviePilot 宿主插件卡片菜单](screenshots/50-host-entry.jpg)
