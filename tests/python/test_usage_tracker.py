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
    assert summary["cached_tokens"] == 0
    assert summary["recent"][0]["model"] == "m-1"


def test_openai_nested_cached_tokens():
    record_usage("openai", "gpt-4o", {
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 800},
    })
    summary = get_summary()
    assert summary["input_tokens"] == 1000
    assert summary["cached_tokens"] == 800
    assert summary["cache_write_tokens"] == 0
    assert summary["cache_hit_rate"] == 0.8
    assert summary["recent"][0]["cached_tokens"] == 800


def test_deepseek_prompt_cache_hit_tokens():
    record_usage("deepseek", "deepseek-chat", {
        "prompt_tokens": 200,
        "completion_tokens": 10,
        "prompt_cache_hit_tokens": 150,
    })
    summary = get_summary()
    assert summary["cached_tokens"] == 150
    assert summary["cache_hit_rate"] == 0.75


def test_anthropic_cache_read_counts_as_input():
    record_usage("anthropic", "claude", {
        "input_tokens": 20,
        "output_tokens": 5,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 30,
    })
    summary = get_summary()
    assert summary["input_tokens"] == 150
    assert summary["cached_tokens"] == 100
    assert summary["cache_write_tokens"] == 30
    assert summary["total_tokens"] == 155


def test_legacy_usage_db_gains_cache_columns(tmp_path, monkeypatch):
    import sqlite3
    from utils.usage_tracker import close_thread_conn

    close_thread_conn()
    db_dir = tmp_path / "data" / "usage"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(db_dir / "llm_usage.db")
    conn.execute(
        "CREATE TABLE llm_usage ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, provider TEXT, model TEXT, "
        "phase TEXT, plan_id TEXT, input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER)"
    )
    conn.execute(
        "INSERT INTO llm_usage (ts, provider, model, phase, plan_id, input_tokens, output_tokens, total_tokens) "
        "VALUES ('t', 'p', 'm', 'adhoc', NULL, 1, 1, 2)"
    )
    conn.commit()
    conn.close()

    record_usage("p", "m", {
        "prompt_tokens": 10,
        "completion_tokens": 1,
        "prompt_tokens_details": {"cached_tokens": 4},
    })
    summary = get_summary()
    assert summary["calls"] == 2
    assert summary["cached_tokens"] == 4
    close_thread_conn()
