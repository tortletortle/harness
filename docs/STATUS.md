# EAS 2.0 项目状态

> 本文记录当前实现状态（2026-09-17）。历史测试报告以 archive/ 中的日期版本为准。

## 一句话现状

EAS 2.0 是面向个人本地开发助手的任务编排与执行系统：需求 → Designer 生成设计书 → 人工审批 → 执行计划 → Executor 分步调用工具 → SQLite 持久化与 React 控制台展示。

当前 Python/FastAPI 是生产任务执行的唯一权威运行时；Rust workspace 已产品化为桌面控制台、契约校验/工具伴随服务及迁移基础，不重复实现或伪装成第二套完整 Executor。

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
- 默认离线 Python 回归：802 passed / 8 skipped（2026-09-12，含新增工具契约 34 + llm_provider 24 + designer 覆盖 31 + ssrf 重定向 7 用例；证据 `out/pytest-after-mimosa.log`）。
- 覆盖率补齐（2026-09-12）：`python/designer/__init__.py` 60%→100%（401/401）、`python/llm_client/__init__.py` 47%→100%（162/162），全仓 TOTAL 66%→70%；0% 纯脚本与 browser_check 判定为有意不测/不管。证据 `out/pytest-full-after-cov.log`（该次全量+覆盖跑法下 2 个 e2e 因建服超时 error，单跑复核 2 passed，属环境抖动与本次改动无关；证据 `out/pytest-e2e-rerun.log`）。
- 真实浏览器（Playwright Chromium）检查：11 passed。
- Mimosa 深度扫描复核完成（2026-09-12）：台账 8 findings——6 项 static_fix_verified（git_operator：list-form 无 shell=True；acceptance_b×5：_validate_base 本机 allowlist；机器复扫+人工抽核）；2 项 HIGH 路径穿越（misc_routes:233/shared.py:157）判误报：路径全由服务端固定拼接（_config_dir()/__file__+固定文件名），零请求输入，routes 开槽审计仅 3 处 open 全安全。另补真实残留：urllib 重定向链不复检（302 可跳内网），新增 _SafeRedirectHandler（web_fetcher+super_search 共用）+7 用例；证据 `out/pytest-ssrf.log`；browser_check 页内跳转与 DNS-rebinding 列为接受残留。
- Rust 可执行测试（2026-09-17）：七个 workspace 测试程序全部通过，共 **36 passed / 0 failed**（GUI 31，DB/Dispatcher/Logger/Server/Tool Manager 各 1，Common 0）。


## 检查点恢复加固（2026-09-13）

- 检查点不再绕过当前分支、步骤状态与依赖校验：先调度已就绪的必要前置，再执行恢复目标；非法/缺失/循环/失败依赖明确阻塞，已完成目标不重跑。
- 原恢复目标保留至真正被选中或已完成；修复前置批次、暂停接口、取消及预算暂停导致 Failed 恢复目标丢失的问题。清空检查点失败不启动工具，错误收尾不再伪装 Completed。
- 新增 51 项临时 SQLite/工具桩回归；最终源码副本离线验证 **1004 passed / 9 skipped / 2 deselected**（188.91s）。初次全量收集因外部工具目录未配置报错，已在验证副本复制外部工具源码并设置独立 EAS_TOOLS_DIR 后重跑，不修改这些既有测试。
- 原有业务行只读指纹复核通过；验证期间另一桌面预览验收计划新增的数据完整保留。两个旧计划未执行/归档；未重启既有后台，不把源码验证宣称为运行进程已加载新代码。
- 详细语义、证据与边界：`out/checkpoint-fix-20260913/CHECKPOINT_FIX_COMPLETED.md`、`out/checkpoint-verification-full-20260913-181933/`。

## 模型配置界面完善（2026-09-13）

