"""Factual report history, not just Completed and a valid artifact hash."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from test_execution_ownership import env
from api.runtime import artifacts


def history(env, failures=3):
    item=env.make()
    for i in range(failures+1):
        run=env.db.start_execution_run(item.pid,('Pending','Completed','Failed'),owner_token='private-owner',branch_version=i+1)
        status='Failed' if i<failures else 'Completed'
        assert env.db.finish_execution_run(run['run_id'],'private-owner',status)
        env.db.update_plan_state_machine(item.pid,status,current_branch=i+1)
    env.db.update_step(item.sid,'Success')
    return item,env.db.get_plan(item.pid),run['run_id']


def test_history_counts_and_unknown_manual_are_frozen(env):
    item,plan,rid=history(env)
    job=artifacts.prepare_final_report(plan,rid,database=env.db)
    facts=job['metadata']['execution_facts']
    assert facts['run_counts']=={'Completed':1,'Failed':3}
    assert facts['manual_interventions']['status']=='unknown'
    assert facts['current_steps']=={'Success':1}
    assert 'private-owner' not in json.dumps(facts)
    history(env,1)  # A different plan must not contaminate the snapshot.
    assert artifacts.prepare_final_report(plan,rid,database=env.db)['metadata']['execution_facts']==facts


@pytest.mark.parametrize('claim',['无失败、无重试。','零人工干预，一次成功。','No failures and no manual intervention.','历史失败次数：0。'])
async def test_false_narrative_is_not_published_as_verified_report(env,claim):
    _,plan,rid=history(env)
    job=artifacts.prepare_final_report(plan,rid,database=env.db)
    designer=SimpleNamespace(generate_summary=AsyncMock(return_value=claim))
    await artifacts._run_report_job(job,env.db,designer)
    metadata=env.db.get_artifact(job['artifact_id'])['metadata']
    assert metadata['status']=='completed' and metadata['narrative_status']=='facts_only'
    assert metadata['narrative_generation_attempts']==1
    from api.routes.artifact_routes import _report_job_status
    assert _report_job_status(env.db.get_artifact(job['artifact_id']))['narrative_status']=='facts_only'
    report=env.db.get_artifact(metadata['report_artifact_id'])
    assert report['metadata']['narrative_status']=='facts_only'
    assert env.db.verify_artifact_hash(metadata['report_artifact_id']) is True
    text=env.db.read_artifact_content(metadata['report_artifact_id']).decode()
    assert '历史 Failed Run：3' in text and '模型补充说明未被采用' in text
    assert claim not in text
    designer.generate_summary.assert_awaited_once()
    assert env.db.get_execution_run(rid)['status']=='Completed'


async def test_honest_report_has_authoritative_history_section(env):
    _,plan,rid=history(env)
    job=artifacts.prepare_final_report(plan,rid,database=env.db)
    await artifacts._run_report_job(job,env.db,SimpleNamespace(generate_summary=AsyncMock(return_value='当前步骤执行成功。历史失败次数：3。人工干预情况未知。')))
    meta=env.db.get_artifact(job['artifact_id'])['metadata']
    assert meta['status']=='completed' and meta['narrative_status']=='validated'
    assert meta['narrative_generation_attempts']==1
    text=env.db.read_artifact_content(meta['report_artifact_id']).decode()
    assert '系统核验事实' in text and '历史 Failed Run：3' in text
    assert '未登记不等于未发生' in text


async def test_rejected_narrative_is_rewritten_once_with_only_safe_text_published(env):
    _,plan,rid=history(env)
    job=artifacts.prepare_final_report(plan,rid,database=env.db)
    invalid='无失败、无重试。'
    corrected='历史失败次数：3。当前步骤状态仅为快照；人工/操作方情况未知。'
    designer=SimpleNamespace(generate_summary=AsyncMock(return_value=invalid),
                             generate_summary_retry=AsyncMock(return_value=corrected))
    await artifacts._run_report_job(job,env.db,designer)
    metadata=env.db.get_artifact(job['artifact_id'])['metadata']
    assert metadata['status']=='completed' and metadata['narrative_status']=='validated'
    assert metadata['narrative_generation_attempts']==2
    text=env.db.read_artifact_content(metadata['report_artifact_id']).decode()
    assert corrected in text and invalid not in text
    assert env.db.verify_artifact_hash(metadata['report_artifact_id']) is True
    from api.routes.artifact_routes import _report_job_status
    assert _report_job_status(env.db.get_artifact(job['artifact_id']))['narrative_generation_attempts']==2
    designer.generate_summary.assert_awaited_once()
    designer.generate_summary_retry.assert_awaited_once()


@pytest.mark.parametrize('revised',['一次成功。','',None])
async def test_second_invalid_or_empty_narrative_only_delivers_verified_facts(env,revised):
    _,plan,rid=history(env)
    job=artifacts.prepare_final_report(plan,rid,database=env.db)
    designer=SimpleNamespace(generate_summary=AsyncMock(return_value='无失败。'),
                             generate_summary_retry=AsyncMock(return_value=revised))
    await artifacts._run_report_job(job,env.db,designer)
    metadata=env.db.get_artifact(job['artifact_id'])['metadata']
    assert metadata['status']=='completed' and metadata['narrative_status']=='facts_only'
    assert metadata['narrative_generation_attempts']==2
    text=env.db.read_artifact_content(metadata['report_artifact_id']).decode()
    assert '历史 Failed Run：3' in text and '模型补充说明未被采用' in text
    assert '无失败。' not in text and (not revised or revised not in text)
    designer.generate_summary_retry.assert_awaited_once()


async def test_rewrite_shares_one_report_deadline_and_falls_back(env):
    _,plan,rid=history(env)
    job=artifacts.prepare_final_report(plan,rid,database=env.db)
    async def slow_retry(*_args):
        await asyncio.sleep(.2)
        return '历史失败次数：3。'
    designer=SimpleNamespace(generate_summary=AsyncMock(return_value='无失败。'),
                             generate_summary_retry=AsyncMock(side_effect=slow_retry))
    await asyncio.wait_for(artifacts._run_report_job(job,env.db,designer,timeout=.05),1)
    metadata=env.db.get_artifact(job['artifact_id'])['metadata']
    assert metadata['status']=='completed' and metadata['narrative_status']=='facts_only'
    assert metadata['narrative_generation_attempts']==2
    assert env.db.get_execution_run(rid)['status']=='Completed'


async def test_rewrite_cancellation_does_not_claim_report_complete(env):
    _,plan,rid=history(env)
    job=artifacts.prepare_final_report(plan,rid,database=env.db)
    started=asyncio.Event()
    async def pending_retry(*_args):
        started.set()
        await asyncio.Event().wait()
    designer=SimpleNamespace(generate_summary=AsyncMock(return_value='无失败。'),
                             generate_summary_retry=pending_retry)
    task=asyncio.create_task(artifacts._run_report_job(job,env.db,designer))
    await asyncio.wait_for(started.wait(),1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert env.db.get_artifact(job['artifact_id'])['metadata']['status']=='interrupted'
    assert env.db.get_artifact(job['artifact_id'].replace('report-job-','report-',1)) is None


def test_revision_preserves_original_and_explicit_intervention(env):
    _,plan,rid=history(env)
    old=artifacts.prepare_final_report(plan,rid,database=env.db)
    note={'description':'测试预期行号 12 → 13；主程序未修改。','evidence_sha256':'a'*64}
    revised=artifacts.prepare_final_report(plan,rid,database=env.db,revision_id=str(uuid4()),interventions=[note])
    assert revised['artifact_id']!=old['artifact_id']
    assert env.db.get_artifact(old['artifact_id'])==old
    assert revised['metadata']['execution_facts']['manual_interventions']=={'status':'operator_recorded','records':[note]}


async def test_direct_designer_prompt_marks_history_unknown(env):
    from designer import DefaultDesigner
    plan=env.make().plan
    d=DefaultDesigner.__new__(DefaultDesigner)
    d.model_config={'primary':{'provider':'fake','model':'fake'}}
    d._chat_design=AsyncMock(return_value=SimpleNamespace(content='当前结果摘要。'))
    await d.generate_summary(plan,[s for g in plan.segments for s in g.steps])
    messages=d._chat_design.await_args.kwargs['messages']
    assert '未登记不等于未发生' in messages[0].content
    assert 'execution_facts' in messages[1].content and 'unknown' in messages[1].content
    assert '四步成功' not in messages[0].content
    assert '无失败、无重试' not in messages[0].content


async def test_retry_prompt_uses_only_bounded_facts_not_rejected_or_operator_text(env):
    from designer import DefaultDesigner
    from domain.report_truth import capture_facts,report_facts
    plan=env.make().plan
    facts=capture_facts(env.db,plan)
    facts['manual_interventions']={'status':'operator_recorded','records':[
        {'description':'UNTRUSTED_OPERATOR_INJECTION','evidence_sha256':'a'*64}]}
    d=DefaultDesigner.__new__(DefaultDesigner)
    d.model_config={'primary':{'provider':'fake','model':'fake'}}
    d._chat_design=AsyncMock(return_value=SimpleNamespace(content='历史 Failed Run：0。'))
    token=report_facts.set(facts)
    try:
        await d.generate_summary_retry(plan,[s for g in plan.segments for s in g.steps])
    finally:
        report_facts.reset(token)
    kwargs=d._chat_design.await_args.kwargs
    assert kwargs['max_tokens']==768 and kwargs['temperature']==0.0
    assert 'UNTRUSTED_OPERATOR_INJECTION' not in kwargs['messages'][1].content
    assert 'outputs_excerpt' not in kwargs['messages'][1].content
    payload=json.loads(kwargs['messages'][1].content)
    assert payload['manual_records_count']==1 and payload['historical_failed_runs']==0




def test_run_totals_not_truncated_at_list_api_limit(env):
    from contextlib import closing
    from domain.report_truth import capture_facts
    item=env.make()
    with closing(env.db._get_connection()) as conn,conn:
        conn.executemany('INSERT INTO execution_runs (run_id,plan_id,status,trigger,branch_version,started_at,created_at,owner_token) VALUES (?,?,?,?,?,?,?,?)',
                         [(str(uuid4()),item.pid,'Failed','execute',1,'2026-01-01','2026-01-01','secret-owner') for _ in range(505)])
    facts=capture_facts(env.db,item.plan)
    assert facts['runs_total']==505 and facts['run_counts']=={'Failed':505}
    assert len(facts['runs_sample'])==40 and facts['runs_sample_truncated']
    assert 'secret-owner' not in json.dumps(facts)


def test_old_run_cutoff_excludes_later_runs(env):
    from domain.report_truth import capture_facts
    item,plan,rid=history(env,1)
    run=env.db.start_execution_run(item.pid,('Completed',),owner_token='later',branch_version=3)
    env.db.finish_execution_run(run['run_id'],'later','Failed')
    assert capture_facts(env.db,plan,rid)['run_counts']=={'Completed':1,'Failed':1}
    assert capture_facts(env.db,plan)['run_counts']=={'Completed':1,'Failed':2}


@pytest.mark.parametrize('notes',[[],None])
def test_empty_manual_list_never_means_no_intervention(env,notes):
    from domain.report_truth import capture_facts
    assert capture_facts(env.db,env.make().plan,interventions=notes)['manual_interventions']['status']=='unknown'


def test_cross_plan_run_rejected(env):
    from domain.report_truth import capture_facts
    _,_,rid=history(env)
    with pytest.raises(ValueError):capture_facts(env.db,env.make().plan,rid)


@pytest.mark.parametrize('notes',[[{'description':'x','evidence_sha256':'bad'}],[{'description':'x','evidence_sha256':'a'*64,'owner_token':'secret'}]])
def test_invalid_operator_records_rejected(env,notes):
    from domain.report_truth import capture_facts
    with pytest.raises(ValueError):capture_facts(env.db,env.make().plan,interventions=notes)


async def test_legacy_job_does_not_invent_history_from_new_database_state(env):
    _,plan,rid=history(env)
    job=artifacts.prepare_final_report(plan,rid,database=env.db)
    metadata=dict(job['metadata']);metadata.pop('execution_facts')
    env.db.update_artifact_metadata(job['artifact_id'],metadata)
    await artifacts._run_report_job(job,env.db,SimpleNamespace(generate_summary=AsyncMock(return_value='历史情况无法确认。')))
    current=env.db.get_artifact(job['artifact_id'])['metadata']
    text=env.db.read_artifact_content(current['report_artifact_id']).decode()
    assert '历史 Failed Run：未知' in text


async def test_concurrent_reports_keep_facts_context_isolated(env):
    import asyncio
    from domain.report_truth import report_facts
    _,p1,r1=history(env,1);_,p2,r2=history(env,2)
    async def summary(plan,steps):
        before=report_facts.get()
        await asyncio.sleep(.01)
        assert report_facts.get()==before
        assert before['plan_id']==str(plan.plan_id)
        return '仅描述当前步骤。'
    designer=SimpleNamespace(generate_summary=summary)
    await asyncio.gather(*[artifacts._run_report_job(artifacts.prepare_final_report(p,r,database=env.db),env.db,designer) for p,r in [(p1,r1),(p2,r2)]])
    assert report_facts.get() is None
    assert all(j['metadata']['status']=='completed' for j in env.db.list_artifacts() if j['kind']=='delivery_report_job')


@pytest.mark.parametrize('claim',['无\u200b失败','ｎｏ ｆａｉｌｕｒｅｓ','生产就绪','历史失败次数：２'])
def test_guard_normalizes_unicode_and_rejects_known_false_counts(env,claim):
    from domain.report_truth import capture_facts,validate_narrative,ReportTruthError
    _,plan,rid=history(env)
    with pytest.raises(ReportTruthError):validate_narrative(claim,capture_facts(env.db,plan,rid))


def test_completed_snapshot_does_not_mean_current_run_executed_steps(env):
    from domain.report_truth import capture_facts
    _,plan,rid=history(env)
    facts=capture_facts(env.db,plan,rid)
    assert facts['current_steps']=={'Success':1}
    assert facts['current_run_attempts_total']==0
    assert facts['current_run_attempt_counts']=={}


def test_zero_attempt_run_cannot_claim_it_executed_snapshot_steps(env):
    from domain.report_truth import capture_facts,validate_narrative,ReportTruthError
    _,plan,rid=history(env)
    with pytest.raises(ReportTruthError):
        validate_narrative('本次运行 4 个步骤均成功并通过验收。',capture_facts(env.db,plan,rid))
