"""后台总循环（自 server.py 拆分；行为不变）。

本模块包含 execute_plan_background —— 计划执行的状态机入口：
- 原子认领计划并签发 Run；
- 依赖感知 + 资源冲突过滤的批取步；
- 有界并发执行批内步骤（任一失败即停）；
- 收尾：收敛计划状态 / 项目记忆 / 用量上下文 / 审批与 Grant 清理 / Run 收尾。

所有符号仍可从 api.server 导入（见 server.py 末尾的 re-export）。
"""
from __future__ import annotations

import asyncio
import traceback

from api.shared import (
    broadcast_log,
    broadcast_plan_update,
    state,
    _require_write,
)
from api.execution_ownership import (
    ExecutionBusy, reserve_execution, release_execution, owns_reservation,
)
from api.runtime.artifacts import _generate_final_report
from api.runtime.step_execution import _execute_step_to_completion
from models import AttemptStatus, PlanStatus, RunStatus, StepStatus

from logging_config import get_logger

logger = get_logger(__name__)


async def execute_plan_background(
    plan, resume: bool = False, run_id: str = None, owner_token: str = None,
    reservation_token: str = None,
):
    """后台执行计划

    resume=True 时：从断点继续，用于 /resume 或回滚后的 /execute。
    状态机模式：每次只取下一个可运行 step，执行后写 event 与压缩上下文。

    Run 生命周期（保持旧计划状态机行为不变）：
    - 认领即建 Run：start_execution_run 原子完成「条件 UPDATE plans→Running + INSERT execution_run」，
      语义等价于旧的 claim_plan_status，认领失败不产生 Run，也不覆盖他人状态；
    - execute 从 Pending/Approved/Failed/Completed/RolledBack 认领，resume 只从 Paused 认领；
    - 执行期间周期心跳 Run；结束时按计划终态收尾 Run（Completed/Failed/Paused），
      带 owner_token 的 finish 使旧任务无法覆盖新任务持有的 Run。
    - 每个步骤的每次工具调用落一条 Attempt（create/finish），失败重试轮产生新 Attempt。
    """
    def debug_log(msg):
        logger.debug(msg)

    print(f"[BG] STARTED plan={plan.plan_id} resume={resume}", flush=True)

    attempt_id = None
    _bound_worktree = None
    _workspace_override_token = None
    _reservation_owned = False
    _run_claimed = False
    _usage_context_bound = False
    current_step_id = None
    resume_from_step_id = None
    _scheduling_failed = False

    def still_owns_run():
        if not (_run_claimed and _reservation_owned
                and owns_reservation(state, str(plan.plan_id), reservation_token)):
            return False
        try:
            record = state.database.get_execution_run(run_id)
            latest = state.database.list_execution_runs(str(plan.plan_id), limit=1)
            return bool(record and record["plan_id"] == str(plan.plan_id)
                        and record["owner_token"] == owner_token
                        and record["status"] == RunStatus.RUNNING.value
                        and latest and latest[0]["run_id"] == run_id)
        except Exception as exc:
            logger.warning("无法确认 Run 所有权，跳过共享状态清理: {}", exc)
            return False

    try:
        if reservation_token is None:
            isolated = state.database.get_active_worktree(str(plan.plan_id)) is not None
            reservation_token = await reserve_execution(state, str(plan.plan_id), isolated=isolated)
        elif not owns_reservation(state, str(plan.plan_id), reservation_token):
            return
        _reservation_owned = True
        debug_log(f"execute_plan_background STARTED for plan {plan.plan_id} (resume={resume})")

        # 原子认领计划并签发 Run（语义等价旧 claim_plan_status，见模块 docstring）。
        # - execute：从除 Paused 外的全部可执行状态认领——计划若在任务排队期间被暂停，
        #   认领必然失败，执行绝不会把 Paused 翻成 Running（暂停被无声吞掉的竞态）。
        #   Completed/RolledBack 在列以保持「重跑已完成计划」「回滚后重跑」可用。
        # - resume：只从 Paused 认领。
        if not run_id:
            if resume:
                claim_from = (PlanStatus.PAUSED.value,)
            else:
                claim_from = (
                    PlanStatus.PENDING.value,
                    PlanStatus.APPROVED.value,
                    PlanStatus.FAILED.value,
                    PlanStatus.COMPLETED.value,
                    PlanStatus.ROLLED_BACK.value,
                )
            _run = state.database.start_execution_run(
                str(plan.plan_id),
                from_statuses=claim_from,
                trigger="resume" if resume else "execute",
                branch_version=getattr(plan, 'current_branch', 1) or 1,
            )
            if _run:
                run_id = str(_run["run_id"])
                owner_token = str(_run["owner_token"])
                _run_claimed = True
        if run_id and not owner_token:
            owner_token = run_id
        if not run_id:
            # 认领失败 = 状态已被并发操作改变（被暂停 / 已被另一个任务认领 Running /
            # 状态在排队期间变化）。此时绝不标记 Failed：Paused 归暂停逻辑所有，
            # 已是 Running 说明另一个任务合法拥有执行权，把计划打成 Failed 会
            # 覆盖别人的合法状态。只释放自己的请求占用，不做计划级清理。
            current_status = state.database.get_plan_status(str(plan.plan_id))
            debug_log(f"Plan {plan.plan_id} claim Running failed (resume={resume}, current={current_status}), exiting gracefully")
            if current_status == PlanStatus.PAUSED.value:
                await broadcast_log("计划已暂停，跳过执行", "info")
            else:
                await broadcast_log(
                    f"计划状态已变化（当前: {current_status}），跳过执行", "info"
                )
            return

        # Explicitly supplied Run IDs must match this plan and token as well.
        record = state.database.get_execution_run(run_id)
        if not (record and record["plan_id"] == str(plan.plan_id)
                and record["owner_token"] == owner_token
                and record["status"] == RunStatus.RUNNING.value):
            _run_claimed = False
            return
        _run_claimed = True
        if not still_owns_run():
            return
        slot = state.current_execution_owner or {}
        if slot.get("reservation_token") == reservation_token:
            slot.update(run_id=run_id, owner_token=owner_token)
        await broadcast_plan_update(str(plan.plan_id))

        # 认领成功后重新读取计划：请求入队与真正执行之间，失败重试/回滚可能已经
        # 更新了 checkpoint 或当前分支，入口传入的对象不再是权威快照。
        latest_plan = state.database.get_plan(str(plan.plan_id))
        if latest_plan is None:
            logger.warning("计划在认领后不存在，停止执行: {}", plan.plan_id)
            return
        plan = latest_plan
        branch_version = getattr(plan, 'current_branch', 1) or 1
        resume_from_step_id = (
            str(getattr(plan, 'resume_from_step_id', None))
            if getattr(plan, 'resume_from_step_id', None) else None
        )
        # Keep the persisted goal while prerequisite batches are running.
        # A failed checkpoint would otherwise disappear from ordinary Pending selection.
        restoring_checkpoint = resume_from_step_id is not None

        print(f"[BG] plan={plan.plan_id} status=Running", flush=True)
        await broadcast_log(f"{'恢复' if (resume or restoring_checkpoint) else '开始'}执行计划: {plan.plan_id}", "info")

        # P0 用量记账：为本次执行绑定 plan 上下文，执行期内所有 LLM 调用自动归账
        from utils import usage_tracker as _usage_tracker
        _usage_tracker.set_context(plan_id=str(plan.plan_id), phase="executor")
        _usage_context_bound = True

        # Worktree 隔离：计划绑定 active worktree 时，把本执行上下文的工作区根
        # 重定向到 worktree（contextvar 随 asyncio 任务传播，to_thread 工具线程
        # 内同样生效；多计划并行时各执行上下文互不影响，无需进程级 env 切换）。
        _bound_worktree = state.database.get_active_worktree(str(plan.plan_id))
        _workspace_override_token = None
        if _bound_worktree:
            from tools.workspace import set_workspace_override
            _workspace_override_token = set_workspace_override(_bound_worktree["worktree_path"])
            await broadcast_log(
                f"[WORKTREE] 计划绑定隔离 worktree: {_bound_worktree['worktree_path']}（分支 {_bound_worktree['branch']}）",
                "info",
            )

        current_step_id = None
        while True:
            if not still_owns_run():
                return
            current_status = state.database.get_plan_status(str(plan.plan_id))
            if current_status == "Paused":
                if resume_from_step_id:
                    _require_write(
                        state.database.update_plan_state_machine(
                            str(plan.plan_id), "Paused", resume_from_step_id=resume_from_step_id,
                        ),
                        "暂停时保留尚未执行的恢复目标",
                    )
                debug_log(f"Plan {plan.plan_id} is Paused, stopping execution loop")
                await broadcast_log(f"计划 {plan.plan_id} 已暂停", "info")
                break

            # Run 心跳失效后停止启动新步骤，不再执行计划级清理。
            if run_id and owner_token:
                try:
                    if not state.database.heartbeat_execution_run(run_id, owner_token):
                        debug_log(f"Run heartbeat fenced or missing: {run_id}")
                        _run_claimed = False
                        return
                except Exception as heartbeat_exc:
                    debug_log(f"Run heartbeat failed: {heartbeat_exc}")
                    raise

            # P0 预算闸门（超限 → Paused，可 /resume 恢复继续）
            _budget_limit = getattr(state, "budget_max_tokens_per_plan", 0) or 0
            if _budget_limit > 0:
                _used_tokens = _usage_tracker.plan_total_tokens(str(plan.plan_id))
                if _used_tokens >= _budget_limit:
                    _budget_msg = (
                        f"[BUDGET] 计划 {plan.plan_id} token 预算超限"
                        f"（已用 {_used_tokens} ≥ 上限 {_budget_limit}），自动暂停；"
                        f"提高 config budget.max_tokens_per_plan 后可 /resume 继续"
                    )
                    logger.warning(_budget_msg)
                    await broadcast_log(_budget_msg, "warning")
                    _require_write(
                        state.database.update_plan_state_machine(str(plan.plan_id), "Paused"),
                        f"计划 {plan.plan_id} 状态 → Paused（token 预算超限）",
                    )
                    await broadcast_plan_update(str(plan.plan_id))
                    break

            # 依赖感知取批：返回本批可执行步骤（依赖就绪 + 两两资源不冲突），
            # 或说明为何没有可执行步骤（依赖失败 / 存在无法就绪的 pending 步骤）。
            _scheduling_failed = True
            selection = state.database.select_next_batch(
                str(plan.plan_id),
                branch_version=branch_version,
                resume_from_step_id=resume_from_step_id,
                max_concurrency=getattr(state, "executor_max_concurrency", 1),
                parallel_graphs=getattr(state, "parallel_graphs", False),
            )
            _scheduling_failed = False
            if resume_from_step_id and selection.get("checkpoint_consumed", False):
                # Clear only after dependency validation, before dispatching the target.
                # A failed clear must stop execution and retain an actionable checkpoint.
                current_step_id = resume_from_step_id
                _scheduling_failed = True
                _require_write(
                    state.database.clear_plan_checkpoint(str(plan.plan_id)),
                    "消费已就绪或已完成的恢复检查点",
                )
                resume_from_step_id = None
                _scheduling_failed = False
            batch = selection["steps"]
            if not batch:
                blocked_reason = selection.get("blocked_reason")
                blocked_step_ids = selection.get("blocked_step_ids") or []
                if blocked_reason or blocked_step_ids:
                    _scheduling_failed = True
                    # 仍有 pending 步骤但依赖无法满足：绝不能报 Completed。
                    anchor_step_id = blocked_step_ids[0] if blocked_step_ids else current_step_id
                    reason = blocked_reason or f"依赖未就绪的步骤: {blocked_step_ids[:5]}"
                    _require_write(
                        state.database.update_plan_state_machine(
                            str(plan.plan_id), "Failed",
                            resume_from_step_id=anchor_step_id,
                            current_branch=branch_version,
                        ),
                        f"计划 {plan.plan_id} 状态 → Failed（依赖阻塞）",
                    )
                    await broadcast_plan_update(str(plan.plan_id))
                    await broadcast_log(
                        f"计划 {plan.plan_id} 依赖阻塞，无法继续: {reason}", "error"
                    )
                    break
                active_failed = state.database.get_active_failed_steps(
                    str(plan.plan_id), branch_version
                )
                if active_failed:
                    failed_step_id = str(active_failed[0]["step_id"])
                    _require_write(
                        state.database.update_plan_state_machine(
                            str(plan.plan_id), "Failed",
                            resume_from_step_id=failed_step_id,
                            current_branch=branch_version,
                        ),
                        f"计划 {plan.plan_id} 保持 Failed（存在未处理失败步骤）",
                    )
                    await broadcast_plan_update(str(plan.plan_id))
                    await broadcast_log(
                        f"计划 {plan.plan_id} 仍有失败步骤，未误报完成", "error"
                    )
                    break
                _require_write(
                    state.database.update_plan_state_machine(str(plan.plan_id), "Completed"),
                    f"计划 {plan.plan_id} 状态 → Completed",
                )
                await broadcast_plan_update(str(plan.plan_id))
                await broadcast_log(f"计划执行完成: {plan.plan_id}", "success")
                debug_log(f"Plan {plan.plan_id} execution COMPLETED")
                await _generate_final_report(plan, run_id)
                break

            # Prerequisite batches do not consume the original recovery goal.
            skipped_ids = {s["step_id"] for s in state.database.get_steps(str(plan.plan_id)) if s.get("status") == "Skipped"}

            # 批内并发执行（有界并发）：每个步骤独立走完整状态机 + 重试 + 事件。
            # 任何一步返回 "stop"（拦截/校验失败/重试耗尽/中止）即终止执行，
            # 不启动下一批，保持「失败即停」与 checkpoint/回滚语义。
            async def _dispatch_one(step_row):
                return await _execute_step_to_completion(
                    plan, step_row, run_id, owner_token, branch_version,
                    resume=bool(resume), skipped_ids=skipped_ids, debug_log=debug_log,
                )

            stop_step_id = None
            current_step_id = str(batch[0]["step_id"])
            if len(batch) == 1:
                outcome, step_id = await _dispatch_one(batch[0])
                current_step_id = step_id
                if outcome == "stop":
                    stop_step_id = step_id
            else:
                max_conc = max(1, int(getattr(state, "executor_max_concurrency", 1)))
                sem = asyncio.Semaphore(max_conc)

                async def _bounded(step_row):
                    async with sem:
                        return await _dispatch_one(step_row)

                outcomes = await asyncio.gather(*(_bounded(s) for s in batch), return_exceptions=True)
                for idx, outcome in enumerate(outcomes):
                    if isinstance(outcome, Exception):
                        logger.exception("批内步骤异常: {}", outcome)
                        stop_step_id = str(batch[idx].get("step_id"))
                        break
                    code, step_id = outcome
                    current_step_id = step_id
                    if code == "stop":
                        stop_step_id = step_id
                        break
            if stop_step_id:
                if resume_from_step_id:
                    _require_write(
                        state.database.update_plan_state_machine(
                            str(plan.plan_id), state.database.get_plan_status(str(plan.plan_id)),
                            resume_from_step_id=resume_from_step_id,
                        ),
                        "前置步骤停止时保留原恢复目标",
                    )
                await broadcast_log(f"[ERROR] 批内步骤 {stop_step_id} 失败，终止执行", "error")
                break

        # 最后一个步骤可能在本轮循环末尾完成，下一轮通常会完成状态收敛；
        # 这里再做一次幂等收敛，避免收尾阶段把成功运行误判为 Failed。
        if still_owns_run() and state.database.get_plan_status(str(plan.plan_id)) == PlanStatus.RUNNING.value:
            active_steps = state.database.get_active_steps_snapshot(str(plan.plan_id), branch_version)
            if active_steps and all(
                step_row.get("status") in {StepStatus.SUCCESS.value, StepStatus.SKIPPED.value}
                for step_row in active_steps
            ):
                _require_write(
                    state.database.update_plan_state_machine(str(plan.plan_id), PlanStatus.COMPLETED.value),
                    f"计划 {plan.plan_id} 状态 → Completed",
                )
                await broadcast_plan_update(str(plan.plan_id))
                await broadcast_log(f"计划执行完成: {plan.plan_id}", "success")
                await _generate_final_report(plan, run_id)

    except ExecutionBusy as e:
        await broadcast_log(f"跳过重复执行: {e}", "info")
    except asyncio.CancelledError:
        try:
            if still_owns_run():
                status = state.database.get_plan_status(str(plan.plan_id))
                if status in {"Running", "Paused"}:
                    # Cancelled batches must not leave unschedulable Running rows.
                    rows = state.database.get_active_steps_snapshot(
                        str(plan.plan_id), getattr(plan, "current_branch", 1) or 1,
                    )
                    interrupted = [r for r in rows if r["status"] == "Running"]
                    for row in interrupted:
                        _require_write(
                            state.database.update_step(row["step_id"], "Pending"),
                            "取消执行后的步骤恢复",
                        )
                    if status == "Running" or resume_from_step_id:
                        checkpoint = resume_from_step_id or (interrupted[0]["step_id"] if interrupted else current_step_id)
                        _require_write(
                            state.database.update_plan_state_machine(
                                str(plan.plan_id), "Failed" if status == "Running" else "Paused",
                                resume_from_step_id=checkpoint,
                            ),
                            "取消执行后的计划状态更新",
                        )
        except Exception as exc:
            logger.warning("取消执行时状态收敛失败: {}", exc)
        raise
    except Exception as e:
        debug_log(f"EXCEPTION in execute_plan_background: {e}")
        import traceback
        debug_log(f"Traceback: {traceback.format_exc()}")
        try:
            if still_owns_run():
                state.database.update_plan_state_machine(
                    str(plan.plan_id), "Failed",
                    resume_from_step_id=resume_from_step_id or current_step_id,
                    current_branch=getattr(plan, "current_branch", 1) or 1,
                )
                await broadcast_plan_update(str(plan.plan_id))
        except Exception as inner:
            debug_log(f"update_plan failed during exception handling: {inner}")
        await broadcast_log(f"执行失败: {str(e)}", "error")
    finally:
        try:
            # Worktree 隔离：恢复本执行上下文的工作区覆盖（无论成功、失败还是异常）。
            if _workspace_override_token is not None:
                try:
                    from tools.workspace import reset_workspace_override
                    reset_workspace_override(_workspace_override_token)
                except Exception as _ws_restore_err:
                    debug_log(f"恢复工作区覆盖失败（忽略）: {_ws_restore_err}")
            if still_owns_run():
                # 项目工作记忆：计划结束时把执行状态写入工作区，让后续任务"接着干"
                try:
                    from tools.project_memory import save_entry as _pm_save
                    _steps_rows = state.database.get_steps(str(plan.plan_id)) or []
                    _plan_final = state.database.get_plan_status(str(plan.plan_id)) or "未知"
                    _mem_lines = [f"计划 {str(plan.plan_id)[:8]} 执行结束（最终状态: {_plan_final}）"]
                    for _s in _steps_rows:
                        _line = f"- 步骤{_s.get('step_order')} [{_s.get('tool')}] {_s.get('status')}"
                        if _s.get('failure_reason'):
                            _line += f"（原因: {_s.get('failure_reason')}）"
                        if _s.get('description'):
                            _line += f" —— {_s.get('description')}"
                        _mem_lines.append(_line)
                    _pm_save("执行摘要", chr(10).join(_mem_lines))
                    debug_log(f"项目记忆已更新: {plan.plan_id}")
                except Exception as _mem_err:
                    debug_log(f"项目记忆写入失败: {_mem_err}")
                # P0-a 安全网：计划已终止（完成/失败/异常），批量拒绝其未决审批，
                # 防止残留的挂起请求泄漏或被后续计划误 resolve。
                try:
                    state.approval_manager.reject_all_for_plan(str(plan.plan_id), "plan execution ended")
                    state.authorization_manager.revoke_all_for_plan(str(plan.plan_id))
                except Exception as runtime_cleanup_exc:
                    debug_log(f"runtime grant cleanup failed: {runtime_cleanup_exc}")
                # 最终收敛安全网：若所有活动步骤均已成功/跳过，即使执行循环曾因收尾异常
                # 保留了 Failed，也不能让已完成的计划继续以失败结束；只在无失败步骤时修正。
                try:
                    _cleanup_status = state.database.get_plan_status(str(plan.plan_id))
                    if not _scheduling_failed and _cleanup_status in {PlanStatus.RUNNING.value, PlanStatus.FAILED.value}:
                        _cleanup_branch = getattr(plan, "current_branch", 1) or 1
                        _cleanup_steps = state.database.get_active_steps_snapshot(str(plan.plan_id), _cleanup_branch)
                        if _cleanup_steps and all(
                            row.get("status") in {StepStatus.SUCCESS.value, StepStatus.SKIPPED.value}
                            for row in _cleanup_steps
                        ):
                            state.database.update_plan_state_machine(
                                str(plan.plan_id), PlanStatus.COMPLETED.value,
                            )
                            state.database.clear_plan_checkpoint(str(plan.plan_id))
                            await broadcast_plan_update(str(plan.plan_id))
                except Exception as _status_cleanup_exc:
                    debug_log(f"final plan status convergence failed: {_status_cleanup_exc}")
                # Run 收尾：按计划最终状态映射 Run 终态，owner_token fencing 保证只本任务可收尾。
                _run_terminal = {
                    PlanStatus.COMPLETED.value: RunStatus.COMPLETED.value,
                    PlanStatus.FAILED.value: RunStatus.FAILED.value,
                    PlanStatus.PAUSED.value: RunStatus.PAUSED.value,
                }
                if run_id and owner_token:
                    try:
                        _final_status = state.database.get_plan_status(str(plan.plan_id))
                        _run_status = _run_terminal.get(_final_status, RunStatus.FAILED.value)
                        if not state.database.finish_execution_run(
                            run_id, owner_token, _run_status,
                            failure_reason="interrupted by service shutdown" if _run_status == RunStatus.FAILED.value else None,
                        ):
                            debug_log(f"finish_execution_run fenced or missing: run={run_id}")
                    except Exception as run_close_exc:
                        debug_log(f"finish_execution_run failed: {run_close_exc}")
                # 收尾前未结束的 Running Attempt 一并补齐（如异常路径残留）。
                if attempt_id:
                    try:
                        state.database.finish_execution_attempt(
                            attempt_id, AttemptStatus.INTERRUPTED.value,
                            failure_reason="run ended before attempt completed",
                            run_id=run_id, owner_token=owner_token,
                        )
                    except Exception:
                        pass
        finally:
            if _usage_context_bound:
                try:
                    from utils import usage_tracker as tracker
                    # The tracker is currently thread-local: do not clear a sibling's context.
                    if tracker.get_context().get("plan_id") == str(plan.plan_id):
                        tracker.clear_context()
                except Exception:
                    pass
            if _reservation_owned:
                release_execution(state, str(plan.plan_id), reservation_token)
            debug_log(f"execute_plan_background FINISHED for plan {plan.plan_id}")
