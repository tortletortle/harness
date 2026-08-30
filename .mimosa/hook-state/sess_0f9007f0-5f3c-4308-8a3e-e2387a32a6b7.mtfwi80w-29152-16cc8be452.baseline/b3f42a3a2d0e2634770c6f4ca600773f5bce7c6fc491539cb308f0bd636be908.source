"""计划管理端点（归档 / 重命名 / 删除）的 API 级测试。

不触发 lifespan：直接对 app 发请求，monkeypatch state.database 为
tmp_path 下的临时库；种子数据直接走底层 SQL（先设计书后计划，满足外键）。
"""

import pytest
from fastapi.testclient import TestClient

from api.server import app, state
from api.auth import reset_token_cache
from database import Database

TOKEN = "mgmt-token"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("EAS_API_TOKEN", TOKEN)
    reset_token_cache()
    db = Database(str(tmp_path / "mgmt.db"))
    monkeypatch.setattr(state, "database", db)
    c = TestClient(app)
    yield c, db
    monkeypatch.delenv("EAS_API_TOKEN", raising=False)
    reset_token_cache()


def _headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def _seed(db, plan_id="p1", status="Pending", display_name=None, archived=0):
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO designs (design_id, user_request, timestamp, "
            "part1_design, part2_tool_assessment, user_approval, user_comments, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("11111111-1111-1111-1111-111111111111", "测试需求", "2026-08-30T00:00:00", "{}", "{}", None, None,
             "2026-08-30T00:00:00"),
        )
        conn.execute(
            "INSERT INTO plans (plan_id, design_id, created_at, status, segments, "
            "implementation_list, dynamic_insertions, archived, display_name) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (plan_id, "11111111-1111-1111-1111-111111111111", "2026-08-30T00:00:00", status, "[]", "[]", "[]",
             archived, display_name),
        )
        conn.commit()
    finally:
        conn.close()


def test_management_requires_token(client):
    c, _ = client
    assert c.post("/api/plan/p1/archive", json={"archived": True}).status_code == 401
    assert c.post("/api/plan/p1/rename", json={"name": "x"}).status_code == 401
    assert c.delete("/api/plan/p1").status_code == 401


def test_archive_roundtrip(client):
    c, db = client
    _seed(db)
    resp = c.post("/api/plan/p1/archive", headers=_headers(), json={"archived": True})
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "plan_id": "p1", "archived": True}

    listing = c.get("/api/plans", headers=_headers()).json()["plans"]
    assert listing[0]["archived"] is True

    # 取消归档
    resp = c.post("/api/plan/p1/archive", headers=_headers(), json={"archived": False})
    assert resp.status_code == 200
    listing = c.get("/api/plans", headers=_headers()).json()["plans"]
    assert listing[0]["archived"] is False


def test_archive_unknown_plan_returns_404(client):
    c, _ = client
    resp = c.post("/api/plan/nope/archive", headers=_headers(), json={"archived": True})
    assert resp.status_code == 404


def test_rename_roundtrip_and_validation(client):
    c, db = client
    _seed(db)
    resp = c.post(
        "/api/plan/p1/rename", headers=_headers(), json={"name": " 登录页重构 "}
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "登录页重构"  # 服务端去首尾空白
    listing = c.get("/api/plans", headers=_headers()).json()["plans"]
    assert listing[0]["display_name"] == "登录页重构"

    # 空名称 400
    assert c.post("/api/plan/p1/rename", headers=_headers(), json={"name": "  "}).status_code == 400
    # 超长名称 400
    assert c.post(
        "/api/plan/p1/rename", headers=_headers(), json={"name": "x" * 101}
    ).status_code == 400
    # 未知计划 404
    assert c.post("/api/plan/nope/rename", headers=_headers(), json={"name": "x"}).status_code == 404


def test_delete_plan_removes_rows(client):
    c, db = client
    _seed(db)
    resp = c.delete("/api/plan/p1", headers=_headers())
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "plan_id": "p1"}

    conn = db._get_connection()
    try:
        remaining_plans = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        remaining_steps = conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
    finally:
        conn.close()
    assert remaining_plans == 0
    assert remaining_steps == 0


def test_delete_unknown_plan_returns_404(client):
    c, _ = client
    assert c.delete("/api/plan/nope", headers=_headers()).status_code == 404


def test_delete_running_plan_returns_409(client):
    c, db = client
    _seed(db, plan_id="p-run", status="Running")
    resp = c.delete("/api/plan/p-run", headers=_headers())
    assert resp.status_code == 409
    # 计划仍在
    conn = db._get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1
    finally:
        conn.close()


def test_delete_design_roundtrip(client):
    c, db = client
    _seed(db)  # 设计书 d1 + 计划 p1（外键引用 d1）
    resp = c.delete("/api/design/11111111-1111-1111-1111-111111111111", headers=_headers())
    assert resp.status_code == 409  # 被计划引用，禁止删除

    # 先删计划，再删设计书
    assert c.delete("/api/plan/p1", headers=_headers()).status_code == 200
    resp = c.delete("/api/design/11111111-1111-1111-1111-111111111111", headers=_headers())
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "design_id": "11111111-1111-1111-1111-111111111111"}
    # 重复删除 → 404
    assert c.delete("/api/design/11111111-1111-1111-1111-111111111111", headers=_headers()).status_code == 404
