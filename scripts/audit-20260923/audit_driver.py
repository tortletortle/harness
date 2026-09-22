"""Isolated audit runner. Uses actual configured providers, never deterministic stubs."""
import asyncio, hashlib, json, os, re, subprocess, sys, threading, time, traceback
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
ROOT = Path(__file__).resolve().parents[2]
MODE = sys.argv[1] if len(sys.argv)>1 else 'probe'
BASE = ROOT/'out'/'audit-20260923'
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
    except Exception as exc:
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
async def probe():
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
                response=await asyncio.wait_for(client.chat([Message(role='user',content='Reply with exactly AUDIT_OK. Do not call tools.')],model=model,temperature=0,max_tokens=64),timeout=75)
                row.update(passed=response.content.strip()=='AUDIT_OK',content=response.content,usage=response.usage,finish_reason=response.finish_reason)
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

if __name__=='__main__':
    started=time.monotonic()
    try:
        inventory()
        if MODE=='probe': result=asyncio.run(probe())
        elif MODE=='live-b': result=live_b()
        else: raise ValueError(MODE)
        save('run-status.json',{'completed':True,'elapsed_s':round(time.monotonic()-started,2)})
    except BaseException as exc:
        save('run-status.json',{'completed':False,'elapsed_s':round(time.monotonic()-started,2),'type':type(exc).__name__,'error':clean(exc),'traceback':clean(traceback.format_exc())}); raise
