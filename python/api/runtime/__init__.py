"""API 层执行运行时（自 server.py 拆分；行为不变，符号仍可从 api.server 导入）。


本包按职责分三块：
- step_execution.py: 单步执行体 + 状态机 + 步骤状态持久化辅助。
- artifacts.py:     步骤产物登记 + 最终交付报告。
- background.py:   后台总循环（执行认领 / 暂停 / 恢复 / 回滚 / 收尾）。

公开符号（兼容既有 import 路径，详见 server.py 末尾的 re-export）：
- execute_plan_background
- _execute_step_with_state_machine / _execute_step_to_completion
- _persist_step_status / _is_retryable_exit_code_acceptance / _compress_step_context
- _register_step_artifacts / _generate_final_report
"""
from api.runtime.background import execute_plan_background
from api.runtime.step_execution import (
    _compress_step_context,
    _execute_step_to_completion,
    _execute_step_with_state_machine,
    _is_retryable_exit_code_acceptance,
    _persist_step_status,
)
from api.runtime.artifacts import (
    _generate_final_report,
    _register_step_artifacts,
)

__all__ = [
    "execute_plan_background",
    "_execute_step_with_state_machine",
    "_execute_step_to_completion",
    "_persist_step_status",
    "_is_retryable_exit_code_acceptance",
    "_compress_step_context",
    "_register_step_artifacts",
    "_generate_final_report",
]
