# EAS Agent Harness 设计稿

> 这是 EAS Agent Harness 的唯一主设计稿。
> 后续源码分析、架构决策和实现调整都必须修改本文件，不另建平行方案。

## 版本

- 当前版本：`1.1-draft`
- 状态：初版，已通过五个成熟开源项目源码分析验证；P0-P2、AcceptanceRunner 第一阶段与 Runtime Grant 第一阶段已实施；工具选型评测达标；worktree 路线已定
- 最后更新：2026-08-29

## 1. 目标

为 EAS 建立一个工具优先的 AI 软件工程工作台：

- 用户提出目标，系统生成可执行任务图。
- 普通工具和专业 Agent 使用统一的 Capability 接口。
- Agent 之间保持平行关系，默认禁止创建子 Agent。
- 系统携带临时授权，Agent 不持有永久权限。
- 每个任务有明确资源、预算和验收标准。
- 简单任务低成本快速完成，复杂任务才使用任务图和强模型。
- 所有执行过程可观察、可暂停、可恢复、可审计。

## 2. 核心原则

1. **工具优先**：能用确定性工具完成的工作，不调用 Agent。
2. **Agent 即工具**：Agent 是一种高级 Capability，不是组织层级中的下属。
3. **平行协作**：Agent 之间不互相创建、不进行长篇转述，通过结构化产物协作。
4. **系统授权**：授权由 Runtime 在调用时签发和校验，过期或撤销后立即失效。
5. **资源优先**：读操作可以并行；同一资源的写操作必须串行或经过合并控制。
6. **独立验收**：Agent 不能自我宣布成功，结果必须由验收器或人工确认。
7. **有限动态性**：允许局部重规划，但限制循环、预算、调用次数和图版本。
8. **最小充分上下文**：只传递完成当前工作所需的数据和产物引用。

## 3. 总体架构

```text
用户目标
   ↓
Designer / Graph Planner
   ↓ 生成 WorkGraph
Graph Validator
   ↓ 校验图、依赖、预算、资源和验收
Policy & Authorization
   ↓ 签发临时 Grant
Task Runtime
   ├── 调度
   ├── 并行控制
   ├── 资源锁
   ├── 超时/取消/重试
   └── 事件记录
        ↓
Capability Registry
   ├── 普通工具：read/search/edit/run/test/git
   ├── Agent Tool：coding/review/diagnosis
   └── Human Tool：approval/input
        ↓
Workspace / Artifact Store / Event Store
        ↓
Acceptance Runner
        ↓
交付报告
```

## 4. 任务模式

不是所有请求都生成动态图：

### 4.1 直接工具模式

适合读取文件、搜索字符串、运行测试、查看 Git 状态等简单任务。

```text
目标 → 工具 → 验证 → 结果
```

### 4.2 轻量图模式（默认）

适合普通开发任务。

```text
搜索 → 修改 → 测试 → 必要时有限修复 → 审查
```

### 4.3 深度图模式

适合跨模块重构、复杂调试、部署和高风险任务。

启用人工审批、更多检查节点和更严格预算。

## 5. WorkGraph 初步模型

```json
{
  "graph_id": "fix-auth",
  "version": 1,
  "goal": "修复认证问题并通过测试",
  "nodes": [
    {
      "id": "inspect",
      "kind": "tool",
      "executor": "code_search",
      "depends_on": [],
      "resources": {"read": ["workspace/**"], "write": []},
      "budget": {"max_calls": 5},
      "acceptance": [{"type": "artifact_exists"}]
    },
    {
      "id": "implement",
      "kind": "agent_tool",
      "executor": "coding_agent",
      "depends_on": ["inspect"],
      "resources": {
        "read": ["workspace/python/api/auth.py"],
        "write": ["workspace/python/api/auth.py"]
      },
      "budget": {"max_model_calls": 3, "max_tokens": 5000},
      "acceptance": [{"type": "diff_scope_valid"}]
    },
    {
      "id": "verify",
      "kind": "tool",
      "executor": "test_runner",
      "depends_on": ["implement"],
      "resources": {"read": ["workspace/**"], "write": []},
      "acceptance": [{"type": "command_exit_code", "equals": 0}]
    }
  ],
  "limits": {
    "max_nodes": 20,
    "max_replans": 3,
    "max_elapsed_seconds": 600
  }
}
```

## 6. Capability / Agent Tool 契约

普通工具和 Agent 工具统一实现以下概念：

```text
Capability
  id / version
  kind: deterministic_tool | agent_tool | human_tool
  input_schema
  output_schema
  required_permissions
  resource_spec
  budget_spec
  acceptance_spec
  side_effect
  idempotent
```

Agent Tool 只接收一个受限 WorkItem：

- 目标摘要
- 必要输入和 artifact 引用
- 可访问资源
- 可使用工具
- 时间、Token、调用次数预算
- 验收标准

默认策略：`can_spawn_agents = false`。

## 7. 授权模型

授权由 Runtime 临时签发，至少绑定：

```text
grant_id
work_item_id
subject
allowed_capabilities
allowed_resources
budget
expires_at
approval_policy
```

每次调用都重新检查：

```text
Grant 是否有效
工具是否允许
资源是否匹配
参数是否符合 Schema
预算是否足够
是否需要人工审批
```

高风险操作（删除、推送、部署、外部发送、依赖安装）默认需要审批。

## 8. 状态和产物

### 8.1 运行对象

```text
Task
WorkGraph
WorkItem
Run
Attempt
Grant
Event
Artifact
Approval
```

### 8.2 Agent 间协作

禁止传递完整思考过程，使用不可变产物引用：

