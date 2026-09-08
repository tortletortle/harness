"""
后端语义加固回归测试（暂停断点 / 原子状态认领 / 跳过守卫）。

覆盖的语义契约：
1. 暂停写入恢复检查点：paused_at=当前 Running 步骤，resume_from=第一个
   Pending/Retry 步骤；状态与断点在单条 UPDATE 内原子落库；无未完成步骤时保留旧断点。
2. 恢复认领 Running 成功后清除检查点（清除失败不使恢复失败，此处覆盖正常路径）。
3. claim_plan_status 原子认领：execute 不得把 Paused 翻成 Running（暂停/执行竞态），
   resume 只能从 Paused 认领；Completed/Failed/RolledBack 可重跑；非法状态/缺失计划返回 False。
4. 跳过守卫：计划终态拒绝；步骤生效状态取活动分支克隆行（回滚后活动行是克隆），
   非 Pending/Retry 拒绝。

数据库层用例直接使用 Database（风格同 test_rollback_branching.py）；
端点/后台任务用例在进程内直接调用 async 函数（不起新进程、不触发 lifespan），
仅替换 state.database / state.executor 为临时实例。
"""

import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from database import Database
from fastapi import HTTPException
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

# execute 路径的认领来源（server.execute_plan_background 中的集合，排除 Paused）
EXECUTE_CLAIM_FROM = ("Pending", "Approved", "Failed", "Completed", "RolledBack")


