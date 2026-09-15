"""Pure preparation of an atomic replan; no database or network side effects."""
from __future__ import annotations

import json
from copy import deepcopy
from uuid import uuid4

from domain.work_graph import normalize_dependencies, validate_graph


class ReplanValidationError(ValueError):
    """The failed-plan snapshot cannot safely accept the proposed repair graph."""


def replan_anchor(snapshot):
    plan = snapshot["plan"]
    branch = int(plan.get("current_branch") or 1)
    active = sorted(
        (row for row in snapshot["steps"]
         if not row["is_obsolete"] and int(row["branch_version"] or 1) == branch),
        key=lambda row: (row["step_order"], row["step_id"]),
    )
    if plan["status"] != "Failed":
        raise ReplanValidationError("只能对 Failed 计划做重规划")
    anchor_id = plan.get("resume_from_step_id")
    if anchor_id:
        anchor = next((row for row in active if row["step_id"] == anchor_id), None)
    else:
        anchor = next((row for row in active if row["status"] == "Failed"), None)
    if anchor is None or anchor["status"] != "Failed":
        raise ReplanValidationError("恢复锚点不是当前分支的活动失败步骤")
    return anchor, active


def _reset_step(row):
    row.update(status="Pending", outputs=None, executed_at=None, retry_count=0,
               state_snapshot=None, compressed_context=None, duration_ms=None,
               failure_reason=None)


def _validate_effective_graph(rows, branch):
    """Validate the same parent-ID aliases used by Database.select_next_batch."""
    effective = {}
    ordered = sorted(rows, key=lambda r: (-int(r["is_obsolete"]),
                                         int(r["branch_version"]), r["step_order"], r["step_id"]))
    for row in ordered:
        effective[row["step_id"]] = row
        if row.get("parent_step_id") and not row["is_obsolete"]:
            effective[row["parent_step_id"]] = row
    active = [r for r in rows if not r["is_obsolete"] and r["branch_version"] == branch]
    if not active:
        raise ReplanValidationError("重规划不能生成空活动分支")
    active_ids = {r["step_id"] for r in active}
    graph = {}
    stack = list(active)
    while stack:
        row = stack.pop()
        sid = row["step_id"]
        if sid in graph:
            continue
        # A historical prerequisite will not be scheduled in this branch.
        if sid not in active_ids and row["status"] not in {"Success", "Skipped"}:
            raise ReplanValidationError("重规划依赖了非成功的历史步骤")
        deps = []
        for dep in normalize_dependencies(row.get("depends_on")):
            resolved = effective.get(dep)
            if resolved is None:
                raise ReplanValidationError(f"重规划依赖不存在的步骤: {dep}")
            deps.append(resolved["step_id"])
            stack.append(resolved)
        graph[sid] = {"step_id": sid, "depends_on": deps}
    result = validate_graph(list(graph.values()))
    if not result.valid:
        raise ReplanValidationError(result.error)


def prepare_replan(snapshot, repair_steps):
    """Return inserted/updated rows and one event, leaving snapshot untouched.

    A first-step repair stays on the original branch; repairs after a prefix
    clone the suffix to a new branch. Every write is committed together later.
    """
    anchor, active = replan_anchor(snapshot)
    if not isinstance(repair_steps, list) or not repair_steps:
        raise ReplanValidationError("修复步骤不能为空")
    for repair in repair_steps:
        if (not isinstance(repair, dict) or not repair.get("tool")
                or not isinstance(repair.get("inputs"), dict)
                or not isinstance(repair.get("acceptance"), list)
                or not repair["acceptance"]):
            raise ReplanValidationError("修复步骤缺少工具、输入或验收规则")
    rows = deepcopy(snapshot["steps"])
    row_map = {r["step_id"]: r for r in rows}
    branch = int(snapshot["plan"].get("current_branch") or 1)
    predecessors = [r for r in active if r["step_order"] < anchor["step_order"]]
    predecessor = predecessors[-1] if predecessors else None
    new_branch = branch + 1 if predecessor else branch
    if predecessor and any(int(r["branch_version"]) >= new_branch for r in rows):
        raise ReplanValidationError("目标分支已存在，请先核对历史重规划状态")
    explicit = any(normalize_dependencies(r.get("depends_on")) for r in active)
    inserted, updated, obsolete = [], {}, []
    if predecessor:
        for source in active:
            if source["step_order"] <= predecessor["step_order"]:
                continue
            clone = deepcopy(source)
            clone.update(step_id=str(uuid4()), parent_step_id=source["step_id"],
                         branch_version=new_branch, is_obsolete=0)
            _reset_step(clone)
            inserted.append(clone)
            rows.append(clone)
            row_map[source["step_id"]]["is_obsolete"] = 1
            obsolete.append(source["step_id"])
        suffix = list(inserted)
        retry = next(r for r in suffix if r["parent_step_id"] == anchor["step_id"])
    else:
        retry = row_map[anchor["step_id"]]
        _reset_step(retry)
        updated[retry["step_id"]] = retry
        suffix = [retry] + [row_map[r["step_id"]] for r in active
                            if r["step_order"] > anchor["step_order"]
                            and r["status"] in {"Pending", "Retry"}]

    prev = predecessor["step_id"] if predecessor else None
    repair_ids = []
    max_order = max(r["step_order"] for r in rows)
    for index, repair in enumerate(repair_steps):
        row = deepcopy(anchor)
        _reset_step(row)
        row.update(
            step_id=str(uuid4()), parent_step_id=None, branch_version=new_branch,
            is_obsolete=0, step_order=max_order + index + 1,
            description=str(repair.get("description") or "修复步骤"), tool=repair["tool"],
            inputs=json.dumps(repair["inputs"], ensure_ascii=False),
            acceptance_json=json.dumps(repair["acceptance"], ensure_ascii=False),
            check_criteria=str(repair.get("check_criteria") or "手动检查"),
            depends_on=json.dumps([prev] if prev else []),
        )
        inserted.append(row)
        rows.append(row)
        repair_ids.append(row["step_id"])
        prev = row["step_id"]
    deps = normalize_dependencies(anchor.get("depends_on")) + [repair_ids[-1]]
    if not explicit:
        if predecessor:
            deps.insert(0, predecessor["step_id"])
        chain_prev = retry["step_id"]
        for follower in suffix[1:]:
            follower["depends_on"] = json.dumps([chain_prev])
            if not predecessor:
                updated[follower["step_id"]] = follower
            chain_prev = follower["step_id"]
    retry["depends_on"] = json.dumps(normalize_dependencies(deps))
    _validate_effective_graph(rows, new_branch)
    return {
        "new_branch": new_branch, "anchor": anchor["step_id"],
        "repair_steps": len(repair_ids), "inserted": inserted,
        "updated": list(updated.values()), "obsolete": obsolete,
    }
