# -*- coding: utf-8 -*-
"""
llm_client 覆盖率补齐：provider 消息转换、错误包装、用量记账、健康检查。

全部 mock / 离线，不访问网络。pytest.ini 已设 asyncio_mode=auto，async 测试直接写即可。
"""
import sys
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import llm_client as llm_client_mod
from llm_client import (
    AnthropicProvider,
    DeterministicProvider,
    LLMClient,
    LLMProvider,
    LLMResponse,
    Message,
    OpenAICompatibleProvider,
    OpenAIProvider,
)


# ===== 抽象基类契约 =====

async def test_provider_abc_contract():
    """LLMProvider 抽象方法可被继承；显式走一遍基类 pass 体。"""

    class Dummy(LLMProvider):
        @property
        def name(self):
            return "dummy"

        async def chat(self, *args, **kwargs):
            await super().chat(*args, **kwargs)
            return LLMResponse(content="x", model="m")

        async def health_check(self):
            await super().health_check()
            return True

    dummy = Dummy()
    assert dummy.name == "dummy"
    # 基类 name property 的 pass 体
    assert LLMProvider.name.fget(dummy) is None
    # 基类 async 方法的 pass 体（经由 super() 调用）
    response = await dummy.chat([], "m")
    assert response.content == "x"
    assert await dummy.health_check() is True


def test_cannot_instantiate_abstract_provider():
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[abstract]


# ===== AnthropicProvider =====

def test_anthropic_init_missing_lib(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(ImportError, match="anthropic package not installed"):
        AnthropicProvider(api_key="k")


def test_anthropic_init_ok():
    pytest.importorskip("anthropic")
    provider = AnthropicProvider(api_key="k")
    assert provider.name == "anthropic"
    assert provider.client is not None


def _anthropic_with_mock_client():
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.api_key = "k"
    provider.client = MagicMock()
    return provider


async def test_anthropic_chat_converts_system_and_usage():
    provider = _anthropic_with_mock_client()
    fake_message = MagicMock()
    fake_message.content = [MagicMock(text="hi")]
    fake_message.usage = MagicMock(input_tokens=3, output_tokens=5)
    fake_message.stop_reason = "end_turn"
    provider.client.messages.create = AsyncMock(return_value=fake_message)

    response = await provider.chat(
        [Message(role="system", content="sys"), Message(role="user", content="q")],
        model="claude-x",
    )

    assert response.content == "hi"
    assert response.model == "claude-x"
    assert response.usage == {"input_tokens": 3, "output_tokens": 5}
    assert response.finish_reason == "end_turn"
    _, kwargs = provider.client.messages.create.call_args
    assert kwargs["system"] == "sys"
    assert kwargs["messages"] == [{"role": "user", "content": "q"}]
    assert kwargs["model"] == "claude-x"


async def test_anthropic_chat_error_wrapped():
    provider = _anthropic_with_mock_client()
    provider.client.messages.create = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(Exception, match="Anthropic API error"):
        await provider.chat([Message(role="user", content="q")], model="claude-x")


async def test_anthropic_health_check():
    provider = _anthropic_with_mock_client()
    provider.chat = AsyncMock(return_value=LLMResponse(content="t", model="m"))
    assert await provider.health_check() is True
    provider.chat = AsyncMock(side_effect=Exception("down"))
    assert await provider.health_check() is False


# ===== OpenAIProvider =====

def test_openai_init_missing_lib(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ImportError, match="openai package not installed"):
        OpenAIProvider(api_key="k")


def test_openai_init_ok():
    pytest.importorskip("openai")
    provider = OpenAIProvider(api_key="k")
    assert provider.name == "openai"
    assert provider.client is not None


def _openai_with_mock_client():
    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.api_key = "k"
    provider.client = MagicMock()
    return provider


async def test_openai_chat_converts_messages_and_usage():
    provider = _openai_with_mock_client()
    fake_choice = MagicMock()
    fake_choice.message.content = "hello"
    fake_choice.finish_reason = "stop"
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    fake_response.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
    provider.client.chat.completions.create = AsyncMock(return_value=fake_response)

    response = await provider.chat(
        [Message(role="system", content="s"), Message(role="user", content="q")],
        model="gpt-x",
    )

    assert response.content == "hello"
    assert response.usage == {"prompt_tokens": 10, "completion_tokens": 20}
    assert response.finish_reason == "stop"
    _, kwargs = provider.client.chat.completions.create.call_args
    assert kwargs["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q"},
    ]


async def test_openai_chat_error_wrapped():
    provider = _openai_with_mock_client()
    provider.client.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(Exception, match="OpenAI API error"):
        await provider.chat([Message(role="user", content="q")], model="gpt-x")


async def test_openai_health_check():
    provider = _openai_with_mock_client()
    provider.chat = AsyncMock(return_value=LLMResponse(content="t", model="m"))
    assert await provider.health_check() is True
    provider.chat = AsyncMock(side_effect=Exception("down"))
    assert await provider.health_check() is False


# ===== LLMClient =====

async def test_client_chat_unknown_provider():
    client = LLMClient()
    with pytest.raises(ValueError, match="not found"):
        await client.chat("nope", [Message(role="user", content="q")], "m")


