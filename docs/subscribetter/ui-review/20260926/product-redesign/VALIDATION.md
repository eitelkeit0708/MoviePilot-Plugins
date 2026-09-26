# 验证记录与开放项目

产品最终提交 `30c0bd105e5c6800b1c94d806a342eaeaf67f9a8`。截图跨多个本轮构建，版本以 SCREENSHOTS.md 为准。

## 固定三条产品标准

| 标准 | 实现及证据 | 不能据此推出 |
|---|---|---|
| 列表不进诊断即可辨认阶段和需处理项 | 49 合成活跃、暂停、停止、未知；45 真实历史任务。后端阶段最多 6 个目标采样并标覆盖 | 真实宿主当前所有混合阶段均已生成；当前宿主没有活跃在途计划 |
| 不解析文件名即可说出该集新旧差别及未完成原因 | 50–51 六集状态、54 窄屏；27 展开；真实 33 档案 | 合成 25% 是真实下载；发布者标注已实测；旧版一定可播放 |
| 首次配置不查数据库、不手填内部 ID、不搬回执 | 空白开始 17–20、组件完整路径/失败保持测试、28–30 离开保护；39 真实聚焦流程、48 多库恢复 | 在全新真实宿主完整保存和端到端入库已通过；外部115父目录编号已自动选取 |

三条均为**供人类审阅的实现**，不自行判定视觉和可用性通过。尤其原图比未溢出断言更重要。

## 本轮执行结果

- 前端最终：`npm test` → **52 passed / 0 failed / 0 skipped**，`npm run build` → **101 modules，成功**。完整最终测试输出在 frontend-tests.txt（仅统一行尾空白）。材料提交额外补齐测试夹具的 VDialog 替身，产品源文件未改变。
- 后端：275 项，271 通过、4 跳过，56.889 秒。命令见下。该次运行在 83df7c4 后，随后只修改前端；没有将较早测试冒充最终重新执行。此项是会话输出摘要，本包未保存该次完整 stdout。
- 四个跳过：POSIX ctime 身份、Linux mountinfo、Linux dirfd/nofollow、POSIX credential filesystem enforcement。Windows 通过不能替代这些 Linux gate。
- 部署最终源码文件 169 个 SHA 校验，安装 HTTP 200 / success=true。见 deployment.json。
- 最终隔离读取：config revision=271；enabled=false；dry_run=true；ordinary_work_active=false；ai_enabled=false；errors=[]。配置 digest 与开始时相同。见 live-readonly.jsonl。
- 真实《一瓯春》：列表 evaluation 移除；summary 8/16；单条 detail 16；`sort=newest` 两条计划与对完整集合排序结果一致。新候选详情截图46按 1、2、3…排列。
- 真实鼠标选择、菜单/页签操作、键盘 Enter 打开作品、返回及焦点恢复。非零列表滚动为 **827.5 → 827.5**，焦点回《一瓯春》行。见 interaction-checks.json。
- 合成配置浏览器操作：空配置从选择服务到保存；未填当前步骤时报错；长中文名称与路径；共享修改显示另一方案；保留草稿离开、返回及实际 reload 后恢复路径。
- 真实 AI：生产关闭，点击「测试连接」，模型 deepseek-flash 返回有效名称识别结果，1 次请求。截图42–44属于 b615795；后续改动没有触碰 AI 后端/组件。最终配置再次确认 AI 关闭。没有为取得失败图破坏真实凭据。

## 安全和契约回归

前端检查覆盖：读取失败与空结果、过期响应丢弃、部分摘要与完整详情、字节计数未知、分组不跨不同原因、共享实际字段影响、逐步缺项、空白方案自动关联、预检失败保留草稿、原生保存响应丢失后不重复 PUT、精确回读后解除锁定、修改后撤销旧成功提示，以及两份媒体库恢复目标不会固定选第一份。

AI 后端新增检查覆盖生产关闭时显式测试可请求、普通辅助仍关闭、预算耗尽不旁路；沿用原有权限、并发、失败处理和私密存储测试。本轮没有删掉发布/清理/未知结果保护来换取 UI 成功。

## 开放项目和限制

1. **真实浏览器 200% 缩放未完成**：Ctrl+plus 操作后 viewport/DPR 没有变化，不能把尺寸模拟当缩放通过。
2. **真实触屏及读屏软件未测**。390×844 是桌面内嵌浏览器的 viewport，鼠标/键盘操作不是手机触摸证明。
3. **AI 真实失败未出现**。未配置、部分保存失败、测试失败图来自合成适配器；真实只证实成功，不把合成 503 当服务端实测。
4. **全新真实配置闭环未重新执行**。空白方案浏览器保存是 fake native adapter；组件测试覆盖原生保存丢失响应，后端配置检查独立运行。隔离宿主只读编辑已有方案，真实配置 revision 保持271。
5. **本轮未重跑下载、秒传、普通上传、Symedia 停机恢复、清理或播放**。旧验收结果仍查既有台账；截图不能补齐这些证据。
6. **旧模型限制保留**：一个云盘可能连多个映射；方案名称仍是 ID；115 父目录高级映射仍涉及外部文件夹编号。没有伪装成已迁移的独立名称或自动识别目录。
7. **高级少用配置仍有通用控件**，如高级映射条件、115父目录、原始依据。日常列表、分集、方案、AI主流程已改，不能声称每个高级表单都完成专项设计。
8. **部分原图是实现阶段材料**，具体见清单。21–23曾出现中间配置或截图裁切异常，已从审阅集合排除；32/36等有修复前措辞，也不列入最终证据。

## 复现

在仓库根目录准备 Python 环境与 `tests/v3/subscribetter/requirements-review.txt`。前端组件测试使用根目录 `.venv` 下 Python 读取真实 SQLite DTO 夹具。安装依赖后：

```powershell
$env:PYTHONPATH='tests/v3/subscribetter'
.\.venv\Scripts\python.exe -X utf8 -m unittest test_management_display test_management test_management_spec_fix test_management_reload_fix test_management_quality_fix test_native_ui test_configuration test_ai test_archive test_planner test_delivery -q
```

```powershell
Set-Location plugins.v3/subscribetter/frontend
npm ci
npm test
npm run build
npm run dev -- --host 127.0.0.1 --port 4179
```

浏览 `http://127.0.0.1:4179/dev/index.html`，顶部明确标合成数据。六集混合状态、异常选择、空白配置入口仅影响浏览器内的模拟状态，不连接用户 NAS。截图用 1440×900、1280×720、390×844 viewport；应选当前可见 tab，否则本工具曾发生隐藏标签截图裁切异常。

本包 verify.py 只校验原图，不执行真实业务，也不评定美观。
