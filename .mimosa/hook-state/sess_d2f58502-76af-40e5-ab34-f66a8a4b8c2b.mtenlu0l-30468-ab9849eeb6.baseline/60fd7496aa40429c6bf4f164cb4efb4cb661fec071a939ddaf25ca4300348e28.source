"""
示例插件：JSON处理工具
演示如何创建自定义工具插件
"""

import json
from typing import Any, Dict
import sys
from pathlib import Path

# 添加python目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from plugin_system import ToolPlugin


class JsonToolPlugin(ToolPlugin):
    """JSON处理工具插件"""
    
    @property
    def name(self) -> str:
        return "json_tool"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    @property
    def description(self) -> str:
        return "JSON数据处理工具"
    
    def get_metadata(self) -> Dict[str, Any]:
        """获取工具元数据"""
        return {
            "tool_id": "json_tool",
            "name": "JSON处理工具",
            "description": "处理JSON数据",
            "version": self.version,
            "capabilities": {
                "instructions": [
                    {
                        "name": "parse_json",
                        "description": "解析JSON字符串",
                        "parameters": {
                            "json_str": "JSON字符串"
                        }
                    },
                    {
                        "name": "format_json",
                        "description": "格式化JSON",
                        "parameters": {
                            "data": "数据对象",
                            "indent": "缩进空格数（默认2）"
                        }
                    },
                    {
                        "name": "validate_json",
                        "description": "验证JSON格式",
                        "parameters": {
                            "json_str": "JSON字符串"
                        }
                    }
                ]
            }
        }
    
    def execute(self, instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        if instruction == "parse_json":
            return self._parse_json(inputs.get("json_str", ""))
        elif instruction == "format_json":
            return self._format_json(
                inputs.get("data"),
                inputs.get("indent", 2)
            )
        elif instruction == "validate_json":
            return self._validate_json(inputs.get("json_str", ""))
        else:
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Invalid instruction"
            }
    
    def _parse_json(self, json_str: str) -> Dict[str, Any]:
        """解析JSON"""
        try:
            data = json.loads(json_str)
            return {
                "success": True,
                "message": "JSON解析成功",
                "data": data
            }
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "message": f"JSON解析失败: {str(e)}",
                "error": str(e)
            }
    
    def _format_json(self, data: Any, indent: int = 2) -> Dict[str, Any]:
        """格式化JSON"""
        try:
            formatted = json.dumps(data, indent=indent, ensure_ascii=False)
            return {
                "success": True,
                "message": "JSON格式化成功",
                "formatted": formatted
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"JSON格式化失败: {str(e)}",
                "error": str(e)
            }
    
    def _validate_json(self, json_str: str) -> Dict[str, Any]:
        """验证JSON"""
        try:
            json.loads(json_str)
            return {
                "success": True,
                "message": "JSON格式有效"
            }
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "message": f"JSON格式无效: {str(e)}",
                "error": str(e)
            }
