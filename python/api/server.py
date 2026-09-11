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

# ===== 全局状态 =====

class AppState:
    """应用状态"""
    def __init__(self):
        self.llm_client: Optional[LLMClient] = None
        self.designer: Optional[DefaultDesigner] = None
        self.executor: Optional[DefaultExecutor] = None
        self.tool_registry: Optional[ToolRegistry] = None
        self.capability_registry: Optional[CapabilityRegistry] = None
        self.dispatcher = None  # Dispatcher实例
        self.database: Optional[Database] = None
        self.config: Dict[str, Any] = {}
        self.websocket_clients: List[WebSocket] = []
        self.current_execution: Optional[str] = None  # 兼容：当前执行 plan_id
        self.current_execution_owner: Optional[Dict[str, str]] = None  # plan_id/run_id/owner_token
        # 保护 current_execution 的 check-then-set，避免并发请求重复启动执行
        self.execution_lock: asyncio.Lock = asyncio.Lock()
        # 重试策略（阶段3.1）：从 config 顶层 retry 字段读取，lifespan 中赋值
        self.retry_max_attempts: int = 3
        self.retry_backoff_factor: float = 2
        # 工具级审批（P0-a）：高风险工具执行前挂起等待人工批准
        self.approval_manager: ApprovalManager = ApprovalManager()
        # Runtime 临时 Grant（授权一次步骤调用，执行后撤销）
        self.authorization_manager: AuthorizationManager = AuthorizationManager()
        # plan_update 广播自增版本号（P1-b）：前端丢弃过期推送，防止乱序覆盖
        self.plan_update_version: int = 0
        # 应用内置对话助手（chat 会话层）：lifespan 中初始化
        self.chat_manager: Optional[ChatManager] = None
        # 技能包管理器（生态层）：lifespan 中加载并注册
        self.skill_manager: Optional[SkillManager] = None
        # IM 通知器（入口层）：计划终态/暂停、待审批、设计书就绪时主动推送
        self.notifier: Optional[Notifier] = None

state = AppState()

def _require_write(ok: bool, what: str) -> None:
    """把数据库写入未命中转换为可观测执行错误。"""
    if not ok:
        raise DatabaseError(f"{what}失败")


# ===== 配置加载 =====

def _deep_merge_dict(base: dict, override: dict) -> dict:
    """递归合并：override 覆盖 base，嵌套 dict 深合并，其余类型直接覆盖。"""
    merged = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_dict(merged[k], v)
        else:
            merged[k] = v
    return merged


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

    # IM 通知（入口层）：计划到达终态/暂停时主动推送（fire-and-forget，fail-open）。
    if state.notifier is not None and summary.get("status") in ("Completed", "Failed", "Paused"):
        _st = str(summary["status"])
        _steps = summary.get("steps") or []
        _total = len(_steps)
        _done = sum(1 for s in _steps if s.get("status") == "Success")
        _failed = sum(1 for s in _steps if s.get("status") == "Failed")
        _evmap = {"Completed": "plan_completed", "Failed": "plan_failed", "Paused": "plan_paused"}
        asyncio.create_task(
            state.notifier.send(
                _evmap[_st],
                f"计划 {_st}：{str(plan_id)[:8]}",
                f"计划 {plan_id} 状态变为 {_st}。\n"
                f"步骤进度：{_done} 成功 / {_failed} 失败 / 共 {_total} 步。",
                payload={"plan_id": str(plan_id), "status": _st},
                dedup_key=f"{plan_id}:{_st}",
            )
        )
    # P1-b 版本围栏：每条广播自增 version，前端只应用更大版本的事件，
    # 防止慢速广播用旧状态覆盖新状态（参考 Cline stateVersion 语义）。
    state.plan_update_version += 1
    payload = {
        "type": "plan_update",
        "plan_id": str(plan_id),
        "status": summary["status"],
        "steps": summary["steps"],
        "version": state.plan_update_version,
    }
    await _send_to_clients(payload)


async def broadcast_approval_request(approval: "object") -> None:
    """广播待审批请求（type=approval_request），前端弹审批卡片。"""
    await _send_to_clients({
        "type": "approval_request",
        **approval.to_dict(),
    })


