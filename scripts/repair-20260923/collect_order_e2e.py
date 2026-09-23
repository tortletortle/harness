"""Sanitized archive preserving both the failed first run and subsequent verification."""
import collections
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
os.environ['EAS_REPAIR_EVIDENCE_DIR']='out/order-e2e-20260923'
sys.argv=[sys.argv[0],'collect']
import verify_live as a
base=a.BASE

def text(path):
    raw=path.read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig',errors='replace')

token=a.ROOT/'config/api_token'
if token.exists():a.SECRETS.append(token.read_text().strip())
summary={'captured_at':a.datetime.now(a.timezone.utc).isoformat(),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=a.ROOT).decode().strip(),
         'tests':{},'cases':{},'llm':{}}
for name in ('before','focused','backend','recovery-before','recovery-focused','backend-final','checkpoint-before','checkpoint-focused','backend-verified'):
    root=ET.parse(base/(name+'.xml'))
    cases=list(root.iter('testcase'))
    summary['tests'][name]={'passed':sum(not any(c.find(t) is not None for t in ('failure','error','skipped')) for c in cases),
        'skipped':sum(c.find('skipped') is not None for c in cases),
        'failures':[{'name':c.get('name'),'message':e.get('message')} for c in cases for e in c if e.tag in ('failure','error')],
        'log_summary':text(base/(name+'.log')).splitlines()[-1]}
rows=[]
for case in ('case-1','case-2'):
    folder=base/case
    info={}
    for name in ('generate-error.json','execute-error.json','generated.json','execution.json','report-verification.json','oracle.json','execution-retry.json','report-verification-retry.json','oracle-retry.json','task-repair.json','rollback.json','execution-retry2.json','report-verification-retry2.json','oracle-retry2.json','rollback-retry2.json','execution-retry3.json','report-verification-retry3.json','oracle-retry3.json','rollback-retry3.json'):
        p=folder/name
        if p.exists():info[name]=json.loads(text(p))
    summary['cases'][case]=info
    for line in text(folder/'events.jsonl').splitlines():
        rows.append({**json.loads(line),'case':case})
requests=collections.Counter((r['case'],r['request_hash']) for r in rows if r['kind']=='llm_request')
terminals=collections.Counter((r['case'],r['request_hash']) for r in rows if r['kind'] in ('llm_response','llm_transport_error'))
responses=[r for r in rows if r['kind']=='llm_response']
summary['llm']={'requests':sum(requests.values()),'terminals':sum(terminals.values()),'missing_terminals':sum((requests-terminals).values()),
    'http_statuses':dict(collections.Counter(str(r['status']) for r in responses)),
    'reported_tokens':sum((r.get('usage') or {}).get('total_tokens',0) for r in responses),
    'transport_errors':[r for r in rows if r['kind']=='llm_transport_error'],
    'truncated_completions':[r for r in rows if r['kind']=='model_completion' and any(x in ('length','max_tokens') for x in r['finish_reasons'])],
    'scope':'Instrumented calls only; includes failed first generation; not a bill.'}
manifest=json.loads(text(base/'patch-manifest.json'))
for f in manifest['files']:
    assert hashlib.sha256((a.ROOT/f['path']).read_text(encoding='utf-8').replace('\r\n','\n').encode()).hexdigest()==f['after_sha256_lf'],f['path']
summary['patch']=manifest
summary['end_to_end_passed']=summary['cases']['case-2'].get('oracle-retry3.json',{}).get('passed',False)
(base/'summary.json').write_text(a.clean(json.dumps(summary,ensure_ascii=False,indent=2)),encoding='utf-8')
(base/'llm-evidence.json').write_text(a.clean(json.dumps(rows,ensure_ascii=False,indent=2)),encoding='utf-8')
with zipfile.ZipFile(base/'evidence.zip','w',zipfile.ZIP_DEFLATED) as z:
    def put(name,content):
        if name!='order-e2e.patch' and not name.startswith('source/'):
            content=a.clean(content)
        assert not any(s and len(s)>6 and s in content for s in a.SECRETS),name
        z.writestr(name,content)
    for name in ('summary.json','llm-evidence.json','patch-manifest.json','order-e2e.patch'):
        put(name,text(base/name))
    for pattern in ('*.xml','*.log'):
        for p in base.glob(pattern):put(p.name,text(p))
    for case in ('case-1','case-2'):
        folder=base/case
        for p in folder.iterdir():
            if p.is_file() and (p.suffix in ('.json','.jsonl','.xml') or p.name=='test_reconcile.before.py'):
                put(case+'/'+p.name,text(p))
        for name in ('reconcile.py','test_reconcile.py','data/orders.csv','data/oracle-unseen.csv'):
            p=folder/'workspace'/name
            if p.is_file():put(case+'/workspace/'+name,text(p))
        for pattern in ('workspace/out/orders/*','workspace/out/oracle-unseen/*','artifacts/report-*'):
            for p in folder.glob(pattern):
                if p.is_file():put(case+'/'+p.relative_to(folder).as_posix(),text(p))
    for f in manifest['files']:put('source/'+f['path'],text(a.ROOT/f['path']))
print(json.dumps({'bytes':(base/'evidence.zip').stat().st_size,'sha256':hashlib.sha256((base/'evidence.zip').read_bytes()).hexdigest(),
    'tests':summary['tests'],'end_to_end_passed':summary['end_to_end_passed'],'llm':summary['llm']},ensure_ascii=False,indent=2))
