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

## 当前验证基线（2026-09-10）

- 前端 Vitest：36 passed；生产构建成功。
- 隔离 checkpoint 验收脚本：`PYTHONPATH=python python scripts/live_checkpoint_acceptance.py` 退出码 0（首次 Failed + checkpoint 保留；同计划恢复 Completed；Run/步骤/审计/Attempt/产物全部核对）。
- 默认离线 Python 回归（CI 同款命令）：635 passed / 9 skipped / 2 deselected。
- 安全回归：URL/SSRF（web_fetcher、super_search、http_api、authorization）45 passed。
- Mimosa 深度扫描（2026-09-10）：6 个静态 finding（path-traversal 5、SSRF 1）；super_search 的 SSRF 已修复（引擎请求统一走 `_validate_url`）；其余为脚本型工具/缓存写入路径，需要逐项复核后再关闭。
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
