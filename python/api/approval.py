"""
工具级审批管理（P0-a）

参考 Cline 的"挂起 Promise + 单槽 resolver"模式：
执行协程在调用高风险工具前创建审批请求并 await 一个 asyncio.Event；
用户通过 /approve 或 /reject 端点 resolve 该 Event，执行协程立即恢复。

设计约束（对应 docs/AGENT_HARNESS_DESIGN.md §7 授权模型）：
- 审批状态只存在于内存（服务重启即失效，孤儿恢复会把中断步骤标为可重跑）；
- 每个审批请求只能被 resolve 一次（幂等保护：第二次 resolve 返回 False）；
- 等待超时视为拒绝（fail-safe：没人批准就不执行）；
- 哪些工具需要审批由配置驱动（approval.required_tools），默认为空——
  保持向后兼容：不配置时所有工具照旧自动执行，现有测试不受影响。
"""

from copy import deepcopy
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from logging_config import get_logger

logger = get_logger(__name__)


class ApprovalTimeout(Exception):
    """审批等待超时（视为拒绝）。"""


@dataclass
class ApprovalRequest:
    """一次待审批的工具调用请求。"""

    approval_id: str
    plan_id: str
    step_id: str
    tool_id: str
    instruction: str
    inputs: Dict[str, Any]
    created_at: str
    # Event 在 dataclass 默认值中不能用 field(default_factory=asyncio.Event)
    # ——Event 绑定事件循环的方式在 3.11 下是惰性的，直接实例化是安全的。
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: Optional[bool] = None
    comment: Optional[str] = None
    desktop_scope: Optional[Dict[str, Any]] = None

    @property
    def resolved(self) -> bool:
        return self.approved is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "tool_id": self.tool_id,
            "instruction": self.instruction,
            "inputs": deepcopy(self.inputs),
            **({"desktop_scope": deepcopy(self.desktop_scope)} if self.desktop_scope else {}),
            "created_at": self.created_at,
        }


class ApprovalManager:
    """内存中的工具审批队列：创建 → 等待 → resolve（一次）。"""

    def __init__(
        self,
        required_tools: Optional[List[str]] = None,
        timeout_seconds: float = 600.0,
        human_steps: bool = True,
    ) -> None:
        self.required_tools: set = set(required_tools or [])
        self.timeout_seconds = float(timeout_seconds)
        # 人工步骤（kind=human_tool，如 manual_tool）是否强制挂审批。
        # 默认开启：设计稿 §3 Human Tool 是正式控制流，人工步骤必须真确认。
        self.human_steps = bool(human_steps)
        self._pending: Dict[str, ApprovalRequest] = {}

    def is_required(self, tool_id: str) -> bool:
        """该工具是否需要人工审批。"""
        return tool_id == "desktop_observe" or tool_id in self.required_tools

    def is_required_for_step(self, tool_id: str, capability_kind: Optional[str] = None) -> bool:
        """步骤级审批判定：配置的高风险工具，或 human_tool 步骤（§23.1）。

        capability_kind 来自 CapabilityRegistry；不传或非 human_tool 时
        退化为原有 is_required 行为。
        """
        if self.is_required(tool_id):
            return True
        return bool(self.human_steps) and capability_kind == "human_tool"

    def create(
        self,
        plan_id: str,
        step_id: str,
        tool_id: str,
        instruction: str,
        inputs: Dict[str, Any],
        desktop_scope: Optional[Dict[str, Any]] = None,
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            approval_id=uuid.uuid4().hex,
            plan_id=str(plan_id),
            step_id=str(step_id),
            tool_id=tool_id,
            instruction=instruction,
            inputs=deepcopy(inputs or {}),
            desktop_scope=deepcopy(desktop_scope),
            created_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        )
        self._pending[request.approval_id] = request
        return request

    async def wait(self, approval_id: str) -> ApprovalRequest:
        """挂起等待审批决定。超时抛 ApprovalTimeout（语义 = 拒绝）。"""
        request = self._pending.get(approval_id)
        if request is None:
            raise KeyError(approval_id)
        try:
            await asyncio.wait_for(request.event.wait(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            # 超时按拒绝处理并清理，避免悬挂请求泄漏
            self._pending.pop(approval_id, None)
            raise ApprovalTimeout(f"审批等待超时（{self.timeout_seconds}s）: {approval_id}")
        return request

    def resolve(self, approval_id: str, approved: bool, comment: Optional[str] = None, desktop_confirmation: Optional[str] = None) -> bool:
        """
        写入审批决定并唤醒等待者。

        Returns:
            True  — 首次 resolve 成功
            False — 请求不存在或已被处理（幂等拒绝）
        """
        request = self._pending.get(approval_id)
        if request is None or request.resolved:
            return False
        if approved and request.tool_id == "desktop_observe":
            if not request.desktop_scope or desktop_confirmation != request.desktop_scope.get("confirmation"):
                return False
        request.approved = bool(approved)
        request.comment = comment
        request.event.set()
        return True

    def cleanup(self, approval_id: str) -> None:
        """决定已被消费后移除请求，防止字典无限增长。"""
        self._pending.pop(approval_id, None)

    def list_pending(self, plan_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出待审批请求（供 GET /api/approvals 与前端轮询兜底）。"""
        items = [
            r.to_dict()
            for r in self._pending.values()
            if not r.resolved and (plan_id is None or r.plan_id == str(plan_id))
        ]
        items.sort(key=lambda r: r["created_at"])
        return items

    def reject_all_for_plan(self, plan_id: str, comment: str = "plan terminated") -> int:
        """计划终止/失败时批量拒绝其未决审批，防止执行协程永久挂起。"""
        count = 0
        for request in list(self._pending.values()):
            if request.plan_id == str(plan_id) and not request.resolved:
                request.approved = False
                request.comment = comment
                request.event.set()
                count += 1
        return count
