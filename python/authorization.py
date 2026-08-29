"""Runtime-scoped, ephemeral authorization for tool execution.

A grant is issued by the runtime for one work item and is never placed in the
model context.  The executor must present the grant id before each tool call;
this module performs the final policy decision and usage accounting.
"""

from __future__ import annotations

import fnmatch
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from logging_config import get_logger
from tools.workspace import resolve_workspace_root

logger = get_logger(__name__)

_PATH_FIELDS = (
    "path",
    "file_path",
    "cwd",
    "requirements_file",
    "package_json",
    "output",
    "save_to",
)


@dataclass(frozen=True)
class GrantLimits:
    """Hard limits copied into a grant at issuance time."""

    max_tool_calls: int = 1
    max_model_calls: int = 3
    max_elapsed_seconds: float = 600.0


@dataclass
class GrantDecision:
    """Result of a pre-action authorization decision."""

    allowed: bool
    reason: str
    message: str
    grant_id: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "message": self.message,
            "grant_id": self.grant_id,
            "usage": dict(self.usage),
        }


@dataclass
class CapabilityGrant:
    """An in-memory grant bound to exactly one plan step."""

    grant_id: str
    plan_id: str
    step_id: str
    subject: str
    allowed_tools: frozenset[str]
    allowed_resources: tuple[str, ...]
    denied_resources: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    workspace_root: str
    limits: GrantLimits
    approval_required: bool
    issued_at: datetime
    expires_at: datetime
    _issued_monotonic: float = field(repr=False, default_factory=time.monotonic)
    used_tool_calls: int = 0
    used_model_calls: int = 0
    revoked: bool = False

    def usage(self) -> Dict[str, Any]:
        return {
            "tool_calls": self.used_tool_calls,
            "model_calls": self.used_model_calls,
            "max_tool_calls": self.limits.max_tool_calls,
            "max_model_calls": self.limits.max_model_calls,
            "elapsed_seconds": max(0.0, time.monotonic() - self._issued_monotonic),
            "max_elapsed_seconds": self.limits.max_elapsed_seconds,
            "expires_at": self.expires_at.isoformat(),
        }


