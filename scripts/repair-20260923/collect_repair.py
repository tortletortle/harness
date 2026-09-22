"""Sanitized repair evidence; never export configuration secrets or entire databases."""
import collections
import difflib
import hashlib
import json
import sqlite3
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.argv = [sys.argv[0], 'collect']
import verify_live as a
base = a.BASE
if (a.ROOT / 'config' / 'api_token').exists():
    a.SECRETS.append((a.ROOT / 'config' / 'api_token').read_text().strip())

def text(p):
    raw = p.read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig', errors='replace')

summary = {'schema': 'helm-repair/1', 'captured_at': a.datetime.now(a.timezone.utc).isoformat(),
           'tests': {}, 'results': {}, 'llm': {}, 'report_jobs': [], 'pipe_cleanup_errors': {}}
for name in ('backend', 'backend-initial'):
    p = base / (name + '.xml')
    if p.exists():
        root = ET.parse(p)
        summary['tests'][name] = {
            'suites': [dict(s.attrib) for s in root.iter('testsuite')],
            'failures': [{'name': c.get('name'), 'kind': e.tag, 'message': e.get('message')}
                         for c in root.iter('testcase') for e in c if e.tag in ('failure', 'error')]}
for name in ('frontend-test', 'frontend-build', 'rust', 'extra-regressions', 'probe-tests'):
    p = base / (name + '.log')
    if p.exists(): summary['tests'][name] = {'tail': a.clean(text(p)[-5500:])}

results = ['probe/probe.json', 'normal-probe/probe.json', 'fallback/fallback.json',
           'ownership/ownership.json', 'shutdown/shutdown.json', 'live-b/api-smoke.json',
           'live-b/acceptance-b.json', 'live-c/out/real-llm-acceptance-c.json', 'live-exits.json']
for name in results:
    p = base / name
    if p.exists(): summary['results'][name] = json.loads(text(p))
rows = []
for p in sorted(base.glob('*/events.jsonl')):
    for line in text(p).splitlines():
        row = json.loads(line)
        if row.get('kind', '').startswith('llm_'):
            rows.append({**row, 'run': p.parent.name})
req = collections.Counter((r['run'], r['request_hash']) for r in rows if r['kind'] == 'llm_request')
term = collections.Counter((r['run'], r['request_hash']) for r in rows if r['kind'] != 'llm_request')
responses = [r for r in rows if r['kind'] == 'llm_response']
summary['llm'] = {
    'observed_request_count': sum(req.values()),
    'terminal_count': sum(term.values()),
    'missing_terminal_count': sum((req - term).values()),
    'responses_by_status': dict(collections.Counter(str(r['status']) for r in responses)),
    'transport_errors': [r for r in rows if r['kind'] == 'llm_transport_error'],
    'total_reported_tokens': sum((r.get('usage') or {}).get('total_tokens', 0) for r in responses),
    'scope': 'Instrumented verify_live runs only. Excludes initial unmarked exec_e2e subprocess; not a complete bill.',
    'by_model': {}}
for model in sorted({r['requested_model'] for r in responses}):
    subset = [r for r in responses if r['requested_model'] == model]
    latency = [r['elapsed_s'] for r in subset if r['status'] == 200]
    summary['llm']['by_model'][model] = {'responses': len(subset), 'http_200': len(latency),
        'median_s': round(statistics.median(latency), 3) if latency else None,
        'max_s': max(latency) if latency else None}

report_contents = {}
for p in base.glob('*/eas.db'):
    try:
        with sqlite3.connect(p) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute("SELECT artifact_id, plan_id, run_id, metadata_json FROM artifacts WHERE kind='delivery_report_job'"):
                metadata = json.loads(row['metadata_json'])
                summary['report_jobs'].append({'case': p.parent.name,
                    **{k: row[k] for k in ('artifact_id', 'plan_id', 'run_id')},
                    **{k: metadata.get(k) for k in ('status', 'attempts', 'error', 'updated_at', 'report_artifact_id')}})
            for row in conn.execute("SELECT artifact_id, content_hash FROM artifacts WHERE kind='delivery_report'"):
                content = p.parent / 'artifacts' / row['artifact_id']
                if content.exists():
                    actual = 'sha256:' + hashlib.sha256(content.read_bytes()).hexdigest()
                    assert row['content_hash'] == actual, (p.parent.name, row['artifact_id'])
                    report_contents[f"reports/{p.parent.name}/{row['artifact_id']}.md"] = a.clean(text(content))
    except sqlite3.OperationalError:
        pass
