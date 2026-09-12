"""计划路由（列表/详情/执行控制/生命周期/回滚/runs）（自 server.py 拆分；行为不变）。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from tools.git_operator import GitOperator

from api.auth import require_token
from api.shared import (
    state, broadcast_plan_update, broadcast_log, format_plan_for_frontend,
    _require_write, _compute_pause_checkpoint,
)
from models import PlanStatus, StepStatus, RunStatus, AttemptStatus


from logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/api/plans", dependencies=[Depends(require_token)])
async def get_plans(limit: int = 10):
    """获取执行计划列表"""
    try:
        plans_raw = state.database.list_plans(limit=limit)
        plans = []
        for p in plans_raw:
            plans.append({
                "plan_id": p.get("plan_id", ""),
                "design_id": p.get("design_id", ""),
                "status": p.get("status", ""),
                "display_name": p.get("display_name") or "",
                "archived": bool(p.get("archived", 0)),
            })
        return {
            "success": True,
            "plans": plans
        }
    except Exception as e:
        logger.exception("查询执行计划列表失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/plan/{plan_id}", dependencies=[Depends(require_token)])
async def get_plan(plan_id: str):
    """获取单个执行计划"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        
        return {
            "success": True,
            "plan": format_plan_for_frontend(plan)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("查询执行计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/plan/{plan_id}/runs", dependencies=[Depends(require_token)])
async def get_plan_runs(plan_id: str, limit: int = 100):
    """获取计划执行 Run 历史；不向客户端暴露 owner_token。"""
    if not state.database.get_plan_status(plan_id):
        raise HTTPException(status_code=404, detail="执行计划不存在")
    runs = state.database.list_execution_runs(plan_id, limit=limit)
    for run in runs:
        run.pop("owner_token", None)
    return {"success": True, "runs": runs}


@router.get("/api/run/{run_id}", dependencies=[Depends(require_token)])
async def get_run(run_id: str):
    """获取单个执行 Run；不向客户端暴露 owner_token。"""
    run = state.database.get_execution_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="执行 Run 不存在")
    run.pop("owner_token", None)
    return {"success": True, "run": run}


@router.get("/api/run/{run_id}/attempts", dependencies=[Depends(require_token)])
async def get_run_attempts(run_id: str, limit: int = 500):
    """获取 Run 的 Attempt 历史；不返回任何 owner_token。"""
    if not state.database.get_execution_run(run_id):
        raise HTTPException(status_code=404, detail="执行 Run 不存在")
    attempts = state.database.list_execution_attempts(run_id, limit=limit)
    for attempt in attempts:
        attempt.pop("owner_token", None)
    return {"success": True, "attempts": attempts}


