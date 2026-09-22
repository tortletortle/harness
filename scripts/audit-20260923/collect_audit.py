"""Create a sanitized, portable evidence package for the completed audit."""
import json,os,sys,zipfile,hashlib,statistics,xml.etree.ElementTree as ET
from pathlib import Path
sys.argv=[sys.argv[0],'collect']
import audit_driver as a
token_path=a.ROOT/'config'/'api_token'
if token_path.exists():
    token_value=token_path.read_text(encoding='utf-8').strip()
    if token_value:a.SECRETS.append(token_value)
base=a.BASE
summary={'schema':'helm-audit/1','captured_at':a.datetime.now(a.timezone.utc).isoformat(),'tests':{},'results':{},'llm':{},'files':[]}
for name in ('backend','browser','pause-retry'):
    path=base/(name+'.xml')
    if not path.exists():continue
    root=ET.parse(path)
    suites=list(root.iter('testsuite'))
    summary['tests'][name]={'suites':[dict(s.attrib) for s in suites],'failures':[{'classname':c.get('classname'),'name':c.get('name'),'message':c.find('failure').get('message'),'detail':c.find('failure').text} for c in root.iter('testcase') if c.find('failure') is not None],'skipped':[{'name':c.get('name'),'reason':c.find('skipped').get('message')} for c in root.iter('testcase') if c.find('skipped') is not None]}
relative_results=['probe/probe.json','contracts/contracts.json','live-b/api-smoke.json','live-b/acceptance-b.json','live-b-fallback/acceptance-b.json','live-c/out/real-llm-acceptance-c.json','checkpoint/checkpoint.json','ui/ui-smoke.json','ownership/ownership.json','replan-forced/replan.json']
for name in relative_results:
    p=base/name
    if p.exists(): summary['results'][name]=json.loads(p.read_text(encoding='utf-8'))
rows=[]
for p in sorted(base.glob('*/events.jsonl')):
    for line in p.read_text(encoding='utf-8').splitlines():
        try: row=json.loads(line)
        except Exception: continue
        if row.get('kind','').startswith('llm_'):row['run']=p.parent.name;rows.append(row)
summary['llm']['request_count']=sum(r['kind']=='llm_request' for r in rows)
responses=[r for r in rows if r['kind']=='llm_response']
summary['llm']['responses_by_status']={str(code):sum(r['status']==code for r in responses) for code in sorted({r['status'] for r in responses})}
summary['llm']['transport_errors']=[r for r in rows if r['kind']=='llm_transport_error']
from collections import Counter
terminals=Counter((r['run'],r.get('request_hash')) for r in rows if r['kind'] in ('llm_response','llm_transport_error'))
unmatched=[]
for r in rows:
    if r['kind']!='llm_request':continue
    key=(r['run'],r.get('request_hash'))
    if terminals[key]:terminals[key]-=1
    else:unmatched.append(r)
summary['llm']['requests_without_terminal_record']=unmatched
summary['llm']['total_reported_tokens']=sum((r.get('usage') or {}).get('total_tokens',0) for r in responses)
summary['llm']['by_model']={}
for model in sorted({r.get('requested_model','unknown') for r in responses}):
    subset=[r for r in responses if r.get('requested_model')==model];lat=[r['elapsed_s'] for r in subset if r['status']==200]
    summary['llm']['by_model'][model]={'responses':len(subset),'http_200':len(lat),'latency_median_s':round(statistics.median(lat),3) if lat else None,'latency_max_s':max(lat) if lat else None,'reported_tokens':sum((r.get('usage') or {}).get('total_tokens',0) for r in subset)}
(base/'llm-evidence.json').write_text(a.clean(json.dumps(rows,ensure_ascii=False,indent=2)),encoding='utf-8')
def readtext(p):
    raw=p.read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8',errors='replace')
for name in ('frontend-test','frontend-build','rust-fmt','rust-test','rust-gnu','rust-gnu-path','rust-binaries'):
    p=base/(name+'.log')
    if p.exists(): summary['tests'][name]={'tail':a.clean(readtext(p)[-7000:])}
# Keep original full logs only on host; package bounded tails and structured evidence.
with zipfile.ZipFile(base/'evidence.zip','w',zipfile.ZIP_DEFLATED) as z:
    for name in relative_results+['llm-evidence.json','probe/config-summary.json','live-b-fallback/routing-override.json']:
        p=base/name
        if p.exists():z.writestr(name,a.clean(readtext(p)))
    for p in base.glob('*.log'):
        text=a.clean(readtext(p)[-45000:]);z.writestr('log-tails/'+p.name,text)
    for p in base.glob('*.xml'):z.writestr(p.name,a.clean(readtext(p)))
    z.writestr('audit_driver.py',a.clean(Path(a.__file__).read_text(encoding='utf-8')))
    z.writestr('REPORT.md',a.clean((a.ROOT/'docs'/'audit-20260923.md').read_text(encoding='utf-8')))
    z.writestr('errors.csv',a.clean((base/'errors.csv').read_text(encoding='utf-8')))
    for p in base.rglob('run-status.json'):
        z.writestr(str(p.relative_to(base)),a.clean(readtext(p)))
    summary_text=a.clean(json.dumps(summary,ensure_ascii=False,indent=2))
    z.writestr('summary.json',summary_text)
(base/'summary.json').write_text(summary_text,encoding='utf-8')
with zipfile.ZipFile(base/'evidence.zip') as z:
    assert z.testzip() is None
    for name in z.namelist():
        content=z.read(name).decode('utf-8',errors='replace')
        assert not any(secret in content for secret in a.SECRETS if len(secret)>6), 'Credential redaction check failed'
print('Archive integrity and configured credential redaction checks passed.')
print(json.dumps({'summary':str(base/'summary.json'),'evidence_zip':str(base/'evidence.zip'),'zip_bytes':(base/'evidence.zip').stat().st_size,'llm':summary['llm']},ensure_ascii=False,indent=2))
