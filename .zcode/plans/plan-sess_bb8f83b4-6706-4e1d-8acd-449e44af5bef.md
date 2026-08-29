## Rust egui 完整控制台：阶段 2/3 设计与实现计划

### 目标

把当前“执行计划控制台”升级为与 Web 端能力对齐的原生桌面工作台，完整覆盖：

```text
需求输入
  → AI 生成设计书
  → 设计书审阅 / 批准 / 拒绝反馈 / AI 修订
  → 执行计划
  → 工具审批卡片
  → 执行 / 暂停 / 恢复 / 跳过 / 回滚
  → 审计日志 / 结果交付
```

不复制 Python 业务逻辑；Rust GUI 只作为客户端，所有状态、权限和执行结果仍以 Python FastAPI 为准。

---

## A. 先更新唯一主设计稿

修改 `docs/AGENT_HARNESS_DESIGN.md`，版本从 `0.8-draft` 升到 `0.9-draft`，在第 20 节补充以下内容，并在末尾追加变更记录：

### 20.7 完整控制台状态与页面模型

定义桌面端页面模式：

- `DesignInput`：需求输入和生成状态
- `DesignReview`：设计书审阅、批准、拒绝反馈、修订
- `Execution`：计划详情、步骤控制、实时日志
- `Approvals`：工具调用审批队列
- `Audit`：审计日志和筛选

定义两个审批层级：

1. 设计级审批：批准/拒绝设计书，拒绝必须带反馈，修订后留在审阅页。
2. 工具级审批：执行高风险工具前挂起，显示工具、指令、参数和备注，批准或拒绝后继续/终止步骤。

补充完整的数据流：

```text
REST 初始快照
  → 本地 DTO / State
  → WebSocket 增量事件
  → version/epoch 围栏
  → reducer
  → egui repaint
  → 30s REST 权威校准
```

明确后端接口事实：

- 设计书批准接口同时负责生成计划，没有独立的“生成计划”接口。
- 回滚当前只支持 `step_id + strategy`，本阶段不虚构自由文本反馈；用户备注只用于工具审批和设计拒绝反馈。
- 审计接口目前主要记录工具调用，审批决定和回滚仍需通过 step event / 普通日志关联展示。
- `approval_request` / `approval_resolved` 是正式 WebSocket 事件，不能当作普通日志处理。

### 20.8 Rust GUI 状态一致性约束

把当前阶段 1 暴露的风险正式写入设计：

- token 更新必须同时更新 REST client 和 WebSocket session，不能只改界面文本。
- 计划详情请求必须带 request epoch，旧计划响应不能覆盖新选择。
- REST 详情刷新不能清掉较新的 WS version/live status。
- 事件通道必须有界，日志高峰不能无限占用内存。
- 计划详情需要持续刷新，不能只刷新计划列表和服务状态。
- `Draft` 不显示可执行按钮；`RolledBack` 不计入完成进度。
- WS 认证失败、主动关闭、普通断线必须区分处理。

同步更新第 20.5 分期表：

- 阶段 1：已完成（计划列表、详情、日志、执行控制、WS/REST 同步）
- 阶段 2：本轮实现（工具审批、审计、回滚、状态一致性加固）
- 阶段 3：本轮实现（设计输入、设计书审阅和拒绝反馈）

---

## B. 阶段 1 先加固，再扩展功能

在接入审批和设计流程前，先修正当前 Rust GUI 的一致性风险。

### 1. RuntimeController 与 token 热更新

重构 `rust/gui/src/main.rs` 的后台管理方式：

- 新建 `RuntimeController`，集中持有：
  - `Arc<RwLock<ApiClient>>`
  - WebSocket 任务取消句柄
  - 当前 API base/token
  - `CancellationToken` 或等价的 `watch` 信号
