import asyncio
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime

from database import Database
from models import (
    Architecture,
    DesignDocument,
    DesignPart1,
    Difficulty,
    AttemptStatus,
    ExecutionPlan,
    PlanSegment,
    PlanStatus,
    RunStatus,
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


def test_run_heartbeat_requires_latest_active_run(tmp_path):
    db = Database(str(tmp_path / "eas.db"))
    plan_id, _ = _plan(db)

    old_run = db.start_execution_run(
        plan_id, (PlanStatus.PENDING.value,), owner_token="owner-old"
    )
    assert old_run
    # 模拟旧执行仍存活、但另一个实例已重置并认领了该计划。
    assert db.update_plan_state_machine(plan_id, PlanStatus.PENDING.value)
    new_run = db.start_execution_run(
        plan_id, (PlanStatus.PENDING.value,), owner_token="owner-new"
    )
    assert new_run

    assert db.get_execution_run(old_run["run_id"])["status"] == RunStatus.RUNNING.value
    assert not db.heartbeat_execution_run(old_run["run_id"], "owner-old")
    assert db.heartbeat_execution_run(new_run["run_id"], "owner-new")


def test_finish_attempt_rechecks_run_fence_atomically(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "eas.db"))
    plan_id, step_id = _plan(db)
    run = db.start_execution_run(
        plan_id, (PlanStatus.PENDING.value,), owner_token="owner-1"
    )
    attempt = db.create_execution_attempt(run["run_id"], plan_id, step_id, 1)
    original_get_connection = db._get_connection
    fenced = False

    def get_connection_with_fence_race():
        conn = original_get_connection()

        def fence_before_attempt_update(statement):
            nonlocal fenced
            normalized = statement.lstrip().upper()
            if not fenced and normalized.startswith(
                "UPDATE EXECUTION_ATTEMPTS SET STATUS"
            ):
                fenced = True
                # 在 owner 检查完成后、Attempt UPDATE 执行前，另一执行上下文收尾 Run。
                with closing(sqlite3.connect(db.db_path, timeout=30.0)) as other, other:
                    other.execute(
                        "UPDATE execution_runs SET status = ? WHERE run_id = ?",
                        (RunStatus.INTERRUPTED.value, run["run_id"]),
                    )

        conn.set_trace_callback(fence_before_attempt_update)
        return conn

    monkeypatch.setattr(db, "_get_connection", get_connection_with_fence_race)
    finished = db.finish_execution_attempt(
        attempt["attempt_id"], AttemptStatus.SUCCEEDED.value,
        run_id=run["run_id"], owner_token="owner-1",
    )

    assert fenced, "test must fence the Run between the old check and write"
    assert not finished
    assert db.list_execution_attempts(run["run_id"])[0]["status"] == AttemptStatus.RUNNING.value


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
