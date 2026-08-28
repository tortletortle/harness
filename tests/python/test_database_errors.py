"""
数据库异常传播契约测试。

旧契约：所有数据库方法吞掉异常，写入失败返回 False、读取失败返回 None/[]——
调用方无法区分「未命中」与「数据库故障」，故障被静默掩盖。

新契约：
- 数据库异常（连接损坏、SQL 失败等）→ 抛出 DatabaseError（logger.exception 留栈后 raise ... from e）
- False/None/[] 仅表示未命中（step_id/plan_id 不存在、状态值非法），保持安静
"""

import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from database import Database, DatabaseError
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


def _new_plan(db: Database, steps_spec):
    """steps_spec: 每项为 (tool, inputs, check_criteria)。返回 (plan_id, [step_id])"""
    design_id = uuid.uuid4()
    design = DesignDocument(
        design_id=design_id,
        user_request="异常契约测试用设计",
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


def _one_step_plan(db: Database):
    return _new_plan(db, [("file_creator", {"path": "a.txt", "content": "1"}, "手动检查")])


def _break_connection(db: Database, monkeypatch):
    """把 _get_connection 替换成直接抛 RuntimeError 的桩，模拟连接损坏/数据库故障。"""
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "_get_connection", boom)


# ===== 未命中保持安静（False/None 不是故障） =====

def test_update_step_nonexistent_id_returns_false(tmp_path):
    """step_id 不存在是未命中（rowcount==0），必须保持安静返回 False，不得抛异常。"""
    db = Database(str(tmp_path / "eas.db"))

    assert db.update_step(str(uuid.uuid4()), "Success") is False


def test_update_step_invalid_status_returns_false(tmp_path):
    """非法状态值由入口 guard 拒绝，返回 False 并告警——不是异常路径。"""
    db = Database(str(tmp_path / "eas.db"))

    assert db.update_step(str(uuid.uuid4()), "NotARealStatus") is False
    assert db.update_plan_state_machine(str(uuid.uuid4()), "Bogus") is False


# ===== 数据库故障必须显式抛出 DatabaseError =====

def test_update_step_broken_connection_raises_database_error(tmp_path, monkeypatch):
    """回归：update_step 曾在连接损坏时吞异常返回 False，与「未命中」不可区分。"""
    db = Database(str(tmp_path / "eas.db"))
    plan_id, step_ids = _one_step_plan(db)
    _break_connection(db, monkeypatch)

    with pytest.raises(DatabaseError, match="boom"):
        db.update_step(step_ids[0], "Success")


def test_list_plans_broken_connection_raises_database_error(tmp_path, monkeypatch):
    """回归：list_plans 曾在故障时返回 []，调用方误以为只是没有计划。"""
    db = Database(str(tmp_path / "eas.db"))
    _break_connection(db, monkeypatch)

    with pytest.raises(DatabaseError, match="boom"):
        db.list_plans()


def test_get_plan_broken_connection_raises_database_error(tmp_path, monkeypatch):
    """回归：get_plan 曾在故障时返回 None，与「计划不存在」不可区分（404 vs 500）。"""
    db = Database(str(tmp_path / "eas.db"))
    plan_id, _ = _one_step_plan(db)
    _break_connection(db, monkeypatch)

    with pytest.raises(DatabaseError, match="boom"):
        db.get_plan(str(plan_id))


def test_save_plan_broken_connection_raises_database_error(tmp_path, monkeypatch):
    """回归：save_plan 曾在故障时吞异常返回 False。"""
    db = Database(str(tmp_path / "eas.db"))
    plan_id, _ = _one_step_plan(db)
    # 重新构造一个待保存的 plan：直接复用读取到的 plan 即可
    plan = db.get_plan(str(plan_id))
    _break_connection(db, monkeypatch)

    with pytest.raises(DatabaseError, match="boom"):
        db.save_plan(plan)


def test_get_plan_status_broken_connection_raises_database_error(tmp_path, monkeypatch):
    """执行循环依赖 get_plan_status 检测暂停；故障时返回 None 曾会被误判为「无此计划」。"""
    db = Database(str(tmp_path / "eas.db"))
    plan_id, _ = _one_step_plan(db)
    _break_connection(db, monkeypatch)

    with pytest.raises(DatabaseError, match="boom"):
        db.get_plan_status(str(plan_id))


def test_get_next_runnable_step_broken_connection_raises_database_error(tmp_path, monkeypatch):
    """执行循环曾把故障静默当成「没有可运行步骤」而把计划标记为 Completed。"""
    db = Database(str(tmp_path / "eas.db"))
    plan_id, _ = _one_step_plan(db)
    _break_connection(db, monkeypatch)

    with pytest.raises(DatabaseError, match="boom"):
        db.get_next_runnable_step(str(plan_id))


def test_add_step_event_broken_connection_raises_database_error(tmp_path, monkeypatch):
    """状态机事件流写入失败必须显式抛出，事件回放/审计不能悄悄断流。"""
    db = Database(str(tmp_path / "eas.db"))
    plan_id, step_ids = _one_step_plan(db)
    _break_connection(db, monkeypatch)

    with pytest.raises(DatabaseError, match="boom"):
        db.add_step_event(step_ids[0], str(plan_id), "running")


def test_mark_steps_obsolete_broken_connection_raises_database_error(tmp_path, monkeypatch):
    """回滚链路（废弃旧分支步骤）故障必须显式抛出，返回 0 会与「无后续步骤」混淆。"""
    db = Database(str(tmp_path / "eas.db"))
    plan_id, step_ids = _one_step_plan(db)
    _break_connection(db, monkeypatch)

    with pytest.raises(DatabaseError, match="boom"):
        db.mark_steps_obsolete(str(plan_id), step_ids[0], 1)


def test_database_error_preserves_cause(tmp_path, monkeypatch):
    """raise ... from e：原始异常必须保留在 __cause__ 上，便于排障。"""
    db = Database(str(tmp_path / "eas.db"))
    _break_connection(db, monkeypatch)

    with pytest.raises(DatabaseError) as exc_info:
        db.list_plans()
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "boom"
