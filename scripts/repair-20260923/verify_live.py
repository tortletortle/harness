"""Isolated audit runner. Uses actual configured providers, never deterministic stubs."""
import asyncio, hashlib, json, os, re, subprocess, sys, threading, time, traceback
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
ROOT = Path(__file__).resolve().parents[2]
MODE = sys.argv[1] if len(sys.argv)>1 else 'probe'
BASE = ROOT/'out'/'repair-20260923'
RUN = BASE/MODE
RUN.mkdir(parents=True, exist_ok=True)
WS = RUN/'workspace'
WS.mkdir(exist_ok=True)
os.environ.update(EAS_DB_PATH=str(RUN/'eas.db'), EAS_LOG_DIR=str(RUN/'logs'), EAS_WORKSPACE_ROOT=str(WS), PYTHONIOENCODING='utf-8')
sys.path.insert(0,str(ROOT/'python'))
sys.path.insert(0,str(ROOT/'scripts'))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
import cache
cache.DesignCache.DEFAULT_CACHE_FILE=RUN/'design_cache.json'
cache.PlanCache.DEFAULT_CACHE_FILE=RUN/'plan_cache.json'
from api.server import _load_models_config
CONFIG=_load_models_config()
SECRETS=[str(v) for v in CONFIG.get('api_keys',{}).values() if v]
SECRETS += [v for k,v in os.environ.items() if any(s in k.upper() for s in ('TOKEN','SECRET','API_KEY')) and len(v)>6]
def clean(value):
    text=str(value)
    for secret in SECRETS: text=text.replace(secret,'[REDACTED]')
    text=re.sub(r'Bearer\s+[^\s\"\']+', 'Bearer [REDACTED]',text)
    text=re.sub(r'sk-[A-Za-z0-9_-]{8,}','[REDACTED]',text)
    return text
LOCK=threading.Lock()
def event(kind, **values):
    row={'time':datetime.now(timezone.utc).isoformat(),'kind':kind,**values}
    with LOCK:
        with (RUN/'events.jsonl').open('a',encoding='utf-8') as f: f.write(clean(json.dumps(row,ensure_ascii=False,default=str))+'\n')
def save(name,data): (RUN/name).write_text(clean(json.dumps(data,ensure_ascii=False,indent=2,default=str)),encoding='utf-8')
# Observe the real network send; do not fabricate/replace any responses.
import httpx
original_send=httpx.AsyncClient.send
async def observed_send(self,request,*args,**kwargs):
    if '/chat/completions' not in request.url.path: return await original_send(self,request,*args,**kwargs)
    t=time.monotonic(); payload=json.loads(request.content)
    meta={'host':request.url.host,'path':request.url.path,'requested_model':payload.get('model'),'max_tokens':payload.get('max_tokens'),'request_hash':hashlib.sha256(request.content).hexdigest()}
    event('llm_request',**meta)
    try:
        response=await original_send(self,request,*args,**kwargs)
        try: body=response.json()
        except Exception: body={}
        event('llm_response',**meta,status=response.status_code,elapsed_s=round(time.monotonic()-t,3),actual_model=body.get('model'),response_id=body.get('id'),usage=body.get('usage'),error=body.get('error'),response_hash=hashlib.sha256(response.content).hexdigest())
        return response
    except BaseException as exc:
        event('llm_transport_error',**meta,elapsed_s=round(time.monotonic()-t,3),error_type=type(exc).__name__,error=clean(exc)); raise
httpx.AsyncClient.send=observed_send

def setup_git():
    if not (WS/'README.md').exists(): (WS/'README.md').write_text('Audit isolated fixture\n',encoding='utf-8')
    if not (WS/'.git').exists():
        for args in [['init'],['add','README.md'],['-c','user.name=Harness Audit','-c','user.email=audit@example.invalid','commit','-m','isolated fixture']]:
            subprocess.run(['git',*args],cwd=WS,check=True,capture_output=True)
    subprocess.run(['git','config','user.name','Harness Audit'],cwd=WS,check=True)
    subprocess.run(['git','config','user.email','audit@example.invalid'],cwd=WS,check=True)

def inventory():
    data={'roles':CONFIG.get('models',{}),'providers':[{'name':p,'host':urlsplit(u).hostname,'credential_present':bool(CONFIG.get('api_keys',{}).get(p))} for p,u in CONFIG.get('base_urls',{}).items()]}
    save('config-summary.json',data); print(clean(json.dumps(data,ensure_ascii=False,indent=2)),flush=True)
    return data