async def broadcast_approval_resolved(approval_id: str, approved: bool) -> None:
    """广播审批决定（type=approval_resolved），前端关闭对应审批卡片。"""
    await _send_to_clients({
        "type": "approval_resolved",
        "approval_id": approval_id,
        "approved": approved,
    })

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
                "status": p.get("status", ""),
                "display_name": p.get("display_name") or "",
                "archived": bool(p.get("archived", 0)),
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

@app.get("/api/plan/{plan_id}/runs", dependencies=[Depends(require_token)])
async def get_plan_runs(plan_id: str, limit: int = 100):
    """获取计划执行 Run 历史；不向客户端暴露 owner_token。"""
    if not state.database.get_plan_status(plan_id):
        raise HTTPException(status_code=404, detail="执行计划不存在")
    runs = state.database.list_execution_runs(plan_id, limit=limit)
    for run in runs:
        run.pop("owner_token", None)
    return {"success": True, "runs": runs}


@app.get("/api/run/{run_id}", dependencies=[Depends(require_token)])
async def get_run(run_id: str):
    """获取单个执行 Run；不向客户端暴露 owner_token。"""
    run = state.database.get_execution_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="执行 Run 不存在")
    run.pop("owner_token", None)
    return {"success": True, "run": run}


@app.get("/api/run/{run_id}/attempts", dependencies=[Depends(require_token)])
async def get_run_attempts(run_id: str, limit: int = 500):
    """获取 Run 的 Attempt 历史；不返回任何 owner_token。"""
    if not state.database.get_execution_run(run_id):
        raise HTTPException(status_code=404, detail="执行 Run 不存在")
    attempts = state.database.list_execution_attempts(run_id, limit=limit)
    for attempt in attempts:
        attempt.pop("owner_token", None)
    return {"success": True, "attempts": attempts}


@app.get("/api/agent-sessions", dependencies=[Depends(require_token)])
async def list_agent_sessions(plan_id: Optional[str] = None, step_id: Optional[str] = None, limit: int = 100):
    """Agent 运行记录（Session 层）：每次 agent_tool 步骤执行一条。"""
    sessions = state.database.list_agent_sessions(plan_id=plan_id, step_id=step_id, limit=limit)
    return {"success": True, "sessions": sessions}


@app.get("/api/agent-sessions/{session_id}/turns", dependencies=[Depends(require_token)])
async def get_agent_session_turns(session_id: str):
    """Agent 运行记录（Turn 层）：一次 session 内逐轮模型调用与结果。"""
    sessions = state.database.list_agent_sessions(limit=1000)
    if not any(s["session_id"] == session_id for s in sessions):
        raise HTTPException(status_code=404, detail="agent session 不存在")
    return {"success": True, "turns": state.database.list_agent_turns(session_id)}


@app.get("/api/artifacts", dependencies=[Depends(require_token)])
async def list_artifacts(plan_id: Optional[str] = None, step_id: Optional[str] = None):
    """产物元数据列表（内容通过 /api/artifacts/{id}/content 拉取）。"""
    return {"success": True, "artifacts": state.database.list_artifacts(plan_id=plan_id, step_id=step_id)}


@app.get("/api/artifacts/{artifact_id}", dependencies=[Depends(require_token)])
async def get_artifact(artifact_id: str):
    artifact = state.database.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="产物不存在")
    return {"success": True, "artifact": artifact}


@app.get("/api/artifacts/{artifact_id}/content", dependencies=[Depends(require_token)])
async def get_artifact_content(artifact_id: str):
    """拉取产物内容（未入库的外部引用返回 404 与说明）。"""
    content = state.database.read_artifact_content(artifact_id)
    if content is None:
        artifact = state.database.get_artifact(artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="产物不存在")
        raise HTTPException(status_code=404, detail="产物内容未入库（仅登记了外部引用）")
    from fastapi.responses import Response
    return Response(content=content, media_type="application/octet-stream",
                    headers={"X-Artifact-Id": artifact_id})


