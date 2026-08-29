## 下一阶段：Grant 强制化 + Agent 即工具

### 目标
在当前唯一设计稿 `docs/AGENT_HARNESS_DESIGN.md` 的 1.1-draft 基础上继续迭代，不新建平行方案：

```text
API/Runtime 步骤
  → 系统签发 Grant
  → 调用前校验
  → 工具执行
  → 用量记录
  → 自动撤销

Agent Tool
  = 一个受 Grant 约束的异步 Capability
  ≠ 可以自由创建子 Agent 的管理者
```

## 阶段 A：Grant 强制化

### 1. 统一授权入口

修改：

- `python/authorization.py`
- `python/executor/__init__.py`
- `python/api/server.py`

改动：

- 增加 `require_grant` 运行模式；真实 API/状态机路径开启，直接使用 Executor 的旧单测路径保留显式 legacy 兼容模式。
- `DefaultExecutor.execute_step()` 在执行前必须验证：Grant 存在、未撤销、未过期、工具匹配、资源匹配、调用次数未超限。
- 将 `grant_id` 和授权决定写入工具审计及步骤事件。
- 工具拒绝时保证不进入 `ToolRegistry.execute_tool()`。
- 所有异常、超时、取消路径都在 `finally` 撤销 Grant。

### 2. 收紧资源策略但保留现有 E2E 语义

修改：

- `python/authorization.py`
- `config/models.yaml`
- `tests/python/test_authorization.py`

规则：

- 相对路径必须位于 Grant 的 workspace root 内。
- 绝对路径不再获得“整个外部目录”权限，只授权当前步骤明确声明的精确路径；`.env`、`.git/**` 等敏感路径始终拒绝。
- 增加 `allow_absolute_paths` 配置项：兼容模式允许“明确声明的绝对路径”，严格模式拒绝 workspace 外绝对路径。默认先使用兼容模式，以保证当前真实 E2E 不回归。
- 命令、包安装和 Git 未指定 cwd/path 时继续强制绑定 Grant workspace。
- 网络 URL 只有在 `allowed_hosts` 明确配置时才允许；现有 WebFetcher URL 校验保留为第二层。

### 3. 真正启用模型调用预算

新增/修改：

- `python/authorization.py`
- `python/llm_client/__init__.py`
- 必要时新增 `python/grant_llm_client.py`

采用显式绑定方式，不用全局隐式变量：

```python
await grant_llm_client.chat(grant_id, ...)
```

规则：

- 每次 Agent Tool 的模型调用先调用 `record_model_call(grant_id)`。
- 超过 `max_model_calls` 返回 `budget_exceeded`，不请求上游模型。
- 记录 input/output token（Provider 返回 usage 时）和调用次数；当前 Designer 顶层生成仍属于系统操作，不占步骤 Grant 预算。
- 不把 Grant 内容或授权规则注入模型上下文，只向模型提供任务数据和必要工具 Schema。

## 阶段 B：Agent 作为统一 Capability

### 4. 新增异步 Capability/Agent Tool 契约

新增：

- `python/capabilities.py`

定义：

```python
CapabilitySpec
  id / version / kind
  input_schema / output_schema
  required_permissions
  side_effect / idempotent
  budget_spec / acceptance_spec

CapabilityRequest
  work_item_id / goal / inputs / artifact_refs
  allowed_tools / acceptance / grant_id

CapabilityResult
  status / output / artifacts / facts
  acceptance_evidence / usage
  error

AgentTool(Protocol)
  spec()
  async run(request, runtime_context) -> CapabilityResult
```

约束：

- `kind` 支持 `deterministic_tool`、`agent_tool`、`human_tool`。
- `AgentTool` 默认 `can_spawn_agents = false`、`max_nested_depth = 0`。
- Runtime 传入 Grant，Agent Tool 不能签发、复制或扩大 Grant。
- Agent 之间只传 `artifact_refs` 和结构化 facts，不传完整思考历史。

### 5. 与现有 ToolRegistry 分层兼容

修改：

- `python/tools/__init__.py`
- `python/executor/__init__.py`
- 新增 `python/capabilities.py` 的 registry/adapter