async def test_client_chat_records_usage(monkeypatch):
    client = LLMClient()
    client.register_provider(DeterministicProvider("ok"))
    recorded = {}

    def fake_record(provider_name, model, usage):
        recorded.update(provider=provider_name, model=model, usage=usage)

    monkeypatch.setattr(llm_client_mod, "record_usage", fake_record)
    response = await client.chat(
        "deterministic", [Message(role="user", content="q")], "m"
    )
    assert response.content == "ok"
    assert recorded["provider"] == "deterministic"
    assert recorded["model"] == "m"


async def test_client_chat_tracker_failure_swallowed(monkeypatch):
    """用量记账失败绝不影响主链路。"""
    client = LLMClient()
    client.register_provider(DeterministicProvider("ok"))

    def boom(*args):
        raise RuntimeError("tracker down")

    monkeypatch.setattr(llm_client_mod, "record_usage", boom)
    response = await client.chat(
        "deterministic", [Message(role="user", content="q")], "m"
    )
    assert response.content == "ok"


async def test_client_health_check_unknown_provider():
    assert await LLMClient().health_check("nope") is False


async def test_client_health_check_delegates():
    client = LLMClient()
    client.register_provider(DeterministicProvider("ok"))
    assert await client.health_check("deterministic") is True


# ===== OpenAICompatibleProvider =====

def test_compat_init_and_name():
    provider = OpenAICompatibleProvider("newapi", "k", "https://x.com/")
    assert provider.base_url == "https://x.com"
    assert provider.name == "newapi"
    assert provider.api_key == "k"


class _FakeHttpResponse:
    status_code = 200
    text = "ok"

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHttpClient:
    """替代 httpx.AsyncClient：记录请求，返回预制响应。"""

    last_request = None
    responder = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json, headers):
        _FakeHttpClient.last_request = {"url": url, "payload": json, "headers": headers}
        return _FakeHttpClient.responder()


def _patch_http(monkeypatch, responder):
    _FakeHttpClient.last_request = None
    _FakeHttpClient.responder = responder
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpClient)


async def test_compat_chat_payload_and_parse(monkeypatch):
    def ok():
        return _FakeHttpResponse({
            "choices": [{
                "message": {"content": "hello", "tool_calls": [{"id": "1"}]},
                "finish_reason": "tool_calls",
            }],
            "usage": {"a": 1},
        })

    _patch_http(monkeypatch, ok)
    provider = OpenAICompatibleProvider("t", "k", "https://x.com")
    response = await provider.chat(
        [
            Message(role="user", content="q"),
            Message(role="tool", content="r", tool_call_id="c1"),
            Message(role="assistant", content="", tool_calls=[{"id": "1"}]),
        ],
        model="m",
        tools=[{"type": "function"}],
    )

    assert response.content == "hello"
    assert response.tool_calls == [{"id": "1"}]
    assert response.finish_reason == "tool_calls"
    assert response.usage == {"a": 1}
    request = _FakeHttpClient.last_request
    assert request["url"] == "https://x.com/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer k"
    assert request["payload"]["tools"] == [{"type": "function"}]
    assert request["payload"]["messages"][1]["tool_call_id"] == "c1"
    assert request["payload"]["messages"][2]["tool_calls"] == [{"id": "1"}]


async def test_compat_chat_empty_content_defaults(monkeypatch):
    def ok():
        return _FakeHttpResponse({
            "choices": [{"message": {"content": None}, "finish_reason": "stop"}],
        })

    _patch_http(monkeypatch, ok)
    provider = OpenAICompatibleProvider("t", "k", "https://x.com")
    response = await provider.chat([Message(role="user", content="q")], model="m")
    assert response.content == ""
    assert response.tool_calls is None
    assert response.usage == {}


async def test_compat_chat_http_error_enriched(monkeypatch):
    class BadResponse(_FakeHttpResponse):
        status_code = 500
        text = "server exploded"

        def __init__(self):
            pass

        def raise_for_status(self):
            raise RuntimeError("500")

    _patch_http(monkeypatch, BadResponse)
    provider = OpenAICompatibleProvider("t", "k", "https://x.com")
    with pytest.raises(Exception) as exc_info:
        await provider.chat([Message(role="user", content="q")], model="m")
    message = str(exc_info.value)
    assert "t API error" in message
    assert "500" in message
    assert "server exploded" not in message  # upstream body is deliberately excluded


async def test_compat_chat_connection_error_without_response(monkeypatch):
    def boom():
        raise RuntimeError("conn refused")

    _patch_http(monkeypatch, boom)
    provider = OpenAICompatibleProvider("t", "k", "https://x.com")
    with pytest.raises(Exception, match="conn refused"):
        await provider.chat([Message(role="user", content="q")], model="m")


async def test_compat_chat_broken_response_detail_swallowed(monkeypatch):
    """响应细节读取失败时内层 except 兜底，不掩盖原始错误。"""

    class WeirdResponse:
        @property
        def text(self):
            raise RuntimeError("no text")

        def raise_for_status(self):
            raise RuntimeError("bad status")

    def weird():
        return WeirdResponse()

    _patch_http(monkeypatch, weird)
    provider = OpenAICompatibleProvider("t", "k", "https://x.com")
    with pytest.raises(Exception, match="bad status"):
        await provider.chat([Message(role="user", content="q")], model="m")


async def test_compat_health_check_and_close():
    provider = OpenAICompatibleProvider("t", "k", "https://x.com")
    provider.chat = AsyncMock(return_value=LLMResponse(content="t", model="m"))
    assert await provider.health_check() is True
    provider.chat = AsyncMock(side_effect=Exception("down"))
    assert await provider.health_check() is False
    assert await provider.close() is None

