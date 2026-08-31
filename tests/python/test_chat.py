# -*- coding: utf-8 -*-
"""对话助手（chat 会话层）测试：工具调用循环、授权边界、会话管理。"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parent.parent.parent / "python"
sys.path.insert(0, str(PYTHON_DIR))

from chat import ChatManager, ChatSession  # noqa: E402
from authorization import AuthorizationManager  # noqa: E402
from capabilities import CapabilityRegistry  # noqa: E402
from llm_client import LLMClient, LLMProvider, LLMResponse  # noqa: E402
from tools import get_registry  # noqa: E402


class _ToolCallsProvider(LLMProvider):
    """按调用次序返回预设响应的测试桩（记录 messages 与 tools）。"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    @property
    def name(self):
        return "tc"

    async def chat(self, messages, model, temperature=0.7, max_tokens=4000, tools=None, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]

    async def health_check(self):
        return True


def _resp_text(text):
    return LLMResponse(content=text, model="m", finish_reason="stop")


def _resp_tool(name, args: dict):
    return LLMResponse(
        content="",
        model="m",
        finish_reason="tool_calls",
        tool_calls=[
            {"id": "tc1", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
        ],
    )


@pytest.fixture()
def chat_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    manager = AuthorizationManager(workspace_root=tmp_path)
    from capabilities import ToolCapabilityAdapter

    real_registry = get_registry()
    registry = CapabilityRegistry()
    for meta in real_registry.list_tools():
        tool_id = meta.get("tool_id")
        if tool_id and tool_id != "manual_tool":
            registry.register(ToolCapabilityAdapter(real_registry, tool_id))
    return manager, registry


def _make_chat(manager, registry, responses, chat_cfg=None):
    llm = LLMClient()
    provider = _ToolCallsProvider(responses)
    llm.register_provider(provider)
    cfg = {"chat": dict({"provider": "tc", "model": "m", "temperature": 0.2}, **(chat_cfg or {}))}
    mgr = ChatManager(llm, registry, manager, cfg)
    return mgr, provider


@pytest.mark.asyncio
async def test_chat_tool_round_then_reply(chat_env, tmp_path):
    """工具调用一轮（project_memory save）→ 第二轮纯文本回复 → 工具真实落盘。"""
    manager, registry = chat_env
    mgr, provider = _make_chat(
        manager, registry, [_resp_tool("project_memory", {"title": "进度", "content": "今天完成了多轮闭环"}), _resp_text("已记录")],
        chat_cfg={"max_tool_calls": 10},
    )
    session = mgr.get_or_create("s1")

    result = await session.ask("帮我记一下：今天完成了多轮闭环")

    assert result["reply"] == "已记录"
    assert len(result["tool_calls_made"]) == 1
    tc = result["tool_calls_made"][0]
    assert tc["tool"] == "project_memory"
    assert tc["result"]["success"] is True
    # 记忆文件真实写入工作区
    mem = tmp_path / "PROJECT_MEMORY.md"
    assert mem.exists() and "多轮闭环" in mem.read_text(encoding="utf-8")
    # provider 第 2 次调用收到工具结果消息
    assert len(provider.calls) == 2
    roles = [m["role"] for m in provider.calls[1]["messages"]]
    assert roles[-1] == "tool"


@pytest.mark.asyncio
async def test_chat_no_tool_call_direct_reply(chat_env):
    manager, registry = chat_env
    mgr, provider = _make_chat(manager, registry, [_resp_text("你好！")])
    session = mgr.get_or_create("s2")

    result = await session.ask("你好")

    assert result["reply"] == "你好！"
    assert result["tool_calls_made"] == []


@pytest.mark.asyncio
async def test_chat_unavailable_tool_reports_error(chat_env):
    """模型调用了不存在的工具 → 工具结果返回错误 → 对话继续给出回复。"""
    manager, registry = chat_env
    mgr, provider = _make_chat(
        manager, registry, [_resp_tool("nope_tool", {}), _resp_text("抱歉，该工具不可用")]
    )
    session = mgr.get_or_create("s3")

    result = await session.ask("随便")

    assert result["reply"] == "抱歉，该工具不可用"
    assert result["tool_calls_made"][0]["result"]["success"] is False
    assert "不可用" in result["tool_calls_made"][0]["result"]["error"]


@pytest.mark.asyncio
async def test_chat_excludes_external_and_manual(chat_env):
    """external 类与 manual_tool 默认不在助手工具白名单。"""
    manager, registry = chat_env
    mgr, provider = _make_chat(manager, registry, [_resp_text("hi")])
    session = mgr.get_or_create("s4")

    assert "http_api" not in session.allowed_tools
    assert "github_operator" not in session.allowed_tools
    assert "manual_tool" not in session.allowed_tools
    assert "project_memory" in session.allowed_tools


@pytest.mark.asyncio
async def test_chat_grant_budget_exhausts(chat_env):
    """工具调用预算耗尽后，后续调用被授权拒绝（错误反馈给模型）。"""
    manager, registry = chat_env
    # max_tool_calls=1：第一次工具调用后预算耗尽
    responses = [
        _resp_tool("project_memory", {"title": "t1", "content": "c1"}),
        _resp_tool("project_memory", {"title": "t2", "content": "c2"}),
        _resp_text("完成"),
    ]
    mgr, provider = _make_chat(
        manager, registry, responses, chat_cfg={"max_tool_calls": 1, "max_tool_rounds": 3}
    )
    session = mgr.get_or_create("s5")

    result = await session.ask("记两次")

    assert result["reply"] == "完成"
    # 第二次工具调用被拒（预算）
    assert result["tool_calls_made"][1]["result"]["success"] is False
    assert "已用尽" in result["tool_calls_made"][1]["result"]["error"]


def test_chat_manager_sessions_lifecycle(chat_env):
    manager, registry = chat_env
    mgr, _ = _make_chat(manager, registry, [_resp_text("ok")])
    s1 = mgr.get_or_create("a")
    assert mgr.get_or_create("a") is s1
    stats = mgr.stats()
    assert stats["sessions"] == 1
    assert mgr.remove("a") is True
    assert mgr.stats()["sessions"] == 0


@pytest.mark.asyncio
async def test_chat_memory_injected_into_first_prompt(chat_env, tmp_path):
    """项目记忆存在时，首轮提示词包含记忆内容（助手知道项目状态）。"""
    (tmp_path / "PROJECT_MEMORY.md").write_text("上次执行：步骤 6 失败", encoding="utf-8")
    manager, registry = chat_env
    mgr, provider = _make_chat(manager, registry, [_resp_text("知道了")])
    session = mgr.get_or_create("s-mem")

    await session.ask("继续")

    system = provider.calls[0]["messages"][0]["content"]
    assert "上次执行：步骤 6 失败" in system