```json
{
  "status": "completed",
  "artifacts_created": ["artifact://search-result-v1"],
  "facts_added": [{"key": "auth_entry", "value": "python/api/auth.py:42"}],
  "acceptance_evidence": []
}
```

### 8.3 初步状态

```text
pending → running → waiting_approval → running
                  ├→ retrying
                  ├→ paused
                  ├→ failed
                  ├→ cancelled
                  └→ succeeded
```

## 9. 速度和成本指标

每次 Run 记录：

```text
模型调用次数
输入/输出 Token
工具调用次数
模型耗时
工具耗时
锁等待时间
排队时间
重试次数
首次有效结果时间
最终完成时间
```

关键指标：

```text
Time to First Useful Result
Time to Final Completion
Token Efficiency
Useful Tool Ratio
Redundant Call Rate
Parallel Gain
Acceptance Rate
```

速度原则：

- 只读节点可并行。
- 写节点按资源串行。
- 测试在写入完成后运行。
- 失败只进行有限局部修复。
- 简单任务不生成复杂图。

## 10. 与成熟项目的对照表

| 设计模块 | 首要参考项目 | 待验证内容 |
|---|---|---|
| Agent Loop | Aider、mini-SWE-agent、OpenCode | 调用循环、停止条件、反馈方式 |
| 工具契约 | Goose、MCP SDK、PydanticAI | 注册、Schema、扩展和错误 |
| 授权/沙箱 | Codex CLI、Cline、OpenHands | 权限、工作区、命令隔离 |
| 上下文 | Aider、Continue、Cline | 相关文件选择、压缩、预算 |
| 任务图 | LangGraph、Plandex | 节点、状态、重规划、恢复 |
| 事件/运行时 | OpenHands、LangGraph | 事件流、Run、Artifact |
| 验收 | SWE-agent、Inspect AI | 场景、评分、测试证据 |
| UI/审批 | Cline、OpenHands | 确认、diff、过程和结果展示 |

## 11. 当前 EAS 映射

| 现有模块 | 初步职责 | 后续处理 |
|---|---|---|
| `python/designer` | 生成 WorkGraph | 对照 LangGraph/Plandex 验证 |
| `python/dispatcher` | Capability 路由与策略检查 | 避免再次成为重型 Planner |
| `python/executor` | 单次 Capability 执行 | 对照 Aider/Codex Loop 拆分 |
| `python/tools` | 普通工具实现 | 统一 Tool/Agent 契约 |
| `python/database.py` | Run、状态、事件、产物索引 | 对照 OpenHands/LangGraph |
| `frontend` | 图预览、审批、过程和交付 | 对照 Cline/OpenHands |
| `rust/gui` | 桌面原生控制台（egui） | 同一 Python 后端，见第 20 节 |

## 12. 待验证问题

源码拆解时只更新本设计稿，不新建方案。重点回答：

1. Aider、Goose、Codex CLI 的 Agent Loop 能否直接映射到 EAS？
2. 哪个项目的工具授权模型最适合当前 Python 工具体系？
3. Agent Tool 是否应该采用 MCP 协议，还是保留内部接口并提供 MCP 适配器？
4. WorkGraph 是自研最小格式，还是采用 LangGraph/PydanticAI 的部分实现？
5. 现有 `step_events` 是否能演化为统一事件流？
6. 哪些现有 Designer/Dispatcher/Executor 逻辑应保留、拆分或删除？
7. 单个写 Agent 的资源锁和 workspace 版本如何实现？
8. 哪些验收器可以完全确定性执行？

## 13. 源码拆解验证：Aider

参考仓库：<https://github.com/Aider-AI/aider>

关键路径：

- `aider/coders/base_coder.py`：`Coder.run()`、`run_one()`、`send()`、`parse_response()`，形成“请求模型 → 解析编辑 → 应用修改 → 将结果反馈给模型”的循环。
- `aider/coders/editblock_coder.py`：`apply_edits()` 和 SEARCH/REPLACE 编辑协议，编辑失败会生成诊断并反馈给下一轮。
- `aider/repomap.py`：`RepoMap` 通过符号定义/引用和重要性排序选择相关上下文，并受 token 预算限制。
- `aider/repo.py`：`GitRepo` 提供 dirty files、diff、tracked files、HEAD 和 commit 能力。
- `aider/commands.py`：测试、lint、shell 和文件上下文管理都作为明确的命令入口。
- `aider/models.py`：模型上下文窗口、token/费用统计和请求封装。

### 已验证并保留的设计

1. EAS 需要独立的 `AgentSession/AgentLoop`，不能把循环塞进单步 `Executor`。
2. 上下文必须由 `ContextManager` 按相关文件和符号选择，不能每轮发送整个仓库。
3. 编辑应先解析为结构化 `EditOperation`，支持 dry-run、diff、匹配校验和写入后确认。
4. 测试和 lint 结果必须作为结构化反馈回到下一轮，而不是只返回最终文本。
5. Git 状态、变更文件和 diff 是验收输入，应补齐现有 `git_operator` 的只读查询能力。
6. Session 级预算必须独立记录模型调用、输入/输出 token、工具调用、修复轮数和耗时。

### 不直接复用的部分

- Aider 的 SEARCH/REPLACE 文本协议不能作为唯一编辑协议；EAS 保留结构化工具调用，并可提供该协议适配器。
- Aider 主要是单 Agent 交互循环，不能直接解决 EAS 的平行 WorkGraph、临时授权和资源锁。
- Aider 的命令权限不能直接视为安全边界；EAS 必须由 Runtime 在每次调用前执行授权和资源检查。

### 对 EAS 的首个实现影响

```text
AgentSession
  → ContextManager
  → LLMClient
  → ResponseParser
  → EditManager
  → DefaultExecutor/ToolRegistry
  → Test/Git feedback
  → Budget + StopPolicy
```

