"""
示例工具

演示如何创建符合EAS规范的工具
"""

from typing import Any, Dict


class ExampleTool:
    """示例工具"""
    
    def __init__(self) -> None:
        self.tool_id = "example-tool"
        self.version = "1.0.0"
    
    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具"""
        # 实现工具逻辑
        return {
            "success": True,
            "output": "Example output",
        }
    
    async def validate(self, inputs: Dict[str, Any]) -> bool:
        """验证输入"""
        # 验证输入格式
        return True


# 工具Schema（在注册时提供）
TOOL_SCHEMA = {
    "tool_id": "example-tool",
    "version": "1.0.0",
    "name": "Example Tool",
    "description": "An example tool for demonstration",
    "capabilities": {
        "instructions": [
            {
                "name": "process",
                "description": "Process input data",
                "parameters": {
                    "input": "string",
                },
            }
        ],
        "input_format": {
            "type": "object",
            "properties": {
                "input": {"type": "string"},
            },
        },
        "output_format": {
            "type": "object",
            "properties": {
                "output": {"type": "string"},
            },
        },
        "quality_level": "high",
        "performance": {
            "avg_execution_time": "100ms",
            "success_rate": 0.99,
        },
        "limitations": [],
    },
    "file_location": "tools/example_tool.py",
    "index": {
        "main": "example_tool.py",
        "config": None,
        "tests": None,
        "docs": "README.md",
    },
    "license": {
        "license_type": "MIT",
        "max_instances": 10,
        "copy_strategy": "reference",
    },
    "dependencies": [],
}
