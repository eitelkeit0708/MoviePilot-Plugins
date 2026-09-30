# 2026-09-30 提交前验证

范围：当前 `codex/subscribetter-v3` 工作树中的质量策略细分、准入与优先级编辑、同等优先分组、返回和榜单导航，以及正式前端产物。环境为 Windows、仓库 `.venv` Python 3.12.3、Node.js 24.16.0。

| 检查 | 结果 | 实际命令与范围 |
| --- | --- | --- |
| 前端回归 | PASS | 在 `plugins.v3/subscribetter/frontend` 执行 `node --test --test-isolation=none test/*.test.mjs`，142 项通过，无失败或跳过。 |
| 正式构建 | PASS | 同目录执行 `node node_modules/vite/bin/vite.js build --config ../vite.config.mjs`，117 个模块；Page JS 526.90 kB，gzip 147.72 kB。保留现有超过 500 kB 的包体提示。 |
| 后端完整回归 | PASS | 在仓库根执行 `.venv/Scripts/python.exe -B -X utf8 -m unittest discover -s tests/v3/subscribetter -p 'test_*.py'`，780 项中 775 项通过、5 项因 POSIX/Linux 条件跳过，无失败，用时 233.094 秒。 |
| 差异格式 | PASS | `git diff --check`。 |
| 图集与当前源码一致 | PASS | 当前插件 146 个源码文件的 SHA-256 全部与架构图的源码快照清单一致，正式重构建没有改变产物。 |
| 图集发布副本 | PASS | 三张原生 HTML 和单文件入口的哈希与交付清单一致，完整 ZIP 内的 HTML/JSON 与发布副本逐字节一致。完整 Archify 校验回执见 [图集](architecture-20260930/README.md)。 |
| Linux 与宿主实际业务链 | NOT_RUN | 本轮提交前验证没有运行 Linux 专用门禁，也没有连接真实 MoviePilot、NAS、下载器或媒体服务。 |

跳过的五项涉及凭据文件系统权限、POSIX ctime 身份/失效、Linux mountinfo 和 dirfd/nofollow。Windows 回归不能代替这些宿主门禁。

本机全局 `npm` 启动入口缺少 `npm-cli.js`，因此直接使用 Node 执行项目现有测试和构建脚本对应命令；没有修改项目依赖、锁文件或全局 npm 配置。
