"""任务模式识别入口路由（自 server.py 拆分；行为不变）。"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_token
from api.shared import state
from authorization import GrantLimits

router = APIRouter()


@router.post("/api/quick-task", dependencies=[Depends(require_token)])
async def quick_task(request: Dict[str, Any]):
    """任务模式识别入口（设计稿 §4）。

    direct：单一读取/搜索/测试类动作 → 签发一次性 Grant 直接调用工具并返回
    结构化结果（不绕过 Grant/审计/blocked 语义，行为与计划内工具调用一致）。
    graph：创建/修改/多步任务 → 引导走 /api/design 设计书流程。
    """
    body = request or {}
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="缺少 message")

    from application.task_router import classify_task
    classification = classify_task(message)

    if classification["mode"] != "direct":
        return {
            "success": True,
            "mode": "graph",
            "reason": classification.get("reason"),
            "next": "/api/design",
        }

    tool_id = classification["tool_id"]
    instruction = classification["instruction"]
    inputs = dict(classification.get("inputs") or {})
    if not state.tool_registry.get_tool(tool_id):
        return {
            "success": True,
            "mode": "graph",
            "reason": f"意图工具 {tool_id} 未注册，回退设计书流程",
            "next": "/api/design",
        }

    # 直接工具模式同样受 Grant 约束：签发一次性授权（量级同设计期搜索），
    # 执行前强制 authorize（资源/deny 规则与计划内工具调用同一闸门）。
    grant = state.authorization_manager.issue_grant(
        subject="direct_mode",
        allowed_tools={tool_id},
        limits=GrantLimits(max_tool_calls=3, max_model_calls=0, max_elapsed_seconds=600),
    )
    try:
        decision = state.authorization_manager.authorize(
            grant.grant_id, tool_id, instruction, inputs
        )
        if not decision.allowed:
            result = {
                "success": False,
                "error": decision.message,
                "blocked": True,
                "blocked_reason": decision.reason,
            }
        else:
            result = await asyncio.to_thread(
                state.tool_registry.execute_tool, tool_id, instruction, inputs
            )
    except Exception as tool_exc:
        result = {"success": False, "error": str(tool_exc), "blocked": False}
    finally:
        state.authorization_manager.revoke(grant.grant_id)

    blocked = bool(result.get("blocked"))
    state.database.save_audit_log(
        tool_id=tool_id,
        instruction=instruction,
        inputs=inputs,
        success=bool(result.get("success")),
        blocked=blocked,
        block_reason=result.get("error") if blocked or not result.get("success") else None,
        actor="direct_mode",
        grant_id=grant.grant_id,
    )
    return {
        "success": True,
        "mode": "direct",
        "tool_id": tool_id,
        "instruction": instruction,
        "result": result,
        "grant_usage": grant.usage(),
    }


