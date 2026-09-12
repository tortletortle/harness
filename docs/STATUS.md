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
- WorkGraph 第二阶段（2026-09-11）：资源声明（`resources: {read, write}`）与路径冲突检测（write×write / write×read 冲突，read×read 兼容）；`select_next_batch` 依赖感知 + 冲突过滤的批取步；执行循环提取统一单步执行器，支持有界并发（`asyncio.Semaphore`）批级并行；配置 `models.executor.max_concurrency`（默认 1 = 严格串行）与顶层 `parallel_graphs`（默认 false）。并行默认关闭，旧计划与现有串行行为完全不变。
- Worktree 隔离第一阶段（2026-09-12）：`git_operator` 新增 worktree 指令组（add/list/diff/merge/remove，worktrunk 三命令语义，worktree 位于仓库同级 `<repo>-wt-<name>`）；新增 `worktrees` 表与绑定生命周期（active/merged/discarded）；API 端点 `POST/GET /api/plan/{id}/worktree`、`.../merge`、`.../discard`（合并/丢弃为用户显式动作，Running 状态拒绝）；计划绑定 worktree 执行时 workspace 根重定向（env + Grant workspace），执行结束恢复；合并前自动快照未提交产物，冲突返回结构化失败不自动重试。
- WorkGraph 第三阶段（2026-09-12）：工作区隔离升级为 contextvar（工具线程池显式传播），多计划并行执行落地——绑定 worktree 的计划不占主工作区执行权，可与其它计划并行；AgentSession/Turn 运行记录分层（`agent_sessions`/`agent_turns` 表，coding_agent 逐轮记录模型调用/token/outcome，API `GET /api/agent-sessions[/{id}/turns]`）；Artifact 内容层（`store_artifact_content` SHA-256 落盘 + 读取/校验 + API `GET/POST /api/artifacts`，步骤产物自动登记）；动态重规划（`POST /api/plan/{id}/replan`：Designer 契约化生成修复步骤链，挂入新分支依赖图，F 重跑点挂在 Rn 之后，旧线性计划显式化）；review_agent/diagnosis_agent 注册进 Capability 清单；计划 Completed 自动生成最终交付报告（kind=delivery_report，fail-open）。
- 任务模式识别（2026-09-12）：`application/task_router.py` + `POST /api/quick-task`，实现设计稿 §4 的直接工具模式路由（graph 模式沿用设计书流程）。

## 当前验证基线（2026-09-12）

- **C 标准真实验收：通过（4/4 场景）**。真实 LLM 补齐此前缺的证据：C1 受控并行（显式依赖图 + parallel_graphs=true，running 事件时间戳重叠证实 sibling 并发执行）✓；C2 多 worktree 并行（两计划各绑独立 worktree 并发执行，产物隔离在主仓库之外）✓；C3 AgentSession/Turn 记录（真实 coding_agent 步骤产生 session + 逐轮 turn，token/终态可查）✓；C4 交付报告内容（报告真实生成且为可读 markdown）✓。证据 `out/real-llm-acceptance-c.json`。
- **B 标准真实验收：通过（4/4 场景）**（拆分后重跑确认链路完好）。证据 `out/real-llm-acceptance-b.json`。
- A 标准五任务真实验收：**通过**。证据 `out/real-llm-acceptance-a.json`。
- **拆分修复（2026-09-12）**：server.py 按领域拆分后，真实链路验收暴露并修复了 7 处 `api/routes/*` 的符号缺失——5 个文件缺 `logger` import（异常分支 NameError）、design_routes 缺 `GrantLimits`、plan/worktree 缺 `GitOperator`、replan 缺 `List` 等。用 AST 扫描 + 逐文件补全，路由注册全部验证通过。
- 任务模式识别（2026-09-12）：`application/task_router.py` + `POST /api/quick-task`（direct 直达工具 + Grant + 审计）。
- 前端 Vitest：49 passed（新增 InputView 快速执行组件测试）；生产构建成功；WorkGraph 依赖视图 / 产物库 / Agent 记录三视图已接入。
- 前端任务模式入口（2026-09-12）：新建任务页新增"快速执行"按钮，调用 `/api/quick-task`——识别为 direct（读取/搜索/测试/检查）时内联展示工具结果（内容/失败原因/安全拦截），识别为 graph 时引导进入设计书流程。目标任务模式识别的前端闭环完整。
- 默认离线 Python 回归：694 passed / 9 skipped / 2 deselected。
- 真实浏览器（Playwright Chromium）检查：11 passed。
- Mimosa 深度扫描：super_search SSRF 已修复，其余待复核。
- Rust 测试：当前开发机未安装 cargo，尚未执行。


## 已知短板

1. ~~`python/api/server.py` 仍然是大型协调入口~~ **已拆分（两轮，2026-09-12）**：
   - 第一轮：状态/广播/格式化进 `api/shared.py`，HTTP 路由按领域进 `api/routes/`（design/plan/approval/artifact/agent_session/worktree/quick_task/replan/misc 九个模块，`app.include_router` 装配）；server.py 3242 → 1643 行。
   - 第二轮：执行运行时拆到 `python/api/runtime/`（step_execution.py / artifacts.py / background.py），server.py 1643 → 545 行（净减 1098 行）。
   - 兼容 re-export：所有 runtime 符号仍可从 `api.server` 导入（`from api.server import execute_plan_background` 等），单步状态机调用通过 `api.server` 间接解析，测试 `monkeypatch.setattr(server_mod, "_execute_step_with_state_machine", ...)` 路径保持兼容，零测试改动。
   - 顺手清理：删除 `_is_retryable_exit_code_acceptance` 函数尾部的孤儿 docstring + if/raise（永不执行的死代码）；删除与 `api/shared.py` 重复的 `format_plan_for_frontend` 定义，统一以 shared.py 为准。
2. ~~AgentSession/Run/Turn/Attempt 的完整持久化分层尚未完成~~ Session/Turn 已落地（2026-09-12，含前端视图）；Turn 级上下文压缩与跨会话恢复仍为后续。
3. 覆盖率分布不均，Designer、LLM client 和部分入口代码较薄。
4. Rust 与 Python 的运行时、错误码和事件契约仍需通过兼容测试逐步对齐；Rust 代码从未编译验证（本机无 cargo）。
5. 根目录存在历史脚本、实验目录和运行残留，需先分类再决定归档，不直接删除未知文件。
6. 工具契约“工具返回结构化失败而非把业务失败冒充成功”有文档和测试约束，但仍需逐工具验证异常路径。
7. 前端已覆盖 WorkGraph 依赖视图、产物库与 Agent 记录（2026-09-12）；quick-task 直达入口的 UI 仍未接入（后端 API 已就绪）。

## 后续原则

- 面向个人本地使用，优先稳定性、可观测性和日常工具覆盖，不提前引入多用户/多租户复杂度。
- 第一阶段不修改数据库 schema，不引入 outbox；先稳定 Repository、Application Service 和状态机边界。
- 服务端 HTTP 请求仅允许 http/https，并在请求前拒绝 localhost、环回、私有及保留地址。
- 所有 SQL 外部输入必须使用参数绑定，禁止字符串拼接。
- 所有凭据只从环境变量或密钥服务读取；本地覆盖文件不得提交到 Git。
