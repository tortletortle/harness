"""
验证 Database.save_plan() 会把嵌套在 segments 里的 steps 展开落地到
独立的 steps 表（而不仅仅存在 plans.segments 这个 JSON 字段里）。

替换已删除的 python/test_steps_db.py：原脚本逻辑有效，仅改写为
pytest 风格并使用 tmp_path 避免写入生产数据库。
"""

from uuid import uuid4
from datetime import datetime, timezone
import pytest

from database import Database
from models import (
    ExecutionPlan,
    PlanSegment,
    Step,
    DesignDocument,
    DesignPart1,
    Architecture,
    Difficulty,
    ToolAssessment,
    ToolRequirement,
)


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test_steps.db"))


def _make_design():
    return DesignDocument(
        design_id=uuid4(),
        user_request="Test task",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
        part1_design=DesignPart1(
            architecture=Architecture(components=["Component1"], interactions=["Interaction1"]),
            difficulty=Difficulty(overall="Simple", breakdown={"technical": "Low"}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[ToolRequirement(tool_id="file_creator", availability="Available")],
            overall_tool_completeness=1.0,
            risks=[],
        ),
    )


def test_save_plan_flattens_segment_steps_into_steps_table(db):
    design = _make_design()
    assert db.save_design(design)

    plan = ExecutionPlan(
        plan_id=uuid4(),
        design_id=design.design_id,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status="Pending",
        segments=[
            PlanSegment(
                segment_id=uuid4(),
                description="Test Segment",
                steps=[
                    Step(
                        step_id=uuid4(),
                        order=1,
                        description="Create file test.txt",
                        tool="file_creator",
                        inputs={"instruction": "create_file", "path": "test.txt"},
                        check_criteria="File created",
                        status="Pending",
                    ),
                    Step(
                        step_id=uuid4(),
                        order=2,
                        description="Create directory src",
                        tool="file_creator",
                        inputs={"instruction": "create_directory", "path": "src"},
                        check_criteria="Directory created",
                        status="Pending",
                    ),
                ],
            )
        ],
        implementation_list=["test.txt"],
    )

    assert db.save_plan(plan)

    steps = db.get_steps(str(plan.plan_id))
    assert len(steps) == 2
    descriptions = {s["description"] for s in steps}
    assert descriptions == {"Create file test.txt", "Create directory src"}
