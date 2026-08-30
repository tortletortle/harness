"""CodingAgentTool 与 agent_tool 步骤调度测试（确定性，不调真实 LLM）。

覆盖最小闭环的结构边界：

1. 正常路径：Grant → 受权读取 → 单轮模型 → 受权写回 → AcceptanceRunner 确定性验收。
2. 模型不能重定向写入路径（Runtime 决定写哪里）。
3. 内部工具未获 Grant 授权 → 拒绝执行（tool_not_granted），文件保持原状。
4. Grant 预算耗尽 → 拒绝执行（budget_exceeded）。
5. 越界路径在 Grant 校验层被拒（资源路径越界，blocked）。
6. 非法模型输出安全失败（不伪装成功）。
7. 单轮预算：模型调用恰好 1 次，第二次调用被 Grant 拒绝。
8. agent_step_grant_params：只对 agent_tool 步骤放行额外工具与预算。
9. agent_tool 调度对任意 agent Capability 通用（FakeAgentTool 回归）。
10. 内部工具调用写入审计日志并关联 grant_id。
"""

import json
import sqlite3

import pytest

from agent_tools import AgentRuntimeContext, FakeAgentTool
from authorization import AuthorizationManager, GrantLimits, agent_step_grant_params
from capabilities import CapabilityRegistry, CapabilityRequest, CapabilityResult
from coding_agent import CodingAgentTool
from executor import DefaultExecutor
from llm_client import DeterministicProvider, LLMClient
from models import Step
from tools import get_registry

ORIGINAL = "def greet():\n    return 1\n"
EDITED = "def greet():\n    return 'hello, world'\n"
TARGET = "src/app.py"


def _model_response(target_path: str = TARGET, content: str = EDITED, status: str = "completed") -> str:
    return json.dumps(
        {
            "status": status,
            "output": {"path": target_path, "content": content},
            "artifacts": [],
            "facts": [],
            "error": None,
        },
        ensure_ascii=False,
    )


def _make_step(**extra_inputs) -> Step:
    inputs = {
        "path": TARGET,
        "instruction": "把返回值改成 'hello, world'",
        "allowed_tools": ["text_editor"],
    }
    inputs.update(extra_inputs)
    return Step(
        order=1,
        description="修改 greet 函数返回值",
        tool="coding_agent",
        inputs=inputs,
        check_criteria="",
        acceptance=[{"type": "content_contains", "path": TARGET, "text": "hello, world"}],
    )


def _make_env(monkeypatch, tmp_path, response: str, database=None):
    """构建 manager + CodingAgentTool + 注册后的 executor（确定性 provider）。"""
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    manager = AuthorizationManager(workspace_root=tmp_path)
    llm = LLMClient()
    provider = DeterministicProvider(response)
    llm.register_provider(provider)
    agent = CodingAgentTool(
        llm_client=llm,
        authorization_manager=manager,
        tool_registry=get_registry(),
        provider_name="deterministic",
        model="test-model",
        database=database,
    )
    registry = CapabilityRegistry()
    registry.register(agent)
    executor = DefaultExecutor(
        llm_client=llm,
        model_config={"primary": {"provider": "deterministic", "model": "test-model"}},
        tool_registry=get_registry(),
        database=database,
        authorization_manager=manager,
        capability_registry=registry,
    )
    return manager, agent, executor


