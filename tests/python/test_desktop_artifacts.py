import base64,hashlib,json,os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import pytest
from api.desktop_artifacts import is_bounded_png,store_desktop_screenshot
sys.path.insert(0,str(Path(os.environ['EAS_TOOLS_DIR']).resolve().parents[1]))
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')

@pytest.fixture
def data(tmp_path):
    path=tmp_path/'window.png';path.write_bytes(PNG);saved=[];events=[]
    step={'step_id':'s','tool':'desktop_observe:screenshot','inputs':{'window_id':'123','output':str(path)}}
    grant=NS(plan_id='p',step_id='s',allowed_tools={'desktop_observe'},used_tool_calls=1,workspace_root=str(tmp_path),allowed_resources=(str(path),'desktop:capture:123:4:5'))
    def store(a,content,**kw):
        saved.append((bytes(content),kw));return {'artifact_id':a,'content_hash':'sha256:'+hashlib.sha256(content).hexdigest()}
    database=NS(get_steps=lambda p:[step],store_artifact_content=store,add_step_event=lambda *a,**k:events.append(a))
    state=NS(database=database,authorization_manager=NS(get=lambda g:grant))
    result={'success':True,'grant_id':'g','output':{'success':True,'width':1,'height':1,'window':{'title':'Synthetic'},'artifact':{'path':str(path),'media_type':'image/png','bytes':len(PNG),'sha256':hashlib.sha256(PNG).hexdigest()}}}
    return state,result,path,step,grant,saved,events

def test_snapshot_is_independent(data):
    state,r,path,_,g,saved,events=data
    assert store_desktop_screenshot(state,'p','s',r)
    path.write_bytes(b'changed after capture')
    assert saved[0][0]==PNG and saved[0][1]['kind']=='desktop_screenshot'
    assert events[0][2]=='desktop_screenshot_stored'

@pytest.mark.parametrize('field',['wrong_tool','wrong_step','wrong_plan','unused_grant','missing_capture','failed_output'])
def test_untrusted_result_cannot_read(data,field):
    state,r,path,step,g,saved,_=data
    if field=='wrong_tool':step['tool']='file_find:find'
    if field=='wrong_step':g.step_id='other'
    if field=='wrong_plan':g.plan_id='other'
    if field=='unused_grant':g.used_tool_calls=0
    if field=='missing_capture':g.allowed_resources=(str(path),)
    if field=='failed_output':r['output']['success']=False
    assert not store_desktop_screenshot(state,'p','s',r) and not saved

def test_changed_bytes_refused(data):
    state,r,path,_,_,saved,_=data;path.write_bytes(PNG+b'changed')
    with pytest.raises(ValueError):store_desktop_screenshot(state,'p','s',r)
    assert not saved

def test_wrong_path_refused(data,tmp_path):
    state,r,path,_,_,saved,_=data;other=tmp_path/'other.png';other.write_bytes(PNG);r['output']['artifact']['path']=str(other)
    with pytest.raises(ValueError):store_desktop_screenshot(state,'p','s',r)
    assert not saved

@pytest.mark.parametrize('content',[b'<svg/>',b'\x89PNG\r\n\x1a\n',PNG[:16]+b'\xff'*8+PNG[24:]])
def test_invalid_png(content):assert not is_bounded_png(content)

def test_image_http_integrity_and_headers(monkeypatch):
    from fastapi.testclient import TestClient
    from api.server import app,state
    from api.auth import reset_token_cache
    monkeypatch.setenv('EAS_API_TOKEN','screenshot-api-test');reset_token_cache()
    body=[PNG];record={'kind':'desktop_screenshot','content_hash':'sha256:'+hashlib.sha256(PNG).hexdigest()}
    monkeypatch.setattr(state,'database',NS(read_artifact_content=lambda i:body[0],get_artifact=lambda i:record))
    client=TestClient(app);url='/api/artifacts/synthetic/content';headers={'Authorization':'Bearer screenshot-api-test'}
    try:
        assert client.get(url).status_code==401
        response=client.get(url,headers=headers)
        assert response.content==PNG and response.headers['content-type']=='image/png'
        assert response.headers['cache-control']=='no-store' and response.headers['x-content-type-options']=='nosniff'
        body[0]=PNG+b'changed';assert client.get(url,headers=headers).status_code==409
        body[0]=b'<svg/>';record['content_hash']='sha256:'+hashlib.sha256(body[0]).hexdigest()
        assert client.get(url,headers=headers).status_code==415
    finally:reset_token_cache()
