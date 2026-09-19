# EAS 2.0 Harness — 项目索引

> 生成日期：2026-09-10。本文件是全仓库的导航索引：每个模块的位置、职责、规模与入口。
> 现状与待办见 [STATUS.md](STATUS.md)，目录运行边界见 [STRUCTURE.md](STRUCTURE.md)。

## 一句话概览

AI 任务编排与执行系统：用户需求 → Designer（LLM）生成技术设计书 → 人工审批 → 生成执行计划 → Executor 按步骤调用工具（受 Grant 授权边界约束）执行 → 全程 SQLite 持久化 + WebSocket 实时推送 + React 控制台可视化。

**唯一实际运行时是 Python/FastAPI**；Rust workspace 是未来主实现候选，未接入主链路（边界契约见 [runtime-boundary.md](runtime-boundary.md)）。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11、FastAPI、SQLite、asyncio、WebSocket |
| 前端 | React 18 + Vite + TypeScript + Tailwind CSS + Zustand |
| LLM | OpenAI 兼容多 Provider（stepfun/bbapi/newapi/hejuapi）+ 离线 DeterministicProvider |
| Rust | workspace crates（common/dispatcher/db/gui/logger/server/tool_manager），未接线 |
| 测试 | pytest（后端）、Vitest（前端）、cargo test（Rust，CI 跑）、Playwright（浏览器用例） |

## 核心运行链路（文件级索引）

```
用户输入需求
  → python/api/server.py            POST /api/design
    → python/designer/__init__.py   LLM 生成设计书（缓存 python/cache.py）
  → POST /api/design/{id}/approve   人工审批 → 生成执行计划
  → POST /api/plan/{id}/execute     execute_plan_background() 状态机循环
    → python/executor/__init__.py   步骤执行、重试、超时、checkpoint
    → python/authorization.py       Grant 签发/撤销（工具调用边界）
    → python/capabilities.py        能力层（工具 + MCP 包装）
    → python/tools/__init__.py      ToolRegistry，15 个内置工具
  → python/database.py              SQLite 全量持久化
  → WebSocket /ws/logs              broadcast_plan_update / log / approval → 前端
```

## Python 后端（`python/`，运行时所在）

### 入口与服务层（2026-09-12 已拆分）
| 位置 | 行数 | 职责 |
|---|---|---|
| `api/server.py` | ~545 | **组装点**：lifespan 装配、异常处理、`include_router` 装配、兼容 re-export（routes 与 runtime） |
| `api/runtime/` | ~1157 | **执行运行时**（自 server.py 拆分）：`step_execution.py`（单步状态机 / 重试 / attempt / 事件 / 状态写入 / 上下文压缩）、`artifacts.py`（步骤产物登记 + 最终交付报告）、`background.py`（execute_plan_background 后台总循环）；runtime 符号同时从 `api.server` 命名空间可访问，测试 monkeypatch 面兼容 |
| `api/shared.py` | ~360 | AppState 全局状态、WebSocket 广播、格式化、配置加载、暂停检查点计算 |
| `api/routes/`（9 个模块） | ~1770 | 按领域拆分的 HTTP 路由：design（207）、plan（590）、approval（70）、artifact（72）、agent_session（29）、worktree（162）、quick_task（98）、replan（209）、misc（321，含状态/工具/chat/skills/notify/capabilities/config/审计/用量/重启/WebSocket） |
| `api/auth.py` | — | Bearer / X-API-Token 鉴权（`require_token`）；GET `/` 公开健康检查，WS 走 query token |
| `database.py` | ~1900 | SQLite 持久化：tables `designs, plans, steps(depends_on), execution_runs, execution_attempts, step_events, logs, audit_logs, artifacts, worktrees, agent_sessions, agent_turns`；迁移与索引 |
| `application/plan_service.py`、`domain/plan_state.py`、`domain/work_graph.py`、`repositories/plan_repository.py` | 30/38/约 200/31 | 渐进式拆分边界：应用服务、纯状态机规则、DAG 校验 + 依赖就绪判定 + 资源冲突检测 + 批选择、Repository facade |

