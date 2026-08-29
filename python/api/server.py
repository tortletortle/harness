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
from uuid import UUID
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
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import yaml

# 添加python目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_client import LLMClient, Message
from designer import DefaultDesigner, GenerationError
from executor import DefaultExecutor
from tools import ToolRegistry
from database import Database, DatabaseError
from models import DesignDocument, ExecutionPlan, PlanSegment, Step, StepStatus, PlanStatus
from api.auth import load_token, require_token, verify_token
print("[SERVER MODULE LOADED]", __file__)
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

# ===== 全局异常处理 =====
# DatabaseError / GenerationError 统一转换为带 code 的 JSON 响应：
# - 数据库故障 → 500 database_error
# - LLM 生成失败（上游依赖故障）→ 502 generation_failed
# 端点内的 HTTPException(404/409/500) 分支不受影响，仍然原样抛出。

@app.exception_handler(DatabaseError)
async def _database_error_handler(request, exc):
    return JSONResponse(status_code=500, content={"detail": str(exc), "code": "database_error"})


@app.exception_handler(GenerationError)
async def _generation_error_handler(request, exc):
    return JSONResponse(status_code=502, content={"detail": str(exc), "code": "generation_failed"})

# ===== WebSocket 日志广播 =====

async def _send_to_clients(payload: dict) -> None:
    """把 payload 发送给所有 WebSocket 客户端，并移除发送失败的连接。

    单个客户端发送失败（连接已断开等）只摘除该连接，不中断对其余客户端的
    广播；不能用裸 except——那会连取消信号（CancelledError）一起吞掉。
    """
    disconnected = []
    for client in state.websocket_clients:
        try:
            await client.send_json(payload)
        except Exception:
            # 连接已断开等发送失败；不能用裸 except——那会连取消信号一起吞掉
            disconnected.append(client)

    # 移除断开的客户端
    for client in disconnected:
        state.websocket_clients.remove(client)


async def broadcast_log(message: str, level: str = "info"):
    """向所有 WebSocket 客户端广播日志"""
    log_data = {
        "type": "log",
        "level": level,
        "message": message
    }
    await _send_to_clients(log_data)


async def broadcast_plan_update(plan_id: str) -> None:
    """向所有 WebSocket 客户端广播计划状态变更（type=plan_update）。

    载荷取自数据库的最新状态摘要（计划状态 + 未废弃步骤状态），前端据此
    免轮询刷新计划视图。设计约束：
    - 计划已消失（摘要为 None）时静默返回——客户端的 30s 轮询兜底覆盖该边界；
    - 摘要读取失败只记日志不向上抛：广播是观察性副作用，绝不能把执行循环
      或端点的成功路径打断（与 _send_to_clients 的逐客户端容错一致）。
    """
    try:
        summary = state.database.get_plan_status_summary(str(plan_id))
    except Exception as e:
        logger.warning("plan_update 广播跳过（状态摘要读取失败）: {}", e)
        return
    if summary is None:
        return
    payload = {
        "type": "plan_update",
        "plan_id": str(plan_id),
        "status": summary["status"],
        "steps": summary["steps"],
    }
    await _send_to_clients(payload)

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
        # 计划级权威状态，前端轮询据此驱动暂停/恢复/进度，而非解析日志文本
        "status": plan_data.get('status') or 'Pending',
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
        logger.exception("生成设计书端点失败: {}", e)
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
        logger.exception("查询设计书列表失败: {}", e)
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
        logger.exception("查询设计书失败: {}", e)
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
        logger.exception("审批设计书端点失败: {}", e)
        await broadcast_log(f"审批失败: {str(e)}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/plans", dependencies=[Depends(require_token)])
