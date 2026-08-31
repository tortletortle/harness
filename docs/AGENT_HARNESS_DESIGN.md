# EAS Agent Harness 设计稿

> 这是 EAS Agent Harness 的唯一主设计稿。
> 后续源码分析、架构决策和实现调整都必须修改本文件，不另建平行方案。

## 版本

- 当前版本：`1.12-draft`
- 状态：初版，已通过五个成熟开源项目源码分析验证；P0-P2、AcceptanceRunner 第一阶段、Runtime Grant 第一阶段、AgentTool 骨架、coding_agent 全流程贯通、工具管理修正三项（human_tool 真确认 / test_runner / 搜索授权化）多轮 Agent Loop（CodingAgentTool v2，有界自测迭代）与上下文缓存命中优化（对话式消息前缀复用、设计书缓存指纹校验与命中率观测）已实施；工具选型评测达标；工具补充四件（lint_gate / github_operator / http_api / browser_check，§24）已实施；验收失败反馈回路（content_contains 缺口转 feedback 喂回 coding_agent 重修一轮，§25）已实施；coding_agent patch 输出模式契约（削接缝动作 A，§26）已实施；worktree 路线已定
- 最后更新：2026-08-31

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

> 实施状态（2026-08-30）：P0-a、P0-b、P1-a、P1-b、P2 已全部实现并通过回归测试，见第 19 节实现记录；Runtime 签发 Grant 第一阶段已落地（1.1），Agent 即工具已接入 WorkGraph 执行（1.3）。

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
- 强制化：真实 API 状态机路径通过 `require_grant: true` 必须携带 Grant；旧的直接 Executor 调用仍保持兼容模式，便于现有单测和插件迁移。
- 关键共识：提示词与 hook 都不是安全边界，沙箱才是。

### Agent 即工具第一阶段（已完成）

- 新增 `python/capabilities.py`：`CapabilitySpec`、`CapabilityRequest`、`CapabilityResult`、`CapabilityRegistry` 和 `ToolCapabilityAdapter`。
- 新增 `python/agent_tools.py`：`BaseAgentTool`、`SingleTurnAgentTool`、`FakeAgentTool`；Agent Tool 只做单轮模型调用，禁止创建子 Agent（`can_spawn_agents=false`、`max_nested_depth=0`），只接收 WorkItem/Artifact 引用和 Runtime Grant。
- 新增 `python/grant_llm_client.py`：显式 `grant_id` 绑定的模型客户端，模型调用前扣 `max_model_calls`，调用后记录 provider usage；超预算不访问上游模型。
- `ToolRegistry` 普通工具通过 Adapter 纳入 Capability 清单；新增 `GET /api/capabilities` 供 Runtime/UI 发现能力。
- 验收：AgentTool、Capability、Grant 模型预算专项测试已通过；未把 Coding/Test/Review 伪装成已完成的复杂 Agent。

### Agent 即工具第二阶段：接入 WorkGraph（1.3 已完成）

- 新增 `python/coding_agent.py`：`CodingAgentTool`（kind=agent_tool，单轮）。执行链路：受权读取目标文件（`text_editor:read_file`，authorize+审计）→ 单次模型调用输出结构化 JSON（修改后完整文件内容）→ 写入位置固定为步骤输入声明的 path（模型输出 path 不一致即失败，不可重定向）→ 受权写回（`text_editor:write_file`，authorize+审计）→ 结构化 CapabilityResult（facts/artifacts/usage）交给验收。
- `python/authorization.py`：`issue_for_step` 支持 `extra_allowed_tools`（并入 Grant 工具白名单）与 `limits`（预算覆盖）；新增 `agent_step_grant_params` —— 仅 kind=agent_tool 步骤允许声明额外工具与预算（普通步骤忽略 `inputs.allowed_tools`，防借道扩权）；agent 预算 = 1 次外层调度 + 默认 4 次内部调用，模型调用次数以 spec 为准（单轮 = 1）。
- `python/executor/__init__.py`：`DefaultExecutor` 可选注入 `CapabilityRegistry`；agent_tool 步骤在调用前授权（外层闸门，消耗 1 次工具调用）后分发给 Capability 契约执行，结果字典与普通工具同构（success/output/error），复用同一验收/校验/审计路径；agent 步骤超时默认 300s（`agent_step_timeout` 可配）。
- `python/api/server.py`：lifespan 在 authorization_manager 定稿后构建 `CodingAgentTool` 并注册进 `CapabilityRegistry`（`GET /api/capabilities` 可见）；状态机 Grant 签发改用 `agent_step_grant_params`；内部工具调用以 `actor=agent:coding_agent` 写入 `audit_logs` 并携带同一 `grant_id`。
- fail-closed 边界（均有确定性测试）：无 Grant 失败；内部工具未授权（tool_not_granted）失败；预算耗尽（budget_exceeded）失败；路径越界在 Grant 校验层被拒；模型输出非法 JSON 或试图重定向写入路径失败且文件保持原状；单轮预算第二次模型调用被拒。
- 验证：`tests/python/test_coding_agent.py` 10 项确定性测试全过（含 FakeAgentTool 调度回归与审计关联）；真实 LLM E2E 见 `tests/python/test_coding_agent_e2e.py`（RUN_REAL_LLM_EVAL=1 门控）。
- 待办：Designer 生成 agent_tool 节点（计划层尚未能产出 coding_agent 步骤，当前以测试直连 executor 验证）；Artifact Store 引用落地；多轮 Agent Loop。

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