### 生成与执行
| 文件 | 行数 | 职责 |
|---|---|---|
| `designer/__init__.py` | ~1050 | Designer：真实 LLM 生成设计书 + 执行计划、审批反馈修订、结果缓存 |
| `executor/__init__.py` | ~660 | Executor：步骤执行、重试/超时、暂停/恢复、失败 checkpoint 续跑、跳步、回滚分支 |
| `executor/acceptance_feedback.py` | — | 验收标准反馈回路 |
| `llm_client/__init__.py` | ~360 | OpenAI 兼容多 Provider 客户端 + 离线 DeterministicProvider；`grant_llm_client.py` 为 Grant 约束的 LLM 调用 |
| `json_repair.py` / `utils/json_fixer.py` | — | LLM 输出 JSON 修复（实验记录见 [json_stability_experiment.json](json_stability_experiment.json)） |
| `chat.py` | ~270 | 内置助手 `/api/chat`：会话式工具调用，受 Grant 预算约束 |
| `coding_agent.py` | ~515 | 编码代理（patch 模式，配套 `patcher.py`） |
| `dispatcher/__init__.py`、`toolmanager/__init__.py`、`models/__init__.py` | 96/142/225 | 调度、工具管理、模型配置解析 |

### 工具层（`python/tools/`，默认注册 15 个）
| 工具 | 行数 | 用途 | 设计文档 |
|---|---|---|---|
| `super_search.py` | ~755 | 多引擎聚合搜索+深度检索+正文抽取（无 Key 可用，可选 Tavily/Brave/Serper） | [SUPER_SEARCH.md](SUPER_SEARCH.md) |
| `code_context.py` | ~675 | Aider 式仓库地图 + SQLite FTS5 全文检索 + 知识笔记记忆 | [CODE_CONTEXT.md](CODE_CONTEXT.md) |
| `text_editor.py` | ~415 | 文本编辑 | |
| `command_runner.py` | ~250 | 命令执行（安全约束） | |
| `web_fetcher.py` | ~230 | 网页抓取（SSRF 防护） | |
| `github_operator.py` / `git_operator.py` | ~230/230 | GitHub / 本地 git 操作 | |
| `package_installer.py` | ~200 | 依赖安装 | |
| `lint_gate.py` / `test_runner.py` | ~190/180 | lint 门禁 / 测试运行 | |
| `browser_check.py` | ~185 | Playwright 浏览器检查 | |
| `file_creator.py` | ~165 | 文件创建 | |
| `http_api.py` | ~185 | HTTP 请求（仅 http/https，拒绝 localhost/私有地址） | |
| `project_memory.py` | ~330 | 项目记忆读写（PROJECT_MEMORY.md）：整条边界滚动 + 归档文件、按 dedupe_key 折叠重复摘要、条数与字符数双限（环境变量可调） | |
| `manual_tool.py` | ~70 | 人工步骤占位 | |
| `workspace.py` | ~35 | 工作区路径 | 工具契约见 [TOOL_INTERFACE_DESIGN.md](TOOL_INTERFACE_DESIGN.md) |

### 生态与支撑层
| 文件 | 职责 |
|---|---|
| `authorization.py` (~480) | Runtime Grant：每个 API 步骤单独签发、执行后撤销；deny `.env*`、`.git/**` |
| `capabilities.py` / `mcp_client.py` / `mcp_server.py` / `mcp_adapter.py` | 能力抽象 + MCP 双向集成（Notion server 已配置；asyncio 行上限修复见 models.yaml 注释） |
| `skills_loader.py` | Skills 加载（`skills/daily-standup`、`skills/notion-archive`），见 [SKILLS.md](SKILLS.md) |
| `notify.py` | IM 通知（飞书/钉钉/企微/Telegram/通用 webhook），fail-open + 冷却去重 + jsonl 审计，见 [NOTIFY.md](NOTIFY.md) |
| `cache.py` | 设计书/计划结果缓存（`data/design_cache.json`、`plan_cache.json`） |
| `plugin_system.py` + `plugins/` | 插件系统（示例 `example_json_tool.py`） |
| `knowledge/injection.py` | 知识注入（`data/knowledge/`） |
| `logging_config.py`、`utils/usage_tracker.py`、`utils/interfaces.py` | 日志、token 用量、接口定义 |

## 前端（`frontend/src/`）

- **壳**：`App.tsx`（视图切换、WS 事件、主题）、`components/Sidebar.tsx`、`LogPanel.tsx`、`AiPanel.tsx`、`ToastHub.tsx`、`ApprovalBanner.tsx`
- **8 个主视图**（`components/`）：InputView 新建任务 / DesignView 设计书审阅 / PlanView 执行计划 / ExecutionView 实时执行 / ToolsView 工具库 / HistoryView 历史 / AuditView 审计 / ConfigView 设置
- **基础设施**：`api/client.ts`（REST + WS + token）、`stores/appStore.ts`（Zustand，持久化 currentView/inputText/currentPlan）、`hooks/usePlanPolling.ts`、`types/index.ts`
- 端口 3000，Vite 将 `/api` 与 `/ws` 代理到 8000；`npm test`（Vitest，36 passed）

