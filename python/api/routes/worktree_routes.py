"""Worktree 隔离路由（自 server.py 拆分；行为不变）。"""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse


from api.auth import require_token
from api.shared import state, broadcast_log, _require_write

from logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


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

