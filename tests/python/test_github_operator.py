# -*- coding: utf-8 -*-
"""github_operator 功能回归：元数据、argv 构造、拦截语义、注册/推断接线。
真实 gh CLI 的只读用例打标 RUN_REAL_GH=1，默认全部离线（mock subprocess）。"""
import json
import os
from types import SimpleNamespace

import pytest

from tools import get_registry
from tools import github_operator as gh_mod
from tools.github_operator import ACTION_TEMPLATES, GitHubOperator
from executor import DefaultExecutor


# ===== 元数据 =====

def test_metadata_complete():
    meta = GitHubOperator.get_metadata()
    assert meta["tool_id"] == "github_operator"
    assert meta["category"] == "git"
    instructions = meta["capabilities"]["instructions"]
    assert {i["name"] for i in instructions} == set(ACTION_TEMPLATES)
    for item in instructions:
        assert item["description"]
        # 无参数的指令（pr_list）允许空表；有参数的说明不能为空串
        assert all(str(v).strip() for v in item["parameters"].values())


# ===== argv 构造 =====

def test_build_argv_pr_create_minimal():
    argv = GitHubOperator.build_argv("pr_create", {"title": "t", "body": "b"})
    assert argv == ["gh", "pr", "create", "--title", "t", "--body", "b"]


def test_build_argv_pr_create_optional_flags():
    argv = GitHubOperator.build_argv(
        "pr_create", {"title": "t", "body": "b", "base": "main", "head": "feat", "draft": True}
    )
    assert argv[-5:] == ["--base", "main", "--head", "feat", "--draft"]


def test_build_argv_target_resolution():
    assert GitHubOperator.build_argv("pr_status", {"number": 7}) == ACTION_TEMPLATES["pr_status"] + ["7"]
    assert GitHubOperator.build_argv("pr_checks", {"branch": "feat/x"}) == ACTION_TEMPLATES["pr_checks"] + ["feat/x"]
    assert GitHubOperator.build_argv("pr_list", {}) == ACTION_TEMPLATES["pr_list"]


# ===== 拦截语义 =====

def test_unknown_action_blocked():
    result = GitHubOperator.execute("execute", {"action": "repo", "extra": "delete"})
    assert result["success"] is False
    assert result.get("blocked") is True
    assert "available" in result


def test_pr_create_missing_required_blocked():
    result = GitHubOperator.execute("pr_create", {"title": "only-title"})
    assert result["success"] is False
    assert result.get("blocked") is True
    assert "body" in result["message"]


def test_missing_action_blocked():
    result = GitHubOperator.execute("execute", {})
    assert result["success"] is False
    assert result.get("blocked") is True


def test_unknown_instruction_without_action_rejected():
    result = GitHubOperator.execute("nuke", {})
    assert result["success"] is False
    assert result["error"] == "Unknown instruction"


# ===== 执行路径（mock subprocess） =====

@pytest.fixture
def capture_run(monkeypatch):
    """替换 subprocess.run，捕获调用参数并返回可配置结果。"""
    calls = {}

    def _install(returncode=0, stdout="", stderr=""):
        def fake_run(argv, **kwargs):
            calls["argv"] = argv
            calls["kwargs"] = kwargs
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

        monkeypatch.setattr(gh_mod.subprocess, "run", fake_run)
        return calls

    return _install


def test_pr_list_success_parses_json(capture_run):
    payload = [{"number": 1, "title": "x", "state": "OPEN"}]
    calls = capture_run(returncode=0, stdout=json.dumps(payload))
    result = GitHubOperator.execute("pr_list", {})
    assert result["success"] is True
    assert result["exit_code"] == 0
    assert result["data"] == payload
    assert calls["kwargs"]["shell"] is False
    assert calls["kwargs"]["cwd"]  # 总是有工作目录，不落到进程当前目录


def test_pr_status_failure_carries_exit_code(capture_run):
    calls = capture_run(returncode=1, stdout="", stderr="no pull requests found")
    result = GitHubOperator.execute("pr_status", {"number": "999"})
    assert result["success"] is False
    assert result["exit_code"] == 1
    assert "no pull requests" in result["stderr"]
    assert calls["argv"] == ACTION_TEMPLATES["pr_status"] + ["999"]


def test_timeout_reported(capture_run, monkeypatch):
    def fake_run(argv, **kwargs):
        raise gh_mod.subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(gh_mod.subprocess, "run", fake_run)
    result = GitHubOperator.execute("pr_status", {})
    assert result["success"] is False
    assert result["exit_code"] is None
    assert "超时" in result["message"]


def test_gh_missing_reported(capture_run, monkeypatch):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(gh_mod.subprocess, "run", fake_run)
    result = GitHubOperator.execute("pr_status", {})
    assert result["success"] is False
    assert "gh CLI 未安装" in result["message"]


# ===== 注册与推断接线 =====

def test_registered_in_tool_registry():
    registry = get_registry()
    assert registry.get_tool("github_operator") is GitHubOperator


def test_inferred_instruction_accepted():
    executor = DefaultExecutor(None, {})
    assert executor._infer_instruction("github_operator", {"action": "pr_create"}) == "pr_create"
    assert executor._infer_instruction("github_operator", {"action": "whatever"}) == "pr_status"


# ===== 真实 gh（打标，默认跳过） =====

@pytest.mark.skipif(os.environ.get("RUN_REAL_GH") != "1", reason="真实 gh 用例需 RUN_REAL_GH=1")
def test_real_pr_list_readonly():
    result = GitHubOperator.execute("pr_list", {})
    assert result["success"] is True
    assert isinstance(result.get("data"), list)
