# -*- coding: utf-8 -*-
"""改进验证测试：验收反馈文本构建 + bing 长查询拆分。"""
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