@app.post("/api/artifacts", dependencies=[Depends(require_token)])
async def create_artifact(request: Dict[str, Any]):
    """登记产物：支持纯引用（uri）或直接携带内容入库（content 文本，SHA-256 落盘）。"""
    body = request or {}
    kind = str(body.get("kind") or "file")
    artifact_id = str(body.get("artifact_id") or uuid4())
    content = body.get("content")
    if content is not None:
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            raise HTTPException(status_code=400, detail="content 必须是文本")
        record = state.database.store_artifact_content(
            artifact_id, content_bytes, plan_id=body.get("plan_id"),
            step_id=body.get("step_id"), kind=kind, metadata=body.get("metadata"),
        )
        return {"success": True, "artifact": record}
    uri = str(body.get("uri") or "").strip()
    if not uri:
        raise HTTPException(status_code=400, detail="缺少 uri 或 content")
    state.database.create_artifact(
        artifact_id=artifact_id, kind=kind, uri=uri,
        plan_id=body.get("plan_id"), step_id=body.get("step_id"),
        content_hash=body.get("content_hash"), metadata=body.get("metadata"),
    )
    return {"success": True, "artifact": state.database.get_artifact(artifact_id)}



@app.post("/api/plan/{plan_id}/execute", dependencies=[Depends(require_token)])
async def execute_plan(plan_id: str):
    """执行计划"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")

        # check-then-set 必须在同一把锁内完成。
        # 否则两个并发请求可以同时通过检查，产出两个后台任务争抢同一份 DB 和文件系统。
        # 绑定 active worktree 的计划走隔离工作区，不占用主工作区执行权，
        # 可与其它计划并行（同计划重复执行由 DB 状态机原子认领兜底拒绝）。
        _worktree_bound = state.database.get_active_worktree(plan_id) is not None
        if not _worktree_bound:
            async with state.execution_lock:
                if state.current_execution:
                    # 认领残留自愈：进程重启可能发生在 claim 之后、执行任务死亡之前，
                    # 留下"current_execution 残留 + 计划非 Running"僵局（孤儿恢复不覆盖此态）。
                    # 只有被认领计划确实 Running 时才视为真冲突（409）；否则释放残留并放行。
                    claimed = state.current_execution
                    claimed_status = state.database.get_plan_status(claimed)
                    if claimed_status == "Running":
                        raise HTTPException(
                            status_code=409,
                            detail=f"已有任务在执行中: {claimed}",
                        )
                    logger.warning("释放残留执行认领: {}（计划 DB 状态: {}，非 Running）", claimed, claimed_status)
                    state.current_execution = None
                    state.current_execution_owner = None
                state.current_execution = plan_id

        # 在后台执行（run 由 execute_plan_background 内部原子认领并签发）
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

        # 绑定 active worktree 的计划走隔离工作区，不占用主工作区执行权。
        _worktree_bound = state.database.get_active_worktree(plan_id) is not None
        if not _worktree_bound:
            async with state.execution_lock:
                if state.current_execution:
                    # 认领残留自愈：进程重启可能发生在 claim 之后、执行任务死亡之前，
                    # 留下"current_execution 残留 + 计划非 Running"僵局（孤儿恢复不覆盖此态）。
                    # 只有被认领计划确实 Running 时才视为真冲突（409）；否则释放残留并放行。
                    claimed = state.current_execution
                    claimed_status = state.database.get_plan_status(claimed)
                    if claimed_status == "Running":
                        raise HTTPException(
                            status_code=409,
                            detail=f"已有任务在执行中: {claimed}",
                        )
                    logger.warning("释放残留执行认领: {}（计划 DB 状态: {}，非 Running）", claimed, claimed_status)
                    state.current_execution = None
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


# ===== P0-a 工具级审批端点 =====

class ApprovalDecisionRequest(BaseModel):
    """审批决定请求"""
    comment: Optional[str] = None


@app.get("/api/approvals", dependencies=[Depends(require_token)])
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


@app.post("/api/plan/{plan_id}/approval/{approval_id}/approve", dependencies=[Depends(require_token)])
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


@app.post("/api/plan/{plan_id}/approval/{approval_id}/reject", dependencies=[Depends(require_token)])
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



@app.post("/api/plan/{plan_id}/archive", dependencies=[Depends(require_token)])
async def archive_plan(plan_id: str, request: Optional[Dict[str, Any]] = None):
    """归档 / 取消归档计划（归档不删除数据，仅从默认列表隐藏）。"""
    try:
        body = request or {}
        archived = bool(body.get("archived", True))
        if not state.database.get_plan_status(plan_id):
            raise HTTPException(status_code=404, detail="执行计划不存在")
        if not state.database.set_plan_archived(plan_id, archived):
            raise HTTPException(status_code=500, detail="归档状态写入失败")
        return {"success": True, "plan_id": plan_id, "archived": archived}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("归档计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/plan/{plan_id}/rename", dependencies=[Depends(require_token)])
async def rename_plan(plan_id: str, request: Optional[Dict[str, Any]] = None):
    """重命名计划（可读备注名，用于列表展示替代 UUID）。"""
    try:
        body = request or {}
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="缺少名称")
        if len(name) > 100:
            raise HTTPException(status_code=400, detail="名称过长（不超过 100 字符）")
        if not state.database.get_plan_status(plan_id):
            raise HTTPException(status_code=404, detail="执行计划不存在")
        if not state.database.rename_plan(plan_id, name):
            raise HTTPException(status_code=500, detail="重命名写入失败")
        return {"success": True, "plan_id": plan_id, "name": name}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("重命名计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/plan/{plan_id}", dependencies=[Depends(require_token)])
async def delete_plan(plan_id: str):
    """删除执行计划（连同其全部步骤与事件；审计日志保留作为历史记录）。

    Running/Paused 状态的计划必须先暂停，不允许直接删除。
    """
    try:
        current_status = state.database.get_plan_status(plan_id)
        if not current_status:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        if current_status in ("Running", "Paused"):
            raise HTTPException(
                status_code=409,
                detail=f"计划正在执行（{current_status}），请先暂停后再删除",
            )
        if not state.database.delete_plan(plan_id):
            raise HTTPException(status_code=500, detail="删除计划失败")
        return {"success": True, "plan_id": plan_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("删除计划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

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


# ===== Worktree 隔离端点：创建绑定 / 查看 / 合并 / 丢弃 =====
# 合并与丢弃是用户显式发起的人工动作（人即审批方）；Running 状态一律拒绝。

@app.post("/api/plan/{plan_id}/worktree", dependencies=[Depends(require_token)])
async def create_plan_worktree(plan_id: str, request: Optional[Dict[str, Any]] = None):
    """为计划创建并绑定隔离 worktree（新分支 worktree/<name>）。"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        if state.database.get_active_worktree(plan_id):
            raise HTTPException(status_code=409, detail="该计划已绑定 active worktree")

        body = request or {}
        name = str(body.get("name") or f"plan-{str(plan_id)[:8]}")
        from tools.git_operator import GitOperator
        repo_path = str(state.authorization_manager.workspace_root)
        result = GitOperator.execute("worktree_add", {"path": repo_path, "name": name})
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error") or "worktree 创建失败")

        worktree_id = str(uuid4())
        state.database.create_worktree_record(
            worktree_id=worktree_id,
            plan_id=plan_id,
            repo_path=repo_path,
            worktree_path=result["worktree_path"],
            branch=result["branch"],
            base_commit=result.get("base_commit"),
        )
        await broadcast_log(
            f"[WORKTREE] 计划 {plan_id} 绑定隔离 worktree {result['worktree_path']}", "info"
        )
        return {"success": True, "worktree_id": worktree_id, **{
            k: result[k] for k in ("worktree_path", "branch", "base_commit") if k in result
        }}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("创建 worktree 失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/plan/{plan_id}/worktree", dependencies=[Depends(require_token)])
