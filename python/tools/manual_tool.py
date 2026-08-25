"""
手动工具
用于需要人工干预的步骤
"""

from typing import Any, Dict


class ManualTool:
    """手动工具"""
    
    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        """获取工具元数据"""
        return {
            "tool_id": "manual",
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
                            "error": "错误信息（如果有）"
                        }
                    }
                ]
            }
        }
    
    @staticmethod
    def manual_step(description: str = "", error: str = None) -> Dict[str, Any]:
        """手动步骤"""
        if error:
            return {
                "success": False,
                "message": f"手动步骤失败: {error}",
                "error": error
            }
        
        return {
            "success": True,
            "message": f"手动步骤: {description}",
            "note": "此步骤需要人工确认"
        }
    
    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        return ManualTool.manual_step(
            description=inputs.get("description", ""),
            error=inputs.get("error")
        )
