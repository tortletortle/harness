"""Execution ownership regression: isolated state/SQLite, no real Git or LLM calls."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from database import Database, DatabaseError
from models import (
    Architecture, DesignDocument, DesignPart1, Difficulty, ExecutionPlan,
    PlanSegment, PlanStatus, Step, ToolAssessment,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    from api import server, shared
    from api.routes import plan_routes
    from api.runtime import background, step_execution, artifacts
    from tools import project_memory
    from utils import usage_tracker

    state = shared.AppState()
    db = Database(str(tmp_path / "owner.db"))
    state.database = db
    for module in (server, shared, plan_routes, background, step_execution, artifacts):
        if hasattr(module, "state"):
            monkeypatch.setattr(module, "state", state)
        for name in ("broadcast_log", "broadcast_plan_update"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, AsyncMock())
    memory = Mock(return_value={"success": True})
    monkeypatch.setattr(project_memory, "save_entry", memory)
    monkeypatch.setattr(usage_tracker._local, "ctx", {}, raising=False)
    clear_usage = Mock(wraps=usage_tracker.clear_context)
    monkeypatch.setattr(usage_tracker, "clear_context", clear_usage)
    finish = Mock(wraps=db.finish_execution_run)
    monkeypatch.setattr(db, "finish_execution_run", finish)
    monkeypatch.setattr(background, "_generate_final_report", AsyncMock())

    async def succeed(plan, row, *args, **kwargs):
        db.update_step(row["step_id"], "Success")
        return "continue", row["step_id"]

    monkeypatch.setattr(background, "_execute_step_to_completion", succeed)

    def make(status="Pending", isolated=False):
        design_id = uuid4()
        db.save_design(DesignDocument(
            design_id=design_id, user_request="ownership regression",
            timestamp=datetime.now(timezone.utc),
            part1_design=DesignPart1(
                architecture=Architecture(components=["c"], interactions=["i"]),
                difficulty=Difficulty(overall="low", breakdown={}),
            ),
            part2_tool_assessment=ToolAssessment(
                required_tools=[], overall_tool_completeness=1, risks=[],
            ), user_approval=True,
        ))
        step = Step(step_id=uuid4(), order=1, description="one step", tool="file_creator",
                    inputs={"path": "proof.txt", "content": "ok"}, check_criteria="手动检查")
        plan = ExecutionPlan(
            plan_id=uuid4(), design_id=design_id, status=PlanStatus(status),
            created_at=datetime.now(timezone.utc), implementation_list=[],
            segments=[PlanSegment(segment_id=uuid4(), description="g", steps=[step])],
        )
        db.save_plan(plan)
        pid = str(plan.plan_id)
        if isolated:
            worktree = tmp_path / f"wt-{pid}"
            worktree.mkdir()
            db.create_worktree_record(str(uuid4()), pid, str(tmp_path), str(worktree), f"worktree/{pid}")
        grant = state.authorization_manager.issue_for_step(pid, step)
        approval = state.approval_manager.create(pid, str(step.step_id), step.tool, "create_file", step.inputs)
        return SimpleNamespace(plan=plan, pid=pid, sid=str(step.step_id), grant=grant, approval=approval)

    return SimpleNamespace(
        db=db, state=state, make=make, routes=plan_routes, background=background,
        run=background.execute_plan_background, memory=memory, finish=finish,
        clear_usage=clear_usage, usage=usage_tracker, server=server,
    )


async def _join(task):
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)
    await asyncio.sleep(0)  # Deliver the registered Task done callback.


def _other_slot(env):
    env.state.current_execution = "other-plan"
    env.state.current_execution_owner = {"plan_id": "other-plan", "reservation_token": "other-token"}
    env.state.execution_reservations["other-plan"] = "other-token"


def _assert_other_slot(env):
    assert env.state.current_execution == "other-plan"
    assert env.state.current_execution_owner["reservation_token"] == "other-token"
    assert env.state.execution_reservations["other-plan"] == "other-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,resume", [("Running", False), ("Paused", False), ("Pending", True)])
async def test_failed_claim_has_no_plan_cleanup(env, status, resume):
    item = env.make(status=status, isolated=True)
    _other_slot(env)
    env.db.update_plan_state_machine(item.pid, status, resume_from_step_id=item.sid)
    before = env.db.get_replan_snapshot(item.pid)
    env.usage.set_context(plan_id="other-plan", phase="executor")
    await env.run(item.plan, resume=resume)
    assert env.db.get_replan_snapshot(item.pid) == before
    assert not item.grant.revoked and not item.approval.resolved
    env.memory.assert_not_called()
    env.finish.assert_not_called()
    env.clear_usage.assert_not_called()
    assert env.usage.get_context()["plan_id"] == "other-plan"
    assert item.pid not in env.state.execution_reservations
    _assert_other_slot(env)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["claim", "status_read"])
async def test_exception_before_claim_does_not_mark_plan_failed_or_revoke(env, monkeypatch, stage):
    item = env.make(isolated=True)
    _other_slot(env)
    before = env.db.get_replan_snapshot(item.pid)
    if stage == "claim":
        monkeypatch.setattr(env.db, "start_execution_run", Mock(side_effect=DatabaseError("injected claim failure")))
    else:
        monkeypatch.setattr(env.db, "start_execution_run", Mock(return_value=None))
        monkeypatch.setattr(env.db, "get_plan_status", Mock(side_effect=DatabaseError("injected read failure")))
    await env.run(item.plan)
    assert env.db.get_replan_snapshot(item.pid) == before
    assert not item.grant.revoked and not item.approval.resolved
    env.memory.assert_not_called()
    env.finish.assert_not_called()
    assert item.pid not in env.state.execution_reservations
    _assert_other_slot(env)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["token", "plan", "missing", "finished"])
async def test_supplied_run_must_really_belong_to_caller(env, invalid):
    item = env.make(isolated=True)
    other = env.make(isolated=True)
    run = env.db.start_execution_run(item.pid, from_statuses=("Pending",))
    rid, token = run["run_id"], run["owner_token"]
    if invalid == "token":
        token = "wrong-token"
    elif invalid == "plan":
        other_run = env.db.start_execution_run(other.pid, from_statuses=("Pending",))
        rid, token = other_run["run_id"], other_run["owner_token"]
    elif invalid == "missing":
        rid = str(uuid4())
    else:
        env.db.finish_execution_run(rid, token, "Failed")
    env.finish.reset_mock()
    before = env.db.get_replan_snapshot(item.pid)
    await env.run(item.plan, run_id=rid, owner_token=token)
    assert env.db.get_replan_snapshot(item.pid) == before
    assert not item.grant.revoked and not other.grant.revoked
    assert not item.approval.resolved
    env.memory.assert_not_called()
    env.finish.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize("resume", [False, True])
async def test_duplicate_request_rejected_even_before_background_starts(env, isolated, resume):
    item = env.make(status="Paused" if resume else "Pending", isolated=isolated)
    route = env.routes.resume_plan if resume else env.routes.execute_plan
    assert (await route(item.pid))["success"]
    task = next(iter(env.state.execution_tasks))
    try:
        assert not env.db.list_execution_runs(item.pid)
        with pytest.raises(HTTPException) as exc:
            await route(item.pid)
        assert exc.value.status_code == 409
        assert len(env.state.execution_tasks) == 1
        assert not item.grant.revoked and not item.approval.resolved
    finally:
        task.cancel()
        await _join(task)
    assert not env.state.execution_reservations
    assert not env.state.execution_tasks
    assert env.state.current_execution is None
    env.finish.assert_not_called()
    env.memory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_task_creation_failure_releases_only_own_reservation(env, monkeypatch, resume):
    item = env.make(status="Paused" if resume else "Pending", isolated=True)
    _other_slot(env)
    route = env.routes.resume_plan if resume else env.routes.execute_plan
    with monkeypatch.context() as patch:
        patch.setattr(env.routes.asyncio, "create_task", Mock(side_effect=RuntimeError("enqueue failed")))
        with pytest.raises(HTTPException) as exc:
            await route(item.pid)
        assert exc.value.status_code == 500
    assert item.pid not in env.state.execution_reservations
    assert not env.state.execution_tasks
    assert not item.grant.revoked and not item.approval.resolved
    _assert_other_slot(env)


@pytest.mark.asyncio
async def test_old_done_callback_cannot_release_replacement(env):
    item = env.make()
    await env.routes.execute_plan(item.pid)
    task = next(iter(env.state.execution_tasks))
    task.cancel()
    env.state.execution_reservations[item.pid] = "new-owner"
    env.state.current_execution_owner = {"plan_id": item.pid, "reservation_token": "new-owner"}
    await _join(task)
    assert env.state.current_execution == item.pid
    assert env.state.execution_reservations[item.pid] == "new-owner"
    assert env.state.current_execution_owner["reservation_token"] == "new-owner"


@pytest.mark.asyncio
async def test_worktree_duplicate_and_completion_do_not_disturb_main_task(env, monkeypatch):
    main, wt = env.make(), env.make(isolated=True)
    started = {item.pid: asyncio.Event() for item in (main, wt)}
    release = {item.pid: asyncio.Event() for item in (main, wt)}

    async def execute(plan, row, *args, **kwargs):
        pid = str(plan.plan_id)
        env.db.update_step(row["step_id"], "Running")
        started[pid].set()
        await release[pid].wait()
        env.db.update_step(row["step_id"], "Success")
        return "continue", row["step_id"]

    monkeypatch.setattr(env.background, "_execute_step_to_completion", execute)
    main_task = await env.routes._schedule_plan_execution(main.plan)
    wt_task = await env.routes._schedule_plan_execution(wt.plan)
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 3)
        owner = dict(env.state.current_execution_owner)
        with pytest.raises(HTTPException) as exc:
            await env.routes.execute_plan(wt.pid)
        assert exc.value.status_code == 409
        await env.run(wt.plan)  # Bypassing HTTP cannot bypass reservation ownership.
        assert env.state.current_execution_owner == owner
        assert not wt.grant.revoked and not wt.approval.resolved
        assert not main.grant.revoked and not main.approval.resolved
        assert len(env.db.list_execution_runs(wt.pid)) == 1
        env.memory.assert_not_called()
        release[wt.pid].set()
        await _join(wt_task)
        assert wt.grant.revoked and wt.approval.resolved
        assert not main.grant.revoked and not main.approval.resolved
        assert env.state.current_execution_owner == owner
        assert env.state.current_execution == main.pid
        assert env.db.list_execution_runs(wt.pid)[0]["status"] == "Completed"
    finally:
        for event in release.values():
            event.set()
        await _join(wt_task)
        await _join(main_task)
    assert main.grant.revoked and main.approval.resolved
    assert not env.state.execution_reservations
    assert env.state.current_execution is None


@pytest.mark.asyncio
async def test_paused_but_still_executing_task_cannot_be_replaced(env, monkeypatch):
    item, other = env.make(), env.make()
    started, release = asyncio.Event(), asyncio.Event()

    async def execute(plan, row, *args, **kwargs):
        env.db.update_step(row["step_id"], "Running")
        started.set()
        await release.wait()
        env.db.update_step(row["step_id"], "Success")
        return "continue", row["step_id"]

    monkeypatch.setattr(env.background, "_execute_step_to_completion", execute)
    task = await env.routes._schedule_plan_execution(item.plan)
    try:
        await asyncio.wait_for(started.wait(), 3)
        await env.routes.pause_plan(item.pid)
        for route, pid in [(env.routes.resume_plan, item.pid), (env.routes.execute_plan, other.pid)]:
            with pytest.raises(HTTPException) as exc:
                await route(pid)
            assert exc.value.status_code == 409
        assert not item.grant.revoked and not item.approval.resolved
    finally:
        release.set()
        await _join(task)
    assert env.db.list_execution_runs(item.pid)[0]["status"] == "Paused"
    assert (await env.routes.resume_plan(item.pid))["success"]
    await _join(next(iter(env.state.execution_tasks)))


@pytest.mark.asyncio
async def test_completed_plan_still_holds_slot_until_report_and_cleanup_finish(env, monkeypatch):
    item, other = env.make(), env.make()
    entered, release = asyncio.Event(), asyncio.Event()

    async def report(*args):
        entered.set()
        await release.wait()

    monkeypatch.setattr(env.background, "_generate_final_report", report)
    task = await env.routes._schedule_plan_execution(item.plan)
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert env.db.get_plan_status(item.pid) == "Completed"
        for pid in (item.pid, other.pid):
            with pytest.raises(HTTPException) as exc:
                await env.routes.execute_plan(pid)
            assert exc.value.status_code == 409
        assert not item.grant.revoked
    finally:
        release.set()
        await _join(task)
    assert item.grant.revoked and item.approval.resolved
    assert env.state.current_execution is None
    assert env.db.list_execution_runs(item.pid)[0]["status"] == "Completed"


@pytest.mark.asyncio
async def test_cancelled_owner_cleans_own_resources_and_propagates_cancellation(env, monkeypatch):
    item = env.make(isolated=True)
    _other_slot(env)
    started = asyncio.Event()

    async def execute(plan, row, *args, **kwargs):
        env.db.update_step(row["step_id"], "Running")
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(env.background, "_execute_step_to_completion", execute)
    task = await env.routes._schedule_plan_execution(item.plan)
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    await _join(task)
    assert task.cancelled()
    assert item.grant.revoked and item.approval.resolved
    assert env.db.get_plan_status(item.pid) == "Failed"
    assert env.db.list_execution_runs(item.pid)[0]["status"] == "Failed"
    assert item.pid not in env.state.execution_reservations
    _assert_other_slot(env)
    assert env.db.get_plan(item.pid).resume_from_step_id is not None
    assert env.db.get_steps(item.pid)[0]["status"] == "Pending"
    executed = []

    async def succeed(plan, row, *args, **kwargs):
        executed.append(row["step_id"])
        env.db.update_step(row["step_id"], "Success")
        return "continue", row["step_id"]

    monkeypatch.setattr(env.background, "_execute_step_to_completion", succeed)
    await env.run(env.db.get_plan(item.pid))
    assert executed == [item.sid]
    assert env.db.get_plan_status(item.pid) == "Completed"
    _assert_other_slot(env)


@pytest.mark.asyncio
async def test_error_after_claim_but_before_first_step_cleans_own_run(env, monkeypatch):
    item = env.make(isolated=True)
    _other_slot(env)
    monkeypatch.setattr(env.db, "get_plan", Mock(side_effect=DatabaseError("injected read failure after claim")))
    await env.run(item.plan)
    assert env.db.get_plan_status(item.pid) == "Failed"
    assert env.db.list_execution_runs(item.pid)[0]["status"] == "Failed"
    assert item.grant.revoked and item.approval.resolved
    assert item.pid not in env.state.execution_reservations
    _assert_other_slot(env)


@pytest.mark.asyncio
async def test_owner_token_lost_during_step_skips_shared_cleanup(env, monkeypatch):
    item = env.make(isolated=True)

    async def execute(plan, row, run_id, owner_token, *args, **kwargs):
        from contextlib import closing
        with closing(env.db._get_connection()) as conn, conn:
            conn.execute("UPDATE execution_runs SET owner_token = 'new-owner' WHERE run_id = ?", (run_id,))
        return "stop", row["step_id"]

    monkeypatch.setattr(env.background, "_execute_step_to_completion", execute)
    await env.run(item.plan)
    assert env.db.get_plan_status(item.pid) == "Running"
    assert not item.grant.revoked and not item.approval.resolved
    env.finish.assert_not_called()
    env.memory.assert_not_called()


@pytest.mark.asyncio
async def test_two_main_plans_cannot_both_queue_while_pending(env):
    first, second = env.make(), env.make()
    task = await env.routes._schedule_plan_execution(first.plan)
    try:
        assert env.db.get_plan_status(first.pid) == "Pending"
        with pytest.raises(HTTPException) as exc:
            await env.routes.execute_plan(second.pid)
        assert exc.value.status_code == 409
        assert env.state.current_execution == first.pid
    finally:
        task.cancel()
        await _join(task)


@pytest.mark.asyncio
async def test_ownerless_legacy_running_marker_is_not_erased(env):
    item = env.make(status="Running")
    env.state.current_execution = item.pid
    before = env.db.get_replan_snapshot(item.pid)
    await env.run(item.plan)
    assert env.state.current_execution == item.pid
    assert env.db.get_replan_snapshot(item.pid) == before
    assert not item.grant.revoked and not item.approval.resolved
    env.memory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fenced", "database_error"])
async def test_heartbeat_failure_never_starts_a_step(env, monkeypatch, failure):
    item = env.make(isolated=True)
    dispatch = AsyncMock()
    monkeypatch.setattr(env.background, "_execute_step_to_completion", dispatch)
    heartbeat = Mock(return_value=False)
    if failure == "database_error":
        heartbeat.side_effect = DatabaseError("heartbeat unavailable")
    monkeypatch.setattr(env.db, "heartbeat_execution_run", heartbeat)
    await env.run(item.plan)
    dispatch.assert_not_awaited()
    if failure == "fenced":
        assert not item.grant.revoked and not item.approval.resolved
        env.finish.assert_not_called()
        env.memory.assert_not_called()
    else:
        assert env.db.get_plan_status(item.pid) == "Failed"
        assert env.db.list_execution_runs(item.pid)[0]["status"] == "Failed"
        assert item.grant.revoked
    assert item.pid not in env.state.execution_reservations


@pytest.mark.asyncio
async def test_valid_preclaimed_run_still_executes(env):
    item = env.make(isolated=True)
    run = env.db.start_execution_run(item.pid, from_statuses=("Pending",))
    await env.run(item.plan, run_id=run["run_id"], owner_token=run["owner_token"])
    assert env.db.get_plan_status(item.pid) == "Completed"
    assert env.db.get_execution_run(run["run_id"])["status"] == "Completed"
    assert len(env.db.list_execution_runs(item.pid)) == 1
    assert item.grant.revoked and item.approval.resolved


@pytest.mark.asyncio
async def test_cancelled_parallel_batch_can_rerun_every_interrupted_step(env, monkeypatch):
    item = env.make(isolated=True)
    env.db.update_step(item.sid, "Success")
    segment_id = str(item.plan.segments[0].segment_id)
    ids = [str(uuid4()), str(uuid4())]
    for index, sid in enumerate(ids):
        env.db.insert_repair_step(
            item.pid, 1, segment_id, sid, index + 2, f"parallel-{index}", "file_creator",
            {"path": f"{index}.txt", "resources": {"write": [f"{index}.txt"]}},
            [{"type": "file_exists", "path": f"{index}.txt"}], [item.sid],
        )
    env.state.parallel_graphs = True
    env.state.executor_max_concurrency = 2
    started = asyncio.Event()
    arrivals = []

    async def wait(plan, row, *args, **kwargs):
        env.db.update_step(row["step_id"], "Running")
        arrivals.append(row["step_id"])
        if len(arrivals) == 2:
            started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(env.background, "_execute_step_to_completion", wait)
    task = await env.routes._schedule_plan_execution(item.plan)
    try:
        await asyncio.wait_for(started.wait(), 3)
    finally:
        task.cancel()
        await _join(task)
    statuses = {row["step_id"]: row["status"] for row in env.db.get_steps(item.pid)}
    assert all(statuses[sid] == "Pending" for sid in ids)
    executed = []

    async def succeed(plan, row, *args, **kwargs):
        executed.append(row["step_id"])
        env.db.update_step(row["step_id"], "Success")
        return "continue", row["step_id"]

    monkeypatch.setattr(env.background, "_execute_step_to_completion", succeed)
    await env.run(env.db.get_plan(item.pid))
    assert set(executed) == set(ids)
    assert env.db.get_plan_status(item.pid) == "Completed"