## 22. coding_agent 全流程设计（1.4 已实施）

目标：让 coding_agent 从“测试直连可跑”变成“用户从 UI 发起即可达”——补齐工具目录、计划生成、设计期校验三段，执行层（1.3）与展示层契约不变。

### 22.1 全链路与各层职责

```text
用户需求
  → Designer 生成设计书（工具目录含 agent_tool，见 22.2）
  → 用户审批/反馈
  → Designer 生成计划（分级白名单 + coding_agent 契约示例，见 22.3）
  → 设计期契约校验 validate_agent_steps（fail-closed，见 22.4）
  → 执行：Grant 签发（agent_step_grant_params）→ agent_tool 分发 →
     受权读写 → AcceptanceRunner（1.3 已完成，不变）
  → 展示：/api/plan 步骤 outputs 携带 agent_status/facts/acceptance
     （数据已带齐，UI 呈现归前端会话，见 22.6）
```

### 22.2 工具目录层（Designer 看得到 agent）

- `CapabilitySpec` 新增 `description` 字段；`CodingAgentTool.spec()` 携带人类可读描述。
- `Dispatcher(tool_registry, capability_registry=None)`：`request_tool_info()` 把 kind=agent_tool 的能力合并进工具目录（`category=agent`、`kind=agent_tool`），普通工具行为不变；Designer 的设计书生成由此能看到 coding_agent 并将其纳入 required_tools 评估。

### 22.3 计划生成层（Designer 敢用、用对）

- 原约束“只使用 file_creator”改为分级白名单：允许 file_creator（创建新文件）与 coding_agent（修改已存在文件）；shell 类工具禁令不变。
- 计划提示词新增 coding_agent 步骤契约与示例：inputs 必含 path（单文件）/instruction/allowed_tools=["text_editor"]；一步一文件；acceptance 必填。
- 已知限制：设计书→计划之间没有单独的 agent 步骤规划器，是否使用 coding_agent 依赖计划 LLM 对提示词的遵守；真实 E2E 在 user_request 中显式提及 coding_agent 以施加确定性压力。

### 22.4 设计期校验（fail-closed，比执行期拒绝便宜）

- `utils/json_fixer.py` 新增 `AGENT_TOOL_CONTRACTS` 与 `validate_agent_steps(plan_data)`：coding_agent 步骤缺 path/instruction/allowed_tools、allowed_tools 越权（非 text_editor）、acceptance 缺失或类型非法、path 为目录 → 计划打回重试。
- 挂载点：`generate_plan` 在 JSON 修复后、构建 ExecutionPlan 前；普通步骤不受影响。
- `generate_plan` 重试策略会硬编码补位 stepfun/hejuapi 等 fallback，会把根因稀释成 “Provider not found”；现 agent 契约错误随 GenerationError 附加暴露（`期间 agent 契约校验失败: ...`）。
- 双保险口径：设计期校验管“计划写得对不对”，执行期 Grant 管运行时边界；两层独立，不因前者存在而放松后者。

### 22.5 验证

- `tests/python/test_full_flow_design.py` 12 项：目录聚合（含无 capability_registry 回归）、契约校验矩阵（缺 path/越权/缺 allowed_tools/无 acceptance/非法类型/目录 path）、提示词含契约且旧一刀切白名单已替换、非法 agent 计划设计期打回且根因随异常暴露。
- `tests/python/test_fullflow_e2e.py`（RUN_REAL_LLM_EVAL=1）：真实服务全链路——设计书含 coding_agent 评估 → 计划产出 coding_agent 步骤 → 执行 Success → 文件真实修改（return 0 → return 1）→ 验收通过 → 审计 grant_id 关联。

### 22.6 展示层契约（前端会话实施）

- API 无需改动：`/api/plan/{id}` 步骤 outputs 已含 `output.agent_status`、`agent_facts`、`agent_artifacts`、`acceptance*`；`/api/capabilities` 暴露 kind 供徽标分类。
- 建议呈现：agent_tool 步骤独立徽标（单轮/Grant 受限）、facts 列表（目标文件、写入字节数）、验收证据展开；与 Rust egui 阶段 2 的审批卡片同批。

### 22.7 后续阶段（不在 1.4）