- `SetToken` 必须：
  1. 使用原 API base 对新 token 做真实 `/api/status` 校验；
  2. 校验成功后原子替换 REST client token；
  3. 取消旧 WS 任务；
  4. 用新 token 重启 WS；
  5. 校验失败时保留旧 token 和旧连接，不显示“更新成功”。
- API 请求增加统一超时，避免桌面 UI 永久等待网络。

### 2. 请求 epoch 与详情一致性

- `SelectPlan` 命令携带递增 `request_id/epoch`。
- `PlanDetail` 事件带回 epoch；UI 只接受当前 epoch 的响应。
- `set_plan_detail` 保留当前计划已有的 live status/version；REST 只覆盖详情字段，不把较新的 WS 状态回退。
- 30 秒兜底轮询同时刷新：
  - 服务状态
  - 计划列表
  - 当前选中计划完整详情
- 计划切换时清理旧计划的临时控制状态，但保留按 plan_id 的最后事件版本。

### 3. 有界事件通道和版本围栏

- UI/后台/WS 通道改为有界 `tokio::sync::mpsc`。
- 日志事件使用“满载丢最旧日志”策略；状态、审批、控制结果不得丢失。
- plan_update 按 plan_id 保存 version，严格丢弃过期事件。
- 明确 `version=0/缺失` 的兼容行为：允许首次快照，但不能覆盖已知的带版本状态。
- `RolledBack` 不算完成；只有 `Success`/`Skipped` 计入进度。

---

## C. 阶段 2：工具审批、审计和回滚

### 1. DTO 与 API

扩展 `rust/gui/src/types.rs`：

- `ApprovalRequest`：approval_id、plan_id、step_id、tool_id、instruction、inputs、created_at
- `ApprovalResolved`：approval_id、approved
- `AuditLogEntry`：后端原始审计字段及 null/0/1 语义
- `RollbackResult`：new_branch、resume_from、strategy、marked_obsolete
- 审批和审计的加载状态、错误状态

扩展 `rust/gui/src/api.rs`：

- `get_pending_approvals(plan_id?)`
- `approve_tool_call(plan_id, approval_id, comment?)`
- `reject_tool_call(plan_id, approval_id, comment?)`
- `get_audit_logs(limit, plan_id?)`
- `rollback_plan(plan_id, step_id, strategy)`

所有非 2xx 都解析 FastAPI `{detail}`；控制冲突保留 HTTP 状态码和中文原因。

### 2. WebSocket 事件

扩展 `rust/gui/src/ws.rs`：

- 解析 `approval_request`
- 解析 `approval_resolved`
- 普通日志、plan_update、审批事件使用不同的 Rust 枚举，不允许统一当日志字符串处理。
- 正确解析 Close frame code；1008 认证失败停止重连，普通断线才退避重连。
- 重连时先 REST 拉取待审批列表和当前计划详情，避免错过事件。

### 3. 状态层

扩展 `rust/gui/src/state.rs`：

- `pending_approvals: Vec<ApprovalRequest>`，支持多个审批请求，不再只保留一个
- `audit_logs: Vec<AuditLogEntry>`、limit、search、loading/error
- 每个审批请求独立 in-flight 状态
- approval_resolved 只关闭匹配 approval_id 的卡片
- 保留审批失败时的卡片和备注，允许重试
- 回滚成功后清理旧计划临时状态并拉取完整详情

### 4. UI 层

扩展 `rust/gui/src/app.rs`：

- 顶部或中央上方显示审批卡片队列：工具、步骤短 ID、指令、JSON 输入、备注输入、批准/拒绝按钮
- 批准/拒绝期间只禁用当前审批卡片，不阻塞其他只读 UI
- 步骤行增加“回滚到此步”按钮和确认区：显示新分支、恢复点、废弃数量
- 新增 Audit 页面/模式：
  - 时间、工具、指令、计划短 ID、结果
  - 成功/失败/拦截/未知徽章
  - blocked reason
  - limit 和关键字过滤
- 回滚只使用后端已有的 `strategy` 字段，不伪造尚不存在的自由文本字段。

