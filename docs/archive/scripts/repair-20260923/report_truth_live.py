"""Two genuine provider reports from a read-only backup of the real order run."""
import asyncio,hashlib,json,os,shutil,sqlite3,sys
from pathlib import Path
from uuid import uuid4
os.environ['EAS_REPAIR_EVIDENCE_DIR']='out/report-truth-20260923'
sys.argv=[sys.argv[0],os.getenv('EAS_REPORT_TRUTH_BATCH','live')]
import verify_live as a
from database import Database
from designer import DefaultDesigner
from llm_client import LLMClient,OpenAICompatibleProvider
from api.runtime.artifacts import prepare_final_report,_run_report_job
import httpx
previous=httpx.AsyncClient.send
async def observe(self,request,*args,**kwargs):
    response=await previous(self,request,*args,**kwargs)
    if '/chat/completions' in request.url.path:
        req=json.loads(request.content);body=response.json()
        a.event('report_content',request_hash=hashlib.sha256(request.content).hexdigest(),
                messages=req.get('messages'),choices=body.get('choices'))
    return response
httpx.AsyncClient.send=observe

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

async def main():
    source=a.ROOT/'out/order-e2e-20260923/case-2'
    assert not (a.RUN/'eas.db').exists(),'New report verification must not overwrite existing evidence'
    watched=[source/'eas.db',source/'workspace/reconcile.py',source/'workspace/test_reconcile.py',
             source/'workspace/data/orders.csv',*sorted((source/'artifacts').glob('report-*'))]
    before={str(p.relative_to(source)):digest(p) for p in watched}
    with sqlite3.connect((source/'eas.db').resolve().as_uri()+'?mode=ro',uri=True) as src:
        with sqlite3.connect(a.RUN/'eas.db') as dest:src.backup(dest)
    shutil.copytree(source/'artifacts',a.RUN/'artifacts',dirs_exist_ok=True)
    a.save('source-integrity-before.json',before)
    task_repair=json.loads((source/'task-repair.json').read_text(encoding='utf-8'))
    assert digest(source/'workspace/reconcile.py')==task_repair['program_sha256']
    assert digest(source/'workspace/test_reconcile.py')==task_repair['after_sha256']
    assert digest(source/'test_reconcile.before.py')==task_repair['before_sha256']
    a.save('operator-evidence.json',task_repair)
    note={'description':'测试执行器在模型计划外将 test_reconcile.py 的 invalid_status 行号预期由 12 改为 13；订单主程序未改。这是操作方登记，不表示模型自主修复。',
          'evidence_sha256':digest(a.RUN/'operator-evidence.json')}
    db=Database(str(a.RUN/'eas.db'))
    pid='79d1ac84-6a2c-467b-b990-eba2b4a8ae58'
    plan=db.get_plan(pid)
    assert plan.status.value=='Completed' and all(s.status.value=='Success' for g in plan.segments for s in g.steps)
    runs=db.list_execution_runs(pid)
    rid=max(runs,key=lambda r:r['started_at'])['run_id']
    assert len(runs)==5 and sum(r['status']=='Failed' for r in runs)==3
    client=LLMClient()
    config=a.CONFIG['models']['designer']
    for name in {v['provider'] for v in config.values() if isinstance(v,dict) and 'provider' in v}:
        client.register_provider(OpenAICompatibleProvider(name,a.CONFIG['api_keys'][name],a.CONFIG['base_urls'][name]))
    designer=DefaultDesigner(client,config)
    a.inventory()
    cases=[]
    for mode,notes in [('unknown',None),('recorded',[note])]:
        job=prepare_final_report(plan,rid,database=db,workspace=a.WS,revision_id=str(uuid4()),interventions=notes)
        await _run_report_job(job,db,designer,timeout=180)
        current=db.get_artifact(job['artifact_id']);metadata=current['metadata']
        row={'mode':mode,'job':current,'passed':False}
        if metadata.get('status')=='completed':
            report_id=metadata['report_artifact_id']
            content=db.read_artifact_content(report_id)
            (a.RUN/(mode+'-report.md')).write_bytes(content)
            row.update(report=db.get_artifact(report_id),hash_valid=db.verify_artifact_hash(report_id))
            facts=metadata['execution_facts']
            row['passed']=(row['hash_valid'] is True and facts['run_counts']=={'Completed':2,'Failed':3}
                           and facts['current_steps']=={'Success':4}
                           and facts['current_run_attempts_total']==0
                           and facts['manual_interventions']['status']==('unknown' if notes is None else 'operator_recorded'))
        cases.append(row);a.save('results.json',cases)
    after={str(p.relative_to(source)):digest(p) for p in watched}
    a.save('source-integrity-after.json',after)
    assert before==after,'Original source evidence was changed'
    assert len(db.list_execution_runs(pid))==5,'Report-only test must not create execution Runs'
    a.save('verification.json',{'passed':all(c['passed'] for c in cases),'source_unchanged':True,
                             'original_run_count':5,'failed_runs':3,'current_steps_success':4,
                             'operator_evidence_hash':note['evidence_sha256'],
                             'scope':'Report generation only, from an isolated SQLite backup. No tools rerun.'})
    print(json.dumps({'cases':[{k:r.get(k) for k in ('mode','passed','hash_valid')} for r in cases],
                      'source_unchanged':True},ensure_ascii=False),flush=True)
    return all(c['passed'] for c in cases)

if __name__=='__main__':raise SystemExit(0 if asyncio.run(main()) else 1)
