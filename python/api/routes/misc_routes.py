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
from api.auth import verify_token

from api.shared import state, broadcast_log, _load_models_config, _deep_merge_dict
from database import DatabaseError
from designer import GenerationError
from version import __version__ as EAS_VERSION

from logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


from api.model_probe import ProbeRequest, probe_configuration
from api import model_catalog
from api.configuration import (
    ConfigUpdateRequest, ConfigProblem, KeyUpdateRequest, read_configuration,
    save_configuration, save_api_key,
)


def _config_dir() -> Path:
    """定位 config/ 目录：沿模块父目录上溯查找被跟踪的 models.yaml。

    层级无关（routes 文件比原 server.py 深一层也不会指错），找不到时回落
    到当前工作目录的 config/（与 api/shared.py 的 _load_models_config 一致）。
    """
    here = Path(__file__).resolve()
    for base in here.parents:
        candidate = base / "config"
        if (candidate / "models.yaml").exists():
            return candidate.resolve()
    return (Path.cwd() / "config").resolve()


@router.get("/")
async def root():
    """健康检查"""
    return {
        "status": "ok",
        "version": EAS_VERSION,
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


@router.post("/api/config/probe", dependencies=[Depends(require_token)])
async def probe_models(request: "ProbeRequest"):
    """Explicit non-persistent connectivity / minimal inference check."""
    try:
        result = await probe_configuration(_config_dir(), state.config, request)
    except ConfigProblem as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    catalog = None
    if request.mode == "connectivity" and result.get("ok") and result.get("models"):
        # Remember the fetched IDs so the routing table can offer them after a
        # reload. Cache failures must never turn a successful probe into an error.
        try:
            directory = _config_dir()
            urls = model_catalog.effective_base_urls(
                read_configuration(directory, state.config)["base_urls"]
            )
            catalog = model_catalog.remember_catalog(
                directory, request.provider, urls.get(request.provider, ""),
                result["models"], known_providers=set(urls),
            )
        except (ConfigProblem, OSError):
            logger.warning("模型目录缓存写入失败（不影响本次检测结果）")
    return {"success": True, "result": result, "catalog": catalog}


@router.get("/api/config/catalog", dependencies=[Depends(require_token)])
async def get_model_catalog():
    """Cached model IDs from earlier explicit fetches; never contacts upstream."""
    try:
        directory = _config_dir()
        urls = read_configuration(directory, state.config)["base_urls"]
    except ConfigProblem as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    return {"success": True, "catalog": model_catalog.load_catalog(directory, urls)}


@router.delete("/api/config/catalog/{provider}", dependencies=[Depends(require_token)])
async def delete_model_catalog(provider: str):
    """Forget one provider's cached list; no upstream request, no config change."""
    try:
        directory = _config_dir()
        urls = read_configuration(directory, state.config)["base_urls"]
    except ConfigProblem as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    if provider not in urls:
        raise HTTPException(status_code=404, detail="未知的提供商")
    try:
        removed = model_catalog.forget_catalog(directory, provider)
    except OSError:
        raise HTTPException(status_code=500, detail="无法更新模型目录缓存文件") from None
    return {"success": True, "removed": removed}


@router.get("/api/config", dependencies=[Depends(require_token)])
async def get_config():
    """Read persisted model routing, with runtime drift and no secret values."""
    try:
        return {"success": True, "config": read_configuration(_config_dir(), state.config)}
    except ConfigProblem as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


@router.post("/api/config", dependencies=[Depends(require_token)])
async def update_config(request: ConfigUpdateRequest):
    """Atomically persist non-secret settings; leave running tasks/config untouched."""
    try:
        config = save_configuration(_config_dir(), state.config, request)
        return {"success": True, "config": config,
                "message": "配置已保存；未自动重启服务"}
    except ConfigProblem as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


@router.post("/api/config/key", dependencies=[Depends(require_token)])
async def set_config_key(request: KeyUpdateRequest):
    """Persist or clear one provider key in the encrypted account-local store."""
    try:
        return {"success": True, "key": save_api_key(_config_dir(), request)}
    except ConfigProblem as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


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

