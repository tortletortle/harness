import json
import pytest
from api import desktop_approval as d

@pytest.mark.parametrize('content,allowed',[
    (None,False),('broken',False),('[]',False),
    ('{"version":1,"mode":"ask","consent":"persistent_read_only_desktop"}',False),
    ('{"version":1,"mode":"auto-readonly"}',False),
    ('{"version":1,"mode":"auto-readonly","consent":"persistent_read_only_desktop"}',True),
    ('{"version":2,"mode":"auto-readonly","consent":"persistent_read_only_desktop"}',False),
    ('x'*4097,False),
])
def test_policy(tmp_path,monkeypatch,content,allowed):
    path=tmp_path/'desktop_access.json';monkeypatch.setattr(d,'DESKTOP_ACCESS_CONFIG',path)
    if content is not None:path.write_text(content,encoding='utf-8')
    assert d.desktop_auto_allowed() is allowed

def test_switch_applies_on_next_step(tmp_path,monkeypatch):
    path=tmp_path/'desktop_access.json';monkeypatch.setattr(d,'DESKTOP_ACCESS_CONFIG',path)
    cfg={'version':1,'mode':'auto-readonly','consent':'persistent_read_only_desktop'}
    path.write_text(json.dumps(cfg));assert d.desktop_auto_allowed()
    cfg['mode']='ask';path.write_text(json.dumps(cfg));assert not d.desktop_auto_allowed()
