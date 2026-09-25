# 外部复现、证据与剩余验收

## 1. GitHub 审核能够独立完成什么

完整产品/前端/测试源码、固定宿主源码、原设计及后续修订、完整需求矩阵、公开测试输入、Archify spec/HTML/PNG/回执均可访问。无须 NAS 凭据。若审核执行器已有 Python/Node 依赖，可直接复现离线测试；若只能读取 GitHub、不能下载 PyPI/npm，仍可做源审，必须标明测试未运行。不要假称 GitHub 访问权限同时包含依赖安装或真实外部服务。

历史 NAS 日志包含账户、站点、网络及媒体信息，**不原样公开**。公开 `evidence-index.json` 是经过裁剪与脱敏的历史摘要：列出原提交、环境、观察、限制、结果及原件 SHA-256，原件标记 `private_original_not_published`。这些哈希供持有原件者比对，不能让没有原件的外部 agent 独立验证现场事实。公开报告只代表已记录的证据，不是第三方见证。

本包中的公开复验记录（`checks/`）、源码、测试输入和 manifest 可以独立检查。不得将“历史台账有一条 passed”自动提升成当前提交的同层复验。

## 2. 获取与版本固定

```sh
git clone --branch codex/subscribetter-v3 https://github.com/eitelkeit0708/MoviePilot-Plugins.git
cd MoviePilot-Plugins
git rev-parse HEAD
python docs/subscribetter/external-review/verify_review.py
```

最后一条命令只用 Python 标准库：核对公开资料清单、完整矩阵计数、引用的证据 ID 以及产品文件 SHA。记录该次 `HEAD`，之后用它替换分支名复核资料；产品固定在 `efb56bb`，资料提交中的产品字节须与之完全一致。

## 3. Python 离线检查

Python 3.12。以下依赖用于离线测试，宿主模块由测试合同/stub 提供；不等于装好真实 MP、115/CD2 客户端与 Linux 运行环境。

```sh
python -m venv .review-venv
# Linux/macOS:
. .review-venv/bin/activate
# Windows PowerShell 可改用 .review-venv/Scripts/python.exe 执行后续命令
python -m pip install -r tests/v3/subscribetter/requirements-review.txt
python -B -X utf8 -m unittest discover -s tests/v3/subscribetter -p 'test_*.py' -v
```

策略回归依赖的三份历史 JSON 已发布在 `docs/subscribetter/design-v1.2-20260917/输入参考/`。它们是非敏感规则数据，不需要原设计 ZIP 或任何私密文件。

历史最新 Windows 记录为 714 tests、5 skipped，产品提交 efb56bb；本次公开包复验以 [checks/python.json](checks/python.json) 为准。标准库 unittest 的“714 tests”包含 skipped，不能写成“714 个都执行通过”。Windows 会跳过 POSIX 文件权限、ctime、mountinfo/dirfd 等条件测试；实际跳过名和原因保留在复验日志。Linux 运行数量/时间/跳过数可不同。

**PUB-02：首次公开包全量运行有 1 个时限测试错误。** 714 项中 `test_cleanup_deadline_blocks_files_and_remove_after_slow_task` 在预派发检查抛出 `TICK_DEADLINE`（5 项跳过），原记录见 [python-first.json](checks/python-first.json) / [完整日志](checks/python-first.log)。该 fixture 从设置 100ms deadline 起仍要写 SQLite 设置并准备调用；在预期慢 task 之前就可能到期。随后同环境的 [7 项定向重跑](checks/python-deadline-focused.json) 全部通过。这说明存在对实际调度/磁盘时延的敏感性，不能据定向通过删除初次错误，也尚不能仅凭这一次错误认定生产发生清理越权。外部审核应分别检查测试稳定性和运行时到期异常的上层处理。

随后对同一产品与测试字节、同一新建依赖环境进行了独立全量复跑：**714 tests / 225.710s / OK (skipped=5)**，见 [最新回执](checks/python.json) / [完整日志](checks/python.log)。第二次没有同时运行前端构建或大文本比较；没有放宽测试时限或修改实现。首次错误仍作为 PUB-02 保留，不以第二次通过推断时序敏感性已经修复。

`test_acceptance_t001/032/054/068/081.py` 是受控离线合同，随本次补齐发布。特别是 T081 fixture 不能代表真实 SubscribeAssistantEnhanced 的 handler/jobs 已消失。离线测试里的 stub、MockTransport、合成文件和固定 Provider 身份不是可替代真实服务的证据。

## 4. 前端检查与构建

```sh
cd plugins.v3/subscribetter/frontend
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run build
```

依赖锁在 package-lock.json，已发布资产在 `dist/assets`。构建基线 Node 24.16 / npm 11.17，Vue 3.5.13 / Vuetify 3.7.3 来自宿主。对不同 Node/npm 的复验需记录版本。检查只有 `./Page`、`./Config`、无框架 fallback 和独立服务。行为测试不是已安装宿主浏览器/键盘/布局验收。

