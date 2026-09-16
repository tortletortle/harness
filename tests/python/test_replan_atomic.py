"""Replan atomicity regressions with temporary SQLite; no real LLM or workspace writes."""
import asyncio
import json
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from database import Database
from designer import GenerationError
from models import (
    Architecture, DesignDocument, DesignPart1, Difficulty, ExecutionPlan,
    PlanSegment, PlanStatus, Step, ToolAssessment,
)


REPAIRS = [
    {"description": f"repair-{i}", "tool": "file_creator",
     "inputs": {"path": f"fix-{i}.txt", "content": "fixed"},
     "acceptance": [{"type": "file_exists", "path": f"fix-{i}.txt"}]}
    for i in (1, 2)
]


@pytest.fixture
def make_env(tmp_path, monkeypatch):
    from api.routes import replan_routes as routes

    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    broadcast = AsyncMock()
    log = AsyncMock()
    monkeypatch.setattr(routes, "broadcast_plan_update", broadcast)
    monkeypatch.setattr(routes, "broadcast_log", log)

    def make(failed_index=0, size=3, linear=False):
        db = Database(str(tmp_path / f"{uuid4()}.db"))
        design_id = uuid4()
        db.save_design(DesignDocument(
            design_id=design_id, user_request="atomic replan",
            timestamp=datetime.now(timezone.utc),
            part1_design=DesignPart1(
                architecture=Architecture(components=["c"], interactions=["i"]),
                difficulty=Difficulty(overall="low", breakdown={}),
            ),
            part2_tool_assessment=ToolAssessment(
                required_tools=[], overall_tool_completeness=1, risks=[],
            ), user_approval=True,
        ))
        steps = [Step(
            step_id=uuid4(), order=i + 1, description=f"original-{i}",
            tool="file_creator", inputs={"path": f"{i}.txt", "content": "ok"},
            check_criteria="手动检查",
        ) for i in range(size)]
        if not linear:
            for previous, step in zip(steps, steps[1:]):
                step.depends_on = [str(previous.step_id)]
        plan = ExecutionPlan(
            plan_id=uuid4(), design_id=design_id, status=PlanStatus.FAILED,
            created_at=datetime.now(timezone.utc),
            segments=[PlanSegment(segment_id=uuid4(), description="g", steps=steps)],
            implementation_list=[], resume_from_step_id=steps[failed_index].step_id,
        )
        db.save_plan(plan)
        ids = [str(step.step_id) for step in steps]
        for sid in ids[:failed_index]:
            db.update_step(sid, "Success")
        db.update_step_state_machine(ids[failed_index], "Failed", failure_reason="broken")
        db.update_plan_state_machine(str(plan.plan_id), "Failed", paused_at_step_id=ids[failed_index])
        designer = SimpleNamespace(generate_repair_steps=AsyncMock(return_value=deepcopy(REPAIRS)))
        monkeypatch.setattr(routes, "state", SimpleNamespace(database=db, designer=designer))
        return SimpleNamespace(
            db=db, plan_id=str(plan.plan_id), ids=ids, designer=designer,
            route=routes.replan_plan, broadcast=broadcast, log=log,
        )
    return make


def _snapshot(env):
    with closing(env.db._get_connection()) as conn:
        events = [dict(row) for row in conn.execute(
            "SELECT * FROM step_events WHERE plan_id = ? ORDER BY event_id", (env.plan_id,),
        )]
    return env.db.get_replan_snapshot(env.plan_id), events


def _drain(db, plan_id, branch):
    executed = []
    for _ in range(20):
        selection = db.select_next_batch(plan_id, branch_version=branch)
        assert not selection["blocked_reason"], selection
        if not selection["steps"]:
            break
        row = selection["steps"][0]
        executed.append(row["description"])
        assert db.update_step(row["step_id"], "Success")
    return executed