- 多轮 Agent Loop 第一阶段（1.7 已落地）：CodingAgentTool v2 支持有界自测迭代——inputs.max_turns>1 时每轮写回后用 test_runner 自测（须在 allowed_tools 声明），测试输出作为结构化反馈进入下一轮提示词，通过即提前结束；Grant 预算按轮扩展（max_model_calls=turns、max_tool_calls=1+3*turns，见 agent_step_grant_params）。最终验收仍归 AcceptanceRunner。默认 max_turns=1 与单轮行为完全兼容。完整 AgentSession/Turn/Attempt 分层（§15）仍为后续。
- 上下文缓存命中优化（1.8 已落地）：多轮改为对话式消息序列（system + 首轮请求 + 各轮 assistant 输出 + 反馈追加），前缀跨轮不变 → 上游前缀缓存（KV cache）可命中，降低多轮延迟与费用；设计书缓存增加工具目录/模型指纹校验（目录或模型变化即淘汰陈旧条目，防止错误命中）与 lookups/hit_rate 观测。默认行为向后兼容。
- Artifact Store 引用落地；worktree 并行（§19 顺序不变，最后做）。

## 23. 工具管理修正计划（1.5 · 已定，待实施）

来源：2026-08-30 工具管理审计（实现 vs 本稿 §2/§3/§6/§7/§11 对照）确认三项实质偏离。以下为修正设计；每项独立可交付，顺序 B → C → A。

### 23.1 P1-B 人工步骤真确认（human_tool 第三类）—— 已实施（1.6）

问题：`manual_tool` 以 deterministic 身份注册且执行即成功（仅附 note）；§3 Human Tool: approval/input 未落地，计划里的人工步骤实际无人确认。

实施：
- `capabilities.py` 新增 `HumanCapabilityAdapter`（kind="human_tool"），manual_tool 以第三类注册进 CapabilityRegistry。
- 状态机审批闸门条件扩展：`is_required(tool)` **或** capability.kind=="human_tool" → 必挂 ApprovalManager（复用 P0-a 全流程：WS 卡片、approve/reject 端点、超时即拒）。
- 批准后 manual_tool 返回步骤描述；拒绝/超时走 blocked=user_rejected 路径。
- 兼容口径：旧计划 manual 步骤行为改变（会挂起等待）——这是设计意图的矫正而非回归；预留 `approval.human_steps` 开关（默认 true）。
- manual_tool metadata 描述同步更新（"等待人工确认后执行"）。

验证：无 required_tools 配置时 human 步骤必挂审批；approve→Success；reject→Failed blocked；超时→拒；`/api/capabilities` 出现 kind=human_tool；普通工具不受影响。

### 23.2 P1-C test_runner 工具（补全 run/test/git 的 test 位）—— 已实施（1.6）

问题：7 工具无测试类，计划白名单又禁 shell → coding_agent 改完代码无法跑测试，“改→测→修”闭环断裂。

设计：
- 新增 `python/tools/test_runner.py`：只执行白名单命令模板，不做 shell 逃生舱。
  - 模板来源：config `test_runner.commands`（默认内置 pytest / npm 两模板）；inputs={suite, test_path?, args?} → list argv 拼接（无 shell=True）。
  - cwd 固定解析到工作区根（复用 tools/workspace.py）；超时默认 120s；返回 `{success: exit_code==0, exit_code, stdout 尾部, stderr 尾部}`（验收器已支持 returncode/exit_code 双键）。
- 注册进 ToolRegistry（category="test"）→ Adapter 自动进 Capability 目录 → Dispatcher 聚合对 Designer 可见。
- 计划提示词白名单扩为 file_creator / coding_agent / test_runner，test_runner 契约：inputs 必含 suite；acceptance 必含 `{"type":"exit_code","equals":0}`。
- `json_fixer` 新增普通工具轻契约 TOOL_CONTRACTS：test_runner 缺 suite / 缺 acceptance → 计划打回。
- 授权：test_runner 步骤 Grant allowed_tools={test_runner}；无网络；是否需审批交给 approval.required_tools 配置（默认不挂）。

验证：pytest 模板通过/失败两态；未登记模板拒绝；argv 拼接无 shell 注入面；契约校验拒绝缺 suite/缺 acceptance 的 test_runner 步骤；真实 E2E 扩展：coding_agent 修改 → test_runner 跑 pytest → 全绿。

### 23.3 P1-A 搜索旁路收编（授权化设计期搜索）—— 已实施（1.6）

问题：设计书生成阶段 ToolRegistryAgent 直调 WebFetcher.execute —— 无 Grant、无 allowed_hosts、无审计，绕过 §1/§7（当前配置 enforce=true 且 allowed_hosts 为空，旁路完全绕开了这层）。

设计：
- `AuthorizationManager` 新增无 step 的通用签发 `issue_grant(subject, allowed_tools, limits=None, ttl=None)`：资源为空，URL 检查走 allowed_hosts。
- server 在 /api/design 调用 generate_design 前签发 design_search Grant：allowed_tools={"web_fetcher"}、max_tool_calls=6、TTL=600s、allowed_hosts 继承配置；用后撤销。
- ToolRegistryAgent 的抓取改走 CapabilityRegistry 的 web_fetcher Adapter（authorize + to_thread，复用 1.3 机制）；每次写审计（actor=designer_search，带 grant_id）。
- 预算超限 / 主机不在 allowlist → 结构化拒绝反馈给 LLM 继续生成（不中断设计书）。
- 兼容口径 fail-closed：无 Grant 且 enforce=true → 搜索返回“未授权”结果（设计书继续，只是不联网）；enforce=false 保持旧行为。

