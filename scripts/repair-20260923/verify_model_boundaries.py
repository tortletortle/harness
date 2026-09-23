"""Real-provider gate/cancellation/design validation; never replaces HTTP responses."""
import asyncio
import collections
import copy
import json
import os
import sys
import time

# Isolated process setting only; does not edit user environment or model routes.
os.environ['EAS_LLM_MAX_CONCURRENCY'] = '1'
os.environ['EAS_REPAIR_EVIDENCE_DIR'] = 'out/model-boundaries-20260923'
sys.argv = [sys.argv[0], 'live-models']
import verify_live as a
import httpx
from designer import DefaultDesigner
from llm_client import LLMClient, Message, OpenAICompatibleProvider


def events():
    path = a.RUN/'events.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


async def main():
    # A reused directory must not silently merge evidence or count cached responses.
    assert not (a.RUN/'events.jsonl').exists(), 'Use a fresh evidence directory for another run.'
    a.inventory()
    config = copy.deepcopy(a.CONFIG['models']['designer'])
    client = LLMClient()
    for name in {config[t]['provider'] for t in ('primary','fallback') if config.get(t)}:
        client.register_provider(OpenAICompatibleProvider(name,a.CONFIG['api_keys'][name],a.CONFIG['base_urls'][name]))
    checks=[]
    async def check(name,operation):
        start=time.monotonic()
        try:
            result=await asyncio.wait_for(operation(),300)
            row={'case':name,**result}
        except Exception as exc:
            row={'case':name,'passed':False,'error_type':type(exc).__name__,'error':a.clean(exc)}
        row['elapsed_s']=round(time.monotonic()-start,3)
        checks.append(row)
        a.save('checks.json',checks)
        print(a.clean(json.dumps(row,ensure_ascii=False)),flush=True)

    async def concurrency():
        primary=config['primary']
        p=client.providers[primary['provider']]
        aliases=[OpenAICompatibleProvider('alias-'+str(i),p.api_key,p.base_url) for i in range(3)]
        entered=asyncio.Event()
        active=peak=0
        previous=httpx.AsyncClient.send
        async def observe(self,request,*args,**kwargs):
            nonlocal active,peak
            if '/chat/completions' not in request.url.path:
                return await previous(self,request,*args,**kwargs)
            active+=1
            peak=max(peak,active)
            entered.set()
            try:
                return await previous(self,request,*args,**kwargs)
            finally:
                active-=1
        httpx.AsyncClient.send=observe
        prompt=[Message(role='user',content='What is two plus two? Please answer with the number only.')]
        before=sum(r['kind']=='llm_request' for r in events())
        tasks=[asyncio.create_task(obj.chat(prompt,primary['model'],temperature=0,max_tokens=128)) for obj in aliases]
        try:
            await asyncio.wait_for(entered.wait(),30)
            tasks[2].cancel()
            cancelled=False
            try:
                await tasks[2]
            except asyncio.CancelledError:
                cancelled=True
            responses=await asyncio.gather(*tasks[:2])
            during=sum(r['kind']=='llm_request' for r in events())-before
            # A subsequent request proves cancellation did not consume a gate permit.
            responses.append(await p.chat(prompt,primary['model'],temperature=0,max_tokens=128))
            shared=all(obj._slot is p._slot for obj in aliases)
            return {'passed':cancelled and shared and peak==1 and during==2 and all(r.content.strip()=='4' for r in responses),
                    'shared_gate':shared,'configured_limit':1,'peak_observed_network_requests':peak,
                    'queued_cancel_propagated':cancelled,'batch_network_requests':during,
                    'subsequent_request_completed':True,'contents':[r.content for r in responses]}
        finally:
            for task in tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            httpx.AsyncClient.send=previous
    await check('real_shared_gate_and_queued_cancellation',concurrency)

    async def fallback_probe():
        spec=config['fallback']
        response=await client.chat(provider_name=spec['provider'],model=spec['model'],temperature=0,max_tokens=128,
            messages=[Message(role='user',content='What is two plus two? Please answer with the number only.')])
        return {'passed':response.content.strip()=='4','content':response.content,'requested_model':spec['model'],
                'scope':'Direct real fallback-route availability check, not an injected upstream failure.'}
    if config.get('fallback'):
        await check('configured_fallback_route',fallback_probe)

    async def design():
        designer=DefaultDesigner(client,config)
        document=await designer.generate_design('设计一个最小方案：只使用 file_creator，在工作区创建 boundary.txt，内容为 hello。不要运行 shell，不要访问外部网络。当前只生成设计书，不执行任何文件操作。')
        data=document.model_dump(mode='json')
        a.save('design.json',data)
        return {'passed':bool(data.get('part1_design',{}).get('architecture',{}).get('components')),'executed':False}
    await check('real_business_design',design)

    observed=events()
    requests=collections.Counter(r['request_hash'] for r in observed if r['kind']=='llm_request')
    terminals=collections.Counter(r['request_hash'] for r in observed if r['kind'] in ('llm_response','llm_transport_error'))
    result={'passed':all(r['passed'] for r in checks),'checks':checks,'requests':sum(requests.values()),
        'terminals':sum(terminals.values()),'missing_terminals':sum((requests-terminals).values()),
        'http_statuses':dict(collections.Counter(str(r['status']) for r in observed if r['kind']=='llm_response')),
        'transport_errors':[r for r in observed if r['kind']=='llm_transport_error'],
        'reported_tokens':sum((r.get('usage') or {}).get('total_tokens',0) for r in observed if r['kind']=='llm_response'),
        'scope':'Actual provider responses; one queued caller cancelled before network send. No production server or plan execution.'}
    a.save('summary.json',result)
    print(a.clean(json.dumps(result,ensure_ascii=False,indent=2)),flush=True)
    return result


if __name__=='__main__':
    try:
        result=asyncio.run(main())
    except BaseException as exc:
        a.save('runner-error.json',{'type':type(exc).__name__,'error':a.clean(exc)})
        raise
    raise SystemExit(0 if result['passed'] else 1)