@pytest.mark.asyncio
@pytest.mark.parametrize("linear", [False, True])
@pytest.mark.parametrize("size", [1, 3])
async def test_first_failed_step_keeps_nonempty_current_branch(make_env, linear, size):
    env = make_env(size=size, linear=linear)
    result = await env.route(env.plan_id)
    assert result["new_branch"] == 1
    plan = env.db.get_plan(env.plan_id)
    assert plan.current_branch == 1
    assert plan.resume_from_step_id is None
    assert plan.paused_at_step_id is None
    assert env.db.get_plan_status(env.plan_id) == "Pending"
    rows = env.db.get_active_steps_snapshot(env.plan_id, 1)
    assert len(rows) == size + len(REPAIRS)
    assert all(row["branch_version"] == 1 for row in env.db.get_steps(env.plan_id))
    assert _drain(env.db, env.plan_id, 1) == ["repair-1", "repair-2"] + [f"original-{i}" for i in range(size)]
    snapshot, events = _snapshot(env)
    assert len(events) == 1 and events[0]["event_type"] == "replanned"
    assert events[0]["branch_version"] == 1
    assert json.loads(events[0]["event_data"])["new_branch"] == 1
    env.broadcast.assert_awaited_once_with(env.plan_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("linear", [False, True])
async def test_later_failure_clones_suffix_and_preserves_prefix(make_env, linear):
    env = make_env(failed_index=1, linear=linear)
    before, _ = _snapshot(env)
    result = await env.route(env.plan_id)
    assert result["new_branch"] == 2
    after, _ = _snapshot(env)
    prefix_before = next(row for row in before["steps"] if row["step_id"] == env.ids[0])
    prefix_after = next(row for row in after["steps"] if row["step_id"] == env.ids[0])
    assert prefix_after == prefix_before
    assert _drain(env.db, env.plan_id, 2) == ["repair-1", "repair-2", "original-1", "original-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_index", [0, 1])
@pytest.mark.parametrize("error", [GenerationError, RuntimeError, asyncio.CancelledError])
async def test_generation_failure_or_cancellation_leaves_everything_unchanged(make_env, failed_index, error):
    env = make_env(failed_index=failed_index)
    before = _snapshot(env)

    async def fail(**kwargs):
        assert _snapshot(env) == before  # No clone/obsolete writes before the LLM call.
        raise error("injected generator failure")

    env.designer.generate_repair_steps = fail
    expected = asyncio.CancelledError if error is asyncio.CancelledError else HTTPException
    with pytest.raises(expected) as exc:
        await env.route(env.plan_id)
    if error is not asyncio.CancelledError:
        assert exc.value.status_code == (502 if error is GenerationError else 500)
    assert _snapshot(env) == before
    env.broadcast.assert_not_awaited()
    env.log.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("repairs,status", [([], 502), ([{}], 409)])
async def test_invalid_generated_repairs_do_not_write(make_env, repairs, status):
    env = make_env(failed_index=1)
    before = _snapshot(env)
    env.designer.generate_repair_steps.return_value = repairs
    with pytest.raises(HTTPException) as exc:
        await env.route(env.plan_id)
    assert exc.value.status_code == status
    assert _snapshot(env) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,failed_index", [
    ("repair_insert", 1), ("obsolete", 1), ("dependency_update", 0),
    ("plan_update", 1), ("event_insert", 1), ("event_ignore", 0), ("step_ignore", 1),
])
def _install_fail_replan_trigger(conn, stage):
    if stage == "repair_insert":
        conn.execute(
            "CREATE TRIGGER fail_replan BEFORE INSERT ON steps "
            "WHEN NEW.description = 'repair-2' "
            "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
        )
        return
    if stage == "obsolete":
        conn.execute(
            "CREATE TRIGGER fail_replan BEFORE UPDATE OF is_obsolete ON steps "
            "WHEN NEW.is_obsolete = 1 "
            "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
        )
        return
    if stage == "dependency_update":
        conn.execute(
            "CREATE TRIGGER fail_replan BEFORE UPDATE OF depends_on ON steps "
            "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
        )
        return
    if stage == "plan_update":
        conn.execute(
            "CREATE TRIGGER fail_replan BEFORE UPDATE OF status ON plans "
            "WHEN NEW.status = 'Pending' "
            "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
        )
        return
    if stage == "event_insert":
        conn.execute(
            "CREATE TRIGGER fail_replan BEFORE INSERT ON step_events "
            "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
        )
        return
    if stage == "event_ignore":
        conn.execute(
            "CREATE TRIGGER fail_replan BEFORE INSERT ON step_events "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
        return
    if stage == "step_ignore":
        conn.execute(
            "CREATE TRIGGER fail_replan BEFORE INSERT ON steps "
            "WHEN NEW.description = 'repair-2' "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
        return
    raise ValueError(stage)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,failed_index", [
    ("repair_insert", 1), ("obsolete", 1), ("dependency_update", 0),
    ("plan_update", 1), ("event_insert", 1), ("event_ignore", 0),
    ("step_ignore", 1),
])
async def test_sql_failure_rolls_back_all_changes_including_event(make_env, stage, failed_index):
    env = make_env(failed_index=failed_index)
    with closing(env.db._get_connection()) as conn, conn:
        _install_fail_replan_trigger(conn, stage)
    before = _snapshot(env)
    with pytest.raises(HTTPException) as exc:
        await env.route(env.plan_id)
    assert exc.value.status_code == 500
    assert _snapshot(env) == before
    env.broadcast.assert_not_awaited()
    env.log.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["status", "branch", "checkpoint", "step", "execute_aba"])
