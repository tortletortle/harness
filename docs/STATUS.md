# EAS 2.0 项目状态

> 本文是唯一的现状文档（2026-08-29 更新）。早期的 CURRENT_STATUS.md、MISSING_FEATURES.md
> 与若干测试报告已移入 [archive/](archive/)，描述的是 2026-08-21~24 的状态，多数结论已过时。

## 一句话现状

核心链路（需求 → 设计书 → 审批 → 计划 → 执行 → 日志/历史）完整可用且经过加固；
主要短板是工具数量少（9 个，2026-08-30 新增 super_search 超级搜索后仍偏少）、知识库未实现、计划状态靠轮询而非推送。

## 已验证的数据（2026-08-29 实测）

| 项目 | 结果 |
|------|------|
| 后端 pytest | 244 通过 + 2 跳过，覆盖率约 48% |
| 前端 vitest | 30 通过（5 个文件） |
| API 端点 | 16 个（含暂停/恢复/跳步/回滚/审计/配置/重启）+ WebSocket 日志流 |
| 前端视图 | 输入、设计书、计划、执行、历史、工具、配置、审计 8 个视图 + 全局错误提示 |
| 内置工具 | 10 个（2026-08-30 新增 super_search 超级搜索 + code_context 代码上下文） |
| CI | 三任务流水线（Rust / pytest / vitest） |

## 已完成

- **Designer 流程**：设计书生成（多提供商 fallback、缓存）、审批（批准生成计划 / 拒绝按反馈修订）
- **执行系统**：计划执行、步骤重试与超时策略、暂停/恢复（原子断点）、跳步守卫、
  回滚到指定步骤（克隆新分支 + 废弃旧分支）、孤儿恢复
- **状态机加固**：原子计划认领（claim_plan_status）、Paused 不可被队列任务抢走、
  状态与断点单条 UPDATE 落库
- **错误处理契约**：DatabaseError / GenerationError 分层抛出，禁止假装成功；
  前端错误/警告全局 toast。详见 [ERROR_HANDLING.md](ERROR_HANDLING.md)
- **前端**：React 仪表盘、WebSocket 实时日志（带重连去抖）、全局错误提示（ToastHub）、审计日志视图
- **安全**：全部 /api/* 端点 token 鉴权（Bearer / X-API-Token，常数时间比较）、
  命令执行与网络请求工具的安全加固（有专项测试）
- **持久化**：SQLite 全量持久化 + 审计日志

## 缺失 / 待办（按优先级）

1. **工具数量**（最大短板）：已有 10 个基础工具（含 2026-08-30 新增的 super_search 超级搜索与 code_context 代码上下文）（文件、命令、装包、Git、文本编辑、
   网络下载、人工干预）。没有数据库操作、Docker、API 测试等，很多真实任务无法自动化。
2. **计划状态推送**：目前仅日志走 WebSocket，计划/步骤状态靠前端 2 秒轮询。
   （工作区已有一个未提交的 `Database.get_plan_status_summary()` 草稿，是为广播准备的）
3. **知识库**：`python/knowledge/` 为空壳，未实现任何检索/记忆能力。
   （2026-08-30 注：已通过内置工具 `code_context` 落地第一步——工作区仓库地图、
   SQLite FTS5 全文检索与知识笔记长期记忆，见 docs/CODE_CONTEXT.md；
   跨任务记忆编排与向量检索仍是待办）
4. **架构分层与代码分布脱节**：`designer/`、`executor/`、`dispatcher/`、`toolmanager/`
   目录只有空 `__init__.py`，真实逻辑集中在 `api/server.py`（约 1500 行）与
   `utils/interfaces.py`。文档与目录结构反映的是设计愿景而非现实。
5. **测试覆盖不均**：总体 48%，designer / llm_client / dispatcher 偏薄。
6. **Rust 侧**：crates 存在（common/db/dispatcher/logger/server/tool_manager/gui），
   但 Python 层已自成体系，Rust 部分与主链路未打通；错误处理约定未同步。
7. **遗留清理**：`python/api/main.py`（9000 端口旧入口）与 `server.py.bak` 未删；
   根目录 `a.txt`、`out.html`、`test.py`、`package.json`/`node_modules` 为用户文件未整理。

## 成熟度评估

| 场景 | 可用性 |
|------|--------|
| 单机演示 / 简单项目生成与执行 | ✅ 可用 |
| 开发辅助（文件/命令/Git/装包类任务） | ✅ 可用 |
| 复杂项目实施 | ⚠️ 受限于工具数量 |
| 生产环境 / 多用户 / 企业级 | ❌ 不具备（无监控、权限、协作） |
