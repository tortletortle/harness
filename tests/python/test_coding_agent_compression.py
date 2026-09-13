# -*- coding: utf-8 -*-
"""
coding_agent Turn 级压缩回归测试：全部离线。

覆盖（设计稿见 turn_compression_design.md v2）：
- _estimate_tokens：空/ASCII/CJK/混合
- _summarize_turn_facts：applied/patch_error/selftest/blocks/缺失/turn 前缀防串扰
- _compress_messages：折叠形态/尾部保留/幂等不嵌套
- 会话笔记：文件名 slug 防穿越、创建/追加/上限停写（workspace_override 指 tmp）
- 集成：多轮触发折叠 + 笔记落盘 + 首轮降级/指令过大 + 单轮零变更 + 笔记失败降级 + env 预算
"""
from types import SimpleNamespace

import pytest

from agent_tools import AgentRuntimeContext
from authorization import AuthorizationManager
from capabilities import CapabilityRequest
from coding_agent import (
    CodingAgentTool,
    _append_session_note,
    _compress_messages,
    _estimate_tokens,
    _session_note_filename,
    _summarize_turn_facts,
)
from llm_client import Message
from tools.workspace import reset_workspace_override, set_workspace_override


def _facts():
    return [
        {"key": "coding_agent_target", "value": "x.py"},
        {"key": "coding_agent_turn1_bytes", "value": 120},
        {"key": "coding_agent_turn2_patch_error", "value": "search_miss"},
        {"key": "coding_agent_turn3_bytes", "value": 200},
        {"key": "coding_agent_turn3_test_exit", "value": 1},
        {"key": "coding_agent_turn3_blocks_applied", "value": 2},
    ]


def _msgs(*contents):
    roles = ["system", "user", "assistant", "user", "assistant", "user",
             "assistant", "user", "assistant", "user"]
    return [Message(role=roles[i], content=c) for i, c in enumerate(contents)]


# ---- _estimate_tokens ----

def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 0
    assert _estimate_tokens(None) == 0


def test_estimate_tokens_ascii():
    assert _estimate_tokens("a" * 100) == 25


def test_estimate_tokens_cjk():
    assert _estimate_tokens("中文测试" * 10) == 40


def test_estimate_tokens_mixed():
    assert _estimate_tokens("ab中文") == 3  # 2 CJK + ceil(2/4)


# ---- _summarize_turn_facts ----

def test_summarize_applied():
    assert _summarize_turn_facts(_facts(), 1) == "applied(120B)"


def test_summarize_patch_error():
    assert _summarize_turn_facts(_facts(), 2) == "patch_error(search_miss)"


def test_summarize_combined():
    assert (
        _summarize_turn_facts(_facts(), 3)
        == "applied(200B); selftest_exit=1; blocks=2"
    )


def test_summarize_missing_turn():
    assert _summarize_turn_facts(_facts(), 9) == "done"


def test_summarize_turn_prefix_safety():
    # turn1 不能误匹配 turn10 的 facts
    facts = [{"key": "coding_agent_turn10_bytes", "value": 5}]
    assert _summarize_turn_facts(facts, 1) == "done"
    assert _summarize_turn_facts(facts, 10) == "applied(5B)"


# ---- _compress_messages ----

def test_compress_shape():
    msgs = _msgs("sys", "u1", "a1", "u2", "a2", "u3")
    out = _compress_messages(msgs, "[历史压缩] t1")
    assert len(out) == 5
    assert [m.content for m in out[:2]] == ["sys", "u1"]
    assert out[2].role == "user" and out[2].content.startswith("[历史压缩")
    assert [m.content for m in out[3:]] == ["a2", "u3"]


def test_compress_idempotent_no_nesting():
    first = _compress_messages(_msgs("sys", "u1", "a1", "u2", "a2", "u3"), "[历史压缩] t1")
    grown = first + [Message(role="assistant", content="a3"), Message(role="user", content="u4")]
    second = _compress_messages(grown, "[历史压缩] t1+t2")
    assert len(second) == 5
    assert sum(1 for m in second if m.content.startswith("[历史压缩")) == 1
    assert [m.content for m in second[3:]] == ["a3", "u4"]


def test_compress_short_passthrough():
    msgs = _msgs("sys", "u1")
    assert _compress_messages(msgs, "[历史压缩] x") == msgs


# ---- 会话笔记 ----

def test_note_filename_slug():
    assert (
        _session_note_filename("abcd1234-xxxx", "src/a.py")
        == "coding-agent-abcd1234-src_a.py.md"
    )


def test_note_filename_traversal_safe():
    name = _session_note_filename("abcd1234", "../../etc/x")
    assert "/" not in name and name.startswith("coding-agent-abcd1234-")
    assert _session_note_filename("abcd1234", "///") == "coding-agent-abcd1234-note.md"


