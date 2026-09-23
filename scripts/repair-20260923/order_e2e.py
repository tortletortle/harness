"""Real order-reconciliation end-to-end task. Separate generate/review/execute stages."""
import ast
import copy
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STAGE=sys.argv[1] if len(sys.argv)>1 else 'generate'
os.environ['EAS_REPAIR_EVIDENCE_DIR']='out/order-e2e-20260923'
sys.argv=[sys.argv[0],os.getenv('EAS_ORDER_CASE','case-1')]
import verify_live as a
from fastapi.testclient import TestClient
import api.server as server
from api.shared import state
import tempfile
temp_root=a.WS/'.tmp'
temp_root.mkdir(exist_ok=True)
for key in ('TMP','TEMP','TMPDIR'):os.environ[key]=str(temp_root)
tempfile.tempdir=str(temp_root)
import httpx
previous_send=httpx.AsyncClient.send
async def completion_observer(self,request,*args,**kwargs):
    response=await previous_send(self,request,*args,**kwargs)
    if '/chat/completions' in request.url.path:
        try:
            body=response.json()
            a.event('model_completion',request_hash=hashlib.sha256(request.content).hexdigest(),
                finish_reasons=[c.get('finish_reason') for c in body.get('choices',[])],
                completion_tokens=(body.get('usage') or {}).get('completion_tokens'))
        except Exception:
            pass
    return response
httpx.AsyncClient.send=completion_observer

config=copy.deepcopy(a.CONFIG)
config['test_runner']={'commands':{
    'reconcile':[sys.executable,'reconcile.py','--input','data/orders.csv','--output','out/orders'],
    'pytest':[sys.executable,'-m','pytest','-c','pytest.ini','test_reconcile.py','-q'],
}}
server._load_models_config=lambda *args,**kwargs:copy.deepcopy(config)
token=os.getenv('EAS_API_TOKEN') or (a.ROOT/'config'/'api_token').read_text().strip()
a.SECRETS.append(token)
headers={'Authorization':'Bearer '+token}


def call(client,method,path,body=None):
    start=time.monotonic()
    response=client.request(method,path,json=body,headers=headers)
    try:data=response.json()
    except ValueError:data={'text':response.text[:1000]}
    if method!='GET':a.event('api',method=method,path=path,status=response.status_code,elapsed_s=round(time.monotonic()-start,3))
    if response.status_code!=200:raise RuntimeError(a.clean(json.dumps({'path':path,'status':response.status_code,'response':data},ensure_ascii=False)))
    return data


def generate():
    assert not (a.RUN/'generated.json').exists(),'Do not overwrite an existing task run.'
    a.setup_git()
    task=(a.ROOT/'docs/order-e2e-task-20260923.md').read_text(encoding='utf-8')
    csv=task.split('```csv\n',1)[1].split('```',1)[0]
    (a.WS/'data').mkdir(exist_ok=True)
    (a.WS/'data/orders.csv').write_text(csv,encoding='utf-8',newline='')
    (a.WS/'pytest.ini').write_text('[pytest]\naddopts = -q\n',encoding='utf-8')
    subprocess.run(['git','add','data/orders.csv','pytest.ini'],cwd=a.WS,check=True,capture_output=True)
    subprocess.run(['git','commit','-m','seed isolated order test fixture'],cwd=a.WS,check=True,capture_output=True)
    user_request=task.split('## 可直接提交给系统的任务正文',1)[1].split('## 独立验收门槛',1)[0]
    with TestClient(server.app,raise_server_exceptions=False) as client:
        design=call(client,'POST','/api/design',{'user_request':user_request})
        did=design['design']['design_id']
        a.save('design.json',design)
        approved=call(client,'POST',f'/api/design/{did}/approve',{'design_id':did,'approved':True})
        pid=approved['plan']['plan_id']
        plan=state.database.get_plan(pid).model_dump(mode='json')
        a.save('plan.json',plan)
        proposed=[]
        for segment in plan['segments']:
            for step in segment['steps']:
                proposed.append({k:step.get(k) for k in ('step_id','description','tool','inputs','depends_on','acceptance')})
        a.save('proposed-steps.json',proposed)
        a.save('generated.json',{'design_id':did,'plan_id':pid,'input_sha256':hashlib.sha256((a.WS/'data/orders.csv').read_bytes()).hexdigest()})
        print(a.clean(json.dumps({'stage':'generated-awaiting-review','plan_id':pid,'steps':len(proposed),'tools':[s['tool'] for s in proposed]},ensure_ascii=False)),flush=True)


