"""End-to-end state-machine assertion for deterministic acceptance."""

from datetime import datetime
from uuid import uuid4

import pytest

from database import Database
from executor import DefaultExecutor
from llm_client import LLMClient
from models import (
    Architecture,
    DesignDocument,
    DesignPart1,
    Difficulty,
    ExecutionPlan,
    PlanSegment,
    Step,
    ToolAssessment,
)


class SuccessWithoutSideEffectRegistry:
    def execute_tool(self, tool_id, instruction, inputs):
        # 模拟工具错误地自报成功，但没有真正创建目标文件
        return {"success": True, "message": "reported success"}


def _design():
    return DesignDocument(
        design_id=uuid4(),
        user_request="acceptance state machine",
        timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1.0, risks=[]
        ),
    )


@pytest.mark.asyncio
async def test_state_machine_does_not_mark_false_tool_success_as_success(tmp_path, monkeypatch):
    import api.server as server

    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    db = Database(str(tmp_path / "eas.db"))
    design = _design()
    assert db.save_design(design)
    step = Step(
        order=1,
        description="create expected file",
        tool="file_creator:create_file",
        inputs={"path": "expected.txt"},
        check_criteria="执行成功",
        acceptance=[
            {"type": "file_exists", "path": "expected.txt", "kind": "file"},
            {"type": "content_contains", "path": "expected.txt", "text": "hello"},
        ],
    )
    plan = ExecutionPlan(
        plan_id=uuid4(),
        design_id=design.design_id,
        created_at=datetime.utcnow(),
        status="Pending",
        segments=[PlanSegment(segment_id=uuid4(), description="segment", steps=[step])],
        implementation_list=[],
    )
    assert db.save_plan(plan)

    monkeypatch.setattr(server.state, "database", db)
    monkeypatch.setattr(
        server.state,
        "executor",
        DefaultExecutor(
            llm_client=LLMClient(),
            model_config=None,
            tool_registry=SuccessWithoutSideEffectRegistry(),
            database=None,
        ),
    )
    monkeypatch.setattr(server.state, "websocket_clients", [])
    monkeypatch.setattr(server.state, "current_execution", None)
    monkeypatch.setattr(server.state, "retry_max_attempts", 1)
    monkeypatch.setattr(server.state, "retry_backoff_factor", 1)

    await server.execute_plan_background(plan)

    assert db.get_plan_status(str(plan.plan_id)) == "Failed"
    rows = db.get_steps(str(plan.plan_id))
    assert rows[0]["status"] == "Failed"
    outputs = __import__("json").loads(rows[0]["outputs"])
    assert outputs["acceptance_status"] == "failed"
    assert outputs["reward"] == 0.0
    assert any(not check["passed"] for check in outputs["acceptance"]["checks"])
