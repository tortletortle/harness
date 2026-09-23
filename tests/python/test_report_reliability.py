"""Adversarial report/lifecycle regression; no real providers or credential stores."""
import asyncio
import ast
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_execution_ownership import env  # isolated AppState/SQLite fixture
from database import DatabaseError


async def test_concurrent_same_job_calls_model_once_and_preserves_content(env):
    from api.runtime import artifacts
    item = env.make()
    job = artifacts.prepare_final_report(item.plan, database=env.db)
    entered, release = asyncio.Event(), asyncio.Event()

    async def summarize(*args):
        entered.set()
        await release.wait()
        return "one immutable report"

    designer = SimpleNamespace(generate_summary=AsyncMock(side_effect=summarize))
    first = asyncio.create_task(artifacts._run_report_job(job, env.db, designer))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(artifacts._run_report_job(job, env.db, designer))
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.gather(first, second)
    assert designer.generate_summary.await_count == 1
    record = env.db.get_artifact(job['artifact_id'])['metadata']
    assert record['status'] == 'completed'
    assert env.db.verify_artifact_hash(record['report_artifact_id']) is True


async def test_broadcast_failure_cannot_downgrade_committed_report(env, monkeypatch):
    from api.runtime import artifacts
    item = env.make()
    job = artifacts.prepare_final_report(item.plan, database=env.db)
    monkeypatch.setattr(artifacts, 'broadcast_log', AsyncMock(side_effect=RuntimeError('websocket gone')))
    designer = SimpleNamespace(generate_summary=AsyncMock(return_value='persisted report'))
    await artifacts._run_report_job(job, env.db, designer)
    assert env.db.get_artifact(job['artifact_id'])['metadata']['status'] == 'completed'


async def test_cancel_survives_interrupted_metadata_write_failure(env, monkeypatch):
    from api.runtime import artifacts
    item = env.make()
    job = artifacts.prepare_final_report(item.plan, database=env.db)
    started = asyncio.Event()

    async def summarize(*args):
        started.set()
        await asyncio.Event().wait()

    update = env.db.update_artifact_metadata
    def faulty(artifact_id, metadata):
        if metadata['status'] == 'interrupted':
            raise DatabaseError('injected full disk')
        return update(artifact_id, metadata)

    monkeypatch.setattr(env.db, 'update_artifact_metadata', faulty)
    task = asyncio.create_task(artifacts._run_report_job(job, env.db, SimpleNamespace(generate_summary=summarize)))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_malformed_recovery_row_does_not_block_other_jobs(env):
    from api.runtime import artifacts
    bad = artifacts.prepare_final_report(env.make().plan, database=env.db)
    metadata = dict(bad['metadata'], attempts='not-an-integer')
    env.db.update_artifact_metadata(bad['artifact_id'], metadata)
    good = artifacts.prepare_final_report(env.make().plan, database=env.db)
    env.state.designer = SimpleNamespace(generate_summary=AsyncMock(return_value='recovered'))
    env.state.llm_client = object()
    try:
        artifacts.recover_report_jobs()
    finally:
        if env.state.execution_tasks:
            await asyncio.gather(*env.state.execution_tasks, return_exceptions=True)
    assert env.db.get_artifact(bad['artifact_id'])['metadata']['status'] == 'failed'
    assert env.db.get_artifact(good['artifact_id'])['metadata']['status'] == 'completed'


async def test_startup_failure_closes_already_owned_resources(env, monkeypatch):
    client = SimpleNamespace(stop=AsyncMock())
    started = asyncio.Event()
    async def worker():
        started.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(worker())
    await started.wait()
    def fail_start():
        env.state.config = {'reports': {'shutdown_grace_seconds': 0}}
        env.state.mcp_clients = [client]
        env.state.execution_tasks.add(task)
        raise RuntimeError('injected startup failure with acquired resources')
    monkeypatch.setattr(env.server, '_load_models_config', fail_start)
    try:
        with pytest.raises(RuntimeError, match='injected startup'):
            async with env.server.lifespan(env.server.app):
                pytest.fail('startup unexpectedly succeeded')
        client.stop.assert_awaited_once()
        assert task.cancelled()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_api_token_is_not_passed_to_logger():
    from api import server
    tree = ast.parse(textwrap.dedent(inspect.getsource(server.lifespan)))
    leaks = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and isinstance(node.func.value, ast.Name) and node.func.value.id == 'logger'
             and any(isinstance(child, ast.Name) and child.id == 'token'
                     for arg in node.args for child in ast.walk(arg))]
    assert not leaks, 'API token must not be logged, even at DEBUG level'


