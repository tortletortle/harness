"""
手动工具
用于需要人工干预的步骤
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Dict

# 默认超时（秒）。此工具本身是瞬时内存操作，几乎不可能真正超时，
# 但仍统一提供超时参数，与其他工具保持一致的接口约定。
DEFAULT_TIMEOUT = 10


def _run_with_timeout(func, timeout: float, *args, **kwargs) -> Dict[str, Any]:
    """用线程池包一层，实现对同步阻塞调用的真实超时熔断（跨平台，不依赖 signal.alarm）"""
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            return {
                "success": False,
                "message": f"操作超时（{timeout}秒）",
                "error": "Operation timeout",
            }


class ManualTool:
    """手动工具"""

    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        return {
            "tool_id": "manual_tool",
            "name": "手动工具",
            "description": "需要人工干预的步骤",
            "category": "other",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "manual_step",
                        "description": "手动执行步骤",
                        "parameters": {
                            "description": "步骤描述",
                            "error": "错误信息（如果有）",
                            "timeout": f"超时时间（秒，默认{DEFAULT_TIMEOUT}）"
                        }
                    }
                ]
            }
        }

    @staticmethod
    def _manual_step_impl(description: str = "", error: str = None) -> Dict[str, Any]:
        if error:
            return {"success": False, "message": f"手动步骤失败: {error}", "error": error}
        return {"success": True, "message": f"手动步骤: {description}", "note": "此步骤需要人工确认"}

    @staticmethod
    def manual_step(description: str = "", error: str = None, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        return _run_with_timeout(ManualTool._manual_step_impl, timeout, description, error)

    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return ManualTool.manual_step(
            description=inputs.get("description", ""),
            error=inputs.get("error"),
            timeout=inputs.get("timeout", DEFAULT_TIMEOUT)
        )