async def test_concurrent_changes_are_not_overwritten(make_env, change):
    env = make_env(failed_index=1)
    changed = None

    async def generate(**kwargs):
        nonlocal changed
        if change == "status":
            env.db.update_plan_state_machine(env.plan_id, "Running")
        elif change == "branch":
            env.db.update_plan_state_machine(env.plan_id, "Failed", current_branch=2)
        elif change == "checkpoint":
            env.db.clear_plan_checkpoint(env.plan_id)
        elif change == "step":
            env.db.update_step(env.ids[-1], "Skipped")
        else:
            run = env.db.start_execution_run(env.plan_id, from_statuses=("Failed",))
            env.db.finish_execution_run(run["run_id"], run["owner_token"], "Failed")
            env.db.update_plan_state_machine(env.plan_id, "Failed")
        changed = _snapshot(env)
        return deepcopy(REPAIRS)

    env.designer.generate_repair_steps = generate
    with pytest.raises(HTTPException) as exc:
        await env.route(env.plan_id)
    assert exc.value.status_code == 409
    assert _snapshot(env) == changed
    env.broadcast.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_index", [0, 1])
async def test_two_replan_requests_only_one_commits(make_env, failed_index):
    env = make_env(failed_index=failed_index)
    ready = asyncio.Event()
    arrivals = 0

    async def generate(**kwargs):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=3)
        return deepcopy(REPAIRS)

    env.designer.generate_repair_steps = generate
    results = await asyncio.gather(env.route(env.plan_id), env.route(env.plan_id), return_exceptions=True)
    assert sum(isinstance(result, dict) and result.get("success") for result in results) == 1
    failures = [result for result in results if isinstance(result, HTTPException)]
    assert len(failures) == 1 and failures[0].status_code == 409
    snapshot, events = _snapshot(env)
    assert sum(row["description"].startswith("repair-") for row in snapshot["steps"]) == 2
    assert len(events) == 1
    env.broadcast.assert_awaited_once()