- 模型工作台新增角色/提供商概览、搜索、主/备用模型编辑、响应式卡片、未保存及冲突保护；模型 ID 不再受硬编码列表限制。
- 修复旧页读取 agents 而运行时使用 models、模型分配未提交、密钥输入被后台忽略以及保存后盲目调用退出接口的问题。新版配置 API 带 schema 2、revision 与待重启提示，原子写本地覆盖，保留高级参数和当前运行配置。
- API Key 支持环境变量注入或页面单独 DPAPI 加密保存；保存后仍需手动重启后台，未自动执行真实上游推理。只读确认既有后台返回 schema 2 和 12 个模型角色，受保护配置文件哈希全部保持不变。
- 验证：前端 **76 passed**、TypeScript/生产构建通过；源码副本 Python **1031 passed / 9 skipped / 2 deselected**，环境目录漏项另测 **1 passed**；隔离浏览器桌面/窄屏、键盘与模拟保存通过。新增 27 项后端及 15 项前端/客户端测试。
- 使用与边界：`docs/MODEL_CONFIGURATION_UI.md`；证据：`out/model-config-20260913/`、`out/model-config-verification-full-20260913-185028/`。交互预览是内存演示，不是已经写入真实配置的证明。

## 模型配置明暗主题与上游诊断修正（2026-09-13）

- 修复模型配置页与真实 App 亮色主题混用的问题；语义色和容器响应式布局覆盖亮/暗主题、模型/提供商页及窄内容区。首轮孤立深色预览不足以验收，本次改用完整 App 外壳验证。
- 新增认证 `POST /api/config/probe`：独立 GET 模型列表与明确费用确认后的最小文本推理。无自动请求/重试；20 秒总超时、响应体上限、重定向阻止、结果分类与旧后台能力门控。列表/HTTP 可达不冒充模型推理通过，文本测试不证明多模态/工具能力。
- 前端 **85 passed / 15 files** + TypeScript/生产构建通过；脱敏源码副本 Python **1060 passed / 9 skipped / 2 deselected**。新增 29 项后端、9 项前端测试；模拟传输，不访问真实上游。
- 原配置、密钥及前端环境文件共 5 项哈希未变。用户同意空闲时受控启用后，复核发现后台进程已更新，GET config 返回 **probe_version 1 / 12 roles**，无需重复重启；未执行历史任务或真实模型检测。
- 详见 `docs/MODEL_CONFIGURATION_UI.md`、`out/model-probes-20260913/` 与 `out/model-probes-verification-full-20260913-191646/`。

## 已保存中文提供商修复与真实连通性复测（2026-09-13）

- 已保存中文提供商支持检测及原样保存，不再出现“国模”本地 422 或其他配置被保存校验阻断；新建标识规则、已保存端点白名单与控制字符限制保持。
- 当前后台已加载修复。仅对“国模”复测一次 GET 模型列表：HTTP 401 / 约 1.47 秒，说明已到达上游，下一步需要正确注入密钥；未发起模型推理。
- 定向后端 **100 passed**；全量脱敏回归 **1077 passed / 9 skipped / 2 deselected**；前端 **86 passed**，TypeScript/构建通过。新增 17 个后端与 1 个前端回归用例。原配置及密钥未修改；全量证据 `out/model-probes-verification-full-20260913-200034/`。
- hejuapi 的 `/v1/models` 404 尚不能确定为地址错误，公开资料不足以确认替代地址，未猜测改写。参见 `docs/MODEL_CONFIGURATION_UI.md` 与 `out/upstream-connectivity-20260913-200100.json`。

## 模型密钥页面录入与加密保存（2026-09-16）

- 模型配置页已支持直接填写提供商 API Key；后台用 Windows DPAPI 加密保存到 `config/keys.enc.json`，文件已加入 `.gitignore`，接口和日志不回显明文。
- 环境变量 `EAS_LLM_API_KEY_<提供商大写标识>` 继续优先；页面显示环境变量/本地加密保存状态，支持单提供商保存与清除，保存不会丢弃其他未保存草稿。
- 新增 `POST /api/config/key`，旧配置接口提交 `api_keys` 仍明确拒绝；配置读取、上游探测和服务启动均使用相同的环境变量优先级。
- 非 Windows 平台不写明文降级，保存明确失败并继续支持环境变量；加密文件损坏只显示未配置。
- 验证：Python 离线回归 **1120 passed / 9 skipped / 2 deselected**；前端 **87 passed / 15 files**；TypeScript 与生产构建通过。

