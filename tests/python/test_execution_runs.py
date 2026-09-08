import asyncio
import uuid
from datetime import datetime

from database import Database
from models import (
    Architecture,
    DesignDocument,
    DesignPart1,
    Difficulty,
    ExecutionPlan,
    PlanSegment,
    PlanStatus,
    Step,
    StepStatus,
    ToolAssessment,
)


def _plan(db):
    design_id = uuid.uuid4()
    db.save_design(DesignDocument(
        design_id=design_id,
        user_request="run lifecycle",
        timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1.0, risks=[]
        ),
    ))
    plan_id = uuid.uuid4()
    step_id = uuid.uuid4()
    db.save_plan(ExecutionPlan(
        plan_id=plan_id,
        design_id=design_id,
        created_at=datetime.utcnow(),
        status=PlanStatus.PENDING,
        segments=[PlanSegment(
            segment_id=uuid.uuid4(), description="s",
            steps=[Step(
                step_id=step_id, order=0, description="step", tool="file_creator",
                inputs={"path": "x"}, check_criteria="ok", status=StepStatus.PENDING,
            )],
        )],
        implementation_list=[], dynamic_insertions=[],
    ))
    return str(plan_id), str(step_id)


def test_run_attempt_fencing_and_orphan_recovery(tmp_path):
    db = Database(str(tmp_path / "eas.db"))
    plan_id, step_id = _plan(db)

    run = db.start_execution_run(plan_id, (PlanStatus.PENDING.value,), owner_token="owner-1")
    assert run and run["owner_token"] == "owner-1"
    # 计划已 Running：从 PENDING 认领必须失败（阻止双 Run 并发执行同一计划）
    assert db.start_execution_run(plan_id, (PlanStatus.PENDING.value,), owner_token="owner-2") is None

    attempt = db.create_execution_attempt(run["run_id"], plan_id, step_id, 1)
    assert not db.finish_execution_attempt(
        attempt["attempt_id"], "Succeeded", run_id=run["run_id"], owner_token="wrong"
    )
    assert db.finish_execution_attempt(
        attempt["attempt_id"], "Succeeded", run_id=run["run_id"], owner_token="owner-1"
    )
    # 终态 attempt 不可二次 finish
    assert not db.finish_execution_attempt(
        attempt["attempt_id"], "Failed", run_id=run["run_id"], owner_token="owner-1"
    )

    # 模拟重启：旧 Run 未收尾（plan 被新实例改回 Pending），新 Run 与旧并存
    db.update_plan_state_machine(plan_id, PlanStatus.PENDING.value)
    run2 = db.start_execution_run(plan_id, (PlanStatus.PENDING.value,), owner_token="owner-2")
    attempt2 = db.create_execution_attempt(run2["run_id"], plan_id, step_id, 1)
    assert db.recover_orphaned_runs() == 2
    assert db.get_execution_run(run["run_id"])["status"] == "Interrupted"
    assert db.get_execution_run(run2["run_id"])["status"] == "Interrupted"
    assert db.list_execution_attempts(run2["run_id"])[0]["status"] == "Interrupted"
    assert attempt2["attempt_id"]


def test_run_endpoints_strip_owner_token(tmp_path, monkeypatch):
    import api.server as server

    db = Database(str(tmp_path / "eas.db"))
    plan_id, step_id = _plan(db)
    run = db.start_execution_run(plan_id, (PlanStatus.PENDING.value,), owner_token="secret")
    db.create_execution_attempt(run["run_id"], plan_id, step_id, 1)
    monkeypatch.setattr(server.state, "database", db)

    runs = asyncio.run(server.get_plan_runs(plan_id))["runs"]
    one = asyncio.run(server.get_run(run["run_id"]))["run"]
    attempts = asyncio.run(server.get_run_attempts(run["run_id"]))["attempts"]
    assert all("owner_token" not in item for item in runs)
    assert "owner_token" not in one
    assert all("owner_token" not in item for item in attempts)
