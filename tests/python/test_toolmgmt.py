"""工具管理修正（1.6）的确定性测试。

P1-B：human_tool 第三类 + 人工步骤审批闸门。
P1-C：test_runner 白名单模板执行器 + 普通工具轻契约。
P1-A：设计期搜索授权化（无 Grant fail-closed / 越权主机拒绝 / 预算耗尽）。
"""

import pytest

from api.approval import ApprovalManager
from authorization import AuthorizationManager, GrantLimits
from capabilities import CapabilityRegistry, CapabilityRequest, HumanCapabilityAdapter
from tools import get_registry
from tools.test_runner import TestRunner, configure_templates
from utils.json_fixer import validate_agent_steps, validate_tool_contracts


# ===== P1-B human_tool =====


def test_human_capability_adapter_kind():
    adapter = HumanCapabilityAdapter(get_registry(), "manual_tool")
    spec = adapter.spec()
    assert spec.kind == "human_tool"
    assert spec.required_permissions == ("human:manual_tool",)
    assert "人工" in spec.description


def test_approval_manager_human_step_gate():
    mgr = ApprovalManager()  # 不配置 required_tools
    assert not mgr.is_required("manual_tool")
    # human_tool 步骤必须挂审批；普通工具/未知 kind 不挂
    assert mgr.is_required_for_step("manual_tool", "human_tool") is True
    assert mgr.is_required_for_step("manual_tool") is False
    assert mgr.is_required_for_step("file_creator", "deterministic_tool") is False
    # 开关可关（兼容口径）
    mgr_off = ApprovalManager(human_steps=False)
    assert mgr_off.is_required_for_step("manual_tool", "human_tool") is False
    # 高风险工具配置仍然生效
    mgr_cfg = ApprovalManager(required_tools=["command_runner"])
    assert mgr_cfg.is_required_for_step("command_runner", "deterministic_tool") is True


@pytest.mark.asyncio
async def test_human_adapter_runs_under_grant(tmp_path):
    mgr = AuthorizationManager(workspace_root=tmp_path)
    step = type("S", (), {"tool": "manual_tool", "inputs": {}})()
    grant = mgr.issue_for_step("p-human", step)
    adapter = HumanCapabilityAdapter(get_registry(), "manual_tool")
    result = await adapter.run(
        CapabilityRequest(
            work_item_id="s1",
            goal="人工做某事",
            inputs={"instruction": "manual_step", "description": "人工做某事"},
            grant_id=grant.grant_id,
        ),
        {"authorization_manager": mgr, "grant_id": grant.grant_id},
    )
    assert result.status == "completed"
    assert grant.used_tool_calls == 1


# ===== P1-C test_runner =====


@pytest.fixture(autouse=True)
def _reset_templates():
    configure_templates(None)
    yield
    configure_templates(None)