def _new_plan(db: Database, workdir: Path, steps_spec):
    """steps_spec: 每项为 (tool, inputs, check_criteria)。返回 (plan_id, [step_id])"""
    design_id = uuid.uuid4()
    design = DesignDocument(
        design_id=design_id,
        user_request="语义加固测试用设计",
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


def _clones_of(db: Database, plan_id, parent_step_id: str):
    return [
        r for r in db.get_steps(str(plan_id))
        if r["parent_step_id"] == parent_step_id and r["is_obsolete"] == 0
    ]


class _StubExecutor:
    """进程内执行桩：所有步骤一次性成功（unverified 视为完成）。"""

    async def execute_step(self, step):
        return {"success": True, "check_status": "unverified", "output": {}, "error": None, "blocked": False}


@pytest.fixture
def server_state(tmp_path):
    """进程内调用 api.server 的端点/后台函数：替换全局 state 的 database/executor，
    monkeypatch 自动还原，不污染其他测试。"""
    from api import server as server_mod

    db = Database(str(tmp_path / "eas.db"))
    server_mod.state.database = db
    server_mod.state.executor = _StubExecutor()
    return SimpleNamespace(module=server_mod, db=db)


# ===== 特性 1：暂停写入恢复检查点 =====

def test_pause_endpoint_persists_resume_checkpoint(server_state, tmp_path):
    """暂停必须一次性写 Paused + 断点：paused_at=Running 步骤，resume_from=第一个未完成步骤。"""
    db = server_state.db
    plan_id, step_ids = _three_step_plan(db, tmp_path)
    assert db.update_step(step_ids[0], "Success")
    assert db.update_step(step_ids[1], "Running")
    assert db.update_plan_state_machine(str(plan_id), "Running")

    result = asyncio.run(server_state.module.pause_plan(str(plan_id)))
    assert result["success"] is True

    assert db.get_plan_status(str(plan_id)) == "Paused"
    plan = db.get_plan(str(plan_id))
    assert str(plan.paused_at_step_id) == step_ids[1], "断点应停在当前 Running 步骤"
    assert str(plan.resume_from_step_id) == step_ids[2], "恢复点应是第一个未完成步骤"


def test_compute_pause_checkpoint_prefers_retry_as_resume_target(server_state, tmp_path):
    """resume_from 取第一个 Pending/Retry 步骤；paused_at 取第一个 Running 步骤。"""
    db = server_state.db
    plan_id, step_ids = _three_step_plan(db, tmp_path)
    assert db.update_step(step_ids[0], "Running")
    assert db.update_step(step_ids[1], "Retry")
    assert db.update_plan_state_machine(str(plan_id), "Running")

    paused_at, resume_from = server_state.module._compute_pause_checkpoint(str(plan_id), 1)
    assert paused_at == step_ids[0]
    assert resume_from == step_ids[1], "Retry 步骤也应作为恢复目标（早于 Pending 的 step3）"


def test_pause_keeps_old_checkpoint_when_no_incomplete_steps(server_state, tmp_path):
    """分支上没有未完成步骤时暂停：COALESCE 语义下不传字段，保留旧断点而非清空。"""
    db = server_state.db
    plan_id, step_ids = _three_step_plan(db, tmp_path)
    for sid in step_ids:
        assert db.update_step(sid, "Success")

    old_resume, old_paused = step_ids[0], step_ids[2]
    # 预置旧断点并把状态置回 Running（暂停端点要求 Running/Pending）
    assert db.update_plan_state_machine(
        str(plan_id), "Running",
        resume_from_step_id=old_resume, paused_at_step_id=old_paused,
    )

    result = asyncio.run(server_state.module.pause_plan(str(plan_id)))
    assert result["success"] is True

    plan = db.get_plan(str(plan_id))
    assert (plan.status.value if hasattr(plan.status, "value") else plan.status) == "Paused"
    assert str(plan.resume_from_step_id) == old_resume, "无未完成步骤时必须保留旧 resume 断点"
    assert str(plan.paused_at_step_id) == old_paused, "无未完成步骤时必须保留旧 paused 断点"


def test_get_active_steps_snapshot_filters_branch_and_obsolete(tmp_path):
    """快照方法必须在 SQL 内过滤 branch/is_obsolete：新分支只返回克隆行，旧分支只剩未废弃行。"""
    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_id, step_ids = _three_step_plan(db, workdir)
    for sid in step_ids:
        assert db.update_step(sid, "Success")
    _rollback(db, plan_id, step_ids[0])  # branch 2：克隆 step2/step3

    snap2 = db.get_active_steps_snapshot(str(plan_id), 2)
    assert {r["step_id"] for r in snap2} != set(step_ids)
    assert len(snap2) == 2
    assert all(r["is_obsolete"] == 0 and r["branch_version"] == 2 for r in snap2)
    assert all(r["parent_step_id"] in set(step_ids) for r in snap2)

    snap1 = db.get_active_steps_snapshot(str(plan_id), 1)
    assert [r["step_id"] for r in snap1] == [step_ids[0]], "旧分支上 step2/step3 已废弃，只剩 step1"

    # 快照按 step_order 升序，且状态可读
    assert [r["step_order"] for r in snap2] == sorted(r["step_order"] for r in snap2)


# ===== 特性 2：恢复清除检查点 =====

def test_clear_plan_checkpoint(tmp_path):
    """clear_plan_checkpoint 把两个断点字段置 NULL；未命中返回 False；SQL 契约保持。"""
    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_id, step_ids = _three_step_plan(db, workdir)

    assert db.update_plan_state_machine(
        str(plan_id), "Paused",
        resume_from_step_id=step_ids[1], paused_at_step_id=step_ids[0],
    )
    assert db.clear_plan_checkpoint(str(plan_id)) is True

    plan = db.get_plan(str(plan_id))
    assert plan.resume_from_step_id is None
    assert plan.paused_at_step_id is None
    assert db.get_plan_status(str(plan_id)) == "Paused", "清除断点不得改动状态"

    assert db.clear_plan_checkpoint(str(uuid.uuid4())) is False, "不存在的计划是未命中，返回 False"


def test_resume_background_clears_checkpoint_after_claim(server_state, tmp_path):
    """恢复流程：先原子认领 Running，再清除检查点；清除发生在执行开始前，
    恢复完成后库中不得残留过期断点。"""
    from api.server import execute_plan_background

    db = server_state.db
    plan_id, step_ids = _three_step_plan(db, tmp_path)
    assert db.update_step(step_ids[0], "Success")
    assert db.update_plan_state_machine(
        str(plan_id), "Paused",
        resume_from_step_id=step_ids[1], paused_at_step_id=step_ids[0],
    )

    plan = db.get_plan(str(plan_id))
    asyncio.run(execute_plan_background(plan, resume=True))

    assert db.get_plan_status(str(plan_id)) == "Completed", "桩执行器下两个未完成步骤应跑完"
    plan = db.get_plan(str(plan_id))
    assert plan.resume_from_step_id is None, "恢复认领成功后必须清除 resume 断点"
    assert plan.paused_at_step_id is None, "恢复认领成功后必须清除 paused 断点"
    steps = {r["step_id"]: r["status"] for r in db.get_steps(str(plan_id))}
    assert steps[step_ids[1]] == "Success"
    assert steps[step_ids[2]] == "Success"


# ===== 特性 3：原子计划状态认领 =====

@pytest.mark.parametrize("source_status", ["Pending", "Approved", "Failed", "Completed", "RolledBack"])
def test_claim_execute_paths_allow_expected_sources(tmp_path, source_status):
    """execute 认领来源：Pending/Approved/Failed/Completed/RolledBack → Running 成功
    （Completed/RolledBack 必须保留，重跑已完成/已回滚计划的既有行为不能破坏）。"""
    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_id, _ = _three_step_plan(db, workdir)

    assert db.update_plan_state_machine(str(plan_id), source_status)
    assert db.claim_plan_status(str(plan_id), "Running", EXECUTE_CLAIM_FROM) is True
    assert db.get_plan_status(str(plan_id)) == "Running"


@pytest.mark.parametrize("source_status", ["Paused", "Running", "Draft"])
def test_claim_execute_paths_reject_other_sources(tmp_path, source_status):
    """execute 认领来源之外的状态必须认领失败且状态不变——尤其是 Paused（竞态核心）。"""
    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_id, _ = _three_step_plan(db, workdir)

    assert db.update_plan_state_machine(str(plan_id), source_status)
    assert db.claim_plan_status(str(plan_id), "Running", EXECUTE_CLAIM_FROM) is False
    assert db.get_plan_status(str(plan_id)) == source_status, "认领失败不得改动状态"


def test_claim_running_from_paused_blocked_but_resume_claim_succeeds(tmp_path):
    """核心竞态用例：Paused 计划对 execute 认领（Running from 非Paused 集合）必须返回 False；
    resume 认领（仅从 Paused）必须返回 True。"""
    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_id, _ = _three_step_plan(db, workdir)
    assert db.update_plan_state_machine(str(plan_id), "Paused")

    # execute 路径：Paused 不在认领来源中 → False（不得把 Paused 翻成 Running）
    assert db.claim_plan_status(str(plan_id), "Running", EXECUTE_CLAIM_FROM) is False
    assert db.get_plan_status(str(plan_id)) == "Paused"

    # resume 路径：只从 Paused 认领 → True
    assert db.claim_plan_status(str(plan_id), "Running", ("Paused",)) is True
    assert db.get_plan_status(str(plan_id)) == "Running"


def test_claim_plan_status_invalid_and_missing(tmp_path):
    """非法 to_status 与缺失计划返回 False（未命中/非法，非故障）；空来源集合返回 False。"""
    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_id, _ = _three_step_plan(db, workdir)

    assert db.claim_plan_status(str(plan_id), "Bogus", EXECUTE_CLAIM_FROM) is False
    assert db.get_plan_status(str(plan_id)) == "Pending", "非法状态不得改动数据"

    assert db.claim_plan_status(str(uuid.uuid4()), "Running", EXECUTE_CLAIM_FROM) is False
    assert db.claim_plan_status(str(plan_id), "Running", ()) is False
    assert db.get_plan_status(str(plan_id)) == "Pending"


def test_execute_background_does_not_flip_paused_plan(server_state, tmp_path):
    """端到端竞态防护：计划在任务排队期间被暂停时，execute 后台任务必须优雅退出，
    既不翻成 Running 也不误标 Failed。"""
    from api.server import execute_plan_background

    db = server_state.db
    plan_id, _ = _three_step_plan(db, tmp_path)
    assert db.update_plan_state_machine(str(plan_id), "Paused")

    plan = db.get_plan(str(plan_id))
    asyncio.run(execute_plan_background(plan, resume=False))

    assert db.get_plan_status(str(plan_id)) == "Paused", "Paused 计划不得被 execute 改写状态"


def test_execute_background_resume_claim_requires_paused(server_state, tmp_path):
    """resume 认领失败（计划不是 Paused，例如已被别的任务恢复）必须优雅退出，不标 Failed。"""
    from api.server import execute_plan_background

    db = server_state.db
    plan_id, _ = _three_step_plan(db, tmp_path)
    # 计划保持 Pending——resume 认领（仅从 Paused）必然失败

    plan = db.get_plan(str(plan_id))
    asyncio.run(execute_plan_background(plan, resume=True))

    assert db.get_plan_status(str(plan_id)) == "Pending", "认领失败不得改动状态（尤其不得标 Failed）"


def test_failed_step_checkpoint_is_retried_and_following_steps_continue(server_state, tmp_path, monkeypatch):
    """业务失败必须留下 checkpoint；下一次 /execute 从失败步骤重跑并继续后续步骤。"""
    from api import server as server_mod

    db = server_state.db
    plan_id, step_ids = _three_step_plan(db, tmp_path)
    calls = []

    async def fail_once(plan, step):
        calls.append(str(step["step_id"]))
        return {
            "success": False,
            "retryable": False,
            "blocked": False,
            "check_status": "unverified",
            "error": "temporary test failure",
        }

    monkeypatch.setattr(server_mod, "_execute_step_with_state_machine", fail_once)
    monkeypatch.setattr(server_mod.state, "retry_max_attempts", 1)
    asyncio.run(server_mod.execute_plan_background(db.get_plan(str(plan_id))))

    assert db.get_plan_status(str(plan_id)) == "Failed"
    failed_plan = db.get_plan(str(plan_id))
    assert str(failed_plan.resume_from_step_id) == step_ids[0]
    assert calls == [step_ids[0]]
    assert db.get_steps(str(plan_id))[0]["status"] == "Failed"

    async def succeed(plan, step):
        calls.append(str(step["step_id"]))
        db.update_step_state_machine(
            step_id=str(step["step_id"]),
            status="Success",
            outputs={},
        )
        return {
            "success": True,
            "retryable": False,
            "blocked": False,
            "check_status": "unverified",
            "output": {},
        }

    monkeypatch.setattr(server_mod, "_execute_step_with_state_machine", succeed)
    asyncio.run(server_mod.execute_plan_background(db.get_plan(str(plan_id))))

    assert db.get_plan_status(str(plan_id)) == "Completed"
    assert calls == [step_ids[0], *step_ids]
    assert all(row["status"] == "Success" for row in db.get_steps(str(plan_id)))
    assert db.get_plan(str(plan_id)).resume_from_step_id is None



def test_get_effective_step_status_prefers_active_clone(tmp_path):
    """生效状态：回滚后活动行是克隆行（parent_step_id 指向原 id），必须优先取克隆行；
    无克隆回落到原行；两者皆无返回 None。"""
    db = Database(str(tmp_path / "eas.db"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    plan_id, step_ids = _three_step_plan(db, workdir)
    for sid in step_ids:
        assert db.update_step(sid, "Success")
    _rollback(db, plan_id, step_ids[0])  # branch 2：克隆 step2/step3（Pending）

    assert db.get_effective_step_status(str(plan_id), step_ids[1], 2) == "Pending", \
        "原行 Success、克隆 Pending：生效状态必须是克隆的 Pending"
    assert db.get_effective_step_status(str(plan_id), step_ids[1], 1) == "Success", \
        "分支 1 上无克隆：回落到原行状态"
    assert db.get_effective_step_status(str(plan_id), step_ids[0], 2) == "Success", \
        "回滚目标自身无克隆：回落到原行状态"
    assert db.get_effective_step_status(str(plan_id), str(uuid.uuid4()), 2) is None


def test_skip_endpoint_allows_original_when_clone_pending(server_state, tmp_path):
    """回滚后跳过原 id：生效状态来自 Pending 的克隆行 → 允许，且 Skipped 传播到克隆。"""
    db = server_state.db
    plan_id, step_ids = _three_step_plan(db, tmp_path)
    for sid in step_ids:
        assert db.update_step(sid, "Success")
    _rollback(db, plan_id, step_ids[0])

    result = asyncio.run(server_state.module.skip_step(str(plan_id), step_ids[1]))
    assert result["success"] is True

    rows = {r["step_id"]: r for r in db.get_steps(str(plan_id))}
    assert rows[step_ids[1]]["status"] == "Skipped", "原行本身也应标记 Skipped"
    clones = _clones_of(db, plan_id, step_ids[1])
    assert clones, "活动分支克隆行必须存在"
    assert all(r["status"] == "Skipped" for r in clones), "Skipped 必须传播到活动分支克隆行"
    # 未跳过的步骤不受影响
    other = _clones_of(db, plan_id, step_ids[2])
    assert all(r["status"] == "Pending" for r in other)


def test_skip_endpoint_rejects_running_clone(server_state, tmp_path):
    """克隆行已在执行（Running）时，跳过原 id 必须被 409 拒绝（生效状态非 Pending/Retry）。"""
    db = server_state.db
    plan_id, step_ids = _three_step_plan(db, tmp_path)
    for sid in step_ids:
        assert db.update_step(sid, "Success")
    _rollback(db, plan_id, step_ids[0])

    clone = _clones_of(db, plan_id, step_ids[1])[0]
    assert db.update_step(clone["step_id"], "Running")

    with pytest.raises(HTTPException) as ei:
        asyncio.run(server_state.module.skip_step(str(plan_id), step_ids[1]))
    assert ei.value.status_code == 409
    assert "步骤已在执行或已完成，无法跳过" == ei.value.detail

    rows = {r["step_id"]: r["status"] for r in db.get_steps(str(plan_id))}
    assert rows[step_ids[1]] == "Success", "被拒绝的跳过不得改动任何状态"
    assert rows[clone["step_id"]] == "Running"


def test_skip_endpoint_rejects_finished_step_without_clone(server_state, tmp_path):
    """无克隆时按原行状态守卫：已 Success 的步骤不能再次跳过。"""
    db = server_state.db
    plan_id, step_ids = _three_step_plan(db, tmp_path)
    assert db.update_step(step_ids[0], "Success")

    with pytest.raises(HTTPException) as ei:
        asyncio.run(server_state.module.skip_step(str(plan_id), step_ids[0]))
    assert ei.value.status_code == 409
    assert ei.value.detail == "步骤已在执行或已完成，无法跳过"


@pytest.mark.parametrize("terminal_status", ["Completed", "Failed", "RolledBack"])
def test_skip_endpoint_rejects_terminal_plan(server_state, tmp_path, terminal_status):
    """计划已进入终态时跳过必须被 409 拒绝，且给出含当前状态的中文提示。"""
    db = server_state.db
    plan_id, step_ids = _three_step_plan(db, tmp_path)
    assert db.update_plan_state_machine(str(plan_id), terminal_status)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(server_state.module.skip_step(str(plan_id), step_ids[0]))
    assert ei.value.status_code == 409
    assert "计划已结束" in ei.value.detail
    assert terminal_status in ei.value.detail


def test_skip_endpoint_404s(server_state, tmp_path):
    """计划不存在 / 步骤不存在保持 404。"""
    db = server_state.db
    plan_id, step_ids = _three_step_plan(db, tmp_path)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(server_state.module.skip_step(str(uuid.uuid4()), step_ids[0]))
    assert ei.value.status_code == 404

    with pytest.raises(HTTPException) as ei:
        asyncio.run(server_state.module.skip_step(str(plan_id), str(uuid.uuid4())))
    assert ei.value.status_code == 404
