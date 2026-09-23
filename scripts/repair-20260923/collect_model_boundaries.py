"""Export only this round's sanitized evidence and exact, secret-checked source."""
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

os.environ['EAS_REPAIR_EVIDENCE_DIR']='out/model-boundaries-20260923'
sys.argv=[sys.argv[0],'collect']
import verify_live as a
base=a.BASE

def text(path):
    raw=path.read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig',errors='replace')

token=a.ROOT/'config'/'api_token'
if token.exists():a.SECRETS.append(token.read_text().strip())
manifest=json.loads(text(base/'patch-manifest.json'))
for item in manifest['files']:
    source=(a.ROOT/item['path']).read_text(encoding='utf-8').replace('\r\n','\n')
    assert hashlib.sha256(source.encode()).hexdigest()==item['after_sha256_lf'],item['path']
summary={'schema':'helm-model-boundaries/1','captured_at':a.datetime.now(a.timezone.utc).isoformat(),
    'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=a.ROOT).decode().strip(),
    'tests':{},'patch':manifest,'live':json.loads(text(base/'live-models'/'summary.json'))}
for name in ('before','focused','backend'):
    root=ET.parse(base/(name+'.xml'))
    cases=list(root.iter('testcase'))
    summary['tests'][name]={
        'passed':sum(not any(c.find(t) is not None for t in ('failure','error','skipped')) for c in cases),
        'skipped':sum(c.find('skipped') is not None for c in cases),
        'failures':[{'name':c.get('name'),'type':e.tag,'message':e.get('message')} for c in cases for e in c if e.tag in ('failure','error')],
        'suites':[dict(s.attrib) for s in root.iter('testsuite')],
        'log_summary':text(base/(name+'.log')).splitlines()[-1]}
    if name=='backend':
        summary['new_test_count']=sum('test_model_boundaries' in c.get('classname','') for c in cases)
(base/'summary.json').write_text(a.clean(json.dumps(summary,ensure_ascii=False,indent=2)),encoding='utf-8')
with zipfile.ZipFile(base/'evidence.zip','w',zipfile.ZIP_DEFLATED) as z:
    def put(name,content):
        if name!='model-boundaries.patch' and not name.startswith('source/'):
            content=a.clean(content)
        assert not any(s and len(s)>6 and s in content for s in a.SECRETS),name
        z.writestr(name,content)
    for name in ('summary.json','patch-manifest.json','model-boundaries.patch'):
        put(name,text(base/name))
    for suffix in ('*.log','*.xml'):
        for p in base.glob(suffix):put(p.name,text(p))
    for p in (base/'live-models').iterdir():
        if p.is_file() and p.suffix in ('.json','.jsonl'):
            put('live/'+p.name,text(p))
    for item in manifest['files']:
        put('source/'+item['path'],text(a.ROOT/item['path']))
print(json.dumps({'bytes':(base/'evidence.zip').stat().st_size,
    'sha256':hashlib.sha256((base/'evidence.zip').read_bytes()).hexdigest(),
    'tests':{k:v['log_summary'] for k,v in summary['tests'].items()},
    'new_test_count':summary['new_test_count'],'live':summary['live']},ensure_ascii=False,indent=2))
