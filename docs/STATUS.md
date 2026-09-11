# EAS 2.0 项目状态

> 本文记录当前实现状态（2026-09-09）。历史测试报告以 archive/ 中的日期版本为准。

## 一句话现状

EAS 2.0 是面向个人本地开发助手的任务编排与执行系统：需求 → Designer 生成设计书 → 人工审批 → 执行计划 → Executor 分步调用工具 → SQLite 持久化与 React 控制台展示。

当前 Python/FastAPI 是唯一实际运行时；Rust workspace 保留为未来主实现，尚未接入 Python 主链路。

## 已完成能力

- Designer 真实生成设计书和执行计划，支持缓存、多 Provider 配置和审批反馈修订。
- Executor 支持步骤执行、重试、超时、暂停/恢复、失败 checkpoint 续跑、孤儿 Retry 恢复、跳步与回滚分支。
- 15 个已注册内置工具：file_creator、command_runner、package_installer、manual_tool、git_operator、text_editor、web_fetcher、super_search、code_context、test_runner、lint_gate、github_operator、http_api、browser_check、project_memory。
- SQLite 持久化计划、步骤、运行、尝试、日志和审计记录。
- 受保护的 API 业务端点统一使用 Bearer/X-API-Token；GET / 为公开健康入口；WebSocket 使用 query token。
- React 控制台包含新建、设计书、计划、执行、工具、历史、审计和配置视图，并提供审批浮层、活动流、主题切换和 WebSocket plan_update。
- Skills、MCP、coding_agent、chat 和 IM 通知已具备生态层实现。
- 前端计划页支持连接状态显示、计划状态刷新、空计划提示、展开/收起阶段、操作错误反馈和活动流过滤。
- WorkGraph 第一阶段（2026-09-11）：步骤支持显式 `depends_on`；计划生成、缓存复用与 `save_plan` 三处都做 DAG 校验（重复 id / 悬空依赖 / 自依赖 / 环依赖 fail-closed）；执行循环按依赖就绪取步，依赖失败或无法就绪时计划转 Failed 而非误报 Completed；旧线性计划无依赖字段时按 order 保持原顺序。新增 `artifacts` 产物元数据表（仅存引用与哈希）与配套 CRUD。

## 当前验证基线（2026-09-10）

- A 标准五任务真实验收：**通过**。5 个真实 LLM 任务全部 Completed，产物 `harness_real_task_*.md` 落盘。证据 `out/real-llm-acceptance-a.json`。
- 计划缓存外键 bug 修复：`generate_plan` 缓存命中时复用计划结构但重新生成 plan_id/step_id，避免重建已执行计划触发 `execution_attempts` 外键违约。
- 前端 Vitest：36 passed；生产构建成功。
- 隔离 checkpoint 验收脚本：退出码 0。
- 默认离线 Python 回归：635 passed / 9 skipped / 2 deselected。
- 真实浏览器（Playwright Chromium）检查：11 passed。
- 计划/生命周期/checkpoint 回归：42 passed。
- Mimosa 深度扫描：6 个静态 finding（path-traversal 5、SSRF 1）；super_search SSRF 已修复，其余待复核。
- Rust 测试：当前开发机未安装 cargo，尚未执行。


## 已知短板

1. `python/api/server.py` 仍然是大型协调入口，后续可按 routes/services 做轻拆。
2. AgentSession/Run/Turn/Attempt 的完整持久化分层尚未完成。
3. 覆盖率分布不均，Designer、LLM client 和部分入口代码较薄。
4. Rust 与 Python 的运行时、错误码和事件契约仍需通过兼容测试逐步对齐。
5. 根目录存在历史脚本、实验目录和运行残留，需先分类再决定归档，不直接删除未知文件。
6. 工具契约“工具返回结构化失败而非把业务失败冒充成功”有文档和测试约束，但仍需逐工具验证异常路径。

## 后续原则

- 面向个人本地使用，优先稳定性、可观测性和日常工具覆盖，不提前引入多用户/多租户复杂度。
- 第一阶段不修改数据库 schema，不引入 outbox；先稳定 Repository、Application Service 和状态机边界。
- 服务端 HTTP 请求仅允许 http/https，并在请求前拒绝 localhost、环回、私有及保留地址。
- 所有 SQL 外部输入必须使用参数绑定，禁止字符串拼接。
- 所有凭据只从环境变量或密钥服务读取；本地覆盖文件不得提交到 Git。