不把异步 Agent 硬改成同步 `ToolRegistry.execute_tool()`；采用：

```text
ToolRegistry.execute_tool()       → 保留现有同步普通工具
CapabilityRegistry.run()          → 新增异步 Agent/人工 Capability
Runtime.dispatch_capability()     → 统一 Grant、事件、预算、验收
```

增加一个 `ToolCapabilityAdapter`，让现有普通工具也能以 Capability 方式被 Designer 调度；这样普通工具和 Agent Tool 最终共享同一份输入/输出/权限/验收协议。

### 6. 先实现可测试的 Agent Tool 骨架

新增：

- `python/agent_tools.py`
- `tests/python/test_agent_tools.py`

第一版不做复杂 Coding Agent，而实现：

- `BaseAgentTool`：统一读取 WorkItem、Grant 和 Artifact 引用。
- `SingleTurnAgentTool`：一次模型调用，返回结构化 `CapabilityResult`；不能调用未列入 Grant 的工具，不能创建子 Agent。
- `FakeAgentTool`：测试用实现，用于验证 Runtime 调度、预算和拒绝语义。

先用 FakeAgentTool 验证协议，再把现有 Designer/Executor 接入；Coding/Test/Review Agent 作为后续具体实现，不在这一轮伪装成已经完成。

### 7. Runtime 调度与验收

修改：

- `python/api/server.py`
- `python/acceptance_runner.py`（仅在需要时扩展结果类型）
- `python/database.py`（必要的事件/审计字段复用现有 JSON 容器）

Agent Tool 执行流程：

```text
Designer WorkItem
  → Runtime issue Grant
  → CapabilityRegistry.run
  → grant_llm_client.record_model_call
  → Agent 返回 ToolCall/Final/NeedsInput
  → Runtime 再次校验每个内部工具调用
  → 写 Artifact/Event/Usage
  → AcceptanceRunner 验收
  → revoke Grant
```

本轮只允许单轮 Agent Tool，不实现 Agent 内部任意多轮工具循环；这样先验证“Agent 是工具”的边界和成本，而不是重新造一个失控 Agent Loop。

### 8. 测试覆盖

新增/扩展：

- `tests/python/test_authorization.py`
  - API 强制 Grant；无 Grant、错误工具、越界资源、敏感资源、过期、撤销、次数/模型预算拒绝。
- `tests/python/test_grant_llm_client.py`
  - 模型预算在上游请求前扣减；超限不发请求；usage 记录。
- `tests/python/test_agent_tools.py`
  - Agent Tool 只收到受限 WorkItem；不能 spawn；Grant 不可扩大；结构化结果和 Artifact 引用。
- `tests/python/test_capabilities.py`
  - 普通 Tool Adapter 与 Agent Tool 共用 Capability 契约。
- `tests/python/test_exec_e2e.py`
  - 真实 LLM 文件创建仍成功；Grant issued/allowed/revoked 事件和审计可查。

验证命令：

```bash
python -m pytest tests/python/test_authorization.py tests/python/test_grant_llm_client.py tests/python/test_agent_tools.py tests/python/test_capabilities.py -q --no-cov
python -m pytest tests/python -q --no-cov --ignore=tests/python/test_exec_e2e.py
python -m pytest tests/python/test_exec_e2e.py -q --no-cov
cd frontend && npm run test && npm run build
```

### 9. 不在本轮实现

- OPA/Cerbos 外置策略服务。
- Docker/Firecracker/OS 级沙箱。
- Agent 任意多轮工具循环。
- Agent 动态创建 Agent 或递归子图。
- 多写 Agent worktree 并行。
- `pass^3` 多 Run 统计。

### 10. 设计稿更新

完成后只更新：

- `docs/AGENT_HARNESS_DESIGN.md`：新增 Grant 强制化和 Agent Tool 实施记录、版本号和遗留边界。

保留已验证事实：当前 EAS 的 DB 状态机、AcceptanceRunner、血缘回滚和普通 ToolRegistry 不推翻；新增 Capability 层作为兼容适配层。