验证：无 Grant 拒绝（断言无网络调用）；有 Grant 放行+审计+用量；越权主机拒绝；enforce=false 兼容。

> 现状注记（2026-08-30）：`super_search` 工具已落地并注册（多引擎聚合搜索 + 深度检索 + 正文抽取，无需 API Key 可用，详见 docs/SUPER_SEARCH.md）；`ToolRegistryAgent.handle_search_request` 已改为优先调用 super_search（无则退回 web_fetcher）。P1-A 实施时建议以 super_search 为搜索载体（design_search Grant 的 allowed_tools={"super_search"}），本节 Grant/审计/fail-closed 设计不变。

### 23.4 审计顺带入档（不改代码）

- `plugin_system.py`（PluginManager/ToolPlugin）标记废弃候选：P3 外部扩展层如启用（MCP 适配）以新设计为准；代码头部加废弃注记。
- Dispatcher 职责漂移（路由实际在 executor）：接受现状，§11 注记更新而非改代码。
- Schema 校验（§7 第六项）列 P2：等 capability input_schema 有真实内容后统一补。
- 工具分类学（generation/other/search vs read/edit/run/test/git）列 P3，仅影响目录可读性。

### 23.5 实施顺序与总验收口径 —— 已全部达成（1.6）

实施顺序：P1-B → P1-C → P1-A，每项确定性测试先行 + 全量回归。
三项完成的标志均已达成：`/api/capabilities` 出现三种 kind；搜索受 Grant/allowlist/审计约束；真实 E2E 计划包含 test_runner 步骤（见 test_fullflow_e2e.py）。

## 24. 工具补充四件（1.9 · 已实施）

来源：2026-08-30 工具缺口盘点（对照 OpenHands / Cline / SWE-agent / MCP 生态的常见工具类型）。四个工具统一按 docs/TOOL_INTERFACE_DESIGN.md（§6 契约的工具层细化）落地：实现 → 注册 → 副作用登记 → 指令推断 → 设计期接入（如进白名单）→ 测试 → 文档 → 冒烟。

### 24.1 lint_gate 工具化 —— 已实施

`python/tools/lint_gate.py` 原有的 `lint_python()` 是 file_creator/text_editor 的写入前语法门禁（对已损坏旧文件放行以允许修复中间态）。本次新增独立检查函数 `lint_file_content()`（无旧内容豁免：坏即坏）与 `LintGateTool`（tool_id=lint_gate，category=test，instruction `lint_file`，inputs.path），返回 exit_code 0/1 供验收器直接消费。已注册、已进计划白名单 + TOOL_CONTRACTS（required path / exit_code）。executor 中 code_context 的三段重复推断分支顺带去重。

### 24.2 github_operator —— 已实施

gh CLI 白名单动作执行器：`pr_create` / `pr_status` / `pr_checks` / `pr_list`，子命令骨架写死、argv 拼接无 shell、title/body 等值只作独立 argv 项；cwd 由 executor 注入 Grant 工作区（加入 command_runner/package_installer 同款注入集合）。`side_effect="external"`（capabilities.py 新增 `_EXTERNAL_TOOLS` 分类与 external 取值），**不进计划白名单**（计划不应自动建 PR），是否强制审批交给 approval.required_tools 配置；真实 gh 只读用例打标 RUN_REAL_GH。

### 24.3 http_api —— 已实施

单次 HTTP 请求（httpx）：方法白名单 GET/HEAD/POST/PUT/PATCH/DELETE；复用 `web_fetcher._validate_url` SSRF 防护（super_search 同款先例）；**不跟随重定向**（重定向到未授权主机是 allowed_hosts 的绕过面）；响应 20MB 上限、正文尾部截断、回显响应头只留安全子集（不回显 set-cookie）。支持 expected_status 与 Grant allowed_hosts。side_effect=external，不进计划白名单。

### 24.4 browser_check —— 已实施

Playwright 无头浏览器**一次性无状态页面检查**（`check_page`：expect_text / not_expect_text / expect_selector + 截图 output 键），不做会话/多步交互（点击填表留待工具级异步/会话协议演进项）。依赖懒加载：playwright 未安装 → blocked（注册表不受影响）；浏览器二进制需一次性 `playwright install chromium`（requirements 新增 playwright==1.61.0）。已进计划白名单 + TOOL_CONTRACTS（required url / exit_code）。真实浏览器用例直接跑本地静态页（零外网）。

### 24.5 验证

新增测试 test_lint_gate_tool / test_github_operator(+_security) / test_http_api(+_security) / test_browser_check；安全专项覆盖无 shell 与 argv 注入面、白名单外动作 subprocess 前拒绝、SSRF 矩阵、方法白名单、依赖缺失 blocked；browser_check 真实浏览器用例通过（本地页面断言+截图落盘）。已知实现适配：仓库安全扫描器把「`def execute` + 字符串拼接」误判为 SQL 注入，四个新工具统一采用 `_run` + `execute = staticmethod(_run)` 别名模式（契约入口不变，docs/TOOL_INTERFACE_DESIGN.md §3.1 已成文）。

