# 质量策略完整链路修正

范围：独立策略识别、分类准入、维度内排序、共享规则定义、额外条件作用域及真实编辑流程。

## 当前补充：直接入口与可编辑的同等优先

移除“打开质量策略 / 下载方案 / 设置”中转按钮。关闭顶层编辑器或插件后，导航缓存可能保留页面名称但没有编辑器；恢复该状态和直接以 policy / plans 启动时，现在自动建立对应的顶层编辑器，并保留原有草稿离开保护及子页返回逻辑。

具体规格顶部新增“设置同等优先”：勾选至少两项后点“合并为同等优先”，新组放在所选项最前的位置。同等优先框内可点“拆开”，也能拖动某项单独排序。拖动别的项目会保留现有分组。分组只在同一主类内生效；分辨率等无主类的比较项也支持同等优先。

preferences 兼容原来的字符串列表，同时允许用字符串子数组表达同等优先。配置保存和重新初始化保留分组；服务端拒绝空组、重复值、未知规格、多层嵌套及跨主类分组。比较时同组同分，继续判断下一比较项；证据不足时仍等待核实。

- PASS：新增测试先复现中转页和缺少合并能力，再修复。最终前端 142 / 142、相关后端 164 / 164、原生 UI 8 / 8。日志：[前端](ui-review/20260929-reading-order/quality-equal-tests.log)、[后端](ui-review/20260929-reading-order/quality-equal-backend-tests.log)。
- PASS：真实 Vue 浏览器操作验证顶部直接进入、选择 P5 / P8、合并、保存回读及拆分。390px 窄屏无横向溢出（文档 375px）；浏览器 error / warn 为空，检查后恢复默认视口。截图：[选择](ui-review/20260929-reading-order/quality-equal-selection.png)、[保存后的同等优先](ui-review/20260929-reading-order/quality-equal-saved.png)、[窄屏](ui-review/20260929-reading-order/quality-equal-mobile.png)。
- PASS：2026-09-29 23:35 HKT 已更新隔离实例，150 个安装文件和 6 个 HTTP 资源校验一致。实际宿主配置预览接受嵌套优先级并保留其结构；未提交预览，前后配置完全相同、revision 279，未启动媒体任务。备份 `/config/experience-backups/20260929T153552Z`，见 [部署证据](ui-review/20260929-reading-order/quality-equal-deployment.json)。
- PASS：构建完成，Page JS 526.90 kB（gzip 147.72 kB）；保留现有 500 kB 体积提示。
- NOT_RUN：实体手机触控、宿主页面人工视觉验收和真实媒体任务链；浏览器截图使用本地合成接口。

## 三级拖拽、音轨重分组与共享规则原位编辑

按用户最新反馈，将版本优先级改成一个大框中从左到右的“比较项 → 主类 → 具体规格”。右侧嵌在所属层级中；分辨率等没有主类的项目直接显示规格，不增加空层级。三个层级均使用手柄拖拽，支持键盘方向键、Home / End 和 Escape 取消。默认同优先级的格式显示在同一个小框、使用相同序号并解释含义；拖动后按用户的明确顺序保存。

收录范围直接勾选具体规格，去掉“只允许勾选的子类”和整类启用开关。未配置限制时视为全部允许；全选恢复无限制，全部取消会阻止保存并提示至少选择一个规格。

音轨主类改为“无损空间音频 → 无损 → 空间音频 → 其他音轨”。TrueHD Atmos、同音轨证据确认的 DTS-HD MA + DTS:X 属于无损空间音频；DDP Atmos 属于空间音频，普通 DDP 属于其他音轨。保留旧数值音频事实和锁定值，通过新 group 与 legacy_groups 兼容原配置，避免将旧事实 3 误读为已确认无损空间音频。各音轨独立识别，不把一条音轨的无损标记与另一条音轨的空间音频标记组合。既有粗粒度档案需要重新扫描后才能补充细分证据；未知规格不能被当作更低版本触发升级。

共享规则在对应条目下展开，切换规则保留未完成草稿。添加输入框与按钮对齐，新建规则置顶并展开；“用于策略 → 加入收录条件”把规则与该策略已有条件同时要求，显示使用位置并阻止删除仍被引用的规则。“取反”改为“排除符合这组条件的资源”，“子组”改为带父子说明的“条件组”。同时修复 Vue 嵌套代理导致条件排除操作无法记录撤销快照的问题。