class AuthorizationManager:
    """Issue and enforce short-lived grants for runtime tool calls."""

    def __init__(
        self,
        *,
        enforce: bool = True,
        default_ttl_seconds: float = 600.0,
        default_max_tool_calls: int = 1,
        default_max_model_calls: int = 3,
        default_max_elapsed_seconds: float = 600.0,
        deny_resources: Optional[Iterable[str]] = None,
        allowed_hosts: Optional[Iterable[str]] = None,
        workspace_root: Optional[Path | str] = None,
    ) -> None:
        self.enforce = bool(enforce)
        self.default_ttl_seconds = max(0.01, float(default_ttl_seconds))
        self.default_limits = GrantLimits(
            max_tool_calls=max(1, int(default_max_tool_calls)),
            max_model_calls=max(0, int(default_max_model_calls)),
            max_elapsed_seconds=max(0.01, float(default_max_elapsed_seconds)),
        )
        self.deny_resources = tuple(str(p) for p in (deny_resources or (".env", ".env.*", ".git/**")))
        self.allowed_hosts = tuple(self._normalise_host(h) for h in (allowed_hosts or ()) if str(h).strip())
        self.workspace_root = (Path(workspace_root).resolve() if workspace_root else resolve_workspace_root())
        self._grants: Dict[str, CapabilityGrant] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "AuthorizationManager":
        config = config or {}
        return cls(
            enforce=config.get("enforce", True),
            default_ttl_seconds=config.get("default_ttl_seconds", 600),
            default_max_tool_calls=config.get("default_max_tool_calls", 1),
            default_max_model_calls=config.get("default_max_model_calls", 3),
            default_max_elapsed_seconds=config.get("default_max_elapsed_seconds", 600),
            deny_resources=config.get("deny_resources"),
            allowed_hosts=config.get("allowed_hosts"),
            workspace_root=config.get("workspace_root"),
        )

    def issue_for_step(
        self,
        plan_id: str,
        step: Any,
        workspace_root: Optional[Path | str] = None,
        subject: str = "runtime",
        approval_required: bool = False,
    ) -> CapabilityGrant:
        """Issue a grant for one step; the returned object stays inside Runtime."""
        root = Path(workspace_root).resolve() if workspace_root else self.workspace_root
        tool = str(getattr(step, "tool", "")).split(":", 1)[0]
        inputs = getattr(step, "inputs", {}) or {}
        resources = self._extract_resources(inputs, root)
        hosts = self._extract_hosts(inputs)
        issued = datetime.utcnow()
        grant = CapabilityGrant(
            grant_id=uuid.uuid4().hex,
            plan_id=str(plan_id),
            step_id=str(getattr(step, "step_id", "")),
            subject=subject,
            allowed_tools=frozenset({tool}) if tool else frozenset(),
            allowed_resources=tuple(resources),
            denied_resources=self.deny_resources,
            allowed_hosts=self.allowed_hosts,
            workspace_root=str(root),
            limits=self.default_limits,
            approval_required=bool(approval_required),
            issued_at=issued,
            expires_at=issued + timedelta(seconds=self.default_ttl_seconds),
        )
        with self._lock:
            self._grants[grant.grant_id] = grant
        return grant

    def authorize(
        self,
        grant_id: Optional[str],
        tool_id: str,
        instruction: str,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> GrantDecision:
        """Authorize and reserve one tool-call slot before side effects occur."""
        if not self.enforce:
            return GrantDecision(True, "enforcement_disabled", "授权检查已关闭", grant_id)
        if not grant_id:
            return GrantDecision(False, "grant_required", "Runtime 执行必须携带临时 Grant")

        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None:
                return GrantDecision(False, "grant_not_found", "Grant 不存在", grant_id)
            if grant.revoked:
                return GrantDecision(False, "grant_revoked", "Grant 已撤销", grant_id, grant.usage())
            if datetime.utcnow() >= grant.expires_at:
                return GrantDecision(False, "grant_expired", "Grant 已过期", grant_id, grant.usage())
            if time.monotonic() - grant._issued_monotonic > grant.limits.max_elapsed_seconds:
                return GrantDecision(False, "budget_exceeded", "Grant 时间预算已用尽", grant_id, grant.usage())
            if tool_id not in grant.allowed_tools:
                return GrantDecision(False, "tool_not_granted", f"工具未获授权: {tool_id}", grant_id, grant.usage())
            if grant.used_tool_calls >= grant.limits.max_tool_calls:
                return GrantDecision(False, "budget_exceeded", "Grant 工具调用次数已用尽", grant_id, grant.usage())

            resource_reason = self._check_resources(grant, inputs or {})
            if resource_reason:
                return GrantDecision(False, "resource_denied", resource_reason, grant_id, grant.usage())

            grant.used_tool_calls += 1
            return GrantDecision(True, "allowed", "工具调用已获授权", grant_id, grant.usage())

    def record_model_call(self, grant_id: str) -> GrantDecision:
        """Reserve a model-call slot for future AgentSession integration."""
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None:
                return GrantDecision(False, "grant_not_found", "Grant 不存在", grant_id)
            if grant.revoked:
                return GrantDecision(False, "grant_revoked", "Grant 已撤销", grant_id, grant.usage())
            if datetime.utcnow() >= grant.expires_at:
                return GrantDecision(False, "grant_expired", "Grant 已过期", grant_id, grant.usage())
            if grant.used_model_calls >= grant.limits.max_model_calls:
                return GrantDecision(False, "budget_exceeded", "Grant 模型调用次数已用尽", grant_id, grant.usage())
            grant.used_model_calls += 1
            return GrantDecision(True, "allowed", "模型调用已获授权", grant_id, grant.usage())

    def revoke(self, grant_id: str) -> bool:
        """Revoke a grant; subsequent calls fail even before expiry."""
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None or grant.revoked:
                return False
            grant.revoked = True
            return True

    def revoke_all_for_plan(self, plan_id: str) -> int:
        """Release grants if a plan terminates before the step cleanup path."""
        with self._lock:
            count = 0
            for grant in self._grants.values():
                if grant.plan_id == str(plan_id) and not grant.revoked:
                    grant.revoked = True
                    count += 1
            return count

    def get(self, grant_id: str) -> Optional[CapabilityGrant]:
        with self._lock:
            return self._grants.get(grant_id)

    def active_for_plan(self, plan_id: str) -> List[str]:
        with self._lock:
            return [g.grant_id for g in self._grants.values() if g.plan_id == str(plan_id) and not g.revoked]

    def _check_resources(self, grant: CapabilityGrant, inputs: Dict[str, Any]) -> Optional[str]:
        for field_name in _PATH_FIELDS:
            value = inputs.get(field_name)
            if not isinstance(value, (str, Path)) or not str(value).strip():
                continue
            resource = self._canonical_resource(str(value), Path(grant.workspace_root))
            if resource is None:
                return f"资源路径越界: {field_name}={value!r}"
            relative = self._relative_resource(resource, Path(grant.workspace_root))
            if self._matches_any(relative, grant.denied_resources) or self._matches_any(Path(relative).name, grant.denied_resources):
                return f"资源被拒绝: {relative}"
            if grant.allowed_resources and resource not in grant.allowed_resources:
                return f"资源不在本步骤授权范围: {relative}"

        url = inputs.get("url")
        if isinstance(url, str) and url.strip():
            host = self._normalise_host(urlsplit(url).hostname or "")
            if not host:
                return "URL 缺少合法主机名"
            if not grant.allowed_hosts or not any(fnmatch.fnmatchcase(host, pattern) for pattern in grant.allowed_hosts):
                return f"网络主机未获授权: {host}"
        return None

    @classmethod
    def _extract_resources(cls, inputs: Dict[str, Any], root: Path) -> List[str]:
        resources: List[str] = []
        for field_name in _PATH_FIELDS:
            value = inputs.get(field_name)
            if not isinstance(value, (str, Path)) or not str(value).strip():
                continue
            canonical = cls._canonical_resource(str(value), root)
            if canonical is not None:
                resources.append(canonical)
        return list(dict.fromkeys(resources))

    @staticmethod
    def _extract_hosts(inputs: Dict[str, Any]) -> List[str]:
        url = inputs.get("url")
        if not isinstance(url, str):
            return []
        host = AuthorizationManager._normalise_host(urlsplit(url).hostname or "")
        return [host] if host else []

    @staticmethod
    def _canonical_resource(value: str, root: Path) -> Optional[str]:
        raw = Path(value)
        if raw.is_absolute():
            return str(raw.resolve())
        candidate = (root / raw).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return str(candidate)

    @staticmethod
    def _relative_resource(resource: str, root: Path) -> str:
        path = Path(resource)
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.as_posix()

    @staticmethod
    def _matches_any(value: str, patterns: Iterable[str]) -> bool:
        value = value.replace("\\", "/")
        return any(
            fnmatch.fnmatchcase(value, str(pattern).replace("\\", "/"))
            or fnmatch.fnmatchcase(value, str(pattern).replace("\\", "/").lstrip("./"))
            for pattern in patterns
        )

    @staticmethod
    def _normalise_host(host: str) -> str:
        return str(host).strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")


__all__ = ["CapabilityGrant", "GrantDecision", "GrantLimits", "AuthorizationManager"]
