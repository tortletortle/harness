"""API 层共享状态与助手（从 server.py 拆出，行为不变）。

- AppState / state：全局装配点（lifespan 填充、路由与运行时读取）。
- 广播助手：WebSocket 日志 / 计划状态 / 审批事件推送。
- 格式化与配置加载助手。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import WebSocket

from database import Database, DatabaseError
from llm_client import LLMClient
from designer import DefaultDesigner
from executor import DefaultExecutor
from tools import ToolRegistry
from capabilities import CapabilityRegistry
from api.approval import ApprovalManager
from api.auth import load_token, require_token, verify_token
from api import secret_store
from authorization import AuthorizationManager
from chat import ChatManager
from skills_loader import SkillManager
from notify import Notifier
from models import StepStatus
from logging_config import get_logger

logger = get_logger(__name__)


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
        # 进程内任务占用覆盖排队/执行/收尾，独立于数据库 Run 状态。
        self.execution_reservations: Dict[str, str] = {}
        self.execution_tasks: set[asyncio.Task] = set()
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
        env_key = os.getenv(secret_store.env_key_name(provider_name))
        env_url = os.getenv(f"EAS_LLM_BASE_URL_{provider_name.upper()}")
        api_key = env_key or stored_keys.get(provider_name, "")
        if api_key:
            api_keys[provider_name] = api_key
        if env_url:
            base_urls[provider_name] = env_url
    return config


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

    user_approval = (
        design.user_approval if hasattr(design, 'user_approval') else design.get('user_approval')
    )
    return {
        "design_id": str(design.design_id) if hasattr(design, 'design_id') else str(design.get('design_id', '')),
        "user_request": design.user_request if hasattr(design, 'user_request') else design.get('user_request', ''),
        "created_at": design.timestamp.isoformat() if hasattr(design, 'timestamp') else design.get('timestamp', ''),
        "user_approval": bool(user_approval) if user_approval is not None else False,
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
                "depends_on": [str(dep) for dep in (step.get('depends_on') or [])],
                "branch_version": step.get('branch_version') or 1,
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
        "current_branch": plan_data.get('current_branch') or 1,
        "resume_from_step_id": (
            str(plan_data['resume_from_step_id']) if plan_data.get('resume_from_step_id') else None
        ),
        "segments": segments,
        "steps": all_steps,  # Flattened list for easy access
        "created_at": str(plan_data.get('created_at', '')),
    }


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
