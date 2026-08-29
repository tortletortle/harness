"""
命令执行工具
用于执行shell命令
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional
import subprocess

_DANGEROUS_PATTERNS = [
    r"rm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\s+/(\s|$)",
    r"rm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\s+/\*",
    r"rm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\s+~",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    r"\bmkfs\b",
    r"\bdd\s+if=.*of=/dev/",
    r">\s*/dev/sd[a-z]",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bformat\s+[a-z]:",
    r"\bdel\s+/[a-z]*s[a-z]*\s+/[a-z]*q[a-z]*\s+[a-z]:\\\\?\s*$",
    r"\brd\s+/[a-z]*s[a-z]*\s+/[a-z]*q[a-z]*\s+[a-z]:\\\\?\s*$",
    r"\breg\s+delete\b",
    r"\bdiskpart\b",
]
_DANGEROUS_RE = re.compile("|".join(_DANGEROUS_PATTERNS), re.IGNORECASE)


def _is_dangerous_command(command: str):
    match = _DANGEROUS_RE.search(command)
    if match:
        return match.group(0)
    return None


def _resolve_workspace_root() -> Path:
    root = os.environ.get("EAS_WORKSPACE_ROOT")
    if root:
        return Path(root).resolve()
    return Path(__file__).resolve().parent.parent.parent


def _is_cwd_allowed(cwd: str) -> bool:
    try:
        target = Path(cwd).resolve()
    except Exception:
        return False
    root = _resolve_workspace_root()
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


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
            if not command or not command.strip():
                return {
                    "success": False,
                    "message": "command 不能为空",
                    "error": "Missing parameter"
                }

            dangerous_match = _is_dangerous_command(command)
            if dangerous_match:
                return {
                    "success": False,
                    "message": f"命令被拒绝：命中危险命令模式 ({dangerous_match!r})",
                    "error": "Dangerous command blocked"
                }

            if cwd and not _is_cwd_allowed(cwd):
                return {
                    "success": False,
                    "message": f"工作目录不在允许范围内，拒绝执行: {cwd!r}",
                    "error": "Workdir not allowed"
                }

            result = subprocess.run(
                command,
                shell=shell,
                cwd=cwd,
                timeout=timeout,
                capture_output=True,
                text=False,
            )
            stdout = (result.stdout or b"").decode("utf-8", errors="replace")
            stderr = (result.stderr or b"").decode("utf-8", errors="replace")
            return {
                "success": result.returncode == 0,
                "message": "命令执行完成" if result.returncode == 0 else "命令执行失败",
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout
            stderr = exc.stderr
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
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
