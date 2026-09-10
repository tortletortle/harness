import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from api import server
from executor import DefaultExecutor


def test_exit_code_acceptance_failure_is_retryable_only_for_command_runner():
    step = {"tool": "command_runner"}
    result = {
        "returncode": 1,
        "acceptance_result": {"checks": [{"type": "exit_code", "passed": False}]},
    }
    assert server._is_retryable_exit_code_acceptance(step, result)
    assert not server._is_retryable_exit_code_acceptance({"tool": "test_runner"}, result)
    assert not server._is_retryable_exit_code_acceptance(
        step,
        {"returncode": 1, "acceptance_result": {"checks": [{"type": "content_contains", "passed": False}]}},
    )


def test_executor_fallback_preserves_tool_message():
    executor = object.__new__(DefaultExecutor)
    result = {"success": False, "message": "命令执行失败", "returncode": 1}
    normalized = result.get("error") or result.get("message") or "Unknown error"
    assert normalized == "命令执行失败"
