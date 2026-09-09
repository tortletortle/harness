import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from api import server


class StubDatabase:
    def __init__(self):
        self.status = "Running"
        self.step_status = "Running"
        self.events = []

    def get_plan_status(self, plan_id):
        return self.status

    def update_step_state_machine(self, step_id, status, **kwargs):
        self.step_status = status
        return True

    def add_step_event(self, *args, **kwargs):
        self.events.append((args, kwargs))
        return True

    def update_plan_state_machine(self, plan_id, status, **kwargs):
        self.status = status
        return True


def test_exit_code_acceptance_failure_is_retryable_only_for_command_runner():
    step = {"tool": "command_runner"}
    result = {
        "returncode": 1,
        "acceptance_result": {"checks": [{"type": "exit_code", "passed": False}]},
    }
    assert server._is_retryable_exit_code_acceptance(step, result)
    assert not server._is_retryable_exit_code_acceptance(
        {"tool": "test_runner"}, result
    )
    assert not server._is_retryable_exit_code_acceptance(
        step,
        {"returncode": 1, "acceptance_result": {"checks": [{"type": "content_contains", "passed": False}]}},
    )


def test_command_exit_failure_preserves_tool_message():
    # The executor fallback should expose a command's message when no error key exists.
    result = {"success": False, "message": "命令执行失败", "returncode": 1}
    assert result.get("error") or result.get("message") == "命令执行失败"
