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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
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
    allow_absolute_paths: bool
    limits: GrantLimits
    approval_required: bool
    issued_at: datetime
    expires_at: datetime
    _issued_monotonic: float = field(repr=False, default_factory=time.monotonic)
    used_tool_calls: int = 0
    used_model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    revoked: bool = False

    def usage(self) -> Dict[str, Any]:
        return {
            "tool_calls": self.used_tool_calls,
            "model_calls": self.used_model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
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
        allow_absolute_paths: bool = True,
        require_grant: bool = False,
    ) -> None:
        self.require_grant = bool(require_grant)
        # require_grant 是强制模式，即使调用方误配 enforce=false 也不能绕过。
        self.enforce = bool(enforce) or self.require_grant
        self.allow_absolute_paths = bool(allow_absolute_paths)
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
            allow_absolute_paths=config.get("allow_absolute_paths", True),
            require_grant=config.get("require_grant", False),
        )

    def issue_for_step(
        self,
        plan_id: str,
        step: Any,
        workspace_root: Optional[Path | str] = None,
        subject: str = "runtime",
        approval_required: bool = False,
        extra_allowed_tools: Optional[Iterable[str]] = None,
        limits: Optional[GrantLimits] = None,
    ) -> CapabilityGrant:
        """Issue a grant for one step; the returned object stays inside Runtime.

        extra_allowed_tools / limits 供 agent_tool 步骤使用（见
        agent_step_grant_params）：agent 需要在同一 Grant 下调用声明的内部
        工具，且单步可能产生多次工具调用；普通步骤传默认值即可。
        """
        root = Path(workspace_root).resolve() if workspace_root else self.workspace_root
        tool = str(getattr(step, "tool", "")).split(":", 1)[0]
        inputs = getattr(step, "inputs", {}) or {}
        # workspace 根目录是 Runtime 为命令/包/Git 工具补入的隐式 cwd/path，
        # 作为已授权资源加入；其余输入路径仍按精确资源匹配。
        resources = list(dict.fromkeys([
            str(root),
            *self._extract_resources(inputs, root, self.allow_absolute_paths),
        ]))
        hosts = self._extract_hosts(inputs)
        allowed_tools = {tool} if tool else set()
        for extra in extra_allowed_tools or ():
            name = str(extra).strip()
            if name:
                allowed_tools.add(name)
        issued = datetime.now(timezone.utc).replace(tzinfo=None)
        grant = CapabilityGrant(
            grant_id=uuid.uuid4().hex,
            plan_id=str(plan_id),
            step_id=str(getattr(step, "step_id", "")),
            subject=subject,
            allowed_tools=frozenset(allowed_tools),
            allowed_resources=tuple(resources),
            denied_resources=self.deny_resources,
            allowed_hosts=self.allowed_hosts,
            workspace_root=str(root),
            allow_absolute_paths=self.allow_absolute_paths,
            limits=limits or self.default_limits,
            approval_required=bool(approval_required),
            issued_at=issued,
            expires_at=issued + timedelta(seconds=self.default_ttl_seconds),
        )
        with self._lock:
            self._grants[grant.grant_id] = grant
        return grant

    def issue_grant(
        self,
        subject: str,
        allowed_tools: Iterable[str],
        limits: Optional[GrantLimits] = None,
        ttl_seconds: Optional[float] = None,
        allowed_hosts: Optional[Iterable[str]] = None,
    ) -> CapabilityGrant:
        """无 step 的通用签发（设计稿 §23.3）：用于设计期搜索等非步骤场景。

        - 资源为空（不授权文件写入）；URL 检查走 allowed_hosts
          （缺省继承全局配置，为空则一律拒绝——fail-closed）。
        - 生命周期同步骤 Grant：到期/撤销/预算耗尽即失效。
        """
        issued = datetime.now(timezone.utc).replace(tzinfo=None)
        ttl = self.default_ttl_seconds if ttl_seconds is None else max(0.01, float(ttl_seconds))
        hosts = (
            self.allowed_hosts
            if allowed_hosts is None
            else tuple(self._normalise_host(h) for h in allowed_hosts if str(h).strip())
        )
        grant = CapabilityGrant(
            grant_id=uuid.uuid4().hex,
            plan_id="",
            step_id="",
            subject=str(subject),
            allowed_tools=frozenset(str(t) for t in allowed_tools if str(t).strip()),
            allowed_resources=(),
            denied_resources=self.deny_resources,
            allowed_hosts=hosts,
            workspace_root=str(self.workspace_root),
            allow_absolute_paths=self.allow_absolute_paths,
            limits=limits or self.default_limits,
            approval_required=False,
            issued_at=issued,
            expires_at=issued + timedelta(seconds=ttl),
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
            if datetime.now(timezone.utc).replace(tzinfo=None) >= grant.expires_at:
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
        """Reserve a model-call slot before sending a request upstream."""
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None:
                return GrantDecision(False, "grant_not_found", "Grant 不存在", grant_id)
            if grant.revoked:
                return GrantDecision(False, "grant_revoked", "Grant 已撤销", grant_id, grant.usage())
            if datetime.now(timezone.utc).replace(tzinfo=None) >= grant.expires_at:
                return GrantDecision(False, "grant_expired", "Grant 已过期", grant_id, grant.usage())
            if time.monotonic() - grant._issued_monotonic > grant.limits.max_elapsed_seconds:
                return GrantDecision(False, "budget_exceeded", "Grant 时间预算已用尽", grant_id, grant.usage())
            if grant.used_model_calls >= grant.limits.max_model_calls:
                return GrantDecision(False, "budget_exceeded", "Grant 模型调用次数已用尽", grant_id, grant.usage())
            grant.used_model_calls += 1
            return GrantDecision(True, "allowed", "模型调用已获授权", grant_id, grant.usage())

    def record_model_usage(self, grant_id: str, usage: Optional[Dict[str, Any]] = None) -> GrantDecision:
        """Record provider-reported token usage after an authorized call."""
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None:
                return GrantDecision(False, "grant_not_found", "Grant 不存在", grant_id)
            usage = usage or {}
            try:
                grant.input_tokens += int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
                grant.output_tokens += int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
            except (TypeError, ValueError):
                return GrantDecision(False, "invalid_usage", "模型 usage 不是有效数字", grant_id, grant.usage())
            return GrantDecision(True, "recorded", "模型 usage 已记录", grant_id, grant.usage())

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
            resource = self._canonical_resource(
                str(value), Path(grant.workspace_root), grant.allow_absolute_paths
            )
            if resource is None:
                return f"资源路径越界: {field_name}={value!r}"
            relative = self._relative_resource(resource, Path(grant.workspace_root))
            if self._matches_denied_resource(relative, grant.denied_resources):
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
    def _extract_resources(
        cls,
        inputs: Dict[str, Any],
        root: Path,
        allow_absolute_paths: bool = True,
    ) -> List[str]:
        resources: List[str] = []
        for field_name in _PATH_FIELDS:
            value = inputs.get(field_name)
            if not isinstance(value, (str, Path)) or not str(value).strip():
                continue
            canonical = cls._canonical_resource(str(value), root, allow_absolute_paths)
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
    def _canonical_resource(value: str, root: Path, allow_absolute_paths: bool = True) -> Optional[str]:
        raw = Path(value)
        if raw.is_absolute():
            candidate = raw.resolve()
            if not allow_absolute_paths:
                try:
                    candidate.relative_to(root)
                except ValueError:
                    return None
            return str(candidate)
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

    @classmethod
    def _matches_denied_resource(cls, relative: str, patterns: Iterable[str]) -> bool:
        """Match both full paths and sensitive directory/file components."""
        relative = relative.replace("\\", "/").lstrip("./")
        parts = relative.split("/")
        for pattern in patterns:
            normalised = str(pattern).replace("\\", "/").lstrip("./")
            if cls._matches_any(relative, (normalised,)):
                return True
            # A bare .env pattern denies .env at any depth; .git/** denies
            # the .git component and everything below it.
            if "/" not in normalised and any(fnmatch.fnmatchcase(part, normalised) for part in parts):
                return True
            if normalised.endswith("/**"):
                prefix = normalised[:-3].rstrip("/")
                if prefix and prefix in parts:
                    return True
        return False

    @staticmethod
    def _normalise_host(host: str) -> str:
        return str(host).strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")


def agent_step_grant_params(step: Any, capability: Any = None) -> Tuple[List[str], Optional[GrantLimits]]:
    """根据步骤与 Capability spec 计算 Grant 签发参数（Runtime 专用）。

    - 仅 kind=agent_tool 的步骤允许声明额外工具与预算：agent 需要
      在同一 Grant 下调用 spec 声明的内部工具；普通工具步骤忽略
      inputs.allowed_tools，防止借道扩权。
    - agent 预算：1 次外层调度 + inputs.max_inner_tool_calls（默认 4）
      次内部调用；模型调用次数以 spec.budget_spec.max_model_calls 为准
      （单轮 agent = 1）。
    """
    spec = capability.spec() if capability is not None else None
    if spec is None or getattr(spec, "kind", "") != "agent_tool":
        return [], None
    inputs = getattr(step, "inputs", {}) or {}
    extra: List[str] = []
    for item in (inputs.get("allowed_tools") or []):
        name = str(item).strip()
        if name and name not in extra:
            extra.append(name)
    budget = spec.budget_spec or {}
    for item in budget.get("inner_tools", ()):
        name = str(item).strip()
        if name and name not in extra:
            extra.append(name)
    try:
        turns = max(1, int(inputs.get("max_turns", 1)))
    except (TypeError, ValueError):
        turns = 1
    # 预算按轮次扩展：每轮 1 次模型调用 + 读/写/自测三类内部调用（外层调度另计 1 次）。
    return extra, GrantLimits(max_tool_calls=1 + turns * 3, max_model_calls=turns)


__all__ = [
    "CapabilityGrant",
    "GrantDecision",
    "GrantLimits",
    "AuthorizationManager",
    "agent_step_grant_params",
]