async def get_plans(limit: int = 10):
    """获取执行计划列表"""
    try:
        plans_raw = state.database.list_plans(limit=limit)
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
        logger.exception("查询执行计划列表失败: {}", e)
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
        logger.exception("查询执行计划失败: {}", e)
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
        task = asyncio.create_task(execute_plan_background(plan))
        def _bg_done(t):
            try:
                exc = t.exception()
            except Exception as e:
                exc = e
            try:
                with open('D:/spot/harness/.tmp/bg_done.log', 'a', encoding='utf-8') as f:
                    f.write(f'plan={plan.plan_id} exc={exc}\n')
                    f.flush()
            except Exception:
                pass
        task.add_done_callback(_bg_done)

        return {
            "success": True,
            "message": "执行已开始"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("启动计划执行失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/plan/{plan_id}/pause", dependencies=[Depends(require_token)])
async def pause_plan(plan_id: str):
    """暂停正在执行的计划。执行循环会在下一个步骤开始前检测到 Paused 状态并退出。

    暂停时一次性写入恢复检查点（resume_from_step_id / paused_at_step_id）：
    状态与断点在同一条 UPDATE 里落库，保证原子性——不会出现「状态已 Paused
    但断点缺失」的中间态，恢复端点因此总能拿到一致的断点。
    """
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")

        current_status = state.database.get_plan_status(plan_id)
        if current_status not in ("Running", "Pending"):
            raise HTTPException(
                status_code=409,
                detail=f"只能暂停 Running/Pending 状态的计划，当前状态: {current_status}",
            )

        branch_version = getattr(plan, 'current_branch', 1) or 1
        paused_at_step_id, resume_from_step_id = _compute_pause_checkpoint(plan_id, branch_version)

        if paused_at_step_id is None and resume_from_step_id is None:
            # 当前分支上没有 Running/Pending/Retry 步骤（例如全部已完成）：
            # update_plan_state_machine 的 COALESCE 语义下不传字段即保留旧断点，
            # 避免把可能有用的历史断点误清成 NULL。
            _require_write(
                state.database.update_plan_state_machine(plan_id, "Paused"),
                f"计划 {plan_id} 状态 → Paused",
            )
        else:
            # 单条 UPDATE 同时写状态与断点（原子）。个别字段为 None 时
            # COALESCE 保留旧值（例如存在 Running 步骤但没有 Pending/Retry 步骤）。
            _require_write(
                state.database.update_plan_state_machine(
                    plan_id,
                    "Paused",
                    resume_from_step_id=resume_from_step_id,
                    paused_at_step_id=paused_at_step_id,
                ),
                f"计划 {plan_id} 状态 → Paused（含恢复断点）",
            )

        await broadcast_plan_update(plan_id)
        await broadcast_log(f"计划 {plan_id} 已请求暂停", "info")
        return {"success": True, "message": "暂停请求已发出，将在当前步骤完成后生效"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("暂停计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


def _compute_pause_checkpoint(plan_id: str, branch_version: int):
    """
    计算暂停检查点，返回 (paused_at_step_id, resume_from_step_id)。

    - paused_at_step_id: 当前分支上第一个 status == Running 的活动步骤（暂停发生地）
    - resume_from_step_id: 当前分支上第一个 status ∈ (Pending, Retry) 的活动步骤
      （按 step_order 排序）；没有运行中步骤时它就是第一个未完成步骤。

    找不到返回 None，由调用方决定保留旧断点（COALESCE）。数据库故障沿
    DatabaseError 向上抛给端点的统一异常处理。
    """
    rows = state.database.get_active_steps_snapshot(plan_id, branch_version)
    paused_at = next(
        (str(r["step_id"]) for r in rows if r["status"] == StepStatus.RUNNING.value),
        None,
    )
    resume_from = next(
        (str(r["step_id"]) for r in rows
         if r["status"] in (StepStatus.PENDING.value, StepStatus.RETRY.value)),
        None,
    )
    return paused_at, resume_from


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
        logger.exception("恢复计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/plan/{plan_id}/skip-step/{step_id}", dependencies=[Depends(require_token)])
async def skip_step(plan_id: str, step_id: str):
    """把指定步骤标记为 Skipped。正在进行中的执行循环会在避开该步骤时直接跳过不执行。

    守卫：
    - 计划已进入终态（Completed/Failed/RolledBack）时拒绝跳过；
    - 按步骤的「生效状态」判断——回滚后活动行是克隆行（parent_step_id 指向原 id），
      生效状态取活动克隆行，无克隆才回落到原行；生效状态非 Pending/Retry 拒绝跳过。
    """
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")

        current_status = state.database.get_plan_status(plan_id)
        if current_status in ("Completed", "Failed", "RolledBack"):
            raise HTTPException(
                status_code=409,
                detail=f"计划已结束（当前状态: {current_status}），无法跳过步骤",
            )

        steps = state.database.get_steps(plan_id)
        if not any(s["step_id"] == step_id for s in steps):
            raise HTTPException(status_code=404, detail="步骤不存在")

        branch_version = getattr(plan, 'current_branch', 1) or 1
        effective_status = state.database.get_effective_step_status(plan_id, step_id, branch_version)
        if effective_status is None:
            # 存在性检查已通过，这里兜底（理论上不可达）
            raise HTTPException(status_code=404, detail="步骤不存在")
        if effective_status not in ("Pending", "Retry"):
            raise HTTPException(status_code=409, detail="步骤已在执行或已完成，无法跳过")

        # 跳过需沿克隆血缘传播：活动分支上的克隆行若保持 Pending，执行时会照常运行
        if not state.database.mark_step_tree_skipped(plan_id, step_id, executed_at=datetime.now()):
            raise HTTPException(status_code=500, detail="跳过失败")

        await broadcast_plan_update(plan_id)
        await broadcast_log(f"步骤 {step_id} 已被手动跳过", "info")
        return {"success": True, "message": "步骤已标记为跳过"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("跳过步骤失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


def _require_write(ok: bool, what: str) -> None:
    """状态写入必须成功；False（未命中/非法状态）与异常同样致命，向上抛给外层统一清理。"""
    if not ok:
        raise RuntimeError(f"状态写入失败: {what}")


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


async def _compress_step_context(plan_id: str, step_id: str, result: Dict[str, Any]) -> Optional[str]:
    """
    调用 LLM 把 step 结果压缩成短摘要，供后续 step 作为上下文。
    失败不影响主流程，仅返回 None。
    """
    try:
        raw = result.get("output") or {}
        payload = {
            "step_id": str(step_id),
            "success": result.get("success"),
            "check_status": result.get("check_status"),
            "error": result.get("error"),
            "tool_output": raw,
        }
        prompt = (
            "请把以下执行结果压缩成 1-3 句中文摘要，保留关键信息："
            "创建了什么文件、输出路径、关键参数、错误信息。\n\n"
            f"原始输出：\n{json.dumps(payload, ensure_ascii=False)[:4000]}"
        )
        summary = await asyncio.wait_for(
            state.llm_client.chat(
                provider_name="newapi",
                messages=[Message(role="user", content=prompt)],
                model="claude-haiku-4-5-20251001",
                temperature=0.2,
                max_tokens=300,
            ),
            timeout=10,
        )
        summary_text = summary.content.strip()
        state.database.update_step_state_machine(
            step_id=str(step_id),
            status="Success",
            compressed_context=summary_text,
        )
        state.database.add_step_event(
            step_id=str(step_id),
            plan_id=str(plan_id),
            event_type="context_compressed",
            event_data={"summary": summary_text},
        )
        return summary_text
    except Exception as e:
        logger.warning("上下文压缩失败: {}", e)
        return None


async def _execute_step_with_state_machine(plan, step: Dict[str, Any]) -> Dict[str, Any]:
    """
    单步状态机执行：执行工具、写 event。
    注意：不直接写入 Failed，失败状态由调用方在最终重试耗尽后写入，
    避免第一次失败就把步骤钉死，导致重试逻辑失效。
    """
    step_id = str(step.get("step_id"))
    plan_id = str(plan.plan_id)

    step_model = Step(
        step_id=UUID(step["step_id"]),
        order=step.get("step_order", step.get("order", 0)),
        description=step.get("description", ""),
        tool=step.get("tool", ""),
        inputs=json.loads(step["inputs"]) if isinstance(step.get("inputs"), str) else step.get("inputs", {}),
        check_criteria=step.get("check_criteria", ""),
        outputs=json.loads(step["outputs"]) if isinstance(step.get("outputs"), str) and step.get("outputs") else step.get("outputs"),
        status=StepStatus(step.get("status", StepStatus.PENDING.value)),
        executed_at=datetime.fromisoformat(step["executed_at"]) if step.get("executed_at") else None,
        retry_count=int(step.get("retry_count", 0)),
        parent_step_id=UUID(step["parent_step_id"]) if step.get("parent_step_id") else None,
        branch_version=int(step.get("branch_version", 1)),
        state_snapshot=json.loads(step["state_snapshot"]) if isinstance(step.get("state_snapshot"), str) and step.get("state_snapshot") else step.get("state_snapshot"),
        compressed_context=step.get("compressed_context"),
        duration_ms=int(step["duration_ms"]) if step.get("duration_ms") is not None else None,
        failure_reason=step.get("failure_reason"),
        is_obsolete=bool(int(step["is_obsolete"])) if step.get("is_obsolete") is not None else False,
    )

    if not state.database.add_step_event(step_id, plan_id, "running"):
        logger.warning("step_event running 写入失败: {}", step_id)

    started = datetime.utcnow()
    result = await state.executor.execute_step(step_model)
    duration_ms = int((datetime.utcnow() - started).total_seconds() * 1000)

    success = bool(result.get("success"))
    check_status = result.get("check_status")
    blocked = bool(result.get("blocked"))
    error = result.get("error")

    if success and check_status != "failed":
        status = StepStatus.SUCCESS.value
        outputs = dict(result.get("output") or {})
        outputs["check_status"] = check_status
        compressed = await _compress_step_context(plan_id, step_id, result)
        state.database.update_step_state_machine(
            step_id=str(step_id),
            status=status,
            outputs=outputs,
            duration_ms=duration_ms,
            compressed_context=compressed,
            state_snapshot=result,
        )
        if not state.database.add_step_event(step_id, plan_id, "success", {"duration_ms": duration_ms}):
            logger.warning("step_event success 写入失败: {}", step_id)
        result["duration_ms"] = duration_ms
        return result

    # 仅记录 failed 事件，不写入 Failed 状态，留给调用方在重试耗尽后统一写入
    failure_reason = error or check_status or "unknown"
    if not state.database.add_step_event(step_id, plan_id, "failed", {"reason": failure_reason, "blocked": blocked, "duration_ms": duration_ms}):
        logger.warning("step_event failed 写入失败: {}", step_id)
    result["duration_ms"] = duration_ms
    return result


async def execute_plan_background(plan, resume: bool = False):
    """后台执行计划

    resume=True 时：从断点继续，用于 /resume 或回滚后的 /execute。
    状态机模式：每次只取下一个可运行 step，执行后写 event 与压缩上下文。
    """
    def debug_log(msg):
        logger.debug(msg)

    print(f"[BG] STARTED plan={plan.plan_id} resume={resume}", flush=True)
    try:
        debug_log(f"execute_plan_background STARTED for plan {plan.plan_id} (resume={resume})")
        # 原子认领：把「读状态→判断→写 Running」收敛为单条条件 UPDATE，
        # 消除 /pause 与 /execute(或 /resume) 并发时的状态覆盖竞态。
        # - execute：从除 Paused 外的全部可执行状态认领——计划若在任务排队期间被暂停，
        #   认领必然失败，执行绝不会把 Paused 翻成 Running（暂停被无声吞掉的竞态）。
        #   Completed/RolledBack 在列以保持「重跑已完成计划」「回滚后重跑」可用。
        # - resume：只从 Paused 认领。
        if resume:
            claim_from = (PlanStatus.PAUSED.value,)
        else:
            claim_from = (
                PlanStatus.PENDING.value,
                PlanStatus.APPROVED.value,
                PlanStatus.FAILED.value,
                PlanStatus.COMPLETED.value,
                PlanStatus.ROLLED_BACK.value,
            )
        claimed = state.database.claim_plan_status(str(plan.plan_id), "Running", claim_from)
        if not claimed:
            # 认领失败 = 状态已被并发操作改变（被暂停 / 已被另一个任务认领 Running /
            # 状态在排队期间变化）。此时绝不标记 Failed：Paused 归暂停逻辑所有，
            # 已是 Running 说明另一个任务合法拥有执行权，把计划打成 Failed 会
            # 覆盖别人的合法状态。优雅退出即可，finally 会释放 current_execution。
            current_status = state.database.get_plan_status(str(plan.plan_id))
            debug_log(f"Plan {plan.plan_id} claim Running failed (resume={resume}, current={current_status}), exiting gracefully")
            if current_status == PlanStatus.PAUSED.value:
                await broadcast_log("计划已暂停，跳过执行", "info")
            else:
                await broadcast_log(
                    f"计划状态已变化（当前: {current_status}），跳过执行", "info"
                )
            return

        await broadcast_plan_update(str(plan.plan_id))
        if resume:
            # 断点已随认领成功被本次恢复消费（plan.resume_from_step_id 已带入执行循环），
            # 此刻清除库中检查点。顺序必须是先认领后清除：认领失败时断点仍在，
            # 下次恢复仍可使用。清除失败不使恢复失败——残留的过期断点是良性的：
            # 它只影响下次 resume 的起点，且显式 resume 路径取到的步骤若已
            # Success/Skipped 会被执行循环跳过；静默吞掉异常仅记日志。
            try:
                if not state.database.clear_plan_checkpoint(str(plan.plan_id)):
                    logger.warning("清除恢复断点未命中（计划可能已不存在），已忽略: {}", plan.plan_id)
            except Exception as clear_exc:
                logger.exception("清除恢复断点失败（忽略，不影响本次恢复）: {}", clear_exc)

        print(f"[BG] plan={plan.plan_id} status=Running", flush=True)
        await broadcast_log(f"{'恢复' if resume else '开始'}执行计划: {plan.plan_id}", "info")

        branch_version = getattr(plan, 'current_branch', 1) or 1
        resume_from_step_id = str(getattr(plan, 'resume_from_step_id', None)) if getattr(plan, 'resume_from_step_id', None) else None

        while True:
            current_status = state.database.get_plan_status(str(plan.plan_id))
            if current_status == "Paused":
                debug_log(f"Plan {plan.plan_id} is Paused, stopping execution loop")
                await broadcast_log(f"计划 {plan.plan_id} 已暂停", "info")
                _require_write(
                    state.database.update_plan_state_machine(str(plan.plan_id), "Paused"),
                    f"计划 {plan.plan_id} 状态 → Paused",
                )
                await broadcast_plan_update(str(plan.plan_id))
                break

            step = state.database.get_next_runnable_step(
                str(plan.plan_id),
                branch_version=branch_version,
                resume_from_step_id=resume_from_step_id,
            )
            if not step:
                _require_write(
                    state.database.update_plan_state_machine(str(plan.plan_id), "Completed"),
                    f"计划 {plan.plan_id} 状态 → Completed",
                )
                await broadcast_plan_update(str(plan.plan_id))
                await broadcast_log(f"计划执行完成: {plan.plan_id}", "success")
                debug_log(f"Plan {plan.plan_id} execution COMPLETED")
                break

            step_id = str(step.get("step_id"))
            resume_from_step_id = None
            step_status_value = step.get("status")

            if resume and step_status_value in ("Success", "Skipped"):
                debug_log(f"Step {step_id} already {step_status_value}, skip on resume")
                continue

            skipped_ids = {s["step_id"] for s in state.database.get_steps(str(plan.plan_id)) if s.get("status") == "Skipped"}
            if step_id in skipped_ids:
                debug_log(f"Step {step_id} marked Skipped, skipping execution")
                await broadcast_log(f"[SKIP] 步骤 {step_id} 已被跳过", "info")
                if step_status_value != "Skipped":
                    state.database.update_step_state_machine(
                        step_id=step_id,
                        status="Skipped",
                        executed_at=datetime.now(),
                        outputs=step.get("outputs") or {},
                    )
                    await broadcast_plan_update(str(plan.plan_id))
                continue

            debug_log(f"Step {step_id}: {step.get('description', '')[:30]}...")
            await broadcast_log(f"执行步骤 {step_id}: {step.get('description', '')[:50]}...", "info")

            if not state.database.update_step(step_id=step_id, status="Running", executed_at=datetime.now()):
                msg = f"步骤 {step_id} 状态写入失败（DB 中无此步骤），中止执行"
                debug_log(f"ABORT: {msg}")
                await broadcast_log(f"[ERROR] {msg}", "error")
                _require_write(
                    state.database.update_plan_state_machine(str(plan.plan_id), "Failed"),
                    f"计划 {plan.plan_id} 状态 → Failed（步骤 {step_id} 未命中）",
                )
                await broadcast_plan_update(str(plan.plan_id))
                break

            await broadcast_plan_update(str(plan.plan_id))

            max_attempts = getattr(state, "retry_max_attempts", 3)
            backoff_factor = getattr(state, "retry_backoff_factor", 2)
            attempt = 0
            result = None
            logger.info(f"STEP_LOOP_START step_id={step_id} max_attempts={max_attempts}")
            while True:
                logger.info(f"STEP_LOOP_CALL step_id={step_id} attempt={attempt}")
                try:
                    result = await _execute_step_with_state_machine(plan, step)
                except Exception as e:
                    logger.info(f"STEP_LOOP_EXCEPTION step_id={step_id} attempt={attempt} exc={e}")
                    raise
                logger.info(f"STEP_LOOP_RESULT step_id={step_id} attempt={attempt} success={result.get('success')} check={result.get('check_status')} blocked={result.get('blocked')}")
                debug_log(
                    f"Step {step_id} attempt {attempt} result: success={result.get('success')} "
                    f"check={result.get('check_status')} blocked={result.get('blocked')}"
                )
                if result.get("blocked"):
                    error = result.get("error", "blocked")
                    failure_reason = error
                    debug_log(f"Step {step_id} blocked: {error}")
                    state.database.update_step_state_machine(
                        step_id=step_id,
                        status=StepStatus.FAILED.value,
                        outputs={"error": error, "check_status": result.get("check_status")},
                        duration_ms=result.get("duration_ms"),
                        failure_reason=failure_reason,
                        state_snapshot=result,
                    )
                    if not state.database.add_step_event(step_id, plan_id, "failed", {"reason": failure_reason, "blocked": True, "duration_ms": result.get("duration_ms")}):
                        logger.warning("step_event failed 写入失败: {}", step_id)
                    _require_write(
                        state.database.update_plan_state_machine(str(plan.plan_id), "Failed"),
                        f"计划 {plan.plan_id} 状态 → Failed（步骤 {step_id} 被拦截）",
                    )
                    await broadcast_plan_update(str(plan.plan_id))
                    break

                if result.get("check_status") == "failed":
                    error = result.get("error", "check failed")
                    failure_reason = error
                    debug_log(f"Step {step_id} check failed: {error}")
                    state.database.update_step_state_machine(
                        step_id=step_id,
                        status=StepStatus.FAILED.value,
                        outputs={"error": error, "check_status": "failed"},
                        duration_ms=result.get("duration_ms"),
                        failure_reason=failure_reason,
                        state_snapshot=result,
                    )
                    if not state.database.add_step_event(step_id, plan_id, "failed", {"reason": failure_reason, "blocked": False, "duration_ms": result.get("duration_ms")}):
                        logger.warning("step_event failed 写入失败: {}", step_id)
                    _require_write(
                        state.database.update_plan_state_machine(str(plan.plan_id), "Failed"),
                        f"计划 {plan.plan_id} 状态 → Failed（步骤 {step_id} 校验失败）",
                    )
                    await broadcast_plan_update(str(plan.plan_id))
                    break

                if result.get("success"):
                    break

                if attempt >= max_attempts - 1:
                    error = result.get("error", "retry exhausted")
                    failure_reason = error
                    check_status = result.get("check_status")
                    debug_log(f"Step {step_id} retry exhausted")
                    if check_status == "failed" or not result.get("success"):
                        state.database.update_step_state_machine(
                            step_id=step_id,
                            status=StepStatus.FAILED.value,
                            outputs={"error": error, "check_status": check_status},
                            duration_ms=result.get("duration_ms"),
                            failure_reason=failure_reason,
                            retry_count=attempt,
                            state_snapshot=result,
                        )
                        if not state.database.add_step_event(step_id, plan_id, "failed", {"reason": failure_reason, "blocked": False, "duration_ms": result.get("duration_ms"), "attempt": attempt}):
                            logger.warning("step_event failed 写入失败: {}", step_id)
                        _require_write(
                            state.database.update_plan_state_machine(str(plan.plan_id), "Failed"),
                            f"计划 {plan.plan_id} 状态 → Failed（步骤 {step_id} 重试耗尽）",
                        )
                        await broadcast_plan_update(str(plan.plan_id))
                        break
                    # success=False 且 check_status=unverified: 仍视为完成，继续后续步骤
                    result["terminal_success"] = True
                    state.database.update_step_state_machine(
                        step_id=step_id,
                        status="Success",
                        outputs={"check_status": check_status, "message": error},
                        duration_ms=result.get("duration_ms"),
                        state_snapshot=result,
                    )
                    if not state.database.add_step_event(step_id, plan_id, "success", {"duration_ms": result.get("duration_ms"), "note": "unverified treated as success"}):
                        logger.warning("step_event success 写入失败: {}", step_id)
                    await broadcast_plan_update(str(plan.plan_id))
                    break

                attempt += 1
                delay = backoff_factor ** attempt
                await broadcast_log(
                    f"[RETRY] 步骤 {step_id} 失败，第 {attempt}/{max_attempts - 1} 次重试（{delay:.1f}秒后）",
                    "warning",
                )
                debug_log(f"Step {step_id} retrying, attempt {attempt}/{max_attempts - 1}, delay={delay}")
                print(f"[RETRY] step={step_id} attempt={attempt} delay={delay}", flush=True)
                state.database.update_step_state_machine(step_id=step_id, status="Retry", retry_count=attempt)
                _require_write(
                    state.database.add_step_event(
                        step_id, str(plan.plan_id), "retry",
                        {"attempt": attempt, "delay": delay}, branch_version=branch_version,
                    ),
                    f"步骤 {step_id} retry 事件写入",
                )
                await broadcast_plan_update(str(plan.plan_id))
                await asyncio.sleep(delay)

            print(f"[AFTER_LOOP] step={step_id} success={result.get('success')} check={result.get('check_status')} attempt={attempt}", flush=True)
            # 明确失败才终止循环继续执行。
            # success=False 是执行失败；check_status="failed" 是校验明确不通过。
            # "unverified" 只表示无法自动校验，不应阻断计划继续。
            if (not result.get("success") and not result.get("terminal_success")) or result.get("check_status") == "failed":
                await broadcast_log(f"[ERROR] 步骤 {step_id} 失败（已重试 {attempt} 次）: {result.get('error')}", "error")
                break

            # 成功但未校验：视为完成，继续执行后续步骤
            await broadcast_log(f"[OK] 步骤 {step_id} 完成（未校验）", "success")
            # 不要在这里 break；外循环需要继续取下一个 step

    except Exception as e:
        debug_log(f"EXCEPTION in execute_plan_background: {e}")
        import traceback
        debug_log(f"Traceback: {traceback.format_exc()}")
        try:
            state.database.update_plan_state_machine(str(plan.plan_id), "Failed")
            await broadcast_plan_update(str(plan.plan_id))
        except Exception as inner:
            debug_log(f"update_plan failed during exception handling: {inner}")
        await broadcast_log(f"执行失败: {str(e)}", "error")
    finally:
        async with state.execution_lock:
            state.current_execution = None
        debug_log(f"execute_plan_background FINISHED for plan {plan.plan_id}")



@app.post("/api/plan/{plan_id}/rollback", dependencies=[Depends(require_token)])
async def rollback_plan(plan_id: str, request: Optional[Dict[str, Any]] = None):
    """回滚到指定 step，并生成新 branch。"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")

        body = request or {}
        target_step_id = body.get("step_id")
        strategy = body.get("strategy", "retry")
        if not target_step_id:
            raise HTTPException(status_code=400, detail="缺少 step_id")

        current_branch = getattr(plan, 'current_branch', 1) or 1
        new_branch = current_branch + 1

        # 先克隆到新分支并校验结果，再废弃旧分支步骤——
        # 任何一步失败都不得留下「步骤已废弃但没有克隆」的破损状态。
        if not state.database.clone_steps_from(plan_id, target_step_id, new_branch):
            raise HTTPException(status_code=500, detail="克隆步骤到新分支失败，回滚未执行")
        marked = state.database.mark_steps_obsolete(plan_id, target_step_id, current_branch)
        _require_write(
            state.database.update_plan_state_machine(
                plan_id,
                PlanStatus.PENDING.value,
                resume_from_step_id=target_step_id,
                current_branch=new_branch,
            ),
            f"计划 {plan_id} 状态 → Pending（回滚到步骤 {target_step_id}，新分支 {new_branch}）",
        )
        state.database.add_step_event(
            target_step_id,
            plan_id,
            "rolled_back",
            {"strategy": strategy, "new_branch": new_branch, "marked_obsolete": marked},
            branch_version=new_branch,
        )
        await broadcast_plan_update(plan_id)
        await broadcast_log(f"计划已回滚到 {target_step_id}，新 branch={new_branch}", "warning")
        return {
            "success": True,
            "new_branch": new_branch,
            "resume_from": target_step_id,
            "strategy": strategy,
            "marked_obsolete": marked,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("回滚计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

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
        logger.exception("查询审计日志失败: {}", e)
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
        logger.exception("查询工具列表失败: {}", e)
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
        logger.exception("查询配置失败: {}", e)
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
        logger.exception("更新配置失败: {}", e)
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
        logger.exception("AI 助手对话失败: {}", e)
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
