"""Agent 运行记录路由（自 server.py 拆分；行为不变）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_token
from api.shared import state

router = APIRouter()


@router.get("/api/agent-sessions", dependencies=[Depends(require_token)])
async def list_agent_sessions(plan_id: Optional[str] = None, step_id: Optional[str] = None, limit: int = 100):
    """Agent 运行记录（Session 层）：每次 agent_tool 步骤执行一条。"""
    sessions = state.database.list_agent_sessions(plan_id=plan_id, step_id=step_id, limit=limit)
    return {"success": True, "sessions": sessions}


@router.get("/api/agent-sessions/{session_id}/turns", dependencies=[Depends(require_token)])
async def get_agent_session_turns(session_id: str):
    """Agent 运行记录（Turn 层）：一次 session 内逐轮模型调用与结果。"""
    sessions = state.database.list_agent_sessions(limit=1000)
    if not any(s["session_id"] == session_id for s in sessions):
        raise HTTPException(status_code=404, detail="agent session 不存在")
    return {"success": True, "turns": state.database.list_agent_turns(session_id)}