这确认了设计稿中的“工具优先 + Agent 即工具”方向，但将 Agent Loop 放在 Runtime 之上、工具执行放在 Runtime 之下。

## 14. 源码拆解验证：Goose

参考仓库：<https://github.com/block/goose>

关键路径：

- `crates/goose-agent/src/machine.rs`：`StateMachine::step/run`、`SessionLoader` 和 `EffectHandler`，通过持久 Session 驱动可继续的 Agent 循环。
- `crates/goose-agent/src/operation.rs`：`Operation`、`Inference`、`OperationResult`、`ConversationEffect`，把模型推理、工具请求和状态效果分离。
- `crates/goose-agent/src/inference.rs`、`events.rs`：Provider 输入、工具结果、事件发布和取消。
- `crates/goose-mcp/src/lib.rs`：内置 Extension 注册和 MCP Server 启动。
- `crates/goose-context-management/`：上下文格式化、容量判断、压缩和摘要。
- `crates/goose-provider-types/src/conversation/`：ToolRequest、ToolResult、token usage 和消息模型。
- `crates/goose-cli/src/session/elicitation.rs`：审批/用户输入等 ActionRequired 的客户端交互。

### 已验证并保留的设计

1. Agent Loop 应成为独立的 `AgentSession`，由 Session 状态驱动，而非只执行一个静态 Step。
2. 模型响应、工具请求、审批请求和最终回答必须使用不同的结构化结果类型。
3. 工具执行结果应回写 Conversation/Task State，成为下一轮模型输入。
4. 审批、用户输入、等待外部事件和取消必须是正式的控制流状态。
5. `ContextManager` 应独立负责上下文容量、压缩和结构化消息保留；长期 Memory 与当前上下文压缩分开。
6. 内置工具可继续使用本地 Registry，MCP 更适合作为外部 Extension 适配层。
7. Event、Effect、Session State 和 UI/审计应保持关联，不能只保存最终文本。

### 不直接复用的部分

- Goose 的 Rust StateMachine 不能直接替换当前 Python 服务；先吸收其 Session、Effect 和 Operation 边界。
- Goose 的 MCP Extension 不意味着所有内部工具都必须改成独立 MCP Server。
- `ActionRequired` 的交互语义可以借鉴，但 EAS 的 Grant、资源锁和预算仍需由自己的 Runtime 强制执行。

### 对 EAS 的首个实现影响

```text
AgentSession
  → load Session/Conversation
  → ContextManager
  → LLMClient
  → parse final/tool/approval/input
  → Runtime policy + temporary Grant
  → ToolRegistry/MCP Extension
  → append ToolResult + Event + Usage
  → continue, yield, pause, cancel, or finish
```

Goose 强化了设计稿中“Agent 即工具”的边界：Agent 可以提供工具请求，但不能直接改变 Session；所有状态改变必须通过 Runtime 的效果处理器应用。

## 15. 源码拆解验证：Codex CLI

参考仓库：<https://github.com/openai/codex>

关键路径：

- `codex-rs/core/src/session/turn.rs`、`session.rs`：Session、Turn、StepContext、工具调用循环、上下文压缩、取消和停止。
- `codex-rs/core/src/session/context_window.rs`、`token_budget.rs`：上下文窗口和 token 预算。
- `codex-rs/core/src/tools/`、`function_tool.rs`、`mcp_tool_call.rs`：原生工具、MCP 工具和工具运行时。
- `codex-rs/core/src/exec_policy.rs`、`exec.rs`、`sandboxing/`：命令策略、沙箱和命令生命周期。
- `codex-rs/app-server-protocol/schema/json/`：命令执行、审批、用户输入、MCP 和流式事件的结构化协议。
- `codex-rs/core/src/event_mapping.rs`、`stream_events_utils.rs`：事件和增量输出。

### 已验证并保留的设计

1. 一次 Agent Turn 可以包含多次模型请求、工具调用、结果回写和 follow-up，不能等同于一个 WorkItem 或一个 Step。
2. 工具请求必须有独立的调用协议和结果协议，不能继续依赖 `tool_id:instruction + Dict` 作为唯一接口。
3. 授权和审批发生在工具执行前，由 Session/Runtime 处理；`blocked` 只能是结果状态，不能替代授权模型。
4. 命令执行需要独立的执行策略、生命周期控制、输出流、终止和取消；workspace 路径检查只是其中一层。
5. Context Window、自动压缩、Turn/Attempt 预算和 usage 应由 Session 统一管理。
6. 事件应覆盖模型增量、工具请求、命令输出、审批、错误、压缩、取消和生命周期，而不只是最终日志。
7. StopPolicy 需要区分正常结束、需要继续工具调用、等待用户、被取消和不可恢复错误。

### 不直接复用的部分

- Codex 的 Rust Session 和 App Server 不能直接嵌入当前 Python EAS；吸收其协议分层和生命周期语义。
- Codex 的沙箱策略不能通过黑名单正则替代；EAS 需要先把 Grant/Policy/Workspace 统一到 Runtime。
- Codex 的工具和 MCP 双通道不要求 EAS 立刻把所有内部 Python 工具改成 MCP。

### 对 EAS 的首个实现影响

```text
AgentSession
  → Turn / Attempt
  → ContextManager + TokenBudget
  → LLMClient streaming
  → ToolCall / Approval / UserInput
  → Policy + Grant + Workspace Lock
  → ToolRegistry / MCP Adapter
  → ToolResult + Event + Usage
  → Acceptance / StopPolicy
```

与 EAS 当前实现相比，优先补齐：

