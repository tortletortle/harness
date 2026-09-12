"""系统/生态路由（状态、工具、chat、skills、通知、能力、配置、审计、用量、重启、WebSocket）（自 server.py 拆分；行为不变）。"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Body, WebSocket, WebSocketDisconnect

from api.auth import require_token
from api.shared import state, broadcast_log, _load_models_config, _deep_merge_dict
from database import DatabaseError
from designer import GenerationError

router = APIRouter()


class ConfigUpdateRequest(BaseModel):
    """更新配置请求"""
    api_keys: Dict[str, str]
    base_urls: Dict[str, str]


@router.get("/")
async def root():
    """健康检查"""
    return {
        "status": "ok",
        "version": "0.1.0",
        "message": "EAS 2.0 API is running"
    }

@router.get("/api/status", dependencies=[Depends(require_token)])
async def get_status():
    """获取系统状态"""
    # 安全地获取 LLM providers
    llm_providers = []
    if state.llm_client:
        try:
            llm_providers = list(state.llm_client.providers.keys())
        except (AttributeError, TypeError):
            llm_providers = []
    
    # 安全地获取工具数量
    tools_count = 0
    if state.tool_registry:
        try:
            tools_count = len(state.tool_registry.tools)
        except (AttributeError, TypeError):
            # 如果无法访问私有属性，使用公开方法
            try:
                tools_count = len(state.tool_registry.list_tools())
            except:
                tools_count = 0
    
    return {
        "status": "running",
        "llm_providers": llm_providers,
        "tools_count": tools_count,
        "current_execution": state.current_execution
    }

@router.get("/api/audit-logs", dependencies=[Depends(require_token)])
async def get_audit_logs(limit: int = 100, plan_id: Optional[str] = None):
    """查询审计日志（工具调用记录，包括被安全拦截的调用）"""
    try:
        logs = state.database.list_audit_logs(limit=limit, plan_id=plan_id)
        return {
            "success": True,
            "logs": logs
        }
    except Exception as e:
        logger.exception("查询审计日志失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/usage", dependencies=[Depends(require_token)])
async def get_usage(limit: int = 50, plan_id: Optional[str] = None):
    """LLM token 用量：总量汇总 + 按阶段分布 + 最近记录（P0 强化）。"""
    try:
        from utils import usage_tracker as _usage_tracker
        summary = _usage_tracker.get_summary(plan_id=plan_id, limit=limit)
        summary["success"] = True
        budget_limit = getattr(state, "budget_max_tokens_per_plan", 0) or 0
        summary["budget_max_tokens_per_plan"] = budget_limit
        return summary
    except Exception as e:
        logger.exception("查询用量失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/tools", dependencies=[Depends(require_token)])
async def get_tools():
    """获取工具列表"""
    try:
        tools = state.tool_registry.list_tools()
        # Format tools for frontend
        formatted_tools = []
        for tool in tools:
            # Extract capabilities as a list of instruction names
            capabilities = []
            if "capabilities" in tool and "instructions" in tool.get("capabilities", {}):
                capabilities = [inst["name"] for inst in tool["capabilities"]["instructions"]]
            
            formatted_tools.append({
                "tool_id": tool.get("tool_id", ""),
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "category": tool.get("category", "other"),
                "capabilities": capabilities,
                "requirements": []  # Can be populated from dependencies if needed
            })
        
        return {
            "success": True,
            "tools": formatted_tools
        }
    except Exception as e:
        logger.exception("查询工具列表失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/chat", dependencies=[Depends(require_token)])
async def api_chat(request: dict = Body(...)):
    """应用内置对话助手：对话 → 意图 → 工具调用（Grant 约束）→ 回复。"""
    message = str((request or {}).get("message") or "").strip()
    session_id = (request or {}).get("session_id")
    if not message:
        raise HTTPException(status_code=400, detail="message 不能为空")

    session = state.chat_manager.get_or_create(session_id)
    try:
        result = await session.ask(message)
    except Exception as e:
        logger.exception("chat 会话异常: {}", e)
        raise HTTPException(status_code=500, detail=f"chat 处理失败: {e}")
    return {
        "reply": result["reply"],
        "session_id": result["session_id"],
        "tool_calls_made": result["tool_calls_made"],
    }


@router.get("/api/chat/sessions", dependencies=[Depends(require_token)])
async def api_chat_sessions(_t: str = Depends(require_token)):
    return state.chat_manager.stats()


@router.get("/api/skills", dependencies=[Depends(require_token)])
async def api_skills(_t: str = Depends(require_token)):
    """技能包清单（生态层）：入口技能 + 知识包 + 加载失败记录。"""
    if state.skill_manager is None:
        return {"success": True, "skills": [], "skipped": []}
    return {"success": True, **state.skill_manager.describe_all()}


@router.post("/api/notify/test", dependencies=[Depends(require_token)])
async def notify_test(_t: str = Depends(require_token)):
    """发送测试通知（IM 通道连通性自检）。未配置任何通道时走本地审计。"""
    if state.notifier is None:
        raise HTTPException(status_code=400, detail="通知通道未初始化")
    results = await state.notifier.send(
        "test",
        "EAS 通知通道测试",
        "这是一条测试通知：如果你看到它，说明 IM 通道已打通。",
        payload={"source": "api/notify/test"},
    )
    return {"success": True, "results": results, "channels": len(results)}


@router.get("/api/capabilities", dependencies=[Depends(require_token)])
async def get_capabilities():
    """获取统一 Capability 清单（普通工具 Adapter + 后续 Agent Tool）。"""
    if state.capability_registry is None:
        return {"success": True, "capabilities": []}
    return {
        "success": True,
        "capabilities": [
            {
                "id": spec.id,
                "version": spec.version,
                "kind": spec.kind,
                "required_permissions": list(spec.required_permissions),
                "side_effect": spec.side_effect,
                "idempotent": spec.idempotent,
                "budget_spec": spec.budget_spec,
                "can_spawn_agents": spec.can_spawn_agents,
            }
            for spec in state.capability_registry.list_specs()
        ],
    }


@router.get("/api/config", dependencies=[Depends(require_token)])
async def get_config():
    """获取配置"""
    try:
        # 隐藏敏感信息
        safe_config = {
            "base_urls": state.config.get("base_urls", {}),
            "agents": state.config.get("agents", {}),
            "api_keys_configured": {
                k: bool(v) for k, v in state.config.get("api_keys", {}).items()
            }
        }
        return {
            "success": True,
            "config": safe_config
        }
    except Exception as e:
        logger.exception("查询配置失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/config", dependencies=[Depends(require_token)])
async def update_config(request: ConfigUpdateRequest):
    """更新配置"""
    try:
        # 安全：真实 key 只写 gitignored 的 models.local.yaml，绝不写回被跟踪的 models.yaml
        local_path = (Path(__file__).resolve().parents[2] / "config" / "models.local.yaml")
        
        local = {}
        if local_path.exists():
            with open(local_path, "r", encoding="utf-8") as f:
                local = yaml.safe_load(f) or {}
        
        # 仅保存非敏感端点配置；API key 由环境变量/密钥服务注入。
        local.pop("api_keys", None)
        local["base_urls"] = request.base_urls
        
        with open(local_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(local, f, allow_unicode=True)
        
        await broadcast_log("配置已更新，需要重启服务生效", "warning")
        
        return {
            "success": True,
            "message": "配置已保存，重启服务后生效"
        }

    except Exception as e:
        logger.exception("更新配置失败: {}", e)
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

@router.post("/api/restart", dependencies=[Depends(require_token)])
async def restart_service():
    """重启服务"""
    import os
    import sys
    
    await broadcast_log("收到重启请求，服务即将重启...", "warning")
    
    # 延迟重启，先返回响应
    async def _restart():
        await asyncio.sleep(1)
        # 在 Windows 上用 os._exit 强制退出，配合外部进程管理器重启
        # 在 Unix 上发送 SIGTERM
        if sys.platform == 'win32':
            os._exit(0)
        else:
            import signal
            os.kill(os.getpid(), signal.SIGTERM)
    
    asyncio.create_task(_restart())
    
    return {
        "success": True,
        "message": "服务正在重启"
    }

@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket, token: Optional[str] = None):
    """
    WebSocket 实时日志。

    浏览器的 WebSocket API 无法自定义请求头，因此 token 通过查询参数传递。
    校验失败时以 1008 (Policy Violation) 关闭连接。
    """
    if not verify_token(token):
        await websocket.close(code=1008, reason="无效的 API token")
        return

    await websocket.accept()
    state.websocket_clients.append(websocket)

    try:
        # 保持连接
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        pass
    finally:
        if websocket in state.websocket_clients:
            state.websocket_clients.remove(websocket)

# ===== 启动函数 =====

