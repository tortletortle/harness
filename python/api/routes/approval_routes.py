"""工具级审批路由（自 server.py 拆分；行为不变）。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import require_token

from api.shared import state, broadcast_approval_resolved, broadcast_log
from designer import GenerationError

from logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


class ApprovalDecisionRequest(BaseModel):
    """审批决定请求"""
    comment: Optional[str] = None


@router.get("/api/approvals", dependencies=[Depends(require_token)])
async def list_pending_approvals(plan_id: Optional[str] = None):
    """列出待审批的工具调用请求（前端轮询兜底；主要通知走 WebSocket）。"""
    return {"success": True, "approvals": state.approval_manager.list_pending(plan_id)}


async def _resolve_tool_approval(plan_id: str, approval_id: str, approved: bool, comment: Optional[str]):
    """approve/reject 共用逻辑：校验归属 → resolve → 广播决定。"""
    pending = {a["approval_id"]: a for a in state.approval_manager.list_pending()}
    info = pending.get(approval_id)
    if not info:
        raise HTTPException(status_code=404, detail="审批请求不存在或已处理")
    if info["plan_id"] != str(plan_id):
        raise HTTPException(status_code=409, detail="审批请求与计划不匹配")

    if not state.approval_manager.resolve(approval_id, approved, comment):
        raise HTTPException(status_code=409, detail="审批请求已被处理")

    await broadcast_approval_resolved(approval_id, approved)
    await broadcast_log(
        f"[APPROVAL] 审批 {'通过' if approved else '拒绝'}: {approval_id}", "info",
    )
    return {"success": True, "approved": approved}


@router.post("/api/plan/{plan_id}/approval/{approval_id}/approve", dependencies=[Depends(require_token)])
async def approve_tool_call(plan_id: str, approval_id: str, request: Optional[ApprovalDecisionRequest] = None):
    """批准一次待审批的工具调用，执行协程立即恢复。"""
    try:
        body = request or ApprovalDecisionRequest()
        return await _resolve_tool_approval(plan_id, approval_id, True, body.comment)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("批准工具调用失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/plan/{plan_id}/approval/{approval_id}/reject", dependencies=[Depends(require_token)])
async def reject_tool_call(plan_id: str, approval_id: str, request: Optional[ApprovalDecisionRequest] = None):
    """拒绝一次待审批的工具调用。执行协程收到结构化拒绝理由（操作未发生）。"""
    try:
        body = request or ApprovalDecisionRequest()
        return await _resolve_tool_approval(plan_id, approval_id, False, body.comment)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("拒绝工具调用失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


