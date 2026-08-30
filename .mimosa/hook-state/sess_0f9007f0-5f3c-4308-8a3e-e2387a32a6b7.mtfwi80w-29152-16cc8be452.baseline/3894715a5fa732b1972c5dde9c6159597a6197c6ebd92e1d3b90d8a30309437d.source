"""Grant-bound model calls consume the model budget before upstream I/O."""

import pytest

from authorization import AuthorizationManager
from grant_llm_client import GrantLLMClient
from llm_client import DeterministicProvider, LLMClient, Message
from models import Step


class CountingProvider(DeterministicProvider):
    def __init__(self, response):
        super().__init__(response)
        self.call_count = 0

    async def chat(self, messages, model, temperature=0.7, max_tokens=4000, **kwargs):
        self.call_count += 1
        return await super().chat(messages, model, temperature, max_tokens, **kwargs)


def _setup(tmp_path, max_model_calls=1):
    manager = AuthorizationManager(
        workspace_root=tmp_path,
        default_max_model_calls=max_model_calls,
    )
    step = Step(order=1, description="agent", tool="coding_agent", inputs={}, check_criteria="")
    grant = manager.issue_for_step("p", step)
    provider = CountingProvider('{"status":"completed"}')
    client = LLMClient()
    client.register_provider(provider)
    return manager, grant, provider, client


@pytest.mark.asyncio
async def test_grant_llm_client_records_usage(tmp_path):
    manager, grant, provider, client = _setup(tmp_path, max_model_calls=1)
    provider.response = '{"status":"completed"}'
    bound = GrantLLMClient(client, manager, grant.grant_id)

    response = await bound.chat("deterministic", [Message(role="user", content="hi")], "test")

    assert response.content
    assert provider.call_count == 1
    assert manager.get(grant.grant_id).used_model_calls == 1


@pytest.mark.asyncio
async def test_model_budget_blocks_upstream_call(tmp_path):
    manager, grant, provider, client = _setup(tmp_path, max_model_calls=1)
    bound = GrantLLMClient(client, manager, grant.grant_id)

    await bound.chat("deterministic", [Message(role="user", content="first")], "test")
    with pytest.raises(PermissionError, match="budget_exceeded"):
        await bound.chat("deterministic", [Message(role="user", content="second")], "test")

    assert provider.call_count == 1


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_model_usage_tokens_are_recorded(tmp_path):
    manager, grant, provider, client = _setup(tmp_path, max_model_calls=1)
    provider.response = '{"status":"completed"}'
    # 直接验证 usage 归集接口（provider 实际可选返回空 usage）
    decision = manager.record_model_call(grant.grant_id)
    assert decision.allowed
    recorded = manager.record_model_usage(
        grant.grant_id,
        {"prompt_tokens": 12, "completion_tokens": 5},
    )
    assert recorded.allowed
    assert recorded.usage["input_tokens"] == 12
    assert recorded.usage["output_tokens"] == 5


@pytest.mark.asyncio
async def test_revoked_grant_blocks_model_call(tmp_path):
    manager, grant, provider, client = _setup(tmp_path)
    manager.revoke(grant.grant_id)
    bound = GrantLLMClient(client, manager, grant.grant_id)

    with pytest.raises(PermissionError, match="grant_revoked"):
        await bound.chat("deterministic", [Message(role="user", content="hi")], "test")
    assert provider.call_count == 0
