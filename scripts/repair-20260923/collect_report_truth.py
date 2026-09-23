"""Export only reviewed text evidence; never databases, secrets or credential files."""
import collections,hashlib,json,os,re,sys,zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
os.environ['EAS_REPAIR_EVIDENCE_DIR']='out/report-truth-20260923'
sys.argv=[sys.argv[0],'collect']
import verify_live as a
base=a.BASE

def text(p):
    raw=p.read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')

def clean(s):
    for secret in a.SECRETS:
        if secret:s=s.replace(secret,'[REDACTED]')
    s=re.sub(r"Bearer\s+[^\s\"']+",'Bearer [REDACTED]',s)
    return re.sub(r'(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}','[REDACTED]',s)

manifest=json.loads(text(base/'patch-manifest.json'))
for item in manifest['files']:
    raw=(a.ROOT/item['path']).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()
    assert hashlib.sha256(raw).hexdigest()==item['after_sha256_lf'],item['path']
summary={'tests':{},'llm':{},'cases':{}}
for name in ('before','focused','focused-expanded','attempt-before','focused-final','backend','backend-final'):
    cases=list(ET.parse(base/(name+'.xml')).iter('testcase'))
    summary['tests'][name]={'passed':sum(not any(c.find(t) is not None for t in ('failure','error','skipped')) for c in cases),
        'failed':sum(c.find('failure') is not None or c.find('error') is not None for c in cases),
        'skipped':sum(c.find('skipped') is not None for c in cases),'log_summary':text(base/(name+'.log')).splitlines()[-1]}
rows=[]
for batch in ('live','live-final'):
    folder=base/batch
    summary['cases'][batch]=json.loads(text(folder/'verification.json'))
    for line in text(folder/'events.jsonl').splitlines():rows.append({**json.loads(line),'batch':batch})
requests=collections.Counter((r['batch'],r['request_hash']) for r in rows if r['kind']=='llm_request')
terminals=collections.Counter((r['batch'],r['request_hash']) for r in rows if r['kind'] in ('llm_response','llm_transport_error'))
summary['llm']={'requests':sum(requests.values()),'missing_terminals':sum((requests-terminals).values()),
    'reported_tokens':sum((r.get('usage') or {}).get('total_tokens',0) for r in rows if r['kind']=='llm_response'),
    'responses':[{k:r.get(k) for k in ('batch','status','requested_model','elapsed_s','usage','error_type')} for r in rows if r['kind'] in ('llm_response','llm_transport_error')]}
summary['interpretation']='The first live batch missed zero-attempt semantics; final verdict requires live-final plus manual narrative review. Not four independent end-to-end executions.'
(base/'summary.json').write_text(clean(json.dumps(summary,ensure_ascii=False,indent=2)),encoding='utf-8')
with zipfile.ZipFile(base/'evidence.zip','w',zipfile.ZIP_DEFLATED) as z:
    def put(name,s,preserve=False):
        safe=clean(s)
        if preserve:assert safe==s,('Source/report requires redaction; cannot silently invalidate hash',name)
        assert not any(secret and len(secret)>6 and secret in safe for secret in a.SECRETS)
        z.writestr(name,safe)
    for name in ('summary.json','patch-manifest.json','report-truth.patch','narrative-review.json'):
        put(name,text(base/name),preserve=True)
    for pattern in ('*.log','*.xml'):
        for p in base.glob(pattern):put(p.name,text(p))
    for batch in ('live','live-final'):
        for p in (base/batch).iterdir():
            if p.is_file() and p.suffix in ('.md','.json','.jsonl'):
                put(batch+'/'+p.name,text(p),preserve=p.suffix=='.md')
    for directory in ('baseline-source','stage1-source'):
        for p in (base/directory).glob('*.py'):put(directory+'/'+p.name,text(p),preserve=True)
    old=a.ROOT/'out/order-e2e-20260923/case-2/artifacts/report-359ab776-bffe-477d-92f9-db50e9e775ea'
    put('baseline/original-model-report.md',text(old),preserve=True)
    for item in manifest['files']:put('source/'+item['path'],text(a.ROOT/item['path']),preserve=True)
print(json.dumps({'bytes':(base/'evidence.zip').stat().st_size,'sha256':hashlib.sha256((base/'evidence.zip').read_bytes()).hexdigest(),**summary},ensure_ascii=False,indent=2))
