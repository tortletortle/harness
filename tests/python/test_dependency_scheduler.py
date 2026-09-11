"""Database-backed dependency selection tests."""

from datetime import datetime
from uuid import uuid4

import pytest

from database import Database, DatabaseError
from models import (
    Architecture,
    DesignDocument,
    DesignPart1,
    Difficulty,
    ExecutionPlan,
    PlanSegment,
    PlanStatus,
    Step,
    ToolAssessment,
)


def _seed_plan(db, specs):
    """specs: list of (description, depends_on). depends_on may use earlier names."""
    design_id = uuid4()
    db.save_design(DesignDocument(
        design_id=design_id,
        user_request="graph",
        timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["x"], interactions=["y"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1, risks=[]
        ),
        user_approval=True,
    ))
    by_name: dict[str, str] = {}
    steps = []
    for order, (name, deps) in enumerate(specs, 1):
        resolved = [by_name[dep] for dep in deps]
        step = Step(
            step_id=uuid4(),
            order=order,
            description=name,
            tool="manual_tool",
            inputs={},
            check_criteria="手动检查",
            depends_on=resolved,
        )
        by_name[name] = str(step.step_id)
        steps.append(step)
    plan = ExecutionPlan(
        plan_id=uuid4(),
        design_id=design_id,
        status=PlanStatus.PENDING,
        created_at=datetime.utcnow(),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=steps)],
        implementation_list=[],
    )
    db.save_plan(plan)
    return plan, steps


def test_dependencies_survive_round_trip(tmp_path):
    db = Database(str(tmp_path / "graph.db"))
    plan, steps = _seed_plan(db, [("a", []), ("b", ["a"])])
    loaded = db.get_plan(str(plan.plan_id))
    assert loaded.segments[0].steps[1].depends_on == [str(steps[0].step_id)]


def test_select_next_step_respects_dependency_and_allows_independent_step(tmp_path):
    db = Database(str(tmp_path / "graph.db"))
    plan, steps = _seed_plan(db, [("a", []), ("b", ["a"])])
    first = db.select_next_step(str(plan.plan_id))
    assert first["step"]["step_id"] == str(steps[0].step_id)

    db.update_step(str(steps[0].step_id), "Success")
    second = db.select_next_step(str(plan.plan_id))
    assert second["step"]["step_id"] == str(steps[1].step_id)


def test_select_next_step_returns_independent_step_while_dependency_pending(tmp_path):
    db = Database(str(tmp_path / "graph.db"))
    plan, steps = _seed_plan(db, [("a", []), ("b", ["a"]), ("c", [])])
    # a is first by order; c has no dependencies and is also ready.
    db.update_step(str(steps[0].step_id), "Running")
    selected = db.select_next_step(str(plan.plan_id))
    assert selected["step"]["step_id"] == str(steps[2].step_id)


def test_select_next_step_reports_failed_dependency(tmp_path):
    db = Database(str(tmp_path / "blocked.db"))
    plan, steps = _seed_plan(db, [("a", []), ("b", ["a"])])
    db.update_step(str(steps[0].step_id), "Failed")
    selected = db.select_next_step(str(plan.plan_id))
    assert selected["step"] is None
    assert "依赖步骤失败" in selected["blocked_reason"]


def test_legacy_linear_plan_without_dependencies_stays_ordered(tmp_path):
    db = Database(str(tmp_path / "legacy.db"))
    plan, steps = _seed_plan(db, [("a", []), ("b", []), ("c", [])])
    assert db.select_next_step(str(plan.plan_id))["step"]["step_id"] == str(steps[0].step_id)
    db.update_step(str(steps[0].step_id), "Success")
    assert db.select_next_step(str(plan.plan_id))["step"]["step_id"] == str(steps[1].step_id)


def test_save_plan_rejects_cyclic_graph(tmp_path):
    db = Database(str(tmp_path / "cyclic.db"))
    design_id = uuid4()
    db.save_design(DesignDocument(
        design_id=design_id,
        user_request="cyclic",
        timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["x"], interactions=["y"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1, risks=[]
        ),
        user_approval=True,
    ))
    a, b = Step(step_id=uuid4(), order=1, description="a", tool="manual_tool", inputs={}, check_criteria="手动检查"), Step(step_id=uuid4(), order=2, description="b", tool="manual_tool", inputs={}, check_criteria="手动检查")
    a.depends_on = [str(b.step_id)]
    b.depends_on = [str(a.step_id)]
    plan = ExecutionPlan(
        plan_id=uuid4(), design_id=design_id, status=PlanStatus.PENDING,
        created_at=datetime.utcnow(),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=[a, b])],
        implementation_list=[],
    )
    with pytest.raises(DatabaseError):
        db.save_plan(plan)
