"""Pure dependency frontier for checkpoint recovery; no IO or state mutation."""
from __future__ import annotations

from domain.work_graph import normalize_dependencies

_SUCCESS = {"Success", "Skipped"}
_RETRYABLE_TARGET = {"Pending", "Failed", "Retry"}


class _Blocked(ValueError):
    pass


def checkpoint_frontier(rows, branch_version, checkpoint_id):
    """Select only ready prerequisites, or the ready checkpoint itself.

    Failed/Retry may be retried only when explicitly named as the checkpoint.
    Historical successful prerequisites and the scheduler's parent-ID aliases
    remain valid. A finished checkpoint is consumed without rerunning it.
    """
    checkpoint_id = str(checkpoint_id)
    branch_version = int(branch_version)

    def result(candidates=(), reason=None, consumed=False, handled=True):
        return {"candidates": list(candidates), "blocked_reason": reason,
                "blocked_step_ids": [checkpoint_id] if reason else [],
                "checkpoint_consumed": consumed, "handled": handled}

    def active(row):
        return not row.get("is_obsolete") and int(row.get("branch_version") or 1) == branch_version

    target = next((r for r in rows if str(r["step_id"]) == checkpoint_id), None)
    if target is None or not active(target):
        return result(reason="恢复检查点不存在、已废弃或不属于当前分支")
    if target["status"] in _SUCCESS:
        return result(consumed=True, handled=False)
    if target["status"] not in _RETRYABLE_TARGET:
        return result(reason=f"恢复检查点状态不可执行: {target['status']}")

    # Match Database.select_next_batch and replan's one-level parent-ID aliases.
    effective = {}
    for row in sorted(rows, key=lambda r: (-int(r.get("is_obsolete") or 0),
                                         int(r.get("branch_version") or 1),
                                         r["step_order"], str(r["step_id"]))):
        effective[str(row["step_id"])] = row
        if row.get("parent_step_id") and not row.get("is_obsolete"):
            effective[str(row["parent_step_id"])] = row

    visiting, visited, candidates = set(), set(), []

    def visit(row):
        sid = str(row["step_id"])
        if sid in visiting:
            raise _Blocked(f"恢复检查点依赖存在循环: {sid}")
        if sid in visited:
            return
        visiting.add(sid)
        ready = True
        for dep in normalize_dependencies(row.get("depends_on")):
            prerequisite = effective.get(dep)
            if prerequisite is None:
                raise _Blocked(f"恢复检查点依赖不存在: {dep}")
            dep_id = str(prerequisite["step_id"])
            if dep_id in visiting:
                raise _Blocked(f"恢复检查点依赖存在循环: {dep_id}")
            if int(prerequisite.get("branch_version") or 1) > branch_version:
                raise _Blocked(f"恢复检查点依赖了未来分支步骤: {dep}")
            if prerequisite["status"] in _SUCCESS:
                continue
            ready = False
            if prerequisite["status"] in {"Failed", "RolledBack"}:
                raise _Blocked(f"依赖步骤失败，无法恢复: {dep}")
            if not active(prerequisite):
                raise _Blocked(f"恢复检查点依赖了未成功的历史步骤: {dep}")
            if prerequisite["status"] != "Pending":
                raise _Blocked(f"恢复检查点依赖尚不可执行: {dep} ({prerequisite['status']})")
            visit(prerequisite)
        visiting.remove(sid)
        visited.add(sid)
        if ready:
            candidates.append(row)

    try:
        visit(target)
    except ValueError as exc:
        return result(reason=str(exc))
    candidates.sort(key=lambda r: (r["step_order"], str(r["step_id"])))
    if not candidates:
        return result(reason="恢复检查点没有已就绪的前置步骤")
    return result(candidates, consumed=any(str(r["step_id"]) == checkpoint_id for r in candidates))