for p in base.glob('live-*.log'):
    t = text(p)
    markers = [m for m in ('I/O operation on closed pipe', '_ProactorBasePipeTransport.__del__', 'Event loop is closed') if m in t]
    if markers: summary['pipe_cleanup_errors'][p.name] = markers

changed = [
    'python/api/routes/design_routes.py', 'python/api/routes/artifact_routes.py',
    'python/api/runtime/artifacts.py', 'python/api/runtime/background.py', 'python/api/server.py',
    'python/database.py', 'python/designer/__init__.py', 'python/llm_client/__init__.py',
    'python/mcp_client.py', 'python/utils/usage_tracker.py', 'scripts/build-rust.ps1',
    'tests/conftest.py', 'tests/python/test_execution_ownership.py', 'tests/python/test_llm_providers.py',
    'tests/python/test_mcp_integration.py', 'tests/python/test_pause_resume_retry.py',
    'tests/python/test_exec_e2e.py', 'docs/ERROR_HANDLING.md']
added = ['tests/fixtures/offline_api_server.py', 'tests/python/test_repair_model_recovery.py',
         'scripts/repair-20260923/verify_live.py', 'scripts/repair-20260923/collect_repair.py']
patch = subprocess.run(['git', 'diff', '--no-ext-diff', '--', *changed], cwd=a.ROOT,
                       capture_output=True, check=True).stdout.decode('utf-8')
for name in added:
    patch += f'diff --git a/{name} b/{name}\nnew file mode 100644\n'
    patch += ''.join(difflib.unified_diff([], (a.ROOT/name).read_text(encoding='utf-8').splitlines(keepends=True),
                                       fromfile='/dev/null', tofile='b/' + name))
(base / 'repair.patch').write_text(patch, encoding='utf-8')
summary['files'] = [{'path': name, 'sha256': hashlib.sha256((a.ROOT/name).read_bytes()).hexdigest()} for name in changed + added]
summary['head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=a.ROOT).decode().strip()
summary['routing_override'] = {'scope': 'gitignored config/models.local.yaml only; credentials unchanged',
                               'stepfun_chat': a.CONFIG['models']['stepfun_chat']}
(base/'summary.json').write_text(a.clean(json.dumps(summary, ensure_ascii=False, indent=2)), encoding='utf-8')
(base/'llm-evidence.json').write_text(a.clean(json.dumps(rows, ensure_ascii=False, indent=2)), encoding='utf-8')
with zipfile.ZipFile(base/'evidence.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    def put(name, content):
        content = a.clean(content)
        assert not any(s and len(s) > 6 and s in content for s in a.SECRETS), name
        z.writestr(name, content)
    for name in results + ['summary.json', 'llm-evidence.json', 'repair.patch']:
        p = base / name
        if p.exists(): put(name, text(p))
    for p in base.glob('*.log'): put('log-tails/' + p.name, text(p)[-45000:])
    for p in base.glob('*.xml'): put(p.name, text(p))
    for p in base.glob('*/run-status.json'): put(str(p.relative_to(base)), text(p))
    for name, content in report_contents.items(): put(name, content)
    for name in changed + added: put('source/' + name, (a.ROOT/name).read_text(encoding='utf-8'))
print(json.dumps({'zip_bytes': (base/'evidence.zip').stat().st_size,
                  'sha256': hashlib.sha256((base/'evidence.zip').read_bytes()).hexdigest(),
                  'llm': summary['llm'], 'pipe_cleanup_errors': summary['pipe_cleanup_errors']}, ensure_ascii=False, indent=2))
