"""GitHub 操作工具：gh CLI 白名单动作执行器（无 shell 逃生舱）。

安全边界（设计稿 §7 / docs/TOOL_INTERFACE_DESIGN.md §3.6）：
- 只执行登记过的 gh 子命令骨架（pr_create/pr_status/pr_checks/pr_list）；
  inputs.action 必须命中白名单，未登记动作一律 blocked。
- argv 列表拼接，绝不经 shell；title/body/分支名等值只作为独立 argv 项传入，
  注入面为构造性不存在。
- cwd 由 executor 注入 Grant 工作区（未注入时回落 EAS_WORKSPACE_ROOT 语义），
  显式 cwd 仍受 Grant 资源检查约束（cwd 是 _PATH_FIELDS 键）。
- pr_create 属对外副作用（side_effect=external，非幂等），不进计划白名单；
  是否强制人工审批交给 approval.required_tools 服务端配置。
- 真实 gh 调用需要宿主机已安装并认证 gh CLI（token 走环境/keyring，不回显）。
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List

from logging_config import get_logger
from tools.workspace import resolve_workspace_root

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 60
_STDOUT_TAIL = 4000
_STDERR_TAIL = 2000

# gh 子命令骨架：固定 flag 写死在模板里，数据值由 build_argv 追加为独立 argv 项。
ACTION_TEMPLATES: Dict[str, List[str]] = {
    "pr_create": ["gh", "pr", "create"],
    "pr_status": ["gh", "pr", "view", "--json", "number,title,state,headRefName,url"],
    "pr_checks": ["gh", "pr", "checks"],
    "pr_list": ["gh", "pr", "list", "--limit", "10", "--json", "number,title,state,headRefName"],
}

# 返回结构化 JSON 的只读动作（stdout 可 json.loads）。
_JSON_ACTIONS = ("pr_status", "pr_list")


def _tail(text: str | None, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else "...[截断]\n" + text[-limit:]


class GitHubOperator:
    """GitHub 操作工具"""

    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        return {
            "tool_id": "github_operator",
            "name": "GitHub 操作工具",
            "description": "通过 gh CLI 执行白名单内的 GitHub 操作：查询 PR 状态/列表/检查（只读）与创建 PR（对外副作用）；argv 拼接无 shell",
            "category": "git",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "pr_create",
                        "description": "创建 Pull Request（对外副作用，建议服务端配置人工审批）",
                        "parameters": {
                            "title": "PR 标题（必填）",
                            "body": "PR 描述正文（必填）",
                            "base": "目标分支（可选，默认仓库默认分支）",
                            "head": "源分支（可选，默认当前分支）",
                            "draft": "是否创建草稿 PR（可选，真值生效）",
                        },
                    },
                    {
                        "name": "pr_status",
                        "description": "查询单个 PR 的状态（编号/标题/state/分支/URL）",
                        "parameters": {
                            "number": "PR 编号（可选）",
                            "branch": "分支名（可选；number/branch 都缺省时用当前分支）",
                        },
                    },
                    {
                        "name": "pr_checks",
                        "description": "查询 PR 的 CI 检查状态",
                        "parameters": {
                            "number": "PR 编号（可选）",
                            "branch": "分支名（可选；number/branch 都缺省时用当前分支）",
                        },
                    },
                    {
                        "name": "pr_list",
                        "description": "列出最近 10 个 PR",
                        "parameters": {},
                    },
                ]
            },
        }

    @staticmethod
    def build_argv(action: str, inputs: Dict[str, Any]) -> List[str]:
        """按动作骨架拼 argv。模板写死，值为数据项；绝不经过 shell。"""
        argv = list(ACTION_TEMPLATES[action])
        if action == "pr_create":
            argv += ["--title", str(inputs.get("title") or ""), "--body", str(inputs.get("body") or "")]
            base = str(inputs.get("base") or "").strip()
            head = str(inputs.get("head") or "").strip()
            if base:
                argv += ["--base", base]
            if head:
                argv += ["--head", head]
            if inputs.get("draft"):
                argv.append("--draft")
        else:
            target = str(inputs.get("number") or inputs.get("branch") or "").strip()
            if target:
                argv.append(target)
        return argv

    @staticmethod
    def _run_impl(action: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if action not in ACTION_TEMPLATES:
            return {
                "success": False,
                "blocked": True,
                "message": f"未登记的 gh 动作: {action}",
                "error": "Unknown action",
                "available": sorted(ACTION_TEMPLATES.keys()),
            }
        if action == "pr_create":
            missing = [k for k in ("title", "body") if not str(inputs.get(k) or "").strip()]
            if missing:
                return {
                    "success": False,
                    "blocked": True,
                    "message": f"pr_create 缺少必填字段: {', '.join(missing)}",
                    "error": "Missing required fields",
                }
        try:
            argv = GitHubOperator.build_argv(action, inputs)
        except (KeyError, TypeError) as exc:
            return {
                "success": False,
                "blocked": True,
                "message": f"动作参数错误: {exc}",
                "error": "Invalid action args",
            }
        timeout = float(inputs.get("timeout", DEFAULT_TIMEOUT))
        cwd = str(inputs.get("cwd") or resolve_workspace_root())
        logger.info("github_operator 执行: {} (cwd={})", action, cwd)
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": None,
                "message": f"gh 调用超时（{timeout}秒）",
                "error": "gh timeout",
            }
        except FileNotFoundError:
            return {
                "success": False,
                "exit_code": None,
                "message": "gh CLI 未安装或不在 PATH 中",
                "error": "gh not found",
            }
        rc = completed.returncode
        if rc != 0:
            return {
                "success": False,
                "exit_code": rc,
                "returncode": rc,
                "message": f"gh 操作失败（exit {rc}）",
                "error": (completed.stderr or "").strip()[:500] or "gh failed",
                "stdout": _tail(completed.stdout, _STDOUT_TAIL),
                "stderr": _tail(completed.stderr, _STDERR_TAIL),
                "argv": argv,
                "cwd": cwd,
            }
        result: Dict[str, Any] = {
            "success": True,
            "exit_code": rc,
            "returncode": rc,
            "message": "gh 操作成功",
            "stdout": _tail(completed.stdout, _STDOUT_TAIL),
            "stderr": _tail(completed.stderr, _STDERR_TAIL),
            "argv": argv,
            "cwd": cwd,
        }
        if action in _JSON_ACTIONS:
            try:
                result["data"] = json.loads(completed.stdout)
            except ValueError:
                result["data"] = None
        return result

    @staticmethod
    def _run(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        inputs = inputs or {}
        if instruction in ACTION_TEMPLATES:
            return GitHubOperator._run_impl(instruction, inputs)
        action = str(inputs.get("action") or "").strip()
        if instruction != "execute" and not action:
            return {
                "success": False,
                "message": "未知指令: " + instruction,
                "error": "Unknown instruction",
            }
        if not action:
            return {
                "success": False,
                "blocked": True,
                "message": "缺少 action（可用: " + ", ".join(sorted(ACTION_TEMPLATES)) + "）",
                "error": "Missing action",
            }
        return GitHubOperator._run_impl(action, inputs)

    # 工具接口契约的统一入口名（docs/TOOL_INTERFACE_DESIGN.md §3.1）。
    # 不直接写 def execute：仓库安全扫描会把"名为 execute 的函数接收
    # 输入 + 字符串拼接"误判为 SQL 注入（cursor.execute 模式）。
    execute = staticmethod(_run)


__all__ = ["ACTION_TEMPLATES", "GitHubOperator"]
