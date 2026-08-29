"""Deterministic acceptance checks for executed work items.

The runner observes the workspace and the structured result of the tool that just
ran. It never asks an LLM to decide whether a deterministic check passed.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from logging_config import get_logger
from tools.workspace import resolve_workspace_path, resolve_workspace_root

logger = get_logger(__name__)

_DEFAULT_MAX_READ_BYTES = 2 * 1024 * 1024


@dataclass
class AcceptanceResult:
    """Serializable result of a deterministic acceptance run."""

    status: str
    passed: bool
    reward: float
    checks: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def evidence(self) -> List[Dict[str, Any]]:
        return [check.get("evidence", {}) for check in self.checks if check.get("evidence")]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "reward": self.reward,
            "checks": self.checks,
            "evidence": self.evidence,
        }


class AcceptanceRunner:
    """Run bounded, deterministic checks against the post-tool environment."""

    TYPE_ALIASES = {
        "artifact_exists": "file_exists",
        "command_exit_code": "exit_code",
        "diff_scope_valid": "diff_scope",
    }

    def __init__(
        self,
        workspace_root: Optional[Path | str] = None,
        max_read_bytes: int = _DEFAULT_MAX_READ_BYTES,
        command_timeout: float = 10.0,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else resolve_workspace_root()
        self.max_read_bytes = max(1, int(max_read_bytes))
        self.command_timeout = max(0.1, float(command_timeout))

    def run(
        self,
        acceptance_spec: Any,
        tool_result: Optional[Dict[str, Any]] = None,
        workspace_root: Optional[Path | str] = None,
    ) -> AcceptanceResult:
        """Evaluate all checks. Any malformed/unknown check fails closed."""
        runner = self
        if workspace_root is not None:
            runner = AcceptanceRunner(
                workspace_root=workspace_root,
                max_read_bytes=self.max_read_bytes,
                command_timeout=self.command_timeout,
            )

        checks_spec = runner._normalise_spec(acceptance_spec)
        if not checks_spec:
            return AcceptanceResult(
                status="unverified",
                passed=False,
                reward=0.0,
                checks=[],
            )

        checks: List[Dict[str, Any]] = []
        for spec in checks_spec:
            checks.append(runner._run_check(spec, tool_result or {}))

        passed = all(bool(check.get("passed")) for check in checks)
        return AcceptanceResult(
            status="passed" if passed else "failed",
            passed=passed,
            reward=1.0 if passed else 0.0,
            checks=checks,
        )

    @staticmethod
    def _normalise_spec(spec: Any) -> List[Dict[str, Any]]:
        if isinstance(spec, dict):
            if isinstance(spec.get("checks"), list):
                spec = spec["checks"]
            else:
                spec = [spec]
        if not isinstance(spec, list):
            return [{"type": "__invalid_spec__"}] if spec is not None else []
        # 保留非法列表项，让 _run_check 走 fail-closed，而不是把它们
        # 静默过滤后错误地返回 unverified。
        return [item if isinstance(item, dict) else {"type": "__invalid_spec__"} for item in spec]

    def _run_check(self, spec: Dict[str, Any], tool_result: Dict[str, Any]) -> Dict[str, Any]:
        requested_type = str(spec.get("type", "")).strip().lower()
        check_type = self.TYPE_ALIASES.get(requested_type, requested_type)
        try:
            if check_type == "file_exists":
                return self._check_file_exists(spec)
            if check_type == "content_contains":
                return self._check_content_contains(spec)
            if check_type == "exit_code":
                return self._check_exit_code(spec, tool_result)
            if check_type == "diff_scope":
                return self._check_diff_scope(spec)
            return self._failed(
                requested_type or "unknown",
                "未知验收类型（fail-closed）",
            )
        except Exception as exc:
            logger.warning("验收检查失败（{}）: {}", check_type or "unknown", exc)
            return self._failed(check_type or "unknown", f"验收异常（fail-closed）: {exc}")

    def _check_file_exists(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        path_value = spec.get("path", spec.get("target"))
        if not path_value:
            return self._failed("file_exists", "缺少 path")
        path = self._resolve_check_path(path_value)
        exists = path.exists()
        expected_kind = spec.get("kind")
        kind_ok = expected_kind not in ("file", "directory") or (
            (expected_kind == "file" and path.is_file())
            or (expected_kind == "directory" and path.is_dir())
        )
        passed = exists and kind_ok
        evidence = {
            "path": str(path),
            "exists": exists,
            "is_file": path.is_file() if exists else False,
            "is_directory": path.is_dir() if exists else False,
        }
        if exists and path.is_file():
            evidence["size"] = path.stat().st_size
        reason = "" if passed else ("路径不存在" if not exists else f"类型不是 {expected_kind}")
        return self._check("file_exists", passed, reason, evidence)

    def _check_content_contains(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        path_value = spec.get("path", spec.get("target"))
        expected = spec.get("text", spec.get("content", spec.get("contains")))
        if not path_value:
            return self._failed("content_contains", "缺少 path")
        if expected is None:
            return self._failed("content_contains", "缺少 text/content")
        path = self._resolve_check_path(path_value)
        if not path.is_file():
            return self._check("content_contains", False, "目标不是文件或不存在", {"path": str(path)})

        size = path.stat().st_size
        if size > self.max_read_bytes:
            return self._check(
                "content_contains",
                False,
                f"文件超过读取上限（{self.max_read_bytes} bytes）",
                {"path": str(path), "size": size},
            )
        encoding = str(spec.get("encoding", "utf-8"))
        try:
            content = path.read_text(encoding=encoding)
        except (UnicodeError, OSError) as exc:
            return self._check(
                "content_contains",
                False,
                f"文件无法读取: {exc}",
                {"path": str(path), "size": size},
            )
        expected_text = str(expected)
        matched = expected_text in content
        digest = hashlib.sha256(content.encode(encoding, errors="replace")).hexdigest()
        evidence = {
            "path": str(path),
            "size": size,
            "sha256": digest,
            "matched": matched,
        }
        return self._check(
            "content_contains",
            matched,
            "" if matched else "文件内容不包含期望文本",
            evidence,
        )

    def _check_exit_code(self, spec: Dict[str, Any], tool_result: Dict[str, Any]) -> Dict[str, Any]:
        actual = tool_result.get("returncode", tool_result.get("exit_code"))
        expected = spec.get("equals", spec.get("expected", spec.get("returncode", 0)))
        try:
            expected_int = int(expected)
        except (TypeError, ValueError):
            return self._failed("exit_code", "期望 exit code 不是整数")
        passed = actual is not None and int(actual) == expected_int
        evidence = {"expected": expected_int, "actual": actual}
        return self._check(
            "exit_code",
            passed,
            "缺少工具 returncode" if actual is None else ("exit code 不匹配" if not passed else ""),
            evidence,
        )

    def _check_diff_scope(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        allowed_value = spec.get("allowed", spec.get("paths", spec.get("allow")))
        if not isinstance(allowed_value, list):
            return self._failed("diff_scope", "缺少 allowed 路径列表")
        allowed = {self._normalise_git_path(item) for item in allowed_value}
        actual, error = self._changed_files()
        if error:
            return self._check("diff_scope", False, error, {"allowed": sorted(allowed)})
        allow_extra = bool(spec.get("allow_extra", False))
        actual_set = set(actual)
        if allow_extra:
            passed = actual_set.issubset(allowed)
            unexpected = sorted(actual_set - allowed)
            missing: List[str] = []
        else:
            passed = actual_set == allowed
            unexpected = sorted(actual_set - allowed)
            missing = sorted(allowed - actual_set)
        evidence = {
            "allowed": sorted(allowed),
            "actual": sorted(actual_set),
            "unexpected": unexpected,
            "missing": missing,
        }
        reason = "" if passed else "Git 变更范围不符合验收标准"
        return self._check("diff_scope", passed, reason, evidence)

    def _changed_files(self) -> tuple[List[str], Optional[str]]:
        if not self.workspace_root.is_dir():
            return [], f"workspace 不存在: {self.workspace_root}"
        commands = [
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            ["git", "diff", "--name-only"],
            ["git", "diff", "--cached", "--name-only"],
        ]
        files = set()
        for command in commands:
            try:
                result = subprocess.run(
                    command,
                    cwd=self.workspace_root,
                    capture_output=True,
                    text=True,
                    timeout=self.command_timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return [], f"Git 查询失败: {exc}"
            if result.returncode != 0:
                details = (result.stderr or result.stdout or "").strip()
                return [], f"Git 查询失败: {details or result.returncode}"
            if command[1] == "status":
                for line in result.stdout.splitlines():
                    if len(line) >= 4:
                        value = line[3:].strip()
                        if " -> " in value:
                            value = value.split(" -> ", 1)[-1]
                        if value:
                            files.add(self._normalise_git_path(value))
            else:
                files.update(
                    self._normalise_git_path(line)
                    for line in result.stdout.splitlines()
                    if line.strip()
                )
        return sorted(files), None

    def _resolve_check_path(self, value: Any) -> Path:
        raw = Path(str(value))
        if raw.is_absolute():
            # Keep existing EAS semantics for explicit absolute paths.
            return raw.resolve()
        root = self.workspace_root
        # AcceptanceRunner may be pointed at an isolated test workspace; do not
        # consult the process-wide EAS_WORKSPACE_ROOT in that case.
        candidate = (root / raw).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"相对路径越界: {value}") from exc
        return candidate

    @staticmethod
    def _normalise_git_path(value: Any) -> str:
        text = str(value).replace("\\", "/").strip()
        while text.startswith("./"):
            text = text[2:]
        return text

    @staticmethod
    def _check(check_type: str, passed: bool, reason: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": check_type,
            "passed": bool(passed),
            "reason": reason,
            "evidence": evidence,
        }

    @staticmethod
    def _failed(check_type: str, reason: str) -> Dict[str, Any]:
        return AcceptanceRunner._check(check_type, False, reason, {})


__all__ = ["AcceptanceResult", "AcceptanceRunner"]
