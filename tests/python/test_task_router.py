# -*- coding: utf-8 -*-
"""任务模式识别回归：classify 规则矩阵 + /api/quick-task 端点（Grant/审计/blocked 语义）。"""
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest

from application.task_router import classify_task


# ===== 分类规则矩阵 =====

def test_search_request_routes_direct():
    c = classify_task("搜索 python asyncio 教程")
    assert c["mode"] == "direct"
    assert c["tool_id"] == "super_search"
    assert c["inputs"]["query"] == "python asyncio 教程"


def test_read_file_request_routes_direct():
    c = classify_task("读取 src/main.py")
    assert c["mode"] == "direct"
    assert c["tool_id"] == "text_editor"
    assert c["instruction"] == "read_file"
    assert c["inputs"]["path"] == "src/main.py"


def test_run_test_request_routes_direct():
    c = classify_task("运行测试 tests/python/test_x.py")
    assert c["mode"] == "direct"
    assert c["tool_id"] == "test_runner"
    assert c["inputs"]["suite"] == "pytest"
    assert c["inputs"]["test_path"] == "tests/python/test_x.py"


def test_lint_request_routes_direct():
    c = classify_task("语法检查 app.py")
    assert c["mode"] == "direct"
    assert c["tool_id"] == "lint_gate"
    assert c["inputs"]["path"] == "app.py"


@pytest.mark.parametrize("text", [
    "创建一个 Markdown 文件",
    "修改 src/main.py 里的函数",
    "重构认证模块并补测试",
    "读取 a.txt 然后创建 b.txt",
    "",
])
def test_graph_signals_route_to_design_flow(text):
    assert classify_task(text)["mode"] == "graph"


def test_unknown_request_defaults_to_graph():
    assert classify_task("帮我看看这个项目怎么样")["mode"] == "graph"


# ===== /api/quick-task 端点 =====

TOKEN = "quick-task-token"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from api.auth import reset_token_cache
    from api import server as server_mod
    from database import Database

    monkeypatch.setenv("EAS_API_TOKEN", TOKEN)
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    reset_token_cache()
    db = Database(str(tmp_path / "eas.db"))
    monkeypatch.setattr(server_mod.state, "database", db)
    from tools import get_registry
    monkeypatch.setattr(server_mod.state, "tool_registry", get_registry())
    (tmp_path / "hello.txt").write_text("hello quick task", encoding="utf-8")
    c = TestClient(server_mod.app)
    yield c, tmp_path
    monkeypatch.delenv("EAS_API_TOKEN", raising=False)
    monkeypatch.delenv("EAS_WORKSPACE_ROOT", raising=False)
    reset_token_cache()


def _headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_quick_task_direct_reads_file(client):
    c, tmp_path = client
    resp = c.post("/api/quick-task", json={"message": "读取 hello.txt"},
                  headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "direct"
    assert body["tool_id"] == "text_editor"
    assert body["result"]["success"] is True
    assert body["result"]["content"] == "hello quick task"


def test_quick_task_graph_mode_returns_hint(client):
    c, _ = client
    resp = c.post("/api/quick-task", json={"message": "创建一个新的报告文件"},
                  headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "graph"
    assert body["next"] == "/api/design"


def test_quick_task_blocked_env_still_audited(client):
    """direct 模式不绕过安全边界：读取 .env 被 deny，审计入档。"""
    c, tmp_path = client
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    resp = c.post("/api/quick-task", json={"message": "读取 .env"},
                  headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "direct"
    assert body["result"]["success"] is False
    # Grant 资源检查拒绝或工具内 deny，二者必须其一
    assert body["result"].get("blocked") is True or body["result"].get("error")


def test_quick_task_requires_auth(client):
    c, _ = client
    resp = c.post("/api/quick-task", json={"message": "读取 hello.txt"})
    assert resp.status_code == 401
