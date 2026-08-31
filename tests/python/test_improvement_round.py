# -*- coding: utf-8 -*-
"""改进验证测试：验收反馈文本构建 + bing 长查询拆分。"""
import json
import sys

sys.path.insert(0, "python")

from tools import super_search as ss
from tools.super_search import SuperSearch


# ===== 验收反馈文本构建 =====

def test_build_acceptance_feedback_content_contains():
    from api.server import _build_acceptance_feedback

    result = {
        "passed": False,
        "checks": [
            {"type": "content_contains", "path": "src/a.py", "text": "component_list", "passed": False},
            {"type": "content_contains", "path": "src/a.py", "text": "interaction_flow", "passed": False},
        ],
    }
    feedback = _build_acceptance_feedback(result)
    assert feedback is not None
    assert "component_list" in feedback and "interaction_flow" in feedback
    assert "逐字出现" in feedback


def test_build_acceptance_feedback_passed_only_returns_none():
    from api.server import _build_acceptance_feedback

    result = {"checks": [{"type": "content_contains", "text": "x", "passed": True}]}
    assert _build_acceptance_feedback(result) is None


def test_build_acceptance_feedback_non_content_contains_ignored():
    from api.server import _build_acceptance_feedback

    result = {"checks": [{"type": "file_exists", "path": "a.py", "passed": False}]}
    assert _build_acceptance_feedback(result) is None


def test_build_acceptance_feedback_never_raises():
    from api.server import _build_acceptance_feedback

    assert _build_acceptance_feedback(None) is None
    assert _build_acceptance_feedback({"checks": "garbage"}) is None




def test_build_acceptance_feedback_test_passes():
    from api.server import _build_acceptance_feedback

    result = {
        "passed": False,
        "checks": [
            {
                "type": "test_passes",
                "passed": False,
                "evidence": {
                    "suite": "pytest",
                    "test_path": "tests/test_api.py",
                    "exit_code": 1,
                    "stderr_tail": "AssertionError: expected 3 != 2",
                },
            }
        ],
    }
    feedback = _build_acceptance_feedback(result)
    assert feedback is not None
    assert "pytest" in feedback and "tests/test_api.py" in feedback
    assert "AssertionError" in feedback


def test_build_acceptance_feedback_mixed_test_and_content():
    from api.server import _build_acceptance_feedback

    result = {
        "passed": False,
        "checks": [
            {"type": "test_passes", "passed": False, "evidence": {"suite": "pytest", "exit_code": 2, "stdout_tail": "1 failed"}},
            {"type": "content_contains", "path": "src/a.py", "text": "component_list", "passed": False},
        ],
    }
    feedback = _build_acceptance_feedback(result)
    assert feedback is not None
    assert "测试未全部通过" in feedback
    assert "逐字出现" in feedback


# ===== stepfun /v1/search 引擎（阶跃网页搜索） =====

def test_stepfun_engine_parse(monkeypatch, tmp_path):
    monkeypatch.setattr("tools.workspace.resolve_workspace_root", lambda: tmp_path)
    monkeypatch.delenv("STEPFUN_API_KEY", raising=False)
    captured = {}

    def fake_fetch(url, timeout=15, data=None, headers=None, max_bytes=1):
        captured["url"] = url
        captured["body"] = json.loads(data.decode("utf-8"))
        captured["headers"] = headers
        return json.dumps({
            "query": captured["body"]["query"],
            "results": [
                {"url": "https://docs.stepfun.com/", "position": 1,
                 "title": "StepFun docs", "time": "2026-03-20T00:00:00",
                 "snippet": "official snippet", "content": "long content"},
                {"url": "", "title": "no url", "snippet": "skipped"},
            ],
        })

    monkeypatch.setattr(ss, "_http_fetch", fake_fetch)
    monkeypatch.setattr(ss, "_stepfun_key", lambda: "sk-test")
    items = ss._search_stepfun("AutoClaw 积分", 5, 10)
    assert len(items) == 1
    assert items[0]["title"] == "StepFun docs"
    assert items[0]["url"] == "https://docs.stepfun.com/"
    assert captured["url"] == "https://api.stepfun.com/step_plan/v1/search"
    assert captured["body"]["n"] == 5
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


def test_stepfun_engine_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.workspace.resolve_workspace_root", lambda: tmp_path)
    monkeypatch.delenv("STEPFUN_API_KEY", raising=False)
    (tmp_path / "config").mkdir(exist_ok=True)
    import pytest
    with pytest.raises(ss.EngineError):
        ss._search_stepfun("q", 5, 10)


def test_stepfun_config_key_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "resolve_workspace_root", lambda: tmp_path)
    monkeypatch.delenv("STEPFUN_API_KEY", raising=False)
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    yaml_text = "api_keys:" + chr(10) + "  stepfun: test-key-123" + chr(10)
    (cfg / "models.yaml").write_text(yaml_text, encoding="utf-8")
    assert ss._stepfun_key() == "test-key-123"


def test_stepfun_registered_and_autoselect(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "resolve_workspace_root", lambda: tmp_path)
    monkeypatch.delenv("STEPFUN_API_KEY", raising=False)
    assert "stepfun" in ss._ENGINE_FUNCS
    monkeypatch.setattr(ss, "_stepfun_key", lambda: "k")
    assert "stepfun" in ss._auto_select_engines()


# ===== bing 长中文查询拆分 =====

def test_bing_long_cjk_query_split(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.workspace.resolve_workspace_root", lambda: tmp_path)
    captured = []

    def fake_fetch(url, timeout=15):
        captured.append(url)
        if "%E5%A4%A7%E6%A8%A1%E5%9E%8B" in url:
            return '<html><li class="b_algo"><h2><a href="https://x.com/1">Free API list</a></h2><p>2026</p></li></html>'
        return '<html><li class="b_algo"><h2><a href="https://x.com/2">Kimi free</a></h2><p>tier</p></li></html>'

    monkeypatch.setattr(ss, "_http_fetch", fake_fetch)
    items = ss._search_bing("大模型 API 免费额度 智谱 DeepSeek Kimi 通义", 10, 10)
    assert len(captured) == 2  # 拆成两个子查询
    assert len(items) == 2
    assert any("Free API list" in i["title"] for i in items)


def test_bing_short_query_single_request(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.workspace.resolve_workspace_root", lambda: tmp_path)
    captured = []

    def fake_fetch(url, timeout=15):
        captured.append(url)
        return '<html><li class="b_algo"><h2><a href="https://x.com/1">t</a></h2><p>s</p></li></html>'

    monkeypatch.setattr(ss, "_http_fetch", fake_fetch)
    ss._search_bing("大模型 API 免费额度", 10, 10)
    assert len(captured) == 1
