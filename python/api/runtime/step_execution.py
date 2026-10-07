"""步骤执行体（自 server.py 拆分；行为不变）。

本模块包含：
- 单步状态机执行器 _execute_step_with_state_machine
- 含重试 / attempt / 事件 / 状态写入的完整执行器 _execute_step_to_completion
- 步骤状态持久化辅助 _persist_step_status
- 命令退出码判定 _is_retryable_exit_code_acceptance
- Turn 级上下文压缩 _compress_step_context

所有符号仍可从 api.server 导入（见 server.py 末尾的 re-export）。
"""
from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime
from utils.utcnow import utcnow

from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from llm_client import Message

from api.approval import ApprovalTimeout
from api.shared import (
    broadcast_approval_request,
    broadcast_approval_resolved,
    broadcast_log,
    broadcast_plan_update,
    state,
    _require_write,
)
from api.runtime.artifacts import _register_step_artifacts
from authorization import agent_step_grant_params
from retry_policy import automatic_retry_policy, annotate_retry_failure
from executor.acceptance_feedback import build_acceptance_feedback as _build_acceptance_feedback
from domain.work_graph import normalize_dependencies
from models import AttemptStatus, PlanStatus, Step, StepStatus

from logging_config import get_logger

logger = get_logger(__name__)


def step_tool_id(raw_tool: Any) -> str:
    """计划步骤 tool 字段 → 主工具 id（去掉 instruction 后缀）。"""
    return str(raw_tool or "").split(":")[0].strip()


def tool_is_resolvable(
    tool_id: str,
    *,
    tool_registry: Any = None,
    capability_registry: Any = None,
) -> bool:
    """执行前：工具必须在 ToolRegistry 或 CapabilityRegistry 中（工具优先 + fail-closed）。"""
    tid = str(tool_id or "").strip()
    if not tid:
        return False
    if tool_registry is not None:
        tools = getattr(tool_registry, "tools", None) or {}
        aliases = getattr(tool_registry, "aliases", None) or {}
        if tid in tools or tid in aliases:
            return True
        resolve = getattr(tool_registry, "resolve_alias", None)
        if callable(resolve):
            try:
                if resolve(tid) in tools:
                    return True
            except Exception:
                pass
    if capability_registry is not None:
        try:
            if capability_registry.get(tid) is not None:
                return True
        except Exception:
            pass
    return False


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


# 交接上下文的体积闸门：下游 prompt 只需要上游的结论与引用，不需要全量输出。
HANDOFF_MAX_OUTPUT_CHARS = 2000
HANDOFF_MAX_ENTRIES = 8


def _handoff_json_object(value: Any) -> Dict[str, Any]:
    """宽松解析步骤行里的 JSON 列（str/dict 都接受），非对象视为空。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _handoff_text(value: Any) -> Optional[str]:
    """把上游产出压成有界文本，避免整段输出灌进下游 prompt。"""
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = text.strip()
    if not text or text in ("{}", "[]", "null", "None"):
        return None
    if len(text) <= HANDOFF_MAX_OUTPUT_CHARS:
        return text
    return text[:HANDOFF_MAX_OUTPUT_CHARS] + "…(截断)"


def _agent_capability_for(tool_value: Any):
    """tool → kind=agent_tool 的 Capability；普通工具/未注册返回 None。"""
    registry = getattr(state, "capability_registry", None)
    if registry is None:
        return None
    capability = registry.get(str(tool_value or "").split(":")[0])
    if capability is None:
        return None
    try:
        kind = capability.spec().kind
    except Exception:
        return None
    return capability if kind == "agent_tool" else None


def _handoff_nested_layers(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 state_snapshot 沿 output 键逐层展开，供 facts / artifacts 收集。

    真实运行时 state_snapshot 是 execute_step 的返回值：agent 结果被包了一层
    output（capability 结果 wrapper），wrapper 内又有一层 output（模型真实输出）；
    facts / artifacts 落在 wrapper 层，只查顶层会丢。逐层展开后从任一层的
    agent_facts/facts 与 agent_artifacts/artifacts 里收集，兼容真实链路与
    直接构造的测试形状。上限 4 层，与登记器的逐层展开同一思路。
    """
    layers: List[Dict[str, Any]] = []
    node: Any = snapshot
    for _ in range(4):
        if not isinstance(node, dict):
            break
        layers.append(node)
        node = node.get("output")
    return layers


def _handoff_list_layer(layers: List[Dict[str, Any]], keys: tuple) -> Optional[List[Any]]:
    """从展开的层里取第一个非空列表字段（agent_facts/facts、agent_artifacts/artifacts）。"""
    for layer in layers:
        for key in keys:
            bag = layer.get(key)
            if isinstance(bag, list) and bag:
                return bag
    return None


