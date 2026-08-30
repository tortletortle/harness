"""统一 Capability 契约和普通 Tool Adapter 测试。"""

import pytest

from authorization import AuthorizationManager
from capabilities import (
    CapabilityRegistry,
    CapabilityRequest,
    CapabilityResult,
    CapabilitySpec,
    ToolCapabilityAdapter,
)
from models import Step


class StubRegistry:
    def __init__(self):
        self.calls = []

    def list_tools(self):
        return [{
            "tool_id": "fake_tool",
            "version": "2.0.0",
            "description": "fake",
        }]

    def execute_tool(self, tool_id, instruction, inputs):
        self.calls.append((tool_id, instruction, inputs))
        return {"success": True, "value": 42}


class FakeCapability:
    def __init__(self, capability_id="fake"):
        self.capability_id = capability_id

    def spec(self):
        return CapabilitySpec(id=self.capability_id, kind="agent_tool")

    async def run(self, request, runtime_context):
        return CapabilityResult(status="completed", output={"goal": request.goal})


def test_registry_rejects_duplicate_ids():
    registry = CapabilityRegistry()
    registry.register(FakeCapability("same"))
    with pytest.raises(ValueError, match="已注册"):
        registry.register(FakeCapability("same"))


@pytest.mark.asyncio
async def test_registry_runs_peer_capability_without_child_creation():
    registry = CapabilityRegistry()
    capability = FakeCapability("peer")
    registry.register(capability)
    result = await registry.run(
        "peer",
        CapabilityRequest(work_item_id="w1", goal="inspect"),
        {},
    )
    assert result.status == "completed"
    assert result.output == {"goal": "inspect"}
    assert capability.spec().can_spawn_agents is False


@pytest.mark.asyncio
async def test_tool_adapter_uses_common_capability_contract(tmp_path):
    tools = StubRegistry()
    manager = AuthorizationManager(workspace_root=tmp_path)
    step = Step(
        order=1,
        description="run fake",
        tool="fake_tool",
        inputs={"path": "result.txt"},
        check_criteria="",
    )
    grant = manager.issue_for_step("p1", step)
    adapter = ToolCapabilityAdapter(tools, "fake_tool")

    spec = adapter.spec()
    assert spec.id == "fake_tool"
    assert spec.version == "2.0.0"
    result = await adapter.run(
        CapabilityRequest(
            work_item_id="w1",
            goal="run",
            inputs={"instruction": "execute", "path": "result.txt"},
            grant_id=grant.grant_id,
        ),
        {"authorization_manager": manager, "grant_id": grant.grant_id},
    )

    assert result.status == "completed"
    assert result.output["value"] == 42
    assert tools.calls == [("fake_tool", "execute", {"path": "result.txt"})]