def _seed_pytest_project(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")


def test_test_runner_pytest_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    _seed_pytest_project(tmp_path)
    result = TestRunner.execute("run", {"suite": "pytest", "test_path": "test_ok.py"})
    assert result["success"] is True, result
    assert result["exit_code"] == 0


def test_test_runner_pytest_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    _seed_pytest_project(tmp_path)
    result = TestRunner.execute("run", {"suite": "pytest", "test_path": "test_bad.py"})
    assert result["success"] is False
    assert result["exit_code"] == 1


def test_test_runner_unknown_suite_blocked(tmp_path):
    result = TestRunner.execute("run", {"suite": "rm -rf /"})
    assert result["success"] is False
    assert result.get("blocked") is True


def test_test_runner_missing_suite():
    result = TestRunner.execute("run", {})
    assert result["success"] is False
    assert "suite" in result["message"]


def test_test_runner_custom_templates_from_config():
    configure_templates({"echo": ["{python}", "-c", "print('hi')"]})
    result = TestRunner.execute("run", {"suite": "echo"})
    assert result["success"] is True
    assert result["exit_code"] == 0


def test_validate_tool_contracts():
    good = {
        "segments": [
            {
                "description": "s",
                "steps": [
                    {
                        "description": "跑测试",
                        "tool": "test_runner",
                        "inputs": {"suite": "pytest", "test_path": "test_ok.py"},
                        "acceptance": [{"type": "exit_code", "equals": 0}],
                    }
                ],
            }
        ]
    }
    ok, err = validate_tool_contracts(good)
    assert ok, err

    missing_suite = {
        "segments": [
            {
                "description": "s",
                "steps": [
                    {
                        "description": "d",
                        "tool": "test_runner",
                        "inputs": {"test_path": "test_ok.py"},
                        "acceptance": [{"type": "exit_code", "equals": 0}],
                    }
                ],
            }
        ]
    }
    ok, err = validate_tool_contracts(missing_suite)
    assert not ok and "suite" in err

    wrong_acceptance = {
        "segments": [
            {
                "description": "s",
                "steps": [
                    {
                        "description": "d",
                        "tool": "test_runner",
                        "inputs": {"suite": "pytest"},
                        "acceptance": [{"type": "content_contains", "path": "x", "text": "y"}],
                    }
                ],
            }
        ]
    }
    ok, err = validate_tool_contracts(wrong_acceptance)
    assert not ok and "exit_code" in err

    # lint_gate 契约（1.9）：缺 path 打回；acceptance 类型不对打回
    lint_missing = {
        "segments": [
            {
                "description": "s",
                "steps": [
                    {
                        "description": "d",
                        "tool": "lint_gate",
                        "inputs": {},
                        "acceptance": [{"type": "exit_code", "equals": 0}],
                    }
                ],
            }
        ]
    }
    ok, err = validate_tool_contracts(lint_missing)
    assert not ok and "path" in err

    lint_wrong_acceptance = {
        "segments": [
            {
                "description": "s",
                "steps": [
                    {
                        "description": "d",
                        "tool": "lint_gate",
                        "inputs": {"path": "a.py"},
                        "acceptance": [{"type": "file_exists", "path": "a.py"}],
                    }
                ],
            }
        ]
    }
    ok, err = validate_tool_contracts(lint_wrong_acceptance)
    assert not ok and "exit_code" in err

    # browser_check 契约（1.9）：缺 url 打回；缺 acceptance 打回
    browser_missing_url = {
        "segments": [
            {
                "description": "s",
                "steps": [
                    {
                        "description": "d",
                        "tool": "browser_check",
                        "inputs": {"expect_text": "x"},
                        "acceptance": [{"type": "exit_code", "equals": 0}],
                    }
                ],
            }
        ]
    }
    ok, err = validate_tool_contracts(browser_missing_url)
    assert not ok and "url" in err

    browser_no_acceptance = {
        "segments": [
            {
                "description": "s",
                "steps": [
                    {
                        "description": "d",
                        "tool": "browser_check",
                        "inputs": {"url": "http://example.com", "expect_text": "x"},
                    }
                ],
            }
        ]
    }
    ok, err = validate_tool_contracts(browser_no_acceptance)
    assert not ok and "acceptance" in err

    # 非 test_runner 步骤不受影响；agent 契约校验与其互不干扰
    ok, err = validate_tool_contracts(
        {"segments": [{"description": "s", "steps": [{"description": "d", "tool": "file_creator", "inputs": {"path": "a.txt", "content": "x"}}]}]}
    )
    assert ok, err
    ok, err = validate_agent_steps(good)
    assert ok, err


@pytest.mark.asyncio
async def test_search_super_search_branch_authorized(tmp_path, monkeypatch):
    """对方会话新增的 super_search 分支也必须走 design_search Grant（§23.3 合并）。"""
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    from llm_client import LLMClient
    from tools import ToolRegistry
    from tools import super_search

    registry = ToolRegistry(LLMClient(), {"primary": {"provider": "deterministic", "model": "m"}})
    manager = AuthorizationManager(workspace_root=tmp_path)
    registry.authorization_manager = manager
    registry.database = _StubDB()
    grant = manager.issue_grant(
        "design_search",
        ["web_fetcher", "super_search"],
        limits=GrantLimits(max_tool_calls=2, max_model_calls=0),
        allowed_hosts=["html.duckduckgo.com", "www.bing.com"],
    )
    calls = []
    monkeypatch.setattr(
        super_search.SuperSearch,
        "execute",
        lambda instruction, inputs: calls.append(inputs) or {"success": True, "results": []},
    )
    # 无 Grant → 结构化拒绝，不触达搜索引擎
    r1 = await registry.agent._authorized_search("super_search", "search", {"query": "q"}, context={})
    assert r1["blocked"] is True and "search_denied" in r1["error"]
    assert calls == []
    # 有 Grant → 放行并审计
    r2 = await registry.agent._authorized_search("super_search", "search", {"query": "q"}, context={"grant_id": grant.grant_id})
    assert r2["success"] is True
    assert len(calls) == 1
    assert grant.used_tool_calls == 1


# ===== P1-A 搜索授权化 =====


class _StubDB:
    def __init__(self):
        self.rows = []

    def save_audit_log(self, **kwargs):
        self.rows.append(kwargs)
        return True


@pytest.fixture()
def _wired_registry(tmp_path, monkeypatch):
    """带 AI agent 的新 ToolRegistry + 授权管理器与审计桩。"""
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    from llm_client import LLMClient
    from tools import ToolRegistry

    registry = ToolRegistry(LLMClient(), {"primary": {"provider": "deterministic", "model": "m"}})
    manager = AuthorizationManager(workspace_root=tmp_path)
    registry.authorization_manager = manager
    registry.database = _StubDB()
    yield registry, manager, registry.database


@pytest.mark.asyncio
async def test_search_denied_without_grant_when_enforced(_wired_registry, monkeypatch):
    registry, manager, db = _wired_registry
    from tools import web_fetcher

    def _boom(instruction, inputs):
        raise AssertionError("网络被触达")

    monkeypatch.setattr(web_fetcher.WebFetcher, "execute", _boom)
    result = await registry.agent._authorized_fetch("https://example.com/x", context={})
    assert result["success"] is False
    assert result["blocked"] is True
    assert "search_denied" in result["error"]
    # 拒绝也要留审计
    assert db.rows and db.rows[0]["actor"] == "designer_search"
    assert db.rows[0]["blocked"] is True


@pytest.mark.asyncio
async def test_search_allowed_with_grant_then_budget_exhausted(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    from llm_client import LLMClient
    from tools import ToolRegistry

    registry = ToolRegistry(LLMClient(), {"primary": {"provider": "deterministic", "model": "m"}})
    manager = AuthorizationManager(workspace_root=tmp_path, allowed_hosts=["example.com"])
    registry.authorization_manager = manager
    registry.database = _StubDB()
    try:
        grant = manager.issue_grant(
            "design_search",
            ["web_fetcher"],
            limits=GrantLimits(max_tool_calls=2, max_model_calls=0),
        )
        from tools import web_fetcher

        calls = []

        def _fake(instruction, inputs):
            calls.append(inputs["url"])
            return {"success": True, "content": "ok"}

        monkeypatch.setattr(web_fetcher.WebFetcher, "execute", _fake)
        r1 = await registry.agent._authorized_fetch("https://example.com/a", context={"grant_id": grant.grant_id})
        r2 = await registry.agent._authorized_fetch("https://example.com/b", context={"grant_id": grant.grant_id})
        r3 = await registry.agent._authorized_fetch("https://example.com/c", context={"grant_id": grant.grant_id})
        assert r1["success"] and r2["success"]
        assert calls == ["https://example.com/a", "https://example.com/b"]
        assert r3["blocked"] is True and "budget_exceeded" in r3["error"]
        assert grant.used_tool_calls == 2
    finally:
        registry.authorization_manager = None
        registry.database = None


@pytest.mark.asyncio
async def test_search_denied_for_disallowed_host(_wired_registry, monkeypatch):
    registry, manager, db = _wired_registry
    grant = manager.issue_grant(
        "design_search",
        ["web_fetcher"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=0),
        allowed_hosts=["api.example.com"],
    )
    from tools import web_fetcher

    def _boom(instruction, inputs):
        raise AssertionError("越权主机触达了网络")

    monkeypatch.setattr(web_fetcher.WebFetcher, "execute", _boom)
    result = await registry.agent._authorized_fetch("https://evil.com/x", context={"grant_id": grant.grant_id})
    assert result["blocked"] is True
    assert "resource_denied" in result["error"]
    assert "evil.com" in result["message"]


def test_issue_grant_generic(tmp_path):
    manager = AuthorizationManager(workspace_root=tmp_path, allowed_hosts=["x.com"])
    grant = manager.issue_grant(
        "design_search",
        ["web_fetcher"],
        limits=GrantLimits(max_tool_calls=3, max_model_calls=0),
        ttl_seconds=60,
    )
    assert grant.allowed_tools == frozenset({"web_fetcher"})
    assert grant.plan_id == ""
    decision = manager.authorize(grant.grant_id, "web_fetcher", "fetch_content", {"url": "https://x.com/a"})
    assert decision.allowed
    # 文件资源不因通用签发而放行（资源为空）
    denied = manager.authorize(grant.grant_id, "file_creator", "create_file", {"path": "a.txt"})
    assert not decision.allowed or denied.allowed is False


def test_test_runner_accepts_test_passes_acceptance():
    """动作 B 第一级：test_runner 步骤的 acceptance 可用 test_passes（行为级）。"""
    from utils.json_fixer import validate_tool_contracts

    plan = {"segments": [{"steps": [{
        "tool": "test_runner",
        "description": "跑测试",
        "inputs": {"suite": "pytest", "test_path": "tests/"},
        "acceptance": [{"type": "test_passes", "suite": "pytest", "test_path": "tests/"}],
    }]}]}
    ok, err = validate_tool_contracts(plan)
    assert ok, err


def test_test_runner_still_accepts_exit_code():
    """旧计划兼容：exit_code 验收继续放行。"""
    from utils.json_fixer import validate_tool_contracts

    plan = {"segments": [{"steps": [{
        "tool": "test_runner",
        "description": "跑测试",
        "inputs": {"suite": "pytest"},
        "acceptance": [{"type": "exit_code", "equals": 0}],
    }]}]}
    ok, err = validate_tool_contracts(plan)
    assert ok, err
