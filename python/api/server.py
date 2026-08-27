"""
FastAPI 服务器
提供 RESTful API 和 WebSocket 实时日志
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import sys

from contextlib import asynccontextmanager

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yaml

# 添加python目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_client import LLMClient, Message
from designer import DefaultDesigner
from executor import DefaultExecutor
from tools import ToolRegistry
from database import Database
from api.auth import load_token, require_token, verify_token
from logging_config import get_logger

logger = get_logger(__name__)

# ===== 数据模型 =====

class DesignRequest(BaseModel):
    """生成设计书请求"""
    user_request: str

class ApprovalRequest(BaseModel):
    """审批设计书请求"""
    design_id: str
    approved: bool
    feedback: Optional[str] = None

class ExecutionRequest(BaseModel):
    """执行计划请求"""
    plan_id: str

class ConfigUpdateRequest(BaseModel):
    """更新配置请求"""
    api_keys: Dict[str, str]
    base_urls: Dict[str, str]

class ChatRequest(BaseModel):
    """AI助手对话请求"""
    message: str
    history: List[Dict[str, str]] = []

# ===== 全局状态 =====

class AppState:
    """应用状态"""
    def __init__(self):
        self.llm_client: Optional[LLMClient] = None
        self.designer: Optional[DefaultDesigner] = None
        self.executor: Optional[DefaultExecutor] = None
        self.tool_registry: Optional[ToolRegistry] = None
        self.dispatcher = None  # Dispatcher实例
        self.database: Optional[Database] = None
        self.config: Dict[str, Any] = {}
        self.websocket_clients: List[WebSocket] = []
        self.current_execution: Optional[str] = None  # 当前执行的 plan_id
        # 保护 current_execution 的 check-then-set，避免并发请求重复启动执行
        self.execution_lock: asyncio.Lock = asyncio.Lock()
        # 重试策略（阶段3.1）：从 config 顶层 retry 字段读取，lifespan 中赋值
        self.retry_max_attempts: int = 3
        self.retry_backoff_factor: float = 2

state = AppState()

# ===== 启动和关闭（lifespan） =====

@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务启动/关闭时的生命周期管理，替代已废弃的 @app.on_event"""
    logger.info("正在启动 EAS 2.0 API 服务...")

    try:
        # 加载配置
        config_path = Path("config/models.yaml")
        # 如果不存在，尝试父目录
        if not config_path.exists():
            config_path = Path("../config/models.yaml")
        if not config_path.exists():
            raise FileNotFoundError("配置文件 config/models.yaml 不存在")
        
        with open(config_path, "r", encoding="utf-8") as f:
            state.config = yaml.safe_load(f)
        
        # 初始化数据库
        db_path = Path("data/eas.db")
        if not db_path.parent.exists():
            db_path = Path("../data/eas.db")
        state.database = Database(str(db_path))
        
        # 初始化 LLM 客户端
        state.llm_client = LLMClient()
        
        # 注册所有 API providers
        from llm_client import OpenAICompatibleProvider
        api_keys = state.config.get("api_keys", {})
        base_urls = state.config.get("base_urls", {})

        logger.debug("===== 注册 LLM Providers =====")
        logger.debug("API Keys: {}", list(api_keys.keys()))
        logger.debug("Base URLs: {}", list(base_urls.keys()))

        for provider_name, api_key in api_keys.items():
            if not api_key:
                logger.warning("跳过 {}: 没有 API key", provider_name)
                continue
            if provider_name not in base_urls:
                logger.warning("跳过 {}: 没有 base URL", provider_name)
                continue
            try:
                provider = OpenAICompatibleProvider(
                    name=provider_name,
                    base_url=base_urls[provider_name],
                    api_key=api_key,
                )
                state.llm_client.register_provider(provider)
                logger.info("已注册 provider: {}", provider_name)
            except Exception as e:
                logger.warning("注册 provider 失败 {}: {}", provider_name, e)

        logger.info("已注册的 providers: {}", list(state.llm_client.providers.keys()))
        logger.debug("==================")

        # 初始化 Tool Registry（带AI agent）
        from tools import init_registry_with_ai
        tool_config = state.config.get("models", {}).get("designer", {})
        state.tool_registry = init_registry_with_ai(state.llm_client, tool_config)
        
        # 初始化 Dispatcher
        from dispatcher import Dispatcher
        state.dispatcher = Dispatcher(state.tool_registry)
        
        # 初始化 Designer（传入dispatcher）
        designer_config = state.config.get("models", {}).get("designer", {})
        state.designer = DefaultDesigner(state.llm_client, designer_config, state.dispatcher)
        
        # 初始化 Executor
        executor_config = state.config.get("models", {}).get("executor", {})
        state.executor = DefaultExecutor(
            llm_client=state.llm_client,
            model_config=executor_config,
            tool_registry=state.tool_registry,
            database=state.database
        )

        # 重试策略：读取顶层 retry 配置（不在 models: 下面）
        retry_cfg = state.config.get("retry", {})
        state.retry_max_attempts = retry_cfg.get("max_attempts", 3)
        state.retry_backoff_factor = retry_cfg.get("backoff_factor", 2)

        logger.info("EAS 2.0 API 服务启动成功")

        # 孤儿任务恢复：单进程部署下，进程重启意味着之前标记为 Running 的执行必然已经中断。
        # 若不处理，这些记录会永久卡在 Running，未来的暂停/恢复逻辑也无法感知。
        recovered = state.database.recover_orphaned_executions()
        if recovered:
            logger.warning("检测到 {} 个重启前未完成的执行，已标记为 Failed", recovered)

        # 初始化认证 token（不存在时自动生成）
        token = load_token()
        logger.debug("===== API 认证 =====")
        logger.info("所有 /api/* 端点需要 token：Authorization: Bearer <token>")
        logger.debug("当前 token: {}", token)
        logger.info("前端请在 frontend/.env.local 设置 VITE_API_TOKEN=<token>")
        logger.debug("==================")
        
    except Exception as e:
        logger.exception("启动失败: {}", e)
        raise

    yield

    # 关闭时清理（当前无需要释放的资源，占位以便未来扩展）
    logger.info("EAS 2.0 API 服务已停止")


