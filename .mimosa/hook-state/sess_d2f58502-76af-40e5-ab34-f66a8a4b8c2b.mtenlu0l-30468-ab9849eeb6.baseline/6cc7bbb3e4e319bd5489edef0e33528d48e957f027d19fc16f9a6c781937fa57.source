"""
P0-a 工具审批端点的 API 级测试。

不触发 lifespan（避免真实 DB/LLM 初始化）：直接对 app 发请求，
用 EAS_API_TOKEN 环境变量控制鉴权，替换 state.approval_manager 为受控实例。
"""

import pytest
from fastapi.testclient import TestClient

from api.server import app, state
from api.approval import ApprovalManager
from api.auth import reset_token_cache

TOKEN = "approval-endpoint-test-token"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("EAS_API_TOKEN", TOKEN)
    reset_token_cache()

    mgr = ApprovalManager(required_tools=["command_runner"], timeout_seconds=30)
    monkeypatch.setattr(state, "approval_manager", mgr)

    # 不进入 lifespan 上下文（with 语句会触发 lifespan，覆盖 state 并触碰真实 DB）：
    # TestClient 直接发请求时只注册路由与依赖，不运行启动/关闭事件。
    c = TestClient(app)
    yield c, mgr

    monkeypatch.delenv("EAS_API_TOKEN", raising=False)
    reset_token_cache()


def _headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_approval_requires_token(client):
    c, _ = client
    resp = c.get("/api/approvals")
    assert resp.status_code == 401


def test_list_pending_empty(client):
    c, _ = client
    resp = c.get("/api/approvals", headers=_headers())
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "approvals": []}


def test_approve_unknown_id_returns_404(client):
    c, _ = client
    resp = c.post(
        "/api/plan/p1/approval/nonexistent/approve",
        headers=_headers(),
        json={"comment": None},
    )
    assert resp.status_code == 404


def test_reject_flow_resolves_request(client):
    c, mgr = client
    req = mgr.create("p1", "s1", "command_runner", "run", {"command": "ls"})
    assert len(mgr.list_pending()) == 1

    resp = c.post(
        f"/api/plan/p1/approval/{req.approval_id}/reject",
        headers=_headers(),
        json={"comment": "too risky"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "approved": False}
    assert req.resolved and req.approved is False

    # 已处理的请求从待审批列表消失；再次 reject 返回 404
    assert mgr.list_pending() == []
    resp2 = c.post(
        f"/api/plan/p1/approval/{req.approval_id}/reject",
        headers=_headers(),
        json={"comment": None},
    )
    assert resp2.status_code == 404


def test_plan_mismatch_returns_409(client):
    c, mgr = client
    req = mgr.create("p1", "s1", "command_runner", "run", {})
    resp = c.post(
        f"/api/plan/other-plan/approval/{req.approval_id}/approve",
        headers=_headers(),
        json={"comment": None},
    )
    assert resp.status_code == 409