def test_append_note_create_and_append(tmp_path):
    token = set_workspace_override(tmp_path)
    try:
        header, e1, e2 = "# h", "entry-one", "entry-two"
        p1 = _append_session_note("n.md", header, e1)
        p2 = _append_session_note("n.md", header, e2)
        assert p1 == p2
        text = (tmp_path / "data" / "knowledge" / "notes" / "n.md").read_text(encoding="utf-8")
        assert text == "# h\nentry-one\nentry-two\n"
    finally:
        reset_workspace_override(token)


def test_append_note_capped(tmp_path, monkeypatch):
    import coding_agent as ca

    token = set_workspace_override(tmp_path)
    try:
        monkeypatch.setattr(ca, "_SESSION_NOTE_MAX_CHARS", 20)
        assert _append_session_note("c.md", "H" * 18, "e1") != "capped"
        assert _append_session_note("c.md", "H" * 18, "e2-long-entry") == "capped"
    finally:
        reset_workspace_override(token)


# ---- 集成：多轮压缩 + 会话笔记 + 首轮降级 ----

PATCH_BAD = "这不是补丁块，故意非法"
PATCH_OK = (
    "<<<<<<< SEARCH\n"
    "    return 0\n"
    "=======\n"
    "    return 1\n"
    ">>>>>>> REPLACE\n"
)
FILE_BIG = "    return 0\n" + "z" * 20000  # 读回显按 12000 字符封顶 → 每轮约 +3000 tokens；首含 SEARCH 行供 PATCH_OK 命中
FILE_SMALL = "def old():\n    return 0\n"


class _CaptureClient:
    """替身 GrantLLMClient：按序返回预设响应，并记录每轮实际发送的消息。"""

    scripted: list = []
    seen: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def chat(self, **kwargs):
        _CaptureClient.seen.append(kwargs.get("messages"))
        return SimpleNamespace(content=_CaptureClient.scripted.pop(0), usage={})


def _make_run_env(tmp_path, monkeypatch, inputs):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    auth = AuthorizationManager(workspace_root=str(tmp_path))
    grant = auth.issue_for_step(
        plan_id="plan-1",
        step=SimpleNamespace(step_id="step-1", tool="coding_agent", inputs=dict(inputs)),
        workspace_root=str(tmp_path),
    )
    context = AgentRuntimeContext(
        llm_client=None,
        authorization_manager=auth,
        provider_name="test",
        model="test-model",
        grant_id=grant.grant_id,
        system_prompt="",
        plan_id="plan-1",
    )
    request = CapabilityRequest(
        work_item_id="step-1",
        goal="改 return 0 为 return 1",
        inputs=dict(inputs),
        artifact_refs=[],
        allowed_tools=[],
        acceptance=[],
        grant_id=grant.grant_id,
    )
    return context, request


def _make_run_agent(file_content):
    agent = CodingAgentTool(
        llm_client=None,
        authorization_manager=None,
        tool_registry=None,
        provider_name="test",
        model="test-model",
        database=None,
    )

    async def _inner(context, request, tool_id, instruction, inputs):
        if instruction == "read_file":
            return "ok", {"content": file_content}, None
        if instruction == "write_file":
            return "ok", {}, None
        return "error", None, "unknown"

    agent._inner_tool = _inner
    return agent


def _asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def _fact_map(result):
    return {f["key"]: f["value"] for f in (result.facts or [])}


def test_multiturn_fold_and_note(tmp_path, monkeypatch):
    """4 轮坏补丁 + 预算 7500：T3 折叠 turn1，T4 折叠 turn1-2，笔记落盘 2 条事件。"""
    inputs = {"path": "src/x.py", "max_turns": 4, "output_mode": "patch",
              "max_input_tokens": 7500}
    context, request = _make_run_env(tmp_path, monkeypatch, inputs)
    _CaptureClient.scripted = [PATCH_BAD] * 4
    _CaptureClient.seen = []
    monkeypatch.setattr("coding_agent.GrantLLMClient", _CaptureClient)
    agent = _make_run_agent(FILE_BIG)

    result = _asyncio_run(agent.run(request, context))

    assert result.status == "failed"  # 4 轮补丁全非法，最终失败（但压缩已发生）
    assert len(_CaptureClient.seen) == 4
    # T1/T2 不折叠（T2 时仅 1 轮完成，无可折叠中间轮）
    assert all("[历史压缩" not in (m.content or "") for m in _CaptureClient.seen[0])
    assert all("[历史压缩" not in (m.content or "") for m in _CaptureClient.seen[1])
    # T3 折叠 turn1，T4 折叠 turn1-2
    fold3 = [m for m in _CaptureClient.seen[2] if (m.content or "").startswith("[历史压缩")]
    fold4 = [m for m in _CaptureClient.seen[3] if (m.content or "").startswith("[历史压缩")]
    assert len(fold3) == 1 and len(fold4) == 1
    assert "turn1: patch_error(" in fold3[0].content
    assert "第1–2轮" in fold4[0].content and "turn2: patch_error(" in fold4[0].content
    # 折叠后长度收敛：T4 发送轮数不再增长
    assert len(_CaptureClient.seen[3]) == 5
    # facts + 笔记
    facts = _fact_map(result)
    assert "coding_agent_compressed" in facts
    assert "coding_agent_note" in facts
    notes = list((tmp_path / "data" / "knowledge" / "notes").glob("coding-agent-*.md"))
    assert len(notes) == 1
    text = notes[0].read_text(encoding="utf-8")
    assert text.count("压缩事件") == 2
    assert "turn1: patch_error(" in text