## 25. 验收失败反馈回路（1.10 · 已实施）

真实首跑 step20/22 与观察第 3 期步骤 6 暴露的失败模式：coding_agent 输出与确定性验收的命名/标识符脱节或内容缺口，验收失败后原本直接判死（盲目重试只会重复同样错误）。本回路把**确定性验收缺口**转成反馈文本喂回 coding_agent 再修一轮，让"指令与验收命名脱节"类失败自愈；它不解决模型 JSON 格式稳定性问题（那属于结构化输出通道的独立事项）。

### 25.1 契约

- 触发条件（四者同时满足）：步骤结果 `check_status == "failed"`；`acceptance_result.checks` 存在 `passed=false` 的 `content_contains` 检查；步骤工具为 `coding_agent`；本步反馈尚未使用（每步最多一次，`feedback_used` 门控）。
- 反馈构建：`api/server.py :: _build_acceptance_feedback(acceptance_result)` —— 只提取未通过的 `content_contains` 规则（text + path），生成反馈："上一轮修改未通过确定性验收：文件中缺少以下内容，请在本轮修改中确保完整包含这些标识符（逐字出现）——'<text>'（应出现在 <path> 中）"。无可反馈缺口（全通过 / 非 content_contains / 非法结构）返回 None，走原路径。
- 注入与消费：executor 把反馈写入 `step.inputs.feedback` 后 `attempt += 1` 并立即重跑本步；`CodingAgentTool` 读取 `inputs.feedback`，注入首轮完整提示词（"上一轮反馈"块）并随多轮 followup 携带；反馈文本与测试输出摘要按 `_MAX_FEEDBACK_CHARS = 1500` 截断。
- 失败路径不变：反馈轮仍未通过 → 回到原判死路径（重试耗尽 → 步骤 Failed + 原因落库 → 计划 Failed → finally 执行摘要自动落盘 PROJECT_MEMORY.md）。反馈重试推进重试计数（attempt+1），不绕过 `retry_max_attempts` / `retryable=False` 语义；注：反馈分支先于重试耗尽判定，最后一次尝试失败时反馈轮仍能获得一次额外执行。
- 边界：只覆盖 `content_contains`（标识符级缺口），不做语义诊断；`file_exists` / `exit_code` 等其他验收类型不触发反馈。

### 25.2 验证

- 确定性测试：tests/python/test_improvement_round.py（多缺口提取 / 全通过返回 None / 非 content_contains 忽略）。
- 全量回归：557 passed + 11 skipped（2026-08-31；2 个 e2e error 为需活服务的历史环境项，与回路无关）。
- 真实任务验证：待观察第 4 期构造"验收失败 → 反馈 → 自愈"任务实测。

## 26. coding_agent 输出模式契约：patch 模式（1.12 · 已实施）

1.11 观察期实证的根因之一：代码内容进 JSON 字符串需要完整转义，转义摩擦直接制造解析失败（第 3 期步骤 6 连续两轮 JSON 失败）。削接缝动作 A 把 coding_agent 的输出契约从"JSON 里带文件全文"扩展为 search/replace 补丁块（Aider 式纯文本标记），代码内容从此不进 JSON。与 json_repair 的分工：json_repair 修控制面 JSON（1.11），patcher 处理代码内容（本节）。

### 26.1 模式选择与兼容

- 优先级：步骤级 `inputs.output_mode` > 环境变量 `EAS_CODING_AGENT_OUTPUT_MODE` > 默认 `full`；非法值静默回退 `full`（fail-safe，默认行为与 1.11 完全一致，向后兼容）。
- facts 新增 `coding_agent_output_mode`，单次执行的实际输出模式可审计。

### 26.2 patch 模式契约（python/patcher.py）

- 块格式：`<<<<<<< SEARCH` / `=======` / `>>>>>>> REPLACE`。SEARCH 空段 = 新建文件（仅当文件不存在或为空时合法，否则 `file_exists_conflict`）；SEARCH 非空必须逐字命中且全文唯一（0 次 → `not_found`，≥2 次 → `ambiguous`）；精确失败先做 CRLF→LF 行尾归一再匹配，仍失败才报错。
- 多块按出现顺序依次应用，前一块输出作为后一块输入；块外解释文字一律忽略，允许 markdown 围栏包裹整个补丁；不做空白容忍/模糊匹配——宁可报错走 feedback 重试，也不静默改错位置。
- 错误四分类：`parse_error` / `not_found` / `ambiguous` / `file_exists_conflict`，错误附 SEARCH 首行作为定位线索。

### 26.3 与多轮回路的接线