async def probe(prompt='Reply with exactly AUDIT_OK. Do not call tools.', expected='AUDIT_OK'):
    from llm_client import OpenAICompatibleProvider, Message
    results=[]; seen=set()
    profiles=[(role+'.'+tier,spec) for role,group in CONFIG.get('models',{}).items() if role in ('designer','executor','codegen','knowledge','toolmanager','stepfun_chat') for tier,spec in group.items() if isinstance(spec,dict)]
    for role,spec in profiles:
        provider=spec.get('provider'); model=spec.get('model')
        if not provider or not model or (provider,model) in seen: continue
        seen.add((provider,model))
        row={'role':role,'provider':provider,'model':model}
        key=CONFIG.get('api_keys',{}).get(provider)
        if not key: row.update(passed=False,error='credential missing')
        else:
            client=OpenAICompatibleProvider(provider,key,CONFIG['base_urls'][provider]); t=time.monotonic()
            try:
                response=await asyncio.wait_for(client.chat([Message(role='user',content=prompt)],model=model,temperature=0,max_tokens=64),timeout=75)
                row.update(passed=response.content.strip()==expected,content=response.content,usage=response.usage,finish_reason=response.finish_reason)
            except Exception as exc: row.update(passed=False,error_type=type(exc).__name__,error=clean(exc))
            row['elapsed_s']=round(time.monotonic()-t,3)
        results.append(row); save('probe.json',results); print(clean(json.dumps(row,ensure_ascii=False)),flush=True)
    return results

