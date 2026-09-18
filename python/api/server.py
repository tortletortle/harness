"""
FastAPI 服务器
提供 RESTful API 和 WebSocket 实时日志
"""

import asyncio
import inspect
import json
import os

from version import __version__ as EAS_VERSION
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
from api import secret_store
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

def _load_models_config(config_root: Path | None = None) -> dict:
    """加载提交的模型模板与本地覆盖，并从环境变量或加密存储注入凭据。"""
    config_root = config_root or (Path(__file__).resolve().parents[2] / "config")
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

    # Credentials come from environment variables, falling back to the
    # encrypted account-local store. Template/local YAML values are ignored.
    config["api_keys"] = {}
    api_keys = config["api_keys"]
    base_urls = config.setdefault("base_urls", {})
    keys_dir = config_root if (config_root / "models.yaml").exists() else Path.cwd() / "config"
    try:
        stored_keys = secret_store.load_api_keys(keys_dir)
    except secret_store.SecretStoreError:
        stored_keys = {}
    for provider_name in list(base_urls):
        env_key = os.getenv(f"EAS_LLM_API_KEY_{provider_name.upper()}")
        env_url = os.getenv(f"EAS_LLM_BASE_URL_{provider_name.upper()}")
        api_key = env_key or stored_keys.get(provider_name, "")
        if api_key:
            api_keys[provider_name] = api_key
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
                agent_id="host_review_agent" if state.capability_registry.get("review_agent") else "review_agent",
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

app = FastAPI(title="EAS 2.0 API", version=EAS_VERSION, lifespan=lifespan)

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


# ===== Correlation ID（P2 可观测性）=====
# 每个请求绑定一个 correlation id：优先透传调用方的 X-Correlation-ID
# （长度限制防止日志注入），否则生成新值；响应头回传便于跨端关联。
from correlation import get_correlation_id, new_correlation_id, set_correlation_id  # noqa: E402


@app.middleware("http")
async def correlation_id_middleware(request, call_next):
    incoming = request.headers.get("X-Correlation-ID", "").strip()
    cid = incoming[:64] if incoming and incoming.isprintable() else new_correlation_id()
    set_correlation_id(cid)
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = cid
    return response

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

# ===== 执行运行时已拆分到 python/api/runtime/ =====
# - 单步状态机 + 重试 / attempt / 事件 / 状态写入 -> python/api/runtime/step_execution.py
# - 产物登记 + 最终交付报告  -> python/api/runtime/artifacts.py
# - 后台总循环（认领 / 暂停 / 恢复 / 回滚 / 收尾）-> python/api/runtime/background.py
# 通过 api.runtime import 的符号仍可在 api.server 命名空间访问（见下方 re-export），
# 行为不变；测试与外部调用方 import 路径保持兼容。


# ===== 兼容 re-export（执行运行时；测试与外部调用方经 server 命名空间访问 runtime 符号）=====
from api.runtime import (  # noqa: E402,F401
    execute_plan_background,
    _execute_step_with_state_machine,
    _execute_step_to_completion,
    _persist_step_status,
    _is_retryable_exit_code_acceptance,
    _compress_step_context,
    _register_step_artifacts,
    _generate_final_report,
)

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
    notify_test, get_capabilities, get_config, update_config, set_config_key,
    get_audit_logs, get_usage, restart_service, websocket_logs,
)


def start_server(host: str = "127.0.0.1", port: int = 8000):
    """启动服务器"""
    import uvicorn
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    start_server()
