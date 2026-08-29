"""
阶段3：可靠性——步骤级超时熔断 + retry_count 字段的最小闭环。

覆盖：
1. executor 层的步骤级超时熔断：工具执行超过 step_timeout 时被 asyncio.wait_for 打断，
   返回统一的失败结果（error == "Step execution timeout"），不会无限期挂住。
2. Step 模型新增的 retry_count / max_retries 字段有默认值，不影响现有构造方式。
3. database 层 retry_count 列的持久化：save_plan 写入、get_plan 读回、
   update_step 支持单独更新 retry_count。
"""

import time
import uuid
from datetime import datetime

import pytest


class SlowRegistry:
    """模拟一个会卡住很久才返回的工具（比如系统调用挂死、网络无限等待）。"""

    def __init__(self, delay_seconds):
        self.delay_seconds = delay_seconds

    def execute_tool(self, tool_id, instruction, inputs):
        time.sleep(self.delay_seconds)
        return {"success": True, "message": "finally done"}


class FastRegistry:
    def __init__(self, result):
        self.result = result

    def execute_tool(self, tool_id, instruction, inputs):
        return self.result


class FakeLLM:
    async def chat(self, **kwargs):
        class R:
            content = "YES"
        return R()


def make_step():
    from models import Step, StepStatus
    return Step(
        step_id=uuid.uuid4(),
        order=1,
        description="超时测试步骤",
        tool="file_creator",
        inputs={"path": "a.txt", "content": "x"},
        check_criteria="文件应存在",
        status=StepStatus.PENDING,
    )


@pytest.mark.asyncio
async def test_step_execution_times_out_and_returns_failure():
    from executor import DefaultExecutor

    # step_timeout 设置为很短，SlowRegistry 故意 sleep 更长时间，验证熔断生效。
    config = {"primary": {"provider": "p", "model": "m"}, "step_timeout": 0.3}
    ex = DefaultExecutor(FakeLLM(), config, SlowRegistry(delay_seconds=5))

    start = time.monotonic()
    result = await ex.execute_step(make_step())
    elapsed = time.monotonic() - start

    assert result["success"] is False
    assert result["error"] == "Step execution timeout"
    # 熔断应该在远小于 SlowRegistry 的 5 秒 sleep 时间内生效
    assert elapsed < 3, f"熔断耗时过长: {elapsed}s"


@pytest.mark.asyncio
async def test_step_within_timeout_succeeds():
    from executor import DefaultExecutor

    config = {"primary": {"provider": "p", "model": "m"}, "step_timeout": 5}
    tool_ok = {"success": True, "message": "done"}
    ex = DefaultExecutor(FakeLLM(), config, FastRegistry(tool_ok))

    result = await ex.execute_step(make_step())
    assert result["success"] is True


def test_default_step_timeout_used_when_not_configured():
    from executor import DefaultExecutor

    ex = DefaultExecutor(FakeLLM(), {"primary": {"provider": "p", "model": "m"}}, FastRegistry({}))
    assert ex.step_timeout == DefaultExecutor.DEFAULT_STEP_TIMEOUT


def test_step_model_has_retry_defaults():
    step = make_step()
    assert step.retry_count == 0
    assert step.max_retries == 2


def test_database_persists_retry_count(tmp_path):
    from database import Database
    from models import (
        ExecutionPlan, PlanSegment, Step, StepStatus, PlanStatus,
        DesignDocument, DesignPart1, Architecture, Difficulty, ToolAssessment,
    )

    db = Database(str(tmp_path / "eas.db"))

    design_id = uuid.uuid4()
    design = DesignDocument(
        design_id=design_id,
        user_request="重试持久化验证",
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

    plan_id = uuid.uuid4()
    step_id = uuid.uuid4()
    step = Step(
        step_id=step_id, order=1, description="重试测试步骤", tool="file_creator",
        inputs={"path": "x.txt"}, check_criteria="ok", status=StepStatus.PENDING,
        retry_count=1, max_retries=3,
    )
    plan = ExecutionPlan(
        plan_id=plan_id, design_id=design_id, created_at=datetime.now(),
        status=PlanStatus.PENDING,
        segments=[PlanSegment(segment_id=uuid.uuid4(), description="seg", steps=[step])],
        implementation_list=[], dynamic_insertions=[],
    )
    assert db.save_plan(plan)

    fresh = db.get_plan(str(plan_id))
    assert fresh.segments[0].steps[0].retry_count == 1

    # update_step 可以单独更新 retry_count
    assert db.update_step(str(step_id), "Running", retry_count=2)
    fresh2 = db.get_plan(str(plan_id))
    assert fresh2.segments[0].steps[0].retry_count == 2

    # 不传 retry_count 时保持原值不变
    assert db.update_step(str(step_id), "Success", outputs={"ok": True})
    fresh3 = db.get_plan(str(plan_id))
    assert fresh3.segments[0].steps[0].retry_count == 2


def test_migration_adds_retry_count_to_existing_db(tmp_path):
    """模拟旧库（没有 retry_count 列）：重新初始化 Database 应该自动补齐该列，不报错。"""
    import sqlite3
    from database import Database

    db_path = tmp_path / "legacy.db"

    # 手工建一个不含 retry_count 列的旧版 steps 表
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE steps (
            step_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            step_order INTEGER NOT NULL,
            description TEXT NOT NULL,
            tool TEXT NOT NULL,
            inputs TEXT NOT NULL,
            check_criteria TEXT NOT NULL,
            outputs TEXT,
            status TEXT NOT NULL,
            executed_at TEXT
        )
    """)
    conn.commit()
    conn.close()

    # 初始化 Database 应该自动迁移，加上 retry_count 列
    db = Database(str(db_path))
    conn = sqlite3.connect(str(db_path))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(steps)").fetchall()}
    conn.close()
    assert "retry_count" in columns

    # 再次初始化（模拟服务重启）不应该因为列已存在而报错
    db2 = Database(str(db_path))
    assert db2 is not None