def live_b():
    setup_git()
    from fastapi.testclient import TestClient
    from api.server import app,state
    import real_llm_acceptance_b as b
    from uuid import uuid4
    b._RUN_SEQ['n']=int(time.time())
    with TestClient(app,raise_server_exceptions=False) as client:
        b._CLIENT=client
        token=os.getenv('EAS_API_TOKEN') or (ROOT/'config'/'api_token').read_text().strip()
        os.environ['EAS_API_TOKEN']=token; SECRETS.append(token)
        checks=[]
        for method,path,body,auth in [('GET','/',None,False),('GET','/api/plans',None,False),('GET','/api/plans',None,True),('GET','/api/plan/'+str(uuid4()),None,True),('POST','/api/design',{},True)]:
            r=client.request(method,path,json=body,headers={'Authorization':'Bearer '+token} if auth else {})
            checks.append({'method':method,'path':path,'authenticated':auth,'status':r.status_code,'body':r.text[:700]})
        save('api-smoke.json',checks)
        original_request=b.request
        def logged_request(method,path,body=None,**kw):
            t=time.monotonic(); code,data=original_request(method,path,body,**kw)
            event('api',method=method,path=path,status=code,elapsed_s=round(time.monotonic()-t,3),response=data)
            return code,data
        b.request=logged_request
        evidence=b.run_acceptance(180); save('acceptance-b.json',evidence)
        print(clean(json.dumps(evidence,ensure_ascii=False,indent=2)),flush=True)
        # Direct file content checks supplement upstream status-only acceptance.
        save('workspace-files.json',[{'path':str(p.relative_to(WS)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'content':p.read_text(encoding='utf-8',errors='replace')[:1500]} for p in WS.rglob('*') if p.is_file() and '.git' not in p.parts and p.suffix in ('.txt','.md','.py')])
        return evidence

def ownership_check():
    setup_git()
    from fastapi.testclient import TestClient
    from api.server import app,state
    sys.path.insert(0,str(ROOT/'tests'/'python'))
    from test_pause_resume_retry import _new_plan_with_steps
    token=os.getenv('EAS_API_TOKEN') or (ROOT/'config'/'api_token').read_text().strip()
    SECRETS.append(token)
    headers={'Authorization':'Bearer '+token}
    report={}
    with TestClient(app,raise_server_exceptions=False) as client:
        p1,_=_new_plan_with_steps(state.database,WS,[('file_creator',{'path':str(WS/'first.txt'),'content':'first'},'手动检查')])
        p2,_=_new_plan_with_steps(state.database,WS,[('file_creator',{'path':str(WS/'second.txt'),'content':'second'},'手动检查')])
        start=time.monotonic()
        r=client.post(f'/api/plan/{p1}/execute',headers=headers)
        report.update(first_plan=str(p1),second_plan=str(p2),execute_status=r.status_code)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            plan=client.get(f'/api/plan/{p1}',headers=headers).json().get('plan',{})
            if plan.get('status')=='Completed': break
            time.sleep(.15)
        report['plan_status']=plan.get('status'); report['completed_after_s']=round(time.monotonic()-start,3)
        report['status_after_completed']=client.get('/api/status',headers=headers).json()
        report['runs_after_completed']=client.get(f'/api/plan/{p1}/runs',headers=headers).json()
        r=client.post(f'/api/plan/{p2}/execute',headers=headers)
        report['next_execute_status']=r.status_code; report['next_execute_body']=r.json()
        save('ownership.json',report)
        deadline=time.monotonic()+110
        while time.monotonic()<deadline:
            status=client.get('/api/status',headers=headers).json()
            if not status.get('current_execution'): break
            time.sleep(1)
        report['final_status']=status; report['idle_after_s']=round(time.monotonic()-start,3)
        deadline=time.monotonic()+220
        while time.monotonic()<deadline:
            jobs=[a for a in state.database.list_artifacts() if a['kind']=='delivery_report_job']
            if len(jobs)==2 and all(a['metadata']['status'] in ('completed','failed') for a in jobs): break
            time.sleep(.25)
        report['reports']=[{'id':a['artifact_id'],'status':a['metadata']['status'],'attempts':a['metadata']['attempts']} for a in jobs]
        report['passed']=report['plan_status']=='Completed' and report['next_execute_status']==200 and len(jobs)==2 and all(a['metadata']['status']=='completed' for a in jobs)
        save('ownership.json',report)
    return report

async def contracts():
    from llm_client import OpenAICompatibleProvider, Message
    async def one(name,spec,prompt,tools=None):
        row={'name':name,'provider':spec['provider'],'model':spec['model']}; start=time.monotonic()
        provider=OpenAICompatibleProvider(spec['provider'],CONFIG['api_keys'][spec['provider']],CONFIG['base_urls'][spec['provider']])
        try:
            response=await asyncio.wait_for(provider.chat([Message(role='user',content=prompt)],model=spec['model'],temperature=0,max_tokens=512,**({'tools':tools} if tools else {})),timeout=100)
            row.update(content=response.content,tool_calls=response.tool_calls,usage=response.usage,finish_reason=response.finish_reason)
            if tools:
                calls=response.tool_calls or []
                row['passed']=bool(calls) and calls[0].get('function',{}).get('name')=='audit_sum' and json.loads(calls[0]['function']['arguments'])=={'a':2,'b':2}
            else: row['passed']='4' in response.content
        except Exception as exc: row.update(passed=False,error_type=type(exc).__name__,error=clean(exc))
        row['elapsed_s']=round(time.monotonic()-start,3); event('contract_result',**row)
        return row
    group=CONFIG['models']['designer']
    tools=[{'type':'function','function':{'name':'audit_sum','description':'Add two integer values. Use this function whenever asked to compute their sum.','parameters':{'type':'object','properties':{'a':{'type':'integer'},'b':{'type':'integer'}},'required':['a','b']}}}]
    results=await asyncio.gather(one('primary_simple_math',group['primary'],'2 + 2 等于多少？'),one('primary_tool_call',group['primary'],'Please use audit_sum to calculate the sum of 2 and 2.',tools),one('fallback_tool_call',group['fallback'],'Please use audit_sum to calculate the sum of 2 and 2.',tools))
    save('contracts.json',results)
    return results

def ui_smoke():
    setup_git()
    from api.server import app
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    import uvicorn
    from playwright.sync_api import sync_playwright
    app.mount('/assets',StaticFiles(directory=ROOT/'frontend'/'dist'/'assets'),name='audit-assets')
    @app.get('/ui')
    def audit_ui(): return FileResponse(ROOT/'frontend'/'dist'/'index.html')
    config=uvicorn.Config(app,host='127.0.0.1',port=18023,log_level='warning',access_log=False)
    server=uvicorn.Server(config)
    thread=threading.Thread(target=server.run,daemon=True); thread.start()
    deadline=time.monotonic()+40
    while not server.started and thread.is_alive() and time.monotonic()<deadline: time.sleep(.2)
    if not server.started: raise RuntimeError('audit HTTP server startup timeout')
    report={'checks':[],'page_errors':[],'console_errors':[],'failed_requests':[],'http_errors':[]}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror',lambda e:report['page_errors'].append(clean(e)))
            page.on('console',lambda m:report['console_errors'].append(clean(m.text)) if m.type=='error' else None)
            page.on('requestfailed',lambda r:report['failed_requests'].append({'url':urlsplit(r.url).path,'failure':r.failure}))
            page.on('response',lambda r:report['http_errors'].append({'path':urlsplit(r.url).path,'status':r.status}) if r.status>=400 else None)
            page.goto('http://127.0.0.1:18023/ui',wait_until='networkidle')
            page.get_by_role('heading',name='描述你想要构建的东西').wait_for()
            report['checks'].append({'name':'app_render','passed':True})
            page.screenshot(path=str(RUN/'01-home.png'),full_page=True)
            page.get_by_placeholder('描述你想要实现的功能和技术要求...').fill('读取 README.md')
            page.get_by_role('button',name='快速执行',exact=True).click()
            page.get_by_text('Audit isolated fixture',exact=False).first.wait_for(timeout=20000)
            report['checks'].append({'name':'quick_read_real_api','passed':True})
            page.screenshot(path=str(RUN/'02-quick-task.png'),full_page=True)
            for label in ['设计书','执行计划','实时执行','产物库','Agent 记录','工具库','历史记录','审计日志','设置']:
                page.get_by_role('button',name=label,exact=True).first.click()
                page.wait_for_timeout(400)
                report['checks'].append({'name':'navigation_'+label,'passed':page.locator('body').inner_text().strip()!=''})
            page.screenshot(path=str(RUN/'03-settings.png'),full_page=True)
            page.get_by_role('button',name='新建任务',exact=True).click()
            page.get_by_placeholder('描述你想要实现的功能和技术要求...').fill('读取 audit-missing-file.txt')
            page.get_by_role('button',name='快速执行',exact=True).click()
            page.wait_for_timeout(1500)
            report['missing_file_text']=page.locator('body').inner_text()[-4500:]
            page.screenshot(path=str(RUN/'04-error-state.png'),full_page=True)
            report['service_online']=page.get_by_text('服务在线',exact=True).count()>0
            browser.close()
    except Exception as exc:
        report['error']=clean(exc); report['traceback']=clean(traceback.format_exc())
    finally:
        server.should_exit=True; thread.join(timeout=20)
        report['server_stopped']=not thread.is_alive()
        report['passed']=not report.get('error') and not report['page_errors'] and not report['http_errors'] and report.get('service_online',False)
        save('ui-smoke.json',report)
    return report


async def live_fallback():
    from llm_client import LLMClient, OpenAICompatibleProvider, Message
    from designer import DefaultDesigner
    client=LLMClient()
    for name in ('stepfun',):
        client.register_provider(OpenAICompatibleProvider(name,CONFIG['api_keys'][name],CONFIG['base_urls'][name]))
    # Controlled *configuration* fault, not a mocked HTTP response. The denied
    # primary is called on the real provider; production files remain unchanged.
    model_config={'primary':{'provider':'stepfun','model':'step-3.5-flash','temperature':0},
                  'fallback':{'provider':'stepfun','model':'qwen3.8-max','temperature':0}}
    designer=DefaultDesigner(client,model_config)
    response=await designer._chat_design(provider_name='stepfun',model='step-3.5-flash',
        messages=[Message(role='user',content='Reply with exactly REPAIR_FALLBACK_OK.')],temperature=0,max_tokens=64)
    result={'passed':response.content.strip()=='REPAIR_FALLBACK_OK', 'content':response.content,
            'returned_model':response.model,'scenario':'real denied primary -> configured automatic fallback; not a 429 proof'}
    save('fallback.json',result)
    return result


def shutdown_recovery():
    setup_git()
    import copy
    from fastapi.testclient import TestClient
    import api.server as server
    from api.shared import state
    sys.path.insert(0,str(ROOT/'tests'/'python'))
    from test_pause_resume_retry import _new_plan_with_steps
    config=copy.deepcopy(CONFIG)
    config['reports']={'shutdown_grace_seconds':0.05, 'timeout_seconds':180}
    server._load_models_config=lambda *a,**kw:copy.deepcopy(config)
    token=os.getenv('EAS_API_TOKEN') or (ROOT/'config'/'api_token').read_text().strip()
    SECRETS.append(token)
    headers={'Authorization':'Bearer '+token}
    with TestClient(server.app,raise_server_exceptions=False) as client:
        pid,_=_new_plan_with_steps(state.database,WS,[('file_creator',{'path':str(WS/'shutdown.txt'),'content':'done'},'手动检查')])
        assert client.post(f'/api/plan/{pid}/execute',headers=headers).status_code==200
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            events=(RUN/'events.jsonl').read_text(encoding='utf-8') if (RUN/'events.jsonl').exists() else ''
            if '"kind": "llm_request"' in events: break
            time.sleep(.1)
        assert '"kind": "llm_request"' in events, 'No real report request began'
    jobs=[a for a in state.database.list_artifacts(str(pid)) if a['kind']=='delivery_report_job']
    before=[a['metadata']['status'] for a in jobs]
    assert before==['interrupted'], before
    with TestClient(server.app,raise_server_exceptions=False):
        deadline=time.monotonic()+210
        while time.monotonic()<deadline:
            jobs=[a for a in state.database.list_artifacts(str(pid)) if a['kind']=='delivery_report_job']
            if all(a['metadata']['status'] in ('completed','failed') for a in jobs): break
            time.sleep(.25)
        after=[a['metadata']['status'] for a in jobs]
        result={'passed':after==['completed'] and jobs[0]['metadata']['attempts']==2,
                'before_restart':before,'after_restart':after,'attempts':jobs[0]['metadata']['attempts'],
                'run_status':state.database.list_execution_runs(str(pid))[0]['status'],
                'shutdown_grace_seconds_for_test':0.05}
        save('shutdown.json',result)
    return result

if __name__=='__main__':
    started=time.monotonic()
    try:
        inventory()
        if MODE=='probe': result=asyncio.run(probe())
        elif MODE=='normal-probe': result=asyncio.run(probe('What is two plus two? Please answer with the number only.', '4'))
        elif MODE=='live-b': result=live_b()
        elif MODE=='live-b-fallback':
            import copy
            import api.server as server_module
            effective=copy.deepcopy(CONFIG)
            for role in ('designer','executor','codegen','toolmanager','knowledge'):
                group=effective.get('models',{}).get(role,{})
                if group.get('fallback'): group['primary']=copy.deepcopy(group['fallback'])
            server_module._load_models_config=lambda *a,**kw:copy.deepcopy(effective)
            save('routing-override.json',{'scope':'this audit process only; no config files modified','effective_models':effective['models']})
            result=live_b()
        elif MODE=='ui': result=ui_smoke()
        elif MODE=='ownership': result=ownership_check()
        elif MODE=='fallback': result=asyncio.run(live_fallback())
        elif MODE=='shutdown': result=shutdown_recovery()
        elif MODE=='contracts': result=asyncio.run(contracts())
        elif MODE=='checkpoint':
            import live_checkpoint_acceptance as cp
            result=asyncio.run(cp._run(RUN/'checkpoint.json'))
        elif MODE=='live-c':
            setup_git()
            import real_llm_acceptance_c as c
            token=os.getenv('EAS_API_TOKEN') or (ROOT/'config'/'api_token').read_text().strip()
            SECRETS.append(token)
            c._credentials=lambda: token
            (RUN/'out').mkdir(exist_ok=True)
            os.chdir(RUN)
            sys.argv=['real_llm_acceptance_c.py','--workspace',str(WS),'--timeout','180']
            result=c.main()
        else: raise ValueError(MODE)
        validation_passed = result.get('passed',False) if isinstance(result,dict) else all(r.get('passed',False) for r in result) if isinstance(result,list) else result == 0
        save('run-status.json',{'completed':True,'validation_passed':validation_passed,'elapsed_s':round(time.monotonic()-started,2)})
    except BaseException as exc:
        save('run-status.json',{'completed':False,'elapsed_s':round(time.monotonic()-started,2),'type':type(exc).__name__,'error':clean(exc),'traceback':clean(traceback.format_exc())}); raise
    if not validation_passed:
        raise SystemExit(1)