async def test_cancelled_joiner_does_not_cancel_report_owner(env):
    from api.runtime import artifacts
    job = artifacts.prepare_final_report(env.make().plan, database=env.db)
    started, release = asyncio.Event(), asyncio.Event()
    async def summarize(*args):
        started.set()
        await release.wait()
        return 'owner finished'
    designer = SimpleNamespace(generate_summary=AsyncMock(side_effect=summarize))
    owner = asyncio.create_task(artifacts._run_report_job(job, env.db, designer))
    await started.wait()
    joiner = asyncio.create_task(artifacts._run_report_job(job, env.db, designer))
    await asyncio.sleep(0)
    joiner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await joiner
    assert not owner.done()
    release.set()
    await owner
    assert env.db.get_artifact(job['artifact_id'])['metadata']['status'] == 'completed'
    designer.generate_summary.assert_awaited_once()
    assert not artifacts._report_workers


@pytest.mark.parametrize('damage', ['missing', 'tampered'])
async def test_corrupt_report_is_not_claimed_complete_or_overwritten(env, damage):
    from pathlib import Path
    from api.runtime import artifacts
    job = artifacts.prepare_final_report(env.make().plan, database=env.db)
    designer = SimpleNamespace(generate_summary=AsyncMock(return_value='original report'))
    await artifacts._run_report_job(job, env.db, designer)
    metadata = env.db.get_artifact(job['artifact_id'])['metadata']
    path = Path(env.db.db_path).parent / 'artifacts' / metadata['report_artifact_id']
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'tampered')
    # Deliberately reuse the old pending job object: the worker must reread durable state.
    await artifacts._run_report_job(job, env.db, designer)
    assert env.db.get_artifact(job['artifact_id'])['metadata']['status'] == 'failed'
    designer.generate_summary.assert_awaited_once()
    assert not path.exists() if damage == 'missing' else path.read_bytes() == b'tampered'


async def test_recovery_called_twice_has_one_worker(env):
    from api.runtime import artifacts
    job = artifacts.prepare_final_report(env.make().plan, database=env.db)
    env.state.llm_client = object()
    env.state.designer = SimpleNamespace(generate_summary=AsyncMock(return_value='once'))
    artifacts.recover_report_jobs()
    artifacts.recover_report_jobs()
    await asyncio.gather(*env.state.execution_tasks)
    env.state.designer.generate_summary.assert_awaited_once()
    assert env.db.get_artifact(job['artifact_id'])['metadata']['attempts'] == 1


@pytest.mark.parametrize('value', [None, 'invalid', float('inf'), float('nan'), -1, True, 10000000])
def test_invalid_report_limits_use_finite_defaults(value):
    from api.runtime.artifacts import report_options
    options = report_options({'reports':{'timeout_seconds':value,'shutdown_grace_seconds':value}})
    assert options == {'timeout_seconds':180.0,'shutdown_grace_seconds':15.0}


def test_report_limit_valid_edges_and_invalid_sections():
    from api.runtime.artifacts import report_options
    assert report_options({'reports':{'timeout_seconds':'0.01','shutdown_grace_seconds':0}}) == {
        'timeout_seconds':0.01,'shutdown_grace_seconds':0.0}
    assert report_options({'reports':[]}) == report_options(None)


async def test_notification_cancellation_does_not_mark_report_interrupted(env, monkeypatch):
    from api.runtime import artifacts
    job = artifacts.prepare_final_report(env.make().plan, database=env.db)
    started = asyncio.Event()
    async def notification(*args):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(artifacts, 'broadcast_log', notification)
    task = asyncio.create_task(artifacts._run_report_job(
        job, env.db, SimpleNamespace(generate_summary=AsyncMock(return_value='committed'))))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert env.db.get_artifact(job['artifact_id'])['metadata']['status'] == 'completed'


async def test_forty_concurrent_callers_share_one_report(env):
    from api.runtime import artifacts
    job = artifacts.prepare_final_report(env.make().plan, database=env.db)
    async def summarize(*args):
        await asyncio.sleep(0.01)
        return 'one report for forty callers'
    designer = SimpleNamespace(generate_summary=AsyncMock(side_effect=summarize))
    await asyncio.wait_for(asyncio.gather(*[
        artifacts._run_report_job(job, env.db, designer) for _ in range(40)
    ]), 3)
    designer.generate_summary.assert_awaited_once()
    assert env.db.get_artifact(job['artifact_id'])['metadata']['attempts'] == 1
    assert not artifacts._report_workers
