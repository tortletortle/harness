"""Small, grant-bound Agent-as-Tool implementations.

This module intentionally implements a single model turn only.  It is a
contract/protocol layer, not another recursive agent orchestrator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from authorization import AuthorizationManager
from capabilities import CapabilityRequest, CapabilityResult, CapabilitySpec
from grant_llm_client import GrantLLMClient
from llm_client import LLMClient, Message


@dataclass
class AgentRuntimeContext:
    llm_client: LLMClient
    authorization_manager: AuthorizationManager
    provider_name: str
    model: str
    grant_id: str
    system_prompt: str = ""


class BaseAgentTool:
    """Base class for peer Agent tools; spawning children is disabled."""

    can_spawn_agents = False
    max_nested_depth = 0

    def spec(self) -> CapabilitySpec:
        raise NotImplementedError

    def _validate_request(self, request: CapabilityRequest, context: AgentRuntimeContext) -> Optional[str]:
        if not request.work_item_id:
            return "work_item_id 不能为空"
        if not request.goal:
            return "goal 不能为空"
        if self.can_spawn_agents or self.max_nested_depth != 0:
            return "Agent Tool 禁止创建子 Agent"
        if request.grant_id and request.grant_id != context.grant_id:
            return "WorkItem Grant 与 Runtime Grant 不匹配"
        if not context.grant_id:
            return "Agent Tool 必须携带 Runtime Grant"
        return None

    @staticmethod
    def _base_prompt(request: CapabilityRequest) -> str:
        return (
            "只完成当前 WorkItem，不创建子 Agent，不扩大权限。\n"
            f"目标：{request.goal}\n"
            f"输入：{json.dumps(request.inputs, ensure_ascii=False, default=str)}\n"
            f"已有产物引用：{json.dumps(request.artifact_refs, ensure_ascii=False)}\n"
            f"允许使用的工具：{json.dumps(request.allowed_tools, ensure_ascii=False)}\n"
            "只输出 JSON：{\"status\":\"completed|needs_input|failed\","
            "\"output\":...,\"artifacts\":[],\"facts\":[],\"error\":null}"
        )


class SingleTurnAgentTool(BaseAgentTool):
    """One-turn LLM capability returning a structured result."""

    def __init__(
        self,
        agent_id: str,
        description: str,
        llm_client: LLMClient,
        authorization_manager: AuthorizationManager,
        provider_name: str,
        model: str,
        required_permissions: Optional[List[str]] = None,
    ) -> None:
        self.agent_id = agent_id
        self.description = description
        self.llm_client = llm_client
        self.authorization_manager = authorization_manager
        self.provider_name = provider_name
        self.model = model
        self.required_permissions = tuple(required_permissions or (f"agent:{agent_id}",))

    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            id=self.agent_id,
            version="1.0.0",
            kind="agent_tool",
            required_permissions=self.required_permissions,
            side_effect="none",
            idempotent=True,
            budget_spec={"max_model_calls": 1},
            can_spawn_agents=False,
        )

    async def run(self, request: CapabilityRequest, runtime_context: AgentRuntimeContext) -> CapabilityResult:
        error = self._validate_request(request, runtime_context)
        if error:
            return CapabilityResult(status="failed", error=error)

        grant_id = runtime_context.grant_id
        client = GrantLLMClient(
            runtime_context.llm_client,
            runtime_context.authorization_manager,
            grant_id,
        )
        messages = [
            Message(
                role="system",
                content=runtime_context.system_prompt or self.description,
            ),
            Message(role="user", content=self._base_prompt(request)),
        ]
        try:
            response = await client.chat(
                provider_name=runtime_context.provider_name,
                messages=messages,
                model=runtime_context.model,
                temperature=0.0,
                max_tokens=2000,
            )
        except PermissionError as exc:
            return CapabilityResult(status="failed", error=str(exc))
        except Exception as exc:
            return CapabilityResult(status="failed", error=f"Agent Tool 模型调用失败: {exc}")

        parsed = _parse_result(response.content)
        if parsed is None:
            # 模型输出无法解析时安全失败，不将自然语言伪装成完成。
            return CapabilityResult(
                status="failed",
                error="Agent Tool 返回不是合法 JSON",
                usage=runtime_context.authorization_manager.get(grant_id).usage(),
            )
        status = parsed.get("status", "failed")
        if status not in {"completed", "needs_input", "failed"}:
            status = "failed"
        grant = runtime_context.authorization_manager.get(grant_id)
        return CapabilityResult(
            status=status,
            output=parsed.get("output"),
            artifacts=_list_of_strings(parsed.get("artifacts")),
            facts=_list_of_dicts(parsed.get("facts")),
            usage=grant.usage() if grant else {},
            error=parsed.get("error"),
        )


class FakeAgentTool(BaseAgentTool):
    """Deterministic Agent Tool used to test the capability boundary."""

    def __init__(self, agent_id: str = "fake_agent", result: Optional[CapabilityResult] = None) -> None:
        self.agent_id = agent_id
        self.result = result or CapabilityResult(
            status="completed",
            output={"message": "fake agent completed"},
        )

    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            id=self.agent_id,
            version="1.0.0",
            kind="agent_tool",
            required_permissions=(f"agent:{self.agent_id}",),
            budget_spec={"max_model_calls": 0},
            can_spawn_agents=False,
        )

    async def run(self, request: CapabilityRequest, runtime_context: AgentRuntimeContext) -> CapabilityResult:
        error = self._validate_request(request, runtime_context)
        if error:
            return CapabilityResult(status="failed", error=error)
        if request.grant_id is None:
            return CapabilityResult(status="failed", error="FakeAgentTool 也必须携带 Grant")
        return self.result


def _parse_result(content: str) -> Optional[Dict[str, Any]]:
    text = (content or "").strip()
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _list_of_strings(value: Any) -> List[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _list_of_dicts(value: Any) -> List[Dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


__all__ = ["AgentRuntimeContext", "BaseAgentTool", "FakeAgentTool", "SingleTurnAgentTool"]
