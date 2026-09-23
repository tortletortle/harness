"""Offline output-boundary regression; live task failure is recorded separately."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from designer import DefaultDesigner, GenerationError
from llm_client import LLMResponse
from test_designer_generation_error import _make_designer


def obj(responses,fallback=True):
    instance=DefaultDesigner.__new__(DefaultDesigner)
    instance.llm_client=SimpleNamespace(chat=AsyncMock(side_effect=responses))
    instance.model_config={'fallback':{'provider':'backup','model':'b'}} if fallback else {}
    return instance


@pytest.mark.parametrize('reason',['length','max_tokens'])
async def test_truncated_output_uses_backup_without_repeating_same_route(reason):
    designer=obj([LLMResponse(content='{"partial":',model='m',finish_reason=reason),
                  LLMResponse(content='complete',model='b',finish_reason='stop')])
    result=await designer._chat_design(provider_name='main',model='m',messages=[])
    assert result.content=='complete'
    assert [c.kwargs['provider_name'] for c in designer.llm_client.chat.await_args_list]==['main','backup']


async def test_no_fallback_rejects_truncated_output_without_retry():
    designer=obj([LLMResponse(content='partial',model='m',finish_reason='length')],False)
    with pytest.raises(GenerationError,match='长度上限'):
        await designer._chat_design(provider_name='main',model='m',messages=[])
    assert designer.llm_client.chat.await_count==1


async def test_both_routes_truncated_have_two_call_budget():
    designer=obj([LLMResponse(content='partial',model=m,finish_reason='length') for m in ('m','b')])
    with pytest.raises(GenerationError,match='长度上限'):
        await designer._chat_design(provider_name='main',model='m',messages=[])
    assert designer.llm_client.chat.await_count==2


@pytest.mark.parametrize('wrapped',[False,True])
async def test_valid_json_with_embedded_code_fences_is_not_cut(tmp_path,wrapped):
    marker='example ```json code``` must remain intact'
    payload={'architecture':{'components':[marker],'interactions':[]},'difficulty':{'overall':'easy','breakdown':{}},
             'required_tools':[],'overall_tool_completeness':1.0,'risks':[]}
    content=json.dumps(payload)
    if wrapped:content='```json\n'+content+'\n```'
    client=SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content=content,model='m',finish_reason='stop')))
    designer=_make_designer(client,tmp_path)
    design=await designer.generate_design('output boundary regression')
    assert design.part1_design.architecture.components==[marker]
    client.chat.assert_awaited_once()
