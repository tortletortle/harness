"""Persist only verified outputs of a real desktop screenshot step, never arbitrary file references."""
import hashlib
import json
from pathlib import Path
import struct
from uuid import uuid4

MAX_PNG=8*1024*1024

def is_bounded_png(content):
    if not 33<=len(content)<=MAX_PNG or content[:16]!=b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR':return False
    width,height=struct.unpack('>II',content[16:24])
    return width>0 and height>0 and width*height<=16_000_000


def store_desktop_screenshot(state,plan_id,step_id,result):
    output=result.get('output')
    if not result.get('success') or not isinstance(output,dict) or not output.get('success'):return False
    artifact=output.get('artifact')
    if not isinstance(artifact,dict) or artifact.get('media_type')!='image/png':return False
    step=next((s for s in state.database.get_steps(plan_id) if str(s['step_id'])==str(step_id)),None)
    if not step or step.get('tool')!='desktop_observe:screenshot':return False
    inputs=json.loads(step['inputs']) if isinstance(step.get('inputs'),str) else step.get('inputs',{})
    grant=state.authorization_manager.get(result.get('grant_id'))
    if (not grant or grant.plan_id!=str(plan_id) or grant.step_id!=str(step_id)
            or 'desktop_observe' not in grant.allowed_tools or grant.used_tool_calls!=1):return False
    prefix='desktop:capture:'+str(inputs.get('window_id'))+':'
    if not any(r.startswith(prefix) for r in grant.allowed_resources):return False
    # Grant was already consumed/revoked by Runtime. This is ingestion of its exact output,
    # not an extra arbitrary file-reading capability. Validate path and digest before storage.
    from harness_tools.file_perception import FilePolicy,_path,_snapshot
    policy=FilePolicy(grant.workspace_root,lambda p:p in grant.allowed_resources,lambda p:False)
    expected=_path(policy,inputs.get('output'))
    actual=_path(policy,artifact.get('path'))
    if actual!=expected or actual.suffix.lower()!='.png':raise ValueError('screenshot_artifact_path_mismatch')
    if not 33<=actual.stat().st_size<=MAX_PNG:raise ValueError('screenshot_artifact_size_limit')
    content=_snapshot(actual)
    if (not is_bounded_png(content) or len(content)!=artifact.get('bytes')
            or hashlib.sha256(content).hexdigest()!=artifact.get('sha256')):raise ValueError('screenshot_artifact_integrity_failed')
    record=state.database.store_artifact_content('desktop-'+uuid4().hex,content,plan_id=plan_id,step_id=step_id,
        kind='desktop_screenshot',metadata={'source':'verified_desktop_capture','media_type':'image/png','untrusted_content':True,
        'window_title':str(output.get('window',{}).get('title',''))[:200],'automatic_redaction':False,
        'original_path':str(actual),'width':output.get('width'),'height':output.get('height')})
    state.database.add_step_event(step_id,plan_id,'desktop_screenshot_stored',
        {'artifact_id':record['artifact_id'],'content_hash':record['content_hash']},branch_version=step.get('branch_version',1))
    return True
