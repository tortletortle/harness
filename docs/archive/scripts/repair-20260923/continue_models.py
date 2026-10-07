"""Small real-model follow-up. Unique evidence directory, sequential requests, no HTTP stubs."""
import asyncio
import collections
import copy
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ['EAS_REPAIR_EVIDENCE_DIR'] = 'out/repair-followup-' + datetime.now().strftime('%Y%m%d-%H%M%S')
sys.argv = [sys.argv[0], 'models']
import verify_live as a
from designer import DefaultDesigner
from llm_client import LLMClient, Message, OpenAICompatibleProvider


def events():
    p = a.RUN / 'events.jsonl'
    return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


async def main():
    a.inventory()
    client = LLMClient()
    config = copy.deepcopy(a.CONFIG['models']['designer'])
    for name in {config[t]['provider'] for t in ('primary', 'fallback') if config.get(t)}:
        client.register_provider(OpenAICompatibleProvider(name, a.CONFIG['api_keys'][name], a.CONFIG['base_urls'][name]))
    designer = DefaultDesigner(client, config)
    rows = []

    async def check(name, operation, validate):
        before = len(events())
        started = time.monotonic()
        row = {'case': name}
        try:
            result = await asyncio.wait_for(operation(), timeout=300)
            row.update(passed=bool(validate(result)), result=result)
        except Exception as exc:
            row.update(passed=False, error_type=type(exc).__name__, error=a.clean(exc))
        row.update(elapsed_s=round(time.monotonic()-started,3), events=events()[before:])
        rows.append(row)
        a.save('checks.json', rows)
        print(a.clean(json.dumps({k:v for k,v in row.items() if k not in ('events','result')},ensure_ascii=False)), flush=True)

    async def arithmetic(tier):
        spec = config[tier]
        response = await client.chat(provider_name=spec['provider'], model=spec['model'],
                                     messages=[Message(role='user',content='What is two plus two? Please answer with the number only.')],
                                     temperature=0, max_tokens=128)
        return {'content': response.content, 'model': response.model, 'usage': response.usage}

    for tier in ('primary', 'fallback'):
        if config.get(tier):
            await check(tier+'_arithmetic',lambda tier=tier:arithmetic(tier),lambda r:r['content'].strip()=='4')

    async def fixed_token():
        spec = config['primary']
        response = await client.chat(provider_name=spec['provider'], model=spec['model'],
                                     messages=[Message(role='user',content='Reply with exactly AUDIT_OK. Do not call tools.')],
                                     temperature=0,max_tokens=128)
        return {'content':response.content,'model':response.model,'usage':response.usage}
    await check('primary_fixed_token',fixed_token,lambda r:r['content'].strip()=='AUDIT_OK')

    async def design():
        result = await designer.generate_design(
            '设计一个最小方案：只使用 file_creator，在工作区创建 hello.txt，内容为 hello。'
            '不要运行 shell，不要访问外部网络。当前只生成设计书，不执行任何文件操作。')
        return result.model_dump(mode='json')
    await check('configured_business_design',design,
                lambda r:bool(r.get('part1_design',{}).get('architecture',{}).get('components')))

    observed = events()
    requests = [r for r in observed if r['kind']=='llm_request']
    terminals = [r for r in observed if r['kind'] in ('llm_response','llm_transport_error')]
    missing = collections.Counter(r['request_hash'] for r in requests)-collections.Counter(r['request_hash'] for r in terminals)
    summary = {'passed':all(r['passed'] for r in rows), 'checks':[ {k:v for k,v in r.items() if k!='events'} for r in rows],
               'request_count':len(requests),'terminal_count':len(terminals),'missing_terminal_count':sum(missing.values()),
               'http_statuses':dict(collections.Counter(str(r['status']) for r in terminals if r['kind']=='llm_response')),
               'reported_tokens':sum((r.get('usage') or {}).get('total_tokens',0) for r in terminals if r['kind']=='llm_response'),
               'scope':'Actual configured model requests; no production server, DB, workspace, credentials or model routing changed.',
               'evidence_directory':str(a.RUN)}
    a.save('summary.json',summary)
    print(a.clean(json.dumps(summary,ensure_ascii=False,indent=2)),flush=True)
    return summary


if __name__=='__main__':
    try:
        result = asyncio.run(main())
    except BaseException as exc:
        a.save('runner-error.json',{'type':type(exc).__name__,'error':a.clean(exc)})
        raise
    raise SystemExit(0 if result['passed'] else 1)