def _seed_file(tmp_path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text(ORIGINAL, encoding="utf-8")


@pytest.mark.asyncio
async def test_coding_agent_edits_granted_file_and_passes_acceptance(monkeypatch, tmp_path):
    _seed_file(tmp_path)
    manager, agent, executor = _make_env(monkeypatch, tmp_path, _model_response())
    step = _make_step()
    grant = manager.issue_for_step(
        "plan-ok",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=1),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-ok")

    assert result["success"], result
    assert result["check_passed"] is True
    assert result["acceptance_status"] == "passed"
    assert "hello, world" in (tmp_path / "src" / "app.py").read_text(encoding="utf-8")
    # 单轮：模型调用恰好 1 次；外层调度 1 次 + 内部 read + write = 3 次工具调用。
    assert grant.used_model_calls == 1
    assert grant.used_tool_calls == 3
    # executor 把 agent 结果整体包在 output 字段里（与普通工具同构）。
    agent_output = result["output"]["output"]
    assert agent_output["agent_status"] == "completed"
    assert agent_output["path"] == TARGET


@pytest.mark.asyncio
async def test_coding_agent_rejects_path_redirection(monkeypatch, tmp_path):
    _seed_file(tmp_path)
    manager, agent, executor = _make_env(
        monkeypatch,
        tmp_path,
        _model_response(target_path="src/evil.py", content="pwned"),
    )
    step = _make_step()
    grant = manager.issue_for_step(
        "plan-redirect",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=1),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-redirect")

    assert not result["success"]
    assert "非授权路径" in result["error"]
    # 目标文件保持原状，越界文件从未出现。
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == ORIGINAL
    assert not (tmp_path / "src" / "evil.py").exists()
    # 只消耗了外层调度 + 读，写入从未发生。
    assert grant.used_tool_calls == 2


@pytest.mark.asyncio
async def test_coding_agent_denied_without_inner_tool_grant(monkeypatch, tmp_path):
    _seed_file(tmp_path)
    manager, agent, executor = _make_env(monkeypatch, tmp_path, _model_response())
    step = _make_step()
    # 不传 extra_allowed_tools：Grant 只允许 coding_agent 本身。
    grant = manager.issue_for_step("plan-no-inner", step)

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-no-inner")

    assert not result["success"]
    assert "tool_not_granted" in result["error"]
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.asyncio
async def test_coding_agent_denied_when_budget_exhausted(monkeypatch, tmp_path):
    _seed_file(tmp_path)
    manager, agent, executor = _make_env(monkeypatch, tmp_path, _model_response())
    step = _make_step()
    # 只有 1 次工具调用额度：外层调度用掉后，内部读取必然被拒。
    grant = manager.issue_for_step(
        "plan-budget",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=1, max_model_calls=1),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-budget")

    assert not result["success"]
    assert "budget_exceeded" in result["error"]
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.asyncio
async def test_out_of_bounds_path_rejected_at_grant_layer(monkeypatch, tmp_path):
    _seed_file(tmp_path)
    manager, agent, executor = _make_env(monkeypatch, tmp_path, _model_response())
    step = _make_step(path="../evil.txt")
    grant = manager.issue_for_step(
        "plan-oob",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=1),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-oob")

    assert not result["success"]
    assert result["blocked"] is True
    assert "越界" in result["error"]
    assert not (tmp_path.parent / "evil.txt").exists()


@pytest.mark.asyncio
async def test_coding_agent_fails_closed_on_invalid_model_json(monkeypatch, tmp_path):
    _seed_file(tmp_path)
    manager, agent, executor = _make_env(monkeypatch, tmp_path, "这不是 JSON")
    step = _make_step()
    grant = manager.issue_for_step(
        "plan-badjson",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=1),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-badjson")

    assert not result["success"]
    assert "合法 JSON" in result["error"]
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.asyncio
async def test_coding_agent_model_budget_is_single_turn(monkeypatch, tmp_path):
    _seed_file(tmp_path)
    manager, agent, executor = _make_env(monkeypatch, tmp_path, _model_response())
    step = _make_step()
    grant = manager.issue_for_step(
        "plan-single",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=8, max_model_calls=1),
    )
    context = AgentRuntimeContext(
        llm_client=agent.llm_client,
        authorization_manager=manager,
        provider_name="deterministic",
        model="test-model",
        grant_id=grant.grant_id,
    )
    request = CapabilityRequest(
        work_item_id="w1",
        goal="edit",
        inputs={"path": TARGET, "instruction": "x"},
        grant_id=grant.grant_id,
    )

    first = await agent.run(request, context)
    assert first.status == "completed"

    # 同一 Grant 上第二次模型调用：预算已用尽，必须失败（单轮硬约束）。
    second = await agent.run(request, context)
    assert second.status == "failed"
    assert "budget_exceeded" in second.error


def test_agent_step_grant_params_scope():
    agent = CodingAgentTool(
        llm_client=None,
        authorization_manager=None,
        tool_registry=None,
        provider_name="p",
        model="m",
    )
    step = _make_step()
    extra, limits = agent_step_grant_params(step, agent)
    assert extra == ["text_editor"]
    assert limits.max_tool_calls == 1 + 4
    assert limits.max_model_calls == 1

    # 普通工具步骤：inputs.allowed_tools 必须被忽略，防止借道扩权。
    plain = Step(
        order=1,
        description="d",
        tool="file_creator",
        inputs={"allowed_tools": ["command_runner"]},
        check_criteria="",
    )
    extra_plain, limits_plain = agent_step_grant_params(plain, None)
    assert extra_plain == []
    assert limits_plain is None


@pytest.mark.asyncio
async def test_executor_dispatches_any_agent_capability(monkeypatch, tmp_path):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    manager = AuthorizationManager(workspace_root=tmp_path)
    llm = LLMClient()
    llm.register_provider(DeterministicProvider("unused"))
    registry = CapabilityRegistry()
    registry.register(
        FakeAgentTool(result=CapabilityResult(status="completed", output={"message": "ok"}))
    )
    executor = DefaultExecutor(
        llm_client=llm,
        model_config={"primary": {"provider": "deterministic", "model": "m"}},
        tool_registry=get_registry(),
        authorization_manager=manager,
        capability_registry=registry,
    )
    step = Step(
        order=1,
        description="fake work",
        tool="fake_agent",
        inputs={},
        check_criteria="",
    )
    grant = manager.issue_for_step("plan-fake", step, limits=GrantLimits(max_tool_calls=2, max_model_calls=1))

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-fake")

    assert result["success"] is True
    assert result["output"]["output"]["agent_status"] == "completed"
    assert result["output"]["output"]["message"] == "ok"


@pytest.mark.asyncio
async def test_coding_agent_inner_calls_are_audited_with_grant(monkeypatch, tmp_path):
    _seed_file(tmp_path)
    db_path = tmp_path / "audit.db"
    from database import Database

    database = Database(str(db_path))
    manager, agent, executor = _make_env(
        monkeypatch, tmp_path, _model_response(), database=database
    )
    step = _make_step()
    grant = manager.issue_for_step(
        "plan-audit",
        step,
        extra_allowed_tools=["text_editor"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=1),
    )

    result = await executor.execute_step(step, grant_id=grant.grant_id, plan_id="plan-audit")

    assert result["success"], result
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT tool_id, instruction, actor, grant_id FROM audit_logs WHERE grant_id = ?",
            (grant.grant_id,),
        ).fetchall()
    inner = [r for r in rows if r[2] == "agent:coding_agent"]
    assert {r[1] for r in inner} == {"coding_agent:read_file", "coding_agent:write_file"}
    assert all(r[0] == "text_editor" for r in inner)