- PASS：最终前端 140 / 140，见 [quality-drag-tests.log](ui-review/20260929-reading-order/quality-drag-tests.log)。覆盖三个层级排序、指针取消、键盘排序、共享规则创建与引用、原条件保留、重复应用、条件排除和草稿保护。
- PASS：后端相关 292 项，289 通过、3 因 POSIX/Linux 条件跳过；最终构建后原生 UI 与质量目录 20 / 20。见 [后端日志](ui-review/20260929-reading-order/quality-drag-backend-tests.log) 和 [产物检查](ui-review/20260929-reading-order/quality-drag-native-tests.log)。
- PASS：Vite 构建成功，Page JS 524.92 kB（gzip 146.92 kB），保留现有超过 500 kB 的构建提示。
- PASS：Windows / Codex 内嵌浏览器 / 本地合成接口，实际鼠标拖动三个层级并保存回读；取消 HDR10 收录并保存；创建共享规则、设置排除、用于欧美剧并保存。1440px 深浅主题、390px 窄屏检查；文档 scrollWidth 375，小于 viewport 390；浏览器 error / warn 为空，最终恢复默认 viewport。
- PASS：2026-09-29 23:19 HKT 部署到已授权的 `http://192.168.50.6:13000` 隔离实例。150 个安装文件匹配，6 个 HTTP 前端资源均 200 且 SHA256 匹配。配置前后完全相同，revision 保持 279；enabled=false、dry_run=true、ordinary_work_active=false，诊断无 errors。备份 `/config/experience-backups/20260929T151931Z`；见 [部署证据](ui-review/20260929-reading-order/quality-drag-deployment.json)。
- NOT_RUN：实际手机触控验收、宿主页面中的人工视觉验收及真实媒体下载/入库链路。浏览器保存是合成预览，未修改隔离实例策略配置。

[三级优先级](ui-review/20260929-reading-order/quality-drag-desktop.png) · [音轨四类](ui-review/20260929-reading-order/quality-drag-audio.png) · [直接勾选](ui-review/20260929-reading-order/quality-drag-admission.png) · [规则应用](ui-review/20260929-reading-order/quality-drag-shared-rule.png) · [窄屏](ui-review/20260929-reading-order/quality-drag-mobile.png) · [浅色主题](ui-review/20260929-reading-order/quality-drag-light.png)

以下为历次迭代记录，其中旧布局和旧音轨分类已由上面的当前版本替代。

## 隔离实例部署（2026-09-29 22:43 HKT）

经用户明确授权，已将当前工作区版本安装至 `http://192.168.50.6:13000` 的隔离 V3 实例。宿主安装接口成功，无需重启；149 个安装文件 SHA256 全部匹配，6 份前端资源通过正常登录资源 Cookie 返回 HTTP 200 且哈希匹配。配置 revision 279 保持不变，完整配置前后相同；enabled=false、dry_run=true、ordinary_work_active=false，诊断 errors 为空。

旧插件源码与插件数据库已备份至容器内 `/config/experience-backups/20260929T144333Z`。证据：[deployment.json](ui-review/20260929-reading-order/deployment.json)。部署与资源检查为 PASS；本次未新增真实媒体任务，页面视觉体验待用户在宿主验看。以下 NOT_RUN 记录保留为各次本地实现完成时的验证边界。

## 历史迭代：质量编辑器布局重做

原来的双窄栏把准入条件、全部比较维度和子类排序同时展开，层层灰框与重复标题挤在一起。现改为单个比较项编辑区：顶部保留完整比较顺序，选中一项后只呈现该项主类，点击“类内顺序”展开对应规格，同时只展开一组。主类与类内排序仍分别保存，未修改后端比较语义。

收录范围默认折叠为当前值摘要，并显示额外细分限制数量；进入后仍能编辑全部子类和打开共享规则。侧栏优先显示正在使用与当前选中的策略，未使用的预设收进次级入口。移除重复的分类说明和底部试算引导，试算入口放在版本优先级标题旁。桌面与窄屏统一操作样式、缩进、文字层级，单规格主类保持正常文字对比度。

