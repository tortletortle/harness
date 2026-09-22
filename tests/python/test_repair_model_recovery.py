"""Fault-injection unit tests, not evidence of a live provider returning 429."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from designer import DefaultDesigner, GenerationError
from llm_client import LLMRequestError, LLMResponse, Message, OpenAICompatibleProvider, _retry_after


def designer(client, fallback=True):
    config = {"primary": {"provider": "main", "model": "m", "temperature": 0.1}}
    if fallback:
        config["fallback"] = {"provider": "backup", "model": "b"}
    # No filesystem cache is needed to test routing.
    obj = DefaultDesigner.__new__(DefaultDesigner)
    obj.model_config, obj.llm_client = config, client
    return obj


async def invoke(obj):
    return await obj._chat_design(provider_name="main", model="m", messages=[Message(role="user", content="hi")])


@pytest.mark.parametrize("status", [429, 403, 503])
async def test_structured_failure_automatically_falls_back(status):
    client = SimpleNamespace(chat=AsyncMock(side_effect=[
        LLMRequestError("upstream unavailable", status_code=status, retry_after=60, retryable=status != 403),
        LLMResponse(content="backup worked", model="b"),
    ]))
    assert (await invoke(designer(client))).content == "backup worked"
    assert [c.kwargs["provider_name"] for c in client.chat.await_args_list] == ["main", "backup"]


async def test_rate_limit_retries_once_and_preserves_retry_after(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    client = SimpleNamespace(chat=AsyncMock(side_effect=LLMRequestError(
        "limited", status_code=429, retry_after=2, retryable=True)))
    with pytest.raises(LLMRequestError):
        await invoke(designer(client, fallback=False))
    assert client.chat.await_count == 2
    sleep.assert_awaited_once_with(2)


async def test_long_retry_after_does_not_retry_early(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    client = SimpleNamespace(chat=AsyncMock(side_effect=LLMRequestError(
        "limited", status_code=429, retry_after=60, retryable=True)))
    with pytest.raises(LLMRequestError):
        await invoke(designer(client, fallback=False))
    assert client.chat.await_count == 1
    sleep.assert_not_called()


async def test_bad_request_not_retried_or_fallen_back():
    client = SimpleNamespace(chat=AsyncMock(side_effect=LLMRequestError("bad", status_code=400)))
    with pytest.raises(LLMRequestError):
        await invoke(designer(client))
    assert client.chat.await_count == 1


async def test_cancel_not_retried():
    client = SimpleNamespace(chat=AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await invoke(designer(client))
    assert client.chat.await_count == 1


@pytest.mark.parametrize("status,expected", [(429, 429), (503, 503), (403, 502)])
async def test_generation_api_contract_does_not_leak_upstream_body(status, expected):
    from api.server import _generation_error_handler
    error = GenerationError("wrapped")
    error.__cause__ = LLMRequestError("private upstream payload", status_code=status,
                                     retry_after=2, retryable=status != 403)
    response = await _generation_error_handler(None, error)
    assert response.status_code == expected
    assert b"private" not in response.body
    assert json.loads(response.body)["upstream_status"] == status
    if status != 403:
        assert response.headers["retry-after"] == "2"


async def test_design_route_preserves_generation_error(monkeypatch):
    from api.routes import design_routes as routes
    error = GenerationError("failed")
    monkeypatch.setattr(routes.state, "designer", SimpleNamespace(generate_design=AsyncMock(side_effect=error)))
    monkeypatch.setattr(routes, "broadcast_log", AsyncMock())
    with pytest.raises(GenerationError):
        await routes.create_design(routes.DesignRequest(user_request="test"))


def test_retry_after_numeric_and_http_date():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    assert _retry_after(SimpleNamespace(headers={"retry-after": "3"})) == 3
    assert 5 <= _retry_after(SimpleNamespace(headers={"retry-after": format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10))})) <= 10
    assert _retry_after(SimpleNamespace(headers={"retry-after": "invalid"})) is None


async def test_provider_gate_is_shared_and_cancellation_does_not_leak(monkeypatch):
    from llm_client import _acquire_provider_slot
    monkeypatch.setenv("EAS_LLM_MAX_CONCURRENCY", "1")
    a = OpenAICompatibleProvider("a", "unit-test-gate-key", "https://gate.invalid")
    b = OpenAICompatibleProvider("b", "unit-test-gate-key", "https://gate.invalid")
    assert a._slot is b._slot
    await _acquire_provider_slot(a._slot)
    waiting = asyncio.create_task(_acquire_provider_slot(b._slot))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    a._slot.release()
    await asyncio.wait_for(_acquire_provider_slot(b._slot), 1)
    b._slot.release()
