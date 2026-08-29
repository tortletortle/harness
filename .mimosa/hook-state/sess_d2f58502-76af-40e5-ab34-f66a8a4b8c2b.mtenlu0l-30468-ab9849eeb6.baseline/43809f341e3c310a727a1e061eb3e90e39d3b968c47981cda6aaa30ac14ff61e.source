"""
Git操作工具
用于版本控制操作

路径语义：仓库路径（path）相对时基于工作区根目录解析（tools/workspace.py）。
"""

import subprocess
from typing import Any, Dict

from tools.workspace import resolve_workspace_path


class GitOperator:
    """Git操作工具"""
    
    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        """获取工具元数据"""
        return {
            "tool_id": "git_operator",
            "name": "Git操作工具",
            "description": "Git版本控制操作",
            "category": "other",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "git_init",
                        "description": "初始化Git仓库",
                        "parameters": {
                            "path": "仓库路径"
                        }
                    },
                    {
                        "name": "git_add",
                        "description": "添加文件到暂存区",
                        "parameters": {
                            "path": "仓库路径",
                            "files": "文件列表或'.'表示全部"
                        }
                    },
                    {
                        "name": "git_commit",
                        "description": "提交更改（默认先自动暂存全部改动再提交，无需先调用 git_add）",
                        "parameters": {
                            "path": "仓库路径",
                            "message": "提交信息",
                            "auto_stage": "是否自动暂存（默认True）"
                        }
                    },
                    {
                        "name": "git_clone",
                        "description": "克隆仓库",
                        "parameters": {
                            "url": "仓库URL",
                            "path": "目标路径（可选）"
                        }
                    }
                ]
            }
        }
    
    @staticmethod
    def git_init(path: str) -> Dict[str, Any]:
        """初始化Git仓库"""
        try:
            result = subprocess.run(
                ["git", "init"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            return {
                "success": result.returncode == 0,
                "message": "Git仓库初始化成功" if result.returncode == 0 else "初始化失败",
                "output": result.stdout,
                "error": result.stderr if result.returncode != 0 else None
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Git初始化失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def git_add(path: str, files: str = ".") -> Dict[str, Any]:
        """添加文件到暂存区"""
        try:
            result = subprocess.run(
                ["git", "add", files],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            return {
                "success": result.returncode == 0,
                "message": "文件添加成功" if result.returncode == 0 else "添加失败",
                "output": result.stdout,
                "error": result.stderr if result.returncode != 0 else None
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Git add失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def git_commit(path: str, message: str, auto_stage: bool = True) -> Dict[str, Any]:
        """提交更改。

        auto_stage=True（默认）时先 `git add -A` 再提交：消除"未暂存导致
        commit 必失败"的语义缺口——模型在"提交改动"任务上会在 git_add /
        git_commit 之间随机摇摆，自动暂存让两种输出都能正确完成。
        """
        try:
            staged = None
            if auto_stage:
                stage_result = subprocess.run(
                    ["git", "add", "-A"],
                    cwd=path,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if stage_result.returncode != 0:
                    return {
                        "success": False,
                        "message": "自动暂存失败",
                        "output": stage_result.stdout,
                        "error": stage_result.stderr or "git add -A failed"
                    }
                # 记录实际进入本次提交的文件，供审计
                listed = subprocess.run(
                    ["git", "diff", "--cached", "--name-only"],
                    cwd=path,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                staged = [f for f in listed.stdout.splitlines() if f.strip()]

            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=30
            )

            return {
                "success": result.returncode == 0,
                "message": "提交成功" if result.returncode == 0 else "提交失败",
                "output": result.stdout,
                "error": result.stderr if result.returncode != 0 else None,
                "staged_files": staged,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Git commit失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def git_clone(url: str, path: str = None) -> Dict[str, Any]:
        """克隆仓库"""
        try:
            cmd = ["git", "clone", url]
            if path:
                cmd.append(path)
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            return {
                "success": result.returncode == 0,
                "message": "克隆成功" if result.returncode == 0 else "克隆失败",
                "output": result.stdout,
                "error": result.stderr if result.returncode != 0 else None
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Git clone失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        # 仓库路径相对时基于工作区根解析（"." 也指向工作区根）
        if "path" in inputs:
            inputs = {**inputs, "path": str(resolve_workspace_path(inputs["path"]))}
        if instruction == "git_init":
            return GitOperator.git_init(path=inputs.get("path", "."))
        elif instruction == "git_add":
            return GitOperator.git_add(
                path=inputs.get("path", "."),
                files=inputs.get("files", ".")
            )
        elif instruction == "git_commit":
            return GitOperator.git_commit(
                path=inputs.get("path", "."),
                message=inputs.get("message", "Initial commit"),
                auto_stage=bool(inputs.get("auto_stage", True)),
            )
        elif instruction == "git_clone":
            return GitOperator.git_clone(
                url=inputs.get("url", ""),
                path=inputs.get("path")
            )
        else:
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Invalid instruction"
            }