- `Run/Turn/Attempt/ToolCall/Usage/Approval/Artifact` 一等对象；
- 调用前授权和正式 waiting 状态；
- 统一 ToolCall/ToolResult；
- 命令输出流和取消；
- token budget、自动 compact 和 stop reason；
- 统一事件 envelope。

## 16. 源码拆解验证：Cline

参考仓库：<https://github.com/cline/cline>（本次核实基于 main 分支 commit `cea134b`）

关键路径：

- `apps/vscode/src/shared/WebviewMessage.ts`、`ExtensionMessage.ts`：webview 与扩展之间的 gRPC 信封协议（request_id、流式、stateVersion 单调递增、epoch 会话围栏）。
- `sdk/packages/agents/src/agent-runtime.ts`：无状态 AgentRuntime 循环，`prepareToolExecution` → `resolveToolPolicy` → `requestToolApproval` → 工具执行。
- `apps/vscode/src/sdk/sdk-interaction-coordinator.ts`：审批/提问用"挂起 Promise + 单槽 resolver"实现，turn phase 切到 `awaiting_approval`/`awaiting_followup`，响应到达时 resolve。
- `sdk/packages/core/src/extensions/tools/`：`createTool` 工厂 + zod schema、9 个默认工具（含 `apply_patch`、`run_commands`、`ask_question`）、executors 限制输出大小和路径范围。
- `sdk/packages/core/src/extensions/context/compaction.ts`：0.9 触发线、压缩到 0.7、保留最近 20k token；LLM 摘要与确定性压缩双策略；溢出自愈只重试一次。
- `apps/vscode/src/sdk/sdk-diff-edit-coordinator.ts`：审批前打开只读虚拟 diff 预览（5 秒超时不阻塞审批），批准后写盘。
- `apps/vscode/src/sdk/sdk-task-control-coordinator.ts`：取消时先同步 bump epoch 再 abort，旧事件按 epoch 丢弃。

### 已验证并保留的设计

1. **工具级审批用挂起 Promise，不用状态位轮询**：EAS 若实现"工具调用前等用户确认"，FastAPI 侧用 `asyncio.Event/Future`，`POST /approve` 时 set，执行协程 await——不需要 DB 轮询，也不必等待步骤边界。
2. **审批前推送拟写入 diff（只读预览）+ 超时兜底**：预览卡住不阻塞审批本身。
3. **拒绝理由语义**：拒绝审批时回喂"操作未发生 + 用户附言"的结构化理由，防止模型把拒绝当成"已完成可迭代"。
4. **事件版本围栏**：`broadcast_plan_update` 应带自增 version，前端只应用更新的；取消时先 bump epoch 再 abort。
5. **上下文压缩**：turn 前预算投影 + 0.9 触发 + provider 溢出后确定性压缩重试一次（恢复不依赖 LLM 成功）。
6. **排队消息 queue/steer 双语义**：运行中消息可排队到下一轮或立即注入当前轮。
7. **纯推送 + 轮询兜底**：plan 快照走 WS 推送（权威执行阶段），30 秒轮询只做兜底。

### 不直接复用的部分

- Cline 的 gRPC/postMessage 协议和 VS Code 宿主结构不适用于 EAS 的 FastAPI+React 形态，只借鉴其信封语义。
- Cline 是单会话流式循环，崩溃恢复弱于 EAS 的 DB 权威状态机；不替换 EAS 状态机，而是把审批/diff 预览/排队消息/epoch 围架在执行协程内。
- 自动重试策略：Cline 不做自动 step 重试（避免空转烧 token），EAS 已有有限重试，保留但需上限联动暂停。

### 对 EAS 的首个实现影响

```text
执行协程内（不改状态机）：
  工具执行前 → Policy 判断 → 需要审批？
    → asyncio.Event 挂起 + WS 推送审批卡片(含 diff 预览)
    → /approve 或 /reject → resolve
    → 拒绝时回喂结构化拒绝理由
PlanUpdateEvent 增加自增 version
暂停时提供"放弃等待长命令、取 partial 输出继续"的出路
```

## 17. 源码拆解验证：OpenHands

参考仓库：<https://github.com/OpenHands/OpenHands>（经典 Python 架构核实自 tag 0.62.0，commit `7fbb48c`；当前 main 已转型 TS Agent Canvas，Python 后端迁往 software-agent-sdk）

关键路径：

- `events/stream.py`：`EventStream.add_event` 分配 id → 秘密脱敏 → 同步写 FileStore → 独立线程顺序派发给订阅者。事件既是消息总线也是 append-only 持久化日志。
- `controller/agent_controller.py`：事件驱动（非常驻循环），`_step()` 前检查 `_pending_action` 单飞、`_is_stuck()` 循环检测、ControlFlag 预算；`set_agent_state_to` 状态变更即广播并持久化。
- `events/action/action.py` + `security/llm/analyzer.py`：Action 声明 `security_risk`（自评+独立复核）；无分析器时置 UNKNOWN（fail-safe），HIGH/UNKNOWN 强制进入 `AWAITING_CONFIRMATION`。
- `runtime/base.py` + `runtime/impl/action_execution/action_execution_client.py`：Runtime 抽象 + 沙箱内常驻 FastAPI `action_execution_server`，执行入口双重设卡（拒绝执行 AWAITING_CONFIRMATION/REJECTED 的 action）。
- `controller/state/state.py`：State（事件窗口 + ControlFlag + metrics）pickle 持久化；恢复 = 事件文件全量重放 + pickle 兜底。
- `memory/condenser/`：Condenser 体系（9 个实现），压缩本身也是事件，可审计可重放。
- `storage/`：FileStore 极小接口（write/read/list/delete），local/S3/GCS/memory/web_hook 可换。

### 已验证并保留的设计

