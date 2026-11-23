"""Failed-plan repair: generate first, then atomically compare and commit."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_token
from api.shared import state, broadcast_plan_update, broadcast_log
from designer import GenerationError
from domain.replan import ReplanValidationError, replan_anchor
from domain.recovery_evidence import summarize_failure
from domain.recovery_experiments import build_diagnostic_protocol
from logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()



def _failure_context(anchor, snapshot):
    evidence = {
        **summarize_failure(anchor),
        "history": snapshot.get("failure_history"),
    }
    tool_id = str(anchor.get("tool") or "").split(":")[0].strip()
    capability_registry = getattr(state, "capability_registry", None)
    capability = None
    if capability_registry is not None and tool_id:
        try:
            capability = capability_registry.get(tool_id)
        except Exception:
            capability = None
    try:
        tool_spec = capability.spec() if capability is not None else None
    except Exception:
        tool_spec = None
    return evidence, build_diagnostic_protocol(evidence, tool_spec)


@router.get("/api/plan/{plan_id}/diagnostic-plan", dependencies=[Depends(require_token)])
async def get_diagnostic_plan(plan_id: str):
    """Return a deterministic, read-only diagnostic protocol for a failed step."""
    try:
        snapshot = state.database.get_replan_snapshot(plan_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        anchor, _ = replan_anchor(snapshot)
        evidence, protocol = _failure_context(anchor, snapshot)
        return {
            "success": True,
            "plan_id": str(plan_id),
            "step_id": str(anchor["step_id"]),
            "failure_evidence": evidence,
            "diagnostic_protocol": protocol,
        }
    except HTTPException:
        raise
    except ReplanValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.exception("读取诊断方案失败: {}", exc)
        raise HTTPException(status_code=500, detail="读取诊断方案失败") from exc


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
        failure_evidence, diagnostic_protocol = _failure_context(anchor, snapshot)
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
                "failure_evidence": {
                    **failure_evidence,
                    "diagnostic_protocol": diagnostic_protocol,
                },
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

