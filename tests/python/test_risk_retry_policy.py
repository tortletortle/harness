"""Conservative classification and real retry-loop tests; no external business operations."""
import asyncio
from types import SimpleNamespace as NS
from uuid import uuid4
import pytest
from capabilities import CapabilityRegistry,CapabilitySpec,ToolCapabilityAdapter
from retry_policy import automatic_retry_policy,annotate_retry_failure

class Registry:
    def list_tools(self):return []
    def execute_tool(self,*args):return {'success':False,'error':'transient','retryable':True}

@pytest.mark.parametrize('tid',['command_runner','package_installer','git_operator','web_fetcher','test_runner','browser_check'])
def test_external_classification(tid):
    spec=ToolCapabilityAdapter(Registry(),tid).spec()
    assert spec.side_effect=='external' and not spec.idempotent

@pytest.mark.parametrize('tid',['file_creator','text_editor','code_context','project_memory','pdf_reader','desktop_observe'])
def test_local_writers_are_not_idempotent(tid):
    spec=ToolCapabilityAdapter(Registry(),tid).spec()
    assert spec.side_effect=='reversible' and not spec.idempotent

@pytest.mark.parametrize('tid',['greeting','uppercase_text','toolbox','file_find','document_batch','image_reader','office_reader','lint_gate'])
def test_read_only_known_tools_remain_eligible(tid):
    assert automatic_retry_policy(tid,None,Registry())['eligible']

def test_unknown_plugin_is_not_implicitly_safe():
    spec=ToolCapabilityAdapter(Registry(),'unknown_plugin').spec()
    assert spec.side_effect=='external' and not spec.idempotent
    assert not automatic_retry_policy('unknown_plugin',None,Registry())['eligible']

def test_human_not_auto_repeated():assert not automatic_retry_policy('manual_tool',None,Registry())['eligible']

def test_script_skill_not_idempotent_even_if_declared_none():
    from skills_loader import SkillCapabilityAdapter
    for effect in ('none','reversible','external'):
        skill=NS(description='fixture',side_effect=effect)
        assert not SkillCapabilityAdapter(Registry(),'skill_fixture',skill).spec().idempotent

def test_tool_output_cannot_claim_retry_safety():
    result={'success':False,'retryable':True,'retry_policy':{'eligible':True}}
    checked=annotate_retry_failure(result,automatic_retry_policy('command_runner',None,Registry()))
    assert not checked['retryable'] and not checked['retry_policy']['eligible'] and checked['manual_retry_required']
    assert result['retryable'] # no mutation of source result

@pytest.mark.asyncio
async def test_executor_marks_unsafe_failures_non_retryable():
    from executor import DefaultExecutor
    from models import Step
    ex=DefaultExecutor(None,{},tool_registry=Registry())
    r=await ex.execute_step(Step(order=1,description='fixture',tool='command_runner:run_command',inputs={},check_criteria=''))
    assert not r['retryable'] and r['manual_retry_required']
    safe=await ex.execute_step(Step(order=1,description='fixture',tool='greeting:greet',inputs={},check_criteria=''))
    assert safe['retryable'] and safe['retry_policy']['eligible']

@pytest.mark.asyncio
async def test_timeout_does_not_authorize_repeat():
    from executor import DefaultExecutor
    from models import Step
    class Slow(Registry):
        def execute_tool(self,*args):
            import time;time.sleep(.1);return {'success':True}
    ex=DefaultExecutor(None,{'step_timeout':.01},tool_registry=Slow())
    r=await ex.execute_step(Step(order=1,description='fixture',tool='file_creator:create_file',inputs={},check_criteria=''))
    assert r['error']=='Step execution timeout' and not r['retryable']


def test_chat_and_mcp_defaults_exclude_newly_external_tools():
    from chat import ChatSession
    from mcp_server import default_allowed_tools
    caps=CapabilityRegistry()
    ids=['command_runner','package_installer','git_operator','web_fetcher','test_runner','browser_check','greeting']
    for tid in ids:caps.register(ToolCapabilityAdapter(Registry(),tid))
    assert default_allowed_tools(caps)==['greeting']
    session=ChatSession('fixture',None,caps,{})
    assert session.allowed_tools==['greeting']
    explicit=ChatSession('fixture',None,caps,{'tools':['command_runner']})
    assert explicit.allowed_tools==['command_runner'] # explicit configuration unchanged, still needs Grant

@pytest.mark.asyncio
@pytest.mark.parametrize('tool,mode,expected_calls',[('command_runner','ordinary',1),('command_runner','acceptance',1),('coding_agent','feedback',1),('unknown_plugin','ordinary',1),('greeting','ordinary',3)])
async def test_actual_runtime_loop_respects_policy(monkeypatch,tool,mode,expected_calls):
    import api.server as server
    import api.runtime.step_execution as runtime
    class DB:
        def __init__(self):self.events=[];self.finished=[];self.states=[]
        def update_step(self,*a,**k):return True
        def update_step_state_machine(self,*a,**k):self.states.append(k);return True
        def update_plan_state_machine(self,*a,**k):return True
        def add_step_event(self,*a,**k):self.events.append(a);return True
        def create_execution_attempt(self,*a,**k):return {'attempt_id':str(uuid4())}
        def finish_execution_attempt(self,*a,**k):self.finished.append(k);return True
    db=DB();caps=CapabilityRegistry()
    if tool=='coding_agent':
        caps.register(NS(spec=lambda:CapabilitySpec(id='coding_agent',kind='agent_tool',side_effect='reversible',idempotent=False)))
    else:caps.register(ToolCapabilityAdapter(Registry(),tool))
    monkeypatch.setattr(runtime.state,'database',db)
    monkeypatch.setattr(runtime.state,'capability_registry',caps)
    monkeypatch.setattr(runtime.state,'tool_registry',Registry())
    monkeypatch.setattr(runtime.state,'retry_backoff_factor',0)
    async def noop(*a,**k):pass
    monkeypatch.setattr(runtime,'broadcast_log',noop);monkeypatch.setattr(runtime,'broadcast_plan_update',noop)
    monkeypatch.setattr(runtime,'_register_step_artifacts',lambda *a:None)
    monkeypatch.setattr(runtime,'_build_acceptance_feedback',lambda *a:'repair requested')
    calls=[]
    async def execute(plan,step):
        calls.append(step)
        if len(calls)>=3:return {'success':True,'check_status':'unverified','output':{}}
        result={'success':False,'error':'synthetic failure','check_status':'unverified','retryable':True,'blocked':False}
        if mode in ('acceptance','feedback'):
            result.update(check_status='failed',returncode=1,acceptance_result={'checks':[{'type':'exit_code','passed':False}]})
        return result
    monkeypatch.setattr(server,'_execute_step_with_state_machine',execute)
    step={'step_id':str(uuid4()),'tool':tool,'description':'fixture','inputs':{'retryable':True,'idempotent':True},'status':'Pending','max_retries':5}
    outcome,_=await runtime._execute_step_to_completion(NS(plan_id=uuid4()),step,str(uuid4()),'owner','main',False,set(),lambda *a:None)
    assert len(calls)==expected_calls
    assert outcome==('continue' if tool=='greeting' else 'stop')
    if expected_calls==1:
        assert not db.finished[0]['retryable'] and any(x[2]=='retry_suppressed' for x in db.events)
        assert db.states[-1]['outputs']['manual_retry_required']
    else:assert sum(x[2]=='retry' for x in db.events)==2
