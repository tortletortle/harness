"""Agent-as-Tool contract tests."""

import pytest

from agent_tools import AgentRuntimeContext, FakeAgentTool, SingleTurnAgentTool
from authorization import AuthorizationManager
from capabilities import CapabilityRequest, CapabilityResult
from llm_client import DeterministicProvider, LLMClient
from models import Step


def _runtime(tmp_path, response, max_model_calls=1):
    manager = AuthorizationManager(
        workspace_root=tmp_path,
        default_max_model_calls=max_model_calls,
    )
    llm = LLMClient()
    llm.register_provider(DeterministicProvider(response))
    step = Step(order=1, description="agent", tool="coding_agent", inputs={}, check_criteria="")
    grant = manager.issue_for_step("p", step)
    context = AgentRuntimeContext(
        llm_client=llm,
        authorization_manager=manager,
        provider_name="deterministic",
        model="test-model",
        grant_id=grant.grant_id,
        system_prompt="You are a test agent.",
    )
    return manager, grant, context


@pytest.mark.asyncio
async def test_single_turn_agent_returns_structured_result(tmp_path):
    manager, grant, context = _runtime(
        tmp_path,
        '{"status":"completed","output":{"answer":"ok"},"artifacts":["artifact://x"],"facts":[{"key":"k","value":"v"}]}',
    )
    agent = SingleTurnAgentTool(
        agent_id="coding_agent",
        description="只做代码分析",
        llm_client=context.llm_client,
        authorization_manager=manager,
        provider_name="deterministic",
        model="test-model",
    )
    result = await agent.run(
        CapabilityRequest(
            work_item_id="w1",
            goal="inspect",
            inputs={"file": "main.py"},
            artifact_refs=["artifact://input"],
            allowed_tools=["file_read"],
            grant_id=grant.grant_id,
        ),
        context,
    )
    assert result.status == "completed"
    assert result.output == {"answer": "ok"}
    assert result.artifacts == ["artifact://x"]
    assert result.facts == [{"key": "k", "value": "v"}]
    assert manager.get(grant.grant_id).used_model_calls == 1


@pytest.mark.asyncio
async def test_single_turn_agent_rejects_mismatched_grant(tmp_path):
    manager, grant, context = _runtime(tmp_path, '{"status":"completed"}')
    agent = SingleTurnAgentTool("coding_agent", "agent", context.llm_client, manager, "deterministic", "test-model")
    other = manager.issue_for_step("p", Step(order=1, description="other", tool="coding_agent", inputs={}, check_criteria=""))
    result = await agent.run(
        CapabilityRequest(work_item_id="w1", goal="inspect", grant_id=other.grant_id),
        context,
    )
    assert result.status == "failed"
    assert "不匹配" in result.error
    assert manager.get(grant.grant_id).used_model_calls == 0


@pytest.mark.asyncio
async def test_single_turn_agent_invalid_json_fails_closed(tmp_path):
    manager, grant, context = _runtime(tmp_path, "not json")
    agent = SingleTurnAgentTool("coding_agent", "agent", context.llm_client, manager, "deterministic", "test-model")
    result = await agent.run(
        CapabilityRequest(work_item_id="w1", goal="inspect", grant_id=grant.grant_id),
        context,
    )
    assert result.status == "failed"
    assert "合法 JSON" in result.error


@pytest.mark.asyncio
async def test_fake_agent_requires_grant_and_cannot_spawn(tmp_path):
    manager, grant, context = _runtime(tmp_path, "unused")
    agent = FakeAgentTool()
    assert agent.spec().can_spawn_agents is False
    no_grant = await agent.run(
        CapabilityRequest(work_item_id="w1", goal="test"),
        context,
    )
    assert no_grant.status == "failed"
    assert "Grant" in no_grant.error

    ok = await agent.run(
        CapabilityRequest(work_item_id="w1", goal="test", grant_id=grant.grant_id),
        context,
    )
    assert ok.status == "completed"