1. **状态推导自事件，DB 状态仅作缓存**：EAS 的 `step_events` 已是 append-only，但执行循环真值仍靠改写 `steps.status`。恢复时应以事件重放为准——`recover_orphaned_executions` 目前粗暴把 Running→Failed，若以事件重放为准可区分"步骤实际已成功但状态没写完"。
2. **pending-action 补偿**：进程在"工具已执行、结果未落库"窗口崩溃后，恢复时为 `Running` 步骤补 `step_event('interrupted')` 并强制重跑（OpenHands 在 STOPPED/ERROR 时为未完成 pending action 补 ErrorObservation 保证 tool-call 配对完整），而不是直接标 Failed。
3. **错误分类驱动状态转移**：限流/网络超时 → Retry 状态自动续跑；安全拦截 → Failed。当前"attempt 计数+指数退避"单策略应升级。
4. **确认闸门双层防御 + fail-safe**：无分析器 → UNKNOWN → 强制确认；Runtime 执行入口拒绝 AWAITING_CONFIRMATION 的 action。EAS 的拦截靠错误字符串匹配（`executor/__init__.py:161-166` 依赖魔法字符串），应把 blocked 提升为结构化字段并在 executor 外层加第二道守卫。
5. **状态变更即广播 + 统一领域事件**：在 `update_step_state_machine` 等状态机写入处统一发事件，消除散落的手动 broadcast。
6. **Runtime 进程隔离是远期蓝图**：shell/浏览器放进独立进程，host 侧只有 HTTP 客户端 + 超时 + 心跳 + 重连。执行不受信代码时采用。

### 不直接复用的部分

- OpenHands 0.62.0 的 pickle 状态、每事件一文件的存储对 SQLite EAS 过重；SQLite 行存储 + step_events 已够用。
- OpenHands 没有原生文件系统快照/回滚（只有 git 分支 + 对话截断）；**EAS 的 `clone_steps_from` + `branch_version` 血缘回滚比它强，应保留**。
- Condenser 的 9 种压缩实现不必照搬；先实现 0.9 触发线 + LLM 摘要 + 确定性兜底（Cline 已验证的双策略）。

### 对 EAS 的首个实现影响

```text
崩溃恢复升级：
  恢复时重放 step_events → 区分 interrupted/succeeded
  Running 步骤补 'interrupted' 事件并强制重跑

错误分类：
  result 增加 retryable 字段（限流/超时 → 自动重试；安全拦截 → Failed）

安全闸门：
  blocked 从错误字符串提升为结构化 result 字段
  executor 外层加第二道守卫（对应 OpenHands run_action 双闸）
```

## 18. 拆解总结与 EAS 改造优先级

五个项目拆解完成后的汇总结论：

| 能力 | 最强参考 | EAS 现状 | 改造优先级 |
|---|---|---|---|
| 状态机权威 + 崩溃恢复 | EAS 自身（血缘回滚）+ OpenHands 事件重放 | DB 状态机已强，恢复逻辑粗 | P1：事件重放恢复 |
| 工具级审批（Promise 挂起） | Cline | 无（只有设计书级审批） | P0：asyncio.Event + /approve |
| 授权前置（Grant） | Codex CLI | 无（工具内部正则拦截） | P1：Runtime 签发 Grant |
| blocked 结构化 | OpenHands 双闸 | 错误字符串匹配 | P0：结构化字段 + 外层守卫 |
| 上下文压缩 | Cline 0.9/0.7 + OpenHands Condenser | 每步 LLM 摘要 | P2：预算投影 + 溢出自愈 |
| 事件版本围栏 | Cline epoch/stateVersion | broadcast 无版本号 | P1：自增 version |
| AgentSession/Turn/Attempt | Codex/Goose | Step 一层概念 | P2：概念分层 |
| MCP 适配 | Goose | 无 | P3：外部扩展适配层 |
| 运行时进程隔离 | OpenHands action_execution_server | to_thread + wait_for | P3：远期 |

改造顺序原则：**先做 P0（审批 + blocked 结构化，改动小、收益直接），再做 P1（授权、恢复、围栏），最后做概念分层和隔离。**

> 实施状态（2026-08-29）：P0-a、P0-b、P1-a、P1-b、P2 已全部实现并通过回归测试，见第 19 节实现记录。P1 中"Runtime 签发 Grant"尚未实现（本次落地的是审批挂起），仍为待办。

## 19. 实现记录（0.7-draft）

### 已实现

**P0-a 工具级审批**（新增 `python/api/approval.py`）

- `ApprovalManager`：`create` → `wait`（asyncio.Event 挂起）→ `resolve`（单次幂等）；超时视为拒绝；`reject_all_for_plan` 在计划终止时批量释放挂起请求。
- 配置驱动：`config/models.yaml` 顶层 `approval.required_tools`（默认空 = 全部自动执行，向后兼容）与 `approval.timeout_seconds`（默认 600）。
- 执行协程接入：`_execute_step_with_state_machine` 在工具执行前检查审批要求；请求/决定写入 `step_events`（approval_requested / approval_approved / approval_rejected）与审计日志（actor=user）。
- 端点：`GET /api/approvals`、`POST /api/plan/{id}/approval/{aid}/approve`、`.../reject`（401/404/409 语义完整）。
- 前端：`ApprovalBanner.tsx` 审批卡片（WS approval_request 弹出、approval_resolved 关闭、批准/拒绝 + 备注），API client 新增 approveToolCall/rejectToolCall/getPendingApprovals。

**P0-b blocked 结构化 + 双闸**

- 6 个工具拦截点（command_runner×2、package_installer×2、web_fetcher×2）返回结构化 `"blocked": True`。
- executor：`_is_blocked_result()` 字段优先 + 旧错误码回退；第二道守卫——工具声称 success 但携带 blocked 标志时强制按拦截失败处理。

