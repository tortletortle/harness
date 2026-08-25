"""
命令执行工具
用于执行shell命令
"""

import subprocess
from typing import Any, Dict


class CommandRunner:
    """命令执行工具"""
    
    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        """获取工具元数据"""
        return {
            "tool_id": "command_runner",
            "name": "命令执行工具",
            "description": "执行shell命令",
            "category": "other",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "run_command",
                        "description": "执行shell命令",
                        "parameters": {
                            "command": "要执行的命令",
                            "cwd": "工作目录（可选）",
                            "timeout": "超时时间（秒，可选）",
                            "shell": "是否使用shell（默认True）"
                        }
                    }
                ]
            }
        }
    
    @staticmethod
    def run_command(
        command: str,
        cwd: str = None,
        timeout: int = 60,
        shell: bool = True
    ) -> Dict[str, Any]:
        """执行命令"""
        try:
            result = subprocess.run(
                command,
                shell=shell,
                cwd=cwd,
                timeout=timeout,
                capture_output=True,
                text=True
            )
            
            return {
                "success": result.returncode == 0,
                "message": "命令执行完成" if result.returncode == 0 else "命令执行失败",
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "message": f"命令执行超时（{timeout}秒）",
                "error": "Timeout"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"命令执行失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        if instruction == "run_command" or instruction == "run":
            return CommandRunner.run_command(
                command=inputs.get("command", ""),
                cwd=inputs.get("cwd") or inputs.get("path"),  # 兼容 'path' 键
                timeout=inputs.get("timeout", 60),
                shell=inputs.get("shell", True)
            )
        else:
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Invalid instruction"
            }