async def get_plan_worktree(plan_id: str):
    """查看计划的 worktree 绑定状态与相对主仓库的变更摘要。"""
    plan = state.database.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="执行计划不存在")
    binding = state.database.get_active_worktree(plan_id)
    if not binding:
        return {"success": True, "worktree": None}
    from tools.git_operator import GitOperator
    diff = GitOperator.execute("worktree_diff", {
        "path": binding["repo_path"], "worktree_path": binding["worktree_path"],
    })
    return {
        "success": True,
        "worktree": {k: binding[k] for k in (
            "worktree_id", "repo_path", "worktree_path", "branch", "base_commit", "created_at"
        )},
        "diff": {"stat": diff.get("stat"), "patch_truncated": diff.get("patch_truncated")} if diff.get("success") else {"error": diff.get("error")},
    }


@app.post("/api/plan/{plan_id}/worktree/merge", dependencies=[Depends(require_token)])
async def merge_plan_worktree(plan_id: str, request: Optional[Dict[str, Any]] = None):
    """把计划的 worktree 分支合并回主仓库（人工发起；冲突不自动处理）。"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        current_status = state.database.get_plan_status(plan_id)
        if current_status == "Running":
            raise HTTPException(status_code=409, detail="计划执行中，禁止合并 worktree")
        binding = state.database.get_active_worktree(plan_id)
        if not binding:
            raise HTTPException(status_code=404, detail="该计划没有 active worktree 绑定")

        body = request or {}
        from tools.git_operator import GitOperator
        result = GitOperator.execute("worktree_merge", {
            "path": binding["repo_path"],
            "worktree_path": binding["worktree_path"],
            "message": body.get("message"),
        })
        state.database.save_audit_log(
            tool_id="worktree_merge",
            instruction=f"merge worktree {binding['worktree_path']} -> {binding['repo_path']}",
            inputs={"plan_id": plan_id, "branch": binding["branch"]},
            plan_id=plan_id,
            success=bool(result.get("success")),
            blocked=bool(result.get("conflict")),
            block_reason=result.get("error") if not result.get("success") else None,
            actor="user",
        )
        if not result.get("success"):
            # 冲突/失败保持 active 绑定，由用户解决后重试或丢弃。
            return JSONResponse(status_code=409, content={
                "success": False,
                "conflict": bool(result.get("conflict")),
                "message": result.get("message"),
                "output": result.get("output"),
                "error": result.get("error"),
            })
        state.database.close_worktree(binding["worktree_id"], "merged")
        await broadcast_plan_update(plan_id)
        await broadcast_log(
            f"[WORKTREE] 计划 {plan_id} 的分支 {result.get('branch')} 已合并回主仓库", "success"
        )
        return {"success": True, "branch": result.get("branch"), "output": result.get("output")}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("合并 worktree 失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/plan/{plan_id}/worktree/discard", dependencies=[Depends(require_token)])
async def discard_plan_worktree(plan_id: str):
    """丢弃计划的 worktree（移除目录与分支绑定，标记 discarded）。"""
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        current_status = state.database.get_plan_status(plan_id)
        if current_status == "Running":
            raise HTTPException(status_code=409, detail="计划执行中，禁止丢弃 worktree")
        binding = state.database.get_active_worktree(plan_id)
        if not binding:
            raise HTTPException(status_code=404, detail="该计划没有 active worktree 绑定")

        from tools.git_operator import GitOperator
        result = GitOperator.execute("worktree_remove", {
            "path": binding["repo_path"], "worktree_path": binding["worktree_path"],
        })
        state.database.close_worktree(binding["worktree_id"], "discarded")
        await broadcast_log(
            f"[WORKTREE] 计划 {plan_id} 的 worktree 已丢弃: {binding['worktree_path']}", "warning"
        )
        return {"success": True, "removed": bool(result.get("success")), "detail": result.get("message")}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("丢弃 worktree 失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


# ===== 动态重规划：失败计划生成修复步骤并挂入图（Level-3 介入的落地形态）=====

@app.post("/api/plan/{plan_id}/replan", dependencies=[Depends(require_token)])
async def replan_plan(plan_id: str):
    """对 Failed 计划做局部重规划。

    语义（依赖驱动，不重排既有 order）：
    - 锚点 = resume_from_step_id 或第一个活动失败步骤 F；
    - 有前驱 P：回滚到 P（F 及之后的步骤克隆到新分支，Pending），
      修复步骤 R1..Rn 依赖链挂在 P 之后；F 的克隆依赖追加 Rn（先修复再重跑）；
    - 无前驱（F 即首步）：不回滚，F 置回 Pending，R 链挂在 F 之前（F 依赖追加 Rn）；
    - 修复步骤由 Designer 基于失败上下文生成，复用计划契约校验，fail-closed；
    - 旧线性计划在追加依赖后显式化克隆链顺序，避免隐式链失效导致乱序；
    - 成功后计划转 Pending（新分支），由用户重新 /execute。
    """
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        current_status = state.database.get_plan_status(plan_id)
        if current_status != "Failed":
            raise HTTPException(
                status_code=409,
                detail=f"只能对 Failed 计划做重规划，当前状态: {current_status}",
            )

        branch_version = getattr(plan, "current_branch", 1) or 1
        all_steps = state.database.get_steps(plan_id)
        active_rows = [
            r for r in all_steps
            if not r.get("is_obsolete") and int(r.get("branch_version") or 1) == branch_version
        ]
        failed_rows = [r for r in active_rows if r["status"] == "Failed"]
        anchor_id = (
            str(getattr(plan, "resume_from_step_id", None))
            if getattr(plan, "resume_from_step_id", None)
            else (str(failed_rows[0]["step_id"]) if failed_rows else None)
        )
        if not anchor_id:
            raise HTTPException(status_code=409, detail="计划中没有失败步骤可重规划")
        anchor = next(r for r in all_steps if str(r["step_id"]) == anchor_id)
        anchor_order = int(anchor["step_order"])

        same_branch = [
            r for r in active_rows
            if int(r.get("branch_version") or 1) == branch_version
        ]
        predecessors = [r for r in same_branch if int(r["step_order"]) < anchor_order]
        predecessor = max(predecessors, key=lambda r: int(r["step_order"])) if predecessors else None

        new_branch = branch_version + 1
        if predecessor is not None:
            target = str(predecessor["step_id"])
            if not state.database.clone_steps_from(plan_id, target, new_branch):
                raise HTTPException(status_code=500, detail="克隆失败步骤链失败")
            state.database.mark_steps_obsolete(plan_id, target, branch_version)
            clone_rows = [
                r for r in state.database.get_steps(plan_id)
                if int(r.get("branch_version") or 1) == new_branch and not r.get("is_obsolete")
            ]
            clone_rows.sort(key=lambda r: int(r["step_order"]))
            anchor_clone = next(
                (r for r in clone_rows if r.get("parent_step_id") == anchor_id), None
            )
        else:
            target = anchor_id
            clone_rows = []
            anchor_clone = anchor  # 无前驱：F 原地置回 Pending

        has_explicit_graph = any(
            str(r.get("depends_on") or "").strip() not in ("", "[]", "null")
            for r in same_branch
        )

        # Designer 生成修复步骤（契约校验 fail-closed）
        _design = state.database.get_design(str(plan.design_id))
        anchor_deps_raw = json.loads(anchor["depends_on"]) if anchor.get("depends_on") else []
        failed_inputs = anchor.get("inputs")
        if isinstance(failed_inputs, str):
            try:
                failed_inputs = json.loads(failed_inputs)
            except (ValueError, TypeError):
                failed_inputs = {}
        repair_steps = await state.designer.generate_repair_steps(
            failed_step={
                "description": anchor.get("description", ""),
                "tool": anchor.get("tool", ""),
                "inputs": failed_inputs or {},
            },
            failure_reason=anchor.get("failure_reason") or "未知原因",
            user_request=str(_design.user_request) if _design else "",
        )

        # ===== 在内存中构造最终依赖计划，再统一写库 =====
        # R 链：R1 挂在其前置（P 或就绪即跑）之后，Ri 依赖 R(i-1)；
        # F 重跑点依赖追加 Rn（先修复再重跑）；
        # 旧线性计划追加依赖后隐式链失效 → 克隆链显式化（按 order 顺序链接）。
        max_order = max((int(r["step_order"]) for r in all_steps), default=0)
        segment_id = str(anchor.get("segment_id") or uuid4())
        p_id = str(predecessor["step_id"]) if predecessor is not None else None
        repair_ids: List[str] = []
        prev_id = p_id
        repair_branch = new_branch if predecessor is not None else branch_version
        for i, rs in enumerate(repair_steps):
            sid = str(uuid4())
            deps = [prev_id] if prev_id else []
            ok = state.database.insert_repair_step(
                plan_id=plan_id,
                branch_version=repair_branch,
                segment_id=segment_id,
                step_id=sid,
                step_order=max_order + 1 + i,
                description=str(rs.get("description", "修复步骤")),
                tool=str(rs.get("tool", "manual_tool")),
                inputs=rs.get("inputs") or {},
                acceptance=rs.get("acceptance") or [],
                depends_on=deps,
            )
            if not ok:
                raise HTTPException(status_code=500, detail="修复步骤写入失败")
            repair_ids.append(sid)
            prev_id = sid
        last_repair_id = repair_ids[-1] if repair_ids else None
        f_deps = list(anchor_deps_raw) + ([last_repair_id] if last_repair_id else [])

        if predecessor is not None:
            anchor_clone_id = str(anchor_clone["step_id"]) if anchor_clone else None
            if not has_explicit_graph:
                # legacy 链显式化：F' = [P] + 原deps + [Rn]；其余克隆行按 order 链接
                chain_prev = None
                for row in clone_rows:
                    rid = str(row["step_id"])
                    if rid == anchor_clone_id:
                        deps = ([p_id] if p_id else []) + f_deps
                    else:
                        deps = [chain_prev] if chain_prev else ([p_id] if p_id else [])
                    state.database.update_step_dependencies(rid, deps)
                    chain_prev = rid
            else:
                # 显式图：F' 追加 Rn；其余克隆行保留原依赖（父映射解析）
                if anchor_clone_id:
                    state.database.update_step_dependencies(anchor_clone_id, f_deps)
        else:
            # 无前驱（F 即首步）：F 原地置回 Pending，依赖追加 Rn；
            # legacy 隐式链显式化：F 之后的同分支未完成步骤按 order 链接。
            state.database.update_step_dependencies(anchor_id, f_deps)
            state.database.update_step(anchor_id, "Pending")
            if not has_explicit_graph:
                followers = [
                    r for r in same_branch
                    if int(r["step_order"]) > anchor_order
                    and r["status"] in (StepStatus.PENDING.value, StepStatus.RETRY.value)
                ]
                followers.sort(key=lambda r: int(r["step_order"]))
                chain_prev = anchor_id
                for row in followers:
                    state.database.update_step_dependencies(str(row["step_id"]), [chain_prev])
                    chain_prev = str(row["step_id"])

        state.database.clear_plan_checkpoint(plan_id)
        _require_write(
            state.database.update_plan_state_machine(
                plan_id, PlanStatus.PENDING.value, current_branch=new_branch,
            ),
            f"计划 {plan_id} 状态 → Pending（重规划，新分支 {new_branch}）",
        )
        state.database.add_step_event(
            anchor_id, plan_id, "replanned",
            {"repair_steps": len(repair_steps), "new_branch": new_branch,
             "anchor": anchor_id, "strategy": "repair_before_retry"},
            branch_version=new_branch,
        )
        await broadcast_plan_update(plan_id)
        await broadcast_log(
            f"[REPLAN] 计划 {plan_id} 重规划完成：插入 {len(repair_steps)} 个修复步骤（分支 {new_branch}），"
            "可重新执行", "warning",
        )
        return {
            "success": True,
            "new_branch": new_branch,
            "repair_steps": len(repair_steps),
            "anchor": anchor_id,
        }
    except HTTPException:
        raise
    except GenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("重规划失败: {}", e)
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


@app.get("/api/usage", dependencies=[Depends(require_token)])
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

@app.post("/api/chat", dependencies=[Depends(require_token)])
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


@app.get("/api/chat/sessions", dependencies=[Depends(require_token)])
async def api_chat_sessions(_t: str = Depends(require_token)):
    return state.chat_manager.stats()


@app.get("/api/skills", dependencies=[Depends(require_token)])
async def api_skills(_t: str = Depends(require_token)):
    """技能包清单（生态层）：入口技能 + 知识包 + 加载失败记录。"""
    if state.skill_manager is None:
        return {"success": True, "skills": [], "skipped": []}
    return {"success": True, **state.skill_manager.describe_all()}


@app.post("/api/notify/test", dependencies=[Depends(require_token)])
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


@app.get("/api/capabilities", dependencies=[Depends(require_token)])
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

@app.delete("/api/design/{design_id}", dependencies=[Depends(require_token)])
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
