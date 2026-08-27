"""
阶段3：可靠性——孤儿任务恢复。

单进程部署下，进程重启意味着之前标记为 Running 的执行必然已经中断。
recover_orphaned_executions() 应该在服务启动时把这些残留状态清理掉，
避免计划/步骤永久卡在 Running。
"""

import uuid
from datetime import datetime

from database import Database
from models import (
    ExecutionPlan, PlanSegment, Step, StepStatus, PlanStatus,
    DesignDocument, DesignPart1, Architecture, Difficulty, ToolAssessment,
)


def _make_design(db, design_id):
    design = DesignDocument(
        design_id=design_id,
        user_request="孤儿恢复验证",
        timestamp=datetime.now(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["c"], interactions=["i"]),
            difficulty=Difficulty(overall="low", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1.0, risks=[]
        ),
        user_approval=True,
    )
    assert db.save_design(design)


def _make_plan(db, plan_id, design_id, plan_status, step_statuses):
    steps = []
    for i, s in enumerate(step_statuses):
        steps.append(Step(
            step_id=uuid.uuid4(), order=i, description=f"step{i}",
            tool="file_creator", inputs={"path": f"{i}.txt"},
            check_criteria="ok", status=s,
        ))
    plan = ExecutionPlan(
        plan_id=plan_id, design_id=design_id, created_at=datetime.now(),
        status=plan_status,
        segments=[PlanSegment(segment_id=uuid.uuid4(), description="seg", steps=steps)],
        implementation_list=[], dynamic_insertions=[],
    )
    assert db.save_plan(plan)
    # save_plan 只在 INSERT 时写 status；用 update_plan 显式回写，模拟真实执行中状态
    db.update_plan(str(plan_id), plan_status.value)
    for step, status in zip(steps, step_statuses):
        db.update_step(str(step.step_id), status.value)
    return steps


def test_recover_marks_running_plans_and_steps_as_failed(tmp_path):
    db = Database(str(tmp_path / "eas.db"))

    design_id = uuid.uuid4()
    _make_design(db, design_id)

    running_plan_id = uuid.uuid4()
    steps = _make_plan(
        db, running_plan_id, design_id, PlanStatus.RUNNING,
        [StepStatus.SUCCESS, StepStatus.RUNNING, StepStatus.PENDING],
    )

    recovered = db.recover_orphaned_executions()
    assert recovered == 1

    plan = db.get_plan(str(running_plan_id))
    assert (plan.status.value if hasattr(plan.status, "value") else plan.status) == "Failed"

    db_steps = {s["step_id"]: s["status"] for s in db.get_steps(str(running_plan_id))}
    assert db_steps[str(steps[0].step_id)] == "Success"  # 已完成的不受影响
    assert db_steps[str(steps[1].step_id)] == "Failed"   # 中断时正在跑的被标记失败
    assert db_steps[str(steps[2].step_id)] == "Pending"  # 还没开始的保持原状


def test_recover_does_not_touch_completed_or_pending_plans(tmp_path):
    db = Database(str(tmp_path / "eas.db"))

    design_id = uuid.uuid4()
    _make_design(db, design_id)

    completed_id = uuid.uuid4()
    _make_plan(db, completed_id, design_id, PlanStatus.COMPLETED, [StepStatus.SUCCESS])

    pending_id = uuid.uuid4()
    _make_plan(db, pending_id, design_id, PlanStatus.PENDING, [StepStatus.PENDING])

    recovered = db.recover_orphaned_executions()
    assert recovered == 0

    assert db.get_plan_status(str(completed_id)) == "Completed"
    assert db.get_plan_status(str(pending_id)) == "Pending"


def test_recover_returns_zero_when_nothing_running(tmp_path):
    db = Database(str(tmp_path / "eas.db"))
    assert db.recover_orphaned_executions() == 0


def test_get_plan_status_returns_none_for_missing_plan(tmp_path):
    db = Database(str(tmp_path / "eas.db"))
    assert db.get_plan_status(str(uuid.uuid4())) is None
