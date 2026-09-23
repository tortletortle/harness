"""Offline adversarial provider tests; not evidence of real upstream failures."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

import llm_client as llm
from designer import DefaultDesigner


def provider():
    return llm.OpenAICompatibleProvider('test', 'offline-boundary-key', 'https://'+uuid4().hex+'.invalid/v1')


def install_response(monkeypatch, payload):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))


@pytest.mark.parametrize('cls', [llm.AnthropicProvider, llm.OpenAIProvider, llm.OpenAICompatibleProvider])
async def test_health_check_preserves_cancellation(cls):
    obj = cls.__new__(cls)
    obj.chat = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await obj.health_check()


@pytest.mark.parametrize('value', ['', 'invalid', '0', '-1', '1000000'])
def test_invalid_concurrency_configuration_has_safe_default(monkeypatch, value):
    monkeypatch.setenv('EAS_LLM_MAX_CONCURRENCY', value)
    obj = provider()
    # Check the actual gate, not an implementation-specific semaphore attribute.
    assert obj._slot.acquire(blocking=False)
    try:
        assert obj._slot.acquire(blocking=False)
        try:
            assert not obj._slot.acquire(blocking=False)
        finally:
            obj._slot.release()
    finally:
        obj._slot.release()


async def test_invalid_success_payload_is_safe_and_retryable(monkeypatch):
    private = 'PRIVATE-UPSTREAM-CONTENT-MUST-NOT-APPEAR'
    install_response(monkeypatch, {'choices':[{'message':{'content':{'private':private}}}]})
    with pytest.raises(llm.LLMRequestError) as caught:
        await provider().chat([llm.Message(role='user', content='hello')], 'model')
    assert private not in str(caught.value)
    assert caught.value.retryable is True
    assert caught.value.status_code == 200  # Preserve the actual upstream HTTP status.


async def test_malformed_success_response_uses_configured_fallback(monkeypatch):
    install_response(monkeypatch, {'choices':[]})
    primary = provider()
    backup = SimpleNamespace(chat=AsyncMock(return_value=llm.LLMResponse(content='backup',model='b')),name='backup')
    client = llm.LLMClient()
    client.register_provider(primary)
    client.register_provider(backup)
    designer = DefaultDesigner.__new__(DefaultDesigner)
    designer.llm_client = client
    designer.model_config = {'fallback':{'provider':'backup','model':'b'}}
    result = await designer._chat_design(provider_name='test',model='m',messages=[llm.Message(role='user',content='hello')])
    assert result.content == 'backup'
    backup.chat.assert_awaited_once()


@pytest.mark.parametrize('payload', [None, [], {}, {'choices':None}, {'choices':[]},
    {'choices':[None]}, {'choices':[{'message':None}]},
    {'choices':[{'message':{'content':False}}]},
    {'choices':[{'message':{'content':'ok'}}],'usage':['private']},
    {'choices':[{'message':{'content':None,'tool_calls':'private'}}]}])
async def test_malformed_shapes_are_retryable_without_echo(monkeypatch, payload):
    install_response(monkeypatch, payload)
    with pytest.raises(llm.LLMRequestError) as caught:
        await provider().chat([llm.Message(role='user',content='hello')], 'm')
    assert caught.value.status_code == 200
    assert caught.value.retryable
    assert 'private' not in str(caught.value)


async def test_non_json_success_is_retryable(monkeypatch):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='<html>private gateway page</html>'))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=transport, **kw))
    with pytest.raises(llm.LLMRequestError) as caught:
        await provider().chat([], 'm')
    assert caught.value.retryable
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('status,retryable', [(400,False),(401,False),(403,False),(408,True),(429,True),(500,True),(503,True)])
async def test_http_error_classification_and_safe_message(monkeypatch, status, retryable):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text='private upstream content', headers={'retry-after':'3'}))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=transport, **kw))
    with pytest.raises(llm.LLMRequestError) as caught:
        await provider().chat([], 'm')
    assert caught.value.status_code == status
    assert caught.value.retryable is retryable
    assert caught.value.retry_after == 3
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('limit', [1,2,64])
def test_valid_concurrency_limits(monkeypatch, limit):
    monkeypatch.setenv('EAS_LLM_MAX_CONCURRENCY',str(limit))
    slot=provider()._slot
    for _ in range(limit):
        assert slot.acquire(blocking=False)
    assert not slot.acquire(blocking=False)
    for _ in range(limit):
        slot.release()


async def test_twenty_requests_respect_shared_gate(monkeypatch):
    monkeypatch.setenv('EAS_LLM_MAX_CONCURRENCY','2')
    first = provider()
    second = llm.OpenAICompatibleProvider('alias', first.api_key, first.base_url+'/')
    assert first._slot is second._slot
    active = peak = total = 0
    async def respond(request):
        nonlocal active,peak,total
        active += 1
        total += 1
        peak=max(peak,active)
        try:
            await asyncio.sleep(0.005)
            return httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}]})
        finally:
            active -= 1
    real_client=httpx.AsyncClient
    transport=httpx.MockTransport(respond)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=transport,**kw))
    responses = await asyncio.wait_for(asyncio.gather(*[(first if i%2 else second).chat([], 'm') for i in range(20)]),5)
    assert total == 20 and peak == 2 and active == 0
    assert all(r.content == 'ok' for r in responses)


async def test_inflight_cancel_releases_gate_for_next_request(monkeypatch):
    monkeypatch.setenv('EAS_LLM_MAX_CONCURRENCY','1')
    obj=provider()
    entered=asyncio.Event()
    calls=0
    async def respond(request):
        nonlocal calls
        calls+=1
        if calls==1:
            entered.set()
            await asyncio.Event().wait()
        return httpx.Response(200,json={'choices':[{'message':{'content':'after cancel'}}]})
    real_client=httpx.AsyncClient
    transport=httpx.MockTransport(respond)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=transport,**kw))
    task=asyncio.create_task(obj.chat([], 'm'))
    await asyncio.wait_for(entered.wait(),1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await asyncio.wait_for(obj.chat([], 'm'),1)).content == 'after cancel'


async def test_untrusted_transport_message_not_exposed(monkeypatch):
    real_client=httpx.AsyncClient
    def fail(request):
        raise httpx.ConnectError('private URL password and gateway content',request=request)
    transport=httpx.MockTransport(fail)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=transport,**kw))
    with pytest.raises(llm.LLMRequestError) as caught:
        await provider().chat([], 'm')
    assert caught.value.retryable
    assert caught.value.status_code is None
    assert 'ConnectError' in str(caught.value) and 'private' not in str(caught.value)
