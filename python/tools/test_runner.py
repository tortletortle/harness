"""测试运行工具：白名单命令模板执行器（无 shell 逃生舱）。

设计稿 §23.2：计划白名单内补全 run/test/git 的 test 位。

安全边界：
- 只执行登记过的命令模板（默认内置 pytest / npm，可由 server 从
  config `test_runner.commands` 注入覆盖）；inputs.suite 必须命中模板名。
- argv 列表拼接，绝不经 shell；args 只能作为独立 argv 项追加，
  注入面为构造性不存在。
- cwd 固定解析到工作区根（tools/workspace.py 语义），不受输入控制。
- 返回 exit_code/returncode，供 AcceptanceRunner 的 exit_code 检查直接消费。
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict, List

from logging_config import get_logger
from tools.workspace import resolve_workspace_root

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 120
_STDOUT_TAIL = 4000
_STDERR_TAIL = 2000

# 内置模板：占位符支持 {test_path} 与 {python}；args 作为独立 argv 项追加。
BUILTIN_TEMPLATES: Dict[str, List[str]] = {
    "pytest": ["{python}", "-m", "pytest", "{test_path}", "-q"],
    "npm": ["npm", "test"],
}

# 运行时可由 server 从 config 注入覆盖（白名单只能来自服务端，不来自步骤输入）。
_TEMPLATES: Dict[str, List[str]] = {}


def configure_templates(templates: Dict[str, List[str]] | None) -> None:
    """server 启动时注入白名单模板；传入空/None 恢复内置模板。"""
    global _TEMPLATES
    cleaned: Dict[str, List[str]] = {}
    for name, argv in (templates or {}).items():
        if isinstance(name, str) and isinstance(argv, list) and argv and all(
            isinstance(part, str) for part in argv
        ):
            cleaned[name.strip()] = list(argv)
    _TEMPLATES = cleaned


def get_templates() -> Dict[str, List[str]]:
    return dict(_TEMPLATES) if _TEMPLATES else dict(BUILTIN_TEMPLATES)


def _tail(text: str | None, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else "...[截断]\n" + text[-limit:]


class TestRunner:
    """测试运行工具"""

    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        return {
            "tool_id": "test_runner",
            "name": "测试运行工具",
            "description": "运行登记过的测试命令模板（pytest/npm 等），返回退出码供确定性验收",
            "category": "test",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "run",
                        "description": "运行指定测试模板（run_tests / execute 为同义别名，executor 未指定 instruction 时默认 execute）",
                        "parameters": {
                            "suite": "模板名（必填，如 pytest / npm）",
                            "test_path": "测试文件或目录（相对工作区根，可选）",
                            "args": "附加参数列表（可选，逐项追加为 argv）",
                            "timeout": "超时秒数（默认 120）",
                        },
                    }
                ]
            },
        }

    @staticmethod
    def build_argv(suite: str, inputs: Dict[str, Any]) -> List[str]:
        """按模板拼 argv。占位符替换值为数据，模板本身来自服务端白名单。"""
        template = get_templates()[suite]
        substitutions = {
            "python": sys.executable,
            "test_path": str(inputs.get("test_path") or ""),
            "suite": suite,
        }
        argv = [part.format(**substitutions) for part in template]
        argv = [part for part in argv if part]
        args = inputs.get("args")
        if isinstance(args, list):
            argv.extend(str(item) for item in args)
        return argv

    @staticmethod
    def _run_impl(suite: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        templates = get_templates()
        if suite not in templates:
            return {
                "success": False,
                "blocked": True,
                "message": f"未登记的测试模板: {suite}",
                "error": "Unknown test suite",
                "available": sorted(templates.keys()),
            }
        try:
            argv = TestRunner.build_argv(suite, inputs)
        except (KeyError, IndexError, ValueError) as exc:
            return {
                "success": False,
                "blocked": True,
                "message": f"模板参数错误: {exc}",
                "error": "Invalid template args",
            }
        timeout = float(inputs.get("timeout", DEFAULT_TIMEOUT))
        cwd = str(resolve_workspace_root())
        logger.info("test_runner 执行: {} (cwd={})", argv, cwd)
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
                "message": f"测试超时（{timeout}秒）",
                "error": "Test run timeout",
            }
        except FileNotFoundError as exc:
            return {
                "success": False,
                "exit_code": None,
                "message": f"命令不存在: {exc}",
                "error": "Command not found",
            }
        rc = completed.returncode
        return {
            "success": rc == 0,
            "exit_code": rc,
            "returncode": rc,
            "message": "测试通过" if rc == 0 else f"测试失败（exit {rc}）",
            "stdout": _tail(completed.stdout, _STDOUT_TAIL),
            "stderr": _tail(completed.stderr, _STDERR_TAIL),
            "argv": argv,
            "cwd": cwd,
        }

    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        # executor 对未指定 instruction 的步骤推断为 "execute"，与 run 同义。
        if instruction not in ("run", "run_tests", "execute"):
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Unknown instruction",
            }
        suite = str((inputs or {}).get("suite") or "").strip()
        if not suite:
            return {
                "success": False,
                "blocked": True,
                "message": "缺少 suite（测试模板名）",
                "error": "Missing suite",
            }
        return TestRunner._run_impl(suite, inputs or {})