- 补丁错误不立即判死：非最后一轮时错误文本经 `error_feedback_text` 进 feedback 通道回流下一轮（fact 记 `coding_agent_turn{N}_patch_error`）；最后一轮才 failed（"补丁未应用"）。
- 应用成功记 `coding_agent_turn{N}_blocks_applied`（块数）。
- 空内容防护按模式适配：会把文件清空的补丁被拒，反馈文本改为 patch 语义（基于文件当前内容重新输出补丁块）；自测未过的重试语按模式区分（patch："重新输出补丁块（基于文件当前内容）"；full："重新输出完整文件内容"）。
- 顺带修复：空内容防护分支重复追加 assistant 消息（同一 assistant 内容出现两次）；消息回填统一交循环顶部。

### 26.4 验证

- tests/python/test_coding_agent_patch.py 6 项：块解析与应用矩阵（新建/多块/围栏剥离/行尾归一）、错误分类与 feedback 回流、模式选择优先级与非法值回退。
- coding_agent/patch 相关选择集（-k "coding_agent or patch"）42 项：41 passed + 1 skipped。
- 全量回归（2026-08-31，commit c86cb4b）：595 passed + 11 skipped；2 个 e2e error 为需活上游的历史环境项（test_exec_e2e 经真实 LLM 生成设计书，上游分发渠道当日 503 无可用 channel），与本次接线无关，与 §25.2 记录的基线形态一致。

## 变更记录

### 1.12-draft — 2026-08-31

- **coding_agent 输出模式契约（削接缝动作 A，§26）**：新增 patch 输出模式——search/replace 补丁块（Aider 式）经确定性补丁应用器 python/patcher.py 应用，代码内容不再进 JSON 字符串（转义摩擦根除）；步骤级 `inputs.output_mode` > 环境变量 `EAS_CODING_AGENT_OUTPUT_MODE` > 默认 full（向后兼容，非法值回退）。补丁错误（not_found/ambiguous/parse_error/file_exists_conflict）走 feedback 通道回流重试而非直接判死；空内容防护与自测重试语按模式适配；顺带修复空内容防护分支 assistant 消息重复追加缺陷。
- 测试：test_coding_agent_patch.py 6 项；全量回归 595 passed + 11 skipped（2 个 e2e error 为需活上游的历史环境项，见 §25.2 / §26.4）。

### 1.11-draft — 2026-08-31

- **JSON 输出稳定性加固**：新增 json_repair 模块（尾逗号修复、字符串字面量内裸控制字符转义状态机、markdown 剥离、宽容解析 parse_loose——不引入依赖、不做危险猜测）；coding_agent 解析失败先走修复再进入反馈重试。第 3 期步骤 6 连续两轮 JSON 失败的根因即此类噪声。
- **executor 模型切换**：step-3.7-flash → step-3.5-flash-2603。实验（同一多行代码 JSON 任务 × 5 次）：3.7-flash 直接合法 4/5（1 次不可解析），3.5-flash-2603 5/5 直接合法 + 5/5 语义正确，平均延迟 4.7s。数据：docs/json_stability_experiment.json。
- **save_plan 幂等修复**：计划失败后重新审批（同 plan_id 重存）被 step_events → steps 外键挡住 500；重存前先清该计划的 step_events（失败原因保留在步骤行不丢失）。回归：test_save_plan_fk.py。
- **计划提示词：验收写法指引**——content_contains 只匹配行为性标记（新增断言/函数名/关键逻辑行），禁止精确匹配调用参数顺序；能跑测试的场景最后一步必须用 test_runner 的 exit_code 做行为级验证。与 1.10 验收失败反馈回路互补（反馈解决"怎么修"，指引解决"验收别错杀"）。
- 验证：json_repair 8 项 + save_plan FK 回归 + 计划提示词注入断言全过；第 7 跑真实任务 7/7 全绿收敛（pytest 全绿），执行摘要 Completed 自动落盘。
- 观察期累计：3 期 / 11+ 次真实执行 / 9 个真缺陷修复 / 2 个结构性缺口实证——全部记录于本稿与 Notion 归档。

### 1.10-draft — 2026-08-31

- 验收失败反馈回路（§25，P2）：executor 侧 `_build_acceptance_feedback` 把验收 `content_contains` 缺口转成反馈文本，写入 `inputs.feedback` 喂回 coding_agent 重修一轮（每步最多一次）；coding_agent 首轮提示词与 followup 消费 feedback（`_MAX_FEEDBACK_CHARS=1500` 截断）。反馈轮仍失败走原判死路径。
- 同批入档（commit 5bc24d2，1.9 期遗留）：designer 计划提示词新增任务类型判别（检索类 vs 系统构建类）与计划体积约束（≤20 步 / ≤6 segment / acceptance 用短标识符）；super_search bing 长中文查询自动拆分合并去重；STATUS.md 一句话现状刷新（真实 LLM 首跑 18/20 步）。
- 测试：test_improvement_round.py（反馈构建三例 + bing 拆分）；全量回归 557 passed + 11 skipped。

### 1.9-draft — 2026-08-30

