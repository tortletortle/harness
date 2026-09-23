"""Collect this optimization round only; no credentials or whole databases exported."""
import collections
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

os.environ['EAS_REPAIR_EVIDENCE_DIR'] = 'out/optimization-20260923'
sys.argv = [sys.argv[0], 'collect']
import verify_live as a
base = a.BASE

def text(path):
    raw = path.read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig', errors='replace')

api_token = a.ROOT/'config'/'api_token'
if api_token.exists():
    a.SECRETS.append(api_token.read_text(encoding='utf-8').strip())
live = (a.ROOT/text(base/'live-root.txt').strip()).resolve()
assert live.parent == base
summary = {'schema':'helm-optimization/1', 'captured_at':a.datetime.now(a.timezone.utc).isoformat(),
           'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=a.ROOT).decode().strip(),
           'tests':{}, 'results':{}, 'report_jobs':[], 'pipe_cleanup_errors':{},
           'scope':'Current optimization round only; real-provider report, ownership and shutdown tests in isolated workspaces.'}
for name in ('before','after','focused','backend'):
    root = ET.parse(base/(name+'.xml'))
    cases = list(root.iter('testcase'))
    summary['tests'][name] = {
        'suites':[dict(s.attrib) for s in root.iter('testsuite')],
        'passed':sum(not any(c.find(t) is not None for t in ('failure','error','skipped')) for c in cases),
        'skipped':sum(c.find('skipped') is not None for c in cases),
        'failures':[{'name':c.get('name'),'kind':e.tag,'message':e.get('message')}
                    for c in cases for e in c if e.tag in ('failure','error')],
        'log_summary':text(base/(name+'.log')).splitlines()[-1]}
summary['results']['live-exits'] = json.loads(text(base/'live-exits.json'))
rows = []
reports = {}
for case in ('single-flight','ownership','shutdown'):
    folder = live/case
    summary['results'][case] = json.loads(text(folder/(case+'.json')))
    for line in text(folder/'events.jsonl').splitlines():
        row = json.loads(line)
        if row.get('kind','').startswith('llm_'):
            rows.append({**row,'case':case})
    with sqlite3.connect(folder/'eas.db') as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT artifact_id,plan_id,run_id,metadata_json FROM artifacts WHERE kind='delivery_report_job'"):
            meta = json.loads(row['metadata_json'])
            summary['report_jobs'].append({'case':case, **{k:row[k] for k in ('artifact_id','plan_id','run_id')},
                **{k:meta.get(k) for k in ('status','attempts','error','report_artifact_id')}})
        for row in conn.execute("SELECT artifact_id,content_hash FROM artifacts WHERE kind='delivery_report'"):
            content = folder/'artifacts'/row['artifact_id']
            assert content.is_file(), (case,row['artifact_id'])
            assert 'sha256:'+hashlib.sha256(content.read_bytes()).hexdigest() == row['content_hash']
            reports[f'reports/{case}/{row["artifact_id"]}.md'] = text(content)
req = collections.Counter((r['case'],r['request_hash']) for r in rows if r['kind']=='llm_request')
term = collections.Counter((r['case'],r['request_hash']) for r in rows if r['kind']!='llm_request')
responses = [r for r in rows if r['kind']=='llm_response']
summary['llm'] = {'observed_request_count':sum(req.values()),'terminal_count':sum(term.values()),
    'missing_terminal_count':sum((req-term).values()),
    'responses_by_status':dict(collections.Counter(str(r['status']) for r in responses)),
    'transport_errors':[r for r in rows if r['kind']=='llm_transport_error'],
    'total_reported_tokens':sum((r.get('usage') or {}).get('total_tokens',0) for r in responses),
    'scope':'Observed calls in these three cases only; provider-reported usage, not a bill.'}
for name in ('single-flight.log','live-ownership.log','live-shutdown.log'):
    data = text(base/name)
    markers = [m for m in ('I/O operation on closed pipe','_ProactorBasePipeTransport.__del__','Event loop is closed') if m in data]
    if markers:
        summary['pipe_cleanup_errors'][name] = markers
manifest = json.loads(text(base/'patch-manifest.json'))
for item in manifest['files']:
    data = (a.ROOT/item['path']).read_text(encoding='utf-8').replace('\r\n','\n')
    assert hashlib.sha256(data.encode()).hexdigest() == item['after_sha256_lf'], item['path']
summary['patch'] = manifest
(base/'summary.json').write_text(a.clean(json.dumps(summary,ensure_ascii=False,indent=2)),encoding='utf-8')
(base/'llm-evidence.json').write_text(a.clean(json.dumps(rows,ensure_ascii=False,indent=2)),encoding='utf-8')
with zipfile.ZipFile(base/'evidence.zip','w',zipfile.ZIP_DEFLATED) as z:
    def put(name,content):
        # Preserve executable patch context, including the literal Bearer <token>
        # documentation placeholder. Refuse source export if a known secret occurs.
        if name != 'optimization.patch' and not name.startswith('source/'):
            content = a.clean(content)
        assert not any(s and len(s)>6 and s in content for s in a.SECRETS), name
        z.writestr(name,content)
    for name in ('summary.json','llm-evidence.json','optimization.patch','patch-manifest.json','live-exits.json','live-root.txt'):
        put(name,text(base/name))
    for p in base.glob('*.xml'):
        put(p.name,text(p))
    for p in base.glob('*.log'):
        put('logs/'+p.name,text(p))
    for case in ('single-flight','ownership','shutdown'):
        for p in (live/case).iterdir():
            if p.is_file() and p.suffix in ('.json','.jsonl'):
                put('live/'+case+'/'+p.name,text(p))
    for name,content in reports.items():
        put(name,content)
    for item in manifest['files']:
        put('source/'+item['path'],text(a.ROOT/item['path']))
print(json.dumps({'bytes':(base/'evidence.zip').stat().st_size,
    'sha256':hashlib.sha256((base/'evidence.zip').read_bytes()).hexdigest(),
    'tests':{k:{'passed':v['passed'],'failures':len(v['failures']),'skipped':v['skipped']} for k,v in summary['tests'].items()},
    'results':summary['results'],'llm':summary['llm'],'pipe_cleanup_errors':summary['pipe_cleanup_errors']},ensure_ascii=False,indent=2))
