# Harness 状态机与回溯能力设计

> 目标：把 Harness 从“线性计划执行”升级为“可持久化、可回溯、可压缩上下文的状态机执行引擎”。
> 参考：LangGraph 的状态图/中断点、Temporal 的 durable execution / event sourcing。

---

## 1. 现状问题

当前 `steps` 表只有：
- `status`：`pending/running/success/failed`
- `retry_count`
- `executed_at`

缺失：
- **状态持久化快照**：服务重启/断网后，只能从头重跑
- **分支/回溯**：失败后不能回到指定 step 继续
- **上下文压缩**：后续 step 拿不到前序摘要，只能重新读取大量日志
- **时间旅行**：不能查看“如果我在 step 3 修改了参数，会怎样”

---

## 2. 参考项目核心概念

### 2.1 LangGraph
- **StateGraph**：节点 = agent/工具，边 = 流转条件
- **持久化 checkpoint**：每次状态变化写入 checkpoint，支持断点恢复
- **Human-in-the-loop**：执行到任意节点可中断，修改 state 后继续
- **Time-travel**：基于 checkpoint 可以 replay 任意历史状态

### 2.2 Temporal
- **Workflow/Activity**：Workflow 是状态机，Activity 是执行单元
- **Event Sourcing**：所有状态变化都是 event，不直接改 state
- **Durable Execution**：server 挂了 resume 时，重放 event 即可恢复现场
- **Deterministic replay**：同一组 event 必得同一结果，保证回溯可靠

### 2.3 对 Harness 的启示
- 不要照搬 LangGraph 的图框架，Harness 本身已经是“计划→步骤”线性结构
- 吸收 **checkpoint** 思想：每个 step 执行完写快照
- 吸收 **event sourcing** 思想：step 状态变化记录 event，而非只写最终状态
- 吸收 **human-in-the-loop** 思想：执行中允许用户修改设计书/计划，从指定 step 继续

---

## 3. 新状态模型

### 3.1 Step 状态机

```
pending → running → success
               ↘ failed → retry → running → success
                         ↘ failed → failed ( exhausted )
                         ↘ skipped → skipped
```

新增状态：
- `pending`：等待执行
- `running`：执行中
- `success`：执行成功
- `failed`：执行失败
- `retry`：即将重试
- `skipped`：用户/系统跳过
- `rolled_back`：被回滚

### 3.2 Plan 状态机

```
draft → approved → executing → completed
                    ↘ failed
                    ↘ paused → executing → completed
                                    ↘ failed
```

新增：
- `paused`：用户暂停，支持从指定 step 恢复
- `rolled_back`：已回滚到历史版本

---

## 4. DB Schema 变更

### 4.1 steps 表新增字段

```sql
-- 状态快照：每次状态变化时的完整上下文
ALTER TABLE steps ADD COLUMN state_snapshot TEXT;

-- 父 step_id：支持分支/重试时的回溯链
ALTER TABLE steps ADD COLUMN parent_step_id TEXT;

-- 分支版本号：同一 plan 内多次重试/回滚的版本标识
ALTER TABLE steps ADD COLUMN branch_version INTEGER NOT NULL DEFAULT 1;

-- 压缩上下文：step 成功后的摘要，供后续 step 使用
ALTER TABLE steps ADD COLUMN compressed_context TEXT;

-- 执行耗时（毫秒）
ALTER TABLE steps ADD COLUMN duration_ms INTEGER;

-- 失败原因
ALTER TABLE steps ADD COLUMN failure_reason TEXT;

-- 是否被回滚覆盖
ALTER TABLE steps ADD COLUMN is_obsolete INTEGER NOT NULL DEFAULT 0;
```

### 4.2 新增 step_events 表（事件溯源）

```sql
CREATE TABLE IF NOT EXISTS step_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    event_type TEXT NOT NULL,          -- created/running/success/failed/retry/skipped/rolled_back
    event_data TEXT,                   -- JSON：包含 inputs/outputs/snapshot/failure_reason 等
    branch_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY (step_id) REFERENCES steps(step_id),
    FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
);
```

**为什么加 event 表？**
- Temporal 证明：只存 event 不存 state，可以通过 replay 重建任意历史版本
- Harness 当前只存 `steps.status`，是“最终状态”，丢失了过程信息
- 有了 event，回溯不是“改 DB”，而是“ replay 到某步，生成新 branch”

---

## 5. 核心流程设计

### 5.1 执行引擎状态流转