def build_handoff_context(step: Dict[str, Any], rows: List[Dict[str, Any]], branch_version: int) -> List[Dict[str, Any]]:
    """收集「本步骤直接依赖」在当前分支上的实际交接内容。

    数据源是已落库的上游结果（outputs / state_snapshot / compressed_context），
    不是计划期的 inputs 快照：上游到底产出了什么、留下哪些 facts 与产物引用，
    下游才看得到什么。上游未成功（非 Success/Skipped）时不交接，依赖阻塞由调度器
    负责。回滚克隆按 parent_step_id 别名解析，与 checkpoint_frontier 同一套血缘规则。
    """
    deps = normalize_dependencies(step.get("depends_on"))
    if not deps:
        return []
    effective: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row.get("is_obsolete") or int(row.get("branch_version") or 1) != int(branch_version):
            continue
        row_id = str(row.get("step_id"))
        if not row_id or row_id == "None":
            continue
        effective[row_id] = row
        parent = row.get("parent_step_id")
        if parent and str(parent) not in effective:
            effective[str(parent)] = row
    context: List[Dict[str, Any]] = []
    for dep in deps[:HANDOFF_MAX_ENTRIES]:
        row = effective.get(str(dep))
        if row is None or str(row.get("status")) not in ("Success", "Skipped"):
            continue
        outputs = _handoff_json_object(row.get("outputs"))
        snapshot = _handoff_json_object(row.get("state_snapshot"))
        inner = _handoff_json_object(snapshot.get("output"))
        # agent 结果被 executor 包了一层：真正的模型输出在 output.output。
        agent_output = inner.get("output") if isinstance(inner.get("output"), dict) else {}
        entry: Dict[str, Any] = {
            "step_id": str(row.get("step_id")),
            "tool": row.get("tool"),
            "description": str(row.get("description") or "")[:200],
            "status": str(row.get("status")),
        }
        layers = _handoff_nested_layers(snapshot)
        wrapper_out = outputs.get("output") if isinstance(outputs.get("output"), dict) else None
        summary = (
            _handoff_text(row.get("compressed_context"))
            or _handoff_text(outputs.get("value"))
            or _handoff_text(wrapper_out)
            or _handoff_text(outputs)
            or _handoff_text(agent_output)
            or _handoff_text(inner)
        )
        if summary:
            entry["summary"] = summary
        fact_bag = _handoff_list_layer(layers, ("agent_facts", "facts"))
        facts = [item for item in fact_bag if isinstance(item, dict)][:20] if fact_bag else []
        if facts:
            entry["facts"] = facts
        artifact_bag = _handoff_list_layer(layers, ("agent_artifacts", "artifacts"))
        refs = (
            [item.strip() for item in artifact_bag if isinstance(item, str) and item.strip()][:20]
            if artifact_bag else []
        )
        path = agent_output.get("path") or outputs.get("path")
        if isinstance(path, str) and path.strip():
            candidate = path.strip()
            if not candidate.startswith(("file://", "http://", "https://")):
                candidate = "file://" + candidate.replace("\\", "/")
            if candidate not in refs:
                refs.append(candidate)
        if refs:
            entry["artifacts"] = refs
        error = snapshot.get("error") or outputs.get("error")
        if error:
            entry["error"] = str(error)[:500]
        context.append(entry)
    return context





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

    inputs = json.loads(step["inputs"]) if isinstance(step.get("inputs"), str) else (step.get("inputs") or {})
    # ===== 智能体交接上下文 =====
    # 只给 kind=agent_tool 的步骤注入：普通工具的 inputs 有各自契约，不能顺手塞键。
    # 注入内容只来自当前分支上直接依赖步骤的落库结果（见 build_handoff_context）。
    if isinstance(inputs, dict) and _agent_capability_for(step.get("tool")) is not None:
        handoff_branch = int(step.get("branch_version", 1) or 1)
        handoff = build_handoff_context(
            step,
            state.database.get_active_steps_snapshot(plan_id, handoff_branch),
            handoff_branch,
        )
        if handoff:
            inputs = {**inputs, "upstream_context": handoff}

    step_model = Step(
        step_id=UUID(step["step_id"]),
        order=step.get("step_order", step.get("order", 0)),
        description=step.get("description", ""),
        tool=step.get("tool", ""),
        inputs=inputs,
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

    upstream_context = (step_model.inputs or {}).get("upstream_context")
    if isinstance(upstream_context, list) and upstream_context:
        # 交接本身要可审计：回放时能看出这一步实际收到了哪些上游步骤与几个产物引用。
        state.database.add_step_event(
            step_id, plan_id, "handoff_context",
            {
                "from_steps": [str(e.get("step_id")) for e in upstream_context if isinstance(e, dict)],
                "artifact_refs": sum(
                    len(e.get("artifacts") or []) for e in upstream_context if isinstance(e, dict)
                ),
            },
            branch_version=step_model.branch_version,
        )

    # ===== P0-a 工具级审批闸门 =====
    # 高风险工具在执行前挂起等待人工批准（Cline 式 asyncio.Event 挂起，非轮询）。
    # 拒绝/超时 → 返回结构化 blocked 结果，由调用方走 Failed 路径（重试无意义）。
    tool_id_for_approval = step_tool_id(step.get("tool"))
    # 未知工具：在审批/Grant 之前 fail-closed，避免对不存在的 id 等人批。
    if not tool_is_resolvable(
        tool_id_for_approval,
        tool_registry=getattr(state, "tool_registry", None),
        capability_registry=getattr(state, "capability_registry", None),
    ):
        logger.error("步骤 {} 工具未注册: {}", step_id, tool_id_for_approval)
        state.database.add_step_event(
            step_id, plan_id, "tool_unregistered",
            {"tool_id": tool_id_for_approval},
            branch_version=step_model.branch_version,
        )
        return {
            "success": False,
            "output": None,
            "check_status": "unverified",
            "check_passed": False,
            "error": f"工具未注册，无法执行: {tool_id_for_approval or '(空)'}",
            "blocked": True,
            "blocked_reason": "tool_unregistered",
            "retryable": False,
            "duration_ms": 0,
        }
    step_capability = (
        state.capability_registry.get(tool_id_for_approval)
        if state.capability_registry is not None
        else None
    )
    step_capability_kind = (
        getattr(step_capability.spec(), "kind", None) if step_capability is not None else None
    )
    desktop_scope = None
    desktop_approved = False
    desktop_auto = False
    desktop_worktree = state.database.get_active_worktree(plan_id) if tool_id_for_approval == "desktop_observe" else None
    if tool_id_for_approval == "desktop_observe":
        from api.desktop_approval import prepare_desktop_scope, desktop_auto_allowed
        try:
            desktop_scope = prepare_desktop_scope(step_model, desktop_worktree["worktree_path"] if desktop_worktree else state.authorization_manager.workspace_root)
        except Exception:
            return {"success": False, "blocked": True, "blocked_reason": "invalid_desktop_scope", "error": "Desktop scope unavailable; no window content read", "retryable": False, "check_status": "unverified", "duration_ms": 0}
        desktop_auto = desktop_auto_allowed()
        desktop_approved = desktop_auto
        if desktop_auto:
            state.database.add_step_event(step_id, plan_id, "desktop_auto_authorized",
                {"policy": "persistent_read_only_desktop", "operation": desktop_scope["operation"],
                 "window_id": (desktop_scope.get("identity") or {}).get("window_id")},
                branch_version=step_model.branch_version)
    if not desktop_auto and (tool_id_for_approval == "desktop_observe" or state.approval_manager.is_required_for_step(tool_id_for_approval, step_capability_kind)):
        approval = state.approval_manager.create(
            plan_id=plan_id,
            step_id=step_id,
            tool_id=tool_id_for_approval,
            instruction=str(step.get("description", "")),
            inputs=step_model.inputs,
            **({"desktop_scope": desktop_scope} if desktop_scope else {}),
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
            success=bool(decision and decision.approved),
            blocked=not bool(decision and decision.approved),
            block_reason=None if decision and decision.approved else "user_rejected",
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
        desktop_approved = bool(decision and decision.approved)
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
    if desktop_scope:
        from api.desktop_approval import issue_approved_desktop_grant
        try:
            grant = issue_approved_desktop_grant(state.authorization_manager, plan_id, step_model, desktop_scope,
                approved=desktop_approved, workspace_root=_active_wt["worktree_path"] if _active_wt else None)
        except Exception:
            return {"success": False, "blocked": True, "blocked_reason": "desktop_scope_changed", "error": "Desktop scope changed; a new approval is required", "retryable": False, "check_status": "unverified", "duration_ms": 0}
    else:
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
    started = utcnow()
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
    duration_ms = int((utcnow() - started).total_seconds() * 1000)

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
        if not state.database.update_step_state_machine(
            step_id=str(step_id),
            status=status,
            outputs=outputs,
            duration_ms=duration_ms,
            compressed_context=compressed,
            state_snapshot=result,
        ):
            # 成功结果写不进去（步骤行未命中/被并发删除）必须可见，否则下游交接
            # 只能读到空 outputs，故障点在别处，极难定位。
            logger.warning("步骤 {} 成功状态写入未命中数据行（outputs/交接上下文会缺失）", step_id)
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

    # needs_input 不是失败；外层拿到 Agent 提问并写回 Paused 检查点后，
    # 才能记录带 question 的唯一 needs_input 事件。这里若也记一次，
    # 一次提问会产生两个同名事件，通知/回放消费方会误认为请求了两次输入。
    if result.get("needs_input"):
        result["duration_ms"] = duration_ms
        return result
    # 失败只记录尝试结果，不在这里写入 Failed 状态；由外层重试耗尽后收尾。
    event_type = "failed"
    failure_reason = error or check_status or "unknown"
    event_data = {"reason": failure_reason, "blocked": blocked, "duration_ms": duration_ms}
    if acceptance_result is not None:
        event_data.update({
            "acceptance_status": result.get("acceptance_status"),
            "acceptance_evidence": result.get("acceptance_evidence", []),
            "reward": result.get("reward"),
        })
    if not state.database.add_step_event(
        step_id, plan_id, event_type, event_data,
        branch_version=step_model.branch_version,
    ):
        logger.warning("step_event {} 写入失败: {}", event_type, step_id)
    result["duration_ms"] = duration_ms
    return result


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

    retry_policy = automatic_retry_policy(step.get("tool", ""), getattr(state, "capability_registry", None), getattr(state, "tool_registry", None))
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
            # 通过 api.server 间接访问：测试通过 monkeypatch.setattr(server_mod, "_execute_step_with_state_machine", ...)
            # 影响此处解析，保持旧 server.py 单文件时 monkeypatch 路径兼容（零测试改动）。
            import api.server as _server_mod
            result = await _server_mod._execute_step_with_state_machine(plan, step)
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
        result = annotate_retry_failure(result, retry_policy)
        if not result.get("success") and not result.get("needs_input") and not retry_policy["eligible"]:
            state.database.add_step_event(
                step_id, str(plan.plan_id), "retry_suppressed",
                {"policy": retry_policy, "manual_retry_required": True}, branch_version=branch_version,
            )
            await broadcast_log(f"[RETRY_SUPPRESSED] 步骤 {step_id} 不自动重试；请核对可能已发生的操作后再明确发起执行", "warning")
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
        if result.get("needs_input"):
            # Agent 明确回答「缺输入」：既不是完成也不是失败。步骤回到 Pending，
            # 计划置 Paused 并记下恢复断点；用户 POST
            # /api/plan/{plan_id}/steps/{step_id}/input 补输入后 /resume，
            # 调度器会从同一步继续，而不是把整个计划钉成 Failed 要求重跑。
            needs_output = _handoff_json_object(result.get("output"))
            question = (
                _handoff_text(needs_output.get("output"))
                or _handoff_text(needs_output)
                or str(result.get("message") or "")
                or "agent 需要补充输入"
            )
            if not state.database.update_step_state_machine(
                step_id=step_id,
                status=StepStatus.PENDING.value,
                outputs={"needs_input": True, "question": question},
                duration_ms=result.get("duration_ms"),
                state_snapshot=result,
            ):
                logger.warning("needs_input 步骤状态写回未命中: {}", step_id)
            state.database.add_step_event(
                step_id, str(plan.plan_id), "needs_input",
                {"question": question[:1000], "duration_ms": result.get("duration_ms")},
                branch_version=branch_version,
            )
            _require_write(
                state.database.update_plan_state_machine(
                    str(plan.plan_id), PlanStatus.PAUSED.value,
                    resume_from_step_id=step_id,
                    current_branch=branch_version,
                ),
                f"计划 {plan.plan_id} 状态 → Paused（步骤 {step_id} 等待补充输入）",
            )
            await broadcast_plan_update(str(plan.plan_id))
            await broadcast_log(
                f"[NEEDS_INPUT] 步骤 {step_id} 需要补充输入：{question[:300]}；"
                f"提交 /api/plan/{plan.plan_id}/steps/{step_id}/input 后 /resume 继续",
                "warning",
            )
            return "stop", step_id

        if result.get("blocked"):
            error = result.get("error", "blocked")
            failure_reason = error
            debug_log(f"Step {step_id} blocked: {error}")
            failure_outputs = {"error": error, "check_status": result.get("check_status")}
            failure_outputs.update({k: result[k] for k in ("retry_policy", "manual_retry_required") if k in result})
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

        if result.get("check_status") == "failed" and retry_policy["eligible"] and _is_retryable_exit_code_acceptance(step, result):
            result["retryable"] = True
        if result.get("check_status") == "failed" and not (retry_policy["eligible"] and _is_retryable_exit_code_acceptance(step, result)):
            # P2 反馈回路：验收缺口作为 feedback 喂回 coding_agent 再修一轮
            # （每步最多一次；重试仍失败则走下方原判死路径）。
            feedback_text = _build_acceptance_feedback(result.get("acceptance_result"))
            if (
                feedback_text
                and step.get("tool") == "coding_agent"
                and not feedback_used
                and retry_policy["eligible"]
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
            failure_outputs.update({k: result[k] for k in ("retry_policy", "manual_retry_required") if k in result})
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
                failure_outputs.update({k: result[k] for k in ("retry_policy", "manual_retry_required") if k in result})
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