## Rust（`rust/`，未接入主链路）

workspace crates：`common`（error/traits/types）、`dispatcher`、`db`、`logger`、`tool_manager`、`server`、`gui`（egui 风格桌面端，含 api/ws/state/theme）。接管运行时的边界契约：[runtime-boundary.md](runtime-boundary.md) + `schemas/`。

## 契约与配置

| 位置 | 内容 |
|---|---|
| `schemas/` | `design_document.json`、`execution_plan.json`、`tool_schema.json` —— 跨语言契约 |
| `config/models.yaml` | Provider/base_url/模型分配（codegen/designer/executor/knowledge/vision/tts/asr…）、Grant、MCP、skills、notify、chat、budget |
| `config/models.local.yaml`（gitignore） | 真实密钥覆盖 |
| `config/server.yaml` / `database.yaml` | 服务端口（历史遗留，实际 8000/3000）/ SQLite 路径 |
| `.env` / `.env.example` | `EAS_API_TOKEN`、`EAS_LLM_*` 凭据 |
| `pytest.ini` | testpaths=tests/，markers：integration、browser |

## API 端点索引（`/api/*` 均需 token）

设计书：`POST /api/design`、`GET /api/designs`、`GET/DELETE /api/design/{id}`、`POST /api/design/{id}/approve`
计划：`GET /api/plans`、`GET /api/plan/{id}`、`POST /api/plan/{id}/execute|pause|resume|archive|rename|rollback`、`DELETE /api/plan/{id}`、`POST .../skip-step/{step_id}`、`POST/GET .../approval/.../approve|reject`
运行：`GET /api/run/{id}`、`GET /api/plan/{id}/runs`、`GET /api/run/{id}/attempts`
其他：`/api/status`、`/api/tools`、`/api/capabilities`、`/api/audit-logs`、`/api/usage`、`/api/chat`、`/api/skills`、`/api/notify/test`、`/api/config` GET/POST、`/api/restart`、WS `/ws/logs`
详细参考：[API.md](API.md)；错误契约（改错误路径前必读）：[ERROR_HANDLING.md](ERROR_HANDLING.md)

## 测试索引（`tests/`，76 文件 / ~452 用例，从仓库根运行 pytest）

- `tests/python/`：单元测试为主——plan 生命周期/状态机/checkpoint/恢复/回滚（test_plan_state_domain、test_checkpoint_recovery、test_pause_resume_retry、test_rollback_branching、test_orphan_recovery…）、工具安全（command_runner/github/http/web_fetcher/package_installer…）、Designer/Executor/审批/API/授权/MCP/skills/notify/cache/usage 各模块
- `tests/integration/` + markers `integration`/`browser`：默认 CI 排除；离线基线 `pytest -m "not integration and not browser"`（635 passed / 9 skipped）
- 真实 LLM 验收：`scripts/real_llm_acceptance_a.py`（A 标准五任务，证据 `out/real-llm-acceptance-a.json`）；验收记录 [checkpoint-recovery-acceptance-2026-09-09.md](checkpoint-recovery-acceptance-2026-09-09.md)
- 前端：各 `*.test.tsx` + `client.test.ts`；Rust：`tests/rust/` + cargo（CI）

## 脚本索引（`scripts/`）

启动：`start_api.py`（后端 8000）、`dev_server.py`、`cli.py`（CLI 设计助手）、`harness.py`、`daily_ready.py`、`dev.bat/.sh`、`init.bat/.sh`
验收/基准：`live_checkpoint_acceptance.py`、`real_llm_acceptance_a.py`、`performance_benchmark.py`（结果 `artifacts/`）
运维：`prune_data.py`（数据保留清理）、`memory_admin.py`（项目记忆运维：stats / keep / dedupe，默认 dry-run，`--apply` 才写入并在写入前自动备份）
`test_*.py` 系列：连通性/E2E/覆盖率/专项的独立手动验证脚本（不属于 pytest 套件）
Windows 根目录一键批处理：`启动完整系统.bat`、`一键启动.bat`、`启动后端.bat` 等

## 数据与运行产物