**P1-a 事件重放式崩溃恢复**

- `recover_orphaned_executions` 升级：中断的 Running 步骤逐事件回放——事件流已有 success → 修正为 Success（状态写入丢失场景）；否则补 `interrupted` 事件并标回 Pending（下次 /execute 自动重跑）。计划仍标 Failed。

**P1-b plan_update 版本围栏**

- `broadcast_plan_update` 载荷新增自增 `version`；前端 `App.tsx` 丢弃 `version <= lastPlanEventVersion` 的过期推送。

**P2 错误分类（retryable）**

- executor 全部返回路径携带 `retryable`（超时/普通失败/异常=True；拦截/校验失败=False）。
- 执行循环：`retryable=False` 的失败跳过重试直接进入失败路径。

### 验证结果（2026-08-29）

- 后端：`pytest tests/python`（除 test_exec_e2e）272 passed，含新增 `test_tool_approval.py`（17）、`test_approval_endpoints.py`（5）与重写的 `test_orphan_recovery.py`。
- 前端：vitest 35 passed（含新增 ApprovalBanner.test.tsx 5 项）；`npm run build` 通过。

### 遗留与后续

- Runtime 签发临时 Grant（绑定资源/预算/过期）尚未实现——当前审批是工具级布尔判断。
- 审批前 diff 预览、queue/steer 排队消息（Cline 已验证）未做。
- test_exec_e2e.py（真实 LLM）未在本次验证范围。

### 工具选型评测记录（2026-08-29，tests/eval/test_tool_selection.py）

- 结果：工具选型 15/15（7 个工具 + manual + 2 个"无需工具"负例全对）；指令选型 87%；JSON 契约违规 1/15。
- 发现的问题：
  1. **指令级随机性**：temperature=0 下 git_commit 场景 1/3 输出 git_add——根因是 `git_commit` 不自动暂存，模型自行"先 add"；单步计划会因此失败。修复方向：git_commit 支持自动暂存（-am）或在工具元数据中写明前置条件。
  2. **JSON 契约缺口**：任务引用缺失上下文（"刚才的报错"）时，模型脱离 JSON 契约用自然语言追问。修复方向：路由提示词增加显式澄清输出模式（tool_id=none + note 字段）。
  3. **事件循环绑定 bug**：`OpenAICompatibleProvider` 的 httpx.AsyncClient 绑定首次创建的事件循环，跨 `asyncio.run()`/多循环复用报 "Event loop is closed"。FastAPI 单循环下无症状，worker 线程/脚本场景会踩。
  4. **命名不一致**：manual_tool.py 注册 ID 为 `manual`，易误导模型与开发者。
- 评测方式：RUN_REAL_LLM_EVAL=1 开启；离线 `_infer_instruction` 映射测试随常规 pytest 运行。

### 上述问题修复记录（2026-08-29）

1. **事件循环**：`OpenAICompatibleProvider.chat` 改为每次调用现开现关 `httpx.AsyncClient`；`close()` 保留为兼容空操作。
2. **git_commit 自动暂存**：默认 `git add -A` 后提交，返回 `staged_files` 供审计；支持 `auto_stage=False` 关闭；元数据注明"无需先 git_add"。
3. **路由契约 note 模式**：路由器提示词规定 `tool_id=none` 时 `note` 必填（澄清的结构化通道），评测脚本同步校验——该契约即未来 Capability Router 的输出规格。
4. **manual 命名统一**：注册 ID 统一为 `manual_tool`，registry 新增 `aliases` 机制保留 `manual` 为兼容别名（旧数据库计划不受影响）；元数据 tool_id 同步更新。

修复后验收：评测工具选型 15/15、指令选型 15/15、契约违规 0/15；后端 273 passed + 1 skipped，真实 LLM E2E 2 passed。

### 薄弱环节补强路线（2026-08-29 开源方案调研，待实施）

针对三大薄弱环节（授权半个、验收空白、并行为零）的采纳顺序：

**1. AcceptanceRunner（验收器）—— 已完成第一阶段**

- 参考：τ²-bench（sierra-research/tau2-bench）——任务成功的唯一判据是**最终环境状态**与期望一致，完全不看 Agent 自述；pass^k 指标（arXiv:2406.12045）——同一任务 k 次全过才算可靠。
- 已落地：`python/acceptance_runner.py` 的确定性检查清单（file_exists / content_contains / exit_code / diff_scope，含别名与 fail-closed）；结构化 `Step.acceptance` 随 SQLite `acceptance_json` 保存/读取/branch clone；Executor deterministic-first；失败证据写入 outputs/state_snapshot/step_events；前端展示验收结果。
- 已验证：工具自报成功但文件内容错误时，计划明确 Failed，不调用 LLM 猜测；真实 LLM E2E 已验证文件存在+内容匹配。
- 后续：场景 YAML 与 pass^3 多次 Run 统计（需要 Run/Attempt 表）。

**2. Grant YAML（已完成第一阶段）**

- 参考：Claude Code 权限语义（声明式 allow/deny 规则 + hooks 只收紧 + OS 沙箱是真边界）；资源级决策外置先不引 OPA，等规则复杂再挂（Vercel AI SDK 已有 OPA 接工具审批的范例；alibaba/open-agent-auth 为 agent 场景的细粒度权限框架）。
- 已落地：`python/authorization.py` 的 `CapabilityGrant/GrantLimits/GrantDecision/AuthorizationManager`；每个真实 API 步骤单独签发、调用前校验工具/路径/`.env`/`.git`/网络主机/次数/时效，执行后撤销；拒绝不进入底层 Registry；审计带 `grant_id` 和用量摘要；`audit_logs` 自动迁移。
- 配置：`config/models.yaml` 的 `authorization` 段；默认 600 秒、每步骤 1 次工具调用、禁止 `.env`/`.git`；`allowed_hosts` 空时网络工具不自动放行。
- 保留边界：命令内容级 allowlist 和 OS/容器沙箱尚未实现；没有传 grant_id 的直接 Executor 调用仍保持兼容旧行为。
- 关键共识：提示词与 hook 都不是安全边界，沙箱才是。

