# -*- coding: utf-8 -*-
"""api/secrets_hook 与 design_secret_routes 的回归测试（项目密钥录入钩子）。

外部工具仓（harness_tools.design_secrets）在未配置 / 未安装时可缺席，宿主侧必须
fail-closed：不影响既有审批行为、不把任何密钥值带给模型。本文件通过 stub
api.secrets_hook._load_tools 精确覆盖三条路径（工具缺席 / 成功 / 异常），
并验证 HTTP 路由骨架返回结构化响应。
"""
import types as _types

import pytest


@pytest.fixture
def patch_tools(monkeypatch):
    """替换 _load_tools 的返回值，并提供一个可构造的 SecretStore 桩。"""
    from api import secrets_hook as hook

    class _Store:
        def __init__(self, root):
            self.root = root

        def list(self):
            return []

    def _set(design_secrets, store_cls=None):
        monkeypatch.setattr(hook, "_load_tools",
                            lambda: (design_secrets, store_cls or _Store))
    return _set


class _FakeDesign:
    def __init__(self, user_request="构建一个需要 SECRET_API_TOKEN 的工具",
                 part1=None, part2=None, user_comments=None):
        self.user_request = user_request
        self.part1 = part1
        self.part2 = part2
        self.user_comments = user_comments


DESIGN = _FakeDesign()


def test_normalize_declaration_json_dict():
    from api import secrets_hook as hook
    assert hook._normalize_declaration(
        '{"required_secrets": ["X"]}') == {"required_secrets": ["X"]}
    assert hook._normalize_declaration('{"secrets": ["Y"]}') == {"secrets": ["Y"]}


def test_normalize_declaration_plain_list():
    from api import secrets_hook as hook
    assert hook._normalize_declaration('["A", "B"]') == {"required_secrets": ["A", "B"]}


def test_normalize_declaration_keeps_non_json():
    from api import secrets_hook as hook
    blob = "## secrets code block text"
    assert hook._normalize_declaration(blob) is blob


def test_declaration_json_string_passes_dict_to_after_approval(patch_tools):
    """宿主把声明当 JSON 字符串传，必须被规范成 dict 再交给真模块（此前会静默漏掉）。"""
    from api import secrets_hook as hook

    captured = {}

    def _after_approval(document, store, root=None, launcher=None):
        captured["document"] = document
        return {"prompt_shown": True, "entry_url": "http://localhost/e",
                "pending": ["MY_SECRET_TOKEN"], "already_set": []}

    fake_tools = _types.SimpleNamespace(after_approval=_after_approval)
    patch_tools(fake_tools, None)
    hook.build_secrets_prompt(DESIGN, declaration='{"required_secrets": ["MY_SECRET_TOKEN"]}')
    assert captured["document"] == {"required_secrets": ["MY_SECRET_TOKEN"]}


def test_tools_absent_fails_closed(patch_tools):
    from api import secrets_hook as hook
    patch_tools(None, None)
    result = hook.build_secrets_prompt(DESIGN)
    assert result == {"prompt_shown": False, "available": False,
                      "pending": [], "already_set": []}


def test_success_returns_prompt_and_entry(patch_tools):
    """设计声明了密钥且工具已就绪 → prompt_shown=True + 一次性录入入口。"""
    from api import secrets_hook as hook

    seen = {}

    def _after_approval(document, store, root=None, launcher=None):
        seen["document"] = document
        return {"prompt_shown": True, "available": False,
                "entry_url": "http://localhost/secret-entry",
                "pending": ["SECRET_API_TOKEN"], "already_set": []}

    fake_tools = _types.SimpleNamespace(after_approval=_after_approval)
    patch_tools(fake_tools, None)
    result = hook.build_secrets_prompt(DESIGN)
    assert result["available"] is True          # 宿主侧补上 available=True
    assert result["prompt_shown"] is True
    assert result["entry_url"] == "http://localhost/secret-entry"
    assert result["pending"] == ["SECRET_API_TOKEN"]
    assert "SECRET" in seen["document"]          # 扫的是设计书文本，不带实际值


def test_exception_fails_closed_but_available(patch_tools):
    from api import secrets_hook as hook

    def _boom(*a, **k):
        raise RuntimeError("外部工具仓异常")

    fake_tools = _types.SimpleNamespace(after_approval=_boom)
    patch_tools(fake_tools, None)
    result = hook.build_secrets_prompt(DESIGN)
    assert result == {"prompt_shown": False, "available": True,
                      "pending": [], "already_set": []}


def test_list_secrets_absent_degrades(patch_tools):
    from api import secrets_hook as hook
    patch_tools(None, None)
    assert hook.list_project_secrets() == {"available": False, "count": 0, "entries": []}


def test_secrets_get_route_structured(monkeypatch):
    """GET /api/secrets：工具缺席时返回 success + available=False，不报错。"""
    import api.auth
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import secrets_hook as hook
    from api.routes import design_secret_routes

    monkeypatch.setenv("EAS_API_TOKEN", "secret-test-token")
    monkeypatch.setattr(hook, "_load_tools", lambda: (None, None))
    api.auth.reset_token_cache()

    app = FastAPI()
    app.include_router(design_secret_routes.router)
    with TestClient(app) as client:
        r = client.get("/api/secrets",
                       headers={"Authorization": "Bearer secret-test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["available"] is False
    assert body["count"] == 0


def test_secrets_plan_route_structured(monkeypatch, tmp_path):
    """POST /api/design/{id}/secrets/plan：返回结构化 body，available=False 降级。"""
    import api.auth
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import secrets_hook as hook
    from api.routes import design_secret_routes

    monkeypatch.setenv("EAS_API_TOKEN", "secret-test-token")
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(hook, "_load_tools", lambda: (None, None))
    api.auth.reset_token_cache()

    class _DB:
        def get_design(self, design_id):
            assert design_id == "d1"
            return _FakeDesign()

    monkeypatch.setattr(design_secret_routes, "state",
                        SimpleNamespace(database=_DB()))

    app = FastAPI()
    app.include_router(design_secret_routes.router)
    payload = {"declaration": '{"required_secrets": ["SECRET_API_TOKEN"]}'}
    with TestClient(app) as client:
        r = client.post("/api/design/d1/secrets/plan", json=payload,
                        headers={"Authorization": "Bearer secret-test-token"})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert body.get("prompt_shown") is False
    assert body.get("available") is False