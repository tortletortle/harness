"""Real report single-flight validation; delegates to real configured LLM, no response stubs."""
import asyncio
import json
import sys
import time

sys.argv = [sys.argv[0], 'single-flight']
import verify_live as a
from api.runtime import artifacts
from database import Database
from designer import DefaultDesigner
from llm_client import LLMClient, OpenAICompatibleProvider


async def main():
    a.inventory()
    sys.path.insert(0, str(a.ROOT/'tests'/'python'))
    from test_pause_resume_retry import _new_plan_with_steps
    db = Database(str(a.RUN/'eas.db'))
    path = a.WS/'proof.txt'
    path.write_text('single-flight fixture', encoding='utf-8')
    pid, steps = _new_plan_with_steps(db,a.WS,[('file_creator',{'path':str(path),'content':'single-flight fixture'},'manual')])
    db.update_step(steps[0],'Success')
    db.update_plan_state_machine(str(pid),'Completed')
    job = artifacts.prepare_final_report(db.get_plan(str(pid)),database=db)
    client = LLMClient()
    config = a.CONFIG['models']['designer']
    for name in {config[t]['provider'] for t in ('primary','fallback') if config.get(t)}:
        client.register_provider(OpenAICompatibleProvider(name,a.CONFIG['api_keys'][name],a.CONFIG['base_urls'][name]))
    designer = DefaultDesigner(client, config)
    actual_summary = designer.generate_summary
    calls = 0
    async def observed_summary(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await actual_summary(*args, **kwargs)
    designer.generate_summary = observed_summary
    start = time.monotonic()
    await asyncio.gather(*[artifacts._run_report_job(job,db,designer) for _ in range(8)])
    metadata = db.get_artifact(job['artifact_id'])['metadata']
    reports = [r for r in db.list_artifacts(str(pid)) if r['kind']=='delivery_report']
    verified = len(reports)==1 and db.verify_artifact_hash(reports[0]['artifact_id']) is True
    rows = [json.loads(line) for line in (a.RUN/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    result = {'passed':calls==1 and metadata['status']=='completed' and metadata['attempts']==1 and verified,
              'concurrent_callers':8,'actual_summary_invocations':calls,
              'report_status':metadata['status'],'report_attempts':metadata['attempts'],
              'artifact_count':len(reports),'artifact_hash_valid':verified,
              'http_requests':sum(r['kind']=='llm_request' for r in rows),
              'elapsed_s':round(time.monotonic()-start,3),
              'scope':'Synthetic completed-plan fixture for report-only concurrency; summary uses real configured providers, not HTTP mocks.'}
    a.save('single-flight.json',result)
    print(a.clean(json.dumps(result,ensure_ascii=False,indent=2)),flush=True)
    return result


if __name__=='__main__':
    try:
        result = asyncio.run(main())
    except BaseException as exc:
        a.save('runner-error.json',{'type':type(exc).__name__,'error':a.clean(exc)})
        raise
    raise SystemExit(0 if result['passed'] else 1)
