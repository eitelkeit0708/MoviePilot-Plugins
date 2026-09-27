# 隔离 V3 真实界面截图

以下图片来自 `http://192.168.50.6:13000` 的隔离 MoviePilot V3，加载产品提交 `fd05af1` 的正式组件和真实宿主数据。没有使用合成预览替代宿主截图。

| 文件 | 内容 | 可支持的结论 |
| --- | --- | --- |
| `01-desktop-work-detail.png` | 作品详情与在库版本 | 正式组件能在作品上下文显示当前版本和处理入口。 |
| `02-desktop-subscriptions.png` | 订阅列表 | 海报、状态和空闲记录密度已进入正式宿主。 |
| `03-desktop-discovery-filter-restored.png` | 从作品返回榜单 | 来源筛选与返回焦点已恢复。 |
| `04-desktop-transfer-files.png` | 上传与入库详情 | 文件角色、状态、名称和完整路径入口可见。 |
| `05-desktop-policy-editor.png` | 质量策略 | 共享作用域默认折叠为计数摘要。 |
| `06-desktop-discovery-sources.png` | 榜单来源 | RSSHub／Cron 显示为可读摘要。 |
| `07-mobile-subscriptions.png` | 390×844 订阅列表 | 标题、筛选和作品行在真实 CSS 视口内显示。 |
| `08-mobile-work-detail.png` | 390×844 作品详情 | 返回、作品信息、上下文、页签和版本卡未横向撑宽。 |

移动截图使用浏览器 viewport override `390×844`，捕获前确认 `documentElement.clientWidth=390`、`scrollWidth=390`。先前受浏览器残留缩放影响的图片已经覆盖，不作为证据。

截图只执行读取、筛选、展开、进入作品和返回等界面操作。隔离插件在截图前后均保持禁用和演练模式。