**本次新增发现 PUB-01：源码与提交的 dist 不一致。** 在干净副本中按锁文件安装后，18 项前端行为测试通过、Vite 构建退出 0；重建的 `style-*.js` 与提交资产存在两处 watcher schema 的 `const:false` 差异，以及 watcher 中文标签差异，进而改变 Config/Page 的引用哈希与 remoteEntry。原资产仍写“当前合同仅关闭”，源码写“可选，失败由完整对账接管”。因此构建成功不能记作提交资产逐字节可复现，也不能假设已安装页面已经提供当前源码的 watcher 能力。原始产物对照摘要和日志见 [checks/frontend.json](checks/frontend.json)。本次只发布固定产品基线及审核资料；该产品资产同步问题作为 T200 外的新增审核发现列出，发布前须重新构建、提交并验证实际宿主界面。

## 5. 重建 Archify 图

```sh
git clone https://github.com/tt-a1i/archify.git /tmp/archify
git -C /tmp/archify checkout 9e35d2b0b39b155553ba9fcfe0b4f2a5198dd993
node /tmp/archify/archify/bin/archify.mjs validate architecture docs/subscribetter/external-review/architecture.json --quality showcase --repo-root . --json
node /tmp/archify/archify/bin/archify.mjs deliver architecture docs/subscribetter/external-review/architecture.json /tmp/subscribetter-architecture.html --quality showcase --repo-root . --json
node /tmp/archify/archify/bin/archify.mjs visual-check /tmp/subscribetter-architecture.html --json
```

Windows 换成临时目录绝对路径。生成无需 npm 依赖；浏览器检查要求本机 Chrome/Chromium。本次工具生成 HTML 已通过 9/9 showcase、0 errors、0 warnings，以及 1440×900、1600×1000、1920×1080、2048×1320 的浏览器测量；视觉检查另记在 [回执](architecture-receipt.json)。原始回执中的本机路径已改为仓库相对路径，图的 spec/HTML 字节哈希保持原值。

## 6. 现场证据的时间和版本

可在 GitHub 读取 [五份历史探针的公开摘要](checks/historical-probes.json)，包含各自原件哈希、固定提交和工况限制。这些是操作者记录的摘要，不是独立复现或原始远端完整输出；与本次新执行的 `python.json` / `frontend.json` 分开解读。

| 证据 | 原版本与能说明的结论 | 不能推出的结论 |
|---|---|---|
| T199 真实视频链路 | `216e0a503dfca61743f5f67f2783e989e80589f4`；真实 PT 下载、约 17GB 视频交付与后续 Symedia/Emby 记录 | 当前 efb56bb 的全链路已经重新执行 |
| 后续只读追溯 | 历史任务/操作回执仍在，媒体/STRM/Emby 对象仍可观察；存在后续取消计划与代次变化 | 对象仍在就证明由当前代码新交付；旧 provenance 空值不是当前版本证明 |
| efb56bb 安装核对 | 已隔离 staging，81 个产品文件的 SHA 与目标树一致 | 安装成功就证明所有场景成功 |
| efb56bb T189 | 已选库旧媒体 record_only；未选库/未知库存延后；没有新增 owner submit | 新媒体完整下载链路成功 |
| efb56bb Linux deadline 探针 | 受控 sites/RSS/SQLite 延时，到期不推进 cursor/冷却；到达记录未收据化 | 该延时一定来自真实站点故障 |
| 最终隔离状态 | 当时 enabled=false、dry_run=true、ordinary_work_active=false | 当前 NAS 永远保持该状态；本文没有实时监控 |

## 7. 六个未闭环项

| ID | 缺少什么 | 已有证据及下一步 |
|---|---|---|
| T003 | 真实同站、相同非空标题及描述、不同 torrent ID 的双资源工况 | 离线区分/独立排除测试存在；仍需实际双条资源，或明确有界例外 |
| T006 | 真实 RSS 同时缺 description 与 labels 的资源 | 离线补齐/DEFER 存在；不手工删字段冒充自然工况 |
| T060 | 授权根目录内 CD2 真实 FindFileByPath/GetSubFiles 缺 SHA1 | 合同与 fallback 测试不能替代该响应；需要原工况或有界例外 |
| T081 | 增强助手 0.7.9 安全非重叠共存与切换 | 实际仍有 meta/common jobs，进入 WAIT_OWNER；需源码支持的非重叠模式或安全分类，不能绕过守卫 |
| T092 | 原输入要求的真实 CloudFS 无 CREATE | 实测实际产生 CREATE；控制恢复证据只覆盖另外工况，不能写 literal pass |
| T200 | 剩余完整分支审核及当前提交真实端到端复验 | 当前回归、安装、局部真实服务和 Linux 合成时限证据存在；本资料包开始外部审核，不自动完成 T200 |

原表 T183 保留历史条目并标为 scope_removed，不计 199 个活跃项。R59 的名称辅助保留，独立聊天子要求已被用户撤销。曾被误提升的受控证据已在台账中回退；公开矩阵保留语义审计相关历史摘要，不能用旧报告覆盖最新状态。

## 8. 不在 GitHub 内可独立复现的检查

真实站点搜索/下载、115 授权与秒传、CD2 RPC、单授权 Symedia 停机、Emby 库和实际目录挂载均需要另一个有授权的测试环境。公开资料不含这些秘密，也不授权审核者访问生产。若缺环境，报告“未独立验证”即可；可继续源码、离线与矩阵审计，不必虚构失败或等待用户提供密钥。

放行建议至少区分：实现可审核、离线检查通过、特定宿主合同通过、特定真实工况通过、带明确例外的试运行、生产最终验收。请引用发现和缺口，避免只有一个无条件“通过”。
