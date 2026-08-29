## Runtime 临时 Grant 第一阶段实施计划

### 目标
把现有“按工具 ID 判断是否审批”升级为真正的调用前授权：

```text
步骤开始
  ↓
Runtime 签发一次性 Grant
  ↓
校验工具、资源、预算、时效、撤销状态
  ↓
必要时复用现有审批等待
  ↓
允许执行或结构化拒绝
  ↓
步骤结束立即撤销 Grant
```

Agent 只收到可执行结果，不持有 Grant 管理器，也不能自己创建、复制或扩大授权。

## 1. 新增授权模块

新增：

- `python/authorization.py`

定义：

```python
CapabilityGrant
GrantLimits
GrantDecision
AuthorizationManager
```

Grant 至少包含：

```text
grant_id
plan_id
step_id
subject
allowed_tools
allowed_resources
 denied_resources
approval_required
expires_at
max_tool_calls
max_model_calls
max_elapsed_seconds
used_tool_calls
used_model_calls
revoked
```

`AuthorizationManager` 提供：

```text
issue_for_step(plan_id, step, workspace_root)
authorize(grant_id, tool_id, instruction, inputs)
record_usage(grant_id, ...)
revoke(grant_id)
```

Grant 只保存在服务进程内存中；数据库和模型上下文只记录 `grant_id`、决定和用量摘要，不保存授权本体。

## 2. 资源匹配策略

第一版不引入 OPA，也不执行任意动态策略代码，使用固定类型规则 + glob 匹配：

### 文件资源

从步骤输入提取已知路径字段：

```text
path
file_path
cwd
requirements_file
package_json
output
save_to
```

相对路径相对当前 workspace 解析；路径先 `resolve()` 后做 workspace 边界检查；直接拒绝：

```text
.env
.env.*
.git/**
```

### 网络资源

从 `url` 提取主机名，按 Grant 的 `allowed_hosts` 匹配；没有允许列表时不因 Grant 自动放行新的网络目标，仍保留现有 WebFetcher URL 校验。

### 命令资源

第一版只约束 command_runner 的 `cwd` 和调用次数/时限，不把 shell 字符串伪装成完整沙箱；现有危险命令拦截继续作为第二层防护。命令内容 allowlist 和 OS/容器沙箱列为后续阶段。

## 3. Grant 默认策略与配置

在 `config/models.yaml` 增加可选的 `authorization` 配置读取（不修改现有密钥配置）：

```yaml
authorization:
  enforce: true
  default_ttl_seconds: 600
  default_max_tool_calls: 1
  default_max_model_calls: 3
  default_max_elapsed_seconds: 600
  deny_resources:
    - .env
    - .env.*
    - .git/**
```

未配置时使用同样的安全默认值；对没有可识别资源的旧步骤，Grant 允许其声明的工具在当前 workspace 内运行，继续兼容旧计划。

现有 `approval.required_tools` 仍独立生效：

```text
Grant：系统范围限制
Approval：用户是否批准这一次操作
```

本阶段不强行把审批列表改成非空，避免旧任务自动卡在等待状态；高风险工具由用户显式配置审批，Grant 本身仍会限制工具、路径、次数和时效。

## 4. 接入 Executor 和状态机

修改：

- `python/executor/__init__.py`
- `python/api/server.py`

### Executor

把 `execute_step` 扩展为兼容形式：

```python
execute_step(step, grant_id=None)
```

在调用 `ToolRegistry.execute_tool()` 之前：

```text
校验 grant 存在
  → 未撤销
  → 未过期
  → 工具匹配
  → 输入资源匹配
  → 用量/时间未超限
  → 消耗一次 tool call
  → 通过才执行工具
```

拒绝时返回现有结果协议兼容的结构化结果：

```json
{
  "success": false,
  "blocked": true,
  "blocked_reason": "resource_denied | tool_not_granted | grant_expired | budget_exceeded | grant_revoked",
  "retryable": false,
  "grant_id": "..."
}
```

绝不调用底层工具；这成为现有工具内部安全检查之外的 Runtime 第二道授权闸门。

没有传 `grant_id` 的直接 Executor 调用保留旧行为，避免破坏现有单元测试；真实 API 状态机路径始终签发并传入 Grant。

### Server

在 `_execute_step_with_state_machine()` 中：

1. 从当前 step 生成临时 Grant；
2. 将 `grant_id` 写入 `running`/审批相关事件；
3. 先执行现有工具审批等待；
4. 调用 `state.executor.execute_step(step_model, grant_id=grant_id)`；
5. 在成功、失败、异常、取消路径统一 `finally: revoke(grant_id)`；
6. 将授权决定、拒绝原因和使用次数写入 step event/state snapshot。

在 `AppState` 和 lifespan 中初始化 `AuthorizationManager`，读取可选 `authorization` 配置。

## 5. 审计和可观测性

修改：

- `python/database.py`
- 必要的审计测试

给 `audit_logs` 增加可选 `grant_id` 列并自动迁移；保留旧数据库兼容。

每次授权事件至少记录：

```text
grant_issued
grant_allowed
grant_denied
grant_revoked
```

审计中保存：

```text
grant_id
plan_id
step_id
tool_id
资源判断结果
拒绝原因
used_tool_calls
created_at / expires_at
```

不记录完整授权秘密或模型思考内容。

## 6. 回归测试

新增：

- `tests/python/test_authorization.py`

覆盖：

1. Grant 签发字段完整；
2. 正确工具允许，错误工具拒绝；
3. workspace 内路径允许；
4. workspace 外相对路径拒绝；
5. `.env`、`.git/**` 拒绝；
6. 允许的绝对路径按现有 EAS 语义工作；
7. 过期 Grant 拒绝；
8. revoke 后拒绝；
9. `max_tool_calls` 超限拒绝；
10. 时间预算超限拒绝；
11. 拒绝时底层 Registry 不被调用；
12. 拒绝结果 `blocked_reason` 和 `retryable` 正确。

扩展：

- `tests/python/test_audit_log.py`：grant_id 落库；
- `tests/python/test_tool_approval.py`：审批与 Grant 同时存在时，先审批再授权执行；
- 新增 Executor 集成用例：授权拒绝不会产生工具副作用；
- `tests/python/test_exec_e2e.py`：真实 API E2E 验证正常文件创建仍通过，并检查审计中有 grant 事件。

## 7. 验证命令

```bash
python -m pytest tests/python/test_authorization.py -q --no-cov
python -m pytest tests/python -q --no-cov --ignore=tests/python/test_exec_e2e.py
python -m pytest tests/python/test_exec_e2e.py -q --no-cov
cd frontend && npm run test
cd frontend && npm run build
```

成功标准：

- 越权工具/路径/过期/超预算均在工具调用前被拒绝；
- 被拒绝时底层工具没有运行；
- Grant 在所有终止路径撤销；
- 旧测试和真实 LLM E2E 不回归；
- 审计可按 grant_id 追溯，但模型拿不到授权本体。

## 8. 明确边界

本阶段不实现：

- OPA/Cerbos 外置策略服务；
- shell 命令内容级完整安全分析；
- Docker/Firecracker/Windows Job Object 等真正 OS 沙箱；
- 跨进程/分布式 Grant；
- pass^3 和 Run/Attempt 统计。

因此第一阶段的安全承诺是：**调用前的工具、路径、次数、时效和撤销控制**；不是完整的宿主机沙箱。