def test_first_turn_degrade(tmp_path, monkeypatch):
    """首轮超预算：动态收紧快照后继续，单轮完成。"""
    inputs = {"path": "src/x.py", "max_turns": 1, "output_mode": "patch",
              "max_input_tokens": 1000}
    context, request = _make_run_env(tmp_path, monkeypatch, inputs)
    _CaptureClient.scripted = [PATCH_OK]
    _CaptureClient.seen = []
    monkeypatch.setattr("coding_agent.GrantLLMClient", _CaptureClient)
    agent = _make_run_agent(FILE_BIG)

    result = _asyncio_run(agent.run(request, context))

    assert result.status == "completed", result.error
    facts = _fact_map(result)
    assert 0 < facts["coding_agent_first_turn_degraded"] < 12000
    sent_u1 = _CaptureClient.seen[0][1].content
    assert "...[内容已截断]" in sent_u1
    assert len(sent_u1) < 12000


def test_first_turn_instruction_too_big_fails(tmp_path, monkeypatch):
    """指令本身超预算：fail-closed，一次模型调用都不发。"""
    inputs = {"path": "src/x.py", "max_turns": 1, "output_mode": "patch",
              "max_input_tokens": 1000, "instruction": "y" * 20000}
    context, request = _make_run_env(tmp_path, monkeypatch, inputs)
    _CaptureClient.scripted = [PATCH_OK]
    _CaptureClient.seen = []
    monkeypatch.setattr("coding_agent.GrantLLMClient", _CaptureClient)
    agent = _make_run_agent(FILE_SMALL)

    result = _asyncio_run(agent.run(request, context))

    assert result.status == "failed"
    assert "指令本身过大" in result.error
    assert _CaptureClient.seen == []
    assert "coding_agent_first_turn_over_budget" in _fact_map(result)


def test_single_turn_default_budget_unchanged(tmp_path, monkeypatch):
    """max_turns=1 + 缺省预算：无压缩、无笔记、无新 facts（向后兼容）。"""
    inputs = {"path": "src/x.py", "max_turns": 1, "output_mode": "patch"}
    context, request = _make_run_env(tmp_path, monkeypatch, inputs)
    _CaptureClient.scripted = [PATCH_OK]
    _CaptureClient.seen = []
    monkeypatch.setattr("coding_agent.GrantLLMClient", _CaptureClient)
    agent = _make_run_agent(FILE_SMALL)

    result = _asyncio_run(agent.run(request, context))

    assert result.status == "completed", result.error
    assert len(_CaptureClient.seen) == 1
    assert len(_CaptureClient.seen[0]) == 2  # system + 首轮 user，原样
    keys = set(_fact_map(result))
    assert not (keys & {"coding_agent_compressed", "coding_agent_note",
                        "coding_agent_first_turn_degraded"})
    assert not (tmp_path / "data").exists()


def test_note_failure_degrades(tmp_path, monkeypatch):
    """笔记落盘失败：压缩照常，主链路不受影响。"""
    inputs = {"path": "src/x.py", "max_turns": 4, "output_mode": "patch",
              "max_input_tokens": 7500}
    context, request = _make_run_env(tmp_path, monkeypatch, inputs)
    _CaptureClient.scripted = [PATCH_BAD] * 4
    _CaptureClient.seen = []
    monkeypatch.setattr("coding_agent.GrantLLMClient", _CaptureClient)

    def _boom(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr("coding_agent._append_session_note", _boom)
    agent = _make_run_agent(FILE_BIG)

    result = _asyncio_run(agent.run(request, context))

    assert result.status == "failed"  # 仍是补丁失败，而非笔记失败
    assert "补丁未应用" in result.error
    facts = _fact_map(result)
    assert "coding_agent_compressed" in facts
    assert "coding_agent_note" not in facts


def test_env_budget_override(tmp_path, monkeypatch):
    """环境变量预算生效：未传 inputs 时按 env 触发首轮降级。"""
    monkeypatch.setenv("EAS_CODING_AGENT_MAX_INPUT_TOKENS", "1000")
    inputs = {"path": "src/x.py", "max_turns": 1, "output_mode": "patch"}
    context, request = _make_run_env(tmp_path, monkeypatch, inputs)
    _CaptureClient.scripted = [PATCH_OK]
    _CaptureClient.seen = []
    monkeypatch.setattr("coding_agent.GrantLLMClient", _CaptureClient)
    agent = _make_run_agent(FILE_BIG)

    result = _asyncio_run(agent.run(request, context))

    assert result.status == "completed", result.error
    assert "coding_agent_first_turn_degraded" in _fact_map(result)