@router.post("/api/plan/{plan_id}/execute", dependencies=[Depends(require_token)])
async def execute_plan(plan_id: str):
    """执行计划"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")

        # check-then-set 必须在同一把锁内完成。
        # 否则两个并发请求可以同时通过检查，产出两个后台任务争抢同一份 DB 和文件系统。
        # 绑定 active worktree 的计划走隔离工作区，不占用主工作区执行权，
        # 可与其它计划并行（同计划重复执行由 DB 状态机原子认领兜底拒绝）。
        _worktree_bound = state.database.get_active_worktree(plan_id) is not None
        if not _worktree_bound:
            async with state.execution_lock:
                if state.current_execution:
                    # 认领残留自愈：进程重启可能发生在 claim 之后、执行任务死亡之前，
                    # 留下"current_execution 残留 + 计划非 Running"僵局（孤儿恢复不覆盖此态）。
                    # 只有被认领计划确实 Running 时才视为真冲突（409）；否则释放残留并放行。
                    claimed = state.current_execution
                    claimed_status = state.database.get_plan_status(claimed)
                    if claimed_status == "Running":
                        raise HTTPException(
                            status_code=409,
                            detail=f"已有任务在执行中: {claimed}",
                        )
                    logger.warning("释放残留执行认领: {}（计划 DB 状态: {}，非 Running）", claimed, claimed_status)
                    state.current_execution = None
                    state.current_execution_owner = None
                state.current_execution = plan_id

        # 在后台执行（run 由 execute_plan_background 内部原子认领并签发）
        from api.server import execute_plan_background  # 延迟导入：运行时留在 server，避免装配期循环
        task = asyncio.create_task(execute_plan_background(plan))
        def _bg_done(t):
            try:
                exc = t.exception()
            except Exception as e:
                exc = e
            try:
                with open('D:/spot/harness/.tmp/bg_done.log', 'a', encoding='utf-8') as f:
                    f.write(f'plan={plan.plan_id} exc={exc}\n')
                    f.flush()
            except Exception:
                pass
        task.add_done_callback(_bg_done)

        return {
            "success": True,
            "message": "执行已开始"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("启动计划执行失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/plan/{plan_id}/pause", dependencies=[Depends(require_token)])
async def pause_plan(plan_id: str):
    """暂停正在执行的计划。执行循环会在下一个步骤开始前检测到 Paused 状态并退出。

    暂停时一次性写入恢复检查点（resume_from_step_id / paused_at_step_id）：
    状态与断点在同一条 UPDATE 里落库，保证原子性——不会出现「状态已 Paused
    但断点缺失」的中间态，恢复端点因此总能拿到一致的断点。
    """
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")

        current_status = state.database.get_plan_status(plan_id)
        if current_status not in ("Running", "Pending"):
            raise HTTPException(
                status_code=409,
                detail=f"只能暂停 Running/Pending 状态的计划，当前状态: {current_status}",
            )

        branch_version = getattr(plan, 'current_branch', 1) or 1
        paused_at_step_id, resume_from_step_id = _compute_pause_checkpoint(plan_id, branch_version)

        if paused_at_step_id is None and resume_from_step_id is None:
            # 当前分支上没有 Running/Pending/Retry 步骤（例如全部已完成）：
            # update_plan_state_machine 的 COALESCE 语义下不传字段即保留旧断点，
            # 避免把可能有用的历史断点误清成 NULL。
            _require_write(
                state.database.update_plan_state_machine(plan_id, "Paused"),
                f"计划 {plan_id} 状态 → Paused",
            )
        else:
            # 单条 UPDATE 同时写状态与断点（原子）。个别字段为 None 时
            # COALESCE 保留旧值（例如存在 Running 步骤但没有 Pending/Retry 步骤）。
            _require_write(
                state.database.update_plan_state_machine(
                    plan_id,
                    "Paused",
                    resume_from_step_id=resume_from_step_id,
                    paused_at_step_id=paused_at_step_id,
                ),
                f"计划 {plan_id} 状态 → Paused（含恢复断点）",
            )

        await broadcast_plan_update(plan_id)
        await broadcast_log(f"计划 {plan_id} 已请求暂停", "info")
        return {"success": True, "message": "暂停请求已发出，将在当前步骤完成后生效"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("暂停计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


def _compute_pause_checkpoint(plan_id: str, branch_version: int):
    """
    计算暂停检查点，返回 (paused_at_step_id, resume_from_step_id)。

    - paused_at_step_id: 当前分支上第一个 status == Running 的活动步骤（暂停发生地）
    - resume_from_step_id: 当前分支上第一个 status ∈ (Pending, Retry) 的活动步骤
      （按 step_order 排序）；没有运行中步骤时它就是第一个未完成步骤。

    找不到返回 None，由调用方决定保留旧断点（COALESCE）。数据库故障沿
    DatabaseError 向上抛给端点的统一异常处理。
    """
    rows = state.database.get_active_steps_snapshot(plan_id, branch_version)
    paused_at = next(
        (str(r["step_id"]) for r in rows if r["status"] == StepStatus.RUNNING.value),
        None,
    )
    resume_from = next(
        (str(r["step_id"]) for r in rows
         if r["status"] in (StepStatus.PENDING.value, StepStatus.RETRY.value)),
        None,
    )
    return paused_at, resume_from


@router.post("/api/plan/{plan_id}/resume", dependencies=[Depends(require_token)])
async def resume_plan(plan_id: str):
    """恢复已暂停的计划，从断点（第一个非 Success/Skipped 的步骤）重新开始执行。"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")

        current_status = state.database.get_plan_status(plan_id)
        if current_status != "Paused":
            raise HTTPException(
                status_code=409,
                detail=f"只能恢复 Paused 状态的计划，当前状态: {current_status}",
            )

        # 绑定 active worktree 的计划走隔离工作区，不占用主工作区执行权。
        _worktree_bound = state.database.get_active_worktree(plan_id) is not None
        if not _worktree_bound:
            async with state.execution_lock:
                if state.current_execution:
                    # 认领残留自愈：进程重启可能发生在 claim 之后、执行任务死亡之前，
                    # 留下"current_execution 残留 + 计划非 Running"僵局（孤儿恢复不覆盖此态）。
                    # 只有被认领计划确实 Running 时才视为真冲突（409）；否则释放残留并放行。
                    claimed = state.current_execution
                    claimed_status = state.database.get_plan_status(claimed)
                    if claimed_status == "Running":
                        raise HTTPException(
                            status_code=409,
                            detail=f"已有任务在执行中: {claimed}",
                        )
                    logger.warning("释放残留执行认领: {}（计划 DB 状态: {}，非 Running）", claimed, claimed_status)
                    state.current_execution = None
                state.current_execution = plan_id

        from api.server import execute_plan_background  # 延迟导入：同上
        asyncio.create_task(execute_plan_background(plan, resume=True))

        return {"success": True, "message": "已从断点恢复执行"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("恢复计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/plan/{plan_id}/skip-step/{step_id}", dependencies=[Depends(require_token)])
async def skip_step(plan_id: str, step_id: str):
    """把指定步骤标记为 Skipped。正在进行中的执行循环会在避开该步骤时直接跳过不执行。

    守卫：
    - 计划已进入终态（Completed/Failed/RolledBack）时拒绝跳过；
    - 按步骤的「生效状态」判断——回滚后活动行是克隆行（parent_step_id 指向原 id），
      生效状态取活动克隆行，无克隆才回落到原行；生效状态非 Pending/Retry 拒绝跳过。
    """
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")

        current_status = state.database.get_plan_status(plan_id)
        if current_status in ("Completed", "Failed", "RolledBack"):
            raise HTTPException(
                status_code=409,
                detail=f"计划已结束（当前状态: {current_status}），无法跳过步骤",
            )

        steps = state.database.get_steps(plan_id)
        if not any(s["step_id"] == step_id for s in steps):
            raise HTTPException(status_code=404, detail="步骤不存在")

        branch_version = getattr(plan, 'current_branch', 1) or 1
        effective_status = state.database.get_effective_step_status(plan_id, step_id, branch_version)
        if effective_status is None:
            # 存在性检查已通过，这里兜底（理论上不可达）
            raise HTTPException(status_code=404, detail="步骤不存在")
        if effective_status not in ("Pending", "Retry"):
            raise HTTPException(status_code=409, detail="步骤已在执行或已完成，无法跳过")

        # 跳过需沿克隆血缘传播：活动分支上的克隆行若保持 Pending，执行时会照常运行
        if not state.database.mark_step_tree_skipped(plan_id, step_id, executed_at=datetime.now()):
            raise HTTPException(status_code=500, detail="跳过失败")

        await broadcast_plan_update(plan_id)
        await broadcast_log(f"步骤 {step_id} 已被手动跳过", "info")
        return {"success": True, "message": "步骤已标记为跳过"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("跳过步骤失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


# ===== P0-a 工具级审批端点 =====

@router.post("/api/plan/{plan_id}/archive", dependencies=[Depends(require_token)])
async def archive_plan(plan_id: str, request: Optional[Dict[str, Any]] = None):
    """归档 / 取消归档计划（归档不删除数据，仅从默认列表隐藏）。"""
    try:
        body = request or {}
        archived = bool(body.get("archived", True))
        if not state.database.get_plan_status(plan_id):
            raise HTTPException(status_code=404, detail="执行计划不存在")
        if not state.database.set_plan_archived(plan_id, archived):
            raise HTTPException(status_code=500, detail="归档状态写入失败")
        return {"success": True, "plan_id": plan_id, "archived": archived}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("归档计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/plan/{plan_id}/rename", dependencies=[Depends(require_token)])
async def rename_plan(plan_id: str, request: Optional[Dict[str, Any]] = None):
    """重命名计划（可读备注名，用于列表展示替代 UUID）。"""
    try:
        body = request or {}
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="缺少名称")
        if len(name) > 100:
            raise HTTPException(status_code=400, detail="名称过长（不超过 100 字符）")
        if not state.database.get_plan_status(plan_id):
            raise HTTPException(status_code=404, detail="执行计划不存在")
        if not state.database.rename_plan(plan_id, name):
            raise HTTPException(status_code=500, detail="重命名写入失败")
        return {"success": True, "plan_id": plan_id, "name": name}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("重命名计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/plan/{plan_id}", dependencies=[Depends(require_token)])
async def delete_plan(plan_id: str):
    """删除执行计划（连同其全部步骤与事件；审计日志保留作为历史记录）。

    Running/Paused 状态的计划必须先暂停，不允许直接删除。
    """
    try:
        current_status = state.database.get_plan_status(plan_id)
        if not current_status:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        if current_status in ("Running", "Paused"):
            raise HTTPException(
                status_code=409,
                detail=f"计划正在执行（{current_status}），请先暂停后再删除",
            )
        if not state.database.delete_plan(plan_id):
            raise HTTPException(status_code=500, detail="删除计划失败")
        return {"success": True, "plan_id": plan_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("删除计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/plan/{plan_id}/rollback", dependencies=[Depends(require_token)])
async def rollback_plan(plan_id: str, request: Optional[Dict[str, Any]] = None):
    """回滚到指定 step，并生成新 branch。"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")

        body = request or {}
        target_step_id = body.get("step_id")
        strategy = body.get("strategy", "retry")
        if not target_step_id:
            raise HTTPException(status_code=400, detail="缺少 step_id")

        current_branch = getattr(plan, 'current_branch', 1) or 1
        new_branch = current_branch + 1

        # 先克隆到新分支并校验结果，再废弃旧分支步骤——
        # 任何一步失败都不得留下「步骤已废弃但没有克隆」的破损状态。
        if not state.database.clone_steps_from(plan_id, target_step_id, new_branch):
            raise HTTPException(status_code=500, detail="克隆步骤到新分支失败，回滚未执行")
        marked = state.database.mark_steps_obsolete(plan_id, target_step_id, current_branch)
        _require_write(
            state.database.update_plan_state_machine(
                plan_id,
                PlanStatus.PENDING.value,
                resume_from_step_id=target_step_id,
                current_branch=new_branch,
            ),
            f"计划 {plan_id} 状态 → Pending（回滚到步骤 {target_step_id}，新分支 {new_branch}）",
        )
        state.database.add_step_event(
            target_step_id,
            plan_id,
            "rolled_back",
            {"strategy": strategy, "new_branch": new_branch, "marked_obsolete": marked},
            branch_version=new_branch,
        )
        await broadcast_plan_update(plan_id)
        await broadcast_log(f"计划已回滚到 {target_step_id}，新 branch={new_branch}", "warning")
        return {
            "success": True,
            "new_branch": new_branch,
            "resume_from": target_step_id,
            "strategy": strategy,
            "marked_obsolete": marked,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("回滚计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


# ===== Worktree 隔离端点：创建绑定 / 查看 / 合并 / 丢弃 =====
# 合并与丢弃是用户显式发起的人工动作（人即审批方）；Running 状态一律拒绝。

@router.post("/api/plan/{plan_id}/worktree", dependencies=[Depends(require_token)])
async def create_plan_worktree(plan_id: str, request: Optional[Dict[str, Any]] = None):
    """为计划创建并绑定隔离 worktree（新分支 worktree/<name>）。"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        if state.database.get_active_worktree(plan_id):
            raise HTTPException(status_code=409, detail="该计划已绑定 active worktree")

        body = request or {}
        name = str(body.get("name") or f"plan-{str(plan_id)[:8]}")
        from tools.git_operator import GitOperator
        repo_path = str(state.authorization_manager.workspace_root)
        result = GitOperator.execute("worktree_add", {"path": repo_path, "name": name})
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error") or "worktree 创建失败")

        worktree_id = str(uuid4())
        state.database.create_worktree_record(
            worktree_id=worktree_id,
            plan_id=plan_id,
            repo_path=repo_path,
            worktree_path=result["worktree_path"],
            branch=result["branch"],
            base_commit=result.get("base_commit"),
        )
        await broadcast_log(
            f"[WORKTREE] 计划 {plan_id} 绑定隔离 worktree {result['worktree_path']}", "info"
        )
        return {"success": True, "worktree_id": worktree_id, **{
            k: result[k] for k in ("worktree_path", "branch", "base_commit") if k in result
        }}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("创建 worktree 失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/plan/{plan_id}/worktree", dependencies=[Depends(require_token)])
async def get_plan_worktree(plan_id: str):
    """查看计划的 worktree 绑定状态与相对主仓库的变更摘要。"""
    plan = state.database.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="执行计划不存在")
    binding = state.database.get_active_worktree(plan_id)
    if not binding:
        return {"success": True, "worktree": None}
    from tools.git_operator import GitOperator
    diff = GitOperator.execute("worktree_diff", {
        "path": binding["repo_path"], "worktree_path": binding["worktree_path"],
    })
    return {
        "success": True,
        "worktree": {k: binding[k] for k in (
            "worktree_id", "repo_path", "worktree_path", "branch", "base_commit", "created_at"
        )},
        "diff": {"stat": diff.get("stat"), "patch_truncated": diff.get("patch_truncated")} if diff.get("success") else {"error": diff.get("error")},
    }


@router.post("/api/plan/{plan_id}/worktree/merge", dependencies=[Depends(require_token)])
async def merge_plan_worktree(plan_id: str, request: Optional[Dict[str, Any]] = None):
    """把计划的 worktree 分支合并回主仓库（人工发起；冲突不自动处理）。"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        current_status = state.database.get_plan_status(plan_id)
        if current_status == "Running":
            raise HTTPException(status_code=409, detail="计划执行中，禁止合并 worktree")
        binding = state.database.get_active_worktree(plan_id)
        if not binding:
            raise HTTPException(status_code=404, detail="该计划没有 active worktree 绑定")

        body = request or {}
        from tools.git_operator import GitOperator
        result = GitOperator.execute("worktree_merge", {
            "path": binding["repo_path"],
            "worktree_path": binding["worktree_path"],
            "message": body.get("message"),
        })
        state.database.save_audit_log(
            tool_id="worktree_merge",
            instruction=f"merge worktree {binding['worktree_path']} -> {binding['repo_path']}",
            inputs={"plan_id": plan_id, "branch": binding["branch"]},
            plan_id=plan_id,
            success=bool(result.get("success")),
            blocked=bool(result.get("conflict")),
            block_reason=result.get("error") if not result.get("success") else None,
            actor="user",
        )
        if not result.get("success"):
            # 冲突/失败保持 active 绑定，由用户解决后重试或丢弃。
            return JSONResponse(status_code=409, content={
                "success": False,
                "conflict": bool(result.get("conflict")),
                "message": result.get("message"),
                "output": result.get("output"),
                "error": result.get("error"),
            })
        state.database.close_worktree(binding["worktree_id"], "merged")
        await broadcast_plan_update(plan_id)
        await broadcast_log(
            f"[WORKTREE] 计划 {plan_id} 的分支 {result.get('branch')} 已合并回主仓库", "success"
        )
        return {"success": True, "branch": result.get("branch"), "output": result.get("output")}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("合并 worktree 失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/plan/{plan_id}/worktree/discard", dependencies=[Depends(require_token)])
async def discard_plan_worktree(plan_id: str):
    """丢弃计划的 worktree（移除目录与分支绑定，标记 discarded）。"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        current_status = state.database.get_plan_status(plan_id)
        if current_status == "Running":
            raise HTTPException(status_code=409, detail="计划执行中，禁止丢弃 worktree")
        binding = state.database.get_active_worktree(plan_id)
        if not binding:
            raise HTTPException(status_code=404, detail="该计划没有 active worktree 绑定")

        from tools.git_operator import GitOperator
        result = GitOperator.execute("worktree_remove", {
            "path": binding["repo_path"], "worktree_path": binding["worktree_path"],
        })
        state.database.close_worktree(binding["worktree_id"], "discarded")
        await broadcast_log(
            f"[WORKTREE] 计划 {plan_id} 的 worktree 已丢弃: {binding['worktree_path']}", "warning"
        )
        return {"success": True, "removed": bool(result.get("success")), "detail": result.get("message")}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("丢弃 worktree 失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


# ===== 动态重规划：失败计划生成修复步骤并挂入图（Level-3 介入的落地形态）=====

