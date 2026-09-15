"""Failed-plan repair: generate first, then atomically compare and commit."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_token
from api.shared import state, broadcast_plan_update, broadcast_log
from designer import GenerationError
from domain.replan import ReplanValidationError, replan_anchor
from logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.post("/api/plan/{plan_id}/replan", dependencies=[Depends(require_token)])
async def replan_plan(plan_id: str):
    """Generate a repair without writes, then commit against the original snapshot.

    A failed first step and its repairs remain on the current branch. Otherwise
    the failed suffix is cloned to a new branch. A concurrent execute/replan or
    changed checkpoint/steps invalidates the snapshot and returns HTTP 409.
    """
    try:
        database = state.database
        snapshot = database.get_replan_snapshot(plan_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        anchor, _ = replan_anchor(snapshot)
        design = database.get_design(str(snapshot["plan"]["design_id"]))
        failed_inputs = anchor.get("inputs")
        if isinstance(failed_inputs, str):
            try:
                failed_inputs = json.loads(failed_inputs)
            except (ValueError, TypeError):
                failed_inputs = {}
        # No mutations, transactions or execution locks across this await.
        repair_steps = await state.designer.generate_repair_steps(
            failed_step={
                "description": anchor.get("description", ""),
                "tool": anchor.get("tool", ""),
                "inputs": failed_inputs or {},
            },
            failure_reason=anchor.get("failure_reason") or "未知原因",
            user_request=str(design.user_request) if design else "",
        )
        if not isinstance(repair_steps, list) or not repair_steps:
            raise GenerationError("未生成有效修复步骤，原计划未修改")
        result = database.commit_replan(snapshot, repair_steps)
        if result is None:
            raise HTTPException(
                status_code=409,
                detail="计划在生成修复步骤期间已变化，本次重规划未写入，请刷新后重试",
            )
        await broadcast_plan_update(plan_id)
        await broadcast_log(
            f"[REPLAN] 计划 {plan_id} 重规划完成：插入 {result['repair_steps']} 个修复步骤"
            f"（分支 {result['new_branch']}），可重新执行", "warning",
        )
        return result
    except HTTPException:
        raise
    except ReplanValidationError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except GenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("重规划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

