"""动态重规划路由（自 server.py 拆分；行为不变）。"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional
from typing import List
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.auth import require_token
from api.shared import state, broadcast_plan_update, broadcast_log, _require_write
from designer import GenerationError
from models import PlanStatus, StepStatus


from logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.post("/api/plan/{plan_id}/replan", dependencies=[Depends(require_token)])
async def replan_plan(plan_id: str):
    """对 Failed 计划做局部重规划。

    语义（依赖驱动，不重排既有 order）：
    - 锚点 = resume_from_step_id 或第一个活动失败步骤 F；
    - 有前驱 P：回滚到 P（F 及之后的步骤克隆到新分支，Pending），
      修复步骤 R1..Rn 依赖链挂在 P 之后；F 的克隆依赖追加 Rn（先修复再重跑）；
    - 无前驱（F 即首步）：不回滚，F 置回 Pending，R 链挂在 F 之前（F 依赖追加 Rn）；
    - 修复步骤由 Designer 基于失败上下文生成，复用计划契约校验，fail-closed；
    - 旧线性计划在追加依赖后显式化克隆链顺序，避免隐式链失效导致乱序；
    - 成功后计划转 Pending（新分支），由用户重新 /execute。
    """
    try:
        plan = state.database.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="执行计划不存在")
        current_status = state.database.get_plan_status(plan_id)
        if current_status != "Failed":
            raise HTTPException(
                status_code=409,
                detail=f"只能对 Failed 计划做重规划，当前状态: {current_status}",
            )

        branch_version = getattr(plan, "current_branch", 1) or 1
        all_steps = state.database.get_steps(plan_id)
        active_rows = [
            r for r in all_steps
            if not r.get("is_obsolete") and int(r.get("branch_version") or 1) == branch_version
        ]
        failed_rows = [r for r in active_rows if r["status"] == "Failed"]
        anchor_id = (
            str(getattr(plan, "resume_from_step_id", None))
            if getattr(plan, "resume_from_step_id", None)
            else (str(failed_rows[0]["step_id"]) if failed_rows else None)
        )
        if not anchor_id:
            raise HTTPException(status_code=409, detail="计划中没有失败步骤可重规划")
        anchor = next(r for r in all_steps if str(r["step_id"]) == anchor_id)
        anchor_order = int(anchor["step_order"])

        same_branch = [
            r for r in active_rows
            if int(r.get("branch_version") or 1) == branch_version
        ]
        predecessors = [r for r in same_branch if int(r["step_order"]) < anchor_order]
        predecessor = max(predecessors, key=lambda r: int(r["step_order"])) if predecessors else None

        new_branch = branch_version + 1
        if predecessor is not None:
            target = str(predecessor["step_id"])
            if not state.database.clone_steps_from(plan_id, target, new_branch):
                raise HTTPException(status_code=500, detail="克隆失败步骤链失败")
            state.database.mark_steps_obsolete(plan_id, target, branch_version)
            clone_rows = [
                r for r in state.database.get_steps(plan_id)
                if int(r.get("branch_version") or 1) == new_branch and not r.get("is_obsolete")
            ]
            clone_rows.sort(key=lambda r: int(r["step_order"]))
            anchor_clone = next(
                (r for r in clone_rows if r.get("parent_step_id") == anchor_id), None
            )
        else:
            target = anchor_id
            clone_rows = []
            anchor_clone = anchor  # 无前驱：F 原地置回 Pending

        has_explicit_graph = any(
            str(r.get("depends_on") or "").strip() not in ("", "[]", "null")
            for r in same_branch
        )

        # Designer 生成修复步骤（契约校验 fail-closed）
        _design = state.database.get_design(str(plan.design_id))
        anchor_deps_raw = json.loads(anchor["depends_on"]) if anchor.get("depends_on") else []
        failed_inputs = anchor.get("inputs")
        if isinstance(failed_inputs, str):
            try:
                failed_inputs = json.loads(failed_inputs)
            except (ValueError, TypeError):
                failed_inputs = {}
        repair_steps = await state.designer.generate_repair_steps(
            failed_step={
                "description": anchor.get("description", ""),
                "tool": anchor.get("tool", ""),
                "inputs": failed_inputs or {},
            },
            failure_reason=anchor.get("failure_reason") or "未知原因",
            user_request=str(_design.user_request) if _design else "",
        )

        # ===== 在内存中构造最终依赖计划，再统一写库 =====
        # R 链：R1 挂在其前置（P 或就绪即跑）之后，Ri 依赖 R(i-1)；
        # F 重跑点依赖追加 Rn（先修复再重跑）；
        # 旧线性计划追加依赖后隐式链失效 → 克隆链显式化（按 order 顺序链接）。
        max_order = max((int(r["step_order"]) for r in all_steps), default=0)
        segment_id = str(anchor.get("segment_id") or uuid4())
        p_id = str(predecessor["step_id"]) if predecessor is not None else None
        repair_ids: List[str] = []
        prev_id = p_id
        repair_branch = new_branch if predecessor is not None else branch_version
        for i, rs in enumerate(repair_steps):
            sid = str(uuid4())
            deps = [prev_id] if prev_id else []
            ok = state.database.insert_repair_step(
                plan_id=plan_id,
                branch_version=repair_branch,
                segment_id=segment_id,
                step_id=sid,
                step_order=max_order + 1 + i,
                description=str(rs.get("description", "修复步骤")),
                tool=str(rs.get("tool", "manual_tool")),
                inputs=rs.get("inputs") or {},
                acceptance=rs.get("acceptance") or [],
                depends_on=deps,
            )
            if not ok:
                raise HTTPException(status_code=500, detail="修复步骤写入失败")
            repair_ids.append(sid)
            prev_id = sid
        last_repair_id = repair_ids[-1] if repair_ids else None
        f_deps = list(anchor_deps_raw) + ([last_repair_id] if last_repair_id else [])

        if predecessor is not None:
            anchor_clone_id = str(anchor_clone["step_id"]) if anchor_clone else None
            if not has_explicit_graph:
                # legacy 链显式化：F' = [P] + 原deps + [Rn]；其余克隆行按 order 链接
                chain_prev = None
                for row in clone_rows:
                    rid = str(row["step_id"])
                    if rid == anchor_clone_id:
                        deps = ([p_id] if p_id else []) + f_deps
                    else:
                        deps = [chain_prev] if chain_prev else ([p_id] if p_id else [])
                    state.database.update_step_dependencies(rid, deps)
                    chain_prev = rid
            else:
                # 显式图：F' 追加 Rn；其余克隆行保留原依赖（父映射解析）
                if anchor_clone_id:
                    state.database.update_step_dependencies(anchor_clone_id, f_deps)
        else:
            # 无前驱（F 即首步）：F 原地置回 Pending，依赖追加 Rn；
            # legacy 隐式链显式化：F 之后的同分支未完成步骤按 order 链接。
            state.database.update_step_dependencies(anchor_id, f_deps)
            state.database.update_step(anchor_id, "Pending")
            if not has_explicit_graph:
                followers = [
                    r for r in same_branch
                    if int(r["step_order"]) > anchor_order
                    and r["status"] in (StepStatus.PENDING.value, StepStatus.RETRY.value)
                ]
                followers.sort(key=lambda r: int(r["step_order"]))
                chain_prev = anchor_id
                for row in followers:
                    state.database.update_step_dependencies(str(row["step_id"]), [chain_prev])
                    chain_prev = str(row["step_id"])

        state.database.clear_plan_checkpoint(plan_id)
        _require_write(
            state.database.update_plan_state_machine(
                plan_id, PlanStatus.PENDING.value, current_branch=new_branch,
            ),
            f"计划 {plan_id} 状态 → Pending（重规划，新分支 {new_branch}）",
        )
        state.database.add_step_event(
            anchor_id, plan_id, "replanned",
            {"repair_steps": len(repair_steps), "new_branch": new_branch,
             "anchor": anchor_id, "strategy": "repair_before_retry"},
            branch_version=new_branch,
        )
        await broadcast_plan_update(plan_id)
        await broadcast_log(
            f"[REPLAN] 计划 {plan_id} 重规划完成：插入 {len(repair_steps)} 个修复步骤（分支 {new_branch}），"
            "可重新执行", "warning",
        )
        return {
            "success": True,
            "new_branch": new_branch,
            "repair_steps": len(repair_steps),
            "anchor": anchor_id,
        }
    except HTTPException:
        raise
    except GenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("重规划失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))