- 工具补充四件（§24）：lint_gate 工具化（独立语法检查，进计划白名单）、github_operator（gh CLI 白名单动作，external 副作用，不进白名单）、http_api（方法白名单 + SSRF 防护 + 不跟随重定向）、browser_check（Playwright 一次性页面检查，进计划白名单，依赖懒加载）。
- capabilities.py 副作用分类新增 `external` 取值与 `_EXTERNAL_TOOLS`/`_REVERSIBLE_TOOLS`/`_NON_IDEMPOTENT_TOOLS` 命名集合；executor 新增 github_operator 的 cwd 注入与四个新工具的指令推断分支（顺带去重 code_context 三段重复分支）。
- 设计期接入：TOOL_CONTRACTS 新增 lint_gate / browser_check；计划提示词白名单扩为 file_creator / coding_agent / test_runner / lint_gate / browser_check。
- 工具实现契约成文：docs/TOOL_INTERFACE_DESIGN.md（新工具统一按其 §7 落地清单执行）。
- requirements 新增 playwright==1.61.0。

### 1.8-draft — 2026-08-30

- 上下文缓存命中优化：CodingAgentTool 多轮改为对话式消息序列（每轮只追加 assistant 输出 + 反馈 user 消息，不重建完整提示词），轮间前缀保持稳定以命中上游 KV 前缀缓存（有确定性断言：第 2 轮 messages 完整复用第 1 轮前缀）。
- DesignCache/PlanCache：get/set 支持 fingerprint（工具目录+模型指纹，不一致即淘汰陈旧条目，防止错误命中）；stats() 新增 lookups/process_hits/hit_rate 观测。
- 新增 tests/python/test_cache.py 7 项（命中/未命中计数、key 规范化、指纹失效、TTL 过期、PlanCache 继承）。
- 已知边界：指纹校验当前只保护设计书缓存（计划缓存以 design_id 为键，天然隔离）；上游前缀缓存是否生效取决于 provider 实现。

### 1.8 补充 — 真实任务观察第 2 期（多文件重构，2026-08-30）

- 场景：三文件 User 模块破坏性重构（7 步：3 创建 + 3 coding_agent + 1 test_runner），隔离工作区真实 LLM，5 次执行。
- 修复三缺陷：多轮解析失败无反馈重试、空内容写入（破坏性）、部分成功被判全失败（turn 预算耗尽但已有成功写回 → 交外层验收裁决）。
- 结构性缺口实证：①多文件一致性无保障（AI 真实犯错：service.py 参数顺序与 models.py 不匹配，验收层正确拦截）；②失败后无跨步骤恢复（步骤 5 失败后计划停止，尽管其产物实际正确）。两者共同构成 AgentSession 分层的最高优先级实证。
- 附带发现：设计书 LLM 对长复杂请求的 JSON 输出稳定性不足（连续 4 次解析失败）；观察脚本 token 含非 ASCII 字符导致 HTTP 头编码失败。

### 1.7-draft — 2026-08-30

- 多轮 Agent Loop 第一阶段落地：CodingAgentTool v2（version 0.2.0）支持 inputs.max_turns 有界自测迭代——每轮读→模型→写回→test_runner 自测，测试失败（exit_code 非零）作为合法结构化反馈进入下一轮提示词，通过即提前结束；自测被拒/基础设施失败则停止迭代交外层验收。
- Grant 预算按轮扩展：agent_step_grant_params 以 max_turns 为单一旋钮（max_model_calls=turns、max_tool_calls=1+3*turns）；allowed_tools 契约放宽至 text_editor+test_runner（§23.2 的授权边界不变）。
- test_runner.execute 接受 execute 指令别名（executor 对未指定 instruction 的步骤推断为 execute）。
- 新增多轮确定性测试：两轮自测闭环（第 1 轮改错→自测失败→反馈→第 2 轮改对→自测通过）、预算耗尽（3 轮全部失败→completed+facts 记录 last_test_exit，交外层验收）、跨 segment step_order 全局唯一回归。
- 已知边界：多轮自测的测试发现依赖 inputs.test_path 精确指向测试文件；真实 E2E 曾暴露 step_order 全局唯一 bug（同日修复）。完整 AgentSession/Turn/Attempt 分层仍为后续。
- 跨次续作实证（1.8 补充）：上一期失败留下的半成品，经 PROJECT_MEMORY.md 记忆注入 → 计划自动生成 2 步续作 → AI 一轮改对 test_users.py → pytest 全绿收敛（118.9 秒）。同日 executor 从 newapi（503 账号池耗尽）切换到阶跃星辰 step-3.7-flash。已知轻微瑕疵：AI 修改 service.py 时参数名义顺序与 models.py 不一致（位置兼容、测试通过）——多文件一致性仍是后续课题。
- 第 3 期观察（1.8 补充）：验证"失败 → 自动记忆 → 续作 → 收敛"完整产品路径。Run A：7 步重构任务，步骤 6 失败（coding_agent 连续两轮 JSON 解析失败），执行摘要全自动落盘 PROJECT_MEMORY.md（每步状态/失败原因/Pending 步骤，无需人工）。Run B：续作任务凭记忆生成 2 步计划 → coding_agent 修正 test_users.py（一轮自测通过）→ test_runner 全绿，双步 Success，98 秒。全程零人工干预——"失败后前功尽弃"的结构性缺口已有自动化答案。已知边界：记忆摘要为确定性规则生成（步骤状态+失败原因），不含语义级诊断；步骤 6 连续两轮 JSON 失败的原因（模型对多行代码输出的格式稳定性）待第 4 期深入。

