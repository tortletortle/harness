# -*- coding: utf-8 -*-
"""
usage_tracker 回归测试：记账归一化、上下文绑定、LLMClient 钩子、预算查询。
全部离线；使用临时工作区存放 data/usage/llm_usage.db。
"""
import pytest

from tools import code_context  # noqa: F401  # 确保 tools 包可用
from utils import usage_tracker
from utils.usage_tracker import (
    clear_context,
    get_context,
    get_summary,
    plan_total_tokens,
    record_usage,
    set_context,
)


@pytest.fixture(autouse=True)
def tmp_usage_ws(tmp_path, monkeypatch):
    """每个用例独立的工作区/数据库。"""
    monkeypatch.setattr("tools.workspace.resolve_workspace_root", lambda: tmp_path)
    clear_context()
    yield tmp_path
    clear_context()


def test_record_and_summary_openai_style():
    set_context(phase="executor", plan_id="p-1")
    record_usage("newapi", "gpt-x", {"prompt_tokens": 100, "completion_tokens": 40})
    summary = get_summary(plan_id="p-1")
    assert summary["calls"] == 1
    assert summary["input_tokens"] == 100
    assert summary["output_tokens"] == 40
    assert summary["total_tokens"] == 140
    assert summary["recent"][0]["plan_id"] == "p-1"
    assert summary["recent"][0]["phase"] == "executor"


def test_record_anthropic_style_tokens():
    record_usage("bbapi", "claude-x", {"input_tokens": 7, "output_tokens": 3}, phase="designer")
    summary = get_summary()
    assert summary["total_tokens"] == 10
    assert summary["by_phase"] == [{"phase": "designer", "total_tokens": 10, "calls": 1}]


def test_record_missing_usage_defaults_to_zero():
    record_usage("p", "m", None)
    summary = get_summary()
    assert summary["calls"] == 1
    assert summary["total_tokens"] == 0


def test_record_never_raises_on_garbage():
    class Weird:
        pass

    record_usage("p", "m", Weird())  # 非 dict usage → 尽力归一化，不抛
    record_usage("p", "m", {"prompt_tokens": "abc", "completion_tokens": None})  # 坏值 → 0
    summary = get_summary()
    assert summary["calls"] == 2
    assert summary["total_tokens"] == 0


def test_plan_isolation_and_total():
    record_usage("p", "m", {"total_tokens": 11}, phase="executor", plan_id="planA")
    record_usage("p", "m", {"total_tokens": 22}, phase="executor", plan_id="planA")
    record_usage("p", "m", {"total_tokens": 5}, phase="executor", plan_id="planB")
    assert plan_total_tokens("planA") == 33
    assert plan_total_tokens("planB") == 5
    assert plan_total_tokens("planC") == 0


def test_context_set_and_clear():
    set_context(plan_id="p9", phase="executor")
    assert get_context() == {"plan_id": "p9", "phase": "executor"}
    clear_context()
    assert get_context() == {}
    # 清空后记账落到 adhoc，且不带 plan_id
    record_usage("p", "m", {"total_tokens": 1})
    summary = get_summary()
    assert summary["recent"][0]["phase"] == "adhoc"
    assert summary["recent"][0]["plan_id"] is None


def test_llmclient_hook_records_usage(tmp_usage_ws):
    """LLMClient.chat 统一钩子：任意 provider 调用后自动记账。"""
    import asyncio
    from llm_client import LLMClient, LLMProvider, LLMResponse, Message

    class DummyProvider(LLMProvider):
        def __init__(self):
            self._name = "dummy"

        @property
        def name(self):
            return self._name

        async def chat(self, messages, model, temperature=0.7, max_tokens=4000, **kwargs):
            return LLMResponse(
                content="ok", model=model,
                usage={"prompt_tokens": 12, "completion_tokens": 8},
                finish_reason="stop", tool_calls=None,
            )

        async def health_check(self):
            return True

    set_context(phase="executor", plan_id="hook-plan")
    client = LLMClient()
    client.register_provider(DummyProvider())
    response = asyncio.run(client.chat("dummy", [Message(role="user", content="hi")], "m-1"))
    assert response.content == "ok"

    summary = get_summary(plan_id="hook-plan")
    assert summary["calls"] == 1
    assert summary["total_tokens"] == 20
    assert summary["recent"][0]["model"] == "m-1"
