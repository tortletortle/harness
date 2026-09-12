"""
FastAPI 服务器
提供 RESTful API 和 WebSocket 实时日志
"""

import asyncio
import inspect
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
import sys

# 本地凭据文件（gitignored）支持：优先读环境变量，其次读仓库根 .env。
# 绝不把可用凭据写进源码或被跟踪文件。
_dotenv_path = Path(__file__).resolve().parents[2] / ".env"
if os.getenv("DOTENV_LOADED") is None:
    try:
        from dotenv import load_dotenv

        load_dotenv(_dotenv_path)
        os.environ.setdefault("DOTENV_LOADED", "1")
    except Exception:
        pass

from contextlib import asynccontextmanager

from fastapi import (
    Body,
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
from models import DesignDocument, ExecutionPlan, PlanSegment, Step, StepStatus, PlanStatus, RunStatus, AttemptStatus
from api.auth import load_token, require_token, verify_token
from api.shared import (  # 拆分：状态/广播/助手（行为不变，符号保留在 server 命名空间）
    AppState, state, _require_write, _deep_merge_dict, _load_models_config,
    _send_to_clients, broadcast_log, broadcast_plan_update,
    broadcast_approval_request, broadcast_approval_resolved,
    _format_breakdown, format_design_for_frontend, format_plan_for_frontend,
    _compute_pause_checkpoint,
)
from api.routes import (
    design_routes, plan_routes, approval_routes, artifact_routes,
    agent_session_routes, worktree_routes, quick_task_routes, replan_routes,
    misc_routes,
)

from api.approval import ApprovalManager, ApprovalTimeout
from authorization import AuthorizationManager, GrantLimits, agent_step_grant_params
from capabilities import CapabilityRegistry, HumanCapabilityAdapter, ToolCapabilityAdapter
from coding_agent import CodingAgentTool
from mcp_client import MCPStdioClient, load_mcp_server_configs
from mcp_adapter import MCPToolAdapter
from chat import ChatManager
from skills_loader import SkillManager, SkillCapabilityAdapter
from notify import Notifier
from executor.acceptance_feedback import build_acceptance_feedback

# 兼容别名：既有调用点与测试沿用旧名（server.py 拆分过渡期保留）。
_build_acceptance_feedback = build_acceptance_feedback
print("[SERVER MODULE LOADED]", __file__)
from logging_config import get_logger

logger = get_logger(__name__)

# ===== 数据模型 =====

class ExecutionRequest(BaseModel):
    """执行计划请求"""
    plan_id: str

# ===== 全局状态 =====

def _load_models_config() -> dict:
    """加载提交的模型模板与本地非敏感覆盖，并从环境变量注入凭据。"""
    config_root = Path(__file__).resolve().parents[2] / "config"
    candidates = [config_root / "models.yaml", Path.cwd() / "config" / "models.yaml"]
    config_path = next((p for p in candidates if p.exists()), None)
    if not config_path:
        raise FileNotFoundError("配置文件 config/models.yaml 不存在")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    local_candidates = [config_root / "models.local.yaml", Path.cwd() / "config" / "models.local.yaml"]
    local_path = next((p for p in local_candidates if p.exists()), None)
    if local_path:
        with open(local_path, "r", encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        # Local overrides may customize models and endpoints, but credentials
        # are never accepted from YAML files.
        local.pop("api_keys", None)
        config = _deep_merge_dict(config, local)

    # Credentials come only from environment variables (or an external secret
    # manager populated into them). Template/local YAML values are ignored.
    config["api_keys"] = {}
    api_keys = config["api_keys"]
    base_urls = config.setdefault("base_urls", {})
    for provider_name in list(base_urls):
        env_key = os.getenv(f"EAS_LLM_API_KEY_{provider_name.upper()}")
        env_url = os.getenv(f"EAS_LLM_BASE_URL_{provider_name.upper()}")
        if env_key:
            api_keys[provider_name] = env_key
        if env_url:
            base_urls[provider_name] = env_url
    return config


# ===== 启动和关闭（lifespan） =====

@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务启动/关闭时的生命周期管理，替代已废弃的 @app.on_event"""
    logger.info("正在启动 EAS 2.0 API 服务...")

    try:
        # 加载配置（models.yaml 模板 + models.local.yaml 深覆盖，后者存真实 key 且不入库）
        state.config = _load_models_config()
        
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

        # 普通同步工具通过 Adapter 暴露为统一 Capability；Agent Tool 以后
        # 以同一 registry 注册，不创建层级化子 Agent。
        state.capability_registry = CapabilityRegistry()
        for tool_meta in state.tool_registry.list_tools():
            tool_id = tool_meta.get("tool_id")
            # manual_tool 不进 deterministic 清单：它以 human_tool 第三类
            # 注册（见下），对应设计稿 §3 Human Tool。
            if tool_id and tool_id != "manual_tool":
                state.capability_registry.register(
                    ToolCapabilityAdapter(state.tool_registry, tool_id)
                )
        # 人工步骤第三类（§23.1）：状态机对 human_tool 步骤强制挂审批，
        # 修复"manual 步骤自动通过"的偏离。
        state.capability_registry.register(
            HumanCapabilityAdapter(state.tool_registry, "manual_tool")
        )

        # MCP 生态（设计稿 1.11）：启动配置的外部 MCP server，把其工具包装成
        # Capability（kind=deterministic_tool，side_effect=external），走 Grant 边界。
        # 单个 server 接入失败只记日志，不影响主服务。
        state.mcp_clients = []
        for _mcp_name, _mcp_entry in load_mcp_server_configs(state.config).items():
            _mcp_client = None
            try:
                _mcp_client = MCPStdioClient(
                    _mcp_name,
                    _mcp_entry["command"],
                    _mcp_entry["args"],
                    env=_mcp_entry.get("env"),
                    cwd=_mcp_entry.get("cwd"),
                    request_timeout=float(_mcp_entry.get("request_timeout", 30)),
                )
                await _mcp_client.start()
                state.mcp_clients.append(_mcp_client)
                _mcp_registered = 0
                for _tool in _mcp_client.tools:
                    _adapter = MCPToolAdapter(_mcp_client, _mcp_name, _tool)
                    if state.capability_registry.get(_adapter.tool_id) is None:
                        state.capability_registry.register(_adapter)
                        _mcp_registered += 1
                await broadcast_log(
                    f"MCP server {_mcp_name} 已接入（{len(_mcp_client.tools)} 个工具，注册 {_mcp_registered}）",
                    "success",
                )
            except Exception as _mcp_err:
                await broadcast_log(
                    f"MCP server {_mcp_name} 接入失败（不影响主服务）: {_mcp_err}",
                    "warning",
                )
                if _mcp_client is not None:
                    try:
                        await _mcp_client.stop()
                    except Exception:
                        pass
        
        # 技能包（生态层）：skills/ 目录 → ToolRegistry + Capability 目录双注册。
        # entry 技能成为普通工具：计划步骤可执行、Designer 工具评估可见、对话可调用。
        state.skill_manager = SkillManager.from_config(state.config)
        state.skill_manager.load()
        for _skill_tool_id in state.skill_manager.register_into(state.tool_registry):
            state.capability_registry.register(
                SkillCapabilityAdapter(
                    state.tool_registry, _skill_tool_id, state.skill_manager.by_tool_id(_skill_tool_id)
                )
            )
        await broadcast_log(
            f"技能包已加载: {len(state.skill_manager.entry_skills())} 个入口技能 / "
            f"{len(state.skill_manager.knowledge_skills())} 个知识包"
            + (f"，跳过 {len(state.skill_manager.skipped)} 个" if state.skill_manager.skipped else ""),
            "success",
        )

        # 初始化 Dispatcher（携带 CapabilityRegistry：工具目录合并 agent_tool，
        # Designer 的设计书/计划生成因此能看到 coding_agent）
        from dispatcher import Dispatcher
        state.dispatcher = Dispatcher(state.tool_registry, state.capability_registry)
        
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

        # P0 预算：计划级 token 预算（0 = 不限制；超限自动转 Paused，可 /resume 恢复）
        budget_cfg = state.config.get("budget", {})
        state.budget_max_tokens_per_plan = int(budget_cfg.get("max_tokens_per_plan", 0) or 0)

        # 性能：步骤上下文摘要默认关闭；只有显式开启且输出足够大时才调用 LLM。
        # 该摘要目前只作为观测字段保存，不阻塞主执行链路。
        state.context_compression = state.config.get("context_compression", {}) or {}

        # 受控并行：默认严格串行（max_concurrency=1），只有显式开启
        # parallel_graphs 且步骤声明依赖图时才启用计划内批级并行。
        executor_cfg = state.config.get("models", {}).get("executor", {}) or {}
        state.executor_max_concurrency = max(1, int(executor_cfg.get("max_concurrency", 1) or 1))
        state.parallel_graphs = bool(state.config.get("parallel_graphs", False))

        # 默认空列表 = 全部自动执行（向后兼容，现有测试/流程不变）。
        approval_cfg = state.config.get("approval", {})
        state.approval_manager = ApprovalManager(
            required_tools=approval_cfg.get("required_tools", []),
            timeout_seconds=approval_cfg.get("timeout_seconds", 600),
            human_steps=approval_cfg.get("human_steps", True),
        )

        # IM 通知通道（入口层）：fail-open——任何通知失败只记日志与本地审计，
        # 绝不打断主流程；未配置任何通道时降级为本地审计 notifications.jsonl。
        _notify_audit_dir = Path("data") if Path("data").exists() else Path("../data")
        state.notifier = Notifier.from_config(state.config, audit_dir=_notify_audit_dir)
        await broadcast_log(
            f"通知通道就绪: {len(state.notifier.providers)} 个 IM 通道"
            + ("（未配置 webhook，仅本地审计 data/notifications.jsonl）" if not state.notifier.providers else ""),
            "success" if state.notifier.providers else "warning",
        )

        # Runtime 临时授权：每个步骤单独签发，默认限制一次工具调用、
        # 禁止 .env/.git 资源，结束后撤销；不把授权对象放进模型上下文。
        state.authorization_manager = AuthorizationManager.from_config(
            state.config.get("authorization", {})
        )
        # Executor 通过同一个 manager 执行调用前授权检查。
        state.executor.authorization_manager = state.authorization_manager

        # 应用内置对话助手（chat 会话层）：与执行链路共用 LLM / Capability /
        # 授权体系；chat.provider/model 未配置时回落到 designer 主模型。
        _chat_cfg = state.config.setdefault("chat", {})
        if not _chat_cfg.get("provider"):
            _designer_primary = (
                (state.config.get("models", {}) or {}).get("designer", {}) or {}
            ).get("primary", {}) or {}
            _chat_cfg.setdefault("provider", _designer_primary.get("provider", "newapi"))
            _chat_cfg.setdefault("model", _designer_primary.get("model", ""))
        if state.skill_manager is not None and state.skill_manager.skills:
            _chat_cfg["skills_summary"] = "\n".join(state.skill_manager.summary_lines())
        state.chat_manager = ChatManager(
            state.llm_client,
            state.capability_registry,
            state.authorization_manager,
            state.config,
        )
        logger.info(
            "对话助手已就绪: provider={} model={}",
            _chat_cfg.get("provider"),
            _chat_cfg.get("model"),
        )

        # 设计期搜索授权与审计（§23.3）：ToolRegistryAgent 的网络抓取
        # 走 design_search Grant，不再旁路授权体系。
        state.tool_registry.authorization_manager = state.authorization_manager
        state.tool_registry.database = state.database

        # 测试模板白名单：config test_runner.commands 覆盖内置模板（§23.2）。
        # 模板只能来自服务端配置，不来自步骤输入。
        from tools.test_runner import configure_templates
        configure_templates((state.config.get("test_runner", {}) or {}).get("commands") or {})

        # Agent 即工具（第二阶段第一步）：把 coding_agent 注册进统一
        # Capability 清单。必须在 authorization_manager 定稿后构建，
        # 绑定最终 manager；Executor 通过 registry 识别 agent_tool 步骤。
        executor_primary = state.config.get("models", {}).get("executor", {}).get("primary", {})
        state.capability_registry.register(
            CodingAgentTool(
                llm_client=state.llm_client,
                authorization_manager=state.authorization_manager,
                tool_registry=state.tool_registry,
                provider_name=executor_primary.get("provider", ""),
                model=executor_primary.get("model", ""),
                database=state.database,
            )
        )
        # review / diagnosis Agent（单轮，Grant 受限，复用同一 Capability 管道）
        from agent_tools import SingleTurnAgentTool
        state.capability_registry.register(
            SingleTurnAgentTool(
                agent_id="review_agent",
                description=(
                    "代码审查 Agent：审查指定文件或变更，输出结构化 review 报告"
                    "（status/issues[{severity,title,detail}]/conclusion）。inputs: {path}"
                ),
                llm_client=state.llm_client,
                authorization_manager=state.authorization_manager,
                provider_name=executor_primary.get("provider", ""),
                model=executor_primary.get("model", ""),
            )
        )
        state.capability_registry.register(
            SingleTurnAgentTool(
                agent_id="diagnosis_agent",
                description=(
                    "故障诊断 Agent：基于失败步骤与失败原因输出结构化诊断"
                    "（status/root_cause_hypotheses/recommended_action(retry|repair|manual|replan)）。"
                    "inputs: {step_description, failure_reason, tool}"
                ),
                llm_client=state.llm_client,
                authorization_manager=state.authorization_manager,
                provider_name=executor_primary.get("provider", ""),
                model=executor_primary.get("model", ""),
            )
        )
        state.executor.capability_registry = state.capability_registry

        logger.info("EAS 2.0 API 服务启动成功")

        # 孤儿任务恢复（P1-a 事件重放式）：
        # - 计划 Running → Failed（进程级中断是明确失败信号，重新执行即可从断点续跑）
        # - 中断步骤：事件流有 success → 修正为 Success（状态写入丢失的场景）；
        #   否则补 interrupted 事件并标回 Pending，下次执行自动重跑
        # - 执行 Run/Attempt 层：Running Run 标记 Interrupted，未完成 Attempt 同样标记，
        #   避免失去持有者的 Run 永久停留在 Running（与计划层恢复互补）
        recovered = state.database.recover_orphaned_executions()
        if recovered:
            logger.warning("检测到 {} 个重启前未完成的执行，计划已标记为 Failed（中断步骤将按事件重放恢复）", recovered)
        recovered_runs = state.database.recover_orphaned_runs()
        if recovered_runs:
            logger.warning("检测到 {} 个重启前遗留的执行 Run，已标记为 Interrupted", recovered_runs)

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

    # 关闭时清理：停掉 MCP 客户端
    for _client in getattr(state, 'mcp_clients', []) or []:
        try:
            await _client.stop()
        except Exception:
            pass
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

# ===== 路由装配（按领域拆分至 api/routes/）=====
app.include_router(design_routes.router)
app.include_router(plan_routes.router)
app.include_router(approval_routes.router)
app.include_router(artifact_routes.router)
app.include_router(agent_session_routes.router)
app.include_router(worktree_routes.router)
app.include_router(quick_task_routes.router)
app.include_router(replan_routes.router)
app.include_router(misc_routes.router)


@app.exception_handler(DatabaseError)
async def _database_error_handler(request, exc):
    return JSONResponse(status_code=500, content={"detail": str(exc), "code": "database_error"})


@app.exception_handler(GenerationError)
async def _generation_error_handler(request, exc):
    return JSONResponse(status_code=502, content={"detail": str(exc), "code": "generation_failed"})

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
                "acceptance": step.get('acceptance') or [],
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

def _is_retryable_exit_code_acceptance(step: Dict[str, Any], result: Dict[str, Any]) -> bool:
    """Return true only for a transient command exit-code mismatch."""
    if step.get("tool") != "command_runner" or not isinstance(result, dict):
        return False
    if result.get("returncode") is None and result.get("exit_code") is None:
        return False
    acceptance = result.get("acceptance_result")
    checks = acceptance.get("checks", []) if isinstance(acceptance, dict) else []
    failed = [check for check in checks if not check.get("passed")]
    return bool(failed) and all(check.get("type") == "exit_code" for check in failed)


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


async def _compress_step_context(plan_id: str, step_id: str, result: Dict[str, Any], branch_version: int = 1) -> Optional[str]:
    """
    按配置压缩 step 结果；默认关闭，避免每个成功步骤额外阻塞一次 LLM。
    摘要只由调用方在最终状态写入，避免数据库双写。
    """
    compression_config = getattr(state, "context_compression", {}) or {}
    if not compression_config.get("enabled", False):
        return None
    try:
        raw = result.get("output") or {}
        payload = {
            "step_id": str(step_id),
            "success": result.get("success"),
            "check_status": result.get("check_status"),
            "error": result.get("error"),
            "tool_output": raw,
        }
        raw_text = json.dumps(payload, ensure_ascii=False)
        max_input_chars = int(compression_config.get("max_input_chars", 4000))
        if len(raw_text) < int(compression_config.get("min_input_chars", 1000)):
            return None
        prompt = (
            "请把以下执行结果压缩成 1-3 句中文摘要，保留关键信息："
            "创建了什么文件、输出路径、关键参数、错误信息。\n\n"
            f"原始输出：\n{raw_text[:max_input_chars]}"
        )
        summary = await asyncio.wait_for(
            state.llm_client.chat(
                provider_name=compression_config.get("provider", "newapi"),
                messages=[Message(role="user", content=prompt)],
                model=compression_config.get("model", "claude-haiku-4-5-20251001"),
                temperature=0.2,
                max_tokens=int(compression_config.get("max_tokens", 300)),
            ),
            timeout=float(compression_config.get("timeout_seconds", 10)),
        )
        summary_text = summary.content.strip()
        max_output_chars = int(compression_config.get("max_output_chars", 1000))
        return summary_text[:max_output_chars] or None
    except Exception as e:
        logger.warning("上下文压缩失败（不影响主流程）: {}", e)
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
        acceptance=json.loads(step["acceptance_json"]) if isinstance(step.get("acceptance_json"), str) and step.get("acceptance_json") else (step.get("acceptance") or []),
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

    if not state.database.add_step_event(step_id, plan_id, "running", branch_version=step_model.branch_version):
        logger.warning("step_event running 写入失败: {}", step_id)

    # ===== P0-a 工具级审批闸门 =====
    # 高风险工具在执行前挂起等待人工批准（Cline 式 asyncio.Event 挂起，非轮询）。
    # 拒绝/超时 → 返回结构化 blocked 结果，由调用方走 Failed 路径（重试无意义）。
    tool_id_for_approval = str(step.get("tool", "")).split(":")[0]
    step_capability = (
        state.capability_registry.get(tool_id_for_approval)
        if state.capability_registry is not None
        else None
    )
    step_capability_kind = (
        getattr(step_capability.spec(), "kind", None) if step_capability is not None else None
    )
    if state.approval_manager.is_required_for_step(tool_id_for_approval, step_capability_kind):
        approval = state.approval_manager.create(
            plan_id=plan_id,
            step_id=step_id,
            tool_id=tool_id_for_approval,
            instruction=str(step.get("description", "")),
            inputs=step_model.inputs,
        )
        state.database.add_step_event(
            step_id, plan_id, "approval_requested",
            {"approval_id": approval.approval_id, "tool_id": tool_id_for_approval},
            branch_version=step_model.branch_version,
        )
        await broadcast_approval_request(approval)
        await broadcast_log(
            f"[APPROVAL] 步骤 {step_id} 请求使用工具 {tool_id_for_approval}，等待人工批准", "warning",
        )
        if state.notifier is not None:
            asyncio.create_task(
                state.notifier.send(
                    "approval_required",
                    f"待审批：{tool_id_for_approval}",
                    f"计划 {plan_id} 的步骤请求使用高风险工具 {tool_id_for_approval}，"
                    f"已挂起等待人工批准。\n指令：{str(approval.instruction)[:120]}\n请到 Web 界面完成审批。",
                    payload={"plan_id": plan_id, "approval_id": approval.approval_id,
                             "tool_id": tool_id_for_approval},
                    dedup_key=str(approval.approval_id),
                )
            )
        try:
            decision = await state.approval_manager.wait(approval.approval_id)
        except ApprovalTimeout:
            decision = None
            await broadcast_log(f"[APPROVAL] 步骤 {step_id} 审批超时，视为拒绝", "error")
        state.database.add_step_event(
            step_id, plan_id,
            "approval_rejected" if (decision is None or not decision.approved) else "approval_approved",
            {
                "approval_id": approval.approval_id,
                "comment": decision.comment if decision else "timeout",
            },
            branch_version=step_model.branch_version,
        )
        state.database.save_audit_log(
            tool_id=tool_id_for_approval,
            instruction=approval.instruction,
            inputs=step_model.inputs,
            plan_id=plan_id,
            step_id=step_id,
            success=False,
            blocked=True,
            block_reason="user_rejected" if decision is None else f"user_rejected: {decision.comment or ''}",
            actor="user",
        )
        await broadcast_approval_resolved(approval.approval_id, approved=bool(decision and decision.approved))
        state.approval_manager.cleanup(approval.approval_id)

        if decision is None or not decision.approved:
            reject_reason = (
                f"操作被用户拒绝: {decision.comment}" if decision else "操作被用户拒绝（审批超时）"
            )
            logger.warning("步骤 {} 的工具 {} 被拒绝执行", step_id, tool_id_for_approval)
            return {
                "success": False,
                "output": None,
                "check_status": "unverified",
                "check_passed": False,
                "error": reject_reason,
                "blocked": True,
                "blocked_reason": "user_rejected",
                "retryable": False,
                "duration_ms": 0,
            }
        await broadcast_log(f"[APPROVAL] 步骤 {step_id} 审批通过，继续执行", "info")

    # Runtime Grant：审批（若有）结束后、工具调用前签发一次性授权。
    # Grant 本体只留在 Runtime 内存；事件只记录 id 与用量摘要，不把授权放入模型上下文。
    # agent_tool 步骤：把 spec 声明的内部工具并入 allowed_tools，
    # 并用单轮模型预算覆盖默认 limits（agent_step_grant_params 对普通步骤返回空值）。
    agent_capability = (
        state.capability_registry.get(tool_id_for_approval)
        if state.capability_registry is not None
        else None
    )
    agent_extra_tools, agent_limits = agent_step_grant_params(step_model, agent_capability)
    # Worktree 绑定：计划挂在隔离 worktree 上时，Grant 的 workspace 指向
    # worktree 而非主工作区——命令类工具的隐式 cwd/path 与资源检查随之一致。
    _active_wt = state.database.get_active_worktree(plan_id)
    grant = state.authorization_manager.issue_for_step(
        plan_id=plan_id,
        step=step_model,
        subject="executor",
        approval_required=False,
        extra_allowed_tools=agent_extra_tools,
        limits=agent_limits,
        workspace_root=_active_wt["worktree_path"] if _active_wt else None,
    )
    state.database.add_step_event(
        step_id, plan_id, "grant_issued",
        {"grant_id": grant.grant_id, "expires_at": grant.expires_at.isoformat(), "tool_id": tool_id_for_approval},
        branch_version=step_model.branch_version,
    )
    started = datetime.utcnow()
    try:
        # 兼容尚未升级 grant_id 参数的测试/第三方 Executor；正式 DefaultExecutor
        # 始终走带 Grant 的调用路径。
        execute_step = state.executor.execute_step
        signature = inspect.signature(execute_step).parameters
        kwargs = {}
        if "grant_id" in signature:
            kwargs["grant_id"] = grant.grant_id
        if "plan_id" in signature:
            kwargs["plan_id"] = plan_id
        result = dict(await execute_step(step_model, **kwargs))
        result.setdefault("grant_id", grant.grant_id)
        result.setdefault("grant_usage", grant.usage())
        grant_denied = result.get("blocked_reason") in {
            "grant_not_found",
            "grant_revoked",
            "grant_expired",
            "tool_not_granted",
            "resource_denied",
            "budget_exceeded",
            "grant_required",
        }
        state.database.add_step_event(
            step_id,
            plan_id,
            "grant_denied" if grant_denied else "grant_allowed",
            {
                "grant_id": grant.grant_id,
                "reason": result.get("blocked_reason") if grant_denied else "allowed",
                "usage": result.get("grant_usage"),
            },
            branch_version=step_model.branch_version,
        )
    finally:
        state.authorization_manager.revoke(grant.grant_id)
        state.database.add_step_event(
            step_id, plan_id, "grant_revoked",
            {"grant_id": grant.grant_id, "usage": grant.usage()},
            branch_version=step_model.branch_version,
        )
    duration_ms = int((datetime.utcnow() - started).total_seconds() * 1000)

    success = bool(result.get("success"))
    check_status = result.get("check_status")
    blocked = bool(result.get("blocked"))
    error = result.get("error")

    # acceptance 结果是独立验收证据，不信任工具自报内容。
    acceptance_result = result.get("acceptance_result")
    tool_output = result.get("output")
    if success and check_status != "failed":
        status = StepStatus.SUCCESS.value
        outputs = dict(tool_output) if isinstance(tool_output, dict) else {"value": tool_output}
        outputs["check_status"] = check_status
        if acceptance_result is not None:
            outputs["acceptance"] = acceptance_result
            outputs["acceptance_status"] = result.get("acceptance_status")
            outputs["acceptance_evidence"] = result.get("acceptance_evidence", [])
            outputs["reward"] = result.get("reward")
        compressed = await _compress_step_context(
            plan_id, step_id, result, branch_version=step_model.branch_version
        )
        state.database.update_step_state_machine(
            step_id=str(step_id),
            status=status,
            outputs=outputs,
            duration_ms=duration_ms,
            compressed_context=compressed,
            state_snapshot=result,
        )
        event_data = {"duration_ms": duration_ms}
        if acceptance_result is not None:
            event_data.update({
                "acceptance_status": result.get("acceptance_status"),
                "acceptance_evidence": result.get("acceptance_evidence", []),
                "reward": result.get("reward"),
            })
        if not state.database.add_step_event(
            step_id, plan_id, "success", event_data,
            branch_version=step_model.branch_version,
        ):
            logger.warning("step_event success 写入失败: {}", step_id)
        result["duration_ms"] = duration_ms
        return result

    # 仅记录 failed 事件，不写入 Failed 状态，留给调用方在最终重试耗尽后统一写入
    failure_reason = error or check_status or "unknown"
    event_data = {"reason": failure_reason, "blocked": blocked, "duration_ms": duration_ms}
    if acceptance_result is not None:
        event_data.update({
            "acceptance_status": result.get("acceptance_status"),
            "acceptance_evidence": result.get("acceptance_evidence", []),
            "reward": result.get("reward"),
        })
    if not state.database.add_step_event(
        step_id, plan_id, "failed", event_data,
        branch_version=step_model.branch_version,
    ):
        logger.warning("step_event failed 写入失败: {}", step_id)
    result["duration_ms"] = duration_ms
    return result


async def _generate_final_report(plan, run_id: str = None) -> None:
    """计划 Completed 后生成最终交付报告（Designer LLM），落 artifacts 表。

    fail-open：Designer 缺失或 LLM 失败只记日志，绝不影响收尾状态。
    """
    try:
        if state.designer is None or state.llm_client is None:
            return
        results = [step for segment in plan.segments for step in segment.steps]
        summary = await state.designer.generate_summary(plan, results)
        if not summary or not str(summary).strip():
            return
        record = state.database.store_artifact_content(
            artifact_id=f"report-{str(plan.plan_id)[:8]}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            content=str(summary).encode("utf-8"),
            plan_id=str(plan.plan_id),
            run_id=run_id,
            kind="delivery_report",
            metadata={"source": "final_report", "plan_status": "Completed"},
        )
        await broadcast_log(f"[REPORT] 最终交付报告已生成: {record['artifact_id']}", "success")
    except Exception as report_exc:
        logger.warning("最终交付报告生成失败（忽略）: {}", report_exc)


def _register_step_artifacts(plan_id: str, step_id: str, result: Dict[str, Any]) -> None:
    """步骤成功后把产物引用登记进 artifacts 表（仅元数据，不拷贝内容）。

    收集来源：工具输出的 path（file_creator/text_editor 类）、agent 的
    file:// 产物引用。登记失败只降级（观察性），绝不影响执行主链路。
    """
    try:
        candidates: List[str] = []
        output = result.get("output")
        if isinstance(output, dict):
            p = output.get("path")
            if isinstance(p, str) and p.strip():
                candidates.append(p.strip())
        for ref in (result.get("agent_artifacts") or []):
            if isinstance(ref, str) and ref.startswith("file://"):
                candidates.append(ref[len("file://"):])
        seen = set()
        for path_text in candidates:
            normalized = path_text.replace("\\", "/")
            if normalized in seen:
                continue
            seen.add(normalized)
            try:
                p = Path(path_text)
                size = p.stat().st_size if p.exists() else None
            except OSError:
                size = None
            state.database.create_artifact(
                artifact_id=f"{plan_id[:8]}-{step_id[:8]}-{len(seen)}-{normalized.replace('/', '_')[-60:]}",
                kind="file",
                uri=f"file://{normalized}",
                plan_id=plan_id,
                step_id=step_id,
                size_bytes=size,
                metadata={"source": "step_execution"},
            )
    except Exception as register_exc:
        logger.warning("步骤产物登记失败（忽略）: {}", register_exc)


async def _execute_step_to_completion(
    plan,
    step,
    run_id,
    owner_token,
    branch_version,
    resume: bool,
    skipped_ids: set,
    debug_log,
):
    """执行单个步骤直到终态（含重试/反馈/attempt/事件/状态写入）。

    返回 (outcome, step_id)，outcome ∈ {"stop", "continue"}：
    - "stop":    外层执行循环必须终止（步骤失败/拦截/校验失败/重试耗尽/中止）
    - "continue": 步骤完成（或跳过/已在恢复中完成），外层继续取下一批
    """
    step_id = str(step.get("step_id"))
    attempt_id = None
    # 本轮一旦取出断点，后续步骤按普通 pending 语义继续；失败时会重新保存
    # 当前 step_id 作为下一次 /execute 的恢复目标。
    step_status_value = step.get("status")

    if resume and step_status_value in ("Success", "Skipped"):
        debug_log(f"Step {step_id} already {step_status_value}, skip on resume")
        return "continue", step_id

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
        return "continue", step_id

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
        return "stop", step_id

    await broadcast_plan_update(str(plan.plan_id))

    configured_retries = step.get("max_retries")
    if configured_retries is None:
        max_attempts = getattr(state, "retry_max_attempts", 3)
    else:
        max_attempts = max(1, int(configured_retries) + 1)
    backoff_factor = getattr(state, "retry_backoff_factor", 2)
    attempt = 0
    feedback_used = False
    result = None
    logger.info(f"STEP_LOOP_START step_id={step_id} max_attempts={max_attempts}")
    while True:
        logger.info(f"STEP_LOOP_CALL step_id={step_id} attempt={attempt}")
        # 每次工具调用落一条 Attempt 记录；重试/反馈轮生成新一轮 Attempt。
        try:
            _attempt = state.database.create_execution_attempt(
                run_id, str(plan.plan_id), step_id, attempt + 1,
            )
            attempt_id = str(_attempt["attempt_id"])
        except Exception as attempt_exc:
            # Attempt 记录写入失败不阻断执行主体（观察性分层），仅记日志。
            attempt_id = None
            logger.warning(f"create_execution_attempt 失败（忽略）: {attempt_exc}")
        try:
            result = await _execute_step_with_state_machine(plan, step)
        except Exception as e:
            logger.info(f"STEP_LOOP_EXCEPTION step_id={step_id} attempt={attempt} exc={e}")
            if attempt_id:
                try:
                    state.database.finish_execution_attempt(
                        attempt_id, AttemptStatus.FAILED.value,
                        failure_reason=str(e)[:2000],
                        run_id=run_id, owner_token=owner_token,
                    )
                except Exception as attempt_close_exc:
                    logger.warning(f"finish_execution_attempt 失败（忽略）: {attempt_close_exc}")
                attempt_id = None
            raise
        if attempt_id:
            try:
                state.database.finish_execution_attempt(
                    attempt_id,
                    AttemptStatus.SUCCEEDED.value if result.get("success") else AttemptStatus.FAILED.value,
                    retryable=result.get("retryable"),
                    failure_reason=result.get("error") if not result.get("success") else None,
                    outputs=result,
                    duration_ms=result.get("duration_ms"),
                    run_id=run_id, owner_token=owner_token,
                )
            except Exception as attempt_close_exc:
                logger.warning(f"finish_execution_attempt 失败（忽略）: {attempt_close_exc}")
            attempt_id = None
        logger.info(f"STEP_LOOP_RESULT step_id={step_id} attempt={attempt} success={result.get('success')} check={result.get('check_status')} blocked={result.get('blocked')}")
        debug_log(
            f"Step {step_id} attempt {attempt} result: success={result.get('success')} "
            f"check={result.get('check_status')} blocked={result.get('blocked')}"
        )
        if result.get("blocked"):
            error = result.get("error", "blocked")
            failure_reason = error
            debug_log(f"Step {step_id} blocked: {error}")
            failure_outputs = {"error": error, "check_status": result.get("check_status")}
            if result.get("acceptance_result") is not None:
                failure_outputs.update({
                    "acceptance": result.get("acceptance_result"),
                    "acceptance_status": result.get("acceptance_status"),
                    "acceptance_evidence": result.get("acceptance_evidence", []),
                    "reward": result.get("reward"),
                })
            state.database.update_step_state_machine(
                step_id=step_id,
                status=StepStatus.FAILED.value,
                outputs=failure_outputs,
                duration_ms=result.get("duration_ms"),
                failure_reason=failure_reason,
                state_snapshot=result,
            )
            if not state.database.add_step_event(
                step_id, str(plan.plan_id), "failed",
                {"reason": failure_reason, "blocked": True, "duration_ms": result.get("duration_ms")},
                branch_version=branch_version,
            ):
                logger.warning("step_event failed 写入失败: {}", step_id)
            _require_write(
                state.database.update_plan_state_machine(
                    str(plan.plan_id), "Failed",
                    resume_from_step_id=step_id,
                    current_branch=branch_version,
                ),
                f"计划 {plan.plan_id} 状态 → Failed（步骤 {step_id} 被拦截）",
            )
            await broadcast_plan_update(str(plan.plan_id))
            return "stop", step_id

        if result.get("check_status") == "failed" and _is_retryable_exit_code_acceptance(step, result):
            result["retryable"] = True
        if result.get("check_status") == "failed" and not _is_retryable_exit_code_acceptance(step, result):
            # P2 反馈回路：验收缺口作为 feedback 喂回 coding_agent 再修一轮
            # （每步最多一次；重试仍失败则走下方原判死路径）。
            feedback_text = _build_acceptance_feedback(result.get("acceptance_result"))
            if (
                feedback_text
                and step.get("tool") == "coding_agent"
                and not feedback_used
            ):
                feedback_used = True
                step_inputs = dict(step.get("inputs") or {})
                step_inputs["feedback"] = feedback_text
                step = {**step, "inputs": step_inputs}
                await broadcast_log(
                    f"[FEEDBACK] 步骤 {step_id} 验收未通过，缺口已反馈给 coding_agent 重试一轮",
                    "warning",
                )
                attempt += 1
                continue
            error = result.get("error", "check failed")
            failure_reason = error
            debug_log(f"Step {step_id} check failed: {error}")
            failure_outputs = {"error": error, "check_status": "failed"}
            if result.get("acceptance_result") is not None:
                failure_outputs.update({
                    "acceptance": result.get("acceptance_result"),
                    "acceptance_status": result.get("acceptance_status"),
                    "acceptance_evidence": result.get("acceptance_evidence", []),
                    "reward": result.get("reward"),
                })
            state.database.update_step_state_machine(
                step_id=step_id,
                status=StepStatus.FAILED.value,
                outputs=failure_outputs,
                duration_ms=result.get("duration_ms"),
                failure_reason=failure_reason,
                state_snapshot=result,
            )
            if not state.database.add_step_event(
                step_id, str(plan.plan_id), "failed",
                {"reason": failure_reason, "blocked": False, "duration_ms": result.get("duration_ms")},
                branch_version=branch_version,
            ):
                logger.warning("step_event failed 写入失败: {}", step_id)
            _require_write(
                state.database.update_plan_state_machine(
                    str(plan.plan_id), "Failed",
                    resume_from_step_id=step_id,
                    current_branch=branch_version,
                ),
                f"计划 {plan.plan_id} 状态 → Failed（步骤 {step_id} 校验失败）",
            )
            await broadcast_plan_update(str(plan.plan_id))
            return "stop", step_id

        if result.get("success"):
            break

        # P2 错误分类：executor 判定不可重试的失败（拦截/永久性错误）
        # 直接进入失败路径，不等重试耗尽——重试只会重复同样的错误。
        if attempt >= max_attempts - 1 or result.get("retryable") is False:
            error = result.get("error", "retry exhausted")
            failure_reason = error
            check_status = result.get("check_status")
            debug_log(f"Step {step_id} retry exhausted")
            if check_status == "failed" or not result.get("success"):
                failure_outputs = {"error": error, "check_status": check_status}
                if result.get("acceptance_result") is not None:
                    failure_outputs.update({
                        "acceptance": result.get("acceptance_result"),
                        "acceptance_status": result.get("acceptance_status"),
                        "acceptance_evidence": result.get("acceptance_evidence", []),
                        "reward": result.get("reward"),
                    })
                state.database.update_step_state_machine(
                    step_id=step_id,
                    status=StepStatus.FAILED.value,
                    outputs=failure_outputs,
                    duration_ms=result.get("duration_ms"),
                    failure_reason=failure_reason,
                    retry_count=attempt,
                    state_snapshot=result,
                )
                if not state.database.add_step_event(
                    step_id, str(plan.plan_id), "failed",
                    {"reason": failure_reason, "blocked": False, "duration_ms": result.get("duration_ms"), "attempt": attempt},
                    branch_version=branch_version,
                ):
                    logger.warning("step_event failed 写入失败: {}", step_id)
                _require_write(
                    state.database.update_plan_state_machine(
                        str(plan.plan_id), "Failed",
                        resume_from_step_id=step_id,
                        current_branch=branch_version,
                    ),
                    f"计划 {plan.plan_id} 状态 → Failed（步骤 {step_id} 重试耗尽）",
                )
                await broadcast_plan_update(str(plan.plan_id))
                return "stop", step_id
            # success=False 且 check_status=unverified: 仍视为完成，继续后续步骤
            result["terminal_success"] = True
            state.database.update_step_state_machine(
                step_id=step_id,
                status="Success",
                outputs={"check_status": check_status, "message": error},
                duration_ms=result.get("duration_ms"),
                state_snapshot=result,
            )
            if not state.database.add_step_event(
                step_id, str(plan.plan_id), "success",
                {"duration_ms": result.get("duration_ms"), "note": "unverified treated as success"},
                branch_version=branch_version,
            ):
                logger.warning("step_event success 写入失败: {}", step_id)
            await broadcast_plan_update(str(plan.plan_id))
            _register_step_artifacts(str(plan.plan_id), step_id, result)
            return "continue", step_id

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
        return "stop", step_id

    # 成功但未校验：视为完成，继续执行后续步骤
    await broadcast_log(f"[OK] 步骤 {step_id} 完成（未校验）", "success")
    _register_step_artifacts(str(plan.plan_id), step_id, result)
    return "continue", step_id


async def execute_plan_background(plan, resume: bool = False, run_id: str = None, owner_token: str = None):
    """后台执行计划

    resume=True 时：从断点继续，用于 /resume 或回滚后的 /execute。
    状态机模式：每次只取下一个可运行 step，执行后写 event 与压缩上下文。

    Run 生命周期（保持旧计划状态机行为不变）：
    - 认领即建 Run：start_execution_run 原子完成「条件 UPDATE plans→Running + INSERT execution_run」，
      语义等价于旧的 claim_plan_status，认领失败不产生 Run，也不覆盖他人状态；
    - execute 从 Pending/Approved/Failed/Completed/RolledBack 认领，resume 只从 Paused 认领；
    - 执行期间周期心跳 Run；结束时按计划终态收尾 Run（Completed/Failed/Paused），
      带 owner_token 的 finish 使旧任务无法覆盖新任务持有的 Run。
    - 每个步骤的每次工具调用落一条 Attempt（create/finish），失败重试轮产生新 Attempt。
    """
    def debug_log(msg):
        logger.debug(msg)

    print(f"[BG] STARTED plan={plan.plan_id} resume={resume}", flush=True)

    attempt_id = None
    _bound_worktree = None
    _workspace_override_token = None
    try:
        debug_log(f"execute_plan_background STARTED for plan {plan.plan_id} (resume={resume})")

        # 原子认领计划并签发 Run（语义等价旧 claim_plan_status，见模块 docstring）。
        # - execute：从除 Paused 外的全部可执行状态认领——计划若在任务排队期间被暂停，
        #   认领必然失败，执行绝不会把 Paused 翻成 Running（暂停被无声吞掉的竞态）。
        #   Completed/RolledBack 在列以保持「重跑已完成计划」「回滚后重跑」可用。
        # - resume：只从 Paused 认领。
        if not run_id:
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
            _run = state.database.start_execution_run(
                str(plan.plan_id),
                from_statuses=claim_from,
                trigger="resume" if resume else "execute",
                branch_version=getattr(plan, 'current_branch', 1) or 1,
            )
            if _run:
                run_id = str(_run["run_id"])
                owner_token = str(_run["owner_token"])
        if run_id and not owner_token:
            owner_token = run_id
        if not run_id:
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

        # 认领成功后重新读取计划：请求入队与真正执行之间，失败重试/回滚可能已经
        # 更新了 checkpoint 或当前分支，入口传入的对象不再是权威快照。
        latest_plan = state.database.get_plan(str(plan.plan_id))
        if latest_plan is None:
            logger.warning("计划在认领后不存在，停止执行: {}", plan.plan_id)
            return
        plan = latest_plan
        branch_version = getattr(plan, 'current_branch', 1) or 1
        resume_from_step_id = (
            str(getattr(plan, 'resume_from_step_id', None))
            if getattr(plan, 'resume_from_step_id', None) else None
        )
        checkpoint_consumed = resume_from_step_id is not None
        if checkpoint_consumed:
            # 先保存到局部变量，再清除持久化断点。认领失败不会走到这里，
            # 因而 checkpoint 始终可供下一次执行使用。
            try:
                if not state.database.clear_plan_checkpoint(str(plan.plan_id)):
                    logger.warning("清除恢复断点未命中（计划可能已不存在），已忽略: {}", plan.plan_id)
            except Exception as clear_exc:
                logger.exception("清除恢复断点失败（忽略，不影响本次恢复）: {}", clear_exc)

        print(f"[BG] plan={plan.plan_id} status=Running", flush=True)
        await broadcast_log(f"{'恢复' if (resume or checkpoint_consumed) else '开始'}执行计划: {plan.plan_id}", "info")

        # P0 用量记账：为本次执行绑定 plan 上下文，执行期内所有 LLM 调用自动归账
        from utils import usage_tracker as _usage_tracker
        _usage_tracker.set_context(plan_id=str(plan.plan_id), phase="executor")

        # Worktree 隔离：计划绑定 active worktree 时，把本执行上下文的工作区根
        # 重定向到 worktree（contextvar 随 asyncio 任务传播，to_thread 工具线程
        # 内同样生效；多计划并行时各执行上下文互不影响，无需进程级 env 切换）。
        _bound_worktree = state.database.get_active_worktree(str(plan.plan_id))
        _workspace_override_token = None
        if _bound_worktree:
            from tools.workspace import set_workspace_override
            _workspace_override_token = set_workspace_override(_bound_worktree["worktree_path"])
            await broadcast_log(
                f"[WORKTREE] 计划绑定隔离 worktree: {_bound_worktree['worktree_path']}（分支 {_bound_worktree['branch']}）",
                "info",
            )

        current_step_id = None
        while True:
            current_status = state.database.get_plan_status(str(plan.plan_id))
            if current_status == "Paused":
                debug_log(f"Plan {plan.plan_id} is Paused, stopping execution loop")
                await broadcast_log(f"计划 {plan.plan_id} 已暂停", "info")
                break

            # Run 心跳：旧 owner/token 失效时仅记录并让本轮最终收尾 fencing 失败，
            # 不篡改计划状态机；下一个服务实例可安全接管。
            if run_id and owner_token:
                try:
                    if not state.database.heartbeat_execution_run(run_id, owner_token):
                        debug_log(f"Run heartbeat fenced or missing: {run_id}")
                except Exception as heartbeat_exc:
                    debug_log(f"Run heartbeat failed: {heartbeat_exc}")

            # P0 预算闸门（超限 → Paused，可 /resume 恢复继续）
            _budget_limit = getattr(state, "budget_max_tokens_per_plan", 0) or 0
            if _budget_limit > 0:
                _used_tokens = _usage_tracker.plan_total_tokens(str(plan.plan_id))
                if _used_tokens >= _budget_limit:
                    _budget_msg = (
                        f"[BUDGET] 计划 {plan.plan_id} token 预算超限"
                        f"（已用 {_used_tokens} ≥ 上限 {_budget_limit}），自动暂停；"
                        f"提高 config budget.max_tokens_per_plan 后可 /resume 继续"
                    )
                    logger.warning(_budget_msg)
                    await broadcast_log(_budget_msg, "warning")
                    _require_write(
                        state.database.update_plan_state_machine(str(plan.plan_id), "Paused"),
                        f"计划 {plan.plan_id} 状态 → Paused（token 预算超限）",
                    )
                    await broadcast_plan_update(str(plan.plan_id))
                    break

            # 依赖感知取批：返回本批可执行步骤（依赖就绪 + 两两资源不冲突），
            # 或说明为何没有可执行步骤（依赖失败 / 存在无法就绪的 pending 步骤）。
            selection = state.database.select_next_batch(
                str(plan.plan_id),
                branch_version=branch_version,
                resume_from_step_id=resume_from_step_id,
                max_concurrency=getattr(state, "executor_max_concurrency", 1),
                parallel_graphs=getattr(state, "parallel_graphs", False),
            )
            batch = selection["steps"]
            if not batch:
                blocked_reason = selection.get("blocked_reason")
                blocked_step_ids = selection.get("blocked_step_ids") or []
                if blocked_reason or blocked_step_ids:
                    # 仍有 pending 步骤但依赖无法满足：绝不能报 Completed。
                    anchor_step_id = blocked_step_ids[0] if blocked_step_ids else current_step_id
                    reason = blocked_reason or f"依赖未就绪的步骤: {blocked_step_ids[:5]}"
                    _require_write(
                        state.database.update_plan_state_machine(
                            str(plan.plan_id), "Failed",
                            resume_from_step_id=anchor_step_id,
                            current_branch=branch_version,
                        ),
                        f"计划 {plan.plan_id} 状态 → Failed（依赖阻塞）",
                    )
                    await broadcast_plan_update(str(plan.plan_id))
                    await broadcast_log(
                        f"计划 {plan.plan_id} 依赖阻塞，无法继续: {reason}", "error"
                    )
                    break
                active_failed = state.database.get_active_failed_steps(
                    str(plan.plan_id), branch_version
                )
                if active_failed:
                    failed_step_id = str(active_failed[0]["step_id"])
                    _require_write(
                        state.database.update_plan_state_machine(
                            str(plan.plan_id), "Failed",
                            resume_from_step_id=failed_step_id,
                            current_branch=branch_version,
                        ),
                        f"计划 {plan.plan_id} 保持 Failed（存在未处理失败步骤）",
                    )
                    await broadcast_plan_update(str(plan.plan_id))
                    await broadcast_log(
                        f"计划 {plan.plan_id} 仍有失败步骤，未误报完成", "error"
                    )
                    break
                _require_write(
                    state.database.update_plan_state_machine(str(plan.plan_id), "Completed"),
                    f"计划 {plan.plan_id} 状态 → Completed",
                )
                await broadcast_plan_update(str(plan.plan_id))
                await broadcast_log(f"计划执行完成: {plan.plan_id}", "success")
                debug_log(f"Plan {plan.plan_id} execution COMPLETED")
                await _generate_final_report(plan, run_id)
                break

            # 本轮一旦取出断点，后续步骤按普通 pending 语义继续；失败时会重新保存
            # 当前 step_id 作为下一次 /execute 的恢复目标。
            resume_from_step_id = None
            skipped_ids = {s["step_id"] for s in state.database.get_steps(str(plan.plan_id)) if s.get("status") == "Skipped"}

            # 批内并发执行（有界并发）：每个步骤独立走完整状态机 + 重试 + 事件。
            # 任何一步返回 "stop"（拦截/校验失败/重试耗尽/中止）即终止执行，
            # 不启动下一批，保持「失败即停」与 checkpoint/回滚语义。
            async def _dispatch_one(step_row):
                return await _execute_step_to_completion(
                    plan, step_row, run_id, owner_token, branch_version,
                    resume=bool(resume), skipped_ids=skipped_ids, debug_log=debug_log,
                )

            stop_step_id = None
            if len(batch) == 1:
                outcome, step_id = await _dispatch_one(batch[0])
                current_step_id = step_id
                if outcome == "stop":
                    stop_step_id = step_id
            else:
                max_conc = max(1, int(getattr(state, "executor_max_concurrency", 1)))
                sem = asyncio.Semaphore(max_conc)

                async def _bounded(step_row):
                    async with sem:
                        return await _dispatch_one(step_row)

                outcomes = await asyncio.gather(*(_bounded(s) for s in batch), return_exceptions=True)
                for idx, outcome in enumerate(outcomes):
                    if isinstance(outcome, Exception):
                        logger.exception("批内步骤异常: {}", outcome)
                        stop_step_id = str(batch[idx].get("step_id"))
                        break
                    code, step_id = outcome
                    current_step_id = step_id
                    if code == "stop":
                        stop_step_id = step_id
                        break
            if stop_step_id:
                await broadcast_log(f"[ERROR] 批内步骤 {stop_step_id} 失败，终止执行", "error")
                break

        # 最后一个步骤可能在本轮循环末尾完成，下一轮通常会完成状态收敛；
        # 这里再做一次幂等收敛，避免收尾阶段把成功运行误判为 Failed。
        if state.database.get_plan_status(str(plan.plan_id)) == PlanStatus.RUNNING.value:
            active_steps = state.database.get_active_steps_snapshot(str(plan.plan_id), branch_version)
            if active_steps and all(
                step_row.get("status") in {StepStatus.SUCCESS.value, StepStatus.SKIPPED.value}
                for step_row in active_steps
            ):
                _require_write(
                    state.database.update_plan_state_machine(str(plan.plan_id), PlanStatus.COMPLETED.value),
                    f"计划 {plan.plan_id} 状态 → Completed",
                )
                await broadcast_plan_update(str(plan.plan_id))
                await broadcast_log(f"计划执行完成: {plan.plan_id}", "success")
                await _generate_final_report(plan, run_id)

    except Exception as e:
        debug_log(f"EXCEPTION in execute_plan_background: {e}")
        import traceback
        debug_log(f"Traceback: {traceback.format_exc()}")
        try:
            state.database.update_plan_state_machine(
                str(plan.plan_id), "Failed",
                resume_from_step_id=current_step_id,
                current_branch=getattr(plan, "current_branch", 1) or 1,
            )
            await broadcast_plan_update(str(plan.plan_id))
        except Exception as inner:
            debug_log(f"update_plan failed during exception handling: {inner}")
        await broadcast_log(f"执行失败: {str(e)}", "error")
    finally:
        # Worktree 隔离：恢复本执行上下文的工作区覆盖（无论成功、失败还是异常）。
        if _workspace_override_token is not None:
            try:
                from tools.workspace import reset_workspace_override
                reset_workspace_override(_workspace_override_token)
            except Exception as _ws_restore_err:
                debug_log(f"恢复工作区覆盖失败（忽略）: {_ws_restore_err}")
        # 项目工作记忆：计划结束时把执行状态写入工作区，让后续任务"接着干"
        try:
            from tools.project_memory import save_entry as _pm_save
            _steps_rows = state.database.get_steps(str(plan.plan_id)) or []
            _plan_final = state.database.get_plan_status(str(plan.plan_id)) or "未知"
            _mem_lines = [f"计划 {str(plan.plan_id)[:8]} 执行结束（最终状态: {_plan_final}）"]
            for _s in _steps_rows:
                _line = f"- 步骤{_s.get('step_order')} [{_s.get('tool')}] {_s.get('status')}"
                if _s.get('failure_reason'):
                    _line += f"（原因: {_s.get('failure_reason')}）"
                if _s.get('description'):
                    _line += f" —— {_s.get('description')}"
                _mem_lines.append(_line)
            _pm_save("执行摘要", chr(10).join(_mem_lines))
            debug_log(f"项目记忆已更新: {plan.plan_id}")
        except Exception as _mem_err:
            debug_log(f"项目记忆写入失败: {_mem_err}")
        try:
            from utils import usage_tracker as _usage_tracker_cleanup
            _usage_tracker_cleanup.clear_context()
        except Exception:
            pass
        async with state.execution_lock:
            # 绑定 worktree 的计划不占用主工作区执行权，也不得清理它——
            # 并行运行的主工作区计划可能正由其他任务持有。
            if not _bound_worktree:
                state.current_execution = None
                if state.current_execution_owner and state.current_execution_owner.get("plan_id") == str(plan.plan_id):
                    state.current_execution_owner = None
        # P0-a 安全网：计划已终止（完成/失败/异常），批量拒绝其未决审批，
        # 防止残留的挂起请求泄漏或被后续计划误 resolve。
        try:
            state.approval_manager.reject_all_for_plan(str(plan.plan_id), "plan execution ended")
            state.authorization_manager.revoke_all_for_plan(str(plan.plan_id))
        except Exception as runtime_cleanup_exc:
            debug_log(f"runtime grant cleanup failed: {runtime_cleanup_exc}")
        # 最终收敛安全网：若所有活动步骤均已成功/跳过，即使执行循环曾因收尾异常
        # 保留了 Failed，也不能让已完成的计划继续以失败结束；只在无失败步骤时修正。
        try:
            _cleanup_status = state.database.get_plan_status(str(plan.plan_id))
            if _cleanup_status in {PlanStatus.RUNNING.value, PlanStatus.FAILED.value}:
                _cleanup_branch = getattr(plan, "current_branch", 1) or 1
                _cleanup_steps = state.database.get_active_steps_snapshot(str(plan.plan_id), _cleanup_branch)
                if _cleanup_steps and all(
                    row.get("status") in {StepStatus.SUCCESS.value, StepStatus.SKIPPED.value}
                    for row in _cleanup_steps
                ):
                    state.database.update_plan_state_machine(
                        str(plan.plan_id), PlanStatus.COMPLETED.value,
                    )
                    state.database.clear_plan_checkpoint(str(plan.plan_id))
                    await broadcast_plan_update(str(plan.plan_id))
        except Exception as _status_cleanup_exc:
            debug_log(f"final plan status convergence failed: {_status_cleanup_exc}")
        # Run 收尾：按计划最终状态映射 Run 终态，owner_token fencing 保证只本任务可收尾。
        _run_terminal = {
            PlanStatus.COMPLETED.value: RunStatus.COMPLETED.value,
            PlanStatus.FAILED.value: RunStatus.FAILED.value,
            PlanStatus.PAUSED.value: RunStatus.PAUSED.value,
        }
        if run_id and owner_token:
            try:
                _final_status = state.database.get_plan_status(str(plan.plan_id))
                _run_status = _run_terminal.get(_final_status, RunStatus.FAILED.value)
                if not state.database.finish_execution_run(
                    run_id, owner_token, _run_status,
                    failure_reason="interrupted by service shutdown" if _run_status == RunStatus.FAILED.value else None,
                ):
                    debug_log(f"finish_execution_run fenced or missing: run={run_id}")
            except Exception as run_close_exc:
                debug_log(f"finish_execution_run failed: {run_close_exc}")
        # 收尾前未结束的 Running Attempt 一并补齐（如异常路径残留）。
        if attempt_id:
            try:
                state.database.finish_execution_attempt(
                    attempt_id, AttemptStatus.INTERRUPTED.value,
                    failure_reason="run ended before attempt completed",
                    run_id=run_id, owner_token=owner_token,
                )
            except Exception:
                pass
        debug_log(f"execute_plan_background FINISHED for plan {plan.plan_id}")



# ===== 兼容 re-export（测试与外部调用方经 server 命名空间访问路由函数）=====
from api.routes.plan_routes import (  # noqa: E402,F401
    get_plans, get_plan, get_plan_runs, get_run, get_run_attempts,
    execute_plan, pause_plan, resume_plan, skip_step,
    archive_plan, rename_plan, delete_plan, rollback_plan,
)
from api.routes.design_routes import (  # noqa: E402,F401
    create_design, get_designs, get_design, approve_design, delete_design,
)
from api.routes.approval_routes import (  # noqa: E402,F401
    list_pending_approvals, approve_tool_call, reject_tool_call,
)
from api.routes.worktree_routes import (  # noqa: E402,F401
    create_plan_worktree, get_plan_worktree, merge_plan_worktree, discard_plan_worktree,
)
from api.routes.quick_task_routes import quick_task  # noqa: E402,F401
from api.routes.replan_routes import replan_plan  # noqa: E402,F401
from api.routes.artifact_routes import (  # noqa: E402,F401
    list_artifacts, get_artifact, get_artifact_content, create_artifact,
)
from api.routes.agent_session_routes import (  # noqa: E402,F401
    list_agent_sessions, get_agent_session_turns,
)
from api.routes.misc_routes import (  # noqa: E402,F401
    root, get_status, get_tools, api_chat, api_chat_sessions, api_skills,
    notify_test, get_capabilities, get_config, update_config,
    get_audit_logs, get_usage, restart_service, websocket_logs,
)


def start_server(host: str = "127.0.0.1", port: int = 8000):
    """启动服务器"""
    import uvicorn
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    start_server()
