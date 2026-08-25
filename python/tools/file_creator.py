"""
文件创建工具
用于创建文件和目录
"""

import os
from pathlib import Path
from typing import Any, Dict


class FileCreator:
    """文件创建工具"""
    
    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        """获取工具元数据"""
        return {
            "tool_id": "file_creator",
            "name": "文件创建工具",
            "description": "创建文件和目录",
            "category": "generation",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "create_file",
                        "description": "创建文件并写入内容",
                        "parameters": {
                            "path": "文件路径",
                            "content": "文件内容（可选）",
                            "encoding": "编码格式（默认utf-8）"
                        }
                    },
                    {
                        "name": "create_directory",
                        "description": "创建目录",
                        "parameters": {
                            "path": "目录路径",
                            "parents": "是否创建父目录（默认True）"
                        }
                    }
                ]
            }
        }
    
    @staticmethod
    def create_file(path: str, content: str = "", encoding: str = "utf-8") -> Dict[str, Any]:
        """创建文件"""
        try:
            file_path = Path(path)
            
            # 确保父目录存在
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 写入文件
            file_path.write_text(content, encoding=encoding)
            
            return {
                "success": True,
                "message": f"文件创建成功: {path}",
                "path": str(file_path.absolute()),
                "size": len(content)
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"文件创建失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def create_directory(path: str, parents: bool = True) -> Dict[str, Any]:
        """创建目录"""
        try:
            dir_path = Path(path)
            dir_path.mkdir(parents=parents, exist_ok=True)
            
            return {
                "success": True,
                "message": f"目录创建成功: {path}",
                "path": str(dir_path.absolute())
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"目录创建失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        if instruction == "create_file":
            return FileCreator.create_file(
                path=inputs.get("path", ""),
                content=inputs.get("content", ""),
                encoding=inputs.get("encoding", "utf-8")
            )
        elif instruction == "create_directory":
            return FileCreator.create_directory(
                path=inputs.get("path", ""),
                parents=inputs.get("parents", True)
            )
        else:
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Invalid instruction"
            }
