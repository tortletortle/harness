"""Worktree HTTP 路由回归：真实 SQLite + 隔离 FastAPI，Git 用桩避免改动工作区。"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import Database, DatabaseError
from models import (
    Architecture, DesignDocument, DesignPart1, Difficulty, ExecutionPlan,
    PlanSegment, PlanStatus, ToolAssessment,
)


@pytest.fixture
def route_env(tmp_path, monkeypatch):
    from api import shared
    from api.auth import require_token
    from api.routes import worktree_routes as routes
    from tools.git_operator import GitOperator

    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    db = Database(str(tmp_path / "routes.db"))
    design_id = uuid4()
    db.save_design(DesignDocument(
        design_id=design_id, user_request="worktree route regression",
        timestamp=datetime.now(timezone.utc),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1, risks=[],
        ),
        user_approval=True,
    ))
    plan = ExecutionPlan(
        plan_id=uuid4(), design_id=design_id, status=PlanStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=[])],
        implementation_list=[],
    )
    db.save_plan(plan)
    plan_id = str(plan.plan_id)
    worktree_id = "route-wt"
    repo_path = str(tmp_path / "repo")
    worktree_path = str(tmp_path / "repo-wt-task")
    db.create_worktree_record(
        worktree_id=worktree_id, plan_id=plan_id, repo_path=repo_path,
        worktree_path=worktree_path, branch="worktree/task",
    )
    isolated_state = SimpleNamespace(database=db, notifier=None, plan_update_version=0)
    monkeypatch.setattr(routes, "state", isolated_state)
    monkeypatch.setattr(shared, "state", isolated_state)
    # 保留真实 broadcast_plan_update 的导入与执行，仅替换网络发送边界。
    # 因此成功路由确实覆盖缺失导入，不能通过补造路由全局名称掩盖缺陷。
    sent = AsyncMock()
    monkeypatch.setattr(shared, "_send_to_clients", sent)
    git = Mock(return_value={
        "success": True, "message": "操作成功", "branch": "worktree/task", "output": "ok",
    })
    monkeypatch.setattr(GitOperator, "execute", git)
    close = Mock(wraps=db.close_worktree)
    monkeypatch.setattr(db, "close_worktree", close)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_token] = lambda: None
    with TestClient(app) as client:
        yield SimpleNamespace(
            db=db, client=client, app=app, plan_id=plan_id,
            worktree_id=worktree_id, repo_path=repo_path, worktree_path=worktree_path,
            git=git, close=close, sent=sent,
        )


def _post(env, action, plan_id=None):
    return env.client.post(f"/api/plan/{plan_id or env.plan_id}/worktree/{action}")


@pytest.mark.parametrize("action,terminal,instruction", [
    ("merge", "merged", "worktree_merge"),
    ("discard", "discarded", "worktree_remove"),
])
def test_success_closes_binding_and_broadcasts(route_env, action, terminal, instruction):
    env = route_env
    response = _post(env, action)
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    if action == "discard":
        assert response.json()["removed"] is True
    assert env.db.get_active_worktree(env.plan_id) is None
    assert env.db.get_worktree(env.worktree_id)["status"] == terminal
    env.close.assert_called_once_with(env.worktree_id, terminal)
    inputs = {"path": env.repo_path, "worktree_path": env.worktree_path}
    if action == "merge":
        inputs["message"] = None
    env.git.assert_called_once_with(instruction, inputs)
    payloads = [call.args[0] for call in env.sent.await_args_list]
    updates = [payload for payload in payloads if payload["type"] == "plan_update"]
    assert len(updates) == 1
    assert updates[0]["plan_id"] == env.plan_id
    assert any(payload["type"] == "log" for payload in payloads)


@pytest.mark.parametrize("action,conflict", [
    ("merge", True), ("merge", False), ("discard", False),
])
def test_git_failure_preserves_active_binding(route_env, action, conflict):
    env = route_env
    env.git.return_value = {
        "success": False, "conflict": conflict, "message": "操作失败",
        "error": "permission denied", "output": "git failure details",
    }
    response = _post(env, action)
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "permission denied"
    assert body["output"] == "git failure details"
    if action == "discard":
        assert body["removed"] is False
        assert body["code"] == "worktree_remove_failed"
        assert body["detail"]
    else:
        assert body["conflict"] is conflict
    assert env.db.get_active_worktree(env.plan_id)["worktree_id"] == env.worktree_id
    env.close.assert_not_called()
    env.sent.assert_not_awaited()


@pytest.mark.parametrize("action", ["merge", "discard"])
@pytest.mark.parametrize("failure", ["not_found", "database_error"])
def test_binding_write_failure_is_not_success(route_env, action, failure):
    env = route_env
    if failure == "not_found":
        env.close.side_effect = lambda *_: False
    else:
        env.close.side_effect = DatabaseError("数据库写入失败")
    response = _post(env, action)
    assert response.status_code == 500
    assert response.json()["detail"]
    assert env.db.get_active_worktree(env.plan_id) is not None
    env.git.assert_called_once()
    env.close.assert_called_once()
    env.sent.assert_not_awaited()


@pytest.mark.parametrize("action", ["merge", "discard"])
def test_running_plan_is_rejected_before_git(route_env, action):
    env = route_env
    env.db.update_plan_state_machine(env.plan_id, "Running")
    response = _post(env, action)
    assert response.status_code == 409
    assert env.db.get_active_worktree(env.plan_id) is not None
    env.git.assert_not_called()
    env.close.assert_not_called()
    env.sent.assert_not_awaited()


@pytest.mark.parametrize("action", ["merge", "discard"])
def test_missing_plan_is_rejected_before_git(route_env, action):
    env = route_env
    response = _post(env, action, str(uuid4()))
    assert response.status_code == 404
    env.git.assert_not_called()
    env.close.assert_not_called()
    env.sent.assert_not_awaited()


@pytest.mark.parametrize("action", ["merge", "discard"])
def test_repeated_success_does_not_repeat_git_mutation(route_env, action):
    env = route_env
    assert _post(env, action).status_code == 200
    env.git.reset_mock()
    env.close.reset_mock()
    env.sent.reset_mock()
    response = _post(env, action)
    assert response.status_code == 404  # 保持现有“无 active 绑定”契约。
    env.git.assert_not_called()
    env.close.assert_not_called()
    env.sent.assert_not_awaited()


@pytest.mark.parametrize("action", ["merge", "discard"])
def test_unexpected_git_exception_preserves_binding(route_env, action):
    env = route_env
    env.git.side_effect = OSError("git process unavailable")
    response = _post(env, action)
    assert response.status_code == 500
    assert env.db.get_active_worktree(env.plan_id) is not None
    env.close.assert_not_called()
    env.sent.assert_not_awaited()


@pytest.mark.parametrize("action", ["merge", "discard"])
def test_unauthenticated_request_cannot_mutate_worktree(route_env, action):
    env = route_env
    env.app.dependency_overrides.clear()
    assert _post(env, action).status_code == 401
    env.git.assert_not_called()
    env.close.assert_not_called()
    env.sent.assert_not_awaited()
