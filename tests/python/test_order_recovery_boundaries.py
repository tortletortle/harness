"""Regression prompted by the real order task and a harness checkpoint mistake."""
import asyncio
import pytest
from test_execution_ownership import env
from test_rollback_branching import _three_step_plan, _rollback, _branch_rows
from database import Database


def test_rollback_can_recover_after_an_empty_branch(tmp_path):
    db=Database(str(tmp_path/'empty.db'))
    pid,ids=_three_step_plan(db,tmp_path)
    for sid in ids[:2]:db.update_step(sid,'Success')
    db.update_step(ids[2],'Failed')
    # The API's target is an already completed checkpoint, not the failed step.
    # Legacy callers can have created an empty branch by targeting the last step.
    assert _rollback(db,pid,ids[2])==2
    assert not _branch_rows(db,pid,2)
    assert _rollback(db,pid,ids[1])==3
    rows=_branch_rows(db,pid,3)
    assert len(rows)==1
    assert rows[0]['parent_step_id']==ids[2] and rows[0]['status']=='Pending'
    assert _rollback(db,pid,ids[1])==4
    assert len(_branch_rows(db,pid,4))==1  # No duplicate historical clones.


@pytest.mark.parametrize('mode',['step','exception'])
async def test_failed_run_does_not_claim_service_shutdown(env,monkeypatch,mode):
    item=env.make()
    async def fail(plan,row,*args,**kwargs):
        if mode=='exception':raise RuntimeError('private synthetic detail')
        env.db.update_step(row['step_id'],'Failed')
        env.db.update_plan_state_machine(item.pid,'Failed')
        return 'stop',row['step_id']
    monkeypatch.setattr(env.background,'_execute_step_to_completion',fail)
    await env.run(item.plan)
    run=env.db.list_execution_runs(item.pid)[0]
    assert run['status']=='Failed'
    assert run['failure_reason']==('step execution failed' if mode=='step' else 'execution error: RuntimeError')
    assert 'private' not in run['failure_reason']


async def test_cancelled_run_has_distinct_reason(env,monkeypatch):
    item=env.make()
    started=asyncio.Event()
    async def block(*args,**kwargs):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(env.background,'_execute_step_to_completion',block)
    task=asyncio.create_task(env.run(item.plan))
    await asyncio.wait_for(started.wait(),2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    run=env.db.list_execution_runs(item.pid)[0]
    assert run['status']=='Failed'
    assert run['failure_reason']=='execution task cancelled'
