"""Pure WorkGraph validation and dependency helpers.

The first runtime increment keeps execution serial but makes dependencies
explicit and deterministic.  This module intentionally has no DB or asyncio
side effects so it can also be reused by future runtimes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


SUCCESSFUL_STATUSES = frozenset({"Success", "Skipped"})
FAILED_STATUSES = frozenset({"Failed", "RolledBack"})


@dataclass(frozen=True)
class GraphValidation:
    valid: bool
    error: str | None = None


def normalize_dependencies(value: Any) -> list[str]:
    """Return a de-duplicated dependency list, preserving declaration order."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("depends_on must be an array of step IDs")
    result: list[str] = []
    for item in value:
        if not isinstance(item, (str, int)) or not str(item).strip():
            raise ValueError("depends_on entries must be non-empty step IDs")
        item = str(item).strip()
        if item not in result:
            result.append(item)
    return result


def _step_id(step: Mapping[str, Any]) -> str:
    value = step.get("step_id", step.get("id"))
    return str(value).strip() if value is not None else ""


def _dependencies(step: Mapping[str, Any]) -> list[str]:
    value = step.get("depends_on")
    if value is None:
        value = step.get("dependencies")
    return normalize_dependencies(value)


def validate_graph(steps: Sequence[Mapping[str, Any]]) -> GraphValidation:
    """Validate IDs, dependency references, and cycles for one plan."""
    ids: list[str] = []
    for index, step in enumerate(steps):
        step_id = _step_id(step)
        if not step_id:
            return GraphValidation(False, f"step[{index}] 缺少 step_id")
        if step_id in ids:
            return GraphValidation(False, f"step[{index}] step_id 重复: {step_id}")
        ids.append(step_id)

    known = set(ids)
    graph: dict[str, list[str]] = {}
    for index, step in enumerate(steps):
        step_id = ids[index]
        try:
            deps = _dependencies(step)
        except ValueError as exc:
            return GraphValidation(False, f"step[{index}] {exc}")
        if step_id in deps:
            return GraphValidation(False, f"step[{index}] 不能依赖自身: {step_id}")
        missing = [dep for dep in deps if dep not in known]
        if missing:
            return GraphValidation(False, f"step[{index}] 依赖不存在的步骤: {missing}")
        graph[step_id] = deps

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> str | None:
        if node in visiting:
            return node
        if node in visited:
            return None
        visiting.add(node)
        for dep in graph[node]:
            cycle = visit(dep)
            if cycle is not None:
                return cycle
        visiting.remove(node)
        visited.add(node)
        return None

    for node in ids:
        cycle = visit(node)
        if cycle is not None:
            return GraphValidation(False, f"检测到循环依赖，涉及步骤: {cycle}")
    return GraphValidation(True)


def dependency_statuses(
    step: Mapping[str, Any],
    statuses: Mapping[str, str],
) -> tuple[bool, bool, list[str]]:
    """Return (ready, failed, blocking dependency IDs)."""
    deps = _dependencies(step)
    if not deps:
        return True, False, []
    blocking = [dep for dep in deps if statuses.get(dep) not in SUCCESSFUL_STATUSES]
    failed = [dep for dep in blocking if statuses.get(dep) in FAILED_STATUSES]
    # A dependency ID may refer to a source step that is represented by a
    # rollback clone; only failed/rolled-back states are permanent blockers.
    return not blocking, bool(failed), blocking


def is_step_ready(step: Mapping[str, Any], statuses: Mapping[str, str]) -> bool:
    return dependency_statuses(step, statuses)[0]


def has_unresolvable_dependencies(
    steps: Iterable[Mapping[str, Any]], statuses: Mapping[str, str]
) -> bool:
    """True when pending steps remain but none can ever become ready."""
    pending = [step for step in steps if str(step.get("status")) == "Pending"]
    if not pending:
        return False
    return not any(is_step_ready(step, statuses) for step in pending)