## 已知短板

1. ~~`python/api/server.py` 仍然是大型协调入口~~ **已拆分（两轮，2026-09-12）**：
   - 第一轮：状态/广播/格式化进 `api/shared.py`，HTTP 路由按领域进 `api/routes/`（design/plan/approval/artifact/agent_session/worktree/quick_task/replan/misc 九个模块，`app.include_router` 装配）；server.py 3242 → 1643 行。
   - 第二轮：执行运行时拆到 `python/api/runtime/`（step_execution.py / artifacts.py / background.py），server.py 1643 → 545 行（净减 1098 行）。
   - 兼容 re-export：所有 runtime 符号仍可从 `api.server` 导入（`from api.server import execute_plan_background` 等），单步状态机调用通过 `api.server` 间接解析，测试 `monkeypatch.setattr(server_mod, "_execute_step_with_state_machine", ...)` 路径保持兼容，零测试改动。
   - 顺手清理：删除 `_is_retryable_exit_code_acceptance` 函数尾部的孤儿 docstring + if/raise（永不执行的死代码）；删除与 `api/shared.py` 重复的 `format_plan_for_frontend` 定义，统一以 shared.py 为准。
2. ~~AgentSession/Run/Turn/Attempt 的完整持久化分层尚未完成~~ Session/Turn 已落地（2026-09-12，含前端视图）；Turn 级上下文压缩与跨会话恢复仍为后续。
3. 覆盖率分布不均，Designer、LLM client 和部分入口代码较薄。
4. Rust 不拥有完整生产执行链；Python/FastAPI 仍是权威运行时。迁移必须继续遵守 `docs/runtime-boundary.md` 并以跨语言兼容测试证明，不得双写状态。
5. ~~根目录存在历史脚本、实验目录和运行残留，需先分类再决定归档，不直接删除未知文件~~ **已归档（2026-09-12）**：14 个跟踪文件 `git mv` 至 `docs/archive/`（根目录 9 个调试残留 + `eas_2_0_ui_platform.tsx` 早期原型 + `python/check_*.py` 3 个 0 引用临检脚本 + `server.py.bak` 拆分备份），`.coverage` 删除，`scripts/check_env.py` 有引用保留；归档清单见 `docs/archive/README.md`。
6. ~~工具契约“工具返回结构化失败而非把业务失败冒充成功”有文档和测试约束，但仍需逐工具验证异常路径~~ **已验证（2026-09-12）**：新增 `tests/python/test_tool_contract.py`（34 用例：未知指令/缺参数/错误类型/None 输入逐工具覆盖）；修复 5 类问题——project_memory 非法 limit 抛 ValueError、git worktree 组 `_run_git` 未捕获（Windows NotADirectoryError 穿透）、file_creator 空 path 谎报成功、`resolve_workspace_path` 非字符串 TypeError、7 个工具缺 None-guard。
7. 前端已覆盖 WorkGraph 依赖视图、产物库与 Agent 记录、quick-task 直达入口（2026-09-12：新建任务页「快速执行」按钮 + 内联结果展示，见 InputView.tsx）。

## 后续原则

- 面向个人本地使用，优先稳定性、可观测性和日常工具覆盖，不提前引入多用户/多租户复杂度。
- 第一阶段不修改数据库 schema，不引入 outbox；先稳定 Repository、Application Service 和状态机边界。
- 服务端 HTTP 请求仅允许 http/https，并在请求前拒绝 localhost、环回、私有及保留地址。
- 所有 SQL 外部输入必须使用参数绑定，禁止字符串拼接。
- 所有凭据只从环境变量或账户绑定的加密密钥存储读取；本地覆盖文件与 `config/keys.enc.json` 不得提交到 Git。

