"""
命令执行工具
用于执行shell命令
"""

import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Optional
import subprocess

from tools.workspace import resolve_workspace_root

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
    """工作区根目录（共享实现见 tools/workspace.py）。"""
    return resolve_workspace_root()


def _sandbox_params() -> Optional[Dict[str, str]]:
    """Docker 沙箱配置。显式 opt-in：环境变量 EAS_SANDBOX=docker 启用；未启用返回 None。

    - EAS_SANDBOX_IMAGE：容器镜像（默认 python:3.11-slim）
    - EAS_SANDBOX_NETWORK：容器网络模式（默认 none，最严格）
    """
    mode = (os.environ.get("EAS_SANDBOX") or "").strip().lower()
    if mode != "docker":
        return None
    return {
        "image": (os.environ.get("EAS_SANDBOX_IMAGE") or "python:3.11-slim").strip(),
        "network": (os.environ.get("EAS_SANDBOX_NETWORK") or "none").strip(),
    }


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
            "description": "执行shell命令（工作目录限于工作区内）；可选 Docker 沙箱（EAS_SANDBOX=docker）",
            "category": "other",
            "version": "1.1.0",
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
                    "error": "Dangerous command blocked",
                    # P0-b：结构化拦截标志——上游不再依赖错误文案字符串判断
                    "blocked": True,
                }

            if cwd and not _is_cwd_allowed(cwd):
                return {
                    "success": False,
                    "message": f"工作目录不在允许范围内，拒绝执行: {cwd!r}",
                    "error": "Workdir not allowed",
                    "blocked": True,
                }

            # 沙箱模式（显式 opt-in）：命令在容器内执行，工作区挂载为 /workspace。
            # fail-closed：启用沙箱但 Docker 不可用时直接拒绝，不静默回退宿主机执行。
            sandbox = _sandbox_params()
            if sandbox:
                return CommandRunner._run_in_docker(command, cwd, timeout, sandbox)

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
            # SWE-agent ACI：空输出必须显式确认，避免模型把"无输出"误读成失败或卡死
            empty_output = result.returncode == 0 and not stdout.strip() and not stderr.strip()
            if result.returncode == 0:
                message = "命令执行成功且无输出（已显式确认）" if empty_output else "命令执行完成"
            else:
                message = "命令执行失败"
            return {
                "success": result.returncode == 0,
                "message": message,
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "empty_output": empty_output,
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
    def _run_in_docker(command: str, cwd: Optional[str], timeout: int, sandbox: Dict[str, str]) -> Dict[str, Any]:
        """在 Docker 容器内执行命令（工作区挂载为 /workspace，网络默认隔离）。"""
        try:
            docker_path = shutil.which("docker")
            if not docker_path:
                return {
                    "success": False,
                    "message": "沙箱模式已启用（EAS_SANDBOX=docker）但 Docker 不可用，拒绝在宿主机直接执行",
                    "error": "Docker unavailable",
                    "blocked": True,
                }
            workdir = cwd if cwd else str(_resolve_workspace_root())
            argv = [
                docker_path, "run", "--rm",
                f"--network={sandbox['network']}",
                "-v", f"{workdir}:/workspace",
                "-w", "/workspace",
                sandbox["image"],
                "sh", "-lc", command,
            ]
            result = subprocess.run(argv, timeout=timeout, capture_output=True, text=False)
            stdout = (result.stdout or b"").decode("utf-8", errors="replace")
            stderr = (result.stderr or b"").decode("utf-8", errors="replace")
            empty_output = result.returncode == 0 and not stdout.strip() and not stderr.strip()
            if result.returncode == 0:
                message = "命令执行成功且无输出（已显式确认）" if empty_output else "命令执行完成"
            else:
                message = "命令执行失败"
            return {
                "success": result.returncode == 0,
                "message": message,
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "empty_output": empty_output,
                "sandbox": "docker",
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "message": f"命令执行超时（{timeout}秒，沙箱内）",
                "error": "Timeout"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"沙箱执行失败: {str(e)}",
                "error": str(e)
            }

    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        inputs = inputs or {}
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
