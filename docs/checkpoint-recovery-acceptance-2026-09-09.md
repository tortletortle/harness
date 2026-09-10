# Checkpoint 恢复真实验收记录（2026-09-09）

## 验收目标

同一计划执行两次：第一次步骤 1 失败并保留 checkpoint；第二次从同一计划的 checkpoint 恢复，步骤 1 与步骤 2 成功，计划和第二次 Run 完成，并保留审计事件与最终产物。

## 隔离验收复现

从任意目录运行：

```bash
PYTHONPATH=python python scripts/live_checkpoint_acceptance.py --output out/live-checkpoint-acceptance.json
```

脚本按自身位置定位仓库，使用临时数据库和临时工作区。步骤 1 的第一次失败由真实 `command_runner` 执行受控非零命令构造；第二次恢复时更换为成功命令，仍经过真实 Executor/工具路径。该验收不是 LLM 生成计划测试；真实 LLM 任务记录见 `docs/daily-task-trials-2026-09-09.md`。

## 运行记录（历史样本）
- 执行方式：初始化真实服务 lifespan 后，直接调用当前服务的 `execute_plan_background`；步骤 2 使用真实 `file_creator`，未注入凭据。
- 产物：`out/live-checkpoint-acceptance/final-artifact.txt`
- 产物内容：`checkpoint recovery accepted`

### 第一次运行

- 计划状态：`Failed`
- Run 状态：`Failed`
- 步骤状态：`Failed`, `Pending`
- checkpoint：`resume_from_step_id=92a42a72-fd15-48f0-8162-1557cded07a6`
- 审计事件：步骤 1 `failed`
- 失败原因：`intentional first-run failure`

### 第二次运行（同一计划恢复）

- 计划状态：`Completed`
- Run 状态（按时间倒序）：`Completed`, `Failed`
- 步骤状态：`Success`, `Success`
- checkpoint：已清除（`resume_from_step_id=null`）
- 审计事件序列包含：`failed`, `running`, `grant_issued`, `grant_allowed`, `grant_revoked`, `success`
- 最终产物：存在，内容精确为 `checkpoint recovery accepted\n`
- 步骤调用序列：步骤 1（失败）、步骤 1（恢复成功）、步骤 2（成功）

原始历史样本由旧版本脚本生成，未作为当前可重复验收证据；当前脚本的每次结果会写入 `--output` 指定的脱敏 JSON，验收失败也会先写结果再返回非零退出码。

## 历史样本回归记录

- checkpoint focused tests：通过
- lifecycle focused tests：通过（29 passed）
- 前端 Vitest：36 passed
- 前端 production build：成功
- browser_check 真实浏览器：安装 Playwright Chromium 后 11 passed
- 完整 Python suite：646 passed / 11 skipped / 2 errors；errors 仅剩 test_exec_e2e.py（需要真实 LLM 凭据）
- 完整安全审计：尚未通过（Mimosa 静态 finding 未全部复核关闭）