| 位置 | 内容 |
|---|---|
| `data/eas.db` | 主 SQLite 库；`data/knowledge/` 知识笔记；`data/usage/` 用量；`data/notifications.jsonl` 通知审计；`data/reports/` 报告；SQLite `artifacts` 表登记产物 URI/哈希/元数据；`steps.depends_on` 存显式依赖；资源声明随步骤 `inputs.resources` 持久化 |
| `out/` | 真实验收/每日任务的产物与服务器日志 |
| `logs/eas.log`、`htmlcov/`、`.coverage` | 运行日志、覆盖率报告 |
| `PROJECT_MEMORY.md`、`PROJECT_MEMORY-archive.md` | project_memory 工具的落地记忆；超限时按**整条边界**把最旧条目滚动进归档文件（同一工作区根）。容量双限且可运行时调整：`EAS_PROJECT_MEMORY_MAX_ENTRIES`（默认 60 条）/ `EAS_PROJECT_MEMORY_MAX_CHARS`（默认 20000）、归档 `EAS_PROJECT_MEMORY_ARCHIVE_MAX_ENTRIES`（默认 2000 条）/ `EAS_PROJECT_MEMORY_ARCHIVE_MAX_CHARS`（默认 200000），非法值回落默认并告警。2026-09-19 人工整理：清除 105 条测试噪声（94 条确定性测试写入 + 11 条合成 fixture 验收），原文备份 `docs/archive/PROJECT_MEMORY-20260919-pre-cleanup.md` |

## 历史/独立子项目（不在主链路内）

- `ai_design_system/`、`ai_free_trial_search/`：早期独立原型（各自 main.py + 模块），仅作参考
- `docs/archive/eas_2_0_ui_platform.tsx`：早期单文件 UI 原型（2026-09-12 由根目录归档）
- `docs/archive/`：历史测试报告、旧指南与调试残留，**不代表现状**
- 根目录杂项归档完成（2026-09-12，STATUS 短板#5 关闭）：`approve*.json`/`exec_body.json`/`reject_body.json`（API 调试快照）、`a.txt`/`test.py`/`out.html`（实验残留）、`python/check_*.py`（0 引用的临检脚本）、`python/api/server.py.bak`（拆分前备份）已 `git mv` 至 `docs/archive/`；`.coverage`（可再生）直接删除；`scripts/check_env.py` 有引用予以保留

## 文档索引（docs/）

| 文档 | 主题 |
|---|---|
| STATUS.md | 当前完成度、验证基线、已知短板 |
| STRUCTURE.md / runtime-boundary.md | 目录边界 / Rust-Python 运行时边界 |
| API.md / ERROR_HANDLING.md | API 参考 / 错误契约 |
| DEVELOPMENT.md | 开发环境与测试指南 |
| state-machine-design.md / TOOL_INTERFACE_DESIGN.md | 计划状态机 / 工具契约 |
| SUPER_SEARCH.md / CODE_CONTEXT.md / SKILLS.md / NOTIFY.md | 两个大工具 + skills + 通知 |
| AGENT_HARNESS_DESIGN.md / NEW_ARCHITECTURE_DESIGN.md / HARDENING_2026-08-30.md | 设计与加固（历史参考） |
| plan-cache-fk-bugfix-2026-09-10.md / daily-task-trials-2026-09-09.md / checkpoint-recovery-acceptance-2026-09-09.md | 近期修复与验收记录 |

## 关键约束（改代码前须知）

1. pytest 必须从**仓库根目录**运行（conftest 导入约定）。
2. 凭据只从环境变量/密钥服务或 gitignore 的 `models.local.yaml` 读取，不提交。
3. HTTP 出站仅 http/https，拒绝 localhost/环回/私有/保留地址；SQL 一律参数绑定。
4. **WorkGraph 第 1-3 阶段**：`Step.depends_on` + DAG fail-closed 校验 + 依赖感知取步（1.15）；`Step.resources` 资源冲突检测 + `select_next_batch` 批取步 + 有界并发（1.16）；worktree 隔离（`git_operator` worktree 指令组、`worktrees` 表、API 合并/丢弃，1.17）；多计划并行（contextvar 工作区隔离，worktree 计划不占主执行权）+ AgentSession/Turn 记录（`agent_sessions`/`agent_turns`）+ Artifact 内容层（SHA-256 落盘 + `/api/artifacts`）+ 动态重规划（`POST /api/plan/{id}/replan`）+ review/diagnosis agent + 最终交付报告（1.18）已落地。批并行默认关闭（`parallel_graphs=false`）；任务模式识别（直接工具模式）与前端 WorkGraph 可视化仍未实现。
5. 工具必须返回**结构化失败**，不得把业务失败冒充成功。
6. CI 三个 job：Python 离线回归、前端 test+build、Rust workspace test（本机无 cargo 时不声称已验证）。
