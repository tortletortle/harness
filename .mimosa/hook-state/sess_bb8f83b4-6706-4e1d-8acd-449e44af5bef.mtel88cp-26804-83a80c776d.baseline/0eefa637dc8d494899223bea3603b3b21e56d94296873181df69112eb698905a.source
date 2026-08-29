import pytest

from llm_client import DeterministicProvider, LLMClient, Message


@pytest.mark.asyncio
async def test_deterministic_provider_is_offline_and_repeatable():
    provider = DeterministicProvider('{"steps": [{"id": "step-1"}]}')
    client = LLMClient()
    client.register_provider(provider)

    response = await client.chat(
        "deterministic",
        [Message(role="user", content="create a plan")],
        model="test-model",
    )

    assert response.content == '{"steps": [{"id": "step-1"}]}'
    assert await client.health_check("deterministic")
    assert len(provider.calls) == 1