# ===== FastAPI App =====

app = FastAPI(title="EAS 2.0 API", version="0.1.0", lifespan=lifespan)

# CORS：仅放行本地前端来源。
# 不使用 allow_origins=["*"]，它与 allow_credentials=True 组合违反 CORS 规范，
# 且对非浏览器客户端毫无约束力（CORS 不是访问控制，鉴权由 require_token 负责）。
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

_extra_origins = os.environ.get("EAS_ALLOWED_ORIGINS", "").strip()
if _extra_origins:
    ALLOWED_ORIGINS.extend(
        origin.strip() for origin in _extra_origins.split(",") if origin.strip()
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Token"],
)

# ===== WebSocket 日志广播 =====

async def broadcast_log(message: str, level: str = "info"):
    """向所有 WebSocket 客户端广播日志"""
    log_data = {
        "type": "log",
        "level": level,
        "message": message
    }
    
    disconnected = []
    for client in state.websocket_clients:
        try:
            await client.send_json(log_data)
        except:
            disconnected.append(client)
    
    # 移除断开的客户端
    for client in disconnected:
        state.websocket_clients.remove(client)

# ===== 格式转换辅助函数 =====

def _format_breakdown(breakdown: Dict[str, Any]) -> str:
    """将 breakdown 字典格式化为可读文本"""
    if not breakdown:
        return ""
    
    # 如果是错误信息
    if "error" in breakdown:
        error_msg = breakdown['error']
        error_type = breakdown.get('error_type', '')
        if error_type:
            return f"生成失败 ({error_type}): {error_msg}"
        return f"生成失败: {error_msg}"
    
    # 正常情况下，格式化为多行文本
    lines = []
    for key, value in breakdown.items():
        if isinstance(value, (list, dict)):
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)

