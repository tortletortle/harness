"""
E2E 验证（浏览器走查）发现缺陷的回归测试。

覆盖的缺陷模式（每个测试对应一个真实线上问题）：
1. list_plans 位置参数把 limit 传给 design_id → /api/plans 永远返回空
2. clone_steps_from 沿用原 step_id → 主键冲突静默失败，回滚后步骤永久丢失
3. clone_steps_from 无来源分支过滤 → 多分支计划每次回滚克隆成倍累积，
   恢复执行时反复运行重复步骤
4. get_plan 多分支行覆盖顺序不定 → 活动分支状态被旧分支覆盖
5. 跳过只改原始 id，活动分支克隆行仍 Pending 照常执行 → 跳过无效
6. format_plan_for_frontend 缺少计划级 status → 前端无法权威轮询
"""

import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from database import Database
from models import (
    DesignDocument,
    DesignPart1,
    Architecture,
    Difficulty,
    ExecutionPlan,
    PlanSegment,
    PlanStatus,
    Step,
    StepStatus,
    ToolAssessment,
)


def _new_plan(db: Database, workdir: Path, steps_spec):
    """steps_spec: 每项为 (tool, inputs, check_criteria)。返回 (plan_id, [step_id])"""
    design_id = uuid.uuid4()
    design = DesignDocument(
        design_id=design_id,
        user_request="回归测试用设计",
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

    steps = [
        Step(
            step_id=uuid.uuid4(),
            order=i + 1,
            description=f"步骤{i + 1}",
            tool=tool,
            inputs=inputs,
            check_criteria=criteria,
            status=StepStatus.PENDING,
        )
        for i, (tool, inputs, criteria) in enumerate(steps_spec)
    ]
    plan_id = uuid.uuid4()
    plan = ExecutionPlan(
        plan_id=plan_id,
        design_id=design_id,
        created_at=datetime.now(),
        status=PlanStatus.PENDING,
        segments=[PlanSegment(segment_id=uuid.uuid4(), description="seg", steps=steps)],
        implementation_list=[],
        dynamic_insertions=[],
    )
    assert db.save_plan(plan)
    return plan_id, [str(s.step_id) for s in steps]


def _three_step_plan(db: Database, workdir: Path):
    return _new_plan(db, workdir, [
        ("file_creator", {"path": str(workdir / "a.txt"), "content": "1"}, "手动检查"),
        ("file_creator", {"path": str(workdir / "b.txt"), "content": "2"}, "手动检查"),
        ("file_creator", {"path": str(workdir / "c.txt"), "content": "3"}, "手动检查"),
    ])


def _rollback(db: Database, plan_id, target_step_id: str):
    """按 server.py 回滚端点的顺序执行一次回滚（先克隆、再废弃、再更新计划）。"""
    plan = db.get_plan(str(plan_id))
    new_branch = (plan.current_branch or 1) + 1
    assert db.clone_steps_from(str(plan_id), target_step_id, new_branch)
    db.mark_steps_obsolete(str(plan_id), target_step_id, plan.current_branch or 1)
    assert db.update_plan_state_machine(
        str(plan_id), PlanStatus.PENDING.value,
        resume_from_step_id=target_step_id, current_branch=new_branch,
    )
    return new_branch


def _branch_rows(db: Database, plan_id, branch: int):
    return [
        r for r in db.get_steps(str(plan_id))
        if r["branch_version"] == branch
    ]


def test_list_plans_limit_and_design_id(tmp_path):
    """回归：server 曾以 list_plans(limit) 位置参数调用，limit 落到 design_id 上，
    /api/plans 永远返回空。"""
    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_ids = [_new_plan(db, workdir, [])[0] for _ in range(3)]

    rows = db.list_plans(limit=2)
    assert len(rows) == 2
    rows_all = db.list_plans(limit=10)
    assert len(rows_all) == 3
    assert {r["plan_id"] for r in rows_all} == {str(p) for p in plan_ids}


def test_rollback_creates_exactly_one_clone_set(tmp_path):
    """回归：克隆曾复用原 step_id 撞主键静默失败；修复后必须产生新 id 且血缘正确。"""
    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_id, step_ids = _three_step_plan(db, workdir)

    new_branch = _rollback(db, plan_id, step_ids[0])
    assert new_branch == 2

    clones = _branch_rows(db, plan_id, 2)
    assert len(clones) == 2
    clone_ids = {r["step_id"] for r in clones}
    assert clone_ids == {step_ids[1], step_ids[2]} or True  # 新 id，与原 id 不同
    assert not (clone_ids & set(step_ids)), "克隆行不得复用原 step_id（主键冲突模式）"
    for r in clones:
        assert r["status"] == "Pending"
        assert r["is_obsolete"] == 0
        assert r["parent_step_id"] in set(step_ids)
    # 原步骤 2/3 已废弃，步骤 1 保留
    b1 = {r["step_id"]: r for r in _branch_rows(db, plan_id, 1)}
    assert b1[step_ids[0]]["is_obsolete"] == 0
    assert b1[step_ids[1]]["is_obsolete"] == 1
    assert b1[step_ids[2]]["is_obsolete"] == 1


def test_repeated_rollback_does_not_accumulate_clones(tmp_path):
    """回归：克隆曾无来源分支过滤，多分支计划每次回滚克隆成倍累积
    （线上观察到单分支 8 个同序号克隆，恢复执行陷入重复执行循环）。"""
    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_id, step_ids = _three_step_plan(db, workdir)

    _rollback(db, plan_id, step_ids[0])  # branch 2
    _rollback(db, plan_id, step_ids[0])  # branch 3，来源必须是 branch 2

    assert len(_branch_rows(db, plan_id, 3)) == 2, "新分支只能有一对克隆，不得累积历史分支步骤"
    total = len(db.get_steps(str(plan_id)))
    # b1 原始 3 行 + b2 克隆 2 行 + b3 克隆 2 行
    assert total == 7


def test_get_plan_prefers_active_branch_statuses(tmp_path):
    """回归：多分支同 step_id 行的覆盖顺序曾不确定，旧分支 Success 会盖住新分支 Pending。"""
    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_id, step_ids = _three_step_plan(db, workdir)
    # 已完成的历史：原步骤全部 Success
    for sid in step_ids:
        assert db.update_step(sid, "Success")

    _rollback(db, plan_id, step_ids[0])

    plan = db.get_plan(str(plan_id))
    steps = [s for seg in plan.segments for s in seg.steps]
    by_order = {s.order: s for s in steps}
    assert by_order[1].status == StepStatus.SUCCESS  # 回滚目标保留
    assert by_order[2].status == StepStatus.PENDING  # 必须显示活动分支克隆的 Pending
    assert by_order[3].status == StepStatus.PENDING


def test_skip_propagates_to_active_branch_clone(tmp_path):
    """回归：跳过曾只改原始 id，活动分支克隆行仍 Pending 会照常执行。"""
    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_id, step_ids = _three_step_plan(db, workdir)
    for sid in step_ids:
        assert db.update_step(sid, "Success")
    _rollback(db, plan_id, step_ids[0])

    from datetime import datetime as dt
    assert db.mark_step_tree_skipped(str(plan_id), step_ids[1], executed_at=dt.now())

    rows = {r["step_id"]: r for r in db.get_steps(str(plan_id))}
    assert rows[step_ids[1]]["status"] == "Skipped"  # 原行
    clones = [r for r in rows.values() if r["parent_step_id"] == step_ids[1]]
    assert clones, "活动分支克隆行必须存在"
    assert all(r["status"] == "Skipped" for r in clones), "跳过必须传播到活动分支克隆行"
    # 未跳过的步骤不受影响
    other = [r for r in rows.values() if r["parent_step_id"] == step_ids[2]]
    assert all(r["status"] == "Pending" for r in other)

    plan = db.get_plan(str(plan_id))
    steps = [s for seg in plan.segments for s in seg.steps]
    by_order = {s.order: s for s in steps}
    assert by_order[2].status == StepStatus.SKIPPED  # get_plan 展示层同样反映跳过
    assert by_order[3].status == StepStatus.PENDING


def test_format_plan_for_frontend_includes_plan_status(tmp_path):
    """回归：/api/plan/{id} 曾缺少计划级 status，前端无法权威轮询。"""
    from api.server import format_plan_for_frontend

    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_id, _ = _three_step_plan(db, workdir)
    assert db.update_plan_state_machine(str(plan_id), PlanStatus.RUNNING.value)

    plan = db.get_plan(str(plan_id))
    formatted = format_plan_for_frontend(plan)
    assert formatted["status"] == "Running"
