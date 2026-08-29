"""
文件创建工具
用于创建文件和目录

路径语义：相对路径基于工作区根目录解析（见 tools/workspace.py）。
"""

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any, Dict

from tools.workspace import resolve_workspace_path

# 默认超时（秒）。文件IO一般不会真正卡死，但网络盘/异常磁盘故障时可能长时间阻塞，
# 这里给每个操作一个上限，避免拖垂整个执行流程。
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


class FileCreator:
    """文件创建工具"""

    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        """获取工具元数据"""
        return {
            "tool_id": "file_creator",
            "name": "文件创建工具",
            "description": "创建文件和目录（相对路径基于工作区根目录解析）",
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
                            "encoding": "编码格式（默认utf-8）",
                            "timeout": f"超时时间（秒，默认{DEFAULT_TIMEOUT}）"
                        }
                    },
                    {
                        "name": "create_directory",
                        "description": "创建目录",
                        "parameters": {
                            "path": "目录路径",
                            "parents": "是否创建父目录（默认True）",
                            "timeout": f"超时时间（秒，默认{DEFAULT_TIMEOUT}）"
                        }
                    }
                ]
            }
        }

    @staticmethod
    def _create_file_impl(path: str, content: str = "", encoding: str = "utf-8") -> Dict[str, Any]:
        """创建文件"""
        try:
            file_path = resolve_workspace_path(path)

            # 确保父目录存在
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # 写入文件
            file_path.write_text(content, encoding=encoding)

            return {
                "success": True,
                "message": f"文件创建成功: {file_path}",
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
    def create_file(path: str, content: str = "", encoding: str = "utf-8", timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """创建文件（带超时）"""
        return _run_with_timeout(FileCreator._create_file_impl, timeout, path, content, encoding)

    @staticmethod
    def _create_directory_impl(path: str, parents: bool = True) -> Dict[str, Any]:
        """创建目录"""
        try:
            dir_path = resolve_workspace_path(path)
            dir_path.mkdir(parents=parents, exist_ok=True)

            return {
                "success": True,
                "message": f"目录创建成功: {dir_path}",
                "path": str(dir_path.absolute())
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"目录创建失败: {str(e)}",
                "error": str(e)
            }

    @staticmethod
    def create_directory(path: str, parents: bool = True, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """创建目录（带超时）"""
        return _run_with_timeout(FileCreator._create_directory_impl, timeout, path, parents)

    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        timeout = inputs.get("timeout", DEFAULT_TIMEOUT)
        # 兼容 Designer 可能输出的两种键名，优先用标准键名
        path = inputs.get("path") or inputs.get("file_path", "")
        content = inputs.get("content") or inputs.get("file_content", "")
        encoding = inputs.get("encoding", "utf-8")
        if instruction == "create_file":
            return FileCreator.create_file(
                path=path,
                content=content,
                encoding=encoding,
                timeout=timeout
            )
        elif instruction == "create_directory":
            return FileCreator.create_directory(
                path=inputs.get("path") or inputs.get("file_path", ""),
                parents=inputs.get("parents", True),
                timeout=timeout
            )
        else:
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Invalid instruction"
            }