**3. worktree 并行（parallel-code 模式）—— 最后做**

- 参考：社区共识"别协调、直接隔离"——parallel-code / git-lanes 每个并行 agent 一个 git worktree，完成后审 diff 合并；文件锁只用于必须共享的资源。
- 落法：`git_operator` 增加 worktree 指令；计划与计划之间各占一个 worktree 天然隔离，计划内写步骤保持串行；`current_execution` 单任务锁由"每 worktree 一个执行权"替代。

实施前提：Grant 和验收器就位之前不拆执行循环做并行——没有资源边界和质量闸门的并行是真危险。

## 20. 桌面控制台（Rust egui）设计

### 20.1 定位与后端选择

- 桌面控制台是 **Web 界面的原生补充**，不是独立产品：同一 Python FastAPI 后端
  （`python/api/server.py`，端口 8000，Bearer token 认证），保证 Web 与桌面
  的状态、审批、审计完全一致。
- **明确排除 rust/server(:8090)**：axum 脚手架只有浅校验和内存工具列表，
  无任何消费者（仅 dev 脚本启动）；GUI 直连 Python 后端，该脚手架保持
  现状不扩展不删除。
- 参考系：§10 "UI/审批"行（Cline、OpenHands）的桌面化对应物；
  数据同步复用已验证的"纯推送 + 轮询兜底"模式（§16.7）。

### 20.2 架构

```text
┌─ UI 线程（eframe 0.28）────────────────┐
│ App：四面板布局                          │
│  TopBottomPanel(上) 服务状态 / token 配置 │
│  SidePanel(左)     计划列表              │
│  CentralPanel      计划详情 + 控制按钮    │
│  TopBottomPanel(下) 实时日志流            │
└──────────▲───────────────┬──────────────┘
           │ mpsc recv      │ 命令发送
           │ (request_repaint)│
┌──────────┴────────────────▼──────────────┐
│ tokio 后台线程                            │
│  REST（reqwest）：status/plans/plan/控制   │
│  WS（tokio-tungstenite）：/ws/logs        │
│   ├ type=log         → 日志通道           │
│   ├ type=plan_update → 状态通道（带 version）│
│   └ 30s REST 兜底轮询                     │
└───────────────────────────────────────────┘
```

- 后台线程持有全部 IO；UI 线程只消费 channel 消息并在收到消息时
  `ctx.request_repaint()`——egui 的即时模式不允许在 UI 线程阻塞等待网络。
- 断线重连：WS 关闭后指数退避重连（1s→2s→…→30s 封顶），与 Web 端
  client.ts 的"有意关闭不重连"语义一致。

### 20.3 数据同步与状态

- **plan_update 推送**（后端已实现 version 自增围栏）携带计划级 status 与
  全部非废弃步骤状态；GUI 侧 reducer 只应用 `version` 严格递增的事件，
  30s REST 兜底全量拉取作为权威校准——与 Web 端同构。
- 计划级与步骤级状态枚举必须覆盖 Python 全集：
  PlanStatus = Draft/Pending/Approved/Running/Paused/Completed/Failed/RolledBack；
  StepStatus = Pending/Running/Success/Failed/Retry/Skipped/RolledBack。
- 控制操作（execute/pause/resume/skip）为 REST POST；失败（409/404）时
  把后端中文 detail 显示在状态栏，不弹窗打断。

### 20.4 DTO 策略（fork 不复用 eas-common）

`rust/common` 的枚举缺少 Approved/Retry/RolledBack，API DTO 对应的是旧
`/api/v1` 形状而非 `python/api/server.py` 的实际 REST 契约。决策：
**GUI 自建 `types.rs`** 精确对齐 Python API 响应（serde 默认字段名），
不复用 eas-common；待 eas-common 与 Python 契约对齐后再评估回收。

### 20.5 分期

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 应用骨架 + 计划列表/详情 + 实时状态（WS 推送+兜底）+ 日志流 + execute/pause/resume/skip 控制 | 本次实现 |
| 2 | 审批卡片（对接 ApprovalManager /api/approvals）+ 审计视图 + 回滚（含反馈） | 待做 |
| 3 | 设计书生成 / 审阅 / 拒绝反馈（对齐 Web InputView/DesignView） | 待做 |

### 20.6 依赖决策

- 新增 `tokio-tungstenite`：reqwest 0.11 无稳定 WS 客户端 API。
- `egui_extras` 暂不引入：表格用 `egui::Grid` 足够，避免拉入 table/plot 全家桶。

## 21. 版本变更规则

每次更新本文件时：

- 修改版本号和最后更新时间。
- 在末尾追加变更记录。
- 保留已被验证的结论。
- 将被否定的设计标记为“已废弃”，不要静默删除。
- 源码分析结论必须附项目、路径或链接。

## 变更记录

### 1.1-draft — 2026-08-29

- Runtime Grant 第一阶段完成：新增 `AuthorizationManager`，API 步骤调用前校验工具/资源/网络主机/次数/时效，工具调用后撤销；审计加入 `grant_id` 与用量摘要。
- 新增授权测试覆盖越界、敏感路径、过期、撤销、预算和“拒绝不触发工具”；真实 LLM E2E 验证 Grant 事件与审计关联。
- 验证结果：后端 292 passed + 1 skipped；真实 LLM E2E 2 passed；前端 35 passed；前端构建通过。
- 明确未实现：命令内容级 allowlist、OS/容器沙箱、跨进程 Grant。

