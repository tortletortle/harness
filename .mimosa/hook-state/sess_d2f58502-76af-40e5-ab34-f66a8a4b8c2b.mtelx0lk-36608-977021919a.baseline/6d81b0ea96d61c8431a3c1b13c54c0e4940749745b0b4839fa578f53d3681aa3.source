"""
P0-a / P0-b / P2 回归测试：

- ApprovalManager：创建 → 挂起 → resolve（批准/拒绝/幂等/超时/清理/批量拒绝）
- Executor：blocked 结构化字段（第二道守卫）与 retryable 错误分类
"""

import asyncio
import pytest

from api.approval import ApprovalManager, ApprovalTimeout, ApprovalRequest
from executor import DefaultExecutor, LEGACY_BLOCKED_ERROR_CODES
from models import Step, StepStatus
from llm_client import LLMClient


# ===== ApprovalManager =====

def test_approval_manager_only_required_tools():
    mgr = ApprovalManager(required_tools=["command_runner"], timeout_seconds=5)
    assert mgr.is_required("command_runner")
    assert not mgr.is_required("file_creator")


@pytest.mark.asyncio
async def test_approval_approve_resumes_waiter():
    mgr = ApprovalManager(required_tools=["command_runner"], timeout_seconds=5)
    req = mgr.create(plan_id="p1", step_id="s1", tool_id="command_runner", instruction="run", inputs={})

    async def decide_later():
        await asyncio.sleep(0.01)
        assert mgr.resolve(req.approval_id, True, "ok")

    task = asyncio.create_task(decide_later())
    resolved = await mgr.wait(req.approval_id)
    await task
    assert resolved.approved is True
    assert resolved.comment == "ok"
    assert resolved.resolved
    mgr.cleanup(req.approval_id)
    assert mgr.list_pending() == []


@pytest.mark.asyncio
async def test_approval_reject_resumes_waiter():
    mgr = ApprovalManager(timeout_seconds=5)
    req = mgr.create("p1", "s1", "command_runner", "run", {})
    assert mgr.resolve(req.approval_id, False, "too risky")
    resolved = await mgr.wait(req.approval_id)
    assert resolved.approved is False
    assert resolved.comment == "too risky"


@pytest.mark.asyncio
async def test_approval_timeout_is_rejection():
    mgr = ApprovalManager(timeout_seconds=0.05)
    req = mgr.create("p1", "s1", "command_runner", "run", {})
    with pytest.raises(ApprovalTimeout):
        await mgr.wait(req.approval_id)
    # 超时后请求被清理，不可再 resolve
    assert not mgr.resolve(req.approval_id, True)


@pytest.mark.asyncio
async def test_approval_resolve_is_idempotent():
    mgr = ApprovalManager(timeout_seconds=5)
    req = mgr.create("p1", "s1", "command_runner", "run", {})
    assert mgr.resolve(req.approval_id, True)
    # 第二次 resolve 拒绝（幂等保护）
    assert not mgr.resolve(req.approval_id, False)


@pytest.mark.asyncio
async def test_approval_reject_all_for_plan():
    mgr = ApprovalManager(timeout_seconds=5)
    a1 = mgr.create("p1", "s1", "command_runner", "run", {})
    a2 = mgr.create("p2", "s2", "command_runner", "run", {})
    count = mgr.reject_all_for_plan("p1", "plan ended")
    assert count == 1
    resolved = await mgr.wait(a1.approval_id)
    assert resolved.approved is False
    assert resolved.comment == "plan ended"
    # p2 未受影响
    assert not a2.resolved


def test_approval_list_pending_filters_plan_and_resolved():
    mgr = ApprovalManager(timeout_seconds=5)
    mgr.create("p1", "s1", "command_runner", "run", {})
    a2 = mgr.create("p2", "s2", "command_runner", "run", {})
    mgr.resolve(a2.approval_id, True)
    pending = mgr.list_pending(plan_id="p1")
    assert len(pending) == 1
    assert pending[0]["plan_id"] == "p1"


# ===== Executor：blocked 结构化 + retryable 分类 =====

def _make_step(tool: str = "command_runner:run_command") -> Step:
    return Step(
        order=1,
        description="test",
        tool=tool,
        inputs={"command": "echo hi"},
        check_criteria="",
        status=StepStatus.PENDING,
    )


class _StubRegistry:
    """按预设结果返回的工具注册表桩。"""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute_tool(self, tool_id, instruction, inputs):
        self.calls.append((tool_id, instruction, inputs))
        return dict(self.result)


def _make_executor(registry) -> DefaultExecutor:
    return DefaultExecutor(
        llm_client=LLMClient(),
        model_config={"primary": {"provider": "x", "model": "y"}},
        tool_registry=registry,
        database=None,
    )


@pytest.mark.asyncio
async def test_executor_structured_blocked_field_no_retry():
    """P0-b：工具返回结构化 blocked=True → executor 判定拦截且不可重试。"""
    registry = _StubRegistry({"success": False, "message": "denied", "error": "Dangerous command blocked", "blocked": True})
    executor = _make_executor(registry)
    result = await executor.execute_step(_make_step())
    assert result["blocked"] is True
    assert result["retryable"] is False


@pytest.mark.asyncio
async def test_executor_legacy_error_code_fallback():
    """P0-b：旧工具无 blocked 字段时，错误码回退判定仍然生效。"""
    registry = _StubRegistry({"success": False, "message": "denied", "error": LEGACY_BLOCKED_ERROR_CODES[0]})
    executor = _make_executor(registry)
    result = await executor.execute_step(_make_step())
    assert result["blocked"] is True
    assert result["retryable"] is False


@pytest.mark.asyncio
async def test_executor_second_gate_success_with_blocked_flag():
    """P0-b 第二道守卫：工具声称 success 但携带 blocked=True → 按拦截失败处理。"""
    registry = _StubRegistry({"success": True, "message": "done", "blocked": True})
    executor = _make_executor(registry)
    result = await executor.execute_step(_make_step())
    assert result["success"] is False
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_executor_retryable_classification():
    """P2：超时/普通失败可重试；拦截与校验失败不可重试。"""
    # 普通失败 → retryable=True
    registry = _StubRegistry({"success": False, "message": "boom", "error": "ConnectionError"})
    executor = _make_executor(registry)
    result = await executor.execute_step(_make_step())
    assert result["retryable"] is True

    # 校验明确失败（check_criteria 命中且 LLM 判 NO）→ retryable=False
    step = _make_step()
    step.check_criteria = "文件存在"
    registry2 = _StubRegistry({"success": True, "message": "done"})

    class _NoExecutor(DefaultExecutor):
        async def check_result(self, step, output):
            return self.CHECK_FAILED

    executor2 = _NoExecutor(
        llm_client=LLMClient(),
        model_config={"primary": {"provider": "x", "model": "y"}},
        tool_registry=registry2,
        database=None,
    )
    result2 = await executor2.execute_step(step)
    assert result2["success"] is False
    assert result2["retryable"] is False


@pytest.mark.asyncio
async def test_executor_success_has_retryable_false():
    registry = _StubRegistry({"success": True, "message": "done"})
    executor = _make_executor(registry)
    result = await executor.execute_step(_make_step())
    assert result["success"] is True
    assert result.get("retryable") is False
