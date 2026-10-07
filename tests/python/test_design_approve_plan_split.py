"""批准与生成计划解耦：approve 不调 generate_plan；generate-plan 要求已批准。"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import Database
from models import (
    Architecture,
    DesignDocument,
    DesignPart1,
    Difficulty,
    ExecutionPlan,
    PlanSegment,
    PlanStatus,
    ToolAssessment,
    ToolRequirement,
)


def _design(db: Database, *, approved: bool = False, unavailable: bool = False) -> str:
    design_id = uuid4()
    tools = [
        ToolRequirement(
            tool_id="file_creator",
            availability="unavailable" if unavailable else "available",
            impact="写文件",
        )
    ]
    db.save_design(
        DesignDocument(
            design_id=design_id,
            user_request="拆分审批回归",
            timestamp=datetime.now(timezone.utc),
            part1_design=DesignPart1(
                architecture=Architecture(components=["c"], interactions=["i"]),
                difficulty=Difficulty(overall="low", breakdown={}),
            ),
            part2_tool_assessment=ToolAssessment(
                required_tools=tools,
                overall_tool_completeness=1.0,
                risks=[],
            ),
            user_approval=approved,
        )
    )
    return str(design_id)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from api import shared
    from api.auth import require_token
    from api.routes import design_routes as routes

    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    db = Database(str(tmp_path / "split.db"))
    plan = ExecutionPlan(
        plan_id=uuid4(),
        design_id=uuid4(),
        status=PlanStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=[])],
        implementation_list=[],
    )
    designer = SimpleNamespace(
        _fulfill_required_tools=AsyncMock(side_effect=lambda d: d),
        generate_plan=AsyncMock(return_value=plan),
        revise_design=AsyncMock(),
    )
    isolated = SimpleNamespace(
        database=db,
        designer=designer,
        notifier=None,
        plan_update_version=0,
        authorization_manager=None,
    )
    monkeypatch.setattr(routes, "state", isolated)
    monkeypatch.setattr(shared, "state", isolated)
    monkeypatch.setattr(shared, "_send_to_clients", AsyncMock())
    monkeypatch.setattr(routes, "broadcast_log", AsyncMock())
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_token] = lambda: None
    with TestClient(app) as client:
        yield SimpleNamespace(db=db, client=client, designer=designer, plan=plan)


def test_approve_does_not_generate_plan(env):
    design_id = _design(env.db)
    r = env.client.post(
        f"/api/design/{design_id}/approve",
        json={"design_id": design_id, "approved": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["approved"] is True
    assert "plan" not in body or body.get("plan") is None
    assert body["design"]["user_approval"] is True
    env.designer.generate_plan.assert_not_called()
    saved = env.db.get_design(design_id)
    assert saved.user_approval is True


def test_generate_plan_requires_approval(env):
    design_id = _design(env.db, approved=False)
    r = env.client.post(f"/api/design/{design_id}/generate-plan")
    assert r.status_code == 409
    env.designer.generate_plan.assert_not_called()


def test_generate_plan_after_approve(env):
    from uuid import UUID

    design_id = _design(env.db, approved=True)
    env.plan.design_id = UUID(design_id)

    r = env.client.post(f"/api/design/{design_id}/generate-plan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["plan"]["plan_id"]
    env.designer.generate_plan.assert_awaited_once()
    assert env.designer.generate_plan.await_args.kwargs.get("skip_fulfill") is True
