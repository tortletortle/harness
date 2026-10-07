# 计划缓存外键 bug 修复记录（2026-09-10）

## 现象

A 标准真实 LLM 验收脚本在 `POST /api/design/{id}/approve` 时稳定返回 500：

```
保存执行计划失败: FOREIGN KEY constraint failed
```

真实 LLM 连接正常（stepfun 已注册、设计/计划都已生成成功），问题出在计划保存环节。

## 根因

`python/designer/__init__.py` 的 `generate_plan` 以 `design_id` 为键做计划缓存（`plan_cache`，TTL 7 天）：

```python
cache_key = str(design.design_id)
cached_plan = self.plan_cache.get(cache_key)
if cached_plan:
    logger.info("使用缓存的执行计划")
    return ExecutionPlan(**cached_plan)   # 返回缓存的旧 plan_id / step_id
```

首次审批某 design 时生成 plan `P1`（新 `plan_id`），保存、执行，产生 `execution_attempts`，这些行外键引用 `steps.step_id`（`execution_attempts.step_id → steps.step_id`）。

之后对同一 design 再次触发 `generate_plan`（例如验收脚本对固定需求重跑），命中缓存，直接返回**旧的 `P1`（含旧 step_id）**。`approve_design` 调 `save_plan(P1)` 幂等重建：

```python
DELETE FROM steps WHERE plan_id = ?
```

但 `P1` 已真实执行过，`execution_attempts` 仍外键引用旧 step_id → 删除违约 → `FOREIGN KEY constraint failed`。

回溯定位：`database.py:366`（`DELETE FROM steps`），不是 INSERT plans。

## 修复

重新审批 = 重新规划，不能复用已被真实执行消耗的计划身份。`generate_plan` 缓存命中时复用计划结构，但重新生成 `plan_id` 和所有 `step_id`，并把步骤重置为 `Pending`（清空 outputs/state_snapshot/executed_at/retry/failure 等执行态）。

## 验证

- A 标准五任务真实验收：5 个真实 LLM 任务全部 Completed，产物 `harness_real_task_*.md` 落盘。证据 `out/real-llm-acceptance-a.json`。
- 计划/生命周期/checkpoint 回归：42 passed。
- 数据库 schema 未改，API 路径未改。