7. Full-suite phantom failures (unknown_instruction/render) + root artifact regeneration **resolved (2026-09-13)**: external sibling-repo tool progress_view.py violates TOOL_INTERFACE_DESIGN 3.3/3.5 (failure branch lacks message key), auto-loaded via load_tools_from_dir; harness-side hermeticity fixes in test_tool_contract.py (scope to EXPECTED_TOOL_IDS) and test_instruction_inference.py (EAS_WORKSPACE_ROOT=tmp); suite now 824 passed, 8 skipped.

8. Rust workspace builds green for the first time (2026-09-13): rustup stable 1.98.1 via Tsinghua mirror (msvc host + gnu toolchains, auto-self-update disabled, no MSVC/SDK on machine), linked with zig 0.16 via cargo-zigbuild; all 7 crates OK (eas-common/logger/tool-manager/dispatcher/db/server + eas-gui); warnings only, non-blocking (gui 32, db 2, server 2, tool-manager 1); evidence out/cargo-gnu3.log + out/cargo-gui.log.

9. Rust hardening increment (2026-09-17): common 状态枚举与 runtime-boundary 对齐；SQLite plan/step 事务持久化、读取与更新回读落地；Dispatcher 增加完成态不变量、ID/order 校验和路由输入防护；Tool Manager 增加 schema 校验、稳定排序和实例配额；GUI 增加 Ctrl/Cmd+K 快捷操作中心、工作流切换、上下文刷新及 WS 鉴权断开识别。最初受本机链接器缺失影响，最终已用 Zig GNU 工具链完成全部链接与运行验证。

10. Rust productization increment (2026-09-17): Rust server 改为本地安全默认绑定，统一结构化错误，受保护端点启用 Bearer/X-API-Token 常数时间校验、受限 CORS、环境配置和优雅退出；GUI 支持从环境或 `config/api_token` 自动加载凭据并设置最小可用窗口；补充 Windows/Unix 构建脚本、三平台 release workflow、CI 桌面依赖与 all-target 检查，交付边界和验收流程见 `docs/RUST_PRODUCT.md`。

11. Rust 最终产品验证（2026-09-17）：rustfmt、严格 Clippy（`-D warnings`）、workspace all-target、YAML、diff check 与编辑器诊断全部通过；七个已链接测试程序 **36 passed / 0 failed**；Windows GNU release 产物 `eas-server.exe`（2,102,272 bytes）和最终 `eas-gui.exe`（6,384,128 bytes）构建成功。冒烟验证覆盖服务健康检查、未认证 401、Bearer/X-API-Token 认证 200、缺失 Token 明确拒绝启动，以及 GUI 在后端不可用时仍能启动并进入恢复界面。真实桌面截图复核进一步发现并修复了普通工作区被设置页错误覆盖、日志分支与确认窗重复渲染的问题；修复后计划详情、快捷操作、服务状态、项目列表和错误状态均实际显示，GUI 31 项测试与严格 Clippy 重跑通过。Zig 0.13 链接器的 libunwind/deprecated optimization 提示和 `sqlx-postgres 0.7.4` future-incompatibility 报告来自第三方工具链/依赖，不是 workspace Clippy 或 Rust 源码告警。

12. Workbench UX 重构（2026-09-17）：根据真实桌面截图复核结果，将 GUI 重组为 VS Code 风格的紧凑标题/命令中心、固定 Activity Bar、上下文侧栏、中央工作区、受限宽度 AI 辅助侧栏、独立底部输出区和固定状态栏。工具箱从设置中移出，成为可搜索的一级工作区；Ctrl/Cmd+K 改为支持输入过滤、上下键选择、Enter 执行与 Esc 关闭的命令面板；AI 助手取消易误触的全屏入口。最终 GUI 严格 Clippy 通过，31 项 GUI 测试通过，Windows GNU release 为 6,402,048 bytes，SHA-256 `E9E60E973F9621B6EA4CDAC143DCA6DAFF5FF5C1680896898577243BAA51C78C`，启动 5 秒存活冒烟通过。