- PASS：最终 `npm test` 137 / 137，日志 [quality-layout-tests.log](ui-review/20260929-reading-order/quality-layout-tests.log)。覆盖切换比较项、主类与子类调整、筛选、保存和条件草稿保护；保留窄屏策略自动滚动断言。
- PASS：最终 Vite 构建成功；原生 UI 注册及生成产物检查 8 / 8。Page JS 520.85 kB（gzip 144.99 kB），仍有 Vite 500 kB 提示。
- PASS：Windows / Codex 内嵌浏览器，本地真实 Vue 组件配合合成接口。实际展开 DV、调整 P5 顺序并保存成功；切换音轨只呈现音轨编辑区；收录范围与规则定义入口可用。明暗主题、1440px 桌面及 390px 窄屏已截图检查，窄屏无文档横向溢出。浏览器 error / warn 日志为空，检查后恢复默认 viewport。
- NOT_RUN：真实 MoviePilot 宿主部署、真实移动设备和媒体业务链验收。预览保存只在当前页面模拟，不代表正式配置已更改。

[默认主类界面](ui-review/20260929-reading-order/quality-layout-desktop.png) · [类内编辑](ui-review/20260929-reading-order/quality-layout-expanded.png) · [浅色主题](ui-review/20260929-reading-order/quality-layout-light.png) · [390px 音轨编辑](ui-review/20260929-reading-order/quality-layout-mobile.png)

## 历史迭代：主类与子类分层修正

保留画质主类“杜比视界 / HDR / SDR”和音轨原有四个主类。先比较主类，只有同主类才比较 Profile 或编码等子类。主类顺序独立保存在 family_preferences；preferences 只决定各自类内顺序，子类无法跨主类移动。仅改 DV 顺序不会把 HDR 的默认并列关系改成顺序关系。上一版平铺 preferences 保留，但只按所属主类解释；旧配置缺少新字段时使用默认主类顺序。

界面直接呈现主类，子类默认折叠；支持整类收录开关及展开后的细选。主类、类内顺序分别调整与恢复。

- PASS：前端 137 / 137；后端相关 286 项，283 通过、3 项因 POSIX/Linux 条件跳过。日志见 [quality-hierarchy-tests.log](ui-review/20260929-reading-order/quality-hierarchy-tests.log)。
- PASS：最终补充类间默认顺序隔离断言后，质量比较与原生 UI 检查 16 / 16；构建完成。Page JS 518.33 kB（gzip 144.08 kB），仍有 Vite 500 kB 提示。
- PASS：浏览器修改 DV P8/P5 类内顺序、整类取消 HDR、保存并切换回读；主类位置不变、HDR 类内仍为默认顺序。音轨类内展开正确；390px 宽度下 document scrollWidth 375，无水平溢出。页面无 error / warn 日志，viewport 已恢复。
- NOT_RUN：真实 MoviePilot 宿主、实际资源链路及部署，本轮仍为本地后端与合成预览验证。

[主类默认呈现](ui-review/20260929-reading-order/quality-hierarchy-main.png) · [DV 类内顺序](ui-review/20260929-reading-order/quality-hierarchy-dv.png) · [音轨窄屏](ui-review/20260929-reading-order/quality-hierarchy-audio-mobile.png)

实现约束：保留已有粗粒度事实以读取旧档案；新增明确的画面、音轨与片源细分事实，实测粗粒度事实不能借用文件名补成实测 Profile。所有过滤与优先级来自同一目录，由服务端验证和执行。默认 HDR10+ 与 HDR Vivid 高于 HDR10；DV Profile 默认按用户所述 P7、P5、P8 偏好，允许自行排序，这不是绝对画质等级。缺少细分证据时不据此触发升级。

每份策略可以选择每个比较项允许的子类、修改子类优先级并定义本策略额外条件；全局条件与本策略条件都必须通过。发布组与片源旁直接进入实际共享规则，显示内置表达式、覆盖状态和影响范围。预览目录从后端导出，避免合成接口漏字段导致空白。条件编辑统一控件高度与嵌套层级。

验证：用可重复的后端测试覆盖细分识别、实测覆盖、旧配置、优先级与准入分离、作用域隔离、保存重载和规则覆盖；前端验证具体选项、规则入口、条件切换不丢草稿及窄屏。测试环境为本地，不代表 MoviePilot 宿主或实际媒体链验收。

参考：Dolby bitstream profiles https://ott.dolby.com/OnDelKits/Dolby_Vision_Online_Delivery_Kit/v1/Documentation/Specs/Visio_Profiles/help_files/topics/c_dovi_profiles_public.html；HDR10+ white paper https://hdr10plus.org/wp-content/uploads/2023/11/HDR10_WhitePaper.pdf；HDR Vivid white paper https://uhd-world-association.com/wp-content/uploads/2024/10/31-W00002-202212-HDR-Vivid-Technical-White-Paper-.pdf。

