"""Database-backed dependency selection tests."""

from datetime import datetime, timezone
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
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
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
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
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
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
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
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=[a, b])],
        implementation_list=[],
    )
    with pytest.raises(DatabaseError):
        db.save_plan(plan)


def _seed_graph(db, specs, max_concurrency=2, parallel=True):
    """specs: list of (name, depends_on_names, resources_or_None)."""
    design_id = uuid4()
    db.save_design(DesignDocument(
        design_id=design_id,
        user_request="graph",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
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
    for order, (name, deps, resources) in enumerate(specs, 1):
        step = Step(
            step_id=uuid4(),
            order=order,
            description=name,
            tool="manual_tool",
            inputs={},
            check_criteria="手动检查",
            depends_on=[by_name[d] for d in deps],
            resources=resources,
        )
        by_name[name] = str(step.step_id)
        steps.append(step)
    plan = ExecutionPlan(
        plan_id=uuid4(),
        design_id=design_id,
        status=PlanStatus.PENDING,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        segments=[PlanSegment(segment_id=uuid4(), description="g", steps=steps)],
        implementation_list=[],
    )
    db.save_plan(plan)
    return plan, steps


def test_select_next_batch_runs_independent_ready_steps_together(tmp_path):
    db = Database(str(tmp_path / "batch.db"))
    plan, steps = _seed_graph(db, [("a", [], None), ("b", ["a"], None), ("c", ["a"], None)])
    first = db.select_next_batch(str(plan.plan_id), max_concurrency=2, parallel_graphs=True)
    assert [s["step_id"] for s in first["steps"]] == [str(steps[0].step_id)]

    db.update_step(str(steps[0].step_id), "Success")
    second = db.select_next_batch(str(plan.plan_id), max_concurrency=2, parallel_graphs=True)
    assert {s["step_id"] for s in second["steps"]} == {str(steps[1].step_id), str(steps[2].step_id)}


def test_select_next_batch_respects_write_conflict(tmp_path):
    db = Database(str(tmp_path / "conflict.db"))
    plan, steps = _seed_graph(db, [
        ("a", [], None),
        ("b", ["a"], {"write": ["src/x.py"]}),
        ("c", ["a"], {"write": ["src/x.py"]}),
    ])
    db.update_step(str(steps[0].step_id), "Success")
    second = db.select_next_batch(str(plan.plan_id), max_concurrency=2, parallel_graphs=True)
    assert len(second["steps"]) == 1, "写同一文件的步骤不能同批"
    assert second["steps"][0]["step_id"] in (str(steps[1].step_id), str(steps[2].step_id))


def test_select_next_batch_read_write_conflict(tmp_path):
    db = Database(str(tmp_path / "rw.db"))
    plan, steps = _seed_graph(db, [
        ("a", [], None),
        ("b", ["a"], {"read": ["src/x.py"]}),
        ("c", ["a"], {"write": ["src/x.py"]}),
    ])
    db.update_step(str(steps[0].step_id), "Success")
    second = db.select_next_batch(str(plan.plan_id), max_concurrency=2, parallel_graphs=True)
    assert len(second["steps"]) == 1


def test_select_next_batch_legacy_plan_stays_serial(tmp_path):
    db = Database(str(tmp_path / "legacy_batch.db"))
    plan, steps = _seed_graph(db, [("a", [], None), ("b", [], None), ("c", [], None)])
    batch = db.select_next_batch(str(plan.plan_id), max_concurrency=4, parallel_graphs=True)
    assert len(batch["steps"]) == 1, "无显式依赖的旧计划必须保持串行"


def test_select_next_batch_respects_parallel_disabled(tmp_path):
    db = Database(str(tmp_path / "disabled.db"))
    plan, steps = _seed_graph(db, [("a", [], None), ("b", ["a"], None), ("c", ["a"], None)])
    db.update_step(str(steps[0].step_id), "Success")
    batch = db.select_next_batch(str(plan.plan_id), max_concurrency=2, parallel_graphs=False)
    assert len(batch["steps"]) == 1, "parallel_graphs=False 时不允许批并行"


@pytest.mark.asyncio
async def test_execute_plan_background_runs_ready_batch_in_parallel(tmp_path, monkeypatch):
    """端到端：显式 DAG a→b、a→c 且启用并行时，b/c 在同一批并发执行，计划 Completed。"""
    import asyncio
    from types import SimpleNamespace

    from api import server as server_mod

    db = Database(str(tmp_path / "parallel_e2e.db"))
    server_mod.state.database = db
    server_mod.state.executor_max_concurrency = 2
    server_mod.state.parallel_graphs = True

    plan, steps = _seed_graph(db, [("a", [], None), ("b", ["a"], None), ("c", ["a"], None)])
    overlapping: dict[str, bool] = {}

    async def _fake_step_state_machine(plan, row):
        step_id = str(row["step_id"])
        # 记录是否与其它步骤同时处于 Running。
        running = {sid for sid, flag in overlapping.items() if flag}
        if running:
            overlapping["concurrent"] = True
        overlapping[step_id] = True
        await asyncio.sleep(0.05)
        overlapping[step_id] = False
        db.update_step_state_machine(step_id=step_id, status="Success", outputs={})
        return {"success": True, "check_status": "unverified", "output": {}, "error": None, "blocked": False, "retryable": False}

    monkeypatch.setattr(server_mod, "_execute_step_with_state_machine", _fake_step_state_machine)
    await server_mod.execute_plan_background(db.get_plan(str(plan.plan_id)))

    assert db.get_plan_status(str(plan.plan_id)) == "Completed"
    assert overlapping.get("concurrent"), "b/c 应并发执行（至少两个步骤同时 Running）"
    assert all(r["status"] == "Success" for r in db.get_steps(str(plan.plan_id)))


@pytest.mark.asyncio
async def test_execute_plan_background_serial_when_parallel_disabled(tmp_path, monkeypatch):
    """显式 DAG 但 parallel_graphs=False：仍逐批单步执行，行为与旧版一致。"""
    import asyncio

    from api import server as server_mod

    db = Database(str(tmp_path / "serial_e2e.db"))
    server_mod.state.database = db
    server_mod.state.executor_max_concurrency = 2
    server_mod.state.parallel_graphs = False

    plan, steps = _seed_graph(db, [("a", [], None), ("b", ["a"], None), ("c", ["a"], None)])
    seen: list[str] = []

    async def _fake_step_state_machine(plan, row):
        step_id = str(row["step_id"])
        seen.append(step_id)
        await asyncio.sleep(0.02)
        db.update_step_state_machine(step_id=step_id, status="Success", outputs={})
        return {"success": True, "check_status": "unverified", "output": {}, "error": None, "blocked": False, "retryable": False}

    monkeypatch.setattr(server_mod, "_execute_step_with_state_machine", _fake_step_state_machine)
    await server_mod.execute_plan_background(db.get_plan(str(plan.plan_id)))

    assert db.get_plan_status(str(plan.plan_id)) == "Completed"
    assert seen == [str(s.step_id) for s in steps], "未启用并行时必须按顺序逐步骤执行"