---

## D. 阶段 3：设计书生成与审阅

### 1. DTO 与 API

扩展 `types.rs`：

- Design
- DesignPart1
- Architecture
- Difficulty
- ToolAssessment
- ToolRequirement
- 设计书审批响应（plan/revised/design/rejected/message）

扩展 `api.rs`：

- `create_design(user_request)`
- `get_designs(limit)`
- `get_design(design_id)`
- `approve_design(design_id, approved, feedback?)`

Rust 端始终让请求体 `design_id` 与路径 ID 一致，避免后端当前未校验这一缺口。

### 2. 状态层

新增：

- `designs`
- `current_design`
- `input_text`
- `design_loading`
- `design_action_in_flight`
- `feedback_text`
- `design_error`

拒绝反馈只在提交成功前保留；修订失败时保留原设计和用户反馈，允许重试。

### 3. UI 层

新增设计输入/审阅模式：

- 多行需求输入框
- “生成设计书”按钮和 loading 状态
- 设计书展示：原始需求、架构组件、交互/数据流、难度、工具评估、风险
- “批准并生成计划”按钮
- “拒绝”先打开反馈输入区，不直接发请求
- 空白反馈不可提交
- `revised=true`：替换设计书并留在审阅页
- 普通拒绝：清空当前设计并回到输入模式
- 批准成功：切换执行模式并载入计划

---

## E. 测试和验收

### Rust 单元测试

新增/扩展 `cargo test -p eas-gui`：

1. 完整 PlanStatus/StepStatus DTO 解析，包括 Approved、Retry、RolledBack。
2. ApprovalRequest/Resolved、AuditLog、Design DTO 解析和 null 字段。
3. plan_update version/epoch 围栏。
4. REST 详情刷新不覆盖较新的 live status。
5. token 更新：校验失败不替换旧 client，成功才重启 WS。
6. 旧计划详情响应不能覆盖新选择。
7. 有界日志队列达到上限后的丢弃策略。
8. DesignReview 的批准、拒绝、反馈、修订状态 reducer。
9. 审批队列只关闭匹配 approval_id 的卡片。
10. 回滚和审计过滤状态。

必要时加入轻量 mock HTTP/WS 测试依赖，避免把所有接口行为只留给手动测试。

### 真实后端桌面走查

在隔离数据库和真实 token 下验证：

1. GUI 启动 → 中文字体 → 服务状态、计划列表、WS 连接。
2. token 错误 → 显示失败，不替换当前连接；token 修正 → REST/WS 同时恢复。
3. 选择两个计划快速切换 → 旧响应不覆盖新计划。
4. 执行 → plan_update → 步骤状态和日志实时刷新。
5. 暂停/恢复/跳过/回滚 → 409/成功提示与 Web 后端一致。
6. 配置高风险工具 → approval_request → GUI 批准/拒绝 → 执行继续/终止。
7. 审计页显示真实工具调用和 blocked reason。
8. 需求输入 → 真实 LLM 设计书 → 拒绝反馈 → 修订 → 批准 → 计划 → 执行。
9. 进程重启/WS 断线 → REST 快照恢复计划和审批状态。

### 全量质量门

- `cargo test --workspace`
- `pytest -q`
- `cd frontend && npm test && npm run build`
- 真实桌面 GUI 启动并完成至少一条执行控制链路
- CI 三个 job 全绿

---

## F. 提交拆分

按功能拆分，便于审阅和回滚：

1. `docs: design full Rust desktop console phases 2 and 3`
2. `fix(gui): harden runtime token, epochs, bounded events, and sync`
3. `feat(gui): add approvals, audit view, and rollback controls`
4. `feat(gui): add design generation and review flow`
5. `test(gui): add phase 2 and phase 3 regression coverage`

先完成设计稿提交，再按阶段提交实现；每个提交都跑对应测试，不把所有功能塞进一个不可回滚的大提交。