def format_design_for_frontend(design) -> dict:
    """Transform Python DesignDocument to frontend-compatible format"""
    part1 = design.part1_design if hasattr(design, 'part1_design') else design.get('part1_design', {})
    part2 = design.part2_tool_assessment if hasattr(design, 'part2_tool_assessment') else design.get('part2_tool_assessment', {})
    
    # Handle both Pydantic model and dict
    if hasattr(part1, 'model_dump'):
        part1 = part1.model_dump()
    if hasattr(part2, 'model_dump'):
        part2 = part2.model_dump()
    
    arch = part1.get('architecture', {})
    diff = part1.get('difficulty', {})
    
    return {
        "design_id": str(design.design_id) if hasattr(design, 'design_id') else str(design.get('design_id', '')),
        "user_request": design.user_request if hasattr(design, 'user_request') else design.get('user_request', ''),
        "created_at": design.timestamp.isoformat() if hasattr(design, 'timestamp') else design.get('timestamp', ''),
        "part1": {
            "architecture": {
                "components": arch.get('components', []),
                "data_flow": arch.get('interactions', []),
                "tech_stack": [],
            },
            "difficulty": {
                "level": diff.get('overall', 'unknown'),
                "reasoning": _format_breakdown(diff.get('breakdown', {})),
            },
        },
        "part2": {
            "available_tools": [
                {"tool_id": t.get('tool_id', ''), "purpose": t.get('limitations', '') or '', "quality": t.get('quality', '')}
                for t in part2.get('required_tools', [])
                if t.get('availability', '') == 'available'
            ],
            "missing_tools": [
                {"tool_id": t.get('tool_id', ''), "purpose": t.get('impact', '') or ''}
                for t in part2.get('required_tools', [])
                if t.get('availability', '') != 'available'
            ],
            "recommendations": part2.get('risks', []),
        },
    }


def format_plan_for_frontend(plan) -> dict:
    """Transform Python ExecutionPlan to frontend-compatible format"""
    if hasattr(plan, 'model_dump'):
        plan_data = plan.model_dump(mode='json')
    else:
        plan_data = plan
    
    segments = []
    all_steps = []  # Flattened list of all steps
    
    for seg in plan_data.get('segments', []):
        steps = []
        for step in seg.get('steps', []):
            step_obj = {
                "step_id": str(step.get('step_id', '')),
                "order": step.get('order', 0),
                "description": step.get('description', ''),
                "tool": step.get('tool', ''),
                "inputs": step.get('inputs', {}),
                "expected_outputs": [step.get('check_criteria', '')] if step.get('check_criteria') else [],
                "status": step.get('status', 'Pending'),
                # outputs 包含失败原因（{"error": ...}），不能丢，
                # 否则前端只能看到 "Failed" 而无法得知原因。
                "outputs": step.get('outputs'),
                "executed_at": str(step.get('executed_at')) if step.get('executed_at') else None,
            }
            steps.append(step_obj)
            all_steps.append(step_obj)  # Add to flattened list
            
        segments.append({
            "segment_id": str(seg.get('segment_id', '')),
            "description": seg.get('description', ''),
            "steps": steps,
        })
    
    return {
        "plan_id": str(plan_data.get('plan_id', '')),
        "design_id": str(plan_data.get('design_id', '')),
        "segments": segments,
        "steps": all_steps,  # Flattened list for easy access
        "created_at": str(plan_data.get('created_at', '')),
    }

# ===== API 路由 =====

@app.get("/")
async def root():
    """健康检查"""
    return {
        "status": "ok",
        "version": "0.1.0",
        "message": "EAS 2.0 API is running"
    }

@app.get("/api/status", dependencies=[Depends(require_token)])
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

@app.post("/api/design", dependencies=[Depends(require_token)])
async def create_design(request: DesignRequest):
    """生成设计书"""
    try:
        await broadcast_log(f"收到设计书生成请求: {request.user_request[:50]}...")
        
        # 调用 Designer 生成设计书
        design = await state.designer.generate_design(request.user_request)
        
        # 保存到数据库
        state.database.save_design(design)
        
        await broadcast_log(f"设计书生成完成: {design.design_id}", "success")
        
        return {
            "success": True,
            "design": format_design_for_frontend(design)
        }
        
    except Exception as e:
        await broadcast_log(f"设计书生成失败: {str(e)}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/designs", dependencies=[Depends(require_token)])
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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/design/{design_id}", dependencies=[Depends(require_token)])
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
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/design/{design_id}/approve", dependencies=[Depends(require_token)])
async def approve_design(design_id: str, request: ApprovalRequest):
    """审批设计书"""
    try:
        design = state.database.get_design(design_id)
        if not design:
            raise HTTPException(status_code=404, detail="设计书不存在")
        
        if request.approved:
            await broadcast_log(f"设计书 {design_id} 已批准，开始生成执行计划...")
            
            # 生成执行计划
            plan = await state.designer.generate_plan(design)
            
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
        await broadcast_log(f"审批失败: {str(e)}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/plans", dependencies=[Depends(require_token)])