## 已确认的原因与修复

- 引擎本来已独立于 MP 执行，粗粒度来自插件自己的旧快照和固定排序。新增 quality-options.json 作为服务端与 UI 共用目录，没有增加依赖。
- 画面 12 类、音轨 15 类、片源 5 类，以及分辨率、特效字幕、高码率、动画偏好，均可单独限制收录与调整优先级。HDR10+ / Vivid 默认并列且优先于 HDR10；DV 默认 P7、P5、P8，可按设备偏好调整。未选择的比较维度仍可作为准入条件。移除了暗中跳过非 4K 画质比较的旧逻辑。
- 发布组、片源、基础与语言规则可从策略直接进入真实共享表达式；内置规则能覆盖和恢复，自定义规则可被全局或分类条件引用。页面明确共享修改影响范围。
- 全局与各分类分别保存额外条件，执行时两层均需通过。未完成的条件阻止保存，切换策略不会丢失草稿。旧配置新增字段有迁移，保存与重启读取纳入后端测试。
- 空白下拉来自预览 mock 没有返回 predicate_fields。现在预览从真实后端导出目录，编辑器也保留字段回退。条件控件统一为 44px；窄屏字段、关系、值逐行排列，嵌套条件保持明确层次。
- 细分实测事实必须与粗粒度事实一致；粗粒度实测不会借文件名补成确定 Profile。未知细分不会被推定为低版本。媒体库投影保留 DvProfile，识别 HDR10Plus、HDRVivid / CUVA 等声明。

## 首轮细分功能验证记录

环境：Windows，仓库 venv Python；Vue 3.5.13 / Vuetify 3.7.3 / Vite 5.4.11；Codex 内嵌浏览器，本地 4179 合成接口。

- PASS：npm test，137 / 137；覆盖策略子类筛选、排序、局部条件作用域、草稿切换、共享规则覆盖及字段目录缺失回退。
- PASS：后端相关 284 项，281 通过，3 跳过。模块：test_quality_options、test_policy、test_configuration、test_archive、test_management、test_management_experience、test_management_display、test_planner、test_runtime_fix1、test_acceptance_policy_boundaries、test_delivery、test_management_quality_fix。
- PASS：最后补充 CUVA / Dolby Vision 空格形式后，test_archive + test_quality_options 再跑 63 / 63。
- PASS：test_native_ui，8 / 8；最终 Vite 构建成功。构建仍提示单个 Page JS 为 514.37 kB（gzip 143.11 kB）超过默认 500 kB 提示线。
- PASS：独立浏览器页实际打开发布组表达式、调整 HDR10+ 顺序、取消 HDR10 收录、设置欧美剧做种人数 >= 3、保存及切换页面回读；全局条件仍未启用。页面无 error / warn 日志。
- PASS：390px 窄屏无水平溢出（document scrollWidth 375，viewport 390）；条件 select / input 均为 44px 高、272px 宽。已恢复默认桌面 viewport。
- NOT_RUN：3 项依赖 POSIX ctime、Linux mountinfo、Linux dirfd/nofollow 的测试。真实 MoviePilot / Emby / 下载器接入、实际文件或资源验收与部署均未执行。
- NOT_COMPLETED：曾启动全量后端发现测试，在无关 AI 限流测试等待处停止；全量后端不计作通过。本次结论仅来自上述明确模块。

复现后端相关集（仓库根目录，PowerShell）：

```powershell
.\.venv\Scripts\python.exe -c "import sys,unittest;sys.path.insert(0,'tests/v3/subscribetter');names=['test_quality_options','test_policy','test_configuration','test_archive','test_management','test_management_experience','test_management_display','test_planner','test_runtime_fix1','test_acceptance_policy_boundaries','test_delivery','test_management_quality_fix'];r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names));sys.exit(not r.wasSuccessful())"
```

截图只证明真实前端配合合成接口的交互，不代表宿主执行。预览保存仅当前页面有效；持久化及重启读取由后端测试证明。

- [细分筛选及排序](ui-review/20260929-reading-order/quality-subtypes.png)
- [分类条件与统一控件](ui-review/20260929-reading-order/quality-local-condition.png)
- [共享规则表达式](ui-review/20260929-reading-order/quality-shared-rule.png)
- [窄屏条件](ui-review/20260929-reading-order/quality-condition-mobile.png)
