"""Checkpoint DAG recovery using isolated SQLite and fake tool dispatch only."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from database import Database, DatabaseError
from models import (Architecture, DesignDocument, DesignPart1, Difficulty,
                    ExecutionPlan, PlanSegment, PlanStatus, Step, ToolAssessment)


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    from api import server, shared
    from api.runtime import background, step_execution, artifacts
    from tools import project_memory
    from api.routes import plan_routes
    from utils import usage_tracker

    state = shared.AppState()
    db = Database(str(tmp_path / "checkpoint.db"))
    state.database = db
    for module in (server, shared, plan_routes, background, step_execution, artifacts):
        if hasattr(module, "state"):
            monkeypatch.setattr(module, "state", state)
        for name in ("broadcast_log", "broadcast_plan_update"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, AsyncMock())
    monkeypatch.setattr(project_memory, "save_entry", Mock(return_value={"success": True}))
    monkeypatch.setattr(usage_tracker._local, "ctx", {}, raising=False)
    monkeypatch.setattr(background, "_generate_final_report", AsyncMock())
    seen = []

    async def succeed(plan, row, *args, **kwargs):
        seen.append(row["step_id"])
        db.update_step(row["step_id"], "Success")
        return "continue", row["step_id"]

    monkeypatch.setattr(background, "_execute_step_to_completion", succeed)

    def seed(specs, status="Failed", checkpoint="target"):
        # specs = [(name, dependency names, initial status), ...], including forward edges.
        ids = {name: str(uuid4()) for name, _, _ in specs}
        design_id = uuid4()
        db.save_design(DesignDocument(
            design_id=design_id, user_request="isolated checkpoint regression",
            timestamp=datetime.now(timezone.utc),
            part1_design=DesignPart1(
                architecture=Architecture(components=["c"], interactions=["i"]),
                difficulty=Difficulty(overall="low", breakdown={}),
            ), part2_tool_assessment=ToolAssessment(required_tools=[], overall_tool_completeness=1, risks=[]),
            user_approval=True,
        ))
        steps = [Step(step_id=ids[name], order=index, description=name, tool="manual_tool",
                      inputs={}, check_criteria="手动检查", depends_on=[ids[d] for d in deps])
                 for index, (name, deps, _) in enumerate(specs, 1)]
        plan = ExecutionPlan(plan_id=uuid4(), design_id=design_id, status=PlanStatus(status),
            created_at=datetime.now(timezone.utc), implementation_list=[],
            segments=[PlanSegment(segment_id=uuid4(), description="g", steps=steps)])
        assert db.save_plan(plan)
        pid = str(plan.plan_id)
        for name, _, step_status in specs:
            assert db.update_step(ids[name], step_status)
        if checkpoint:
            assert db.update_plan_state_machine(pid, status, resume_from_step_id=ids[checkpoint])
        return SimpleNamespace(pid=pid, ids=ids)

    def select(item, checkpoint="target", **kwargs):
        sid = item.ids.get(checkpoint, checkpoint)
        return db.select_next_batch(item.pid, resume_from_step_id=sid, **kwargs)

    def sql(statement, params):
        with closing(db._get_connection()) as conn, conn:
            conn.execute(statement, params)

    return SimpleNamespace(db=db, state=state, seed=seed, select=select, sql=sql,
                           runtime=background, routes=plan_routes, seen=seen, succeed=succeed, usage=usage_tracker)


def ids(selection):
    return [r["step_id"] for r in selection["steps"]]


@pytest.mark.parametrize("target_status", ["Pending", "Failed", "Retry"])
@pytest.mark.parametrize("parallel", [False, True])
def test_checkpoint_waits_for_ready_prerequisite(case, target_status, parallel):
    item = case.seed([("target", ["setup"], target_status), ("setup", [], "Pending")])
    before = case.db.get_replan_snapshot(item.pid)
    selected = case.select(item, parallel_graphs=parallel, max_concurrency=3)
    assert ids(selected) == [item.ids["setup"]]
    assert selected["checkpoint_consumed"] is False
    assert case.db.get_replan_snapshot(item.pid) == before, "selection must not mutate state"
    case.db.update_step(item.ids["setup"], "Success")
    selected = case.select(item)
    assert ids(selected) == [item.ids["target"]]
    assert selected["checkpoint_consumed"] is True


def test_transitive_frontier_excludes_unrelated_work(case):
    item = case.seed([("target", ["middle"], "Failed"), ("unrelated", [], "Pending"),
                      ("middle", ["leaf"], "Pending"), ("leaf", [], "Pending")])
    for name in ("leaf", "middle", "target"):
        selected = case.select(item, parallel_graphs=True, max_concurrency=4)
        assert ids(selected) == [item.ids[name]]
        case.db.update_step(item.ids[name], "Success")


@pytest.mark.parametrize("status", ["Success", "Skipped"])
def test_finished_checkpoint_is_not_rerun(case, status):
    item = case.seed([("target", [], status), ("next", [], "Pending")])
    selected = case.select(item)
    assert ids(selected) == [item.ids["next"]]
    assert selected["checkpoint_consumed"] is True


@pytest.mark.parametrize("invalid", ["missing", "foreign", "obsolete", "old_branch", "future_branch"])
def test_invalid_checkpoint_blocks_instead_of_falling_through(case, invalid):
    item = case.seed([("target", [], "Success"), ("next", [], "Pending")])
    checkpoint = item.ids["target"]
    if invalid == "missing":
        checkpoint = str(uuid4())
    elif invalid == "foreign":
        checkpoint = case.seed([("target", [], "Pending")]).ids["target"]
    elif invalid == "obsolete":
        case.sql("UPDATE steps SET is_obsolete=1 WHERE step_id=?", (checkpoint,))
    else:
        case.sql("UPDATE steps SET branch_version=? WHERE step_id=?", (2 if invalid == "future_branch" else 1, checkpoint))
    selected = case.select(item, checkpoint=checkpoint, branch_version=2 if invalid == "old_branch" else 1)
    assert selected["steps"] == []
    assert selected["blocked_reason"]
    assert selected["blocked_step_ids"] == [checkpoint]
    assert selected["checkpoint_consumed"] is False


@pytest.mark.parametrize("status", ["Running", "RolledBack"])
def test_nonrunnable_checkpoint_fails_closed(case, status):
    item = case.seed([("target", [], status)])
    selected = case.select(item)
    assert selected["steps"] == [] and selected["blocked_reason"]


@pytest.mark.parametrize("status", ["Failed", "RolledBack", "Running", "Retry"])
def test_unavailable_prerequisite_does_not_run_unrelated_steps(case, status):
    item = case.seed([("target", ["setup"], "Pending"), ("setup", [], status),
                      ("unrelated", [], "Pending")])
    before = case.db.get_replan_snapshot(item.pid)
    selected = case.select(item, parallel_graphs=True, max_concurrency=4)
    assert selected["steps"] == [] and selected["blocked_reason"]
    assert case.db.get_replan_snapshot(item.pid) == before


@pytest.mark.parametrize("kind", ["missing", "self", "cycle", "invalid_json_type"])
def test_malformed_checkpoint_dependency_graph_is_blocked(case, kind):
    item = case.seed([("target", ["setup"], "Pending"), ("setup", [], "Pending")])
    if kind == "missing":
        case.db.update_step_dependencies(item.ids["target"], [str(uuid4())])
    elif kind == "self":
        case.db.update_step_dependencies(item.ids["target"], [item.ids["target"]])
    elif kind == "cycle":
        case.db.update_step_dependencies(item.ids["setup"], [item.ids["target"]])
    else:
        case.sql("UPDATE steps SET depends_on=? WHERE step_id=?", ('[{}]', item.ids["target"]))
    selected = case.select(item)
    assert selected["steps"] == [] and selected["blocked_reason"]


@pytest.mark.parametrize("historical_status", ["Success", "Skipped", "Pending"])
def test_historical_prerequisite_must_already_be_successful(case, historical_status):
    item = case.seed([("target", ["prefix"], "Pending"), ("prefix", [], historical_status)])
    case.sql("UPDATE steps SET branch_version=2 WHERE step_id=?", (item.ids["target"],))
    selected = case.select(item, branch_version=2)
    if historical_status in {"Success", "Skipped"}:
        assert ids(selected) == [item.ids["target"]]
    else:
        assert not selected["steps"] and selected["blocked_reason"]


def test_parent_id_alias_resolves_to_current_clone(case):
    item = case.seed([("target", ["old"], "Failed"), ("old", [], "Failed"), ("clone", [], "Pending")])
    case.sql("UPDATE steps SET is_obsolete=1 WHERE step_id=?", (item.ids["old"],))
    case.sql("UPDATE steps SET branch_version=2 WHERE step_id IN (?,?)", (item.ids["target"], item.ids["clone"]))
    case.sql("UPDATE steps SET parent_step_id=? WHERE step_id=?", (item.ids["old"], item.ids["clone"]))
    assert ids(case.select(item, branch_version=2)) == [item.ids["clone"]]
    case.db.update_step(item.ids["clone"], "Success")
    assert ids(case.select(item, branch_version=2)) == [item.ids["target"]]


def test_future_alias_cannot_satisfy_checkpoint_dependency(case):
    item = case.seed([("target", ["old"], "Pending"), ("old", [], "Failed"), ("future", [], "Success")])
    case.sql("UPDATE steps SET branch_version=2,parent_step_id=? WHERE step_id=?", (item.ids["old"], item.ids["future"]))
    selected = case.select(item)
    assert not selected["steps"] and selected["blocked_reason"]


@pytest.mark.parametrize("parallel,limit,conflict,expected", [(True,2,False,2),(True,2,True,1),(False,2,False,1),(True,1,False,1)])
def test_prerequisite_frontier_respects_parallel_resource_limits(case, parallel, limit, conflict, expected):
    item = case.seed([("target", ["a","b"], "Failed"), ("a", [], "Pending"), ("b", [], "Pending"), ("unrelated", [], "Pending")])
    import json
    for name in ("a", "b"):
        case.sql("UPDATE steps SET inputs=? WHERE step_id=?", (json.dumps({"resources":{"write":["same.py" if conflict else name+".py"]}}), item.ids[name]))
    selected = case.select(item, parallel_graphs=parallel, max_concurrency=limit)
    assert len(selected["steps"]) == expected
    assert set(ids(selected)) <= {item.ids["a"],item.ids["b"]}
    assert selected["checkpoint_consumed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("target_status", ["Pending", "Failed", "Retry"])
async def test_runtime_retains_checkpoint_until_target_dispatch(case, monkeypatch, target_status):
    item = case.seed([("target", ["middle"], target_status), ("middle", ["leaf"], "Pending"), ("leaf", [], "Pending")])
    async def execute(plan, row, *args, **kwargs):
        checkpoint = case.db.get_plan(item.pid).resume_from_step_id
        assert (checkpoint is None) if row["step_id"] == item.ids["target"] else str(checkpoint) == item.ids["target"]
        return await case.succeed(plan, row, *args, **kwargs)
    monkeypatch.setattr(case.runtime, "_execute_step_to_completion", execute)
    await case.runtime.execute_plan_background(case.db.get_plan(item.pid))
    assert case.seen == [item.ids[n] for n in ("leaf","middle","target")]
    assert case.db.get_plan_status(item.pid) == "Completed"
    assert case.db.get_plan(item.pid).resume_from_step_id is None


@pytest.mark.asyncio
async def test_runtime_parallel_prerequisites_complete_before_failed_target(case, monkeypatch):
    item = case.seed([("target", ["a","b"], "Failed"), ("a", [], "Pending"), ("b", [], "Pending")])
    case.state.parallel_graphs = True
    case.state.executor_max_concurrency = 2
    entered, both = set(), asyncio.Event()
    async def execute(plan, row, *args, **kwargs):
        if row["step_id"] != item.ids["target"]:
            assert str(case.db.get_plan(item.pid).resume_from_step_id) == item.ids["target"]
            entered.add(row["step_id"])
            if len(entered) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 2)
        else:
            assert both.is_set()
            assert all(r['status']=='Success' for r in case.db.get_steps(item.pid) if r['step_id'] in entered)
        return await case.succeed(plan,row,*args,**kwargs)
    monkeypatch.setattr(case.runtime, "_execute_step_to_completion", execute)
    await case.runtime.execute_plan_background(case.db.get_plan(item.pid))
    assert case.db.get_plan_status(item.pid) == "Completed"
    assert case.seen[-1] == item.ids["target"]


@pytest.mark.asyncio
async def test_invalid_checkpoint_is_not_masked_by_successful_cleanup(case):
    item = case.seed([("target", [], "Success")])
    missing = str(uuid4())
    case.db.update_plan_state_machine(item.pid, "Failed", resume_from_step_id=missing)
    await case.runtime.execute_plan_background(case.db.get_plan(item.pid))
    assert case.seen == []
    assert case.db.get_plan_status(item.pid) == "Failed"
    assert str(case.db.get_plan(item.pid).resume_from_step_id) == missing
    assert case.db.list_execution_runs(item.pid)[0]['status'] == 'Failed'


@pytest.mark.asyncio
@pytest.mark.parametrize("clear_error", [False, True])
@pytest.mark.parametrize("target_status", ["Pending", "Success"])
async def test_checkpoint_clear_failure_stops_before_dispatch(case, monkeypatch, clear_error, target_status):
    item = case.seed([("target", [], target_status)])
    clear = Mock(side_effect=DatabaseError("clear failed")) if clear_error else Mock(return_value=False)
    monkeypatch.setattr(case.db, "clear_plan_checkpoint", clear)
    await case.runtime.execute_plan_background(case.db.get_plan(item.pid))
    assert case.seen == []
    assert case.db.get_plan_status(item.pid) == "Failed"
    assert str(case.db.get_plan(item.pid).resume_from_step_id) == item.ids["target"]


@pytest.mark.asyncio
async def test_cancellation_preserves_failed_goal_and_reruns_prerequisite(case, monkeypatch):
    item = case.seed([("target", ["setup"], "Failed"), ("setup", [], "Pending")])
    started = asyncio.Event()
    async def interrupted(plan, row, *args, **kwargs):
        assert row['step_id'] == item.ids['setup']
        case.db.update_step(row['step_id'], 'Running')
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(case.runtime, '_execute_step_to_completion', interrupted)
    task = asyncio.create_task(case.runtime.execute_plan_background(case.db.get_plan(item.pid)))
    try:
        await asyncio.wait_for(started.wait(), 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert str(case.db.get_plan(item.pid).resume_from_step_id) == item.ids['target']
    assert next(r for r in case.db.get_steps(item.pid) if r['step_id']==item.ids['setup'])['status']=='Pending'
    monkeypatch.setattr(case.runtime, '_execute_step_to_completion', case.succeed)
    await case.runtime.execute_plan_background(case.db.get_plan(item.pid))
    assert case.seen == [item.ids['setup'],item.ids['target']]
    assert case.db.get_plan_status(item.pid)=='Completed'


@pytest.mark.asyncio
async def test_pause_keeps_deferred_failed_goal_even_if_pause_route_retargets(case, monkeypatch):
    item = case.seed([("target", ["setup"], "Failed"), ("setup", [], "Pending"), ("unrelated", [], "Pending")])
    async def pause(plan,row,*args,**kwargs):
        result = await case.succeed(plan,row,*args,**kwargs)
        case.db.update_plan_state_machine(item.pid,'Paused',resume_from_step_id=item.ids['unrelated'],paused_at_step_id=row['step_id'])
        return result
    monkeypatch.setattr(case.runtime,'_execute_step_to_completion',pause)
    await case.runtime.execute_plan_background(case.db.get_plan(item.pid))
    assert case.db.get_plan_status(item.pid)=='Paused'
    assert str(case.db.get_plan(item.pid).resume_from_step_id)==item.ids['target']
    monkeypatch.setattr(case.runtime,'_execute_step_to_completion',case.succeed)
    await case.runtime.execute_plan_background(case.db.get_plan(item.pid),resume=True)
    assert case.seen == [item.ids[n] for n in ('setup','target','unrelated')]
    assert case.db.get_plan_status(item.pid)=='Completed'


@pytest.mark.asyncio
async def test_budget_pause_does_not_clear_deferred_goal(case, monkeypatch):
    item = case.seed([("target", ["setup"], "Failed"), ("setup", [], "Pending")])
    case.state.budget_max_tokens_per_plan=1
    monkeypatch.setattr(case.usage,'plan_total_tokens',Mock(return_value=1))
    await case.runtime.execute_plan_background(case.db.get_plan(item.pid))
    assert case.seen == []
    assert case.db.get_plan_status(item.pid)=='Paused'
    assert str(case.db.get_plan(item.pid).resume_from_step_id)==item.ids['target']


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["stop", "exception"])
async def test_prerequisite_failure_preserves_original_goal(case, monkeypatch, outcome):
    item = case.seed([("target", ["setup"], "Failed"), ("setup", [], "Pending")])
    async def fail(plan, row, *args, **kwargs):
        assert row['step_id'] == item.ids['setup']
        case.db.update_step(row['step_id'], 'Failed')
        case.db.update_plan_state_machine(item.pid, 'Failed', resume_from_step_id=row['step_id'])
        if outcome == 'exception':
            raise RuntimeError('fake prerequisite failure')
        return 'stop', row['step_id']
    monkeypatch.setattr(case.runtime, '_execute_step_to_completion', fail)
    await case.runtime.execute_plan_background(case.db.get_plan(item.pid))
    assert case.db.get_plan_status(item.pid) == 'Failed'
    assert str(case.db.get_plan(item.pid).resume_from_step_id) == item.ids['target']
    selected = case.select(item)
    assert not selected['steps'] and selected['blocked_reason']


@pytest.mark.asyncio
async def test_cancel_during_pause_preserves_deferred_goal(case, monkeypatch):
    item = case.seed([("target", ["setup"], "Failed"), ("setup", [], "Pending")])
    paused = asyncio.Event()
    async def pause_and_wait(plan, row, *args, **kwargs):
        assert row['step_id'] == item.ids['setup']
        case.db.update_step(row['step_id'], 'Running')
        case.db.update_plan_state_machine(item.pid, 'Paused', resume_from_step_id=row['step_id'])
        paused.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(case.runtime, '_execute_step_to_completion', pause_and_wait)
    task = asyncio.create_task(case.runtime.execute_plan_background(case.db.get_plan(item.pid)))
    try:
        await asyncio.wait_for(paused.wait(), 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert case.db.get_plan_status(item.pid) == 'Paused'
    assert str(case.db.get_plan(item.pid).resume_from_step_id) == item.ids['target']
    monkeypatch.setattr(case.runtime, '_execute_step_to_completion', case.succeed)
    await case.runtime.execute_plan_background(case.db.get_plan(item.pid), resume=True)
    assert case.seen == [item.ids['setup'], item.ids['target']]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["Success", "Skipped"])
async def test_runtime_skips_finished_checkpoint_and_continues(case, status):
    item = case.seed([("target", [], status), ("next", [], "Pending")])
    await case.runtime.execute_plan_background(case.db.get_plan(item.pid))
    assert case.seen == [item.ids['next']]
    assert case.db.get_plan_status(item.pid) == 'Completed'
    assert case.db.get_plan(item.pid).resume_from_step_id is None


@pytest.mark.asyncio
async def test_pause_endpoint_immediately_persists_deferred_failed_goal(case):
    item = case.seed([("target", ["setup"], "Failed"), ("setup", [], "Running"),
                      ("unrelated", [], "Pending")], status="Running")
    await case.routes.pause_plan(item.pid)
    plan = case.db.get_plan(item.pid)
    assert plan.status.value == "Paused"
    assert str(plan.resume_from_step_id) == item.ids["target"]
    assert str(plan.paused_at_step_id) == item.ids["setup"]