async def get_plans(limit: int = 10):
    """获取执行计划列表"""
    try:
        plans_raw = state.database.list_plans(limit)
        plans = []
        for p in plans_raw:
            plans.append({
                "plan_id": p.get("plan_id", ""),
                "design_id": p.get("design_id", ""),
                "segments": [],
                "created_at": p.get("created_at", ""),
            })
        return {
            "success": True,
            "plans": plans
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/plan/{plan_id}", dependencies=[Depends(require_token)])
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
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/plan/{plan_id}/execute", dependencies=[Depends(require_token)])
async def execute_plan(plan_id: str):
    """执行计划"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")

        # check-then-set 必须在同一把锁内完成。
        # 否则两个并发请求可以同时通过检查，产出两个后台任务争抢同一份 DB 和文件系统。
        async with state.execution_lock:
            if state.current_execution:
                raise HTTPException(
                    status_code=409,
                    detail=f"已有任务在执行中: {state.current_execution}",
                )
            state.current_execution = plan_id

        # 在后台执行
        asyncio.create_task(execute_plan_background(plan))

        return {
            "success": True,
            "message": "执行已开始"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/plan/{plan_id}/pause", dependencies=[Depends(require_token)])
async def pause_plan(plan_id: str):
    """暂停正在执行的计划。执行循环会在下一个步骤开始前检测到 Paused 状态并退出。"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")

        current_status = state.database.get_plan_status(plan_id)
        if current_status != "Running":
            raise HTTPException(
                status_code=409,
                detail=f"只能暂停 Running 状态的计划，当前状态: {current_status}",
            )

        if not state.database.update_plan(plan_id, "Paused"):
            raise HTTPException(status_code=500, detail="暂停失败")

        await broadcast_log(f"计划 {plan_id} 已请求暂停", "info")
        return {"success": True, "message": "暂停请求已发出，将在当前步骤完成后生效"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/plan/{plan_id}/resume", dependencies=[Depends(require_token)])
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

        async with state.execution_lock:
            if state.current_execution:
                raise HTTPException(
                    status_code=409,
                    detail=f"已有任务在执行中: {state.current_execution}",
                )
            state.current_execution = plan_id

        asyncio.create_task(execute_plan_background(plan, resume=True))

        return {"success": True, "message": "已从断点恢复执行"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/plan/{plan_id}/skip-step/{step_id}", dependencies=[Depends(require_token)])
async def skip_step(plan_id: str, step_id: str):
    """把指定步骤标记为 Skipped。正在进行中的执行循环会在避开该步骤时直接跳过不执行。"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")

        steps = state.database.get_steps(plan_id)
        if not any(s["step_id"] == step_id for s in steps):
            raise HTTPException(status_code=404, detail="步骤不存在")

        if not state.database.update_step(step_id=step_id, status="Skipped", executed_at=datetime.now()):
            raise HTTPException(status_code=500, detail="跳过失败")

        await broadcast_log(f"步骤 {step_id} 已被手动跳过", "info")
        return {"success": True, "message": "步骤已标记为跳过"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _persist_step_status(
    step_id: str,
    status: str,
    outputs: Optional[Dict[str, Any]] = None,
    retry_count: Optional[int] = None,
) -> bool:
    """
    写入步骤状态，并返回是否命中了数据行。

    update_step() 在 step_id 不存在时返回 False（例如双写不一致）。
    以前调用方丢弃了返回值，导致「执行日志显示成功、DB 状态没变」的静默失败。
    """
    updated = state.database.update_step(
        step_id=step_id,
        status=status,
        outputs=outputs,
        executed_at=datetime.now(),
        retry_count=retry_count,
    )
    return bool(updated)


async def execute_plan_background(plan, resume: bool = False):
    """后台执行计划

    resume=True 时：从断点继续（跳过已经是 Success/Skipped 的步骤），用于 /resume 端点。
    """
    def debug_log(msg):
        # 阶段4.1：不再手写 append 到 execution_debug.log（该文件会无限增长）。
        # 统一走 logger，由 logging_config.py 里配置的按大小轮转 sink 处理。
        logger.debug(msg)

    plan_failed = False
    plan_paused = False

    try:
        debug_log(f"execute_plan_background STARTED for plan {plan.plan_id} (resume={resume})")
        state.database.update_plan(str(plan.plan_id), "Running")
        await broadcast_log(f"{'恢复' if resume else '开始'}执行计划: {plan.plan_id}", "info")

        for segment in plan.segments:
            debug_log(f"Processing segment with {len(segment.steps)} steps")
            for step in segment.steps:
                step_id = str(step.step_id)

                # 恢复模式下跳过已完成/已跳过的步骤，从第一个未完成的步骤重新开始
                step_status_value = step.status.value if hasattr(step.status, "value") else str(step.status)
                if resume and step_status_value in ("Success", "Skipped"):
                    debug_log(f"Step {step_id} already {step_status_value}, skip on resume")
                    continue

                # 手动跳过：步骤已被 /skip-step 标记为 Skipped，直接跳过不执行
                current_steps = state.database.get_steps(str(plan.plan_id))
                skipped_ids = {
                    s["step_id"] for s in current_steps if s.get("status") == "Skipped"
                }
                if step_id in skipped_ids:
                    debug_log(f"Step {step_id} marked Skipped, skipping execution")
                    await broadcast_log(f"[SKIP] 步骤 {step_id} 已被跳过", "info")
                    continue

                # 暂停检测：每个步骤开始前重新从 DB 读一次计划状态，发现被暂停则退出循环，
                # 保持 Paused 状态不变，等待 /resume 端点重新拉起。
                current_plan_status = state.database.get_plan_status(str(plan.plan_id))
                if current_plan_status == "Paused":
                    debug_log(f"Plan {plan.plan_id} is Paused, stopping execution loop")
                    await broadcast_log(f"计划 {plan.plan_id} 已暂停", "info")
                    plan_paused = True
                    break

                debug_log(f"Step {step_id}: {step.description[:30]}...")
                await broadcast_log(f"执行步骤 {step_id}: {step.description[:50]}...")

                if not _persist_step_status(step_id, "Running"):
                    # 状态写不进去说明 steps 表与计划数据不一致，继续执行只会产生更多误导
                    msg = f"步骤 {step_id} 状态写入失败（DB 中无此步骤），中止执行"
                    debug_log(f"ABORT: {msg}")
                    await broadcast_log(f"[ERROR] {msg}", "error")
                    plan_failed = True
                    break

                # 重试循环：读取 config 的 retry.max_attempts / backoff_factor。
                # 不可重试的失败（安全拦截 blocked、或结果校验不通过 check_status=="failed"）
                # 是逻辑/安全层面的确定性错误，重试不会改变结果，立即判失败退出循环；
                # 其余失败（网络超时、工具执行异常等）按指数退避重试。
                max_attempts = getattr(state, "retry_max_attempts", 3)
                backoff_factor = getattr(state, "retry_backoff_factor", 2)
                attempt = 0
                result = None
                while True:
                    result = await state.executor.execute_step(step)
                    debug_log(
                        f"Step {step_id} attempt {attempt} result: success={result.get('success')} "
                        f"check={result.get('check_status')} blocked={result.get('blocked')}"
                    )
                    if result.get("success") and result.get("check_status") != "failed":
                        break
                    non_retryable = result.get("blocked") or result.get("check_status") == "failed"
                    if non_retryable:
                        debug_log(f"Step {step_id} 遇到不可重试错误，停止重试")
                        break
                    if attempt >= max_attempts - 1:
                        break
                    attempt += 1
                    step.retry_count = attempt
                    step.max_retries = max_attempts - 1
                    delay = backoff_factor ** attempt
                    await broadcast_log(
                        f"[RETRY] 步骤 {step_id} 失败，第 {attempt}/{max_attempts - 1} 次重试（{delay:.1f}秒后）",
                        "warning",
                    )
                    debug_log(f"Step {step_id} retrying, attempt {attempt}/{max_attempts - 1}, delay={delay}")
                    _persist_step_status(step_id, "Running", retry_count=attempt)
                    await asyncio.sleep(delay)

                outputs = result.get("output")
                check_status = result.get("check_status")

                if result.get("success"):
                    if check_status == "unverified":
                        await broadcast_log(
                            f"[WARN] 步骤 {step_id} 完成，但结果未校验", "warning"
                        )
                    else:
                        await broadcast_log(f"[OK] 步骤 {step_id} 完成", "success")

                    payload = dict(outputs) if isinstance(outputs, dict) else {"result": outputs}
                    payload["check_status"] = check_status

                    if not _persist_step_status(step_id, "Success", payload, retry_count=step.retry_count):
                        debug_log(f"WARNING: 步骤 {step_id} 成功状态写入失败")
                        await broadcast_log(
                            f"[WARN] 步骤 {step_id} 状态写入失败", "warning"
                        )
                    else:
                        debug_log(f"Step {step_id} marked as Success")
                else:
                    error = result.get("error", "未知错误")
                    await broadcast_log(
                        f"[ERROR] 步骤 {step_id} 失败（已重试 {step.retry_count} 次）: {error}", "error"
                    )

                    payload = {"error": error, "check_status": check_status}
                    if isinstance(outputs, dict):
                        payload["tool_output"] = outputs

                    if not _persist_step_status(step_id, "Failed", payload, retry_count=step.retry_count):
                        debug_log(f"WARNING: 步骤 {step_id} 失败状态写入失败")

                    debug_log(f"Step {step_id} marked as Failed: {error}")
                    plan_failed = True
                    break

            if plan_failed or plan_paused:
                break

        if plan_paused:
            # 保持 Paused 状态，不覆盖为 Completed/Failed，等待 /resume 重新拉起
            debug_log(f"Plan {plan.plan_id} execution PAUSED")
        else:
            final_status = "Failed" if plan_failed else "Completed"
            state.database.update_plan(str(plan.plan_id), final_status)

            if plan_failed:
                await broadcast_log(f"计划执行失败: {plan.plan_id}", "error")
                debug_log(f"Plan {plan.plan_id} execution FAILED")
            else:
                await broadcast_log(f"计划执行完成: {plan.plan_id}", "success")
                debug_log(f"Plan {plan.plan_id} execution COMPLETED")

    except Exception as e:
        debug_log(f"EXCEPTION in execute_plan_background: {e}")
        import traceback
        debug_log(f"Traceback: {traceback.format_exc()}")
        try:
            state.database.update_plan(str(plan.plan_id), "Failed")
        except Exception as inner:
            debug_log(f"update_plan failed during exception handling: {inner}")
        await broadcast_log(f"执行失败: {str(e)}", "error")
    finally:
        async with state.execution_lock:
            state.current_execution = None
        debug_log(f"execute_plan_background FINISHED for plan {plan.plan_id}")


@app.get("/api/audit-logs", dependencies=[Depends(require_token)])
async def get_audit_logs(limit: int = 100, plan_id: Optional[str] = None):
    """查询审计日志（工具调用记录，包括被安全拦截的调用）"""
    try:
        logs = state.database.list_audit_logs(limit=limit, plan_id=plan_id)
        return {
            "success": True,
            "logs": logs
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tools", dependencies=[Depends(require_token)])
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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config", dependencies=[Depends(require_token)])
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
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config", dependencies=[Depends(require_token)])
async def update_config(request: ConfigUpdateRequest):
    """更新配置"""
    try:
        # 更新配置文件
        config_path = Path("config/models.yaml")
        
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        config["api_keys"] = request.api_keys
        config["base_urls"] = request.base_urls
        
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True)
        
        await broadcast_log("配置已更新，需要重启服务生效", "warning")
        
        return {
            "success": True,
            "message": "配置已保存，重启服务后生效"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat", dependencies=[Depends(require_token)])
async def chat(request: ChatRequest):
    """AI 助手对话"""
    try:
        # 构建消息列表
        messages = []
        for msg in request.history:
            messages.append(Message(role=msg['role'], content=msg['content']))
        messages.append(Message(role='user', content=request.message))
        
        # 获取模型配置 - 使用 designer 配置
        model_config = state.config.get('agents', {}).get('designer', {}).get('primary', {})
        provider = model_config.get('provider', 'newapi')
        model = model_config.get('model', 'claude-haiku-4-5-20251001')
        
        # 调用 LLM
        response = await state.llm_client.chat(
            provider_name=provider,
            messages=messages,
            model=model,
            temperature=0.7,
            max_tokens=2000,
        )
        
        return {
            "success": True,
            "response": response.content
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/restart", dependencies=[Depends(require_token)])
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

@app.websocket("/ws/logs")
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

def start_server(host: str = "127.0.0.1", port: int = 8000):
    """启动服务器"""
    import uvicorn
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    start_server()
