"""AcceptanceRunner 与 DefaultExecutor 的集成测试。"""

import os

import pytest

from executor import DefaultExecutor
from llm_client import LLMClient
from models import Step, StepStatus


class StubRegistry:
    def __init__(self, result):
        self.result = result

    def execute_tool(self, tool_id, instruction, inputs):
        return dict(self.result)


def make_executor(tmp_path, result):
    os.environ["EAS_WORKSPACE_ROOT"] = str(tmp_path)
    return DefaultExecutor(
        llm_client=LLMClient(),
        model_config=None,
        tool_registry=StubRegistry(result),
        database=None,
    )


def make_step(acceptance):
    return Step(
        order=1,
        description="create file",
        tool="file_creator:create_file",
        inputs={"path": "result.txt"},
        check_criteria="执行成功",
        acceptance=acceptance,
        status=StepStatus.PENDING,
    )


@pytest.mark.asyncio
async def test_successful_tool_requires_actual_acceptance(tmp_path):
    (tmp_path / "result.txt").write_text("expected", encoding="utf-8")
    executor = make_executor(tmp_path, {"success": True, "message": "created", "path": "result.txt"})

    result = await executor.execute_step(make_step([
        {"type": "file_exists", "path": "result.txt", "kind": "file"},
        {"type": "content_contains", "path": "result.txt", "text": "expected"},
    ]))

    assert result["success"] is True
    assert result["acceptance_status"] == "passed"
    assert result["reward"] == 1.0
    assert result["acceptance_failed"] is False


@pytest.mark.asyncio
async def test_tool_success_but_wrong_file_content_fails_without_llm(tmp_path):
    (tmp_path / "result.txt").write_text("wrong", encoding="utf-8")
    executor = make_executor(tmp_path, {"success": True, "message": "created", "path": "result.txt"})

    result = await executor.execute_step(make_step([
        {"type": "file_exists", "path": "result.txt"},
        {"type": "content_contains", "path": "result.txt", "text": "expected"},
    ]))

    assert result["success"] is False
    assert result["check_status"] == "failed"
    assert result["acceptance_failed"] is True
    assert result["retryable"] is False
    assert result["acceptance_result"]["checks"][1]["passed"] is False
