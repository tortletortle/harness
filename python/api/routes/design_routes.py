"""设计书路由（自 server.py 拆分；行为不变）。"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import require_token
from api.shared import state, broadcast_plan_update, broadcast_log, format_design_for_frontend, _require_write
from designer import GenerationError

router = APIRouter()


class DesignRequest(BaseModel):
    """生成设计书请求"""
    user_request: str

class ApprovalRequest(BaseModel):
    """审批设计书请求"""
    design_id: str
    approved: bool
    feedback: Optional[str] = None


@router.post("/api/design", dependencies=[Depends(require_token)])
async def create_design(request: DesignRequest):
    """生成设计书"""
    try:
        await broadcast_log(f"收到设计书生成请求: {request.user_request[:50]}...")
        
        # 设计期搜索 Grant（§23.3）：只读网络、预算 6 次、TTL 600s、
        # allowed_hosts 继承配置；用后撤销。未配置 allowlist 时搜索
        # 会被结构化拒绝（fail-closed），设计书照常生成。
        search_grant = state.authorization_manager.issue_grant(
            subject="design_search",
            allowed_tools=["web_fetcher"],
            limits=GrantLimits(max_tool_calls=6, max_model_calls=0, max_elapsed_seconds=600),
        )
        try:
            # 调用 Designer 生成设计书
            design = await state.designer.generate_design(
                request.user_request, search_grant_id=search_grant.grant_id
            )
        finally:
            state.authorization_manager.revoke(search_grant.grant_id)
        
        # 保存到数据库
        state.database.save_design(design)
        
        await broadcast_log(f"设计书生成完成: {design.design_id}", "success")
        if state.notifier is not None:
            asyncio.create_task(
                state.notifier.send(
                    "design_ready",
                    "设计书已生成，等待审批",
                    f"需求：{request.user_request[:120]}\n"
                    f"设计书 ID：{design.design_id}\n请到 Web 界面查看并审批。",
                    payload={"design_id": str(design.design_id)},
                    dedup_key=str(design.design_id),
                )
            )
        
        return {
            "success": True,
            "design": format_design_for_frontend(design)
        }
        
    except Exception as e:
        logger.exception("生成设计书端点失败: {}", e)
        await broadcast_log(f"设计书生成失败: {str(e)}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/designs", dependencies=[Depends(require_token)])
async def get_designs(limit: int = 10):
    """获取设计书列表"""
    try:
        designs_raw = state.database.list_designs(limit)
        # For the list view, provide minimal data the frontend needs
        designs = []
        for d in designs_raw:
            designs.append({
                "design_id": d.get("design_id", ""),
                "user_request": d.get("user_request", ""),
                "created_at": d.get("created_at", ""),
                "part1": {
                    "architecture": {"components": [], "data_flow": [], "tech_stack": []},
                    "difficulty": {"level": "N/A", "reasoning": ""},
                },
                "part2": {
                    "available_tools": [],
                    "missing_tools": [],
                    "recommendations": [],
                },
            })
        return {
            "success": True,
            "designs": designs
        }
    except Exception as e:
        logger.exception("查询设计书列表失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/design/{design_id}", dependencies=[Depends(require_token)])
async def get_design(design_id: str):
    """获取单个设计书"""
    try:
        design = state.database.get_design(design_id)
        if not design:
            raise HTTPException(status_code=404, detail="设计书不存在")
        
        return {
            "success": True,
            "design": format_design_for_frontend(design)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("查询设计书失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/design/{design_id}/approve", dependencies=[Depends(require_token)])
async def approve_design(design_id: str, request: ApprovalRequest):
    """审批设计书"""
    try:
        design = state.database.get_design(design_id)
        if not design:
            raise HTTPException(status_code=404, detail="设计书不存在")
        
        if request.approved:
            await broadcast_log(f"设计书 {design_id} 已批准，开始生成执行计划...")
            
            # 生成执行计划
            # 项目工作记忆：注入上次执行状态，让计划"接着干"而不是从零开始
            try:
                from tools.workspace import resolve_workspace_root as _rwr
                _mem_path = _rwr() / "PROJECT_MEMORY.md"
                _project_memory = _mem_path.read_text(encoding="utf-8", errors="replace")[-3000:] if _mem_path.exists() else ""
            except Exception:
                _project_memory = ""
            plan = await state.designer.generate_plan(design, project_memory=_project_memory)
            
            # 保存到数据库
            state.database.save_plan(plan)
            
            await broadcast_log(f"执行计划生成完成: {plan.plan_id}", "success")
            
            return {
                "success": True,
                "plan": format_plan_for_frontend(plan)
            }
        else:
            # 拒绝设计书：如果有反馈，让AI重新修订设计书
            if request.feedback and request.feedback.strip():
                await broadcast_log(f"设计书 {design_id} 被拒绝，AI正在根据反馈修订...")
                
                # 调用Designer的revise_design方法
                revised_design = await state.designer.revise_design(design, request.feedback)
                
                # 更新数据库中的设计书
                state.database.save_design(revised_design)
                
                await broadcast_log(f"设计书已根据反馈修订完成", "success")
                
                return {
                    "success": True,
                    "revised": True,
                    "design": format_design_for_frontend(revised_design)
                }
            else:
                # 没有反馈，只是记录拒绝
                await broadcast_log(f"设计书 {design_id} 被拒绝（无反馈）")
                return {
                    "success": True,
                    "rejected": True,
                    "message": "设计书已拒绝"
                }
            
    except Exception as e:
        logger.exception("审批设计书端点失败: {}", e)
        await broadcast_log(f"审批失败: {str(e)}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/design/{design_id}", dependencies=[Depends(require_token)])
async def delete_design(design_id: str):
    """删除设计书（已生成执行计划的设计书不可删除，需先删除对应计划）。"""
    try:
        if not state.database.design_exists(design_id):
            raise HTTPException(status_code=404, detail="设计书不存在")
        referenced = state.database.list_plans(design_id=design_id, limit=1)
        if referenced:
            raise HTTPException(
                status_code=409,
                detail="该设计书已生成执行计划，请先删除对应的执行计划",
            )
        if not state.database.delete_design(design_id):
            raise HTTPException(status_code=500, detail="删除设计书失败")
        return {"success": True, "design_id": design_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("删除设计书失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

