"""Desktop approval scopes: synthetic identities only; never enumerate the user's desktop."""
import asyncio
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import pytest
from authorization import AuthorizationManager
from api.approval import ApprovalManager,ApprovalTimeout
from api.desktop_approval import prepare_desktop_scope,issue_approved_desktop_grant
# 本文件依赖外部 harness_tools 仓库（通过 EAS_TOOLS_DIR 定位）。
# 未配置时整文件跳过，而不是在收集阶段抛 KeyError 让整个测试会话中断。
_TOOLS_DIR=os.environ.get('EAS_TOOLS_DIR')
if not _TOOLS_DIR:pytest.skip('EAS_TOOLS_DIR 未配置，跳过依赖外部 harness_tools 的桌面审批测试',allow_module_level=True)
sys.path.insert(0,str(Path(_TOOLS_DIR).resolve().parents[1]))
Native=pytest.importorskip('harness_tools.desktop_native').Native

@pytest.fixture
def fixture(monkeypatch,tmp_path):
    identity={'window_id':'123','pid':7,'process_created':'42'}
    monkeypatch.setattr(Native,'identity',lambda self,h:dict(identity))
    return NS(tool='desktop_observe:inspect_window',inputs={'window_id':'123'},step_id='s'),AuthorizationManager(require_grant=True,workspace_root=tmp_path),identity

def test_mandatory_and_no_generic_approval():
    m=ApprovalManager();assert m.is_required_for_step('desktop_observe')
    req=m.create('p','s','desktop_observe','inspect',{})
    assert not m.resolve(req.approval_id,True)
    assert m.resolve(req.approval_id,False)

def test_confirmation_and_snapshots(fixture):
    step,manager,_=fixture;scope=prepare_desktop_scope(step,manager.workspace_root);m=ApprovalManager()
    req=m.create('p','s','desktop_observe','inspect',step.inputs,desktop_scope=scope)
    step.inputs['window_id']='456';scope['identity']['pid']=999
    shown=m.list_pending()[0];assert shown['inputs']['window_id']=='123' and shown['desktop_scope']['identity']['pid']==7
    shown['desktop_scope']['confirmation']='mutated'
    assert not m.resolve(req.approval_id,True,desktop_confirmation='mutated')
    assert m.resolve(req.approval_id,True,desktop_confirmation=req.desktop_scope['confirmation'])
    assert not m.resolve(req.approval_id,True,desktop_confirmation=req.desktop_scope['confirmation'])

def test_exact_inspect_grant(fixture):
    step,m,_=fixture;scope=prepare_desktop_scope(step,m.workspace_root)
    g=issue_approved_desktop_grant(m,'p',step,scope,approved=True)
    assert g.allowed_resources==('desktop:inspect:123:7:42',)
    assert g.plan_id=='p' and g.step_id=='s' and g.limits.max_tool_calls==1 and g.limits.max_model_calls==0
    m.revoke_all_for_plan('p');assert g.revoked

@pytest.mark.parametrize('field',['window','inputs','workspace'])
def test_changed_scope(fixture,tmp_path,field):
    step,m,identity=fixture;scope=prepare_desktop_scope(step,m.workspace_root);root=m.workspace_root
    if field=='window':identity['process_created']='43'
    if field=='inputs':step.inputs['max_chars']=100
    if field=='workspace':root=tmp_path/'other'
    with pytest.raises(ValueError):issue_approved_desktop_grant(m,'p',step,scope,approved=True,workspace_root=root)

def test_reject_cannot_issue(fixture):
    step,m,_=fixture;scope=prepare_desktop_scope(step,m.workspace_root)
    with pytest.raises(ValueError):issue_approved_desktop_grant(m,'p',step,scope,approved=False)

def test_enumeration_only(fixture):
    step,m,_=fixture;step.tool='desktop_observe:list_windows';step.inputs={}
    scope=prepare_desktop_scope(step,m.workspace_root);assert scope['identity'] is None
    g=issue_approved_desktop_grant(m,'p',step,scope,approved=True)
    assert g.allowed_resources==('desktop:enumerate',)

def test_capture_only_and_new_output(fixture,tmp_path):
    step,m,_=fixture;step.tool='desktop_observe:screenshot';step.inputs['output']=str(tmp_path/'capture.png')
    scope=prepare_desktop_scope(step,m.workspace_root)
    g=issue_approved_desktop_grant(m,'p',step,scope,approved=True)
    assert g.allowed_resources==('desktop:capture:123:7:42',str(tmp_path/'capture.png'))
    (tmp_path/'capture.png').write_bytes(b'keep')
    with pytest.raises(ValueError):issue_approved_desktop_grant(m,'p',step,scope,approved=True)
    assert (tmp_path/'capture.png').read_bytes()==b'keep'

@pytest.mark.parametrize('tool,inputs',[('desktop_observe',{}),('desktop_observe:click',{}),('desktop_observe:list_windows',{'allow_enumeration':True}),('desktop_observe:inspect_window',{'window_id':'0'}),('desktop_observe:list_windows',{'max_windows':True})])
def test_invalid(fixture,tool,inputs):
    step,m,_=fixture;step.tool=tool;step.inputs=inputs
    with pytest.raises(ValueError):prepare_desktop_scope(step,m.workspace_root)

@pytest.mark.asyncio
async def test_timeout_and_cancel_plan(fixture):
    step,m,_=fixture;scope=prepare_desktop_scope(step,m.workspace_root);a=ApprovalManager(timeout_seconds=.01)
    req=a.create('p','s','desktop_observe','inspect',{},desktop_scope=scope)
    with pytest.raises(ApprovalTimeout):await a.wait(req.approval_id)
    assert not a.resolve(req.approval_id,True,desktop_confirmation=scope['confirmation'])
    req=a.create('p','s','desktop_observe','inspect',{},desktop_scope=scope)
    a.reject_all_for_plan('p');assert not (await a.wait(req.approval_id)).approved

def test_http_explicit_confirmation(fixture,monkeypatch):
    from fastapi.testclient import TestClient
    from api.server import app,state
    from api.auth import reset_token_cache
    step,m,_=fixture;scope=prepare_desktop_scope(step,m.workspace_root);a=ApprovalManager()
    monkeypatch.setattr(state,'approval_manager',a);monkeypatch.setenv('EAS_API_TOKEN','desktop-approval-test-only');reset_token_cache()
    req=a.create('p','s','desktop_observe','inspect',step.inputs,desktop_scope=scope);client=TestClient(app)
    path=f'/api/plan/p/approval/{req.approval_id}/approve';headers={'Authorization':'Bearer desktop-approval-test-only'}
    try:
        assert client.post(path,json={}).status_code==401
        assert client.post(path,headers=headers,json={}).status_code==409
        assert client.post(path,headers=headers,json={'desktop_confirmation':'wrong'}).status_code==409
        assert not req.resolved
        assert client.post(path,headers=headers,json={'desktop_confirmation':scope['confirmation']}).status_code==200
        assert client.post(path,headers=headers,json={'desktop_confirmation':scope['confirmation']}).status_code==404
    finally:reset_token_cache()
