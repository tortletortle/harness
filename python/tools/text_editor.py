"""
文本编辑工具
用于编辑现有文件
"""

from pathlib import Path
from typing import Any, Dict


class TextEditor:
    """文本编辑工具"""
    
    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        """获取工具元数据"""
        return {
            "tool_id": "text_editor",
            "name": "文本编辑工具",
            "description": "编辑文本文件内容",
            "category": "generation",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "read_file",
                        "description": "读取文件内容",
                        "parameters": {
                            "path": "文件路径",
                            "encoding": "编码格式（默认utf-8）"
                        }
                    },
                    {
                        "name": "append_to_file",
                        "description": "追加内容到文件末尾",
                        "parameters": {
                            "path": "文件路径",
                            "content": "要追加的内容",
                            "encoding": "编码格式（默认utf-8）"
                        }
                    },
                    {
                        "name": "replace_in_file",
                        "description": "替换文件中的文本",
                        "parameters": {
                            "path": "文件路径",
                            "old_text": "要替换的文本",
                            "new_text": "新文本",
                            "encoding": "编码格式（默认utf-8）"
                        }
                    },
                    {
                        "name": "insert_at_line",
                        "description": "在指定行插入内容",
                        "parameters": {
                            "path": "文件路径",
                            "line_number": "行号（从1开始）",
                            "content": "要插入的内容",
                            "encoding": "编码格式（默认utf-8）"
                        }
                    }
                ]
            }
        }
    
    @staticmethod
    def read_file(path: str, encoding: str = "utf-8") -> Dict[str, Any]:
        """读取文件"""
        try:
            file_path = Path(path)
            
            if not file_path.exists():
                return {
                    "success": False,
                    "message": f"文件不存在: {path}",
                    "error": "File not found"
                }
            
            content = file_path.read_text(encoding=encoding)
            
            return {
                "success": True,
                "message": "文件读取成功",
                "content": content,
                "lines": len(content.splitlines()),
                "size": len(content)
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"读取文件失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def append_to_file(path: str, content: str, encoding: str = "utf-8") -> Dict[str, Any]:
        """追加内容到文件"""
        try:
            file_path = Path(path)
            
            # 读取现有内容
            if file_path.exists():
                existing = file_path.read_text(encoding=encoding)
            else:
                existing = ""
            
            # 追加内容
            new_content = existing + content
            file_path.write_text(new_content, encoding=encoding)
            
            return {
                "success": True,
                "message": "内容追加成功",
                "new_size": len(new_content)
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"追加内容失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def replace_in_file(path: str, old_text: str, new_text: str, encoding: str = "utf-8") -> Dict[str, Any]:
        """替换文件中的文本"""
        try:
            file_path = Path(path)
            
            if not file_path.exists():
                return {
                    "success": False,
                    "message": f"文件不存在: {path}",
                    "error": "File not found"
                }
            
            content = file_path.read_text(encoding=encoding)
            
            if old_text not in content:
                return {
                    "success": False,
                    "message": "未找到要替换的文本",
                    "error": "Text not found"
                }
            
            new_content = content.replace(old_text, new_text)
            file_path.write_text(new_content, encoding=encoding)
            
            count = content.count(old_text)
            
            return {
                "success": True,
                "message": f"替换成功，共{count}处",
                "count": count
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"替换失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def insert_at_line(path: str, line_number: int, content: str, encoding: str = "utf-8") -> Dict[str, Any]:
        """在指定行插入内容"""
        try:
            file_path = Path(path)
            
            if not file_path.exists():
                return {
                    "success": False,
                    "message": f"文件不存在: {path}",
                    "error": "File not found"
                }
            
            lines = file_path.read_text(encoding=encoding).splitlines(keepends=True)
            
            if line_number < 1 or line_number > len(lines) + 1:
                return {
                    "success": False,
                    "message": f"行号超出范围: {line_number}",
                    "error": "Line number out of range"
                }
            
            # 插入新内容（行号从1开始）
            lines.insert(line_number - 1, content + "\n")
            
            new_content = "".join(lines)
            file_path.write_text(new_content, encoding=encoding)
            
            return {
                "success": True,
                "message": f"在第{line_number}行插入成功",
                "line_number": line_number
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"插入失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def write_file(path: str, content: str, encoding: str = "utf-8") -> Dict[str, Any]:
        """写入文件（创建或覆盖）"""
        try:
            file_path = Path(path)
            
            # 确保父目录存在
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 写入内容
            file_path.write_text(content, encoding=encoding)
            
            return {
                "success": True,
                "message": "文件写入成功",
                "path": str(file_path),
                "size": len(content)
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"写入文件失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        if instruction == "write" or instruction == "write_file":
            # 兼容 content 或 code 参数
            content = inputs.get("content") or inputs.get("code", "")
            return TextEditor.write_file(
                path=inputs.get("path", ""),
                content=content,
                encoding=inputs.get("encoding", "utf-8")
            )
        elif instruction == "read_file":
            return TextEditor.read_file(
                path=inputs.get("path", ""),
                encoding=inputs.get("encoding", "utf-8")
            )
        elif instruction == "append_to_file":
            return TextEditor.append_to_file(
                path=inputs.get("path", ""),
                content=inputs.get("content", ""),
                encoding=inputs.get("encoding", "utf-8")
            )
        elif instruction == "replace_in_file":
            return TextEditor.replace_in_file(
                path=inputs.get("path", ""),
                old_text=inputs.get("old_text", ""),
                new_text=inputs.get("new_text", ""),
                encoding=inputs.get("encoding", "utf-8")
            )
        elif instruction == "insert_at_line":
            return TextEditor.insert_at_line(
                path=inputs.get("path", ""),
                line_number=inputs.get("line_number", 1),
                content=inputs.get("content", ""),
                encoding=inputs.get("encoding", "utf-8")
            )
        else:
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Invalid instruction"
            }