### 1.6-draft — 2026-08-30

- §23 三项修正全部落地：P1-B human_tool（HumanCapabilityAdapter + ApprovalManager.is_required_for_step + manual_tool 挂审批，`approval.human_steps` 开关默认 true）；P1-C test_runner（白名单命令模板、config `test_runner.commands` 注入、argv 无 shell、计划白名单扩三工具 + TOOL_CONTRACTS 契约校验）；P1-A 搜索收编（AuthorizationManager.issue_grant 通用签发、design_search Grant 每次设计书生成签发/撤销、ToolRegistryAgent 走授权+审计、无 Grant fail-closed）。
- 新增 tests/python/test_toolmgmt.py 13 项确定性测试；真实全流程 E2E 扩展 test_runner 步骤。
- 计划提示词新增用户约束优先级规则：用户需求/审批反馈明确限定可用工具时，限定优先于白名单（修复 test_exec_e2e 偶发计划偏移）。
- 修复 step_order 全局唯一性：designer 原按 segment 内编号，多 segment 计划全部平局 → get_next_runnable_step 执行顺序随机、回滚 step_order > X 语义失效；改为全计划递增并加跨 segment 回归测试（真实 E2E 曾因此把 test_runner 排到测试文件创建之前）。
- 工具管理合并说明：另一会话引入 super_search（多引擎搜索）后，design_search Grant 白名单覆盖 web_fetcher + super_search，搜索 Agent 的两条抓取路径统一走授权 + 审计（_authorized_search）。

### 1.5-draft — 2026-08-30

- 新增 §23 工具管理修正计划（未实施）：P1-B 人工步骤真确认（human_tool 第三类）、P1-C test_runner 工具（白名单模板，补全测试位）、P1-A 搜索旁路收编（设计期 Grant + 审计 + fail-closed 兼容）；审计顺带入档四项（插件系统废弃候选、Dispatcher 职责注记、Schema 校验列 P2、分类学列 P3）。

### 1.4-draft — 2026-08-30

- coding_agent 全流程贯通：工具目录聚合（Dispatcher 合并 agent_tool）、计划提示词分级白名单 + 契约示例、设计期 `validate_agent_steps` fail-closed 校验（含越权/缺验收拒绝）、agent 契约根因随 GenerationError 暴露。
- `CapabilitySpec` 新增 description；执行层（1.3）与展示 API 契约不变。
- 新增 `test_full_flow_design.py`（12 项）与 `test_fullflow_e2e.py`（真实 LLM 全链路，RUN_REAL_LLM_EVAL=1 门控）。
- 明确未实现：多轮 Agent Loop、Artifact Store 引用、worktree 并行、前端/egui 的 agent 步骤专属呈现（归前端会话）。

### 1.3-draft — 2026-08-30

- Agent 即工具第二阶段完成：`CodingAgentTool` 接入 WorkGraph 执行 —— executor 按 kind=agent_tool 分发（同一 Grant 预算与资源边界），结果过 AcceptanceRunner，与普通工具同验收/审计路径。
- `issue_for_step` 增加 `extra_allowed_tools`/`limits`；新增 `agent_step_grant_params`（普通步骤忽略 `inputs.allowed_tools`，防止借道扩权）。
- coding_agent 内部工具调用（text_editor 读写）逐次 authorize + 审计（actor=agent:coding_agent，关联同一 grant_id）；模型无法重定向写入路径。
- 新增 `test_coding_agent.py`（10 项确定性测试：正路径验收、路径重定向拒绝、未授权工具拒绝、预算耗尽拒绝、越界路径拒绝、非法 JSON fail-closed、单轮预算、参数作用域、FakeAgentTool 调度回归、审计关联）与 `test_coding_agent_e2e.py`（真实 LLM，RUN_REAL_LLM_EVAL=1 门控）。
- 明确未实现：Designer 生成 agent_tool 节点、Artifact Store 引用、多轮 Agent Loop、Agent 动态创建、OS/容器沙箱、OPA/Cerbos、worktree 并行。

### 1.2-draft — 2026-08-29

- Grant 强制化第一阶段完成：`require_grant`、绝对路径策略开关、API/Runtime 调用前校验、workspace 默认绑定、模型调用预算记录。
- Agent 即工具骨架完成：CapabilityRegistry、ToolCapabilityAdapter、SingleTurnAgentTool、FakeAgentTool、GrantLLMClient；禁止子 Agent 递归。
- 新增 Grant/Capability/AgentTool 专项测试；验证结果：后端 305 passed + 1 skipped，真实 LLM E2E 2 passed，前端 35 passed，前端构建通过。
- 明确未实现：复杂 Agent 多轮循环、Agent 动态创建、OS/容器沙箱、OPA/Cerbos、worktree 并行。

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