@pytest.mark.asyncio
async def test_two_database_connections_compare_inside_write_transaction(make_env):
    env = make_env(failed_index=1)
    other = Database(env.db.db_path)
    snapshot = env.db.get_replan_snapshot(env.plan_id)
    results = await asyncio.gather(
        asyncio.to_thread(env.db.commit_replan, snapshot, deepcopy(REPAIRS)),
        asyncio.to_thread(other.commit_replan, snapshot, deepcopy(REPAIRS)),
    )
    assert sum(result is None for result in results) == 1
    assert sum(bool(result and result["success"]) for result in results) == 1
    assert len(_snapshot(env)[1]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["missing_anchor", "obsolete_anchor", "nonfailed_anchor", "missing_dep", "cycle"])
async def test_invalid_anchor_or_graph_is_rejected_without_writes(make_env, invalid):
    env = make_env()
    if invalid == "missing_anchor":
        env.db.update_plan_state_machine(env.plan_id, "Failed", resume_from_step_id=str(uuid4()))
    elif invalid == "obsolete_anchor":
        with closing(env.db._get_connection()) as conn, conn:
            conn.execute("UPDATE steps SET is_obsolete=1 WHERE step_id=?", (env.ids[0],))
    elif invalid == "nonfailed_anchor":
        env.db.update_step(env.ids[0], "Pending")
    else:
        env.db.update_step_dependencies(env.ids[0], [str(uuid4()) if invalid == "missing_dep" else env.ids[-1]])
    before = _snapshot(env)
    with pytest.raises(HTTPException) as exc:
        await env.route(env.plan_id)
    assert exc.value.status_code == 409
    assert _snapshot(env) == before
    env.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_repaired_first_step_is_actually_executed_before_completed(make_env, monkeypatch):
    env = make_env(size=1)
    await env.route(env.plan_id)
    from api import server
    from api.runtime import background
    from executor import DefaultExecutor

    monkeypatch.setattr(server.state, "database", env.db)
    monkeypatch.setattr(server.state, "executor", DefaultExecutor(llm_client=None, model_config=[]))
    monkeypatch.setattr(background, "_generate_final_report", AsyncMock())
    executed = []

    async def execute(plan, row):
        executed.append(row["description"])
        # This seam replaces the whole single-step state machine, whose real
        # success path persists Success before returning to the batch loop.
        assert env.db.update_step_state_machine(row["step_id"], "Success")
        return {"success": True, "check_status": "unverified", "output": {},
                "error": None, "blocked": False, "retryable": False}

    monkeypatch.setattr(server, "_execute_step_with_state_machine", execute)
    await server.execute_plan_background(env.db.get_plan(env.plan_id))
    assert executed == ["repair-1", "repair-2", "original-0"]
    assert env.db.get_plan_status(env.plan_id) == "Completed"


@pytest.mark.asyncio
async def test_missing_checkpoint_falls_back_to_active_failed_step(make_env):
    env = make_env(failed_index=1)
    env.db.clear_plan_checkpoint(env.plan_id)
    result = await env.route(env.plan_id)
    assert result["anchor"] == env.ids[1]
    assert result["new_branch"] == 2


@pytest.mark.asyncio
async def test_replan_again_on_cloned_first_step_preserves_parent_dependencies(make_env):
    env = make_env(failed_index=1)
    first = await env.route(env.plan_id)
    rows = env.db.get_active_steps_snapshot(env.plan_id, first["new_branch"])
    retry = next(row for row in rows if row.get("parent_step_id") == env.ids[1])
    for row in rows:
        if row["description"].startswith("repair-"):
            env.db.update_step(row["step_id"], "Success")
    env.db.update_step_state_machine(retry["step_id"], "Failed", failure_reason="still broken")
    env.db.update_plan_state_machine(env.plan_id, "Failed", resume_from_step_id=retry["step_id"])
    result = await env.route(env.plan_id)
    assert result["new_branch"] == 2  # No predecessor in the active cloned branch.
    assert _drain(env.db, env.plan_id, 2) == ["repair-1", "repair-2", "original-1", "original-2"]
    assert len(_snapshot(env)[1]) == 2
