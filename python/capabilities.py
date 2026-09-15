"""Common Capability contracts for deterministic tools and Agent tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Optional, Protocol

from authorization import AuthorizationManager


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    version: str = "1.0.0"
    kind: str = "deterministic_tool"
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    required_permissions: tuple[str, ...] = ()
    side_effect: str = "none"
    idempotent: bool = True
    budget_spec: Dict[str, Any] = field(default_factory=dict)
    acceptance_spec: List[Dict[str, Any]] = field(default_factory=list)
    can_spawn_agents: bool = False
    # 面向 Designer/前端的人类可读描述（工具目录聚合时使用）。
    description: str = ""


@dataclass
class CapabilityRequest:
    work_item_id: str
    goal: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    artifact_refs: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    acceptance: List[Dict[str, Any]] = field(default_factory=list)
    grant_id: Optional[str] = None


@dataclass
class CapabilityResult:
    status: str
    output: Any = None
    artifacts: List[str] = field(default_factory=list)
    facts: List[Dict[str, Any]] = field(default_factory=list)
    acceptance_evidence: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "output": self.output,
            "artifacts": list(self.artifacts),
            "facts": list(self.facts),
            "acceptance_evidence": list(self.acceptance_evidence),
            "usage": dict(self.usage),
            "error": self.error,
        }


class Capability(Protocol):
    def spec(self) -> CapabilitySpec:
        ...

    async def run(self, request: CapabilityRequest, runtime_context: Any) -> CapabilityResult:
        ...


class CapabilityRegistry:
    """Registry for peer capabilities; it never creates child agents."""

    def __init__(self) -> None:
        self._capabilities: Dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        capability_id = capability.spec().id
        if not capability_id:
            raise ValueError("Capability id 不能为空")
        if capability_id in self._capabilities:
            raise ValueError(f"Capability 已注册: {capability_id}")
        self._capabilities[capability_id] = capability

    def get(self, capability_id: str) -> Optional[Capability]:
        return self._capabilities.get(capability_id)

    def list_specs(self) -> List[CapabilitySpec]:
        return [capability.spec() for capability in self._capabilities.values()]

    async def run(self, capability_id: str, request: CapabilityRequest, runtime_context: Any) -> CapabilityResult:
        capability = self.get(capability_id)
        if capability is None:
            return CapabilityResult(status="failed", error=f"Capability 不存在: {capability_id}")
        return await capability.run(request, runtime_context)


class ToolCapabilityAdapter:
    """Expose an existing synchronous ToolRegistry tool as an async Capability."""

    # 副作用分类（docs/TOOL_INTERFACE_DESIGN.md §4.2）：新工具有副作用时在此登记。
    # reversible：文件写入/git 本地操作，可回退；external：对外发送（PR/HTTP），
    # 不可回退，目录层展示给 Designer 前应审慎；其余视为 none。
    _REVERSIBLE_TOOLS = {"file_creator", "text_editor", "code_context", "project_memory", "pdf_reader", "desktop_observe"}
    _EXTERNAL_TOOLS = {"command_runner", "package_installer", "git_operator", "web_fetcher", "test_runner", "browser_check",
                       "github_operator", "http_api", "browser_observe", "review_agent", "super_search"}
    _READ_ONLY_TOOLS = {"manual_tool", "lint_gate", "file_find", "document_batch", "image_reader", "office_reader",
                        "toolbox", "greeting", "uppercase_text", "progress_view"}
    _NON_IDEMPOTENT_TOOLS = _REVERSIBLE_TOOLS | _EXTERNAL_TOOLS
    _KNOWN_TOOLS = _READ_ONLY_TOOLS | _NON_IDEMPOTENT_TOOLS

    @classmethod
    def risk_for(cls, tool_id: str) -> tuple[str, bool]:
        # An unknown plugin is not automatically safe merely because it is unlisted.
        if tool_id in cls._READ_ONLY_TOOLS:
            return "none", True
        if tool_id in cls._REVERSIBLE_TOOLS:
            return "reversible", False
        return "external", False

    def __init__(self, tool_registry: Any, tool_id: str, version: str = "1.0.0") -> None:
        self.tool_registry = tool_registry
        self.tool_id = tool_id
        self.version = version

    def spec(self) -> CapabilitySpec:
        metadata = next(
            (item for item in self.tool_registry.list_tools() if item.get("tool_id") == self.tool_id),
            {},
        )
        side_effect, idempotent = self.risk_for(self.tool_id)
        return CapabilitySpec(
            id=self.tool_id,
            version=metadata.get("version", self.version),
            kind="deterministic_tool",
            required_permissions=(f"tool:{self.tool_id}",),
            side_effect=side_effect,
            idempotent=idempotent,
            description=str(metadata.get("description", "")),
        )

    async def run(self, request: CapabilityRequest, runtime_context: Any) -> CapabilityResult:
        manager = _context_value(runtime_context, "authorization_manager")
        grant_id = request.grant_id or _context_value(runtime_context, "grant_id")
        inputs = dict(request.inputs or {})
        instruction = str(inputs.pop("instruction", inputs.pop("action", "execute")))
        if self.tool_id in ("browser_observe", "super_search", "pdf_reader", "image_reader", "office_reader", "desktop_observe", "file_find", "document_batch"):
            result = await asyncio.to_thread(
                self.tool_registry.execute_tool_with_grant, self.tool_id, instruction, inputs,
                authorization_manager=manager, grant_id=grant_id,
                allow_loopback=_context_value(runtime_context, "browser_allow_loopback", False),
            )
        else:
            if manager is not None:
                decision = manager.authorize(grant_id, self.tool_id, instruction, inputs)
                if not decision.allowed:
                    return CapabilityResult(
                        status="failed",
                        error=decision.message,
                        usage=decision.usage,
                    )
            result = await asyncio.to_thread(self.tool_registry.execute_tool, self.tool_id, instruction, inputs)
        return CapabilityResult(
            status="completed" if result.get("success") else "failed",
            output=result,
            usage=(manager.get(grant_id).usage() if manager is not None and grant_id and manager.get(grant_id) else {}),
            error=None if result.get("success") else result.get("error", "tool failed"),
        )


class HumanCapabilityAdapter(ToolCapabilityAdapter):
    """把人工步骤工具以 kind=human_tool 暴露给 Capability 目录。

    设计稿 §3/§6：Human Tool（approval/input）是正式的第三类 Capability。
    执行逻辑与普通工具相同，但 Runtime 状态机会对 human_tool 步骤
    强制挂审批（ApprovalManager），实现"人工步骤真确认"。
    """

    def spec(self) -> CapabilitySpec:
        base = super().spec()
        metadata = next(
            (item for item in self.tool_registry.list_tools() if item.get("tool_id") == self.tool_id),
            {},
        )
        return replace(
            base,
            kind="human_tool",
            required_permissions=(f"human:{self.tool_id}",),
            side_effect="none",
            idempotent=True,
            description=str(metadata.get("description", "")),
        )


def _context_value(context: Any, name: str, default: Any = None) -> Any:
    if isinstance(context, dict):
        return context.get(name, default)
    return getattr(context, name, default)


__all__ = [
    "Capability",
    "CapabilityRegistry",
    "CapabilityRequest",
    "CapabilityResult",
    "CapabilitySpec",
    "ToolCapabilityAdapter",
    "HumanCapabilityAdapter",
]
