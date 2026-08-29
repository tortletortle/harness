# EAS Agent Harness 设计稿

> 这是 EAS Agent Harness 的唯一主设计稿。
> 后续源码分析、架构决策和实现调整都必须修改本文件，不另建平行方案。

## 版本

- 当前版本：`0.1-draft`
- 状态：初版，待通过成熟开源项目源码分析验证
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

## 16. 版本变更规则

每次更新本文件时：

- 修改版本号和最后更新时间。
- 在末尾追加变更记录。
- 保留已被验证的结论。
- 将被否定的设计标记为“已废弃”，不要静默删除。
- 源码分析结论必须附项目、路径或链接。

## 变更记录

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
