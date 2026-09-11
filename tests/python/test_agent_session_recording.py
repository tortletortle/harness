# -*- coding: utf-8 -*-
"""Agent Session/Turn 运行记录回归：coding_agent 每轮模型调用落一条 turn，
session 汇总 turns/usage/终态；记录失败只降级不影响执行。"""
from types import SimpleNamespace

import pytest

from authorization import AuthorizationManager
from agent_tools import AgentRuntimeContext
from capabilities import CapabilityRequest
from coding_agent import CodingAgentTool
from database import Database


PATCH_BAD = "这不是补丁块，第一轮故意非法"
PATCH_OK = (
    "<<<<<<< SEARCH\n"
    "    return 0\n"
    "=======\n"
    "    return 1\n"
    ">>>>>>> REPLACE\n"
)


class _Response:
    def __init__(self, content: str):
        self.content = content
        self.usage = {"prompt_tokens": 120, "completion_tokens": 45}


class _FakeGrantLLMClient:
    """替身 GrantLLMClient：按序返回预设响应，不触真模型。"""

    scripted: list = []

    def __init__(self, llm_client, authorization_manager, grant_id):
        pass

    async def chat(self, **kwargs):
        return _Response(_FakeGrantLLMClient.scripted.pop(0))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    db = Database(str(tmp_path / "eas.db"))
    auth = AuthorizationManager(workspace_root=str(tmp_path))
    grant = auth.issue_for_step(
        plan_id="plan-1",
        step=SimpleNamespace(step_id="step-1", tool="coding_agent",
                             inputs={"path": "src/x.py", "max_turns": 2, "output_mode": "patch"}),
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
        inputs={"path": "src/x.py", "max_turns": 2, "output_mode": "patch"},
        artifact_refs=[],
        allowed_tools=[],
        acceptance=[],
        grant_id=grant.grant_id,
    )
    return {"db": db, "auth": auth, "context": context, "request": request, "tmp": tmp_path}


def _make_agent(env, inner_calls):
    agent = CodingAgentTool(
        llm_client=None,
        authorization_manager=env["auth"],
        tool_registry=None,
        provider_name="test",
        model="test-model",
        database=env["db"],
    )

    async def _inner(context, request, tool_id, instruction, inputs):
        inner_calls.append((tool_id, instruction))
        if instruction == "read_file":
            return "ok", {"content": "def old():\n    return 0\n"}, None
        if instruction == "write_file":
            return "ok", {}, None
        return "error", None, "unknown"

    agent._inner_tool = _inner
    return agent


def test_two_turn_session_records_patch_error_then_applied(env, monkeypatch):
    """第一轮补丁非法（patch_error）→ 反馈重试 → 第二轮合法（applied）；
    session 记录 turns_used=2、status=completed。"""
    db = env["db"]
    _FakeGrantLLMClient.scripted = [PATCH_BAD, PATCH_OK]
    monkeypatch.setattr("coding_agent.GrantLLMClient", _FakeGrantLLMClient)

    inner_calls: list = []
    agent = _make_agent(env, inner_calls)
    result = asyncio_run(agent.run(env["request"], env["context"]))

    assert result.status == "completed", result.error
    assert db_last_session(db=env["db"])["status"] == "completed"
    session = db_last_session(env["db"])
    assert session["turns_used"] == 2

    turns = env["db"].list_agent_turns(session["session_id"])
    assert [t["turn_no"] for t in turns] == [1, 2]
    assert turns[0]["outcome"] == "patch_error"
    assert turns[1]["outcome"] == "applied"
    # token 记录来自响应 usage
    assert turns[1]["input_tokens"] == 120
    assert turns[1]["output_tokens"] == 45


def test_single_turn_failure_records_failed_session(env, monkeypatch):
    """两轮都非法补丁 → session failed，turns 记 patch_error。"""
    db = env["db"]
    _FakeGrantLLMClient.scripted = [PATCH_BAD, PATCH_BAD]
    monkeypatch.setattr("coding_agent.GrantLLMClient", _FakeGrantLLMClient)

    agent = _make_agent(env, [])
    result = asyncio_run(agent.run(env["request"], env["context"]))

    assert result.status == "failed"
    session = db_last_session(env["db"])
    assert session["status"] == "failed"
    assert session["error"]
    turns = env["db"].list_agent_turns(session["session_id"])
    assert [t["outcome"] for t in turns] == ["patch_error", "patch_error"]


def test_session_recording_failure_degrades_gracefully(env, monkeypatch):
    """DB 记录层故障（表损坏）不得影响 agent 执行主链路。"""
    db = env["db"]

    class _BrokenDB:
        def __getattr__(self, name):
            raise RuntimeError("db broken")

    _FakeGrantLLMClient.scripted = [PATCH_OK]
    monkeypatch.setattr("coding_agent.GrantLLMClient", _FakeGrantLLMClient)

    agent = _make_agent(env, [])
    agent.database = _BrokenDB()
    result = asyncio_run(agent.run(env["request"], env["context"]))
    assert result.status == "completed", result.error


def db_last_session(db: Database):
    sessions = db.list_agent_sessions(plan_id="plan-1")
    assert sessions, "应至少有一条 session 记录"
    return sessions[0]


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_database_session_turn_crud(tmp_path):
    """Database 层 CRUD：非法终态拒绝、turn outcome 回写、按 plan 过滤。"""
    db = Database(str(tmp_path / "sess.db"))
    db.create_agent_session("s1", plan_id="p1", step_id="st1", agent_tool="coding_agent")
    assert db.finish_agent_session("s1", "bogus") is False
    turn_id = db.record_agent_turn("s1", 1, outcome="model_ok", input_tokens=10, output_tokens=5)
    assert db.update_agent_turn_outcome(turn_id, "applied", {"k": "v"}) is True
    db.finish_agent_session("s1", "completed", turns_used=1, model_calls=1,
                            input_tokens=10, output_tokens=5)
    sessions = db.list_agent_sessions(plan_id="p1")
    assert sessions[0]["turns_used"] == 1
    assert sessions[0]["input_tokens"] == 10
    turns = db.list_agent_turns("s1")
    assert turns[0]["outcome"] == "applied"
    assert turns[0]["detail"] == {"k": "v"}
    assert db.list_agent_sessions(plan_id="p-none") == []