```python
# 伪代码
class StateMachineExecutor:
    async def execute_plan(self, plan_id, resume_from_step=None):
        plan = db.get_plan(plan_id)
        current_branch = db.get_current_branch(plan_id)
        
        while True:
            # 1. 获取当前可执行 step
            step = db.get_next_runnable_step(plan_id, current_branch, resume_from_step)
            if not step:
                break  # plan 完成
            
            # 2. 写入 event：running
            db.add_step_event(step.step_id, "running", {...})
            step.status = "running"
            db.update_step(step)
            
            # 3. 执行工具
            try:
                result = await tool_registry.execute_tool(step.tool, step.inputs)
                step.status = "success"
                step.outputs = json.dumps(result)
                step.duration_ms = elapsed_ms
                
                # 4. 压缩上下文
                summary = await self.compress_context(step, result)
                step.compressed_context = summary
                
                # 5. 把 summary 注入到后续 step 的 context
                db.propagate_context_to_next_steps(step, summary)
                
            except Exception as e:
                # 6. 失败处理
                step.retry_count += 1
                step.failure_reason = str(e)
                db.add_step_event(step.step_id, "failed", {"reason": str(e)})
                
                if step.retry_count < MAX_RETRIES:
                    step.status = "retry"
                    db.add_step_event(step.step_id, "retry", {})
                    await asyncio.sleep(backoff(step.retry_count))
                    continue
                else:
                    step.status = "failed"
                    plan.status = "failed"
                    db.update_plan(plan)
                    break
            
            # 7. 写入 event：success
            db.add_step_event(step.step_id, "success", {"output_summary": summary})
            db.update_step(step)
        
        plan.status = "completed"
        db.update_plan(plan)
```

### 5.2 上下文压缩

```python
async def compress_context(self, step: Step, raw_result: dict) -> str:
    """调用 generate_summary() 把原始输出压缩成摘要"""
    prompt = f"""
    以下是 step 执行结果，请压缩成 1-3 句摘要，供后续 step 作为上下文。
    保留关键信息：创建了什么文件、输出路径、关键参数、错误信息。
    
    原始输出：
    {json.dumps(raw_result, ensure_ascii=False)[:2000]}
    """
    summary = await llm_client.chat(prompt)
    return summary.strip()
```

### 5.3 回溯 / 时间旅行

```python
# POST /api/plan/{plan_id}/rollback
{
    "step_id": "step-003",      # 回滚到这一步
    "strategy": "retry"         # retry | skip | branch
}

# 处理逻辑
def rollback_plan(plan_id, target_step_id, strategy):
    plan = db.get_plan(plan_id)
    branch_version = db.get_next_branch_version(plan_id)
    
    # 1. 标记 target_step 之后的 steps 为 obsolete
    db.mark_steps_obsolete(plan_id, target_step_id, branch_version)
    
    # 2. 根据 strategy 处理 target_step 本身
    target = db.get_step(target_step_id)
    if strategy == "retry":
        target.status = "retry"
        target.retry_count = 0
        target.branch_version = branch_version
    elif strategy == "skip":
        target.status = "skipped"
        target.branch_version = branch_version
    
    # 3. 创建新 branch 的 step 副本（可选，保留原 branch 可对比）
    db.clone_steps_from(plan_id, target_step_id, branch_version)
    
    # 4. 写入 event
    db.add_step_event(target_step_id, "rolled_back", {
        "branch_version": branch_version,
        "strategy": strategy
    })
    
    return {"new_branch": branch_version, "resume_from": target_step_id}
```

---

## 6. API 设计

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/plan/{plan_id}/state` | 获取当前 plan 完整状态图 |
| GET | `/api/plan/{plan_id}/events` | 获取 step event 时间线 |
| POST | `/api/plan/{plan_id}/rollback` | 回滚到指定 step |
| POST | `/api/plan/{plan_id}/pause` | 暂停执行 |
| POST | `/api/plan/{plan_id}/resume` | 从指定 step 恢复 |
| GET | `/api/plan/{plan_id}/branches` | 列出所有 branch 版本 |

---

## 7. 前端可视化

参考：
- **LangGraph Studio**：节点图 + 实时状态高亮 + 点击查看 state
- **Temporal Web UI**：Workflow 执行 timeline + event 详情
- **Temporal Cadence**：DAG 视图 + 重试/回滚按钮

Harness 前端应实现：
1. **Plan DAG 图**：segments/steps 节点图，颜色标识状态
2. **Event Timeline**：横向时间线，显示每个 step 的 event 链
3. **Branch 对比**：支持切换 branch 查看不同执行路径
4. **回滚按钮**：点击任意 failed step，弹出“重试/跳过/分支”选项
5. **上下文查看器**：显示 step 的 compressed_context，支持展开原始输出

---

## 8. 实施优先级

| 阶段 | 内容 | 预估工期 |
|------|------|----------|
| **P0** | DB schema 迁移 + step_events 表 + Executor 状态机改造 | 2-3 天 |
| **P1** | 上下文压缩 + propagate_context + 回溯 API | 2 天 |
| **P2** | 前端 DAG/时间线可视化 | 3-5 天 |
| **P3** | Branch 对比 + 多版本管理 | 2-3 天 |

---

## 9. 风险与注意事项

1. **SQLite 并发**：event 表写入量大时，SQLite 可能成为瓶颈，后续可迁移到 PostgreSQL
2. **LLM 压缩成本**：每个 step 都调一次 `generate_summary()`，token 消耗增加，需控制摘要长度
3. **Branch 爆炸**：用户频繁回滚会产生大量 branch，需设置保留策略（如只保留最近 5 个 branch）
4. **向后兼容**：旧库没有 `step_events` 表，需 graceful fallback

---

## 10. 参考链接

- LangGraph：https://github.com/langchain-ai/langgraph
- Temporal：https://github.com/temporalio/temporal
- LangGraph durable execution：https://docs.langchain.com/oss/python/langgraph/durable-execution
- Temporal event sourcing：https://docs.temporal.io/workflows
