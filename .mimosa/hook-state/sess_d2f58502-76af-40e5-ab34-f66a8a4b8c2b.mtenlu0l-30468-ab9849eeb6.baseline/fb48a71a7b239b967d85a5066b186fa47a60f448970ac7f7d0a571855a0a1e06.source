"""Runtime 临时 Grant 的授权、资源和预算测试。"""

import asyncio
import os
import time

import pytest

from authorization import AuthorizationManager
from executor import DefaultExecutor
from llm_client import LLMClient
from models import Step


def _step(tool="file_creator:create_file", path="src/a.txt"):
    return Step(
        order=1,
        description="test step",
        tool=tool,
        inputs={"path": path},
        check_criteria="手动检查",
    )


def _manager(tmp_path, **overrides):
    return AuthorizationManager(workspace_root=tmp_path, **overrides)


def test_issue_for_step_binds_tool_resource_and_limits(tmp_path):
    manager = _manager(tmp_path)
    grant = manager.issue_for_step("plan-1", _step(), subject="executor")

    assert grant.grant_id
    assert grant.plan_id == "plan-1"
    assert grant.step_id == str(_step().step_id) or grant.step_id
    assert grant.allowed_tools == frozenset({"file_creator"})
    assert str(tmp_path.resolve()) in grant.allowed_resources[0]
    assert grant.limits.max_tool_calls == 1
    assert grant.expires_at > grant.issued_at


def test_authorize_allowed_tool_and_resource_consumes_one_slot(tmp_path):
    manager = _manager(tmp_path)
    step = _step()
    grant = manager.issue_for_step("p", step)

    allowed = manager.authorize(grant.grant_id, "file_creator", "create_file", {"path": "src/a.txt"})
    assert allowed.allowed
    assert allowed.reason == "allowed"
    assert allowed.usage["tool_calls"] == 1

    over_budget = manager.authorize(grant.grant_id, "file_creator", "create_file", {"path": "src/a.txt"})
    assert not over_budget.allowed
    assert over_budget.reason == "budget_exceeded"


def test_wrong_tool_and_wrong_resource_are_denied(tmp_path):
    manager = _manager(tmp_path)
    grant = manager.issue_for_step("p", _step())

    wrong_tool = manager.authorize(grant.grant_id, "command_runner", "run_command", {})
    assert not wrong_tool.allowed
    assert wrong_tool.reason == "tool_not_granted"

    wrong_resource = manager.authorize(
        grant.grant_id,
        "file_creator",
        "create_file",
        {"path": "src/other.txt"},
    )
    assert not wrong_resource.allowed
    assert wrong_resource.reason == "resource_denied"


def test_deny_env_and_git_resources(tmp_path):
    manager = _manager(tmp_path)
    for path in (".env", ".env.local", ".git/config"):
        grant = manager.issue_for_step("p", _step(path=path))
        result = manager.authorize(grant.grant_id, "file_creator", "create_file", {"path": path})
        assert not result.allowed, path
        assert result.reason == "resource_denied"


def test_relative_path_traversal_is_denied(tmp_path):
    manager = _manager(tmp_path)
    grant = manager.issue_for_step("p", _step(path="../outside.txt"))
    result = manager.authorize(
        grant.grant_id, "file_creator", "create_file", {"path": "../outside.txt"}
    )
    assert not result.allowed
    assert result.reason == "resource_denied"


def test_absolute_path_keeps_existing_explicit_path_semantics(tmp_path):
    manager = _manager(tmp_path)
    absolute = tmp_path / "outside-explicit.txt"
    step = _step(path=str(absolute))
    grant = manager.issue_for_step("p", step)
    result = manager.authorize(
        grant.grant_id, "file_creator", "create_file", {"path": str(absolute)}
    )
    assert result.allowed


def test_expired_and_revoked_grants_are_denied(tmp_path):
    manager = _manager(tmp_path, default_ttl_seconds=0.01)
    grant = manager.issue_for_step("p", _step())
    time.sleep(0.03)
    expired = manager.authorize(grant.grant_id, "file_creator", "create_file", {"path": "src/a.txt"})
    assert not expired.allowed
    assert expired.reason == "grant_expired"

    fresh_manager = _manager(tmp_path)
    fresh = fresh_manager.issue_for_step("p", _step())
    assert fresh_manager.revoke(fresh.grant_id)
    revoked = fresh_manager.authorize(fresh.grant_id, "file_creator", "create_file", {"path": "src/a.txt"})
    assert not revoked.allowed
    assert revoked.reason == "grant_revoked"


def test_network_host_requires_explicit_allowlist(tmp_path):
    denied_manager = _manager(tmp_path)
    grant = denied_manager.issue_for_step(
        "p", Step(order=1, description="fetch", tool="web_fetcher:fetch_content", inputs={"url": "https://example.com"}, check_criteria=""),
    )
    denied = denied_manager.authorize(grant.grant_id, "web_fetcher", "fetch_content", {"url": "https://example.com"})
    assert not denied.allowed
    assert denied.reason == "resource_denied"

    allowed_manager = _manager(tmp_path, allowed_hosts=["example.com"])
    grant = allowed_manager.issue_for_step(
        "p", Step(order=1, description="fetch", tool="web_fetcher:fetch_content", inputs={"url": "https://example.com"}, check_criteria=""),
    )
    allowed = allowed_manager.authorize(grant.grant_id, "web_fetcher", "fetch_content", {"url": "https://example.com"})
    assert allowed.allowed


class _CountingRegistry:
    def __init__(self):
        self.calls = 0

    def execute_tool(self, *args):
        self.calls += 1
        return {"success": True, "message": "should not happen"}


@pytest.mark.asyncio
async def test_executor_denial_happens_before_tool_call(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    registry = _CountingRegistry()
    manager = _manager(tmp_path)
    executor = DefaultExecutor(
        llm_client=LLMClient(),
        model_config=None,
        tool_registry=registry,
        authorization_manager=manager,
    )
    grant = manager.issue_for_step("p", _step(path="src/allowed.txt"))

    # 同一 Grant 改请求另一个资源，必须在 Registry 前被拒绝。
    denied_step = _step(path="src/forbidden.txt")
    result = await executor.execute_step(denied_step, grant_id=grant.grant_id)

    assert not result["success"]
    assert result["blocked"]
    assert result["blocked_reason"] == "resource_denied"
    assert result["retryable"] is False
    assert result["grant_id"] == grant.grant_id
    assert registry.calls == 0


@pytest.mark.asyncio
async def test_executor_valid_grant_allows_tool_and_records_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    registry = _CountingRegistry()
    manager = _manager(tmp_path)
    executor = DefaultExecutor(
        llm_client=LLMClient(),
        model_config=None,
        tool_registry=registry,
        authorization_manager=manager,
    )
    step = _step(path="src/a.txt")
    grant = manager.issue_for_step("p", step)
    result = await executor.execute_step(step, grant_id=grant.grant_id)

    assert result["success"] is True
    assert registry.calls == 1
    assert manager.get(grant.grant_id).used_tool_calls == 1
