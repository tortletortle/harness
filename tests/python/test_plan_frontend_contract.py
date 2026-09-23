"""计划详情响应必须保留前端依赖图和恢复操作需要的字段。"""

from uuid import uuid4

from api.shared import format_plan_for_frontend


def _step(step_id, *, order, depends_on=None, branch_version=1):
    return {
        "step_id": step_id,
        "order": order,
        "description": f"步骤 {order}",
        "tool": "file_creator",
        "inputs": {},
        "check_criteria": "文件存在",
        "status": "Pending",
        "depends_on": depends_on or [],
        "branch_version": branch_version,
    }


def test_plan_detail_preserves_dependencies_and_branch_checkpoint():
    first, second = uuid4(), uuid4()
    plan = {
        "plan_id": uuid4(),
        "design_id": uuid4(),
        "status": "Paused",
        "current_branch": 3,
        "resume_from_step_id": second,
        "segments": [{
            "segment_id": uuid4(),
            "description": "执行阶段",
            "steps": [_step(first, order=1), _step(second, order=2,
                                                  depends_on=[first], branch_version=3)],
        }],
    }

    result = format_plan_for_frontend(plan)

    assert result["current_branch"] == 3
    assert result["resume_from_step_id"] == str(second)
    assert result["steps"][1]["depends_on"] == [str(first)]
    assert result["steps"][1]["branch_version"] == 3
    assert result["segments"][0]["steps"][1] == result["steps"][1]


def test_old_plan_without_workgraph_fields_has_safe_defaults():
    result = format_plan_for_frontend({
        "plan_id": uuid4(),
        "design_id": uuid4(),
        "segments": [{"segment_id": uuid4(), "description": "旧计划",
                      "steps": [_step(uuid4(), order=1)]}],
    })

    assert result["current_branch"] == 1
    assert result["resume_from_step_id"] is None
    assert result["steps"][0]["depends_on"] == []
    assert result["steps"][0]["branch_version"] == 1