def preflight(plan):
    allowed_imports={'argparse','csv','json','decimal','pathlib','re','sys','unittest','tempfile','hashlib','io','collections','typing','pytest','reconcile','os','subprocess'}
    steps=[s for segment in plan['segments'] for s in segment['steps']]
    assert 1<=len(steps)<=6,'Unexpected plan size'
    created=set()
    review=json.loads((a.RUN/'source-review.json').read_text(encoding='utf-8'))
    assert review.get('approved') is True,'Manual source review required'
    for step in steps:
        tool=step['tool'];inputs=step['inputs']
        assert tool in {'file_creator','test_runner'},('Unapproved tool',tool)
        if tool=='file_creator':
            path=Path(inputs['path'])
            assert not path.is_absolute() and '..' not in path.parts,'Only workspace-relative paths are allowed'
            assert path.as_posix() in {'reconcile.py','test_reconcile.py'},path
            created.add(path.as_posix())
            assert hashlib.sha256(inputs['content'].encode()).hexdigest()==review['sha256'][path.as_posix()],'Source changed after review'
            tree=ast.parse(inputs['content'])
            for node in ast.walk(tree):
                if isinstance(node,(ast.Import,ast.ImportFrom)):
                    names=[x.name for x in node.names] if isinstance(node,ast.Import) else [node.module or '']
                    assert all(name.split('.')[0] in allowed_imports for name in names),names
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                    assert node.func.id not in {'eval','exec','compile','__import__'},node.func.id
            assert not any(s in inputs['content'] for s in ('api_token','.env','site-packages','C:/Users','C:\\Users','../','..\\')),'Suspicious external path'
        else:
            assert inputs.get('suite') in {'reconcile','pytest'},inputs
            assert not inputs.get('args'),'Extra execution arguments need review'
    assert created=={'reconcile.py','test_reconcile.py'},created
    return steps


def approve_reviewed():
    """Explicit operator sign-off AFTER reading the proposed source, not an automated safety certificate."""
    assert not (a.RUN/'source-review.json').exists(),'Existing review must not be overwritten'
    plan=json.loads((a.RUN/'plan.json').read_text(encoding='utf-8'))
    digest={}
    for seg in plan['segments']:
        for step in seg['steps']:
            if step['tool']=='file_creator':
                digest[Path(step['inputs']['path']).as_posix()]=hashlib.sha256(step['inputs']['content'].encode()).hexdigest()
    a.save('source-review.json',{'approved':True,'sha256':digest,'notes':['Explicit operator approval via approve-reviewed stage; source must be reviewed before this command.']})


def execute():
    suffix='-'+os.getenv('EAS_ORDER_RECOVERY_TAG','retry') if STAGE=='retry' else ''
    saved=json.loads((a.RUN/'generated.json').read_text(encoding='utf-8'))
    assert not (a.RUN/('execution'+suffix+'.json')).exists(),'Do not overwrite prior execution evidence'
    pid=saved['plan_id']
    with TestClient(server.app,raise_server_exceptions=False) as client:
        plan=state.database.get_plan(pid).model_dump(mode='json')
        preflight(plan)
        if suffix:
            repair=json.loads((a.RUN/'task-repair.json').read_text(encoding='utf-8'))
            assert hashlib.sha256((a.WS/'test_reconcile.py').read_bytes()).hexdigest()==repair['after_sha256']
            assert hashlib.sha256((a.WS/'reconcile.py').read_bytes()).hexdigest()==repair['program_sha256']
            assert plan['status']=='Failed',plan['status']
            assert call(client,'GET','/api/status').get('current_execution') is None
            failed=[s for seg in plan['segments'] for s in seg['steps'] if s['status']=='Failed']
            assert len(failed)==1 and failed[0]['tool']=='test_runner' and failed[0]['inputs']['suite']=='pytest'
            predecessors=[s for seg in plan['segments'] for s in seg['steps'] if s['status']=='Success' and s['order']<failed[0]['order']]
            assert predecessors,'Need a completed checkpoint before the failed step'
            checkpoint=max(predecessors,key=lambda s:s['order'])
            rollback=call(client,'POST',f'/api/plan/{pid}/rollback',{'step_id':checkpoint['step_id'],'strategy':'retry'})
            a.save('rollback'+suffix+'.json',{'checkpoint':checkpoint['step_id'],'failed_step':failed[0]['step_id'],**rollback})
            active=state.database.get_active_steps_snapshot(pid,rollback['new_branch'])
            assert len(active)==1 and active[0]['status']=='Pending','Rollback must create the failed suffix, not an empty branch'
        call(client,'POST',f'/api/plan/{pid}/execute')
        deadline=time.monotonic()+600
        final={}
        while time.monotonic()<deadline:
            final=call(client,'GET',f'/api/plan/{pid}')['plan']
            if final.get('status') in {'Completed','Failed','Paused','RolledBack','PartiallyFailed'}:break
            time.sleep(1)
        a.save('execution'+suffix+'.json',{'plan':final,'runs':call(client,'GET',f'/api/plan/{pid}/runs'),
            'status':call(client,'GET','/api/status')})
        deadline=time.monotonic()+180
        while final.get('status')=='Completed' and time.monotonic()<deadline:
            jobs=[j for j in state.database.list_artifacts(pid) if j['kind']=='delivery_report_job']
            if jobs and all(j['metadata'].get('status') in {'completed','failed'} for j in jobs):break
            time.sleep(.5)
        records=state.database.list_artifacts(pid)
        a.save('artifacts'+suffix+'.json',records)
        a.save('report-verification'+suffix+'.json',[{'artifact_id':j['artifact_id'],'hash_valid':state.database.verify_artifact_hash(j['artifact_id'])} for j in records if j['kind']=='delivery_report'])
        print(a.clean(json.dumps({'plan_status':final.get('status'),'artifacts':len(records)},ensure_ascii=False)),flush=True)
        return final.get('status')=='Completed'


if __name__=='__main__':
    try:
        a.inventory()
        if STAGE=='generate':generate()
        elif STAGE=='approve-reviewed':approve_reviewed()
        elif STAGE in ('execute','retry'):raise SystemExit(0 if execute() else 1)
        else:raise ValueError('Unsupported stage')
    except Exception as exc:
        a.save(STAGE+'-error.json',{'type':type(exc).__name__,'error':a.clean(exc)})
        raise
