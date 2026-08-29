## AcceptanceRunner 第一阶段实施计划

### 目标
把当前“工具返回成功 + LLM 判断 YES/NO”升级为：

```text
工具执行
  ↓
AcceptanceRunner 读取真实工作区/命令结果/Git 状态
  ↓
结构化 pass/fail/evidence/reward
  ↓
执行状态机决定 Success 或 Failed
```

旧计划中的 `check_criteria: string` 保留兼容；只有带结构化 `acceptance` 的步骤走确定性验收，不会破坏现有计划和旧测试。

## 1. 新增独立验收模块

新增：

- `python/acceptance_runner.py`

提供：

```python
AcceptanceRunner.run(
    acceptance_spec,
    tool_result=None,
    workspace_root=None,
) -> AcceptanceResult
```

结果统一为：

```json
{
  "status": "passed | failed | unverified",
  "passed": true,
  "reward": 1.0,
  "checks": [
    {
      "type": "file_exists",
      "passed": true,
      "path": "src/main.py",
      "evidence": {"is_file": true, "size": 42},
      "reason": ""
    }
  ]
}
```

首批支持四种确定性检查，并兼容设计稿中的别名：

- `file_exists` / `artifact_exists`
- `content_contains`
- `exit_code` / `command_exit_code`
- `diff_scope` / `diff_scope_valid`

规则：

- 路径统一复用 `tools.workspace.resolve_workspace_path()`。
- 文件内容读取有大小上限，编码错误返回 failed/unverified，不调用 LLM 猜测。
- `exit_code` 读取本次工具的结构化 `returncode`，不重复执行命令。
- `diff_scope` 使用参数列表调用只读 Git 命令，合并 staged、unstaged、untracked 文件，再与允许范围比较。
- 未知检查类型、路径越界、Git 查询失败均 fail-closed。
- `reward=1.0` 仅代表全部检查通过，任一检查失败为 `0.0`。

## 2. 给 Step 增加结构化验收字段

修改：

- `python/models/__init__.py`
- `schemas/execution_plan.json`
- `python/database.py`

新增可选字段：

```python
acceptance: List[Dict[str, Any]] = Field(default_factory=list)
```

数据库在 `steps` 增加 `acceptance_json` 迁移列，并更新：

- `save_plan()` 保存结构化规则
- `get_plan()` 读取结构化规则
- `get_next_runnable_step()` 返回原始规则
- `clone_steps_from()` 在 rollback/branch 时复制规则

这样验收规则不会只存在内存或 prompt 中，重启、回滚和恢复仍然可用。旧数据库自动迁移，旧步骤的空值按 legacy 模式处理。

## 3. 接入 Executor，确定性优先

修改：

- `python/executor/__init__.py`

执行顺序：

```text
工具成功
  ├─ step.acceptance 非空 → AcceptanceRunner
  │    ├─ 全部通过 → Success
  │    └─ 失败/无法验证 → Failed，不调用 LLM，不重试永久性验收失败
  └─ acceptance 为空 → 保留现有 check_result() 兼容逻辑
```

返回结果新增：

```text
acceptance_status
acceptance_result
acceptance_evidence
reward
acceptance_failed
```

同时保留现有 `check_status`、`check_passed`、`blocked` 字段，避免旧 API/测试立即破坏；新增字段用于区分“安全拦截”和“验收失败”。

修正 `process(action="check_result")` 的返回契约，使其同时返回明确的 `check_status`、布尔 `check_passed` 和 acceptance 结果，而不是把字符串状态放进名为 `passed` 的字段。

## 4. 接入执行状态机和持久化证据

修改：

- `python/api/server.py`

在 `_execute_step_with_state_machine()`：

- 从数据库行读取 `acceptance_json` 并构造 Step
- 将 acceptance 结果写入 `outputs` 和 `state_snapshot`
- 在 `step_events.event_data` 中写入检查明细、证据和 reward
- 结构化验收失败写入 `failure_reason`

在 `execute_plan_background()`：

- acceptance 全通过才允许步骤进入 Success
- 结构化 acceptance 失败直接终止当前步骤，不走无意义重试
- 有结构化规则但 runner 无法验证时不再按 legacy 的 unverified 成功放行
- 保留没有 acceptance 的旧步骤行为，避免历史计划全部改变语义

## 5. 让 Designer 能生成验收规则

修改：

- `python/designer/__init__.py`

更新计划生成 prompt：

- 要求每个可自动验证的步骤生成 `acceptance` 数组
- 给出 file、content、exit code、diff scope 示例
- 不能自动验证时显式输出空数组并标记人工检查

更新解析逻辑：

- 读取模型返回的 `acceptance`
- 旧模型未返回时保持空数组和原有 `check_criteria` fallback
- 不再把所有新步骤都误认为已经有确定性验收

示例：

```json
{
  "tool": "file_creator",
  "inputs": {"path": "src/main.py", "content": "print('hello')"},
  "acceptance": [
    {"type": "file_exists", "path": "src/main.py"},
    {"type": "content_contains", "path": "src/main.py", "text": "print('hello')"}
  ]
}
```

## 6. 前端显示验收证据

修改：

- `python/api/server.py` 的 `format_plan_for_frontend()`
- `frontend/src/types/index.ts`
- `frontend/src/components/PlanView.tsx`

增加可选显示：

```text
验收：2/2 通过
奖励：1.0
证据：文件存在、内容匹配
```

失败时显示具体检查项，而不是只显示“执行成功/失败”。不把完整大文件内容直接塞进 UI 或下一次模型上下文，只展示摘要和证据引用。

## 7. 回归测试

新增：

- `tests/python/test_acceptance_runner.py`

覆盖：

- 文件存在/不存在
- 目录存在/类型错误
- 内容包含/不匹配
- 工具 returncode 0/非 0/缺失
- Git staged、unstaged、untracked 的 diff scope
- 允许范围、额外文件、未知检查类型
- 工作区越界
- 文件过大、编码异常、Git 失败时 fail-closed

扩展现有测试：

- `tests/python/test_steps_persistence.py`：验收规则保存、读取、branch clone
- `tests/python/test_p0_fixes.py`：deterministic-first 与 legacy fallback
- `tests/python/test_exec_e2e.py`：真实 LLM 生成的计划若带 acceptance，验证真实文件内容，不只验证文件存在
- 新增一个 executor/server 集成用例：验收失败不能写成 Success

## 8. 验收命令与成功标准

实施完成后依次运行：

```bash
python -m pytest tests/python/test_acceptance_runner.py -q --no-cov
python -m pytest tests/python -q --no-cov --ignore=tests/python/test_exec_e2e.py
python -m pytest tests/python/test_exec_e2e.py -q --no-cov
cd frontend && npm run test
cd frontend && npm run build
```

成功标准：

- 确定性验收器测试全部通过
- 原有后端测试不回归
- 真实 LLM E2E 仍通过
- 一个错误文件内容场景能明确返回 acceptance failed，而不是 unverified/Success
- 前端能看到验收证据

## 9. 明确暂不做

本阶段不实现：

- `pass^3` 的多次 Run 持久化统计（需要 `Run/Attempt` 表，列入下一阶段）
- LLM-as-judge 替代确定性检查
- 任意动态验收脚本执行
- 把验收命令暴露给 Agent 作为可绕过策略的工具
- Rust 侧验收器

这样先把“工具做了什么”与“环境实际变成什么”分开，之后再在这个基础上增加 pass^k 和跨任务可靠性统计。