### 1.0-draft — 2026-08-29

- AcceptanceRunner 第一阶段完成：支持四类确定性检查、fail-closed、结构化 evidence/reward、SQLite 保存/读取/branch clone、Executor/状态机/Designer/前端接入。
- 验证结果：验收器/集成测试通过；后端 282 passed + 1 skipped；真实 LLM E2E 2 passed；前端 35 passed；前端构建通过。
- 设计明确保留旧字符串 `check_criteria` 兼容路径；pass^3、Grant YAML、worktree 并行仍为后续阶段。

### 0.9-draft — 2026-08-29

- 新增 §19"薄弱环节补强路线"：基于开源调研确定三项采纳顺序——AcceptanceRunner（τ²-bench 状态校验 + pass^k）、Grant YAML（Claude Code 权限语义）、worktree 并行（parallel-code 隔离模式）。
- 明确实施前提：Grant 与验收器就位前不拆执行循环做并行。

### 0.7-draft — 2026-08-29

- 实现 P0-a 工具级审批（ApprovalManager + asyncio.Event 挂起 + approve/reject 端点 + 前端审批卡片）、P0-b blocked 结构化与双闸、P1-a 事件重放式崩溃恢复、P1-b plan_update 版本围栏、P2 retryable 错误分类。
- 后端 272 测试、前端 35 测试、双端构建全部通过。
- 新增第 19 节实现记录；Runtime Grant、diff 预览、排队消息列为后续项。

### 0.6-draft — 2026-08-29

- 根据 OpenHands 0.62.0 源码验证：事件驱动状态机、pending-action 补偿、错误分类状态转移、确认双闸 + fail-safe、Condenser、Runtime 进程隔离蓝图。
- 确认 EAS 血缘回滚强于 OpenHands，保留；step_events 应升级为状态推导依据。
- 新增第 18 节：五项目拆解总结与 P0-P3 改造优先级。

### 0.5-draft — 2026-08-29

- 根据 Cline 源码（commit cea134b）验证：Promise 挂起式工具审批、审批前 diff 预览、拒绝理由语义、epoch/stateVersion 围栏、上下文压缩双策略、queue/steer 排队消息、推送+轮询兜底。
- 确认 EAS 保留 DB 权威状态机，交互层（审批/预览/排队）架在执行协程内，不替换状态机。

### 0.4-draft — 2026-08-29

- 根据 Codex CLI 源码验证 Session/Turn/Attempt、工具协议、调用前审批、沙箱/命令生命周期、上下文压缩、事件和取消设计。
- 确认一次 Turn 可包含多轮模型与工具交互，需与 WorkItem/Step 分开。
- 确认 EAS 当前 workspace 检查属于安全边界的一层，不是完整沙箱。

### 0.3-draft — 2026-08-29

- 根据 Goose 源码验证 Session/StateMachine、Operation/Effect、MCP Extension、ActionRequired、上下文压缩和 Usage 设计。
- 确认审批、输入、等待和取消必须是 Runtime 控制流，而不是工具内部布尔结果。
- 确认内部 Tool Registry 可保留，MCP 作为外部 Extension 适配层。

### 0.2-draft — 2026-08-29

- 根据 Aider 源码路径验证 Agent Loop、上下文选择、编辑、Git/测试反馈和预算设计。
- 确认 EAS 需要独立 AgentSession，不将编排循环放入单步 Executor。
- 确认 Aider 作为单 Agent 工具循环参考，不直接解决平行 Agent、授权和资源锁。

### 0.1-draft — 2026-08-29

- 建立工具优先、Agent 即工具、平行协作的初版设计。
- 定义 WorkGraph、Capability、Grant、Artifact 和 Acceptance 的初步边界。
- 规定本文件为后续设计迭代的唯一主文档。

### 0.2.1-draft — 2026-08-29：工作区路径语义（已实现）

- **相对路径** → 所有文件类工具（file_creator / text_editor / web_fetcher
  download / git_operator / package_installer cwd）统一解析到工作区根目录。
  工作区根由 `EAS_WORKSPACE_ROOT` 指定，未设置时回落到仓库根
  （共享实现：`python/tools/workspace.py`）。
- **绝对路径** → 原样使用。自托管环境信任输入，不做白名单沙箱；
  command_runner 的工作目录白名单属于其自身安全语义，保持不变。
- **LLM 侧约定**：计划生成提示词要求 path 使用相对路径（目录以 / 结尾、
  文件带扩展名），工具 metadata 描述同步声明该语义（设计阶段随工具列表
  进入 LLM 上下文）。
- 实现约束：解析必须在各工具内部进行（executor 的 `_infer_instruction`
  依赖原始 path 字符串区分文件/目录），不能前移到执行器。

### 0.8-draft — 2026-08-29

- 新增第 20 节：桌面控制台（Rust egui）设计——定位为 Web 界面的原生补充，
  直连 Python FastAPI 后端；明确排除 rust/server(:8090) 脚手架；
  架构采用 eframe UI 线程 + tokio 后台 IO + mpsc 桥接；
  DTO fork 不复用 eas-common（枚举/契约过期），理由与回收条件入档。
- 阶段 1（骨架/计划列表/详情/实时状态/日志流/四项控制）同日实现，
  阶段 2（审批卡片/审计/回滚）与阶段 3（设计链路）待做。
- 版本号修正：文首长期停留的 0.1-draft 更新为当前实际版本。
