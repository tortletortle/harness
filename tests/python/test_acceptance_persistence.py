"""Structured acceptance persistence and branch-copy tests."""

from datetime import datetime, timezone
from uuid import uuid4

from database import Database
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


def _design():
    return DesignDocument(
        design_id=uuid4(),
        user_request="acceptance persistence",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1.0, risks=[]
        ),
    )


def test_acceptance_survives_plan_round_trip_and_branch_clone(tmp_path):
    db = Database(str(tmp_path / "eas.db"))
    design = _design()
    assert db.save_design(design)

    first = Step(
        order=1,
        description="first",
        tool="file_creator",
        inputs={"path": "first.txt"},
        check_criteria="执行成功",
    )
    second = Step(
        order=2,
        description="second",
        tool="file_creator",
        inputs={"path": "second.txt"},
        check_criteria="执行成功",
        acceptance=[
            {"type": "file_exists", "path": "second.txt", "kind": "file"},
            {"type": "content_contains", "path": "second.txt", "text": "ok"},
        ],
    )
    plan = ExecutionPlan(
        plan_id=uuid4(),
        design_id=design.design_id,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status="Pending",
        segments=[PlanSegment(segment_id=uuid4(), description="segment", steps=[first, second])],
        implementation_list=[],
    )
    assert db.save_plan(plan)

    loaded = db.get_plan(str(plan.plan_id))
    assert loaded is not None
    assert loaded.segments[0].steps[1].acceptance == second.acceptance

    assert db.clone_steps_from(str(plan.plan_id), str(first.step_id), 2)
    rows = db.get_steps(str(plan.plan_id))
    clones = [row for row in rows if row["branch_version"] == 2]
    assert len(clones) == 1
    assert clones[0]["acceptance_json"]
    assert clones[0]["acceptance_json"] == rows[1]["acceptance_json"]
